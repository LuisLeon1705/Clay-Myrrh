"""Aplicación principal: estado de la sesión, bucle REPL y comandos."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__, llamalog, reader, relay, render, sessions
from .attachments import AttachError, Attachment, find_mentions, load_any, shrink_image
from .catalog import Catalog, ModelEntry, build_catalog, entry_from_path, pair_mmproj
from .config import (TRANSIENT, CRASH_LOG, FILES_DIR, AUTOSAVE_DIR, GLOBAL_MEMORY_FILE, HISTORY_FILE, OLLAMA_HOST, Settings,
                     load_memory_text, load_settings, memory_files, parse_size, project_memory_file,
                     save_settings, DEFAULT_SYSTEM_PROMPT)
from . import files as filemod
from . import workspace as wsmod
from .engine import EngineError, GenParams, GenStats, LlamaEngine, OllamaEngine, image_tokens
from .memory import (CTX_STEPS, GPU_OVERHEAD, GiB, MiB, auto_fit, budget, estimate, max_gpu_layers,
                     probe, suggestions)
from .render import console, error, ok, say, warn


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def copy_to_clipboard(text: str) -> bool:
    """Portapapeles de Windows (clip.exe necesita UTF-16 con BOM para acentos)."""
    try:
        p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        p.communicate(b"\xff\xfe" + text.encode("utf-16-le"))
        return p.returncode == 0
    except Exception:
        try:
            import pyperclip
            pyperclip.copy(text)
            return True
        except Exception:
            return False


class EscWatcher:
    """Detecta Esc (o Ctrl+C) durante la generación sin bloquear (Windows: msvcrt)."""

    def __init__(self):
        self.enabled = False
        if sys.platform == "win32":
            try:
                import msvcrt  # noqa: F401
                self.enabled = sys.stdin.isatty()
            except Exception:
                pass

    def keys(self) -> list[str]:
        """Teclas pulsadas desde la última consulta (sin las especiales de dos códigos)."""
        if not self.enabled:
            return []
        import msvcrt
        out = []
        while msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                if msvcrt.kbhit():
                    msvcrt.getwch()
                continue
            out.append(ch)
        return out

    def pressed(self) -> bool:
        return any(k in ("\x1b", "\x03") for k in self.keys())


@dataclass
class SessionStats:
    started: float = field(default_factory=time.time)
    turns: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    gen_time: float = 0.0
    prompt_time: float = 0.0
    interrupted: int = 0
    last: GenStats | None = None


@dataclass
class LoadPlan:
    entry: ModelEntry
    n_ctx: int
    ngl: int
    est: object
    fits_vram: bool
    fits_ram: bool
    vram_budget: int
    ram_budget: int
    reclaim: int
    sysmem: object


# ---------------------------------------------------------------------------
# Aplicación
# ---------------------------------------------------------------------------

class App:
    def __init__(self, settings: Settings, args: argparse.Namespace | None = None):
        self.s = settings
        self.args = args
        self.catalog: Catalog = build_catalog(settings)
        self.engine: LlamaEngine | OllamaEngine | None = None
        self.entry: ModelEntry | None = None
        self.plan: LoadPlan | None = None
        self.history: list[dict] = []
        self.thoughts: dict[int, str] = {}
        self.pending: list[Attachment] = []
        self.last_reply = ""
        self.last_think = ""
        self.last_shell = ""
        self.last_saved: list[Path] = []
        self.project_dir: Path | None = None              # carpeta del proyecto activo (/proyecto o /zero)
        self.workspace: wsmod.Workspace | None = None      # /proyecto: el modelo edita esa carpeta
        self.turn_components = False                       # componentes .lai en este turno (por intención)
        self.turn_query = ""                               # petición del turno (elige qué componentes documentar)
        self.last_transcripts: list[tuple[str, str]] = []     # (origen, texto)
        self.last_relay = ""
        self.assume_yes = False
        self.stats = SessionStats()
        self.session_id = time.strftime("%Y%m%d-%H%M%S")
        self.memory_text = load_memory_text()
        self.ctx_tokens = 0
        self._ctx_dirty = True
        self.box = None
        self.esc = EscWatcher()
        self.commands = self._build_commands()
        self.running = True

    # ------------------------------------------------------------ propiedades
    @property
    def params(self) -> GenParams:
        s = self.s
        return GenParams(s.max_tokens, s.temperature, s.top_p, s.top_k, s.min_p, s.repeat_penalty, s.seed,
                         s.thinking and bool(getattr(self.engine, "can_think", False)))

    @property
    def vision(self) -> bool:
        return bool(self.engine and self.engine.vision)

    @property
    def reader_entry(self) -> ModelEntry | None:
        return reader.pick_reader(self.catalog, self.s)

    def can_see(self) -> bool:
        """True si las imágenes se pueden procesar: con el modelo actual o con el lector visual."""
        return self.vision or self.reader_entry is not None

    def uses_reader(self) -> bool:
        return self.s.vision_mode == "lector" or not self.vision

    @property
    def files_dir(self) -> Path:
        return self.project_dir or FILES_DIR / self.session_id

    @property
    def n_ctx(self) -> int:
        return int(getattr(self.engine, "n_ctx", 0) or 0)

    def system_text(self) -> str:
        parts = [self.s.system_prompt]
        if self.memory_text:
            parts.append("# Instrucciones persistentes del usuario (LOCAL_AI.md)\n" + self.memory_text)
        if self.workspace is not None:
            parts.append(self.workspace.instructions())
        elif self.s.save_files != "never":
            parts.append(filemod.FILE_INSTRUCTIONS)
        if self.s.components_in_chat == "on" or self.turn_components:
            from . import webkit
            prev = self.last_reply if "```lai" in self.last_reply or "pagina.lai" in self.last_reply else ""
            parts.append(webkit.chat_doc(self.turn_query, prev))
        parts.append(f"Fecha: {time.strftime('%Y-%m-%d')}. Directorio de trabajo: {os.getcwd()}")
        return "\n\n".join(parts)

    def messages(self, extra: list[dict] | None = None) -> list[dict]:
        hist = list(self.history) + (extra or [])
        # mantener imágenes solo en los últimos N mensajes que las tengan
        keep = max(0, self.s.keep_images_turns)
        with_imgs = [i for i, m in enumerate(hist) if m.get("images")]
        drop = set(with_imgs[:-keep] if keep else with_imgs)
        if extra:  # el mensaje nuevo siempre conserva sus imágenes
            drop.discard(len(hist) - 1)
        out = [{"role": "system", "content": self.system_text()}]
        for i, m in enumerate(hist):
            if i in drop:
                names = ", ".join(x.get("name", "imagen") for x in m["images"])
                out.append({"role": m["role"], "content": f"[imágenes vistas antes: {names}]\n{m['content']}"})
            else:
                out.append(m)
        return out

    def _mark_ctx(self):
        self._ctx_dirty = True

    def context_used(self) -> int:
        if self.engine is None:
            return 0
        if self._ctx_dirty:
            try:
                self.ctx_tokens = self.engine.prompt_tokens(self.messages(), self.params.thinking) if self.history else 0
            except Exception:
                pass
            self._ctx_dirty = False
        return self.ctx_tokens

    # ------------------------------------------------------------ comandos
    def _build_commands(self):
        from .inputbox import CmdSpec
        sess = lambda: [(i.name, f"{time.strftime('%d/%m %H:%M', time.localtime(i.updated))} · {i.turns} turnos · {i.preview}")
                        for i in sessions.list_sessions(include_auto=False)]
        models = lambda: [(m.name, f"{m.size_gb:.1f} GB · {'👁 visión' if m.has_vision else ('⚠ falta mmproj' if m.vision == 'falta_mmproj' else m.kind)}")
                          for m in self.catalog.models]
        specs = [
            CmdSpec("/help", "Ayuda: comandos y atajos", aliases=("/?", "/h")),
            CmdSpec("/clear", "Borrar la conversación y la pantalla", aliases=("/reset",)),
            CmdSpec("/exit", "Salir", aliases=("/quit", "/q")),
            CmdSpec("/model", "Elegir modelo (selector con tamaño, visión y memoria)", "[nombre]", choices=models),
            CmdSpec("/models", "Tabla de modelos locales y de Ollama"),
            CmdSpec("/compact", "Resumir la conversación para liberar contexto", "[instrucciones]"),
            CmdSpec("/resume", "Retomar una sesión guardada o autoguardada"),
            CmdSpec("/chat", "Sesiones con nombre: save|list|load|delete", "<acción> [nombre]",
                    choices=lambda: [("save", "guardar la sesión actual"), ("list", "listar sesiones"),
                                     ("load", "cargar"), ("delete", "borrar")] + [(f"load {n}", m) for n, m in sess()]),
            CmdSpec("/memory", "Instrucciones persistentes (LOCAL_AI.md)", "[show|add|addp|edit|reload|path]",
                    choices=lambda: [("show", "ver memoria cargada"), ("add ", "añadir a la memoria global"),
                                     ("addp ", "añadir a la memoria del proyecto"), ("edit", "abrir en el editor"),
                                     ("edit proyecto", "editar LOCAL_AI.md del proyecto"),
                                     ("reload", "recargar archivos"), ("path", "rutas")]),
            CmdSpec("/stats", "Tokens de entrada/salida, t/s, tiempos y memoria"),
            CmdSpec("/copy", "Copiar la última respuesta al portapapeles"),
            CmdSpec("/save", "Guardar en un archivo el código de la última respuesta", "[ruta] [nº bloque]", paths=True),
            CmdSpec("/open", "Abrir el último archivo guardado (HTML en el navegador)", "[ruta]", paths=True),
            CmdSpec("/export", "Guardar la conversación en .md", "[archivo.md]", paths=True),
            CmdSpec("/system", "Ver o cambiar el prompt de sistema", "[texto|reset]"),
            CmdSpec("/theme", "Cambiar el tema de colores", "[nombre]",
                    choices=lambda: [(n, "tema") for n in render.THEMES]),
            CmdSpec("/think", "Razonamiento: on|off (pedirlo), show|hide (verlo), last", "[on|off|show|hide|last]",
                    choices=lambda: [("on", "pedir razonamiento al modelo"), ("off", "sin razonamiento"),
                                     ("show", "mostrarlo desplegado"), ("hide", "plegado"), ("last", "ver el último")]),
            CmdSpec("/ctx", "Tamaño del contexto (recarga conservando la conversación)", "[n|32k|auto]",
                    choices=lambda: [(v, "") for v in ("auto", "4k", "8k", "16k", "32k", "64k", "128k")]),
            CmdSpec("/gpu", "Capas en GPU (recarga)", "[n|auto|max]",
                    choices=lambda: [("auto", "las que quepan"), ("max", "todas")]),
            CmdSpec("/kv", "Tipo de caché KV (recarga)", "[f16|q8_0|q4_0]",
                    choices=lambda: [("f16", "precisión completa"), ("q8_0", "½ memoria, casi sin pérdida"),
                                     ("q4_0", "¼ memoria, algo de pérdida")]),
            CmdSpec("/swa", "Caché de ventana deslizante (Gemma): min|full", "[min|full]",
                    choices=lambda: [("min", "solo la ventana (ahorra VRAM)"), ("full", "contexto completo")]),
            CmdSpec("/auto", "Elegir capas en GPU y contexto automáticamente y recargar"),
            CmdSpec("/mem", "Estimación de VRAM/RAM (antes de cargar)", "[modelo] [ctx] [gpu]",
                    aliases=("/estimate",), choices=models),
            CmdSpec("/tokens", "Máximo de tokens por respuesta", "[n]"),
            CmdSpec("/temp", "Temperatura", "[valor]"),
            CmdSpec("/top_p", "Top-p", "[valor]"),
            CmdSpec("/top_k", "Top-k", "[valor]"),
            CmdSpec("/min_p", "Min-p", "[valor]"),
            CmdSpec("/repeat", "Penalización de repetición", "[valor]"),
            CmdSpec("/seed", "Semilla (-1 = aleatoria)", "[n]"),
            CmdSpec("/params", "Ver todos los parámetros de carga y muestreo"),
            CmdSpec("/set", "Ajustes: pdf_max_pages, pdf_max_image_pages, image_max_side, keep_images_turns, vram_margin_mb, auto_ctx_cap, shell, save_files",
                    "<clave> <valor>", choices=lambda: [(k + " ", str(getattr(self.s, k))) for k in SETTABLE]),
            CmdSpec("/file", "Adjuntar archivo de texto/código", "<ruta>", paths=True),
            CmdSpec("/image", "Adjuntar imagen", "<ruta>", paths=True, aliases=("/img",)),
            CmdSpec("/pdf", "Adjuntar PDF", "<ruta> [auto|texto|imagenes] [páginas 1-5]", paths=True),
            CmdSpec("/attachments", "Ver o vaciar adjuntos pendientes", "[clear]", aliases=("/adjuntos",),
                    choices=lambda: [("clear", "vaciar")]),
            CmdSpec("/mmproj", "Proyector de visión del modelo actual", "[archivo|auto|none]",
                    choices=lambda: [("auto", "emparejar por metadatos"), ("none", "desactivar visión")]
                    + [(Path(p.path).name, f"{p.projector_type} · dim {p.projection_dim}") for p in self.catalog.projectors]),
            CmdSpec("/vision", "Lector visual: modo, modelo lector, última transcripción", "[lector|directo|modelo|ver|limpiar]",
                    choices=lambda: [("lector", "un modelo con visión transcribe y tu modelo trabaja el texto"),
                                     ("directo", "las imágenes van directas al modelo (si tiene mmproj)"),
                                     ("ver", "ver la última transcripción"), ("limpiar", "borrar transcripciones guardadas"),
                                     ("modelo auto", "elegir el lector automáticamente")]
                    + [(f"modelo {m.name}", f"{m.size_gb:.1f} GB · 👁") for m in self.catalog.models if m.has_vision]),
            CmdSpec("/archivos", "Archivos creados por el modelo en esta sesión", "[abrir]",
                    choices=lambda: [("abrir", "abrir la carpeta en el Explorador")]),
            CmdSpec("/settings", "Ajustes con menú (visión, razonamiento, tema, archivos, muestreo…)",
                    aliases=("/ajustes",)),
            CmdSpec("/modo", "Cambiar a un perfil de uso (modelo y ajustes del asistente de instalación)",
                    "[categoría]", choices=lambda: [(k, f"{v.get('model', '')}") for k, v in (self.s.profiles or {}).items()]),
            CmdSpec("/setup", "Asistente de instalación: recomienda y descarga modelos para tu equipo"),
            CmdSpec("/componentes", "Catálogo de componentes web prediseñados (.lai)", "[demo|chat auto|on|off]",
                    choices=lambda: [("demo", "abrir en el navegador una página con todos los componentes"),
                                     ("chat auto", "solo cuando pides una página o plantilla (recomendado)"),
                                     ("chat on", "siempre en el chat (≈3k tokens de contexto)"),
                                     ("chat off", "solo en /zero y /plantilla")]),
            CmdSpec("/plantilla", "Crear una página o plantilla con componentes (.lai → HTML, CSS y JS)",
                    "<descripción>", aliases=("/template",)),
            CmdSpec("/proyecto", "Abrir una carpeta como proyecto: el modelo lee y modifica sus archivos",
                    "[ruta|cerrar|recargar]", paths=True, aliases=("/project", "/carpeta"),
                    choices=lambda: [("cerrar", "dejar de trabajar sobre la carpeta"),
                                     ("recargar", "volver a leer la estructura de la carpeta")]
                    + [(r, "reciente") for r in self.s.recent_projects]),
            CmdSpec("/cambios", "Cambios del modelo en el proyecto durante esta sesión", "[diff]",
                    choices=lambda: [("diff", "ver las diferencias del último cambio")]),
            CmdSpec("/deshacer", "Revertir los últimos cambios del modelo en el proyecto", aliases=("/undo",)),
            CmdSpec("/zero", "Prompt-Zero: crear una página web desde cero con un cuestionario y tareas guiadas",
                    "[continuar]", choices=lambda: [("continuar", "retomar el último proyecto a medias")],
                    aliases=("/prompt-zero",)),
            CmdSpec("/doctor", "Diagnóstico: dependencias, backend GPU, modelos, mmproj y memoria"),
            CmdSpec("/debug", "Log nativo de llama.cpp/mtmd", "[on|off]",
                    choices=lambda: [("on", "mostrar logs en vivo"), ("off", "ocultar")]),
        ]
        table = {}
        for sp in specs:
            table[sp.name] = sp
            for a in sp.aliases:
                table[a] = CmdSpec(a, sp.desc, sp.args, sp.choices, sp.paths, hidden=True)
        return table

    def _resolve(self, cmd: str) -> str | None:
        if cmd in self.commands:
            return cmd
        cands = sorted({c for c in self.commands if c.startswith(cmd) and not self.commands[c].hidden})
        return cands[0] if len(cands) == 1 else None

    def handle_command(self, line: str):
        cmd, _, arg = line.strip().partition(" ")
        cmd = cmd.lower()
        arg = arg.strip()
        real = self._resolve(cmd)
        if real is None:
            cands = [c for c in self.commands if c.startswith(cmd) and not self.commands[c].hidden]
            if cands:
                warn(f"'{cmd}' es ambiguo: {', '.join(sorted(cands))}")
            else:
                warn(f"Comando desconocido '{cmd}'. Escribe / para ver el menú o /help.")
            return
        alias = {"/?": "/help", "/h": "/help", "/reset": "/clear", "/quit": "/exit", "/q": "/exit",
                 "/estimate": "/mem", "/img": "/image", "/adjuntos": "/attachments", "/ajustes": "/settings",
                 "/prompt-zero": "/zero", "/template": "/plantilla", "/project": "/proyecto",
                 "/carpeta": "/proyecto", "/undo": "/deshacer"}
        real = alias.get(real, real)
        fn: Callable[[str], None] = getattr(self, "cmd_" + real[1:].replace("-", "_"))
        try:
            fn(arg)
        except EngineError as e:
            error(str(e), e.hints)
        except (AttachError, FileNotFoundError, ValueError) as e:
            error(str(e))

    # ======================================================================
    # Carga de modelos
    # ======================================================================

    def _current_vram(self) -> int:
        if isinstance(self.engine, LlamaEngine):
            return self.engine.real.gpu + GPU_OVERHEAD
        return 0

    def make_plan(self, entry: ModelEntry, ctx=None, gpu=None, kv=None, swa=None,
                  reclaim_current: bool = True) -> LoadPlan | None:
        if entry.kind != "gguf" or entry.info is None:
            return None
        mc = self.s.models.get(entry.name, {})
        ctx = ctx if ctx is not None else mc.get("ctx", self.s.ctx)
        gpu = gpu if gpu is not None else mc.get("gpu", self.s.gpu)
        kv = kv or self.s.kv_type
        swa = self.s.swa_full if swa is None else swa
        info = entry.info
        sysm = probe()
        reclaim = self._current_vram() if (self.engine is not None and reclaim_current) else 0
        vb, rb = budget(sysm, self.s.vram_margin_mb, reclaim)
        ctx_v = None if ctx in (None, "auto") else int(ctx)
        if ctx_v and info.context_length and ctx_v > info.context_length:
            warn(f"{entry.name} admite como máximo {info.context_length} tokens; se usa ese valor.")
            ctx_v = info.context_length
        ngl_v = None if gpu in (None, "auto") else (max_gpu_layers(info) if gpu == "max" else int(gpu))
        calib = float(mc.get("calib", 1.0))
        fit = auto_fit(info, vb, rb, kv_type=kv, swa_full=swa, flash_attn=self.s.flash_attn, mmproj=entry.mmproj,
                       ctx=ctx_v, ngl=ngl_v, ctx_cap=self.s.auto_ctx_cap, calib=calib)
        return LoadPlan(entry, fit.n_ctx, fit.ngl, fit.est, fit.fits_vram, fit.fits_ram, vb, rb, reclaim, sysm)

    def show_plan(self, plan: LoadPlan, title: str | None = None):
        render.mem_report(plan.est, plan.sysmem, plan.vram_budget, plan.ram_budget,
                          title=title or f"Estimación de memoria · {plan.entry.name} · ctx {plan.n_ctx} · gpu {plan.ngl}/{max_gpu_layers(plan.entry.info)}",
                          reclaim=plan.reclaim)

    def confirm(self, question: str, default: bool = False) -> bool:
        if self.assume_yes:
            return True
        if not sys.stdin.isatty():
            return default
        from .inputbox import Choice, select
        try:
            ans = select(question, [Choice(True, "Sí"), Choice(False, "No")], render.current,
                         0 if default else 1, hint="↑/↓ elegir · Enter confirmar · Esc = No")
        except Exception:  # sin consola interactiva
            return default
        say(f"[dim]{escape(question)} → {'Sí' if ans else 'No'}[/]")
        return bool(ans)

    def _engine_gpu_capable(self) -> bool | None:
        try:
            from .engine import llama
            return bool(llama().llama_supports_gpu_offload())
        except Exception:
            return None

    def _warn_cpu_only_engine(self, plan: LoadPlan):
        """El plan quiere capas en la GPU pero el motor instalado no sabe usarla: avisar antes de cargar."""
        if plan.ngl <= 0 or self._engine_gpu_capable() is not False:
            return
        from . import gpu as gpumod
        sm = plan.sysmem
        summary, steps = gpumod.engine_advice(sm.vendor)
        warn(f"Este llama-cpp-python es solo CPU: {plan.entry.name} irá entero a la RAM aunque tengas "
             f"{sm.gpu_name or 'GPU'} ({render.gb(sm.vram_total)} GB). " + summary,
             )
        for st in steps:
            say(f"   [dim]→[/] {escape(st)}")

    def _offload_mismatch(self, plan: LoadPlan, eng) -> bool:
        """¿llama.cpp puso en la GPU mucho menos de lo planificado?"""
        if not plan or plan.ngl <= 0:
            return False
        dl = getattr(eng, "devlog", None)
        if dl is not None and dl.offloaded is not None:
            return dl.offloaded < max(1, plan.ngl // 2)
        expected = plan.est.weights_gpu
        return bool(expected) and eng.real.gpu < expected * 0.25

    def _explain_offload(self, plan: LoadPlan, eng):
        from . import gpu as gpumod
        dl, sm = eng.devlog, plan.sysmem
        got = dl.offloaded if dl.offloaded is not None else 0
        warn(f"llama.cpp ha dejado el modelo casi entero en la CPU: {got} de {plan.ngl} capas previstas en la GPU "
             f"({render.gb(eng.real.gpu)} GB de VRAM). Irá mucho más lento de lo estimado.")
        if not dl.gpu_backend or self._engine_gpu_capable() is False:
            summary, steps = gpumod.engine_advice(sm.vendor)
            say(f"   [dim]Causa:[/] el llama-cpp-python instalado no tiene backend GPU (versión solo CPU). {escape(summary)}")
            for st in steps:
                say(f"   [dim]→[/] {escape(st)}")
        elif all(uma for _, uma in dl.devices) and sm.vram_total:
            names = ", ".join(n for n, _ in dl.devices)
            say(f"   [dim]Causa:[/] llama.cpp solo ve la gráfica integrada ({escape(names)}), no tu {escape(sm.gpu_name)}.")
            say("   [dim]→[/] Instala o actualiza el controlador de la tarjeta (incluye Vulkan) y reinicia.")
            say("   [dim]→[/] Si aparecen varias, elige la dedicada con la variable de entorno GGML_VK_VISIBLE_DEVICES=<nº>.")
        else:
            free = f"{dl.free_mib} MiB libres" if dl.free_mib is not None else "poca VRAM libre"
            say(f"   [dim]Causa probable:[/] llama.cpp veía {free} en {escape(dl.used or 'la GPU')} al cargar "
                "(otros programas la ocupan, o el controlador informa de menos memoria).")
            say("   [dim]→[/] Cierra juegos y programas que usen la GPU y prueba /gpu auto · /doctor para revisar.")

    def check_plan(self, plan: LoadPlan, interactive: bool = True) -> bool:
        """Si no cabe: muestra la estimación, avisa, sugiere y pide confirmación."""
        if plan.fits_vram and plan.fits_ram:
            return True
        self.show_plan(plan)
        info = plan.entry.info
        if not plan.fits_vram:
            warn(f"No cabe en VRAM: {render.gb(plan.est.vram)} GB estimados > {render.gb(plan.vram_budget)} GB disponibles.")
        if not plan.fits_ram:
            warn(f"RAM justa: {render.gb(plan.est.ram)} GB estimados > {render.gb(plan.ram_budget)} GB disponibles.")
        for sgg in suggestions(info, plan.vram_budget, plan.ram_budget, plan.n_ctx, plan.ngl,
                               kv_type=self.s.kv_type, swa_full=self.s.swa_full, flash_attn=self.s.flash_attn,
                               mmproj=plan.entry.mmproj):
            say(f"   [dim]→[/] {sgg}")
        if not interactive:
            return True
        return self.confirm("¿Cargar de todos modos? (puede fallar o ir muy lento)")

    def load(self, entry: ModelEntry, plan: LoadPlan | None, keep_history: bool = True) -> bool:
        prev = (self.entry, self.plan)
        if self.engine is not None:
            self.engine.close()
            self.engine = None
        try:
            if entry.kind == "ollama":
                with console.status(f"[accent]Conectando con Ollama · {entry.name}…[/]", spinner="dots"):
                    ctx = self.s.ctx if isinstance(self.s.ctx, int) else 8192
                    self.engine = OllamaEngine(entry.name, OLLAMA_HOST, ctx)
            else:
                assert plan is not None
                self._warn_cpu_only_engine(plan)
                with console.status(f"[accent]Cargando {entry.name} (ctx {plan.n_ctx}, gpu {plan.ngl}, kv {self.s.kv_type})…[/]",
                                    spinner="dots"):
                    self.engine = LlamaEngine(entry, plan.n_ctx, plan.ngl, self.s.kv_type, self.s.swa_full,
                                              self.s.flash_attn)
        except EngineError as e:
            error(f"No se pudo cargar {entry.name}: {e}", e.hints)
            if prev[0] is not None and prev[0] is not entry and prev[1] is not None:
                say(f"[dim]Recargando el modelo anterior ({prev[0].name})…[/]")
                try:
                    self.engine = LlamaEngine(prev[0], prev[1].n_ctx, prev[1].ngl, self.s.kv_type,
                                              self.s.swa_full, self.s.flash_attn)
                except EngineError as e2:
                    error(f"Tampoco se pudo recargar el anterior: {e2}")
                    self.entry = None
                    return False
            return False
        self.entry, self.plan = entry, plan
        self.s.last_model = entry.name
        eng = self.engine
        if isinstance(eng, LlamaEngine):
            real = eng.real
            dl = getattr(eng, "devlog", None)
            on_gpu = dl.offloaded if dl and dl.offloaded is not None else plan.ngl
            mismatch = self._offload_mismatch(plan, eng)
            est_buf = plan.est.vram - plan.est.overhead_gpu if plan else 0
            if est_buf and real.gpu and not mismatch:          # una carga «fallida» no debe falsear la calibración
                calib = max(0.8, min(1.3, real.gpu / (est_buf / plan.est.calib)))
                self.s.model_cfg(entry.name)["calib"] = round(calib, 3)
            say(f"[ok]✓[/] [bold]{escape(entry.name)}[/] [dim]listo en {eng.load_time:.1f}s · ctx "
                f"{render.human_tokens(eng.n_ctx)} · {on_gpu}/{max_gpu_layers(entry.info)} capas en GPU · "
                f"VRAM {render.gb(real.gpu)} GB[/]")
            if mismatch:
                self._explain_offload(plan, eng)
        else:
            say(f"[ok]✓[/] [bold]{escape(entry.name)}[/] [dim](Ollama) listo[/]")
        rd = self.reader_entry
        if eng.vision_error and entry.vision == "falta_mmproj" and rd is not None and rd.name != entry.name:
            say(f"  [dim]Sin visión propia: las imágenes y PDFs escaneados los leerá {escape(rd.name)}.[/]")
        elif eng.vision_error:
            warn(eng.vision_error)
            hint = entry.mmproj_hint()
            if hint and entry.vision == "falta_mmproj":
                say(f"   [dim]→ Descarga el mmproj: huggingface.co/{hint[0]} · {hint[1]} ({hint[2]}) "
                    f"y ponlo en models/mmproj/[/]")
        if not keep_history:
            self.history, self.thoughts = [], {}
        elif not eng.vision and any(m.get("images") for m in self.history):
            say("[dim]  Las imágenes del historial se enviarán como notas de texto (el modelo no tiene visión).[/]")
        save_settings(self.s)
        self._mark_ctx()
        return True

    def reload_with(self, **overrides) -> bool:
        if not isinstance(self.engine, LlamaEngine) or self.entry is None:
            warn("Esto solo aplica a modelos GGUF cargados con llama.cpp.")
            return False
        plan = self.make_plan(self.entry, **overrides)
        if plan is None or not self.check_plan(plan):
            say("[dim]Cancelado.[/]")
            return False
        return self.load(self.entry, plan)

    # ======================================================================
    # Generación
    # ======================================================================

    def run_generation(self, messages: list[dict], label: str, params: GenParams | None = None, *,
                       engine=None, tool_title: str = "", header: bool = True) -> tuple[str, str, GenStats, bool]:
        """Ejecuta en un hilo, pinta en vivo y permite interrumpir con Esc/Ctrl+C."""
        p = params or self.params
        q: queue.Queue = queue.Queue()
        stop = threading.Event()
        eng = engine or self.engine

        def worker():
            try:
                for kind, piece in eng.stream(messages, p, stop):
                    q.put((kind, piece))
                q.put(("__done__", None))
            except Exception as ex:  # noqa: BLE001
                q.put(("__error__", ex))

        th = threading.Thread(target=worker, daemon=True)
        view = render.StreamView(label, self.s.show_thinking, tool=bool(tool_title), tool_title=tool_title,
                                 header=header)
        self.last_relay = ""
        if any(m.get("images") for m in messages) and eng.vision:
            view.set_phase("Mirando la imagen")
        err = None
        th.start()
        with view:
            done = False
            while not done:
                try:
                    try:
                        item = q.get(timeout=0.05)
                    except queue.Empty:
                        item = None
                    while item is not None:
                        kind, piece = item
                        if kind == "__done__":
                            done = True
                        elif kind == "__error__":
                            err, done = piece, True
                        else:
                            view.feed(kind, piece)
                        try:
                            item = q.get_nowait()
                        except queue.Empty:
                            item = None
                    if not done:
                        keys = self.esc.keys() if hasattr(self.esc, "keys") else (
                            ["\x1b"] if self.esc.pressed() else [])
                        if any(k in ("\x1b", "\x03") for k in keys):
                            stop.set()
                            view.stopping = True
                        if any(k in ("t", "T") for k in keys):     # t: ver/ocultar el razonamiento en vivo
                            view.show_thinking = not view.show_thinking
                except KeyboardInterrupt:
                    stop.set()
                    view.stopping = True
        th.join(timeout=5)
        st = eng.last_stats
        self.last_relay = view.relay
        if err is not None:
            view.live.stop()
            if view.answer.strip():
                view.finish()
            if isinstance(err, EngineError):
                raise err
            raise EngineError(f"{err.__class__.__name__}: {err}", ["/debug muestra el log nativo"], llamalog.tail(30))
        if tool_title:
            view.finish()
            return view.answer.strip(), view.think.strip(), st, st.stopped
        footer = f"⏱ {st.t_total:.1f}s"
        if st.t_first > 0.5:
            footer += f" (prompt {st.t_first:.1f}s)"
        footer += f" · {st.completion_tokens} tok"
        if st.tps:
            footer += f" · {st.tps:.1f} t/s"
        if st.stopped:
            footer += " · interrumpido"
        view.finish(footer)
        return view.answer.strip(), view.think.strip(), st, st.stopped

    # ---------------------------------------------------------------- relevo automático
    def relay_chain(self, messages: list[dict], goal: str, label: str, params: GenParams | None = None, *,
                    tool_title: str = "") -> tuple[str, str, bool]:
        """
        Relevo automático orquestado por el programa (ver relay.py): la primera ejecución trabaja con
        presupuesto y, si no le alcanza, deja el índice; el programa encarga cada parte pendiente con un
        contexto mínimo (objetivo + índice + encargo), continúa lo cortado desde sus últimas líneas y une
        todo al final por id. Ninguna ejecución relee lo ya escrito.
        Devuelve (respuesta completa, razonamiento, interrumpido).
        """
        base = params or self.params
        if not self.s.relay_auto:
            ans, think, st, stopped = self.run_generation(messages, label, base, tool_title=tool_title)
            self._account(st)
            return ans, think, stopped
        reserve = max(256, int(base.max_tokens * max(5, min(40, self.s.relay_reserve)) / 100))
        work = max(512, base.max_tokens - reserve)
        p = dataclasses.replace(base, max_tokens=work)
        msgs = [dict(m) for m in messages]
        base_sys = msgs[0]["content"] if msgs and msgs[0].get("role") == "system" else ""
        if base_sys:
            msgs[0]["content"] = base_sys + "\n\n" + relay.instructions(work)
        else:
            msgs.insert(0, {"role": "system", "content": relay.instructions(work)})
        item_sys = {"role": "system", "content": (base_sys + "\n\n" if base_sys else "") + relay.ITEM_NOTE}
        log = relay.ChainLog(goal[:2000])
        thinks: list[str] = []

        # 1) primera ejecución, con el historial completo
        pt = self._prompt_tokens(msgs, p.thinking)
        first, think, st, stopped = self.run_generation(msgs, label, p, tool_title=tool_title)
        self._account(st)
        if think:
            thinks.append(think)
        r = None if stopped else relay.parse(self.last_relay)
        log.add("ejecución 1", st.completion_tokens, pt, relay=r)
        if not stopped and r is None and st.completion_tokens >= p.max_tokens - 2:
            # sin presupuesto y sin relevo: pedir el índice con la reserva (solo encabezados + últimas líneas)
            first = relay.trim_cut(first)
            req = [{"role": "system", "content": base_sys or "Eres un asistente útil."},
                   {"role": "user", "content": relay.request_prompt(goal, relay.headings(first), relay.tail_of(first))}]
            pt2 = self._prompt_tokens(req, False)
            ra, _, st2, stopped = self.run_generation(req, label, dataclasses.replace(p, max_tokens=reserve, thinking=False),
                                                      tool_title="↻ Preparando el relevo")
            self._account(st2)
            r = relay.parse(self.last_relay or ra)
            log.add("índice con la reserva", st2.completion_tokens, pt2, relay=r)
            if r is None:
                warn("La respuesta se cortó y el modelo no dejó un índice para continuar.")
        if stopped or r is None:
            return self._relay_end(log, first, thinks, stopped)

        # 2) el programa encarga cada parte pendiente (o, si el índice no sirve, continúa desde la cola)
        items = relay.plan_from(r.indice)
        fmt = r.siguiente.strip()
        hs = relay.headings(first)
        if not relay.plan_ok(items, len(hs), fmt):
            log.add("índice descartado", 0, note=f"{len(items)} filas, {len(hs)} encabezados escritos")
            return self._relay_continue(first, goal, item_sys, label, p, log, thinks)
        example = hs[-1] if hs else ""
        parts: dict[str, str] = {"0": first}
        pending = [i for i in items if i.status != "hecho"]
        limit = max(self.s.relay_max, len(pending) + 2)
        runs = 1
        render.tool("info", "↻ Relevo", f"{len(pending)} parte(s) pendientes · contexto mínimo por parte",
                    " · ".join(f"{i.id} {i.title[:28]}" for i in pending)[:200])

        parallel = [i for i in pending if i.status == "pendiente"] if (
            len(r.ramas) >= 2 and isinstance(self.engine, OllamaEngine) and self.s.relay_parallel > 1) else []
        if len(parallel) >= 2:
            outs = self._run_branches(item_sys, [relay.item_prompt(goal, items, i, fmt, example) for i in parallel],
                                      [f"{i.id} {i.title}" for i in parallel], p)
            for i, o in zip(parallel, outs):
                parts[i.id] = o
                i.status = "hecho"
                log.add(f"rama {i.id}", len(o) // 4, note="en paralelo")
            runs += len(parallel)

        for item in [i for i in pending if i.status != "hecho"]:
            key = "0" if item.status == "cortada" else item.id
            text = parts.get(key, "")
            prompt = (relay.resume_prompt(goal, items, item, relay.tail_of(text), fmt, relay.digest(text))
                      if item.status == "cortada" else relay.item_prompt(goal, items, item, fmt, example))
            while True:
                if runs >= limit:
                    warn(f"Se alcanzó el máximo de {limit} ejecuciones; quedan partes sin escribir.")
                    parts[key] = text
                    return self._relay_end(log, relay.unify(parts, items), thinks, False)
                runs += 1
                render.tool("info", f"↻ Parte {item.id}", item.title[:70],
                            "continúa desde sus últimas líneas" if item.status == "cortada" else "")
                req = [item_sys, {"role": "user", "content": prompt}]
                pt = self._prompt_tokens(req, False)
                ans, _, st, stopped = self.run_generation(req, label, dataclasses.replace(p, thinking=False),
                                                          header=False)
                self._account(st)
                text = relay.merge([text, ans]) if text else relay.display(ans)
                log.add(f"parte {item.id}", st.completion_tokens, pt, note=item.status)
                if stopped:
                    parts[key] = text
                    return self._relay_end(log, relay.unify(parts, items), thinks, True)
                if st.completion_tokens < p.max_tokens - 2:
                    break
                item.status = "cortada"                     # no cupo: sigue desde su última frase completa
                text = relay.trim_cut(text)
                prompt = relay.resume_prompt(goal, items, item, relay.tail_of(text), fmt, relay.digest(text))
            parts[key] = text
            item.status = "hecho"
        return self._relay_end(log, relay.unify(parts, items), thinks, False)

    def _relay_continue(self, text: str, goal: str, item_sys: dict, label: str, p: GenParams, log,
                        thinks: list[str]) -> tuple[str, str, bool]:
        """Modo continuación: cada ejecución ve objetivo + encabezados escritos + últimas líneas."""
        render.tool("info", "↻ Relevo", "continúa desde las últimas líneas · contexto mínimo",
                    f"{len(relay.headings(text))} encabezados ya escritos")
        for n in range(2, max(2, self.s.relay_max) + 1):
            req = [item_sys, {"role": "user", "content":
                              relay.continue_prompt(goal, relay.headings(text), relay.tail_of(text), n,
                                                    relay.digest(text))}]
            pt = self._prompt_tokens(req, False)
            ans, _, st, stopped = self.run_generation(req, label, dataclasses.replace(p, thinking=False), header=False)
            self._account(st)
            before = len(text)
            text = relay.merge([text, ans])
            log.add(f"continuación {n}", st.completion_tokens, pt)
            if stopped:
                return self._relay_end(log, text, thinks, True)
            if st.completion_tokens < p.max_tokens - 2:
                break                                   # terminó por sí mismo
            if len(text) - before < 40:
                warn("La continuación no avanza: se detiene el relevo.")
                break
            text = relay.trim_cut(text)
        else:
            warn(f"Se alcanzó el máximo de {self.s.relay_max} relevos.")
        return self._relay_end(log, text, thinks, False)

    def _relay_end(self, log, answer: str, thinks: list[str], stopped: bool) -> tuple[str, str, bool]:
        path = log.save(self.session_id)
        if path:
            total_pt = sum(s.get("prompt_tokens", 0) for s in log.steps)
            say(f"  [dim]Relevo: {len(log.steps)} ejecuciones · {total_pt} tokens de prompt en total · "
                f"registro: {render.short_path(path)}[/]")
        return relay.display(answer), "\n\n".join(thinks), stopped

    def _prompt_tokens(self, msgs: list[dict], thinking: bool) -> int:
        try:
            return int(self.engine.prompt_tokens(msgs, thinking))
        except Exception:  # noqa: BLE001
            return 0

    def _run_branches(self, system: dict, prompts: list[str], names: list[str], p: GenParams) -> list[str]:
        """Partes independientes en paralelo (Ollama atiende varias peticiones a la vez)."""
        import concurrent.futures as cf
        stop = threading.Event()

        def one(prompt):
            msgs = [system, {"role": "user", "content": prompt}]
            return "".join(t for k, t in self.engine.stream(msgs, dataclasses.replace(p, thinking=False), stop)
                           if k == "text")

        with console.status(f"[accent]Escribiendo {len(prompts)} partes en paralelo "
                            f"(hasta {self.s.relay_parallel} a la vez)…[/]", spinner="dots"):
            with cf.ThreadPoolExecutor(max_workers=self.s.relay_parallel) as ex:
                outs = [relay.display(o) for o in ex.map(one, prompts)]
        for n, o in zip(names, outs):
            render.tool("ok", "Parte lista", n[:60], f"{len(o.split())} palabras")
        return outs

    def _account(self, st: GenStats):
        self.stats.turns += 1
        self.stats.tokens_in += st.prompt_tokens
        self.stats.tokens_out += st.completion_tokens
        self.stats.gen_time += max(0.0, st.t_total - st.t_first)
        self.stats.prompt_time += st.t_first
        self.stats.interrupted += int(st.stopped)
        self.stats.last = st

    # ---------------------------------------------------------------- envío
    def _budget_reserve(self) -> int:
        return min(self.s.max_tokens, max(512, self.n_ctx // 4))

    def _fit_history(self, new_msg: dict) -> bool:
        """Descarta turnos antiguos si el prompt no cabe. Devuelve False si ni así cabe."""
        eng = self.engine
        limit = self.n_ctx - self._budget_reserve()
        dropped = 0
        while True:
            need = eng.prompt_tokens(self.messages([new_msg]), self.params.thinking)
            if need <= limit:
                break
            if len(self.history) < 2:
                return False
            del self.history[:2]
            self.thoughts = {k - 2: v for k, v in self.thoughts.items() if k >= 2}
            dropped += 2
        if dropped:
            warn(f"Contexto lleno: se descartaron {dropped} mensajes antiguos (usa /compact para resumirlos "
                 f"en lugar de perderlos, o /ctx para ampliarlo).")
        return True

    def _image_batches(self, user_text: str, images: list[dict]) -> str | None:
        """Procesa imágenes que no caben juntas: lotes -> notas de texto."""
        eng = self.engine
        sys_tokens = eng.count_tokens(self.system_text()) + 200
        per_batch = self.n_ctx - sys_tokens - min(1024, self._budget_reserve()) - 64
        groups: list[list[dict]] = [[]]
        used = 0
        for img in images:
            t = image_tokens(eng.projector, img["w"], img["h"])
            if t > per_batch:
                factor = math.sqrt(per_batch / t) * 0.92
                img = shrink_image(img, factor)
                warn(f"{img['name']} reducida a {img['w']}×{img['h']} px para que quepa en el contexto.")
                t = image_tokens(eng.projector, img["w"], img["h"])
            if used + t > per_batch and groups[-1]:
                groups.append([])
                used = 0
            groups[-1].append(img)
            used += t
        if len(groups) == 1:
            return None
        say(f"[info]Las {len(images)} imágenes no caben a la vez en {self.n_ctx} tokens: se procesan en "
            f"{len(groups)} lotes y se resumen.[/]")
        notes = []
        k = 0
        for gi, g in enumerate(groups, 1):
            names = ", ".join(i["name"] for i in g)
            instr = (f"Imágenes {k + 1}–{k + len(g)} de {len(images)} ({names}). Transcribe fielmente todo el "
                     f"texto legible y describe los elementos visuales relevantes (tablas, figuras, firmas). "
                     f"Luego el usuario preguntará: «{user_text[:500]}». No respondas aún a la pregunta; "
                     f"solo extrae la información.")
            msgs = [{"role": "system", "content": self.system_text()},
                    {"role": "user", "content": instr, "images": g}]
            ans, _, st, stopped = self.run_generation(msgs, f"{self.engine.name} · lote {gi}/{len(groups)}",
                                                      GenParams(**{**self.params.__dict__, "thinking": False,
                                                                   "max_tokens": min(1024, self._budget_reserve())}))
            self._account(st)
            notes.append(f"[Imágenes {k + 1}–{k + len(g)}: {names}]\n{ans}")
            k += len(g)
            if stopped:
                warn("Procesamiento por lotes interrumpido.")
                return None
        return "\n\n".join(notes)

    # ---------------------------------------------------------------- lector visual
    @contextlib.contextmanager
    def _reader_engine(self, rd: ModelEntry):
        """
        Enciende el modelo lector. Si no cabe junto al modelo de chat, descarga este
        primero y lo vuelve a cargar al terminar (la conversación no se pierde).
        """
        if self.entry is not None and self.entry.name == rd.name and self.vision:
            yield self.engine
            return
        main_entry, main_plan = self.entry, self.plan
        main_llama = isinstance(self.engine, LlamaEngine)
        side = self.make_plan(rd, ctx=reader.READER_CTX, gpu="auto", reclaim_current=False)
        together = side is not None and side.fits_vram and side.ngl >= max_gpu_layers(rd.info)
        swapped = False
        if not together and self.engine is not None:
            if main_llama:
                with console.status(f"[dim]Haciendo sitio en la GPU: descargando {main_entry.name}…[/]", spinner="dots"):
                    self.engine.close()
                    self.engine = None
                swapped = True
            elif isinstance(self.engine, OllamaEngine):
                self.engine.unload()
        plan = self.make_plan(rd, ctx=reader.READER_CTX, gpu="auto") if not together else side
        eng = None
        try:
            with console.status(f"[accent]Encendiendo el lector visual {rd.name}…[/]", spinner="dots"):
                eng = LlamaEngine(rd, plan.n_ctx, plan.ngl, "f16", False, self.s.flash_attn)
            if not eng.vision:
                raise EngineError(f"El lector {rd.name} no pudo activar su visión: {eng.vision_error}",
                                  ["/vision modelo <otro> para elegir otro lector", "/doctor"])
            yield eng
        finally:
            if eng is not None:
                eng.close()
            if swapped:
                with console.status(f"[dim]Volviendo a {main_entry.name}…[/]", spinner="dots"):
                    try:
                        self.engine = LlamaEngine(main_entry, main_plan.n_ctx, main_plan.ngl, self.s.kv_type,
                                                  self.s.swa_full, self.s.flash_attn)
                    except EngineError:
                        p2 = self.make_plan(main_entry)
                        self.engine = LlamaEngine(main_entry, p2.n_ctx, p2.ngl, self.s.kv_type,
                                                  self.s.swa_full, self.s.flash_attn)
                        self.plan = p2
                self._mark_ctx()

    def _transcribe(self, att: Attachment) -> str | None:
        """Transcribe las imágenes de un adjunto con el lector visual (usa la caché)."""
        rd = self.reader_entry
        if rd is None:
            raise EngineError("No hay ningún modelo con visión para leer imágenes.",
                              ["descarga un mmproj (mira /doctor) o usa un modelo con 👁"])
        imgs = att.images
        parts: list[tuple[str, str]] = []
        todo = []
        for i, img in enumerate(imgs, 1):
            label = img.get("name", f"imagen {i}")
            txt = reader.cached(img, rd.name)
            parts.append((label, txt or ""))
            if txt is None:
                todo.append((i - 1, img))
        if todo:
            with self._reader_engine(rd) as eng:
                for n, (idx, img) in enumerate(todo, 1):
                    msgs = [{"role": "system", "content": reader.MASTER_PROMPT},
                            {"role": "user", "content": reader.user_prompt(img, idx + 1, len(imgs)), "images": [img]}]
                    prm = GenParams(max_tokens=reader.READER_MAX_TOKENS, temperature=0.1, top_p=0.9, top_k=20,
                                    min_p=0.05, repeat_penalty=1.05, seed=-1, thinking=False)
                    title = f"Leyendo {img.get('name', 'imagen')}" + (f" ({n}/{len(todo)})" if len(todo) > 1 else "") \
                            + f" con {rd.name}"
                    ans, _, st, stopped = self.run_generation(msgs, rd.name, prm, engine=eng, tool_title=title)
                    self._account(st)
                    if stopped:
                        render.tool("warn", "Lectura cancelada", att.name)
                        return None
                    if not ans.strip():
                        ans = "[el lector no devolvió texto para esta imagen]"
                    ans = reader.fix_tables(ans)
                    if st.completion_tokens >= reader.READER_MAX_TOKENS - 2:
                        ans += "\n[la transcripción se cortó por longitud]"
                    reader.store(img, rd.name, ans)
                    parts[idx] = (parts[idx][0], ans)
        text = reader.format_for_chat(att.name, parts, rd.name)
        n_chars = sum(len(t) for _, t in parts)
        cached_n = len(imgs) - len(todo)
        detail = (f"{len(imgs)} imagen(es) → {n_chars:,} caracteres de texto".replace(",", ".")
                  + (f" · {cached_n} ya estaba(n) en memoria" if cached_n else "")
                  + f" · lector {rd.name}")
        render.tool("ok", "Transcrito", att.name, detail)
        self.last_transcripts = [(lbl, t) for lbl, t in parts]
        return text

    def send(self, text: str):
        if self.engine is None:
            error("No hay ningún modelo cargado. Usa /model.")
            return
        # @menciones
        for path, _ in find_mentions(text):
            try:
                att = load_any(path, vision=self.can_see(), settings=self.s)
                self.pending.append(att)
                render.tool("ok", "Leer archivo", att.name, att.note or att.summary())
            except AttachError as e:
                render.tool("err", "Leer archivo", path, str(e))
                return
        # imágenes -> texto con el lector visual (modo lector o modelo sin visión)
        if any(a.images for a in self.pending) and self.uses_reader():
            for a in self.pending:
                if not a.images:
                    continue
                try:
                    tr = self._transcribe(a)
                except EngineError as e:
                    error(str(e), e.hints)
                    return
                if tr is None:
                    return
                a.text = "\n\n".join(x for x in (a.text, tr) if x)
                a.images = []
        texts = [a.text for a in self.pending if a.text]
        images = [img for a in self.pending for img in a.images]
        if images and not self.vision:
            error("Hay imágenes adjuntas pero no hay ningún modelo con visión.",
                  ["descarga un mmproj (/doctor)", "/attachments clear para quitarlas"])
            return
        content = "\n\n".join(texts + [text])
        msg: dict = {"role": "user", "content": content}
        ws_sent = dict(self.workspace.sent) if self.workspace is not None else None
        if self.workspace is not None:
            room = (self.n_ctx - self.context_used() - self.params.max_tokens) * 3
            ctx_txt, names, skipped = self.workspace.fresh_context(int(min(40000, max(2000, room * 0.35))))
            if ctx_txt:
                msg["content"] = ctx_txt + "\n\n[Petición del usuario]\n" + content
                render.tool("ok", "Contexto del proyecto", f"{len(names)} archivo(s) adjuntados",
                            ", ".join(names)[:160] + (f" · {len(skipped)} grandes se leen bajo demanda" if skipped else ""))
        if images:
            msg["images"] = images
            limit = self.n_ctx - self._budget_reserve()
            need = self.engine.prompt_tokens(self.messages([msg]), self.params.thinking)
            if need > limit:
                notes = self._image_batches(text, images)
                if notes is not None:
                    msg = {"role": "user", "content": "\n\n".join(
                        texts + ["[Contenido extraído de las imágenes adjuntas]\n" + notes, text])}
        if not self._fit_history(msg):
            if ws_sent is not None:
                self.workspace.sent = ws_sent
            need = self.engine.prompt_tokens(self.messages([msg]), self.params.thinking)
            error(f"El mensaje con sus adjuntos ocupa ~{need} tokens y el contexto es de {self.n_ctx} "
                  f"(reservando {self._budget_reserve()} para la respuesta).",
                  ["/ctx 32k (mira /mem antes)", "/pdf ruta auto 1-5 para menos páginas",
                   "/set image_max_side 896 para imágenes más pequeñas"])
            return
        self.pending = []
        self.history.append(msg)
        from . import webkit
        self.turn_query = text
        self.turn_components = (self.turn_components or (self.s.components_in_chat == "auto"
                                and webkit.wants_components(text, self.last_reply)))
        if self.turn_components:
            render.tool("info", "Componentes web", "se usan en esta respuesta",
                        "el modelo escribe pagina.lai y el programa genera HTML, CSS y JS")
        if self.workspace is not None:
            self.workspace.begin(text)
        label = f"{self.engine.name}" + (" · 👁" if images and "images" in msg else "")
        try:
            answer, think, stopped = self.relay_chain(self.messages(), content, label)
        except EngineError as e:
            self.history.pop()
            self.turn_components = False
            if ws_sent is not None:
                self.workspace.sent = ws_sent
            error(str(e), e.hints)
            return
        finally:
            self._mark_ctx()
        st = self.stats.last or GenStats()
        if not answer:
            self.history.pop()
            if ws_sent is not None:
                self.workspace.sent = ws_sent
            if think:
                self.last_think = think
                warn("El modelo solo razonó y se quedó sin tokens antes de responder: sube /tokens o usa /think off.")
            elif not stopped:
                warn("El modelo devolvió una respuesta vacía. Prueba otra vez, baja /temp o revisa /debug.")
            self._mark_ctx()
            return
        self.history.append({"role": "assistant", "content": answer})
        if think:
            self.thoughts[len(self.history) - 1] = think
            self.last_think = think
        self.last_reply = answer
        self._mark_ctx()
        self.autosave()
        if not self.s.relay_auto and not stopped and st.completion_tokens >= self.params.max_tokens - 2:
            say(f"  [warning]La respuesta llegó al máximo de {self.params.max_tokens} tokens y puede estar cortada:[/] "
                f"[dim]escribe «continúa» o sube el límite con /tokens 8k[/]")
        try:
            if self.workspace is not None:
                self._project_turn(answer, text, label, stopped)
            else:
                self._offer_files(answer, text)
        finally:
            self.turn_components = False
            self._mark_ctx()

    # ---------------------------------------------------------------- proyecto
    def _project_turn(self, answer: str, goal: str, label: str, stopped: bool):
        """Aplica archivos y ediciones; si el modelo pidió leer o buscar, le devuelve el resultado y sigue."""
        ws = self.workspace
        rounds = 0
        nudged = False
        while True:
            n_before = len(ws.current.changes) if ws.current else 0
            errors = self._offer_files(answer, goal) or []
            acts = wsmod.parse_actions(answer)
            results, errs, applied = self._apply_actions(acts)
            errors += errs
            changed = (len(ws.current.changes) if ws.current else 0) > n_before
            nudge = (not nudged and not changed and not errors and acts.empty
                     and wsmod.announces_without_acting(answer))
            nudged |= nudge
            if stopped or not (acts.needs_reply or errors or nudge):
                break
            if rounds >= max(1, self.s.project_rounds):
                warn(f"Se alcanzó el máximo de {self.s.project_rounds} rondas de herramientas en este mensaje "
                     "(/set project_rounds para cambiarlo).")
                break
            rounds += 1
            budget = max(4000, int((self.n_ctx - self.context_used() - self.params.max_tokens) * 3))
            body = results if len(results) <= budget else (
                results[:budget] + "\n[…resultado recortado por falta de contexto: pide rangos con desde/hasta]")
            parts = ["[Resultado de tus herramientas · mensaje automático del programa]"]
            if applied:
                parts.append("Cambios aplicados: " + "; ".join(applied))
            if errors:
                parts.append("ERRORES (corrígelos):\n" + "\n".join(f"- {e}" for e in errors))
            if nudge:
                parts.append("No escribiste ninguna etiqueta ni bloque, así que NO se ha hecho nada. Hazlo ahora: "
                             "escribe los bloques <editar ruta=\"…\"> con BUSCAR/REEMPLAZAR (o ```lenguaje ruta``` "
                             "para archivos nuevos), o <leer ruta=\"…\"/> si te falta ver algún archivo.")
            if body:
                parts.append(body)
            parts.append("Continúa con la tarea del usuario. No repitas lo ya hecho.")
            self.history.append({"role": "user", "content": "\n\n".join(parts)})
            self._mark_ctx()
            try:
                answer, think, stopped = self.relay_chain(self.messages(), goal, label)
            except EngineError as e:
                self.history.pop()
                error(str(e), e.hints)
                break
            if not answer:
                self.history.pop()
                break
            self.history.append({"role": "assistant", "content": answer})
            self.last_reply = answer
            if think:
                self.thoughts[len(self.history) - 1] = think
        cs = ws.commit()
        if cs:
            self.last_saved = [ws.root / c.rel for c in cs.changes.values() if c.kind != "borrado"]
            render.tool("ok", "Cambios en el proyecto", f"{len(cs.changes)} archivo(s) en {ws.name}",
                        wsmod.summary(cs) + " · /deshacer los revierte · /cambios diff los muestra")
        self.autosave()

    def _apply_actions(self, acts: "wsmod.Actions") -> tuple[str, list[str], list[str]]:
        """Ejecuta las etiquetas del modelo. Devuelve (resultados para el modelo, errores, cambios aplicados)."""
        ws = self.workspace
        out, errs, applied = [], [], []
        for rel, a, b in acts.reads:
            try:
                txt = ws.read(rel, a, b)
                out.append(txt)
                render.tool("ok", "Leer", rel, f"{txt.count(chr(10)) - 2} líneas" + (f" ({a}–{b})" if a or b else ""))
            except (ValueError, OSError) as e:
                errs.append(f"<leer {rel}>: {e}")
                render.tool("err", "Leer", rel, str(e))
        for rel in acts.lists:
            try:
                out.append(ws.listing(rel))
                render.tool("ok", "Listar", rel)
            except (ValueError, OSError) as e:
                errs.append(f"<listar {rel}>: {e}")
        for pat in acts.greps:
            res = ws.grep(pat)
            out.append(res)
            render.tool("ok", "Buscar", pat[:60], f"{max(0, res.count(chr(10)))} coincidencias")
        for rel, pairs in acts.edits:
            try:
                path, e = ws.edit(rel, pairs)
            except (ValueError, OSError) as ex:
                path, e = None, [f"{rel}: {ex}"]
            if path is not None:
                n_ok = len(pairs) - len(e)
                applied.append(f"{rel} ({n_ok} edición(es))")
                render.tool("ok", "Editar", rel, f"{n_ok} de {len(pairs)} cambio(s) aplicados")
            for x in e:
                render.tool("err", "Editar", rel, x)
            errs += e
        for search, repl, hint in acts.loose:
            hits = ws.locate(search)
            norm = hint.replace("\\", "/").lstrip("./")
            target = next((h for h in hits if h == norm or h.endswith("/" + norm)), None) or (hits[0] if len(hits) == 1 else None)
            if target is None:
                why = "no encontré ese texto en ningún archivo" if not hits else f"aparece en varios archivos ({', '.join(hits[:4])})"
                errs.append(f"BUSCAR/REEMPLAZAR sin archivo: {why}; ponlo dentro de <editar ruta=\"…\">")
                render.tool("err", "Editar", "(sin ruta)", why)
                continue
            path, e = ws.edit(target, [(search, repl)])
            if path is not None:
                applied.append(f"{target} (1 edición)")
                render.tool("ok", "Editar", target, "1 cambio aplicado (archivo deducido por su contenido)")
            for x in e:
                render.tool("err", "Editar", target, x)
            errs += e
        for rel, text in acts.writes:
            try:
                existed = ws.resolve(rel).exists()
                ws.write(rel, text)
                applied.append(f"{rel} {'reescrito' if existed else 'creado'}")
                render.tool("ok", "Archivo " + ("actualizado" if existed else "creado"), rel, f"{text.count(chr(10)) + 1} líneas")
            except (ValueError, OSError) as e:
                errs.append(f"{rel}: {e}")
                render.tool("err", "Escribir archivo", rel, str(e))
        for rel in acts.deletes:
            try:
                ws.delete(rel)
                applied.append(f"{rel} borrado")
                render.tool("ok", "Borrar", rel, "copia guardada · /deshacer lo recupera")
            except (ValueError, OSError) as e:
                errs.append(f"<borrar {rel}>: {e}")
                render.tool("err", "Borrar", rel, str(e))
        return "\n\n".join(out), errs, applied

    def open_project(self, path: str) -> bool:
        target = Path(os.path.expandvars(path.strip().strip('"'))).expanduser()
        if not target.is_absolute():
            target = Path.cwd() / target
        if not target.exists():
            if not self.confirm(f"La carpeta {target} no existe. ¿Crearla?", default=True):
                return False
            target.mkdir(parents=True)
        try:
            ws = wsmod.Workspace(target, self.session_id)
        except ValueError as e:
            error(str(e))
            return False
        home = Path.home().resolve()
        if ws.root in (home, Path(ws.root.anchor)) or ws.root in home.parents:
            if not self.confirm(f"{ws.root} es tu carpeta personal o la raíz del disco: el modelo podría modificar "
                                "cualquier archivo dentro. ¿Abrirla igualmente?", default=False):
                return False
        os.chdir(ws.root)
        self.workspace, self.project_dir = ws, ws.root
        self.memory_text = load_memory_text()
        rec = [str(ws.root)] + [r for r in self.s.recent_projects if r != str(ws.root)]
        self.s.recent_projects = rec[:8]
        save_settings(self.s)
        n = len(ws.walk())
        ws.instructions()
        tree_tok = len(ws.tree_cache) // 3
        render.tool("ok", "Proyecto abierto", ws.name,
                    f"{n} archivos · estructura ≈{tree_tok} tokens · el modelo puede leer, crear y editar archivos "
                    f"aquí · /deshacer revierte · /proyecto cerrar")
        if self.memory_text and (ws.root / "LOCAL_AI.md").exists():
            say("  [dim]+ instrucciones del proyecto (LOCAL_AI.md)[/]")
        self._mark_ctx()
        return True

    def cmd_proyecto(self, arg: str):
        a = arg.strip()
        if a.lower() in ("cerrar", "close", "salir"):
            if self.workspace is None:
                say("[dim]No hay ningún proyecto abierto.[/]")
                return
            name = self.workspace.name
            self.workspace = None
            self.project_dir = None
            self._mark_ctx()
            ok(f"Proyecto {name} cerrado: los archivos nuevos vuelven a /archivos.")
            return
        if a.lower() in ("recargar", "refrescar", "reload"):
            if self.workspace:
                self.workspace.refresh()
                self.workspace.instructions()
                self._mark_ctx()
                ok(f"Estructura de {self.workspace.name} actualizada ({len(self.workspace.walk())} archivos).")
            return
        if a:
            self.open_project(a)
            return
        if self.workspace is not None:
            ws = self.workspace
            n_changes = sum(len(c.changes) for c in ws.history)
            say(f"[accent]Proyecto:[/] {escape(ws.name)} [dim]· {escape(str(ws.root))} · {len(ws.walk())} archivos · "
                f"{len(ws.history)} turno(s) con cambios ({n_changes} archivos) · leídos: {len(ws.read_files)}[/]")
            say("[dim]/proyecto <ruta> abre otro · /proyecto cerrar · /cambios · /deshacer[/]")
            return
        from .inputbox import Choice, select
        choices = [Choice(r, Path(r).name, r) for r in self.s.recent_projects if Path(r).is_dir()]
        choices.append(Choice(".", "Carpeta actual", os.getcwd()))
        sel = select("Abrir proyecto (el modelo podrá modificar sus archivos)", choices, render.current)
        if sel:
            self.open_project(sel)

    def cmd_cambios(self, arg: str):
        ws = self.workspace
        if ws is None or not ws.history:
            say("[dim]El modelo no ha cambiado archivos de un proyecto en esta sesión.[/]")
            return
        if arg.strip().lower().startswith("diff"):
            from rich.syntax import Syntax
            console.print(Syntax(ws.diff(), "diff", theme=render.current.code, word_wrap=True))
            return
        for i, cs in enumerate(ws.history, 1):
            say(f"  [accent]{i}.[/] {time.strftime('%H:%M', time.localtime(cs.when))} [dim]«{escape(cs.label)}»[/]\n"
                f"     {escape(wsmod.summary(cs))}")
        say("[dim]/cambios diff · /deshacer revierte el último[/]")

    def cmd_deshacer(self, arg: str):
        ws = self.workspace
        if ws is None:
            say("[dim]No hay ningún proyecto abierto.[/]")
            return
        cs = ws.undo()
        if cs is None:
            say("[dim]No hay cambios que deshacer.[/]")
            return
        self._mark_ctx()
        ok(f"Deshecho «{cs.label}»: {wsmod.summary(cs)}")
        self.history.append({"role": "user", "content": "[Mensaje automático: el usuario ha deshecho tus últimos "
                             f"cambios en los archivos ({wsmod.summary(cs)}). Ya no están en el disco.]"})
        self.history.append({"role": "assistant", "content": "Entendido: esos cambios se han revertido."})

    def cmd_plantilla(self, arg: str):
        if not arg.strip():
            say("[dim]Uso: /plantilla landing de una cafetería con carta, reservas y contacto[/]")
            return
        self.turn_components = True
        self.send("Crea esta página web con los componentes prediseñados (un único bloque ```lai pagina.lai```): "
                  + arg.strip())

    # ---------------------------------------------------------------- archivos
    def _save_blocks(self, items: list, ask: bool, question: str) -> list[Path]:
        cwd = Path.cwd().resolve()
        overwrite = False
        for b, t in items:
            exists = t.exists()
            overwrite |= exists
            rel = t.relative_to(cwd) if cwd in t.parents else t
            state = (f"[warning]sobrescribe ({t.stat().st_size:,} B)[/]".replace(",", ".")
                     if exists else "[ok]nuevo[/]")
            say(f"  💾 [accent]{escape(str(rel))}[/] · {b.lang or 'texto'} · {b.lines} líneas · {state}")
        if ask and not self.confirm(question, default=not overwrite):
            say("[dim]No se ha guardado nada. (/save <ruta> cuando quieras)[/]")
            return []
        saved = []
        for b, t in items:
            try:
                n = filemod.write(t, b.code)
                saved.append(t)
                ok(f"Guardado {t} ({n:,} bytes)".replace(",", "."))
            except OSError as e:
                error(f"No se pudo escribir {t}: {e}")
        if saved:
            self.last_saved = saved
            if any(t.suffix.lower() in (".html", ".htm", ".svg") for t in saved):
                say("[dim]  → /open para verlo en el navegador[/]")
        return saved

    def _offer_files(self, answer: str, user_text: str = "") -> list[str]:
        """Guarda los bloques con ruta. En modo proyecto devuelve los errores para el modelo."""
        ws = self.workspace
        if self.s.save_files == "never" and ws is None:
            return []
        blocks = filemod.extract_blocks(answer)
        filemod.names_from_request(blocks, user_text)
        if ws is not None:
            return self._write_project_blocks(blocks)
        if self.s.save_files == "auto":
            # se guardan solos en la carpeta de memoria de la sesión, sin preguntar
            saved = []
            for b in blocks:
                if not b.path:
                    continue
                try:
                    t = filemod.safe_target(b.path, self.files_dir)
                    existed = t.exists()
                    n = filemod.write(t, b.code)
                    saved.append(t)
                    where = render.short_path(t)
                    if b.closed and t.suffix == ".lai":
                        from .zero import build_page
                        build_page(t)
                        saved.append(t.with_name("index.html" if t.stem.lower() in ("pagina", "index", "page", "web")
                                                 else f"{t.stem}.html"))
                    if b.closed:
                        render.tool("ok", "Archivo " + ("actualizado" if existed else "creado"), b.path,
                                    f"{b.lines} líneas · {n:,} bytes · {where}".replace(",", "."))
                    else:
                        render.tool("warn", "Archivo INCOMPLETO", b.path,
                                    f"la respuesta se cortó ({b.lines} líneas) · escribe «continúa» o sube /tokens · {where}")
                except (ValueError, OSError) as e:
                    render.tool("err", "Crear archivo", b.path, str(e))
            if saved:
                self.last_saved = saved
                hint = "/open lo abre" + (" en el navegador" if any(t.suffix.lower() in (".html", ".htm", ".svg") for t in saved) else "")
                say(f"  [dim]{hint} · /save <ruta> lo copia a otra carpeta · /archivos los lista[/]")
                return []
        cwd = Path.cwd()
        items = []
        for b in blocks:
            if not b.path:
                continue
            try:
                items.append((b, filemod.safe_target(b.path, cwd)))
            except ValueError as e:
                warn(str(e))
        if items:
            self._save_blocks(items, True, f"¿Guardar {len(items)} archivo(s) propuesto(s) por el modelo?")
            return []
        loose = [b for b in blocks if b.lines >= 5 and filemod.EXT.get(b.lang.lower())]
        if loose:
            b = max(loose, key=lambda x: x.lines)
            extra = f" · hay {len(loose)} bloques: /save <ruta> <nº>" if len(loose) > 1 else ""
            say(f"[dim]  💾 /save {b.default_name()} guarda el bloque {b.lang} ({b.lines} líneas){extra}[/]")
        return []

    def _write_project_blocks(self, blocks: list) -> list[str]:
        ws = self.workspace
        errors = []
        for b in blocks:
            if not b.path:
                continue
            try:
                t = ws.resolve(b.path)
                rel = ws.rel(t)
                if not b.closed and t.exists():
                    raise ValueError("la respuesta se cortó antes de cerrar el bloque: no se sobrescribe el archivo "
                                     "existente con contenido incompleto")
                existed = t.exists()
                ws.write(rel, b.code)
                render.tool("ok" if b.closed else "warn", ("Archivo actualizado" if existed else "Archivo creado")
                            + ("" if b.closed else " (INCOMPLETO)"), rel, f"{b.lines} líneas")
                if b.closed and t.suffix == ".lai":
                    from .zero import build_page
                    out = t.with_name("index.html" if t.stem.lower() in ("pagina", "index", "page", "web")
                                      else f"{t.stem}.html")
                    ws.track([out, t.parent / "css" / "styles.css", t.parent / "js" / "app.js"])
                    build_page(t)
            except (ValueError, OSError) as e:
                errors.append(f"{b.path}: {e}")
                render.tool("err", "Escribir archivo", b.path, str(e))
        return errors

    def autosave(self):
        try:
            sessions.save(sessions.autosave_path(self.session_id), self.state())
            sessions.prune_autosaves()
        except Exception:
            pass

    def state(self) -> dict:
        return {"model": self.entry.name if self.entry else None, "system": self.s.system_prompt,
                "history": self.history, "created": self.stats.started, "cwd": os.getcwd()}

    # ======================================================================
    # Barra inferior
    # ======================================================================

    def toolbar(self):
        """(izquierda, derecha) como el pie de Gemini CLI: carpeta | modelo, contexto y memoria."""
        cwd = os.getcwd()
        home = str(Path.home())
        if cwd.lower().startswith(home.lower()):
            cwd = "~" + cwd[len(home):]
        if len(cwd) > 30:
            parts = Path(cwd).parts
            cwd = "…\\" + "\\".join(parts[-2:]) if len(parts) > 2 else cwd
        left = [("class:toolbar.user", f" {cwd}")]
        if self.pending:
            ni = sum(len(a.images) for a in self.pending)
            nd = sum(1 for a in self.pending if a.text)
            left += [("class:toolbar.dim", "  "), ("class:toolbar.mid", f"📎 {nd + ni} adjunto(s)")]
        if self.params.thinking:
            left += [("class:toolbar.dim", "  💭 razonamiento")]
        right = []
        if self.engine is None:
            right.append(("class:toolbar.bad", "sin modelo · /model "))
            return left, right
        name = self.engine.name.replace(".gguf", "")
        if len(name) > 26:
            name = name[:24] + "…"
        right.append(("class:toolbar.accent", name))
        if self.vision and self.s.vision_mode == "directo":
            right.append(("class:toolbar", " 👁"))
        elif self.can_see():
            right.append(("class:toolbar.dim", " 👁 lector"))
        n = self.n_ctx
        if n:
            used = self.context_used()
            free = max(0.0, 1 - used / n)
            lvl = "ok" if free > 0.3 else ("mid" if free > 0.1 else "bad")
            right += [("class:toolbar.dim", " · "), (f"class:toolbar.{lvl}", f"{free:.0%}"),
                      ("class:toolbar.dim", " ctx libre")]
        sm = probe(max_age=2.0)
        if sm.vram_total:
            vr = sm.vram_used / sm.vram_total
            right += [("class:toolbar.dim", " · VRAM "),
                      (f"class:toolbar.{'ok' if vr < .8 else 'mid' if vr < .95 else 'bad'}",
                       f"{'≈' if sm.free_estimated else ''}{render.gb(sm.vram_used)}/{render.gb(sm.vram_total)}G")]
        right.append(("class:toolbar", " "))
        return left, right

    # ======================================================================
    # REPL
    # ======================================================================

    def print_banner(self):
        e = self.engine
        rd = self.reader_entry
        if e is None:
            status = "[bad]●[/] sin modelo cargado · usa [accent]/model[/]"
        else:
            status = f"[ok]●[/] [bold]{escape(e.name.replace('.gguf', ''))}[/] [dim]· ctx {render.human_tokens(self.n_ctx)}"
            if isinstance(e, LlamaEngine) and self.plan:
                status += f" · {self.plan.ngl}/{max_gpu_layers(self.entry.info)} capas GPU"
            status += "[/]"
            if self.vision and self.s.vision_mode == "directo":
                status += " [dim]· 👁 visión propia[/]"
            elif rd is not None:
                status += f" [dim]· 👁 lector {escape(rd.name.replace('.gguf', ''))}[/]"
        if self.memory_text:
            status += f"\n[dim]  memoria: {', '.join(str(f) for f in memory_files())}[/]"
        tips = [
            "Pregunta, pide código o archivos: los que cree el modelo se guardan solos ([accent]/archivos[/]).",
            "Adjunta con [accent]@ruta[/]; imágenes y PDFs escaneados los transcribe el lector visual.",
            "[accent]/model[/] cambia de modelo · [accent]/settings[/] ajustes · [accent]/help[/] comandos · "
            "[accent]?[/] atajos.",
        ]
        render.banner(__version__, status, tips)

    def repl(self):
        from .inputbox import InputBox
        no_arg = {c for c, sp in self.commands.items() if not sp.args}
        self.box = InputBox(self.commands, HISTORY_FILE, self.toolbar, render.current,
                            on_copy=lambda: bool(self.last_reply) and copy_to_clipboard(self.last_reply),
                            no_arg_cmds=no_arg)
        self.print_banner()
        input_errors = 0
        while self.running:
            try:
                text = self.box.prompt()
                input_errors = 0
            except KeyboardInterrupt:
                continue
            except EOFError:
                break
            except Exception as e:  # noqa: BLE001 - la caja de entrada no debe tumbar el CLI
                input_errors += 1
                self._report_crash("caja de entrada", e)
                if input_errors >= 3:
                    error("La caja de entrada falla repetidamente; prueba con LOCALAI_VT_INPUT=1 (ver crash.log).")
                    break
                continue
            if text is None:
                break
            if text == "/__clear_screen__":
                console.clear()
                self.print_banner()
                continue
            text = text.strip()
            if not text:
                continue
            try:
                self._echo_user(text)
                if text.startswith("!"):
                    self.shell(text)
                elif text.startswith("/") and not text.startswith("//"):
                    self.handle_command(text)
                else:
                    self.send(text[1:] if text.startswith("//") else text)
            except KeyboardInterrupt:
                warn("Interrumpido.")
            except Exception as e:  # noqa: BLE001 - un fallo en un turno no debe cerrar la sesión
                self._report_crash(text[:80], e)
        save_settings(self.s)
        say("[dim]Sesión guardada. ¡Hasta pronto! (/resume para retomarla)[/]")

    def _report_crash(self, where: str, exc: BaseException):
        import traceback
        tb = "".join(traceback.format_exception(exc))
        try:
            with open(CRASH_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} · {where} · "
                        f"modelo={self.engine.name if self.engine else '-'}\n{tb}")
        except OSError:
            pass
        error(f"Error inesperado ({exc.__class__.__name__}: {exc}). La sesión sigue abierta.",
              [f"traza completa en {CRASH_LOG}"])

    def _echo_user(self, text: str):
        render.user_echo(text)

    # ======================================================================
    # Shell
    # ======================================================================

    def shell(self, text: str):
        attach = text.startswith("!!")
        cmd = text[2:].strip() if attach else text[1:].strip()
        if not cmd:
            say("[dim]Uso: !comando (ejecuta) · !!comando (ejecuta y añade la salida al contexto)[/]")
            return
        m = re.match(r"^cd\s+(.+)$", cmd)
        if m:
            target = Path(os.path.expandvars(m.group(1).strip().strip('"'))).expanduser()
            try:
                os.chdir(target)
                ok(f"Directorio: {os.getcwd()}")
                self.memory_text = load_memory_text()
            except OSError as e:
                error(str(e))
            return
        if self.s.shell == "cmd":
            argv = f'chcp 65001>nul & {cmd}'
            run_kw = dict(shell=True)
        else:
            argv = ["powershell", "-NoProfile", "-Command",
                    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + cmd]
            run_kw = {}
        t0 = time.time()
        try:
            with console.status(f"[dim]$ {cmd}[/]", spinner="dots"):
                pr = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=300, cwd=os.getcwd(), **run_kw)
        except subprocess.TimeoutExpired:
            error("El comando superó 5 minutos y se canceló (los comandos interactivos no están soportados).")
            return
        except FileNotFoundError as e:
            error(f"No se pudo ejecutar: {e}")
            return
        out = (pr.stdout or "") + (("\n" + pr.stderr) if pr.stderr else "")
        out = out.rstrip()
        lines = out.splitlines()
        shown = "\n".join(lines[-200:]) if len(lines) > 200 else out
        style = "border" if pr.returncode == 0 else "error"
        console.print(Panel(Text(shown or "(sin salida)"), title=f"$ {cmd}", title_align="left",
                            subtitle=f"código {pr.returncode} · {time.time() - t0:.1f}s", border_style=style))
        self.last_shell = f"[Salida de `{cmd}` (código {pr.returncode})]\n```\n{out[-20000:]}\n```"
        if attach:
            self.pending.append(Attachment(f"$ {cmd}", "", "text", text=self.last_shell))
            ok("Salida añadida al contexto del próximo mensaje.")

    # ======================================================================
    # Comandos
    # ======================================================================

    HELP_GROUPS = [
        ("Conversación", ["/clear", "/compact", "/copy", "/export", "/system", "/think", "/memory"]),
        ("Proyecto", ["/proyecto", "/cambios", "/deshacer"]),
        ("Crear", ["/plantilla", "/zero", "/componentes"]),
        ("Modelos y memoria", ["/model", "/modo", "/models", "/setup", "/mem", "/ctx", "/gpu", "/kv", "/swa", "/auto",
                               "/doctor"]),
        ("Archivos y visión", ["/file", "/image", "/pdf", "/attachments", "/vision", "/archivos", "/save", "/open",
                               "/mmproj"]),
        ("Sesiones", ["/resume", "/chat", "/stats"]),
        ("Ajustes", ["/settings", "/theme", "/tokens", "/temp", "/top_p", "/top_k", "/min_p", "/repeat", "/seed",
                     "/params", "/set", "/debug", "/exit"]),
    ]

    def cmd_help(self, arg: str):
        for title, names in self.HELP_GROUPS:
            tb = Table(box=None, padding=(0, 2), show_header=False)
            tb.add_column(style="command", no_wrap=True)
            tb.add_column(style="dim", no_wrap=True)
            tb.add_column()
            for name in names:
                sp = self.commands.get(name)
                if sp:
                    tb.add_row(name, escape(sp.args), escape(sp.desc))
            console.print(Text(title, style="bold"))
            console.print(tb)
        console.print(Text("Atajos", style="bold"))
        keys = Table(box=None, padding=(0, 2), show_header=False)
        keys.add_column(style="accent", no_wrap=True)
        keys.add_column()
        for k, d in [("@ruta", "adjuntar archivo, imagen o PDF (Tab autocompleta)"),
                     ("!cmd / !!cmd", "ejecutar en la shell / y además pasar la salida al contexto"),
                     ("Enter", "enviar  ·  Alt+Enter, Shift+Enter, Ctrl+J o \\ al final: nueva línea"),
                     ("↑ / ↓", "historial  ·  Ctrl+R búsqueda  ·  → acepta la sugerencia gris"),
                     ("Esc", "cancelar la respuesta (sin cerrar el CLI)"),
                     ("?", "mostrar u ocultar los atajos bajo la caja de entrada"),
                     ("Ctrl+K", "copiar la última respuesta  ·  Ctrl+L limpiar pantalla"),
                     ("Ctrl+C", "borrar la línea · dos veces seguidas: salir  ·  Ctrl+D salir")]:
            keys.add_row(k, d)
        console.print(keys)

    def cmd_exit(self, arg: str):
        self.running = False

    def cmd_clear(self, arg: str):
        self.history, self.thoughts, self.pending = [], {}, []
        self.last_reply = ""
        self.session_id = time.strftime("%Y%m%d-%H%M%S")
        if isinstance(self.engine, LlamaEngine):
            try:
                self.engine.llm.reset()
            except Exception:
                pass
        self._mark_ctx()
        console.clear()
        self.print_banner()
        ok("Conversación borrada.")

    # ---------------------------------------------------------------- modelos
    def _model_meta(self, m: ModelEntry, plans: bool = True) -> str:
        vis = {"si": "👁 visión", "falta_mmproj": "⚠ falta mmproj", "no": "texto"}.get(m.vision, "?")
        if m.kind == "ollama":
            return f"{m.size_gb:5.1f} GB · Ollama · {m.extra.get('params', '')} {m.extra.get('quant', '')}"
        meta = f"{m.size_gb:5.1f} GB · {vis:15} · {m.arch:8} · máx {render.human_tokens(m.ctx_max)}"
        if plans:
            p = self.make_plan(m)
            if p:
                fitc = "ok" if p.fits_vram and p.fits_ram else "✖ no cabe"
                meta += (f" · auto: gpu {p.ngl}/{max_gpu_layers(m.info)}, ctx {render.human_tokens(p.n_ctx)} → "
                         f"VRAM {render.gb(p.est.vram)} G, RAM {render.gb(p.est.ram)} G {fitc if fitc != 'ok' else ''}")
        return meta

    def cmd_models(self, arg: str):
        self.catalog = build_catalog(self.s)
        tb = Table(title="Modelos disponibles", title_style="accent", border_style="border")
        for c, j in [("", "left"), ("Modelo", "left"), ("Tamaño", "right"), ("Arquitectura", "left"),
                     ("Visión", "left"), ("Ctx máx", "right"), ("Auto (gpu · ctx · VRAM/RAM)", "left")]:
            tb.add_column(c, justify=j)
        for m in self.catalog.models:
            cur = "●" if self.entry and m.name == self.entry.name else ""
            if m.kind == "ollama":
                tb.add_row(cur, m.name, f"{m.size_gb:.1f} GB", m.arch, "?", "-", "Ollama")
                continue
            vis = {"si": f"[ok]👁 {Path(m.mmproj.path).name if m.mmproj else ''}[/]",
                   "falta_mmproj": "[warning]⚠ falta mmproj[/]", "no": "[dim]texto[/]"}[m.vision]
            p = self.make_plan(m)
            auto = (f"{p.ngl}/{max_gpu_layers(m.info)} · {render.human_tokens(p.n_ctx)} · "
                    f"{render.gb(p.est.vram)}/{render.gb(p.est.ram)} GB" + ("" if p.fits_vram else " [bad]✖[/]")) if p else "-"
            tb.add_row(cur, m.name, f"{m.size_gb:.1f} GB", m.arch, vis, render.human_tokens(m.ctx_max), auto)
        console.print(tb)
        missing = [m for m in self.catalog.models if m.vision == "falta_mmproj"]
        if missing:
            say("[warning]⚠ Modelos multimodales sin mmproj (se cargan como texto):[/] " + ", ".join(m.name for m in missing)
                + "  [dim]→ /doctor para ver qué descargar[/]")

    def cmd_model(self, arg: str):
        self.catalog = build_catalog(self.s)
        entry = None
        if arg:
            entry = self.catalog.find(arg)
            if entry is None and Path(arg.strip('"')).exists():
                entry = entry_from_path(arg.strip('"'), self.catalog.projectors)
            if entry is None:
                cands = [m.name for m in self.catalog.models if arg.lower() in m.name.lower()]
                raise ValueError(f"No encuentro el modelo '{arg}'." + (f" ¿Quizá: {', '.join(cands)}?" if cands else " (/models)"))
        else:
            from .inputbox import Choice, select
            with console.status("[dim]Calculando estimaciones de memoria…[/]", spinner="dots"):
                choices = [Choice(m, ("● " if self.entry and m.name == self.entry.name else "  ") + m.name,
                                  self._model_meta(m)) for m in self.catalog.models]
            cur = next((i for i, c in enumerate(choices) if self.entry and c.value.name == self.entry.name), 0)
            entry = select("Elige un modelo", choices, render.current, cur)
            if entry is None:
                say("[dim]Cancelado.[/]")
                return
        if entry.kind == "ollama":
            self.load(entry, None)
            return
        if entry.vision == "falta_mmproj":
            warn(f"{entry.name}: {entry.vision_note}")
        plan = self.make_plan(entry)
        if not self.check_plan(plan):
            say("[dim]Cancelado.[/]")
            return
        self.load(entry, plan)

    def cmd_mem(self, arg: str):
        parts = arg.split()
        entry = self.entry
        ctx = gpu = None
        rest = []
        for p_ in parts:
            m = self.catalog.find(p_)
            if m is not None and m.kind == "gguf":
                entry = m
            else:
                rest.append(p_)
        if rest:
            ctx = rest[0] if rest[0] == "auto" else parse_size(rest[0])
        if len(rest) > 1:
            gpu = rest[1]
        if entry is None or entry.kind != "gguf":
            raise ValueError("Uso: /mem [modelo.gguf] [ctx] [gpu]  (Ollama gestiona su propia memoria)")
        if entry is self.entry and self.plan is not None:   # por defecto: la configuración cargada
            ctx = self.n_ctx if ctx is None else ctx
            gpu = self.plan.ngl if gpu is None else gpu
        plan = self.make_plan(entry, ctx=ctx, gpu=gpu)
        real = self.engine.real if (isinstance(self.engine, LlamaEngine) and entry is self.entry and not parts) else None
        render.mem_report(plan.est, plan.sysmem, plan.vram_budget, plan.ram_budget, real=real, reclaim=plan.reclaim,
                          title=f"Estimación · {entry.name} · ctx {plan.n_ctx} · gpu {plan.ngl}/{max_gpu_layers(entry.info)}")
        if not plan.fits_vram or not plan.fits_ram:
            for sgg in suggestions(entry.info, plan.vram_budget, plan.ram_budget, plan.n_ctx, plan.ngl,
                                   kv_type=self.s.kv_type, swa_full=self.s.swa_full, flash_attn=self.s.flash_attn,
                                   mmproj=entry.mmproj):
                say(f"   [dim]→[/] {sgg}")

    def cmd_ctx(self, arg: str):
        if not arg:
            if self.engine is None:
                raise ValueError("No hay modelo cargado.")
            used = self.context_used()
            say(f"Contexto actual: [accent]{self.n_ctx}[/] tokens · usado ~{used} ({used / max(self.n_ctx, 1):.0%})")
            if isinstance(self.engine, LlamaEngine):
                info = self.entry.info
                say(f"Máximo del modelo (GGUF {info.arch}.context_length): [accent]{info.context_length}[/]")
                p = self.make_plan(self.entry, ctx=self.n_ctx, gpu=self.plan.ngl)
                say(f"Caché KV: {p.est.kv_per_token / 1024:.1f} KB/token ({self.s.kv_type})")
                tb = Table(box=None, padding=(0, 2))
                tb.add_column("ctx", justify="right")
                tb.add_column("VRAM est.", justify="right")
                tb.add_column("")
                for c in [c for c in CTX_STEPS if c <= max(info.context_length, 4096)]:
                    e = estimate(info, c, self.plan.ngl, self.s.kv_type, self.s.swa_full, self.s.flash_attn,
                                 self.entry.mmproj, calib=self.s.models.get(self.entry.name, {}).get("calib", 1.0))
                    okv = e.vram <= p.vram_budget
                    tb.add_row(render.human_tokens(c), f"{render.gb(e.vram)} GB",
                               "[ok]cabe[/]" if okv else "[bad]no cabe[/]")
                console.print(tb)
                say("[dim]Uso: /ctx 16k · /ctx 32768 · /ctx auto[/]")
            return
        val = "auto" if arg.lower() == "auto" else parse_size(arg)
        if val != "auto" and val < 256:
            raise ValueError("El contexto mínimo razonable es 512 tokens.")
        if isinstance(self.engine, OllamaEngine):
            self.s.ctx = val if val != "auto" else 8192
            self.engine.n_ctx = int(self.s.ctx)
            ok(f"Ollama usará num_ctx={self.engine.n_ctx}")
            return
        if self.reload_with(ctx=val):
            self.s.model_cfg(self.entry.name)["ctx"] = val
            save_settings(self.s)

    def cmd_gpu(self, arg: str):
        if not arg:
            if self.plan:
                say(f"Capas en GPU: [accent]{self.plan.ngl}[/] de {max_gpu_layers(self.entry.info)} "
                    f"(ajuste guardado: {self.s.models.get(self.entry.name, {}).get('gpu', self.s.gpu)})")
            say("[dim]Uso: /gpu 20 · /gpu auto · /gpu max[/]")
            return
        val = arg.lower() if arg.lower() in ("auto", "max") else int(arg)
        if self.reload_with(gpu=val):
            self.s.model_cfg(self.entry.name)["gpu"] = val
            save_settings(self.s)

    def cmd_auto(self, arg: str):
        if self.reload_with(ctx="auto", gpu="auto"):
            mc = self.s.model_cfg(self.entry.name)
            mc["ctx"] = mc["gpu"] = "auto"
            save_settings(self.s)

    def cmd_kv(self, arg: str):
        if not arg:
            say(f"Caché KV: [accent]{self.s.kv_type}[/]  [dim](f16 | q8_0 | q4_0)[/]")
            return
        if arg not in ("f16", "q8_0", "q4_0"):
            raise ValueError("Tipos válidos: f16, q8_0, q4_0")
        old = self.s.kv_type
        self.s.kv_type = arg
        TRANSIENT.pop("kv_type", None)
        if self.engine is not None and not self.reload_with():
            self.s.kv_type = old
        save_settings(self.s)

    def cmd_swa(self, arg: str):
        if arg not in ("min", "full"):
            say(f"Caché SWA: [accent]{'full' if self.s.swa_full else 'min'}[/]  [dim](min ahorra mucha VRAM en "
                f"Gemma; full permite reutilizar más caché entre turnos)[/]")
            return
        old = self.s.swa_full
        self.s.swa_full = arg == "full"
        if self.engine is not None and not self.reload_with():
            self.s.swa_full = old
        save_settings(self.s)

    def _num_param(self, name: str, arg: str, cast, lo, hi, label: str):
        if not arg:
            say(f"{label}: [accent]{getattr(self.s, name)}[/]")
            return
        v = cast(arg)
        if not (lo <= v <= hi):
            raise ValueError(f"{label} debe estar entre {lo} y {hi}")
        setattr(self.s, name, v)
        TRANSIENT.pop(name, None)
        save_settings(self.s)
        ok(f"{label} = {v}")

    def cmd_tokens(self, arg):
        self._num_param("max_tokens", arg, lambda x: parse_size(x), 16, 262144, "Máx. tokens por respuesta")

    def cmd_temp(self, arg):
        self._num_param("temperature", arg, float, 0.0, 2.0, "Temperatura")

    def cmd_top_p(self, arg):
        self._num_param("top_p", arg, float, 0.0, 1.0, "Top-p")

    def cmd_top_k(self, arg):
        self._num_param("top_k", arg, int, 0, 1000, "Top-k")

    def cmd_min_p(self, arg):
        self._num_param("min_p", arg, float, 0.0, 1.0, "Min-p")

    def cmd_repeat(self, arg):
        self._num_param("repeat_penalty", arg, float, 0.5, 2.0, "Penalización de repetición")

    def cmd_seed(self, arg):
        self._num_param("seed", arg, int, -1, 2**31 - 1, "Semilla")

    def cmd_params(self, arg: str):
        tb = Table(box=None, padding=(0, 2), show_header=False)
        tb.add_column(style="dim")
        tb.add_column(style="accent")
        rows = [("modelo", self.engine.name if self.engine else "-"),
                ("n_ctx", f"{self.n_ctx} (ajuste: {self.s.models.get(self.entry.name, {}).get('ctx', self.s.ctx) if self.entry else self.s.ctx})"),
                ("n_gpu_layers", f"{self.plan.ngl if self.plan else '-'}"),
                ("caché KV / SWA / flash-attn", f"{self.s.kv_type} / {'full' if self.s.swa_full else 'min'} / {self.s.flash_attn}"),
                ("max_tokens", self.s.max_tokens), ("temperature", self.s.temperature), ("top_p", self.s.top_p),
                ("top_k", self.s.top_k), ("min_p", self.s.min_p), ("repeat_penalty", self.s.repeat_penalty),
                ("seed", self.s.seed), ("razonamiento", f"{'on' if self.s.thinking else 'off'} / {'visible' if self.s.show_thinking else 'plegado'}")]
        rows += [(k, getattr(self.s, k)) for k in SETTABLE]
        for k, v in rows:
            tb.add_row(k, str(v))
        console.print(Panel(tb, title="Parámetros", title_align="left", border_style="border"))

    def cmd_set(self, arg: str):
        k, _, v = arg.partition(" ")
        if k not in SETTABLE:
            raise ValueError(f"Claves: {', '.join(SETTABLE)}")
        if not v:
            say(f"{k} = [accent]{getattr(self.s, k)}[/]")
            return
        cur = getattr(self.s, k)
        val = v.strip() if isinstance(cur, str) else (parse_size(v) if k == "auto_ctx_cap" else int(v))
        if k == "shell" and val not in ("powershell", "cmd"):
            raise ValueError("shell: powershell | cmd")
        if k == "save_files" and val not in ("auto", "ask", "never"):
            raise ValueError("save_files: auto | ask | never")
        setattr(self.s, k, val)
        save_settings(self.s)
        ok(f"{k} = {val}")

    # ---------------------------------------------------------------- think
    def cmd_think(self, arg: str):
        a = arg.lower()
        if a in ("on", "off"):
            self.s.thinking = a == "on"
            TRANSIENT.pop("thinking", None)
            if a == "on" and self.engine is not None and not getattr(self.engine, "can_think", False):
                warn("La plantilla de este modelo no tiene modo de razonamiento; se aplicará con modelos que sí.")
            ok(f"Razonamiento {'activado' if self.s.thinking else 'desactivado'}.")
        elif a in ("show", "hide"):
            self.s.show_thinking = a == "show"
            ok(f"Razonamiento {'desplegado' if self.s.show_thinking else 'plegado'}.")
        elif a == "last":
            if not self.last_think:
                say("[dim]No hay razonamiento previo.[/]")
            else:
                console.print(Panel(Text(self.last_think, style="think"), title="💭 último razonamiento",
                                    border_style="dim"))
            return
        else:
            say(f"Razonamiento: pedir={'on' if self.s.thinking else 'off'} · mostrar="
                f"{'show' if self.s.show_thinking else 'hide'} · modelo compatible="
                f"{'sí' if getattr(self.engine, 'can_think', False) else 'no'}  [dim](/think on|off|show|hide|last)[/]")
            return
        save_settings(self.s)

    # ---------------------------------------------------------------- compact
    def cmd_compact(self, arg: str):
        if len(self.history) < 2:
            say("[dim]No hay suficiente conversación que compactar.[/]")
            return
        before = self.context_used()
        lines = []
        for m in self.history:
            who = "Usuario" if m["role"] == "user" else "Asistente"
            lines.append(f"### {who}\n{m['content']}")
        transcript = "\n\n".join(lines)
        limit = self.n_ctx - min(1500, self._budget_reserve()) - 400
        tok = self.engine.count_tokens(transcript)
        if tok > limit:
            keep_chars = int(len(transcript) * limit / tok)
            transcript = "…(inicio omitido)…\n" + transcript[-keep_chars:]
            warn("La conversación es más larga que el contexto: se resume la parte más reciente que cabe.")
        instr = ("Resume la conversación siguiente para poder continuarla sin perder información útil. "
                 "Conserva: objetivos del usuario, decisiones, datos y cifras concretas, nombres de archivos, "
                 "fragmentos de código importantes, preguntas abiertas y tareas pendientes. Escribe en español, "
                 "con viñetas, de forma densa." + (f" Instrucciones extra: {arg}" if arg else ""))
        msgs = [{"role": "system", "content": "Eres un asistente que resume conversaciones con precisión."},
                {"role": "user", "content": f"{instr}\n\n<conversacion>\n{transcript}\n</conversacion>"}]
        summary, _, st, stopped = self.run_generation(
            msgs, f"{self.engine.name} · compactando",
            GenParams(**{**self.params.__dict__, "thinking": False, "temperature": 0.3,
                         "max_tokens": min(1500, self._budget_reserve())}))
        self._account(st)
        if stopped or not summary:
            warn("Compactación cancelada; la conversación no se ha modificado.")
            return
        old = (self.history, self.thoughts)
        self.history = [{"role": "user", "content": f"[Resumen de la conversación anterior]\n{summary}"},
                        {"role": "assistant", "content": "Entendido. Continúo a partir de este resumen."}]
        self.thoughts = {}
        self._mark_ctx()
        after = self.context_used()
        if after >= before * 0.9:
            self.history, self.thoughts = old
            self._mark_ctx()
            warn(f"El resumen no libera contexto (~{before} → ~{after} tokens; habitual con modelos pequeños o "
                 f"conversaciones cortas). Se conserva la conversación original.")
            return
        ok(f"Conversación compactada: ~{before} → ~{after} tokens.")
        self.autosave()

    # ---------------------------------------------------------------- sesiones
    def _apply_state(self, d: dict, name: str):
        self.history = d.get("history", [])
        self.thoughts = {}
        if d.get("system"):
            self.s.system_prompt = d["system"]
        self._mark_ctx()
        turns = sum(1 for m in self.history if m.get("role") == "user")
        ok(f"Sesión '{name}' cargada ({turns} turnos).")
        if d.get("model") and self.entry and d["model"] != self.entry.name:
            say(f"[dim]Se creó con {d['model']}; sigues con {self.entry.name} (usa /model si quieres cambiar).[/]")
        for m in self.history[-4:]:
            who = "[user]›[/]" if m["role"] == "user" else "[assistant]◆[/]"
            say(f"{who} [dim]{m['content'][:160].replace(chr(10), ' ')}[/]")

    def cmd_resume(self, arg: str):
        from .inputbox import Choice, select
        items = sessions.list_sessions()
        if not items:
            say("[dim]No hay sesiones guardadas todavía.[/]")
            return
        choices = [Choice(i, ("⟳ " if i.auto else "★ ") + time.strftime("%d/%m %H:%M", time.localtime(i.updated)),
                          f"{i.turns:3} turnos · {i.model[:28]:28} · {i.preview}") for i in items[:60]]
        sel = select("Retomar sesión (★ guardadas · ⟳ autoguardadas)", choices, render.current)
        if sel is None:
            return
        self._apply_state(sessions.load(sel.path), sel.name)
        if sel.auto:
            self.session_id = sel.name

    def cmd_chat(self, arg: str):
        action, _, name = arg.partition(" ")
        name = name.strip()
        if action == "save":
            if not name:
                raise ValueError("Uso: /chat save <nombre>")
            p = sessions.save_named(name, self.state())
            ok(f"Sesión guardada en {p}")
        elif action in ("list", ""):
            items = sessions.list_sessions(include_auto=False)
            if not items:
                say("[dim]No hay sesiones con nombre (/chat save <nombre>). /resume muestra también las autoguardadas.[/]")
                return
            tb = Table(box=None, padding=(0, 2))
            for c in ("Nombre", "Fecha", "Turnos", "Modelo", "Inicio"):
                tb.add_column(c)
            for i in items:
                tb.add_row(i.name, time.strftime("%d/%m/%Y %H:%M", time.localtime(i.updated)), str(i.turns), i.model, i.preview)
            console.print(tb)
        elif action == "load":
            if not name:
                raise ValueError("Uso: /chat load <nombre>")
            self._apply_state(sessions.load(name), name)
        elif action in ("delete", "rm"):
            sessions.delete(name)
            ok(f"Sesión '{name}' borrada.")
        else:
            raise ValueError("Uso: /chat save|list|load|delete [nombre]")

    def cmd_export(self, arg: str):
        path = Path(arg.strip('"')) if arg else Path.cwd() / f"conversacion-{time.strftime('%Y%m%d-%H%M')}.md"
        if path.suffix.lower() != ".md":
            path = path.with_suffix(".md")
        sessions.export_markdown(path, self.engine.name if self.engine else "?", self.system_text(), self.history, self.thoughts)
        ok(f"Conversación exportada a {path.resolve()}")

    def cmd_save(self, arg: str):
        if not self.last_reply:
            raise ValueError("No hay ninguna respuesta todavía.")
        blocks = filemod.extract_blocks(self.last_reply)
        if not blocks:
            raise ValueError("La última respuesta no contiene bloques de código. (/export guarda la conversación)")
        parts = arg.split()
        idx = int(parts.pop()) if parts and parts[-1].isdigit() else None
        path = " ".join(parts).strip().strip('"')
        cwd = Path.cwd()
        if idx is not None and not 1 <= idx <= len(blocks):
            raise ValueError(f"Hay {len(blocks)} bloque(s); usa un número entre 1 y {len(blocks)}.")
        if not path:
            if idx is None and any(b.path for b in blocks):
                items = [(b, filemod.safe_target(b.path, cwd)) for b in blocks if b.path]
            else:
                b = blocks[idx - 1] if idx else max(blocks, key=lambda x: x.lines)
                name = b.path or b.default_name()
                t = filemod.safe_target(name, cwd)
                n = 1
                while t.exists() and not b.path:      # no pisar respuesta.html anteriores
                    t = filemod.safe_target(f"{Path(name).stem}-{n}{Path(name).suffix}", cwd)
                    n += 1
                items = [(b, t)]
        else:
            b = blocks[idx - 1] if idx else (blocks[0] if len(blocks) == 1 else max(blocks, key=lambda x: x.lines))
            t = filemod.safe_target(path, cwd)
            if not t.suffix and b.lang:
                t = t.with_suffix("." + filemod.EXT.get(b.lang.lower(), "txt"))
            items = [(b, t)]
        ask = any(t.exists() for _, t in items)
        self._save_blocks(items, ask, "El archivo ya existe. ¿Sobrescribir?")

    def cmd_open(self, arg: str):
        if arg:
            target = Path(arg.strip().strip('"')).expanduser()
        elif self.last_saved:
            target = self.last_saved[-1]
        else:
            raise ValueError("No hay ningún archivo guardado en esta sesión. Uso: /open <ruta>")
        if not target.is_absolute():
            target = Path.cwd() / target
        if not target.exists():
            raise FileNotFoundError(f"No existe: {target}")
        try:
            os.startfile(str(target))
            ok(f"Abriendo {target}")
        except OSError as e:
            error(f"No se pudo abrir: {e}")

    def cmd_archivos(self, arg: str):
        d = self.files_dir
        files = sorted(p for p in d.rglob("*") if p.is_file()) if d.exists() else []
        if arg.strip().lower().startswith("abr"):
            d.mkdir(parents=True, exist_ok=True)
            os.startfile(str(d))
            ok(f"Abriendo {d}")
            return
        if not files:
            say(f"[dim]El modelo aún no ha creado archivos en esta sesión. Se guardarán en {escape(render.short_path(d))}[/]")
            return
        tb = Table(box=None, padding=(0, 2))
        for c in ("Archivo", "Tamaño", "Modificado"):
            tb.add_column(c)
        for f in files:
            st = f.stat()
            tb.add_row(str(f.relative_to(d)), f"{st.st_size:,} B".replace(",", "."),
                       time.strftime("%H:%M:%S", time.localtime(st.st_mtime)))
        console.print(Text(render.short_path(d), style="dim"))
        console.print(tb)
        say("[dim]/open <archivo> lo abre · /archivos abrir abre la carpeta · /save <ruta> copia el último código[/]")

    def cmd_vision(self, arg: str):
        sub, _, rest = arg.strip().partition(" ")
        sub = sub.lower()
        rd = self.reader_entry
        if sub in ("lector", "directo"):
            if sub == "directo" and not self.vision:
                warn("El modelo actual no tiene visión propia: seguirá usándose el lector hasta que cargues uno con 👁.")
            self.s.vision_mode = sub
            save_settings(self.s)
            ok(f"Modo de visión: {sub}")
            return
        if sub == "modelo":
            name = rest.strip()
            if not name or name == "auto":
                self.s.vision_model = "auto"
            else:
                m = self.catalog.find(name)
                if m is None or not m.has_vision:
                    raise ValueError(f"'{name}' no es un modelo con visión instalada (/models).")
                self.s.vision_model = m.name
            save_settings(self.s)
            rd = self.reader_entry
            ok(f"Lector visual: {rd.name if rd else 'ninguno disponible'}")
            return
        if sub == "ver":
            if not self.last_transcripts:
                say("[dim]Todavía no hay transcripciones en esta sesión.[/]")
            for label, txt in self.last_transcripts:
                console.print(Panel(render.md(txt), title=escape(label), title_align="left", border_style="border"))
            return
        if sub == "limpiar":
            n = reader.clear_cache()
            ok(f"{n} transcripción(es) borrada(s) de la memoria.")
            return
        n, size = reader.cache_stats()
        say(f"Modo: [accent]{self.s.vision_mode}[/] [dim](lector = un modelo con visión transcribe las imágenes y "
            f"tu modelo trabaja con el texto · directo = las imágenes van al modelo si tiene mmproj)[/]")
        say(f"Lector: [accent]{escape(rd.name) if rd else 'ninguno (descarga un mmproj: /doctor)'}[/]"
            f" [dim]({'automático' if self.s.vision_model == 'auto' else 'elegido'})[/]")
        say(f"Transcripciones guardadas: {n} ({size / 1024:.0f} KB) en [dim]{escape(str(reader.CACHE_DIR))}[/]")
        say("[dim]/vision lector|directo · /vision modelo <nombre|auto> · /vision ver · /vision limpiar[/]")

    def cmd_settings(self, arg: str):
        from .inputbox import Choice, select
        s = self.s

        def onoff(v):
            return "sí" if v else "no"

        while True:
            rd = self.reader_entry
            items = [
                ("vision_mode", "Modo de visión", s.vision_mode, ["lector", "directo"]),
                ("vision_model", "Modelo lector", f"{s.vision_model}" + (f" ({rd.name})" if s.vision_model == "auto" and rd else ""),
                 ["auto"] + [m.name for m in self.catalog.models if m.has_vision]),
                ("save_files", "Guardar archivos del modelo", s.save_files, ["auto", "ask", "never"]),
                ("thinking", "Pedir razonamiento", onoff(s.thinking), ["sí", "no"]),
                ("show_thinking", "Mostrar razonamiento", onoff(s.show_thinking), ["sí", "no"]),
                ("theme", "Tema", s.theme, list(render.THEMES)),
                ("max_tokens", "Máx. tokens por respuesta", str(s.max_tokens), None),
                ("temperature", "Temperatura", str(s.temperature), None),
                ("kv_type", "Caché KV (recarga)", s.kv_type, ["f16", "q8_0", "q4_0"]),
                ("shell", "Shell para !comandos", s.shell, ["powershell", "cmd"]),
                ("pdf_max_image_pages", "Máx. páginas escaneadas por PDF", str(s.pdf_max_image_pages), None),
                ("relay_auto", "Relevo automático", onoff(s.relay_auto), ["sí", "no"]),
                ("relay_reserve", "Reserva para el relevo (%)", str(s.relay_reserve), ["10", "15", "20", "25"]),
                ("relay_max", "Relevos máximos por respuesta", str(s.relay_max), ["4", "8", "12", "20"]),
            ]
            width = max(len(t) for _, t, _, _ in items)
            choices = [Choice(i, f"{t.ljust(width)}  {v}") for i, (_, t, v, _) in enumerate(items)]
            idx = select("Ajustes (Enter cambia · Esc sale)", choices, render.current, 0)
            if idx is None:
                return
            key, title, cur, opts = items[idx]
            if opts:
                val = select(title, [Choice(o, o) for o in opts], render.current,
                             opts.index(cur) if cur in opts else 0)
                if val is None:
                    continue
            else:
                try:
                    val = console.input(f"{title} [dim](actual {cur})[/]: ").strip()
                except (EOFError, KeyboardInterrupt):
                    continue
                if not val:
                    continue
            try:
                if key in ("thinking", "show_thinking"):
                    self.cmd_think({"thinking": "on" if val == "sí" else "off",
                                    "show_thinking": "show" if val == "sí" else "hide"}[key])
                elif key == "theme":
                    self.cmd_theme(val)
                elif key == "kv_type":
                    self.cmd_kv(val)
                elif key == "vision_mode":
                    self.cmd_vision(val)
                elif key == "vision_model":
                    self.cmd_vision(f"modelo {val}")
                elif key == "max_tokens":
                    self.cmd_tokens(val)
                elif key == "temperature":
                    self.cmd_temp(val)
                elif key == "relay_auto":
                    self.s.relay_auto = val == "sí"
                    save_settings(self.s)
                elif key in ("relay_reserve", "relay_max"):
                    setattr(self.s, key, int(val))
                    save_settings(self.s)
                else:
                    self.cmd_set(f"{key} {val}")
            except (ValueError, EngineError) as e:
                error(str(e))

    def cmd_copy(self, arg: str):
        if not self.last_reply:
            warn("No hay respuestas que copiar.")
        elif copy_to_clipboard(self.last_reply):
            ok("Última respuesta copiada al portapapeles.")
        else:
            error("No se pudo acceder al portapapeles.")

    def cmd_system(self, arg: str):
        if not arg:
            console.print(Panel(self.s.system_prompt, title="Prompt de sistema", border_style="border"))
            if self.memory_text:
                say("[dim]+ memoria de LOCAL_AI.md (/memory show) + fecha y directorio[/]")
            return
        self.s.system_prompt = DEFAULT_SYSTEM_PROMPT if arg.lower() == "reset" else arg
        TRANSIENT.pop("system_prompt", None)
        save_settings(self.s)
        self._mark_ctx()
        ok("Prompt de sistema actualizado (se aplica desde el próximo mensaje; la conversación se conserva).")

    def cmd_memory(self, arg: str):
        sub, _, rest = arg.partition(" ")
        sub = sub.lower() or "show"
        if sub == "show":
            files = memory_files()
            if not files:
                say(f"[dim]Sin memoria. Crea instrucciones con /memory add <texto> (global: {GLOBAL_MEMORY_FILE}) "
                    f"o /memory addp <texto> (proyecto: {project_memory_file()}).[/]")
                return
            for f in files:
                console.print(Panel(render.md(f.read_text(encoding="utf-8")), title=str(f), border_style="border"))
        elif sub in ("add", "addp"):
            if not rest:
                raise ValueError(f"Uso: /memory {sub} <texto>")
            f = GLOBAL_MEMORY_FILE if sub == "add" else project_memory_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            prev = f.read_text(encoding="utf-8") if f.exists() else "# Memoria de Querencia\n"
            f.write_text(prev.rstrip() + f"\n- {rest.strip()}\n", encoding="utf-8")
            self.memory_text = load_memory_text()
            self._mark_ctx()
            ok(f"Añadido a {f}")
        elif sub == "edit":
            f = project_memory_file() if rest.strip().startswith("proy") else GLOBAL_MEMORY_FILE
            if not f.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("# Memoria de Querencia\n\n- \n", encoding="utf-8")
            editor = os.environ.get("EDITOR", "notepad")
            say(f"[dim]Abriendo {f} con {editor}… (guarda y cierra para continuar)[/]")
            subprocess.call([editor, str(f)])
            self.memory_text = load_memory_text()
            self._mark_ctx()
            ok("Memoria recargada.")
        elif sub == "reload":
            self.memory_text = load_memory_text()
            self._mark_ctx()
            ok(f"Memoria recargada ({len(self.memory_text)} caracteres de {len(memory_files())} archivo(s)).")
        elif sub == "path":
            say(f"Global:   {GLOBAL_MEMORY_FILE} {'[ok](existe)[/]' if GLOBAL_MEMORY_FILE.exists() else '[dim](no existe)[/]'}")
            pf = project_memory_file()
            say(f"Proyecto: {pf} {'[ok](existe)[/]' if pf.exists() else '[dim](no existe)[/]'}")
        else:
            raise ValueError("Uso: /memory [show|add <texto>|addp <texto>|edit [proyecto]|reload|path]")

    def cmd_stats(self, arg: str):
        s = self.stats
        el = time.time() - s.started
        tb = Table(box=None, padding=(0, 2), show_header=False)
        tb.add_column(style="dim")
        tb.add_column(style="accent")
        avg_tps = s.tokens_out / s.gen_time if s.gen_time else 0
        rows = [("Modelo", f"{self.engine.name if self.engine else '-'} ({getattr(self.engine, 'kind', '-')})"),
                ("Duración de la sesión", f"{int(el // 60)} min {int(el % 60)} s"),
                ("Turnos", f"{s.turns} ({s.interrupted} interrumpidos)"),
                ("Tokens de entrada (prompt evaluado)", f"{s.tokens_in:,}".replace(",", ".")),
                ("Tokens de salida", f"{s.tokens_out:,}".replace(",", ".")),
                ("Velocidad media de generación", f"{avg_tps:.1f} t/s"),
                ("Tiempo procesando prompts / generando", f"{s.prompt_time:.1f} s / {s.gen_time:.1f} s"),
                ("Contexto", f"~{self.context_used()} / {self.n_ctx} tokens"),
                ("Mensajes en el historial", str(len(self.history)))]
        if s.last:
            rows.append(("Último turno", f"{s.last.prompt_tokens} in · {s.last.completion_tokens} out · "
                                         f"{s.last.tps:.1f} t/s · {s.last.t_total:.1f} s"))
        if isinstance(self.engine, LlamaEngine):
            rows.append(("Carga del modelo", f"{self.engine.load_time:.1f} s"))
            rows.append(("VRAM real (buffers) / estimada", f"{render.gb(self.engine.real.gpu)} / "
                                                          f"{render.gb(self.plan.est.vram - self.plan.est.overhead_gpu)} GB"))
        for k, v in rows:
            tb.add_row(k, v)
        console.print(Panel(tb, title="📊 Estadísticas", title_align="left", border_style="border"))

    def cmd_theme(self, arg: str):
        name = arg.strip().lower()
        if not name:
            from .inputbox import Choice, select
            name = select("Tema", [Choice(n, n, f"código: {t.code}") for n, t in render.THEMES.items()],
                          render.current, list(render.THEMES).index(render.current.name))
            if name is None:
                return
        if name not in render.THEMES:
            raise ValueError(f"Temas: {', '.join(render.THEMES)}")
        t = render.set_theme(name)
        if self.box:
            self.box.set_theme(t)
        self.s.theme = name
        TRANSIENT.pop("theme", None)
        save_settings(self.s)
        ok(f"Tema '{name}' aplicado.")
        console.print(render.md("```python\ndef hola(nombre: str) -> str:\n    return f\"Hola, {nombre}\"  # vista previa\n```"))

    # ---------------------------------------------------------------- adjuntos
    def _attach(self, path: str, **kw):
        att = load_any(path, vision=self.can_see(), settings=self.s, **kw)
        self.pending.append(att)
        render.tool("ok", "Adjuntar", att.name, (att.note or att.summary()) + " · va con tu próximo mensaje")

    def cmd_file(self, arg: str):
        if not arg:
            raise ValueError("Uso: /file <ruta>  (o escribe @ruta en el mensaje)")
        self._attach(arg)

    def cmd_image(self, arg: str):
        if not arg:
            raise ValueError("Uso: /image <ruta>")
        self._attach(arg)

    def cmd_pdf(self, arg: str):
        m = re.match(r'^("[^"]+"|\S+)(?:\s+(auto|texto|text|imagenes|images))?(?:\s+([\d,\- ]+))?$', arg.strip())
        if not m:
            raise ValueError("Uso: /pdf <ruta> [auto|texto|imagenes] [páginas, p. ej. 1-5,8]")
        mode = {"texto": "text", "imagenes": "images"}.get(m.group(2) or "auto", m.group(2) or "auto")
        self._attach(m.group(1).strip('"'), pdf_mode=mode, pages=(m.group(3) or "").replace(" ", "") or None)

    def cmd_attachments(self, arg: str):
        if arg == "clear":
            self.pending = []
            ok("Adjuntos pendientes eliminados.")
            return
        if not self.pending:
            say("[dim]No hay adjuntos pendientes.[/]")
            return
        for a in self.pending:
            say(f"  📎 {a.summary()}")

    def cmd_mmproj(self, arg: str):
        if self.entry is None or self.entry.kind != "gguf":
            raise ValueError("Solo aplica a modelos GGUF.")
        if not arg:
            cur = self.entry.mmproj
            say(f"mmproj actual: [accent]{Path(cur.path).name if cur else 'ninguno'}[/] "
                f"[dim]({cur.projector_type if cur else self.entry.vision_note or 'sin visión'})[/]")
            for p in self.catalog.projectors:
                users = self.catalog.users_of(p)
                say(f"  · {Path(p.path).name}: {p.projector_type}, projection_dim {p.projection_dim} "
                    f"[dim]→ {', '.join(users) or 'ningún modelo compatible'}[/]")
            return
        ov = None if arg == "auto" else arg
        mc = self.s.model_cfg(self.entry.name)
        if ov is None:
            mc.pop("mmproj", None)
        else:
            mc["mmproj"] = ov
        pair_mmproj(self.entry, self.catalog.projectors, ov)
        say(f"[dim]Visión: {self.entry.vision} {self.entry.vision_note}[/]")
        self.reload_with()

    def cmd_debug(self, arg: str):
        if arg in ("on", "off"):
            llamalog.echo = arg == "on"
            ok(f"Log nativo en vivo {'activado' if llamalog.echo else 'desactivado'}.")
            return
        noise = ("tensor[", "create_tensor", "llama_model_loader: - ", "load_tensors: layer", "llama_kv_cache: layer")
        lines = [l for l in llamalog.tail(1500) if not any(n in l for n in noise)][-80:]
        console.print(Panel(Text("\n".join(lines) or "(vacío)"), title="Últimas líneas del log de llama.cpp/mtmd",
                            border_style="dim"))

    def cmd_setup(self, arg: str):
        if arg.strip() == "cli":
            from .setup_wizard import run_wizard
            done = run_wizard()
        else:
            from .setup_gui import run_gui
            say("[dim]Se abre el asistente en el navegador; cuando termines pulsa «Cerrar asistente» (o Ctrl+C aquí).[/]")
            done = run_gui()
        if done:
            from .config import load_settings as _ls
            new = _ls()
            self.s.profiles, self.s.vision_model = new.profiles, new.vision_model
            self.catalog = build_catalog(self.s)
            say("[dim]Usa /modo <categoría> para cambiar a uno de tus perfiles.[/]")

    def cmd_modo(self, arg: str):
        profiles = self.s.profiles or {}
        if not profiles:
            raise ValueError("Aún no tienes perfiles: ejecuta /setup (o instalar-querencia.bat).")
        key = arg.strip().lower()
        if not key:
            from .inputbox import Choice, select
            from .model_catalog import category
            names = {c: category(c).name for c in profiles}
            key = select("¿Qué perfil?", [Choice(k, names[k], v.get("model", "")) for k, v in profiles.items()],
                         render.current)
            if key is None:
                return
        prof = next((v for k, v in profiles.items() if k.startswith(key)), None)
        if prof is None:
            raise ValueError(f"No hay perfil '{key}'. Tienes: {', '.join(profiles)}")
        for k in ("max_tokens", "temperature", "thinking"):
            if k in prof:
                setattr(self.s, k, prof[k])
        save_settings(self.s)
        entry = self.catalog.find(prof["model"])
        if entry is None:
            raise ValueError(f"El modelo del perfil ({prof['model']}) no está en models/. ¿Falta descargarlo? "
                             f"(querencia --download)")
        if self.entry is not None and entry.name == self.entry.name:
            ok(f"Perfil aplicado ({prof.get('max_tokens')} tokens, temp {prof.get('temperature')}, "
               f"razonamiento {'on' if prof.get('thinking') else 'off'}).")
            return
        plan = self.make_plan(entry)
        if plan is not None and not self.check_plan(plan):
            return
        self.load(entry, plan)

    def cmd_componentes(self, arg: str):
        from . import webkit
        from .webkit.components import REG
        a = arg.strip().lower()
        if a in ("demo", "ver"):
            from .config import APP_DIR
            d = APP_DIR / "demo"
            rep = webkit.build_demo(d)
            os.startfile(str(d / "index.html"))
            ok(f"Demo con {len(rep.components)} componentes abierta en el navegador · {render.short_path(d / 'pagina.lai')} "
               "(mira su código para ver cómo se escribe cada uno)")
            return
        if a.startswith("chat"):
            mode = a.split()[-1] if len(a.split()) > 1 else "auto"
            if mode not in ("auto", "on", "off"):
                warn("Uso: /componentes chat auto|on|off")
                return
            self.s.components_in_chat = mode
            save_settings(self.s)
            self._mark_ctx()
            ok({"auto": "Componentes en el chat: automáticos (solo cuando pides una página o plantilla)",
                "on": "Componentes en el chat: siempre (≈3k tokens de contexto en cada mensaje)",
                "off": "Componentes en el chat: desactivados (siguen en /plantilla y /zero)"}[mode])
            return
        tb = Table(box=None, padding=(0, 2), show_header=False)
        tb.add_column(style="command", no_wrap=True)
        tb.add_column()
        for c in REG.values():
            tb.add_row(f"<{c.name}>", escape(c.desc))
        console.print(Panel(tb, title="Componentes prediseñados", title_align="left", border_style="border"))
        console.print(Panel(Text(webkit.EXAMPLE), title="Ejemplo (pagina.lai)", title_align="left", border_style="dim"))
        say(f"[dim]{len(REG)} componentes · /plantilla <descripción> crea una página con ellos · /zero (elige "
            f"«Componentes») · en el chat: {self.s.components_in_chat} (/componentes chat auto|on|off)[/]")

    def cmd_zero(self, arg: str):
        from .zero import run_zero
        run_zero(self, resume=arg.strip().lower().startswith("cont"))

    def cmd_doctor(self, arg: str):
        from .doctor import run_doctor
        run_doctor(self)


SETTABLE = ("pdf_max_pages", "pdf_max_image_pages", "image_max_side", "keep_images_turns",
            "vram_margin_mb", "auto_ctx_cap", "shell", "save_files", "project_rounds")


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="querencia", description="Querencia — tu IA, en casa (llama.cpp / Ollama)",
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("prompt", nargs="?", help="pregunta directa (modo one-shot; también acepta stdin)")
    p.add_argument("-m", "--model", help="archivo GGUF (nombre o ruta) o modelo de Ollama")
    p.add_argument("-i", "--image", action="append", help="imagen a adjuntar (repetible)")
    p.add_argument("-p", "--pdf", help="PDF a adjuntar")
    p.add_argument("-f", "--file", action="append", help="archivo de texto a adjuntar (repetible)")
    p.add_argument("-s", "--system", help="prompt de sistema")
    p.add_argument("-d", "--dir", help="abrir una carpeta como proyecto (el modelo lee y modifica sus archivos)")
    p.add_argument("--gpu", help="capas en GPU: n, auto o max")
    p.add_argument("--ctx", help="contexto: n, 32k o auto")
    p.add_argument("--tokens", type=int, help="máximo de tokens por respuesta")
    p.add_argument("--kv", choices=["f16", "q8_0", "q4_0"], help="tipo de caché KV")
    p.add_argument("--think", action="store_true", help="activar razonamiento")
    p.add_argument("--theme", choices=list(render.THEMES), help="tema de colores")
    p.add_argument("--list", action="store_true", help="listar modelos y salir")
    p.add_argument("--doctor", action="store_true", help="diagnóstico y salir")
    p.add_argument("--setup", action="store_true", help="asistente de instalación visual (se abre en el navegador)")
    p.add_argument("--setup-cli", action="store_true", help="asistente de instalación en la consola")
    p.add_argument("--port", type=int, default=0, help="puerto del asistente visual (0 = libre)")
    p.add_argument("--no-browser", action="store_true", help="no abrir el navegador automáticamente")
    p.add_argument("--download", action="store_true", help="descargar los modelos elegidos en el asistente")
    p.add_argument("--mem", action="store_true", help="estimación de memoria del modelo elegido y salir")
    p.add_argument("-y", "--yes", action="store_true",
                   help="no pedir confirmación (cargar aunque no quepa; en one-shot, guardar archivos propuestos)")
    return p.parse_args(argv)


def pick_default(app: App, args) -> ModelEntry | None:
    cat = app.catalog
    if args.model:
        e = cat.find(args.model)
        if e is None and Path(args.model).exists():
            e = entry_from_path(args.model, cat.projectors)
        if e is None and not args.model.lower().endswith(".gguf"):
            e = ModelEntry(name=args.model, kind="ollama", size_bytes=0)   # p. ej. "llava:7b"
        if e is None:
            raise ValueError(f"No encuentro el modelo '{args.model}' (querencia --list)")
        return e
    needs_vision = bool(args.image or args.pdf) and app.s.vision_mode == "directo"
    if needs_vision:
        vis = [m for m in cat.models if m.has_vision]
        if vis:
            return vis[0]
    if app.s.last_model:
        e = cat.find(app.s.last_model)
        if e is not None:
            return e
    gg = [m for m in cat.models if m.kind == "gguf"]
    return gg[0] if gg else (cat.models[0] if cat.models else None)


def main(argv=None):
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = parse_args(argv)
    s = load_settings()
    try:  # fallos nativos (llama.cpp, drivers) no dejan traza de Python: faulthandler la escribe aquí
        import faulthandler
        _fh = open(CRASH_LOG, "a", encoding="utf-8")
        _fh.write(f"\n===== inicio {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()} args {sys.argv[1:]}\n")
        _fh.flush()
        faulthandler.enable(file=_fh, all_threads=True)
    except Exception:
        pass
    from .config import TRANSIENT

    def override(key, value):          # solo para esta ejecución; no se guarda
        TRANSIENT.setdefault(key, getattr(s, key))
        setattr(s, key, value)

    if args.theme:
        override("theme", args.theme)
    render.set_theme(s.theme)
    if args.system:
        override("system_prompt", args.system)
    if args.tokens:
        override("max_tokens", args.tokens)
    if args.kv:
        override("kv_type", args.kv)
    if args.think:
        override("thinking", True)
    app = App(s, args)

    if args.list:
        app.cmd_models("")
        return
    if args.doctor:
        app.cmd_doctor("")
        return
    if args.setup:
        from .setup_gui import run_gui
        run_gui(args.port, not args.no_browser)
        return
    if args.setup_cli or args.download:
        from .setup_wizard import run_download, run_wizard
        run_wizard() if args.setup_cli else run_download()
        return

    try:
        entry = pick_default(app, args)
    except ValueError as e:
        error(str(e))
        return
    if entry is None:
        error("No hay modelos: copia archivos .gguf en models/ o arranca Ollama.")
        return
    ctx = None if args.ctx is None else ("auto" if args.ctx == "auto" else parse_size(args.ctx))
    gpu = None if args.gpu is None else (args.gpu if args.gpu in ("auto", "max") else int(args.gpu))

    piped = "" if sys.stdin.isatty() else sys.stdin.read().strip()
    one_shot = bool(args.prompt or piped)

    plan = None
    if entry.kind == "gguf":
        plan = app.make_plan(entry, ctx=ctx, gpu=gpu)
        if args.mem:
            app.show_plan(plan)
            return
        if one_shot:
            if not (plan.fits_vram and plan.fits_ram):
                app.check_plan(plan, interactive=False)
        elif not app.check_plan(plan, interactive=not args.yes):
            say("[dim]Cancelado. Usa /model dentro del CLI o --gpu/--ctx para otros valores.[/]")
            app.engine = None
            entry = None
    if entry is not None and not app.load(entry, plan):
        if one_shot:
            return
    if args.dir and not app.open_project(args.dir):
        return
    attach_failed = False
    if entry is not None and app.engine is not None:
        for f in (args.file or []) + (args.image or []) + ([args.pdf] if args.pdf else []):
            try:
                app._attach(f)
            except (AttachError, FileNotFoundError) as e:
                error(str(e))
                attach_failed = True

    if one_shot:
        if attach_failed:
            say("[dim]No se envía la pregunta porque falló un adjunto (evita respuestas inventadas).[/]")
            return
        if app.engine is None:
            return
        prompt = "\n\n".join(x for x in (piped, args.prompt or "") if x)
        app.assume_yes = args.yes        # one-shot: con -y se guardan los archivos propuestos
        app.send(prompt)
        return
    app.repl()
