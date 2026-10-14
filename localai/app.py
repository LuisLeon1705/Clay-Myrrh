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

from . import __version__, grafo, i18n, llamalog, reader, relay, render, sessions
from .i18n import T
from .attachments import AttachError, Attachment, find_mentions, load_any, shrink_image
from .catalog import Catalog, ModelEntry, build_catalog, entry_from_path, pair_mmproj
from .config import (TRANSIENT, CRASH_LOG, FILES_DIR, AUTOSAVE_DIR, GLOBAL_MEMORY_FILE, HISTORY_FILE, OLLAMA_HOST, Settings,
                     LEGACY_MEMORY_FILENAMES, MEMORY_FILENAME, load_memory_text, load_settings, memory_files, parse_size,
                     project_memory_file, save_settings, DEFAULT_SYSTEM_PROMPT, DEFAULT_SYSTEM_PROMPT_EN)
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
        self.last_stopped = False
        self.assume_yes = False
        self.stats = SessionStats()
        self.session_id = time.strftime("%Y%m%d-%H%M%S")
        self.memory_text = load_memory_text()
        self.ctx_tokens = 0
        self._ctx_dirty = True
        self.box = None
        self.esc = EscWatcher()
        self.lang = i18n.resolve(self.s.language)       # idioma de comandos y mensajes: es | en
        i18n.set_lang(self.lang)
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
        prompt = self.s.system_prompt
        if self.lang == "en" and prompt == DEFAULT_SYSTEM_PROMPT:
            prompt = DEFAULT_SYSTEM_PROMPT_EN
        parts = [prompt]
        # en modo proyecto la red va en el bloque del proyecto (siempre actual): aquí, solo las instrucciones
        memory = grafo.strip_section(self.memory_text) if self.workspace is not None else self.memory_text
        if memory:
            parts.append("# Instrucciones persistentes del usuario (Clay-Myrrh.md)\n" + memory)
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
        """Tabla de comandos en el idioma de la interfaz; los nombres del otro idioma quedan como alias ocultos."""
        from .inputbox import CmdSpec
        L = self.lang

        def tr(es, en):
            return en if L == "en" else es

        sess = lambda: [(i.name, f"{time.strftime('%d/%m %H:%M', time.localtime(i.updated))} · {i.turns} "
                                 f"{tr('turnos', 'turns')} · {i.preview}")
                        for i in sessions.list_sessions(include_auto=False)]
        models = lambda: [(m.name, f"{m.size_gb:.1f} GB · "
                                   + ('👁 ' + tr('visión', 'vision') if m.has_vision else
                                      ('⚠ ' + tr('falta mmproj', 'mmproj missing') if m.vision == 'falta_mmproj' else m.kind)))
                          for m in self.catalog.models]

        def C(key, es, en, args_es="", args_en=None, **kw):
            return key, CmdSpec(i18n.name(key, L), tr(es, en), tr(args_es, args_en if args_en is not None else args_es), **kw)

        specs = [
            C("help", "Ayuda: comandos y atajos", "Help: commands and shortcuts"),
            C("clear", "Borrar la conversación y la pantalla", "Clear the conversation and the screen"),
            C("exit", "Salir", "Quit"),
            C("model", "Elegir modelo (selector con tamaño, visión y memoria)",
              "Choose a model (picker with size, vision and memory)", "[nombre]", "[name]", choices=models),
            C("models", "Tabla de modelos locales y de Ollama", "Table of local and Ollama models"),
            C("compact", "Resumir la conversación para liberar contexto", "Summarise the conversation to free up context",
              "[instrucciones]", "[instructions]"),
            C("resume", "Retomar una sesión guardada o autoguardada", "Resume a saved or autosaved session"),
            C("chat", "Sesiones con nombre: save|list|load|delete", "Named sessions: save|list|load|delete",
              "<acción> [nombre]", "<action> [name]",
              choices=lambda: [("save", tr("guardar la sesión actual", "save the current session")),
                               ("list", tr("listar sesiones", "list sessions")), ("load", tr("cargar", "load")),
                               ("delete", tr("borrar", "delete"))] + [(f"load {n}", m) for n, m in sess()]),
            C("memory", "Instrucciones persistentes (Clay-Myrrh.md)", "Persistent instructions (Clay-Myrrh.md)",
              "[show|add|addp|edit|reload|path]",
              choices=lambda: [("show", tr("ver memoria cargada", "show the loaded memory")),
                               ("add ", tr("añadir a la memoria global", "add to the global memory")),
                               ("addp ", tr("añadir a la memoria del proyecto", "add to the project memory")),
                               ("edit", tr("abrir en el editor", "open in the editor")),
                               (tr("edit proyecto", "edit project"),
                                tr("editar Clay-Myrrh.md del proyecto", "edit the project's Clay-Myrrh.md")),
                               ("reload", tr("recargar archivos", "reload the files")), ("path", tr("rutas", "paths"))]),
            C("stats", "Tokens de entrada/salida, t/s, tiempos y memoria", "Input/output tokens, t/s, timings and memory"),
            C("copy", "Copiar la última respuesta al portapapeles", "Copy the last answer to the clipboard"),
            C("save", "Guardar en un archivo el código de la última respuesta", "Save the last answer's code to a file",
              "[ruta] [nº bloque]", "[path] [block no.]", paths=True),
            C("open", "Abrir el último archivo guardado (HTML en el navegador)",
              "Open the last saved file (HTML in the browser)", "[ruta]", "[path]", paths=True),
            C("export", "Guardar la conversación en .md", "Save the conversation as .md",
              "[archivo.md]", "[file.md]", paths=True),
            C("system", "Ver o cambiar el prompt de sistema", "Show or change the system prompt",
              "[texto|reset]", "[text|reset]"),
            C("theme", "Cambiar el tema de colores", "Change the colour theme", "[nombre]", "[name]",
              choices=lambda: [(n, tr("tema", "theme")) for n in render.THEMES]),
            C("think", "Razonamiento: on|off (pedirlo), show|hide (verlo), last",
              "Reasoning: on|off (request it), show|hide (display it), last", "[on|off|show|hide|last]",
              choices=lambda: [("on", tr("pedir razonamiento al modelo", "ask the model to reason")),
                               ("off", tr("sin razonamiento", "no reasoning")),
                               ("show", tr("mostrarlo desplegado", "show it expanded")),
                               ("hide", tr("plegado", "collapsed")), ("last", tr("ver el último", "show the last one"))]),
            C("ctx", "Tamaño del contexto (recarga conservando la conversación)",
              "Context size (reloads, keeping the conversation)", "[n|32k|auto]",
              choices=lambda: [(v, "") for v in ("auto", "4k", "8k", "16k", "32k", "64k", "128k")]),
            C("gpu", "Capas en GPU (recarga)", "GPU layers (reloads)", "[n|auto|max]",
              choices=lambda: [("auto", tr("las que quepan", "as many as fit")), ("max", tr("todas", "all"))]),
            C("kv", "Tipo de caché KV (recarga)", "KV cache type (reloads)", "[f16|q8_0|q4_0]",
              choices=lambda: [("f16", tr("precisión completa", "full precision")),
                               ("q8_0", tr("½ memoria, casi sin pérdida", "½ memory, almost lossless")),
                               ("q4_0", tr("¼ memoria, algo de pérdida", "¼ memory, some loss"))]),
            C("swa", "Caché de ventana deslizante (Gemma): min|full", "Sliding-window cache (Gemma): min|full", "[min|full]",
              choices=lambda: [("min", tr("solo la ventana (ahorra VRAM)", "window only (saves VRAM)")),
                               ("full", tr("contexto completo", "full context"))]),
            C("auto", "Elegir capas en GPU y contexto automáticamente y recargar",
              "Pick GPU layers and context automatically and reload"),
            C("mem", "Estimación de VRAM/RAM (antes de cargar)", "VRAM/RAM estimate (before loading)",
              "[modelo] [ctx] [gpu]", "[model] [ctx] [gpu]", choices=models),
            C("tokens", "Máximo de tokens por respuesta", "Maximum tokens per answer", "[n]"),
            C("temp", "Temperatura", "Temperature", "[valor]", "[value]"),
            C("top_p", "Top-p", "Top-p", "[valor]", "[value]"),
            C("top_k", "Top-k", "Top-k", "[valor]", "[value]"),
            C("min_p", "Min-p", "Min-p", "[valor]", "[value]"),
            C("repeat", "Penalización de repetición", "Repetition penalty", "[valor]", "[value]"),
            C("seed", "Semilla (-1 = aleatoria)", "Seed (-1 = random)", "[n]"),
            C("params", "Ver todos los parámetros de carga y muestreo", "Show all loading and sampling parameters"),
            C("set", "Ajustes: " + ", ".join(SETTABLE), "Settings: " + ", ".join(SETTABLE),
              "<clave> <valor>", "<key> <value>", choices=lambda: [(k + " ", str(getattr(self.s, k))) for k in SETTABLE]),
            C("file", "Adjuntar archivo de texto/código", "Attach a text/code file", "<ruta>", "<path>", paths=True),
            C("image", "Adjuntar imagen", "Attach an image", "<ruta>", "<path>", paths=True),
            C("pdf", "Adjuntar PDF", "Attach a PDF", "<ruta> [auto|texto|imagenes] [páginas 1-5]",
              "<path> [auto|text|images] [pages 1-5]", paths=True),
            C("attachments", "Ver o vaciar adjuntos pendientes", "Show or clear pending attachments", "[clear]",
              choices=lambda: [("clear", tr("vaciar", "clear"))]),
            C("mmproj", "Proyector de visión del modelo actual", "Vision projector of the current model",
              "[archivo|auto|none]", "[file|auto|none]",
              choices=lambda: [("auto", tr("emparejar por metadatos", "match by metadata")),
                               ("none", tr("desactivar visión", "disable vision"))]
              + [(Path(p.path).name, f"{p.projector_type} · dim {p.projection_dim}") for p in self.catalog.projectors]),
            C("vision", "Lector visual: modo, modelo lector, última transcripción",
              "Visual reader: mode, reader model, last transcription",
              "[lector|directo|modelo|ver|limpiar]", "[reader|direct|model|show|clear]",
              choices=lambda: [(tr("lector", "reader"), tr("un modelo con visión transcribe y tu modelo trabaja el texto",
                                                            "a vision model transcribes and your model works on the text")),
                               (tr("directo", "direct"), tr("las imágenes van directas al modelo (si tiene mmproj)",
                                                             "images go straight to the model (if it has an mmproj)")),
                               (tr("ver", "show"), tr("ver la última transcripción", "show the last transcription")),
                               (tr("limpiar", "clear"), tr("borrar transcripciones guardadas", "delete saved transcriptions")),
                               (tr("modelo auto", "model auto"), tr("elegir el lector automáticamente",
                                                                    "pick the reader automatically"))]
              + [(f"{tr('modelo', 'model')} {m.name}", f"{m.size_gb:.1f} GB · 👁") for m in self.catalog.models
                 if m.has_vision]),
            C("archivos", "Archivos creados por el modelo en esta sesión", "Files created by the model in this session",
              "[abrir]", "[open]",
              choices=lambda: [(tr("abrir", "open"), tr("abrir la carpeta en el Explorador", "open the folder in Explorer"))]),
            C("settings", "Ajustes con menú (idioma, visión, razonamiento, tema, archivos, muestreo…)",
              "Settings menu (language, vision, reasoning, theme, files, sampling…)"),
            C("modo", "Cambiar a un perfil de uso (modelo y ajustes del asistente de instalación)",
              "Switch to a usage profile (model and settings from the setup assistant)", "[categoría]", "[category]",
              choices=lambda: [(k, f"{v.get('model', '')}") for k, v in (self.s.profiles or {}).items()]),
            C("setup", "Asistente de instalación: recomienda y descarga modelos para tu equipo",
              "Setup assistant: recommends and downloads models for your hardware"),
            C("componentes", "Catálogo de componentes web prediseñados (.lai)", "Catalogue of prebuilt web components (.lai)",
              "[demo|chat auto|on|off]",
              choices=lambda: [("demo", tr("abrir en el navegador una página con todos los componentes",
                                           "open a page with all the components in the browser")),
                               ("chat auto", tr("solo cuando pides una página o plantilla (recomendado)",
                                                "only when you ask for a page or template (recommended)")),
                               ("chat on", tr("siempre en el chat (≈3k tokens de contexto)",
                                              "always in the chat (≈3k context tokens)")),
                               ("chat off", tr("solo en /zero y /plantilla", "only in /zero and /template"))]),
            C("plantilla", "Crear una página o plantilla con componentes (.lai → HTML, CSS y JS)",
              "Create a page or template with components (.lai → HTML, CSS and JS)", "<descripción>", "<description>"),
            C("proyecto", "Abrir una carpeta como proyecto: el modelo lee y modifica sus archivos",
              "Open a folder as a project: the model reads and edits its files",
              "[ruta|cerrar|recargar]", "[path|close|reload]", paths=True,
              choices=lambda: [(tr("cerrar", "close"), tr("dejar de trabajar sobre la carpeta", "stop working on the folder")),
                               (tr("recargar", "reload"), tr("volver a leer la estructura de la carpeta",
                                                              "re-read the folder structure"))]
              + [(r, tr("reciente", "recent")) for r in self.s.recent_projects]),
            C("cambios", "Cambios del modelo en el proyecto durante esta sesión",
              "Changes the model made to the project in this session", "[diff]",
              choices=lambda: [("diff", tr("ver las diferencias del último cambio", "show the diff of the last change"))]),
            C("deshacer", "Revertir los últimos cambios del modelo en el proyecto",
              "Undo the model's last changes to the project"),
            C("cadena", "Encadenar prompts: varios separados por una línea --- (o un archivo) se ejecutan seguidos",
              "Chain prompts: several separated by a --- line (or a file) run one after another",
              "<archivo [desde N] | prompts separados por --->", "<file [from N] | prompts separated by --->", paths=True),
            C("grafo", "Red del proyecto en Clay-Myrrh.md: archivos, conexiones y avisos (menos tokens que leerlo)",
              "Project map in Clay-Myrrh.md: files, connections and warnings (fewer tokens than reading it)",
              "[ver|avisos]", "[show|warnings]",
              choices=lambda: [(tr("ver", "show"), tr("mostrar la red", "show the map")),
                               (tr("avisos", "warnings"), tr("piezas que no encajan", "pieces that don't fit"))]),
            C("zero", "Prompt-Zero: crear una página web desde cero con un cuestionario y tareas guiadas",
              "Prompt-Zero: build a website from scratch with a questionnaire and guided tasks",
              "[continuar]", "[continue]",
              choices=lambda: [(tr("continuar", "continue"), tr("retomar el último proyecto a medias",
                                                                 "resume the last unfinished project"))]),
            C("doctor", "Diagnóstico: dependencias, backend GPU, modelos, mmproj y memoria",
              "Diagnostics: dependencies, GPU backend, models, mmproj and memory"),
            C("debug", "Log nativo de llama.cpp/mtmd", "Native llama.cpp/mtmd log", "[on|off]",
              choices=lambda: [("on", tr("mostrar logs en vivo", "show live logs")), ("off", tr("ocultar", "hide"))]),
        ]
        table = {}
        for key, sp in specs:
            table[sp.name] = sp
            hidden = {i18n.other(key, L)} | {a for a, k in i18n.EXTRA_ALIASES.items() if k == key}
            for a in sorted(hidden - {sp.name}):
                table[a] = CmdSpec(a, sp.desc, sp.args, sp.choices, sp.paths, hidden=True)
        return table

    def set_language(self, lang: str):
        """Cambia el idioma al momento: comandos (menú, autocompletado, ayuda) y mensajes."""
        self.lang = i18n.resolve(lang)
        i18n.set_lang(self.lang)
        self._mark_ctx()                # el prompt de sistema por defecto también cambia de idioma
        new = self._build_commands()
        self.commands.clear()
        self.commands.update(new)       # el mismo dict que usa el autocompletado de la caja de entrada
        if getattr(self, "box", None) is not None:
            self.box.no_arg_cmds = {c for c, sp in self.commands.items() if not sp.args}
            self.box.lang = self.lang

    def cmd_name(self, key: str) -> str:
        """Nombre de un comando en el idioma actual (para mensajes y consejos)."""
        return i18n.name(key, self.lang)

    def _resolve(self, cmd: str) -> str | None:
        if cmd in self.commands:
            return cmd
        cands = sorted({c for c in self.commands if c.startswith(cmd) and not self.commands[c].hidden})
        return cands[0] if len(cands) == 1 else None

    def handle_command(self, line: str):
        m = re.match(r"(\S+)\s*(.*)", line.strip(), re.S)
        cmd, arg = (m.group(1).lower(), m.group(2).strip()) if m else ("", "")
        real = self._resolve(cmd)
        if real is None:
            cands = [c for c in self.commands if c.startswith(cmd) and not self.commands[c].hidden]
            if cands:
                warn(i18n.t(self.lang, f"'{cmd}' es ambiguo: {', '.join(sorted(cands))}",
                            f"'{cmd}' is ambiguous: {', '.join(sorted(cands))}"))
            else:
                warn(i18n.t(self.lang, f"Comando desconocido '{cmd}'. Escribe / para ver el menú o {self.cmd_name('help')}.",
                            f"Unknown command '{cmd}'. Type / to see the menu or {self.cmd_name('help')}."))
            return
        key = i18n.key_of(real)
        fn: Callable[[str], None] = getattr(self, "cmd_" + key.replace("-", "_"))
        try:
            fn(i18n.normalize_arg(key, arg))
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
            warn(T(f"{entry.name} admite como máximo {info.context_length} tokens; se usa ese valor.", f"{entry.name} supports at most {info.context_length} tokens; using that value."))
            ctx_v = info.context_length
        ngl_v = None if gpu in (None, "auto") else (max_gpu_layers(info) if gpu == "max" else int(gpu))
        calib = float(mc.get("calib", 1.0))
        fit = auto_fit(info, vb, rb, kv_type=kv, swa_full=swa, flash_attn=self.s.flash_attn, mmproj=entry.mmproj,
                       ctx=ctx_v, ngl=ngl_v, ctx_cap=self.s.auto_ctx_cap, calib=calib)
        return LoadPlan(entry, fit.n_ctx, fit.ngl, fit.est, fit.fits_vram, fit.fits_ram, vb, rb, reclaim, sysm)

    def show_plan(self, plan: LoadPlan, title: str | None = None):
        render.mem_report(plan.est, plan.sysmem, plan.vram_budget, plan.ram_budget,
                          title=title or T(f"Estimación de memoria · {plan.entry.name} · ctx {plan.n_ctx} · gpu {plan.ngl}/{max_gpu_layers(plan.entry.info)}", f"Memory estimate · {plan.entry.name} · ctx {plan.n_ctx} · gpu {plan.ngl}/{max_gpu_layers(plan.entry.info)}"),
                          reclaim=plan.reclaim)

    def confirm(self, question: str, default: bool = False) -> bool:
        if self.assume_yes:
            return True
        if not sys.stdin.isatty():
            return default
        from .inputbox import Choice, select
        try:
            ans = select(question, [Choice(True, T("Sí", "Yes")), Choice(False, "No")], render.current,
                         0 if default else 1, hint=T("↑/↓ elegir · Enter confirmar · Esc = No", "↑/↓ choose · Enter confirm · Esc = No"))
        except Exception:  # sin consola interactiva
            return default
        say(f"[dim]{escape(question)} → {T('Sí', 'Yes') if ans else 'No'}[/]")
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
        warn(T(f"Este llama-cpp-python es solo CPU: {plan.entry.name} irá entero a la RAM aunque tengas "
             f"{sm.gpu_name or 'GPU'} ({render.gb(sm.vram_total)} GB). ", f"This llama-cpp-python is CPU-only: {plan.entry.name} will go entirely to RAM even though you have "
             f"{sm.gpu_name or 'GPU'} ({render.gb(sm.vram_total)} GB). ") + summary,
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
        warn(T(f"llama.cpp ha dejado el modelo casi entero en la CPU: {got} de {plan.ngl} capas previstas en la GPU "
             f"({render.gb(eng.real.gpu)} GB de VRAM). Irá mucho más lento de lo estimado.", f"llama.cpp left the model almost entirely on the CPU: {got} of {plan.ngl} planned layers on the GPU "
             f"({render.gb(eng.real.gpu)} GB of VRAM). It will be much slower than estimated."))
        if not dl.gpu_backend or self._engine_gpu_capable() is False:
            summary, steps = gpumod.engine_advice(sm.vendor)
            say(T(f"   [dim]Causa:[/] el llama-cpp-python instalado no tiene backend GPU (versión solo CPU). {escape(summary)}", f"   [dim]Cause:[/] the installed llama-cpp-python has no GPU backend (CPU-only build). {escape(summary)}"))
            for st in steps:
                say(f"   [dim]→[/] {escape(st)}")
        elif all(uma for _, uma in dl.devices) and sm.vram_total:
            names = ", ".join(n for n, _ in dl.devices)
            say(T(f"   [dim]Causa:[/] llama.cpp solo ve la gráfica integrada ({escape(names)}), no tu {escape(sm.gpu_name)}.", f"   [dim]Cause:[/] llama.cpp only sees the integrated graphics ({escape(names)}), not your {escape(sm.gpu_name)}."))
            say(T("   [dim]→[/] Instala o actualiza el controlador de la tarjeta (incluye Vulkan) y reinicia.", "   [dim]→[/] Install or update the graphics driver (it includes Vulkan) and restart."))
            say(T("   [dim]→[/] Si aparecen varias, elige la dedicada con la variable de entorno GGML_VK_VISIBLE_DEVICES=<nº>.", "   [dim]→[/] If several show up, pick the dedicated one with the environment variable GGML_VK_VISIBLE_DEVICES=<n>."))
        else:
            free = f"{dl.free_mib} MiB libres" if dl.free_mib is not None else "poca VRAM libre"
            say(T(f"   [dim]Causa probable:[/] llama.cpp veía {free} en {escape(dl.used or 'la GPU')} al cargar "
                "(otros programas la ocupan, o el controlador informa de menos memoria).", f"   [dim]Likely cause:[/] llama.cpp saw {free} on {escape(dl.used or 'the GPU')} when loading "
                "(other programs are using it, or the driver reports less memory)."))
            say(T("   [dim]→[/] Cierra juegos y programas que usen la GPU y prueba /gpu auto · /doctor para revisar.", "   [dim]→[/] Close games and programs that use the GPU and try /gpu auto · /doctor to check."))

    def check_plan(self, plan: LoadPlan, interactive: bool = True) -> bool:
        """Si no cabe: muestra la estimación, avisa, sugiere y pide confirmación."""
        if plan.fits_vram and plan.fits_ram:
            return True
        self.show_plan(plan)
        info = plan.entry.info
        if not plan.fits_vram:
            warn(T(f"No cabe en VRAM: {render.gb(plan.est.vram)} GB estimados > {render.gb(plan.vram_budget)} GB disponibles.", f"Doesn't fit in VRAM: {render.gb(plan.est.vram)} GB estimated > {render.gb(plan.vram_budget)} GB available."))
        if not plan.fits_ram:
            warn(T(f"RAM justa: {render.gb(plan.est.ram)} GB estimados > {render.gb(plan.ram_budget)} GB disponibles.", f"Tight on RAM: {render.gb(plan.est.ram)} GB estimated > {render.gb(plan.ram_budget)} GB available."))
        for sgg in suggestions(info, plan.vram_budget, plan.ram_budget, plan.n_ctx, plan.ngl,
                               kv_type=self.s.kv_type, swa_full=self.s.swa_full, flash_attn=self.s.flash_attn,
                               mmproj=plan.entry.mmproj):
            say(f"   [dim]→[/] {sgg}")
        if not interactive:
            return True
        return self.confirm(T("¿Cargar de todos modos? (puede fallar o ir muy lento)", "Load anyway? (it may fail or be very slow)"))

    def load(self, entry: ModelEntry, plan: LoadPlan | None, keep_history: bool = True) -> bool:
        prev = (self.entry, self.plan)
        if self.engine is not None:
            self.engine.close()
            self.engine = None
        try:
            if entry.kind == "ollama":
                with console.status(T(f"[accent]Conectando con Ollama · {entry.name}…[/]", f"[accent]Connecting to Ollama · {entry.name}…[/]"), spinner="dots"):
                    ctx = self.s.ctx if isinstance(self.s.ctx, int) else 8192
                    self.engine = OllamaEngine(entry.name, OLLAMA_HOST, ctx)
            else:
                assert plan is not None
                self._warn_cpu_only_engine(plan)
                with console.status(T(f"[accent]Cargando {entry.name} (ctx {plan.n_ctx}, gpu {plan.ngl}, kv {self.s.kv_type})…[/]", f"[accent]Loading {entry.name} (ctx {plan.n_ctx}, gpu {plan.ngl}, kv {self.s.kv_type})…[/]"),
                                    spinner="dots"):
                    self.engine = LlamaEngine(entry, plan.n_ctx, plan.ngl, self.s.kv_type, self.s.swa_full,
                                              self.s.flash_attn)
        except EngineError as e:
            error(T(f"No se pudo cargar {entry.name}: {e}", f"Could not load {entry.name}: {e}"), e.hints)
            if prev[0] is not None and prev[0] is not entry and prev[1] is not None:
                say(T(f"[dim]Recargando el modelo anterior ({prev[0].name})…[/]", f"[dim]Reloading the previous model ({prev[0].name})…[/]"))
                try:
                    self.engine = LlamaEngine(prev[0], prev[1].n_ctx, prev[1].ngl, self.s.kv_type,
                                              self.s.swa_full, self.s.flash_attn)
                except EngineError as e2:
                    error(T(f"Tampoco se pudo recargar el anterior: {e2}", f"The previous one could not be reloaded either: {e2}"))
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
            say(T(f"[ok]✓[/] [bold]{escape(entry.name)}[/] [dim]listo en {eng.load_time:.1f}s · ctx "
                f"{render.human_tokens(eng.n_ctx)} · {on_gpu}/{max_gpu_layers(entry.info)} capas en GPU · "
                f"VRAM {render.gb(real.gpu)} GB[/]", f"[ok]✓[/] [bold]{escape(entry.name)}[/] [dim]ready in {eng.load_time:.1f}s · ctx "
                f"{render.human_tokens(eng.n_ctx)} · {on_gpu}/{max_gpu_layers(entry.info)} layers on GPU · "
                f"VRAM {render.gb(real.gpu)} GB[/]"))
            if mismatch:
                self._explain_offload(plan, eng)
            if entry.info and not entry.info.chat_template and not eng.vision_error:
                warn(T("Este GGUF no trae plantilla de chat: se usa un formato genérico y las respuestas pueden "
                       "ser peores. Suele ser una conversión defectuosa: descarga otra versión (unsloth o bartowski).",
                       "This GGUF has no chat template: a generic format is used and answers may be worse. It is "
                       "usually a faulty conversion: download another build (unsloth or bartowski)."))
        else:
            say(T(f"[ok]✓[/] [bold]{escape(entry.name)}[/] [dim](Ollama) listo[/]", f"[ok]✓[/] [bold]{escape(entry.name)}[/] [dim](Ollama) ready[/]"))
        rd = self.reader_entry
        if eng.vision_error and entry.vision == "falta_mmproj" and rd is not None and rd.name != entry.name:
            say(T(f"  [dim]Sin visión propia: las imágenes y PDFs escaneados los leerá {escape(rd.name)}.[/]", f"  [dim]No built-in vision: images and scanned PDFs will be read by {escape(rd.name)}.[/]"))
        elif eng.vision_error:
            warn(eng.vision_error)
            hint = entry.mmproj_hint()
            if hint and entry.vision == "falta_mmproj":
                say(T(f"   [dim]→ Descarga el mmproj: huggingface.co/{hint[0]} · {hint[1]} ({hint[2]}) "
                    f"y ponlo en models/mmproj/[/]", f"   [dim]→ Download the mmproj: huggingface.co/{hint[0]} · {hint[1]} ({hint[2]}) "
                    f"and put it in models/mmproj/[/]"))
        if not keep_history:
            self.history, self.thoughts = [], {}
        elif not eng.vision and any(m.get("images") for m in self.history):
            say(T("[dim]  Las imágenes del historial se enviarán como notas de texto (el modelo no tiene visión).[/]", "[dim]  Images in the history will be sent as text notes (the model has no vision).[/]"))
        save_settings(self.s)
        self._mark_ctx()
        return True

    def reload_with(self, **overrides) -> bool:
        if not isinstance(self.engine, LlamaEngine) or self.entry is None:
            warn(T("Esto solo aplica a modelos GGUF cargados con llama.cpp.", "This only applies to GGUF models loaded with llama.cpp."))
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
            view.set_phase(T("Mirando la imagen", "Looking at the image"))
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
            raise EngineError(f"{err.__class__.__name__}: {err}", T(["/debug muestra el log nativo"], ["/debug shows the native log"]), llamalog.tail(30))
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
                                                      tool_title=T("↻ Preparando el relevo", "↻ Preparing the relay"))
            self._account(st2)
            r = relay.parse(self.last_relay or ra)
            log.add("índice con la reserva", st2.completion_tokens, pt2, relay=r)
            if r is None:
                warn(T("La respuesta se cortó y el modelo no dejó un índice para continuar.", "The answer was cut off and the model left no outline to continue from."))
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
        render.tool("info", T("↻ Relevo", "↻ Relay"), T(f"{len(pending)} parte(s) pendientes · contexto mínimo por parte", f"{len(pending)} pending part(s) · minimal context per part"),
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
                    warn(T(f"Se alcanzó el máximo de {limit} ejecuciones; quedan partes sin escribir.", f"Reached the maximum of {limit} runs; some parts are still unwritten."))
                    parts[key] = text
                    return self._relay_end(log, relay.unify(parts, items), thinks, False)
                runs += 1
                render.tool("info", T(f"↻ Parte {item.id}", f"↻ Part {item.id}"), item.title[:70],
                            T("continúa desde sus últimas líneas" if item.status == "cortada" else "", "continues from its last lines" if item.status == "cortada" else ""))
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
        render.tool("info", T("↻ Relevo", "↻ Relay"), T("continúa desde las últimas líneas · contexto mínimo", "continues from the last lines · minimal context"), T(f"{len(relay.headings(text))} encabezados ya escritos", f"{len(relay.headings(text))} headings already written"))
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
                warn(T("La continuación no avanza: se detiene el relevo.", "The continuation is not progressing: stopping the relay."))
                break
            text = relay.trim_cut(text)
        else:
            warn(T(f"Se alcanzó el máximo de {self.s.relay_max} relevos.", f"Reached the maximum of {self.s.relay_max} relays."))
        return self._relay_end(log, text, thinks, False)

    def _relay_end(self, log, answer: str, thinks: list[str], stopped: bool) -> tuple[str, str, bool]:
        path = log.save(self.session_id)
        if path:
            total_pt = sum(s.get("prompt_tokens", 0) for s in log.steps)
            say(T(f"  [dim]Relevo: {len(log.steps)} ejecuciones · {total_pt} tokens de prompt en total · "
                f"registro: {render.short_path(path)}[/]", f"  [dim]Relay: {len(log.steps)} runs · {total_pt} prompt tokens in total · "
                f"log: {render.short_path(path)}[/]"))
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

        with console.status(T(f"[accent]Escribiendo {len(prompts)} partes en paralelo "
                            f"(hasta {self.s.relay_parallel} a la vez)…[/]", f"[accent]Writing {len(prompts)} parts in parallel "
                            f"(up to {self.s.relay_parallel} at a time)…[/]"), spinner="dots"):
            with cf.ThreadPoolExecutor(max_workers=self.s.relay_parallel) as ex:
                outs = [relay.display(o) for o in ex.map(one, prompts)]
        for n, o in zip(names, outs):
            render.tool("ok", T("Parte lista", "Part ready"), n[:60], T(f"{len(o.split())} palabras", f"{len(o.split())} words"))
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
            warn(T(f"Contexto lleno: se descartaron {dropped} mensajes antiguos (usa {self.cmd_name('compact')} para resumirlos "
                 f"en lugar de perderlos, o /ctx para ampliarlo).", f"Context full: {dropped} old messages were dropped (use {self.cmd_name('compact')} to summarise them "
                 f"instead of losing them, or /ctx to enlarge it)."))
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
                warn(T(f"{img['name']} reducida a {img['w']}×{img['h']} px para que quepa en el contexto.", f"{img['name']} shrunk to {img['w']}×{img['h']} px so it fits in the context."))
                t = image_tokens(eng.projector, img["w"], img["h"])
            if used + t > per_batch and groups[-1]:
                groups.append([])
                used = 0
            groups[-1].append(img)
            used += t
        if len(groups) == 1:
            return None
        say(T(f"[info]Las {len(images)} imágenes no caben a la vez en {self.n_ctx} tokens: se procesan en "
            f"{len(groups)} lotes y se resumen.[/]", f"[info]The {len(images)} images don't fit at once in {self.n_ctx} tokens: they are processed in "
            f"{len(groups)} batches and summarised.[/]"))
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
                warn(T("Procesamiento por lotes interrumpido.", "Batch processing interrupted."))
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
                with console.status(T(f"[dim]Haciendo sitio en la GPU: descargando {main_entry.name}…[/]", f"[dim]Making room on the GPU: unloading {main_entry.name}…[/]"), spinner="dots"):
                    self.engine.close()
                    self.engine = None
                swapped = True
            elif isinstance(self.engine, OllamaEngine):
                self.engine.unload()
        plan = self.make_plan(rd, ctx=reader.READER_CTX, gpu="auto") if not together else side
        eng = None
        try:
            with console.status(T(f"[accent]Encendiendo el lector visual {rd.name}…[/]", f"[accent]Starting the visual reader {rd.name}…[/]"), spinner="dots"):
                eng = LlamaEngine(rd, plan.n_ctx, plan.ngl, "f16", False, self.s.flash_attn)
            if not eng.vision:
                raise EngineError(T(f"El lector {rd.name} no pudo activar su visión: {eng.vision_error}", f"The reader {rd.name} could not enable its vision: {eng.vision_error}"), T(["/vision modelo <otro> para elegir otro lector", "/doctor"], ["/vision model <other> to pick another reader", "/doctor"]))
            yield eng
        finally:
            if eng is not None:
                eng.close()
            if swapped:
                with console.status(T(f"[dim]Volviendo a {main_entry.name}…[/]", f"[dim]Switching back to {main_entry.name}…[/]"), spinner="dots"):
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
            raise EngineError(T("No hay ningún modelo con visión para leer imágenes.", "There is no model with vision to read images."), T(["descarga un mmproj (mira /doctor) o usa un modelo con 👁"], ["download an mmproj (see /doctor) or use a model with 👁"]))
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
                    title = T(f"Leyendo {img.get('name', 'imagen')}" + (f" ({n}/{len(todo)})" if len(todo) > 1 else "") \
                            + f" con {rd.name}", f"Reading {img.get('name', 'image')}" + (f" ({n}/{len(todo)})" if len(todo) > 1 else "") \
                            + f" with {rd.name}")
                    ans, _, st, stopped = self.run_generation(msgs, rd.name, prm, engine=eng, tool_title=title)
                    self._account(st)
                    if stopped:
                        render.tool("warn", T("Lectura cancelada", "Reading cancelled"), att.name)
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
        detail = T((f"{len(imgs)} imagen(es) → {n_chars:,} caracteres de texto".replace(",", ".")
                  + (f" · {cached_n} ya estaba(n) en memoria" if cached_n else "")
                  + f" · lector {rd.name}"), (f"{len(imgs)} image(s) → {n_chars:,} characters of text"
                  + (f" · {cached_n} already in memory" if cached_n else "")
                  + f" · reader {rd.name}"))
        render.tool("ok", T("Transcrito", "Transcribed"), att.name, detail)
        self.last_transcripts = [(lbl, t) for lbl, t in parts]
        return text

    def send(self, text: str):
        if self.engine is None:
            error(T(f"No hay ningún modelo cargado. Usa {self.cmd_name('model')}.", f"No model is loaded. Use {self.cmd_name('model')}."))
            return
        # @menciones
        for path, _ in find_mentions(text):
            try:
                att = load_any(path, vision=self.can_see(), settings=self.s)
                self.pending.append(att)
                render.tool("ok", T("Leer archivo", "Read file"), att.name, att.note or att.summary())
            except AttachError as e:
                render.tool("err", T("Leer archivo", "Read file"), path, str(e))
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
            error(T("Hay imágenes adjuntas pero no hay ningún modelo con visión.", "There are attached images but no model with vision."), T(["descarga un mmproj (/doctor)", "/attachments clear para quitarlas"], ["download an mmproj (/doctor)", "/attachments clear to remove them"]))
            return
        content = "\n\n".join(texts + [text])
        msg: dict = {"role": "user", "content": content}
        ws_sent = self.workspace.snapshot() if self.workspace is not None else None
        if self.workspace is not None:
            ws = self.workspace
            room = (self.n_ctx - self.context_used() - self.params.max_tokens) * 3
            ctx_txt, names, skipped = ws.fresh_context(int(min(40000, max(2000, room * 0.35))),
                                                       mentioned=ws.mentioned(text))
            delta = ws.graph_delta()
            if delta:
                render.tool("info", T("Red del proyecto", "Project map"), T(f"{delta.count(chr(10))} línea(s) actualizadas", f"{delta.count(chr(10))} line(s) updated"), T(f"{len(ws.red().avisos)} aviso(s)" if ws.red().avisos else "sin avisos", f"{len(ws.red().avisos)} warning(s)" if ws.red().avisos else "no warnings"))
            if ctx_txt or delta:
                msg["content"] = "\n\n".join(x for x in (delta, ctx_txt) if x) + "\n\n[Petición del usuario]\n" + content
            if ctx_txt:
                render.tool("ok", T("Contexto del proyecto", "Project context"), T(f"{len(names)} archivo(s) adjuntados", f"{len(names)} file(s) attached"), T(", ".join(names)[:160] + (f" · {len(skipped)} se leen bajo demanda" if skipped else ""), ", ".join(names)[:160] + (f" · {len(skipped)} read on demand" if skipped else "")))
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
                self.workspace.restore(ws_sent)
            need = self.engine.prompt_tokens(self.messages([msg]), self.params.thinking)
            error(T(f"El mensaje con sus adjuntos ocupa ~{need} tokens y el contexto es de {self.n_ctx} "
                  f"(reservando {self._budget_reserve()} para la respuesta).", f"The message with its attachments takes ~{need} tokens and the context is {self.n_ctx} "
                  f"(keeping {self._budget_reserve()} for the answer)."), T(["/ctx 32k (mira /mem antes)", "/pdf ruta auto 1-5 para menos páginas",
                   "/set image_max_side 896 para imágenes más pequeñas"], ["/ctx 32k (check /mem first)", "/pdf path auto 1-5 for fewer pages",
                   "/set image_max_side 896 for smaller images"]))
            return
        self.pending = []
        self.history.append(msg)
        from . import webkit
        self.turn_query = text
        self.turn_components = (self.turn_components or (self.s.components_in_chat == "auto"
                                and webkit.wants_components(text, self.last_reply)))
        if self.turn_components:
            render.tool("info", T("Componentes web", "Web components"), T("se usan en esta respuesta", "used in this answer"), T("el modelo escribe pagina.lai y el programa genera HTML, CSS y JS", "the model writes pagina.lai and the program generates HTML, CSS and JS"))
        if self.workspace is not None:
            self.workspace.begin(text)
        label = f"{self.engine.name}" + (" · 👁" if images and "images" in msg else "")
        try:
            answer, think, stopped = self.relay_chain(self.messages(), content, label)
            self.last_stopped = stopped
        except EngineError as e:
            self.history.pop()
            self.turn_components = False
            if ws_sent is not None:
                self.workspace.restore(ws_sent)
            error(str(e), e.hints)
            return
        finally:
            self._mark_ctx()
        st = self.stats.last or GenStats()
        if not answer:
            self.history.pop()
            if ws_sent is not None:
                self.workspace.restore(ws_sent)
            if think:
                self.last_think = think
                warn(T(f"El modelo solo razonó y se quedó sin tokens antes de responder: sube /tokens o usa {self.cmd_name('think')} off.", f"The model only reasoned and ran out of tokens before answering: raise /tokens or use {self.cmd_name('think')} off."))
            elif not stopped:
                warn(T("El modelo devolvió una respuesta vacía. Prueba otra vez, baja /temp o revisa /debug.", "The model returned an empty answer. Try again, lower /temp or check /debug."))
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
            say(T(f"  [warning]La respuesta llegó al máximo de {self.params.max_tokens} tokens y puede estar cortada:[/] "
                f"[dim]escribe «continúa» o sube el límite con /tokens 8k[/]", f"  [warning]The answer hit the maximum of {self.params.max_tokens} tokens and may be cut off:[/] "
                f"[dim]type «continue» or raise the limit with /tokens 8k[/]"))
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
            self._skip_rewritten_deletes(acts, answer)
            results, errs, applied = self._apply_actions(acts)
            errors += errs
            changed = (len(ws.current.changes) if ws.current else 0) > n_before
            nudge = (not nudged and not changed and not errors and acts.empty
                     and wsmod.announces_without_acting(answer))
            nudged |= nudge
            if stopped or not (acts.needs_reply or errors or nudge):
                break
            if rounds >= max(1, self.s.project_rounds):
                warn(T(f"Se alcanzó el máximo de {self.s.project_rounds} rondas de herramientas en este mensaje "
                     "(/set project_rounds para cambiarlo).", f"Reached the maximum of {self.s.project_rounds} tool rounds in this message "
                     "(/set project_rounds to change it)."))
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
                self.last_stopped = self.last_stopped or stopped
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
            render.tool("ok", T("Cambios en el proyecto", "Project changes"), T(f"{len(cs.changes)} archivo(s) en {ws.name}", f"{len(cs.changes)} file(s) in {ws.name}"), T(wsmod.summary(cs) + f" · {self.cmd_name('deshacer')} los revierte · {self.cmd_name('cambios')} diff los muestra", wsmod.summary(cs) + f" · {self.cmd_name('deshacer')} reverts them · {self.cmd_name('cambios')} diff shows them"))
            self._refresh_graph_file()
        self.autosave()

    def _refresh_graph_file(self):
        """Si el proyecto tiene la red en Clay-Myrrh.md (creada con /grafo), la mantiene al día."""
        f = self.workspace.root / MEMORY_FILENAME
        try:
            if f.exists() and grafo.has_section(f.read_text(encoding="utf-8")):
                grafo.update_file(f, self.workspace.red())
        except OSError:
            pass

    # ---------------------------------------------------------------- red del proyecto
    def cmd_grafo(self, arg: str):
        sub = arg.strip().lower()
        root = self.workspace.root if self.workspace is not None else Path.cwd()
        ws = self.workspace or wsmod.Workspace(root, self.session_id)
        red = ws.red()
        if sub in ("ver", "show"):
            console.print(render.md(grafo.strip_marks(red.text())))
            return
        if sub in ("avisos", "warnings"):
            if not red.avisos:
                ok(T("Sin avisos: las piezas del proyecto encajan (rutas, vistas, tablas, variables, Docker…).", "No warnings: the project's pieces fit together (routes, views, tables, variables, Docker…)."))
            for a in red.avisos:
                say(f"  [warning]•[/] {escape(a)}")
            return
        if sub:
            raise ValueError(T("Uso: /grafo [ver|avisos] · sin argumentos crea o actualiza la red en Clay-Myrrh.md", "Usage: /graph [show|warnings] · with no arguments it creates or updates the map in Clay-Myrrh.md"))
        f = root / MEMORY_FILENAME
        legacy = next((root / n for n in LEGACY_MEMORY_FILENAMES if (root / n).exists()), None)
        existed = f.exists()
        grafo.update_file(f, red)
        self.memory_text = load_memory_text()
        if self.workspace is not None:
            self.workspace.refresh()
            self.workspace.instructions()
        self._mark_ctx()
        full = int(red.total_chars / 3.5)
        render.tool("ok", T("Red del proyecto", "Project map"),
                    T(f"{'actualizada' if existed else 'creada'} en {render.short_path(f)}",
                      f"{'updated' if existed else 'created'} in {render.short_path(f)}"),
                    T(f"{len(red.nodes)} archivos · ≈{red.tokens()} tokens (leerlo todo: ≈{full:,})".replace(",", "."),
                      f"{len(red.nodes)} files · ≈{red.tokens()} tokens (reading it all: ≈{full:,})")
                    + (T(f" · ×{full / max(1, red.tokens()):.0f} menos", f" · ×{full / max(1, red.tokens()):.0f} fewer")
                       if full > red.tokens() else ""))
        if not existed:
            say(T("  [dim]Añade tus instrucciones en la sección «Instrucciones» de Clay-Myrrh.md: el modelo las lee siempre."
                " La red se actualiza sola cuando cambian los archivos del proyecto.[/]", "  [dim]Add your instructions in the «Instructions» section of Clay-Myrrh.md: the model always reads them."
                " The map updates itself when the project's files change.[/]"))
        if legacy is not None:
            say(T(f"  [dim]También hay {legacy.name} (nombre anterior): Clay-Myrrh.md tiene prioridad; puedes copiar sus notas "
                "y borrarlo.[/]", f"  [dim]There is also {legacy.name} (old name): Clay-Myrrh.md takes priority; you can copy its notes "
                "and delete it.[/]"))
        if red.avisos:
            say(T(f"  [warning]{len(red.avisos)} aviso(s)[/] [dim](piezas que no encajan):[/]", f"  [warning]{len(red.avisos)} warning(s)[/] [dim](pieces that don't fit):[/]"))
            for a in red.avisos[:8]:
                say(f"  [warning]•[/] {escape(a)}")
            if len(red.avisos) > 8:
                say(T(f"  [dim]… /grafo avisos los muestra todos[/]", f"  [dim]… /graph warnings shows them all[/]"))
        if self.workspace is None:
            say(T("  [dim]Abre la carpeta como proyecto (/proyecto .) para que el modelo use la red al trabajar.[/]", "  [dim]Open the folder as a project (/project .) so the model uses the map while working.[/]"))

    def _skip_rewritten_deletes(self, acts: "wsmod.Actions", answer: str):
        """«Borro y reescribo»: si la misma respuesta trae el archivo completo, no se borra después de escribirlo."""
        ws = self.workspace

        def norm(p: str) -> str | None:
            try:
                return ws.rel(ws.resolve(p))
            except (ValueError, OSError):
                return None

        written = {norm(b.path) for b in filemod.extract_blocks(answer) if b.path and not _edit_like(b.code)}
        written |= {norm(rel) for rel, _ in acts.writes}
        written.discard(None)
        keep = []
        for rel in acts.deletes:
            if norm(rel) in written:
                render.tool("info", T("Borrar", "Delete"), rel, T("se omite: la misma respuesta lo reescribe entero", "skipped: the same answer rewrites it entirely"))
            else:
                keep.append(rel)
        acts.deletes = keep

    def _apply_actions(self, acts: "wsmod.Actions") -> tuple[str, list[str], list[str]]:
        """Ejecuta las etiquetas del modelo. Devuelve (resultados para el modelo, errores, cambios aplicados)."""
        ws = self.workspace
        out, errs, applied = [], [], []
        for rel, a, b in acts.reads:
            try:
                txt = ws.read(rel, a, b)
                out.append(txt)
                render.tool("ok", T("Leer", "Read"), rel, T(f"{txt.count(chr(10)) - 2} líneas", f"{txt.count(chr(10)) - 2} lines") + (f" ({a}–{b})" if a or b else ""))
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
                render.tool("ok", T("Editar", "Edit"), rel, T(f"{n_ok} de {len(pairs)} cambio(s) aplicados", f"{n_ok} of {len(pairs)} change(s) applied"))
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
                render.tool("err", T("Editar", "Edit"), T("(sin ruta)", "(no path)"), why)
                continue
            path, e = ws.edit(target, [(search, repl)])
            if path is not None:
                applied.append(f"{target} (1 edición)")
                render.tool("ok", T("Editar", "Edit"), target, T("1 cambio aplicado (archivo deducido por su contenido)", "1 change applied (file inferred from its content)"))
            for x in e:
                render.tool("err", "Editar", target, x)
            errs += e
        for rel, text in acts.writes:
            try:
                existed = ws.resolve(rel).exists()
                ws.write(rel, text)
                applied.append(f"{rel} {'reescrito' if existed else 'creado'}")
                render.tool("ok", T("Archivo " + ("actualizado" if existed else "creado"), "File " + ("updated" if existed else "created")), rel, T(f"{text.count(chr(10)) + 1} líneas", f"{text.count(chr(10)) + 1} lines"))
            except (ValueError, OSError) as e:
                errs.append(f"{rel}: {e}")
                render.tool("err", T("Escribir archivo", "Write file"), rel, str(e))
        for rel in acts.deletes:
            try:
                ws.delete(rel)
                applied.append(f"{rel} borrado")
                render.tool("ok", T("Borrar", "Delete"), rel, T("copia guardada · /deshacer lo recupera", f"backup saved · {self.cmd_name('deshacer')} restores it"))
            except (ValueError, OSError) as e:
                errs.append(f"<borrar {rel}>: {e}")
                render.tool("err", "Borrar", rel, str(e))
        return "\n\n".join(out), errs, applied

    def open_project(self, path: str) -> bool:
        target = Path(os.path.expandvars(path.strip().strip('"'))).expanduser()
        if not target.is_absolute():
            target = Path.cwd() / target
        if not target.exists():
            if not self.confirm(T(f"La carpeta {target} no existe. ¿Crearla?", f"The folder {target} does not exist. Create it?"), default=True):
                return False
            target.mkdir(parents=True)
        try:
            ws = wsmod.Workspace(target, self.session_id)
        except ValueError as e:
            error(str(e))
            return False
        home = Path.home().resolve()
        if ws.root in (home, Path(ws.root.anchor)) or ws.root in home.parents:
            if not self.confirm(T(f"{ws.root} es tu carpeta personal o la raíz del disco: el modelo podría modificar "
                                "cualquier archivo dentro. ¿Abrirla igualmente?", f"{ws.root} is your home folder or the root of the drive: the model could change "
                                "any file inside. Open it anyway?"), default=False):
                return False
        os.chdir(ws.root)
        self.workspace, self.project_dir = ws, ws.root
        self.memory_text = load_memory_text()
        rec = [str(ws.root)] + [r for r in self.s.recent_projects if r != str(ws.root)]
        self.s.recent_projects = rec[:8]
        save_settings(self.s)
        n = len(ws.walk())
        ws.context_mode = self.s.project_context
        ws.instructions()
        tree_tok = int(len(ws.tree_cache) / 3.5)
        if ws.context_mode == "grafo":
            red = ws.red()
            full = int(red.total_chars / 3.5)
            what = T(f"red del proyecto ≈{tree_tok} tokens (leerlo todo: ≈{full})", f"project map ≈{tree_tok} tokens (reading it all: ≈{full})")
        else:
            red, what = None, T(f"estructura ≈{tree_tok} tokens", f"structure ≈{tree_tok} tokens")
        render.tool("ok", T("Proyecto abierto", "Project opened"), ws.name,
                    T(f"{n} archivos · {what} · el modelo puede leer, crear y editar archivos "
                    f"aquí · /deshacer revierte · /proyecto cerrar", f"{n} files · {what} · the model can read, create and edit files "
                    f"here · {self.cmd_name('deshacer')} reverts · {self.cmd_name('proyecto')} close"))
        mem = next((ws.root / x for x in (MEMORY_FILENAME, *LEGACY_MEMORY_FILENAMES) if (ws.root / x).exists()), None)
        if self.memory_text and mem is not None:
            say(T(f"  [dim]+ instrucciones del proyecto ({mem.name})[/]", f"  [dim]+ project instructions ({mem.name})[/]"))
        elif red is not None and n:
            say(T("  [dim]/grafo guarda la red en Clay-Myrrh.md (con tus instrucciones del proyecto, como un CLAUDE.md)[/]", "  [dim]/graph saves the map in Clay-Myrrh.md (with your project instructions, like a CLAUDE.md)[/]"))
        if red is not None and red.avisos:
            say(T(f"  [warning]{len(red.avisos)} aviso(s) en la red[/] [dim](piezas que no encajan: /grafo avisos)[/]", f"  [warning]{len(red.avisos)} warning(s) in the map[/] [dim](pieces that don't fit: /graph warnings)[/]"))
        self._mark_ctx()
        return True

    def cmd_proyecto(self, arg: str):
        a = arg.strip()
        if a.lower() in ("cerrar", "close", "salir"):
            if self.workspace is None:
                say(T("[dim]No hay ningún proyecto abierto.[/]", "[dim]No project is open.[/]"))
                return
            name = self.workspace.name
            self.workspace = None
            self.project_dir = None
            self._mark_ctx()
            ok(T(f"Proyecto {name} cerrado: los archivos nuevos vuelven a /archivos.", f"Project {name} closed: new files go back to {self.cmd_name('archivos')}."))
            return
        if a.lower() in ("recargar", "refrescar", "reload"):
            if self.workspace:
                self.workspace.refresh()
                self.workspace.instructions()
                self._mark_ctx()
                ok(T(f"Estructura de {self.workspace.name} actualizada ({len(self.workspace.walk())} archivos).", f"Structure of {self.workspace.name} updated ({len(self.workspace.walk())} files)."))
            return
        if a:
            self.open_project(a)
            return
        if self.workspace is not None:
            ws = self.workspace
            n_changes = sum(len(c.changes) for c in ws.history)
            say(T(f"[accent]Proyecto:[/] {escape(ws.name)} [dim]· {escape(str(ws.root))} · {len(ws.walk())} archivos · "
                f"{len(ws.history)} turno(s) con cambios ({n_changes} archivos) · leídos: {len(ws.read_files)}[/]", f"[accent]Project:[/] {escape(ws.name)} [dim]· {escape(str(ws.root))} · {len(ws.walk())} files · "
                f"{len(ws.history)} turn(s) with changes ({n_changes} files) · read: {len(ws.read_files)}[/]"))
            say(T("[dim]/proyecto <ruta> abre otro · /proyecto cerrar · /cambios · /deshacer[/]", "[dim]/project <path> opens another · /project close · /changes · /undo[/]"))
            return
        from .inputbox import Choice, select
        choices = [Choice(r, Path(r).name, r) for r in self.s.recent_projects if Path(r).is_dir()]
        choices.append(Choice(".", T("Carpeta actual", "Current folder"), os.getcwd()))
        sel = select(T("Abrir proyecto (el modelo podrá modificar sus archivos)", "Open a project (the model will be able to edit its files)"), choices, render.current)
        if sel:
            self.open_project(sel)

    def cmd_cambios(self, arg: str):
        ws = self.workspace
        if ws is None or not ws.history:
            say(T("[dim]El modelo no ha cambiado archivos de un proyecto en esta sesión.[/]", "[dim]The model hasn't changed any project files in this session.[/]"))
            return
        if arg.strip().lower().startswith("diff"):
            from rich.syntax import Syntax
            console.print(Syntax(ws.diff(), "diff", theme=render.current.code, word_wrap=True))
            return
        for i, cs in enumerate(ws.history, 1):
            say(f"  [accent]{i}.[/] {time.strftime('%H:%M', time.localtime(cs.when))} [dim]«{escape(cs.label)}»[/]\n"
                f"     {escape(wsmod.summary(cs))}")
        say(T("[dim]/cambios diff · /deshacer revierte el último[/]", "[dim]/changes diff · /undo reverts the last one[/]"))

    def cmd_deshacer(self, arg: str):
        ws = self.workspace
        if ws is None:
            say(T("[dim]No hay ningún proyecto abierto.[/]", "[dim]No project is open.[/]"))
            return
        cs = ws.undo()
        if cs is None:
            say(T("[dim]No hay cambios que deshacer.[/]", "[dim]There are no changes to undo.[/]"))
            return
        self._mark_ctx()
        ok(T(f"Deshecho «{cs.label}»: {wsmod.summary(cs)}", f"Undone «{cs.label}»: {wsmod.summary(cs)}"))
        self.history.append({"role": "user", "content": "[Mensaje automático: el usuario ha deshecho tus últimos "
                             f"cambios en los archivos ({wsmod.summary(cs)}). Ya no están en el disco.]"})
        self.history.append({"role": "assistant", "content": "Entendido: esos cambios se han revertido."})

    # ---------------------------------------------------------------- cadenas de prompts
    def cmd_cadena(self, arg: str):
        a = arg.strip()
        if not a:
            say(T("[dim]Uso:[/] /cadena archivo.txt [desde N] · o escribe varios prompts separados por una línea [accent]---[/] "
                "(Shift+Enter para saltos de línea). Las líneas iniciales que empiezan por / se ejecutan como comandos "
                "(p. ej. /tokens 8000). La cadena se detiene si un paso falla o pulsas Esc.", "[dim]Usage:[/] /chain file.txt [from N] · or type several prompts separated by a [accent]---[/] line "
                "(Shift+Enter for line breaks). Leading lines that start with / run as commands "
                "(e.g. /tokens 8000). The chain stops if a step fails or you press Esc."))
            return
        first, _, rest = a.partition("\n")
        m = re.match(r"^(.*?)(?:\s+desde\s+(\d+))?$", first.strip())
        cand = Path(os.path.expandvars(m.group(1).strip().strip('"'))).expanduser() if not rest.strip() else None
        if cand is not None and cand.is_file():
            self.run_chain(chain_steps(cand.read_text(encoding="utf-8")), str(cand), int(m.group(2) or 1))
            return
        steps = chain_steps(a)
        if len(steps) < 2 and cand is not None and not rest.strip():
            raise ValueError(T(f"No existe el archivo «{m.group(1).strip()}». Para encadenar prompts escritos aquí, "
                             "sepáralos con una línea ---", f"The file «{m.group(1).strip()}» does not exist. To chain prompts typed here, "
                             "separate them with a --- line"))
        self.run_chain(steps, "")

    def run_chain(self, steps: list[str], source: str = "", start: int = 1) -> bool:
        """Ejecuta los pasos como si se escribieran uno tras otro. Devuelve True si se completaron todos."""
        n = len(steps)
        if not n:
            warn(T("La cadena está vacía.", "The chain is empty."))
            return False
        start = max(1, min(start, n))
        t0 = time.time()
        render.tool("info", T("⛓ Cadena", "⛓ Chain"), T(f"{n} pasos", f"{n} steps") + (f" · {Path(source).name}" if source else "")
                    + (T(f" · desde el {start}", f" · from step {start}") if start > 1 else ""),
                    T("cada paso se ejecuta al terminar el anterior · se detiene si uno falla o pulsas Esc", "each step runs when the previous one finishes · stops if one fails or you press Esc"))
        report = [T(f"# Cadena · {time.strftime('%Y-%m-%d %H:%M')}", f"# Chain · {time.strftime('%Y-%m-%d %H:%M')}") + (f" · {source}" if source else ""), ""]
        if self.workspace is not None:
            report.append(T(f"Proyecto: {self.workspace.root}\n", f"Project: {self.workspace.root}\n"))
        changes0 = len(self.workspace.history) if self.workspace is not None else 0
        done, failed_at = start - 1, None
        for i in range(start, n + 1):
            lines = steps[i - 1].splitlines()
            cmds = []
            while lines and lines[0].strip().startswith("/") and not lines[0].strip().startswith("//"):
                cmds.append(lines.pop(0).strip())
            prompt = "\n".join(lines).strip()
            render.tool("info", T(f"⛓ Paso {i}/{n}", f"⛓ Step {i}/{n}"), (prompt or " ".join(cmds)).splitlines()[0][:90],
                        T(f"{(time.time() - t0) / 60:.0f} min desde el inicio", f"{(time.time() - t0) / 60:.0f} min since the start"))
            for c in cmds:
                self._echo_user(c)
                self.handle_command(c)
            ok = True
            if prompt:
                before = len(self.history)
                self.last_stopped = False
                self._echo_user(prompt)
                ts = time.time()
                try:
                    self.send(prompt)
                except KeyboardInterrupt:
                    self.last_stopped = True
                ok = (not self.last_stopped and len(self.history) > before
                      and self.history[-1].get("role") == "assistant")
                report += [T(f"## Paso {i}/{n} · {(time.time() - ts) / 60:.1f} min", f"## Step {i}/{n} · {(time.time() - ts) / 60:.1f} min") + ("" if ok else " · ✖"), "",
                           "**Prompt**", "", prompt, "", T("**Respuesta**", "**Answer**"), "",
                           (self.history[-1]["content"] if ok else T("(sin respuesta)", "(no answer)")), ""]
            if not ok:
                failed_at = i
                why = T("interrumpido con Esc" if self.last_stopped else "el modelo no respondió o hubo un error", "interrupted with Esc" if self.last_stopped else "the model did not answer or there was an error")
                again = T(f"{self.cmd_name('cadena')} {source} desde {i}" if source else "vuelve a lanzar los pasos que faltan", f"{self.cmd_name('cadena')} {source} from {i}" if source else "run the remaining steps again")
                warn(T(f"La cadena se detiene en el paso {i} de {n}: {why}. Para seguir: {again}", f"The chain stops at step {i} of {n}: {why}. To continue: {again}"))
                break
            done = i
        mins = (time.time() - t0) / 60
        extra = ""
        if self.workspace is not None:
            sets = self.workspace.history[changes0:]
            files = {c.rel for cs in sets for c in cs.changes.values()}
            extra = T(f" · {len(files)} archivo(s) del proyecto cambiados", f" · {len(files)} project file(s) changed")
        from .config import APP_DIR
        path = APP_DIR / "cadenas" / f"{self.session_id}-{time.strftime('%H%M%S')}.md"
        k = 2
        while path.exists():
            path = path.with_name(f"{self.session_id}-{time.strftime('%H%M%S')}-{k}.md")
            k += 1
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(report), encoding="utf-8")
        except OSError:
            path = None
        render.tool("ok" if failed_at is None else "warn", T("⛓ Cadena terminada" if failed_at is None else "⛓ Cadena detenida", "⛓ Chain finished" if failed_at is None else "⛓ Chain stopped"),
                    T(f"{done - start + 1 if done >= start else 0}/{n - start + 1} pasos en {mins:.0f} min{extra}", f"{done - start + 1 if done >= start else 0}/{n - start + 1} steps in {mins:.0f} min{extra}"),
                    T((f"informe: {render.short_path(path)}" if path else "") + (f" · {self.cmd_name('cambios')} para ver qué tocó" if extra else ""), (f"report: {render.short_path(path)}" if path else "") + (f" · {self.cmd_name('cambios')} to see what it changed" if extra else "")))
        return failed_at is None

    def cmd_plantilla(self, arg: str):
        if not arg.strip():
            say(T("[dim]Uso: /plantilla landing de una cafetería con carta, reservas y contacto[/]", "[dim]Usage: /template landing page for a café with menu, bookings and contact[/]"))
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
            state = T((f"[warning]sobrescribe ({t.stat().st_size:,} B)[/]".replace(",", ".")
                     if exists else "[ok]nuevo[/]"), (f"[warning]overwrites ({t.stat().st_size:,} B)[/]" if exists else "[ok]new[/]"))
            say(T(f"  💾 [accent]{escape(str(rel))}[/] · {b.lang or 'texto'} · {b.lines} líneas · {state}", f"  💾 [accent]{escape(str(rel))}[/] · {b.lang or 'text'} · {b.lines} lines · {state}"))
        if ask and not self.confirm(question, default=not overwrite):
            say(T("[dim]No se ha guardado nada. (/save <ruta> cuando quieras)[/]", "[dim]Nothing was saved. (/save <path> whenever you like)[/]"))
            return []
        saved = []
        for b, t in items:
            try:
                n = filemod.write(t, b.code)
                saved.append(t)
                ok(T(f"Guardado {t} ({n:,} bytes)".replace(",", "."), f"Saved {t} ({n:,} bytes)"))
            except OSError as e:
                error(T(f"No se pudo escribir {t}: {e}", f"Could not write {t}: {e}"))
        if saved:
            self.last_saved = saved
            if any(t.suffix.lower() in (".html", ".htm", ".svg") for t in saved):
                say(T(f"[dim]  → {self.cmd_name('open')} para verlo en el navegador[/]", f"[dim]  → {self.cmd_name('open')} to view it in the browser[/]"))
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
                                    T(f"{b.lines} líneas · {n:,} bytes · {where}".replace(",", "."), f"{b.lines} lines · {n:,} bytes · {where}"))
                    else:
                        render.tool("warn", T("Archivo INCOMPLETO", "INCOMPLETE file"), b.path,
                                    T(f"la respuesta se cortó ({b.lines} líneas) · escribe «continúa» o sube /tokens · {where}", f"the answer was cut off ({b.lines} lines) · type «continue» or raise /tokens · {where}"))
                except (ValueError, OSError) as e:
                    render.tool("err", T("Crear archivo", "Create file"), b.path, str(e))
            if saved:
                self.last_saved = saved
                hint = T(f"{self.cmd_name('open')} lo abre" + (" en el navegador" if any(t.suffix.lower() in (".html", ".htm", ".svg") for t in saved) else ""), f"{self.cmd_name('open')} opens it" + (" in the browser" if any(t.suffix.lower() in (".html", ".htm", ".svg") for t in saved) else ""))
                say(T(f"  [dim]{hint} · {self.cmd_name('save')} <ruta> lo copia a otra carpeta · {self.cmd_name('archivos')} los lista[/]", f"  [dim]{hint} · {self.cmd_name('save')} <path> copies it elsewhere · {self.cmd_name('archivos')} lists them[/]"))
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
            self._save_blocks(items, True, T(f"¿Guardar {len(items)} archivo(s) propuesto(s) por el modelo?", f"Save {len(items)} file(s) proposed by the model?"))
            return []
        loose = [b for b in blocks if b.lines >= 5 and filemod.EXT.get(b.lang.lower())]
        if loose:
            b = max(loose, key=lambda x: x.lines)
            extra = T(f" · hay {len(loose)} bloques: /save <ruta> <nº>", f" · there are {len(loose)} blocks: /save <path> <no.>") if len(loose) > 1 else ""
            say(T(f"[dim]  💾 /save {b.default_name()} guarda el bloque {b.lang} ({b.lines} líneas){extra}[/]", f"[dim]  💾 /save {b.default_name()} saves the {b.lang} block ({b.lines} lines){extra}[/]"))
        return []

    def _write_project_blocks(self, blocks: list) -> list[str]:
        ws = self.workspace
        errors = []
        for b in blocks:
            if not b.path:
                continue
            if _edit_like(b.code):   # ediciones dentro de una valla: las aplica parse_actions
                render.tool("info", T("Bloque", "Block"), b.path, T("lleva BUSCAR/REEMPLAZAR o <editar>: se aplica como edición, "
                            "no se escribe como archivo", "contains BUSCAR/REEMPLAZAR or <editar>: applied as an edit, "
                            "not written as a file"))
                continue
            try:
                t = ws.resolve(b.path)
                rel = ws.rel(t)
                if not b.closed and t.exists():
                    raise ValueError(T("la respuesta se cortó antes de cerrar el bloque: no se sobrescribe el archivo "
                                     "existente con contenido incompleto", "the answer was cut off before closing the block: the existing file is not "
                                     "overwritten with incomplete content"))
                existed = t.exists()
                ws.write(rel, b.code)
                render.tool("ok" if b.closed else "warn", T(("Archivo actualizado" if existed else "Archivo creado")
                            + ("" if b.closed else " (INCOMPLETO)"), ("File updated" if existed else "File created")
                            + ("" if b.closed else " (INCOMPLETE)")), rel, T(f"{b.lines} líneas", f"{b.lines} lines"))
                if b.closed and t.suffix == ".lai":
                    from .zero import build_page
                    out = t.with_name("index.html" if t.stem.lower() in ("pagina", "index", "page", "web")
                                      else f"{t.stem}.html")
                    ws.track([out, t.parent / "css" / "styles.css", t.parent / "js" / "app.js"])
                    build_page(t)
            except (ValueError, OSError) as e:
                errors.append(f"{b.path}: {e}")
                render.tool("err", T("Escribir archivo", "Write file"), b.path, str(e))
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
            right.append(("class:toolbar.bad", T(f"sin modelo · {self.cmd_name('model')} ", f"no model · {self.cmd_name('model')} ")))
            return left, right
        name = self.engine.name.replace(".gguf", "")
        if len(name) > 26:
            name = name[:24] + "…"
        right.append(("class:toolbar.accent", name))
        if self.vision and self.s.vision_mode == "directo":
            right.append(("class:toolbar", " 👁"))
        elif self.can_see():
            right.append(("class:toolbar.dim", T(" 👁 lector", " 👁 reader")))
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
            status = T(f"[bad]●[/] sin modelo cargado · usa [accent]{self.cmd_name('model')}[/]", f"[bad]●[/] no model loaded · use [accent]{self.cmd_name('model')}[/]")
        else:
            status = f"[ok]●[/] [bold]{escape(e.name.replace('.gguf', ''))}[/] [dim]· ctx {render.human_tokens(self.n_ctx)}"
            if isinstance(e, LlamaEngine) and self.plan:
                status += T(f" · {self.plan.ngl}/{max_gpu_layers(self.entry.info)} capas GPU", f" · {self.plan.ngl}/{max_gpu_layers(self.entry.info)} GPU layers")
            status += "[/]"
            if self.vision and self.s.vision_mode == "directo":
                status += T(" [dim]· 👁 visión propia[/]", " [dim]· 👁 built-in vision[/]")
            elif rd is not None:
                status += T(f" [dim]· 👁 lector {escape(rd.name.replace('.gguf', ''))}[/]", f" [dim]· 👁 reader {escape(rd.name.replace('.gguf', ''))}[/]")
        if self.memory_text:
            status += T(f"\n[dim]  memoria: {', '.join(str(f) for f in memory_files())}[/]", f"\n[dim]  memory: {', '.join(str(f) for f in memory_files())}[/]")
        c = self.cmd_name
        if self.lang == "en":
            tips = [
                f"Ask, request code or files: whatever the model creates is saved automatically ([accent]{c('archivos')}[/]).",
                "Attach with [accent]@path[/]; images and scanned PDFs are transcribed by the visual reader.",
                f"[accent]{c('model')}[/] switches model · [accent]{c('settings')}[/] settings · "
                f"[accent]{c('help')}[/] commands · [accent]?[/] shortcuts.",
            ]
        else:
            tips = [
                f"Pregunta, pide código o archivos: los que cree el modelo se guardan solos ([accent]{c('archivos')}[/]).",
                "Adjunta con [accent]@ruta[/]; imágenes y PDFs escaneados los transcribe el lector visual.",
                f"[accent]{c('model')}[/] cambia de modelo · [accent]{c('settings')}[/] ajustes · "
                f"[accent]{c('help')}[/] comandos · [accent]?[/] atajos.",
            ]
        render.banner(__version__, status, tips)

    def repl(self, banner: bool = True):
        from .inputbox import InputBox
        no_arg = {c for c, sp in self.commands.items() if not sp.args}
        self.box = InputBox(self.commands, HISTORY_FILE, self.toolbar, render.current,
                            on_copy=lambda: bool(self.last_reply) and copy_to_clipboard(self.last_reply),
                            no_arg_cmds=no_arg, lang=self.lang)
        if banner:
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
                    error(T("La caja de entrada falla repetidamente; prueba con LOCALAI_VT_INPUT=1 (ver crash.log).", "The input box keeps failing; try LOCALAI_VT_INPUT=1 (see crash.log)."))
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
        say(T(f"[dim]Sesión guardada. ¡Hasta pronto! ({self.cmd_name('resume')} para retomarla)[/]", f"[dim]Session saved. See you soon! ({self.cmd_name('resume')} to pick it up again)[/]"))

    def _report_crash(self, where: str, exc: BaseException):
        import traceback
        tb = "".join(traceback.format_exception(exc))
        try:
            with open(CRASH_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} · {where} · "
                        f"modelo={self.engine.name if self.engine else '-'}\n{tb}")
        except OSError:
            pass
        error(T(f"Error inesperado ({exc.__class__.__name__}: {exc}). La sesión sigue abierta.", f"Unexpected error ({exc.__class__.__name__}: {exc}). The session is still open."), T([f"traza completa en {CRASH_LOG}"], [f"full trace in {CRASH_LOG}"]))

    def _echo_user(self, text: str):
        render.user_echo(text)

    # ======================================================================
    # Shell
    # ======================================================================

    def shell(self, text: str):
        attach = text.startswith("!!")
        cmd = text[2:].strip() if attach else text[1:].strip()
        if not cmd:
            say(T("[dim]Uso: !comando (ejecuta) · !!comando (ejecuta y añade la salida al contexto)[/]", "[dim]Usage: !command (runs it) · !!command (runs it and adds the output to the context)[/]"))
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
            error(T("El comando superó 5 minutos y se canceló (los comandos interactivos no están soportados).", "The command ran over 5 minutes and was cancelled (interactive commands are not supported)."))
            return
        except FileNotFoundError as e:
            error(T(f"No se pudo ejecutar: {e}", f"Could not run it: {e}"))
            return
        out = (pr.stdout or "") + (("\n" + pr.stderr) if pr.stderr else "")
        out = out.rstrip()
        lines = out.splitlines()
        shown = "\n".join(lines[-200:]) if len(lines) > 200 else out
        style = "border" if pr.returncode == 0 else "error"
        console.print(Panel(Text(shown or T("(sin salida)", "(no output)")), title=f"$ {cmd}", title_align="left",
                            subtitle=T(f"código {pr.returncode} · {time.time() - t0:.1f}s", f"exit code {pr.returncode} · {time.time() - t0:.1f}s"), border_style=style))
        self.last_shell = f"[Salida de `{cmd}` (código {pr.returncode})]\n```\n{out[-20000:]}\n```"
        if attach:
            self.pending.append(Attachment(f"$ {cmd}", "", "text", text=self.last_shell))
            ok(T("Salida añadida al contexto del próximo mensaje.", "Output added to the context of the next message."))

    # ======================================================================
    # Comandos
    # ======================================================================

    HELP_GROUPS = [   # (título en español, en inglés, claves de comando)
        ("Conversación", "Conversation", ["clear", "compact", "copy", "export", "system", "think", "memory"]),
        ("Proyecto", "Project", ["proyecto", "grafo", "cambios", "deshacer", "cadena"]),
        ("Crear", "Create", ["plantilla", "zero", "componentes"]),
        ("Modelos y memoria", "Models and memory", ["model", "modo", "models", "setup", "mem", "ctx", "gpu", "kv", "swa",
                                                    "auto", "doctor"]),
        ("Archivos y visión", "Files and vision", ["file", "image", "pdf", "attachments", "vision", "archivos", "save",
                                                   "open", "mmproj"]),
        ("Sesiones", "Sessions", ["resume", "chat", "stats"]),
        ("Ajustes", "Settings", ["settings", "theme", "tokens", "temp", "top_p", "top_k", "min_p", "repeat", "seed",
                                 "params", "set", "debug", "exit"]),
    ]

    def cmd_help(self, arg: str):
        tr = lambda es, en: i18n.t(self.lang, es, en)
        for title_es, title_en, keys_ in self.HELP_GROUPS:
            tb = Table(box=None, padding=(0, 2), show_header=False)
            tb.add_column(style="command", no_wrap=True)
            tb.add_column(style="dim", no_wrap=True)
            tb.add_column()
            for key in keys_:
                name = self.cmd_name(key)
                sp = self.commands.get(name)
                if sp:
                    tb.add_row(name, escape(sp.args), escape(sp.desc))
            console.print(Text(tr(title_es, title_en), style="bold"))
            console.print(tb)
        console.print(Text(tr("Atajos", "Shortcuts"), style="bold"))
        keys = Table(box=None, padding=(0, 2), show_header=False)
        keys.add_column(style="accent", no_wrap=True)
        keys.add_column()
        for k, es, en in [
                (tr("@ruta", "@path"), "adjuntar archivo, imagen o PDF (Tab autocompleta)",
                 "attach a file, image or PDF (Tab autocompletes)"),
                ("!cmd / !!cmd", "ejecutar en la shell / y además pasar la salida al contexto",
                 "run in the shell / and also pass the output to the context"),
                ("Enter", "enviar  ·  Alt+Enter, Shift+Enter, Ctrl+J o \\ al final: nueva línea",
                 "send  ·  Alt+Enter, Shift+Enter, Ctrl+J or \\ at the end: new line"),
                ("↑ / ↓", "historial  ·  Ctrl+R búsqueda  ·  → acepta la sugerencia gris",
                 "history  ·  Ctrl+R search  ·  → accepts the grey suggestion"),
                ("Esc", "cancelar la respuesta (sin cerrar el CLI)", "cancel the answer (without closing the CLI)"),
                ("?", "mostrar u ocultar los atajos bajo la caja de entrada", "show or hide the shortcuts below the input box"),
                ("Ctrl+K", "copiar la última respuesta  ·  Ctrl+L limpiar pantalla",
                 "copy the last answer  ·  Ctrl+L clear the screen"),
                ("Ctrl+C", "borrar la línea · dos veces seguidas: salir  ·  Ctrl+D salir",
                 "clear the line · twice in a row: quit  ·  Ctrl+D quit")]:
            keys.add_row(k, tr(es, en))
        console.print(keys)
        if self.lang == "en":
            console.print(Text("Commands also work with their Spanish names. Change the language in "
                               f"{self.cmd_name('settings')} or with /set language es.", style="dim"))
        else:
            console.print(Text("Los comandos también funcionan con su nombre en inglés. Cambia el idioma en "
                               f"{self.cmd_name('settings')} o con /set language en.", style="dim"))

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
        ok(T("Conversación borrada.", "Conversation cleared."))

    # ---------------------------------------------------------------- modelos
    def _model_meta(self, m: ModelEntry, plans: bool = True) -> str:
        vis = T({"si": "👁 visión", "falta_mmproj": "⚠ falta mmproj", "no": "texto"}, {"si": "👁 vision", "falta_mmproj": "⚠ mmproj missing", "no": "text"}).get(m.vision, "?")
        if m.kind == "ollama":
            return f"{m.size_gb:5.1f} GB · Ollama · {m.extra.get('params', '')} {m.extra.get('quant', '')}"
        meta = T(f"{m.size_gb:5.1f} GB · {vis:15} · {m.arch:8} · máx {render.human_tokens(m.ctx_max)}", f"{m.size_gb:5.1f} GB · {vis:15} · {m.arch:8} · max {render.human_tokens(m.ctx_max)}")
        if plans:
            p = self.make_plan(m)
            if p:
                fitc = T("ok" if p.fits_vram and p.fits_ram else "✖ no cabe", "ok" if p.fits_vram and p.fits_ram else "✖ doesn't fit")
                meta += (f" · auto: gpu {p.ngl}/{max_gpu_layers(m.info)}, ctx {render.human_tokens(p.n_ctx)} → "
                         f"VRAM {render.gb(p.est.vram)} G, RAM {render.gb(p.est.ram)} G {fitc if fitc != 'ok' else ''}")
        return meta

    def cmd_models(self, arg: str):
        self.catalog = build_catalog(self.s)
        tb = Table(title=T("Modelos disponibles", "Available models"), title_style="accent", border_style="border")
        for c, j in T([("", "left"), ("Modelo", "left"), ("Tamaño", "right"), ("Arquitectura", "left"),
                     ("Visión", "left"), ("Ctx máx", "right"), ("Auto (gpu · ctx · VRAM/RAM)", "left")], [("", "left"), ("Model", "left"), ("Size", "right"), ("Architecture", "left"),
                     ("Vision", "left"), ("Max ctx", "right"), ("Auto (gpu · ctx · VRAM/RAM)", "left")]):
            tb.add_column(c, justify=j)
        for m in self.catalog.models:
            cur = "●" if self.entry and m.name == self.entry.name else ""
            if m.kind == "ollama":
                tb.add_row(cur, m.name, f"{m.size_gb:.1f} GB", m.arch, "?", "-", "Ollama")
                continue
            vis = {"si": f"[ok]👁 {Path(m.mmproj.path).name if m.mmproj else ''}[/]",
                   "falta_mmproj": T("[warning]⚠ falta mmproj[/]", "[warning]⚠ mmproj missing[/]"),
                   "no": T("[dim]texto[/]", "[dim]text[/]")}[m.vision]
            p = self.make_plan(m)
            auto = (f"{p.ngl}/{max_gpu_layers(m.info)} · {render.human_tokens(p.n_ctx)} · "
                    f"{render.gb(p.est.vram)}/{render.gb(p.est.ram)} GB" + ("" if p.fits_vram else " [bad]✖[/]")) if p else "-"
            tb.add_row(cur, m.name, f"{m.size_gb:.1f} GB", m.arch, vis, render.human_tokens(m.ctx_max), auto)
        console.print(tb)
        missing = [m for m in self.catalog.models if m.vision == "falta_mmproj"]
        if missing:
            say(T("[warning]⚠ Modelos multimodales sin mmproj (se cargan como texto):[/] ", "[warning]⚠ Multimodal models without an mmproj (loaded as text-only):[/] ") + ", ".join(m.name for m in missing)
                + T("  [dim]→ /doctor para ver qué descargar[/]", "  [dim]→ /doctor to see what to download[/]"))

    def cmd_model(self, arg: str):
        self.catalog = build_catalog(self.s)
        entry = None
        if arg:
            entry = self.catalog.find(arg)
            if entry is None and Path(arg.strip('"')).exists():
                entry = entry_from_path(arg.strip('"'), self.catalog.projectors)
            if entry is None:
                cands = [m.name for m in self.catalog.models if arg.lower() in m.name.lower()]
                raise ValueError(T(f"No encuentro el modelo '{arg}'.", f"I can't find the model '{arg}'.") + (T(f" ¿Quizá: {', '.join(cands)}?", f" Maybe: {', '.join(cands)}?") if cands else " (/models)"))
        else:
            from .inputbox import Choice, select
            with console.status(T("[dim]Calculando estimaciones de memoria…[/]", "[dim]Computing memory estimates…[/]"), spinner="dots"):
                choices = [Choice(m, ("● " if self.entry and m.name == self.entry.name else "  ") + m.name,
                                  self._model_meta(m)) for m in self.catalog.models]
            cur = next((i for i, c in enumerate(choices) if self.entry and c.value.name == self.entry.name), 0)
            entry = select(T("Elige un modelo", "Choose a model"), choices, render.current, cur)
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
            raise ValueError(T("Uso: /mem [modelo.gguf] [ctx] [gpu]  (Ollama gestiona su propia memoria)", "Usage: /mem [model.gguf] [ctx] [gpu]  (Ollama manages its own memory)"))
        if entry is self.entry and self.plan is not None:   # por defecto: la configuración cargada
            ctx = self.n_ctx if ctx is None else ctx
            gpu = self.plan.ngl if gpu is None else gpu
        plan = self.make_plan(entry, ctx=ctx, gpu=gpu)
        real = self.engine.real if (isinstance(self.engine, LlamaEngine) and entry is self.entry and not parts) else None
        render.mem_report(plan.est, plan.sysmem, plan.vram_budget, plan.ram_budget, real=real, reclaim=plan.reclaim,
                          title=T(f"Estimación · {entry.name} · ctx {plan.n_ctx} · gpu {plan.ngl}/{max_gpu_layers(entry.info)}", f"Estimate · {entry.name} · ctx {plan.n_ctx} · gpu {plan.ngl}/{max_gpu_layers(entry.info)}"))
        if not plan.fits_vram or not plan.fits_ram:
            for sgg in suggestions(entry.info, plan.vram_budget, plan.ram_budget, plan.n_ctx, plan.ngl,
                                   kv_type=self.s.kv_type, swa_full=self.s.swa_full, flash_attn=self.s.flash_attn,
                                   mmproj=entry.mmproj):
                say(f"   [dim]→[/] {sgg}")

    def cmd_ctx(self, arg: str):
        if not arg:
            if self.engine is None:
                raise ValueError(T("No hay modelo cargado.", "No model is loaded."))
            used = self.context_used()
            say(T(f"Contexto actual: [accent]{self.n_ctx}[/] tokens · usado ~{used} ({used / max(self.n_ctx, 1):.0%})", f"Current context: [accent]{self.n_ctx}[/] tokens · used ~{used} ({used / max(self.n_ctx, 1):.0%})"))
            if isinstance(self.engine, LlamaEngine):
                info = self.entry.info
                say(T(f"Máximo del modelo (GGUF {info.arch}.context_length): [accent]{info.context_length}[/]", f"Model maximum (GGUF {info.arch}.context_length): [accent]{info.context_length}[/]"))
                p = self.make_plan(self.entry, ctx=self.n_ctx, gpu=self.plan.ngl)
                say(T(f"Caché KV: {p.est.kv_per_token / 1024:.1f} KB/token ({self.s.kv_type})", f"KV cache: {p.est.kv_per_token / 1024:.1f} KB/token ({self.s.kv_type})"))
                tb = Table(box=None, padding=(0, 2))
                tb.add_column("ctx", justify="right")
                tb.add_column("VRAM est.", justify="right")
                tb.add_column("")
                for c in [c for c in CTX_STEPS if c <= max(info.context_length, 4096)]:
                    e = estimate(info, c, self.plan.ngl, self.s.kv_type, self.s.swa_full, self.s.flash_attn,
                                 self.entry.mmproj, calib=self.s.models.get(self.entry.name, {}).get("calib", 1.0))
                    okv = e.vram <= p.vram_budget
                    tb.add_row(render.human_tokens(c), f"{render.gb(e.vram)} GB",
                               T("[ok]cabe[/]" if okv else "[bad]no cabe[/]", "[ok]fits[/]" if okv else "[bad]doesn't fit[/]"))
                console.print(tb)
                say(T("[dim]Uso: /ctx 16k · /ctx 32768 · /ctx auto[/]", "[dim]Usage: /ctx 16k · /ctx 32768 · /ctx auto[/]"))
            return
        val = "auto" if arg.lower() == "auto" else parse_size(arg)
        if val != "auto" and val < 256:
            raise ValueError(T("El contexto mínimo razonable es 512 tokens.", "The smallest sensible context is 512 tokens."))
        if isinstance(self.engine, OllamaEngine):
            self.s.ctx = val if val != "auto" else 8192
            self.engine.n_ctx = int(self.s.ctx)
            ok(T(f"Ollama usará num_ctx={self.engine.n_ctx}", f"Ollama will use num_ctx={self.engine.n_ctx}"))
            return
        if self.reload_with(ctx=val):
            self.s.model_cfg(self.entry.name)["ctx"] = val
            save_settings(self.s)

    def cmd_gpu(self, arg: str):
        if not arg:
            if self.plan:
                say(T(f"Capas en GPU: [accent]{self.plan.ngl}[/] de {max_gpu_layers(self.entry.info)} "
                    f"(ajuste guardado: {self.s.models.get(self.entry.name, {}).get('gpu', self.s.gpu)})", f"GPU layers: [accent]{self.plan.ngl}[/] of {max_gpu_layers(self.entry.info)} "
                    f"(saved setting: {self.s.models.get(self.entry.name, {}).get('gpu', self.s.gpu)})"))
            say(T("[dim]Uso: /gpu 20 · /gpu auto · /gpu max[/]", "[dim]Usage: /gpu 20 · /gpu auto · /gpu max[/]"))
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
            say(T(f"Caché KV: [accent]{self.s.kv_type}[/]  [dim](f16 | q8_0 | q4_0)[/]", f"KV cache: [accent]{self.s.kv_type}[/]  [dim](f16 | q8_0 | q4_0)[/]"))
            return
        if arg not in ("f16", "q8_0", "q4_0"):
            raise ValueError(T("Tipos válidos: f16, q8_0, q4_0", "Valid types: f16, q8_0, q4_0"))
        old = self.s.kv_type
        self.s.kv_type = arg
        TRANSIENT.pop("kv_type", None)
        if self.engine is not None and not self.reload_with():
            self.s.kv_type = old
        save_settings(self.s)

    def cmd_swa(self, arg: str):
        if arg not in ("min", "full"):
            say(T(f"Caché SWA: [accent]{'full' if self.s.swa_full else 'min'}[/]  [dim](min ahorra mucha VRAM en "
                f"Gemma; full permite reutilizar más caché entre turnos)[/]", f"SWA cache: [accent]{'full' if self.s.swa_full else 'min'}[/]  [dim](min saves a lot of VRAM on "
                f"Gemma; full lets more cache be reused between turns)[/]"))
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
            raise ValueError(T(f"{label} debe estar entre {lo} y {hi}", f"{label} must be between {lo} and {hi}"))
        setattr(self.s, name, v)
        TRANSIENT.pop(name, None)
        save_settings(self.s)
        ok(f"{label} = {v}")

    def cmd_tokens(self, arg):
        self._num_param("max_tokens", arg, lambda x: parse_size(x), 16, 262144, T("Máx. tokens por respuesta", "Max tokens per answer"))

    def cmd_temp(self, arg):
        self._num_param("temperature", arg, float, 0.0, 2.0, T("Temperatura", "Temperature"))

    def cmd_top_p(self, arg):
        self._num_param("top_p", arg, float, 0.0, 1.0, "Top-p")

    def cmd_top_k(self, arg):
        self._num_param("top_k", arg, int, 0, 1000, "Top-k")

    def cmd_min_p(self, arg):
        self._num_param("min_p", arg, float, 0.0, 1.0, "Min-p")

    def cmd_repeat(self, arg):
        self._num_param("repeat_penalty", arg, float, 0.5, 2.0, T("Penalización de repetición", "Repetition penalty"))

    def cmd_seed(self, arg):
        self._num_param("seed", arg, int, -1, 2**31 - 1, T("Semilla", "Seed"))

    def cmd_params(self, arg: str):
        tb = Table(box=None, padding=(0, 2), show_header=False)
        tb.add_column(style="dim")
        tb.add_column(style="accent")
        rows = [(T("modelo", "model"), self.engine.name if self.engine else "-"),
                ("n_ctx", f"{self.n_ctx} ({T('ajuste', 'setting')}: {self.s.models.get(self.entry.name, {}).get('ctx', self.s.ctx) if self.entry else self.s.ctx})"),
                ("n_gpu_layers", f"{self.plan.ngl if self.plan else '-'}"),
                (T("caché KV / SWA / flash-attn", "KV cache / SWA / flash-attn"), f"{self.s.kv_type} / {'full' if self.s.swa_full else 'min'} / {self.s.flash_attn}"),
                ("max_tokens", self.s.max_tokens), ("temperature", self.s.temperature), ("top_p", self.s.top_p),
                ("top_k", self.s.top_k), ("min_p", self.s.min_p), ("repeat_penalty", self.s.repeat_penalty),
                ("seed", self.s.seed), (T("razonamiento", "reasoning"), f"{'on' if self.s.thinking else 'off'} / {'visible' if self.s.show_thinking else T('plegado', 'collapsed')}")]
        rows += [(k, getattr(self.s, k)) for k in SETTABLE]
        for k, v in rows:
            tb.add_row(k, str(v))
        console.print(Panel(tb, title=T("Parámetros", "Parameters"), title_align="left", border_style="border"))

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
        if k == "language":
            val = val.lower()
            if val not in ("auto", *i18n.LANGS):
                raise ValueError(T("language: auto (el de Windows) | es | en", "language: auto (your Windows language) | es | en"))
        if k == "project_context" and val not in ("grafo", "archivos"):
            raise ValueError(T("project_context: grafo (red del proyecto, menos tokens) | archivos (adjunta todos los "
                             "archivos pequeños nuevos o cambiados)", "project_context: grafo (project map, fewer tokens) | archivos (attaches every small "
                             "new or changed file)"))
        setattr(self.s, k, val)
        save_settings(self.s)
        if k == "language":
            self.set_language(val)
            TRANSIENT.pop("language", None)
        if k == "project_context" and self.workspace is not None:
            self.workspace.context_mode = val
            self.workspace.refresh()
            self.workspace.instructions()
            self._mark_ctx()
        ok(f"{k} = {val}")

    # ---------------------------------------------------------------- think
    def cmd_think(self, arg: str):
        a = arg.lower()
        if a in ("on", "off"):
            self.s.thinking = a == "on"
            TRANSIENT.pop("thinking", None)
            if a == "on" and self.engine is not None and not getattr(self.engine, "can_think", False):
                warn(T("La plantilla de este modelo no tiene modo de razonamiento; se aplicará con modelos que sí.", "This model's template has no reasoning mode; it will apply to models that do."))
            ok(f"Razonamiento {'activado' if self.s.thinking else 'desactivado'}.")
        elif a in ("show", "hide"):
            self.s.show_thinking = a == "show"
            ok(f"Razonamiento {'desplegado' if self.s.show_thinking else 'plegado'}.")
        elif a == "last":
            if not self.last_think:
                say(T("[dim]No hay razonamiento previo.[/]", "[dim]There is no previous reasoning.[/]"))
            else:
                console.print(Panel(Text(self.last_think, style="think"), title=T("💭 último razonamiento", "💭 last reasoning"),
                                    border_style="dim"))
            return
        else:
            say(T(f"Razonamiento: pedir={'on' if self.s.thinking else 'off'} · mostrar="
                f"{'show' if self.s.show_thinking else 'hide'} · modelo compatible="
                f"{'sí' if getattr(self.engine, 'can_think', False) else 'no'}  [dim](/think on|off|show|hide|last)[/]", f"Reasoning: request={'on' if self.s.thinking else 'off'} · display="
                f"{'show' if self.s.show_thinking else 'hide'} · model supports it="
                f"{'yes' if getattr(self.engine, 'can_think', False) else 'no'}  [dim](/think on|off|show|hide|last)[/]"))
            return
        save_settings(self.s)

    # ---------------------------------------------------------------- compact
    def cmd_compact(self, arg: str):
        if len(self.history) < 2:
            say(T("[dim]No hay suficiente conversación que compactar.[/]", "[dim]There isn't enough conversation to compact.[/]"))
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
            warn(T("La conversación es más larga que el contexto: se resume la parte más reciente que cabe.", "The conversation is longer than the context: summarising the most recent part that fits."))
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
            warn(T("Compactación cancelada; la conversación no se ha modificado.", "Compaction cancelled; the conversation was not changed."))
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
            warn(T(f"El resumen no libera contexto (~{before} → ~{after} tokens; habitual con modelos pequeños o "
                 f"conversaciones cortas). Se conserva la conversación original.", f"The summary doesn't free any context (~{before} → ~{after} tokens; common with small models or "
                 f"short conversations). Keeping the original conversation."))
            return
        ok(T(f"Conversación compactada: ~{before} → ~{after} tokens.", f"Conversation compacted: ~{before} → ~{after} tokens."))
        self.autosave()

    # ---------------------------------------------------------------- sesiones
    def _apply_state(self, d: dict, name: str):
        self.history = d.get("history", [])
        self.thoughts = {}
        if d.get("system"):
            self.s.system_prompt = d["system"]
        self._mark_ctx()
        turns = sum(1 for m in self.history if m.get("role") == "user")
        ok(T(f"Sesión '{name}' cargada ({turns} turnos).", f"Session '{name}' loaded ({turns} turns)."))
        if d.get("model") and self.entry and d["model"] != self.entry.name:
            say(T(f"[dim]Se creó con {d['model']}; sigues con {self.entry.name} (usa /model si quieres cambiar).[/]", f"[dim]It was created with {d['model']}; you're still on {self.entry.name} (use {self.cmd_name('model')} to switch).[/]"))
        for m in self.history[-4:]:
            who = "[user]›[/]" if m["role"] == "user" else "[assistant]◆[/]"
            say(f"{who} [dim]{m['content'][:160].replace(chr(10), ' ')}[/]")

    def cmd_resume(self, arg: str):
        from .inputbox import Choice, select
        items = sessions.list_sessions()
        if not items:
            say(T("[dim]No hay sesiones guardadas todavía.[/]", "[dim]There are no saved sessions yet.[/]"))
            return
        choices = [Choice(i, ("⟳ " if i.auto else "★ ") + time.strftime("%d/%m %H:%M", time.localtime(i.updated)),
                          T(f"{i.turns:3} turnos · {i.model[:28]:28} · {i.preview}", f"{i.turns:3} turns · {i.model[:28]:28} · {i.preview}")) for i in items[:60]]
        sel = select(T("Retomar sesión (★ guardadas · ⟳ autoguardadas)", "Resume a session (★ saved · ⟳ autosaved)"), choices, render.current)
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
                raise ValueError(T("Uso: /chat save <nombre>", "Usage: /chat save <name>"))
            p = sessions.save_named(name, self.state())
            ok(T(f"Sesión guardada en {p}", f"Session saved to {p}"))
        elif action in ("list", ""):
            items = sessions.list_sessions(include_auto=False)
            if not items:
                say(T(f"[dim]No hay sesiones con nombre (/chat save <nombre>). {self.cmd_name('resume')} muestra también las autoguardadas.[/]", f"[dim]There are no named sessions (/chat save <name>). {self.cmd_name('resume')} also shows the autosaved ones.[/]"))
                return
            tb = Table(box=None, padding=(0, 2))
            for c in T(("Nombre", "Fecha", "Turnos", "Modelo", "Inicio"), ("Name", "Date", "Turns", "Model", "Start")):
                tb.add_column(c)
            for i in items:
                tb.add_row(i.name, time.strftime("%d/%m/%Y %H:%M", time.localtime(i.updated)), str(i.turns), i.model, i.preview)
            console.print(tb)
        elif action == "load":
            if not name:
                raise ValueError(T("Uso: /chat load <nombre>", "Usage: /chat load <name>"))
            self._apply_state(sessions.load(name), name)
        elif action in ("delete", "rm"):
            sessions.delete(name)
            ok(T(f"Sesión '{name}' borrada.", f"Session '{name}' deleted."))
        else:
            raise ValueError(T("Uso: /chat save|list|load|delete [nombre]", "Usage: /chat save|list|load|delete [name]"))

    def cmd_export(self, arg: str):
        path = Path(arg.strip('"')) if arg else Path.cwd() / f"conversacion-{time.strftime('%Y%m%d-%H%M')}.md"
        if path.suffix.lower() != ".md":
            path = path.with_suffix(".md")
        sessions.export_markdown(path, self.engine.name if self.engine else "?", self.system_text(), self.history, self.thoughts)
        ok(T(f"Conversación exportada a {path.resolve()}", f"Conversation exported to {path.resolve()}"))

    def cmd_save(self, arg: str):
        if not self.last_reply:
            raise ValueError(T("No hay ninguna respuesta todavía.", "There is no answer yet."))
        blocks = filemod.extract_blocks(self.last_reply)
        if not blocks:
            raise ValueError(T("La última respuesta no contiene bloques de código. (/export guarda la conversación)", f"The last answer has no code blocks. ({self.cmd_name('export')} saves the conversation)"))
        parts = arg.split()
        idx = int(parts.pop()) if parts and parts[-1].isdigit() else None
        path = " ".join(parts).strip().strip('"')
        cwd = Path.cwd()
        if idx is not None and not 1 <= idx <= len(blocks):
            raise ValueError(T(f"Hay {len(blocks)} bloque(s); usa un número entre 1 y {len(blocks)}.", f"There are {len(blocks)} block(s); use a number from 1 to {len(blocks)}."))
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
        self._save_blocks(items, ask, T("El archivo ya existe. ¿Sobrescribir?", "The file already exists. Overwrite?"))

    def cmd_open(self, arg: str):
        if arg:
            target = Path(arg.strip().strip('"')).expanduser()
        elif self.last_saved:
            target = self.last_saved[-1]
        else:
            raise ValueError(T("No hay ningún archivo guardado en esta sesión. Uso: /open <ruta>", f"No file has been saved in this session. Usage: {self.cmd_name('open')} <path>"))
        if not target.is_absolute():
            target = Path.cwd() / target
        if not target.exists():
            raise FileNotFoundError(f"No existe: {target}")
        try:
            os.startfile(str(target))
            ok(f"Abriendo {target}")
        except OSError as e:
            error(T(f"No se pudo abrir: {e}", f"Could not open it: {e}"))

    def cmd_archivos(self, arg: str):
        d = self.files_dir
        files = sorted(p for p in d.rglob("*") if p.is_file()) if d.exists() else []
        if arg.strip().lower().startswith("abr"):
            d.mkdir(parents=True, exist_ok=True)
            os.startfile(str(d))
            ok(f"Abriendo {d}")
            return
        if not files:
            say(T(f"[dim]El modelo aún no ha creado archivos en esta sesión. Se guardarán en {escape(render.short_path(d))}[/]", f"[dim]The model hasn't created any files in this session yet. They will be saved in {escape(render.short_path(d))}[/]"))
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
        say(T("[dim]/open <archivo> lo abre · /archivos abrir abre la carpeta · /save <ruta> copia el último código[/]", "[dim]/open <file> opens it · /files open opens the folder · /save <path> copies the last code[/]"))

    def cmd_vision(self, arg: str):
        sub, _, rest = arg.strip().partition(" ")
        sub = sub.lower()
        rd = self.reader_entry
        if sub in ("lector", "directo"):
            if sub == "directo" and not self.vision:
                warn(T("El modelo actual no tiene visión propia: seguirá usándose el lector hasta que cargues uno con 👁.", "The current model has no vision of its own: the reader will keep being used until you load one with 👁."))
            self.s.vision_mode = sub
            save_settings(self.s)
            ok(T(f"Modo de visión: {sub}", f"Vision mode: {sub}"))
            return
        if sub == "modelo":
            name = rest.strip()
            if not name or name == "auto":
                self.s.vision_model = "auto"
            else:
                m = self.catalog.find(name)
                if m is None or not m.has_vision:
                    raise ValueError(T(f"'{name}' no es un modelo con visión instalada (/models).", f"'{name}' is not an installed model with vision (/models)."))
                if not reader.usable(m):
                    raise ValueError(T(f"{m.name} no trae plantilla de chat y su visión no puede funcionar (suele ser una "
                                       f"conversión defectuosa): descarga otra versión (unsloth o bartowski).",
                                       f"{m.name} has no chat template, so its vision cannot work (usually a faulty "
                                       f"conversion): download another build (unsloth or bartowski)."))
                self.s.vision_model = m.name
            save_settings(self.s)
            rd = self.reader_entry
            ok(T(f"Lector visual: {rd.name if rd else 'ninguno disponible'}", f"Visual reader: {rd.name if rd else 'none available'}"))
            return
        if sub == "ver":
            if not self.last_transcripts:
                say(T("[dim]Todavía no hay transcripciones en esta sesión.[/]", "[dim]There are no transcriptions in this session yet.[/]"))
            for label, txt in self.last_transcripts:
                console.print(Panel(render.md(txt), title=escape(label), title_align="left", border_style="border"))
            return
        if sub == "limpiar":
            n = reader.clear_cache()
            ok(T(f"{n} transcripción(es) borrada(s) de la memoria.", f"{n} transcription(s) deleted from memory."))
            return
        n, size = reader.cache_stats()
        say(T(f"Modo: [accent]{self.s.vision_mode}[/] [dim](lector = un modelo con visión transcribe las imágenes y "
            f"tu modelo trabaja con el texto · directo = las imágenes van al modelo si tiene mmproj)[/]", f"Mode: [accent]{self.s.vision_mode}[/] [dim](lector = a vision model transcribes the images and "
            f"your model works on the text · directo = images go to the model if it has an mmproj)[/]"))
        say(T(f"Lector: [accent]{escape(rd.name) if rd else 'ninguno (descarga un mmproj: /doctor)'}[/]"
            f" [dim]({'automático' if self.s.vision_model == 'auto' else 'elegido'})[/]", f"Reader: [accent]{escape(rd.name) if rd else 'none (download an mmproj: /doctor)'}[/]"
            f" [dim]({'automatic' if self.s.vision_model == 'auto' else 'chosen'})[/]"))
        say(T(f"Transcripciones guardadas: {n} ({size / 1024:.0f} KB) en [dim]{escape(str(reader.CACHE_DIR))}[/]", f"Saved transcriptions: {n} ({size / 1024:.0f} KB) in [dim]{escape(str(reader.CACHE_DIR))}[/]"))
        say(T("[dim]/vision lector|directo · /vision modelo <nombre|auto> · /vision ver · /vision limpiar[/]", "[dim]/vision reader|direct · /vision model <name|auto> · /vision show · /vision clear[/]"))

    def cmd_settings(self, arg: str):
        from .inputbox import Choice, select
        s = self.s

        yes = T("sí", "yes")

        def onoff(v):
            return yes if v else "no"

        while True:
            rd = self.reader_entry
            items = [
                ("language", T("Idioma", "Language"),
                 f"{s.language} ({self.lang})" if s.language == "auto" else s.language, ["auto", "es", "en"]),
                ("vision_mode", T("Modo de visión", "Vision mode"), s.vision_mode, ["lector", "directo"]),
                ("vision_model", T("Modelo lector", "Reader model"), f"{s.vision_model}" + (f" ({rd.name})" if s.vision_model == "auto" and rd else ""),
                 ["auto"] + [m.name for m in self.catalog.models if reader.usable(m)]),
                ("save_files", T("Guardar archivos del modelo", "Save the model's files"), s.save_files, ["auto", "ask", "never"]),
                ("thinking", T("Pedir razonamiento", "Request reasoning"), onoff(s.thinking), [yes, "no"]),
                ("show_thinking", T("Mostrar razonamiento", "Show reasoning"), onoff(s.show_thinking), [yes, "no"]),
                ("theme", T("Tema", "Theme"), s.theme, list(render.THEMES)),
                ("max_tokens", T("Máx. tokens por respuesta", "Max tokens per answer"), str(s.max_tokens), None),
                ("temperature", T("Temperatura", "Temperature"), str(s.temperature), None),
                ("kv_type", T("Caché KV (recarga)", "KV cache (reloads)"), s.kv_type, ["f16", "q8_0", "q4_0"]),
                ("shell", T("Shell para !comandos", "Shell for !commands"), s.shell, ["powershell", "cmd"]),
                ("pdf_max_image_pages", T("Máx. páginas escaneadas por PDF", "Max scanned pages per PDF"), str(s.pdf_max_image_pages), None),
                ("relay_auto", T("Relevo automático", "Automatic relay"), onoff(s.relay_auto), [yes, "no"]),
                ("relay_reserve", T("Reserva para el relevo (%)", "Relay reserve (%)"), str(s.relay_reserve), ["10", "15", "20", "25"]),
                ("relay_max", T("Relevos máximos por respuesta", "Max relays per answer"), str(s.relay_max), ["4", "8", "12", "20"]),
            ]
            width = max(len(t) for _, t, _, _ in items)
            choices = [Choice(i, f"{t.ljust(width)}  {v}") for i, (_, t, v, _) in enumerate(items)]
            idx = select(T("Ajustes (Enter cambia · Esc sale)", "Settings (Enter changes · Esc exits)"), choices, render.current, 0)
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
                    self.cmd_think({"thinking": "on" if val == yes else "off",
                                    "show_thinking": "show" if val == yes else "hide"}[key])
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
                    self.s.relay_auto = val == yes
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
            warn(T("No hay respuestas que copiar.", "There are no answers to copy."))
        elif copy_to_clipboard(self.last_reply):
            ok(T("Última respuesta copiada al portapapeles.", "Last answer copied to the clipboard."))
        else:
            error(T("No se pudo acceder al portapapeles.", "Could not access the clipboard."))

    def cmd_system(self, arg: str):
        if not arg:
            shown = DEFAULT_SYSTEM_PROMPT_EN if self.lang == "en" and self.s.system_prompt == DEFAULT_SYSTEM_PROMPT else self.s.system_prompt
            console.print(Panel(shown, title=T("Prompt de sistema", "System prompt"), border_style="border"))
            if self.memory_text:
                say(T("[dim]+ memoria de Clay-Myrrh.md (/memory show) + fecha y directorio[/]", "[dim]+ Clay-Myrrh.md memory (/memory show) + date and folder[/]"))
            return
        self.s.system_prompt = DEFAULT_SYSTEM_PROMPT if arg.lower() == "reset" else arg
        TRANSIENT.pop("system_prompt", None)
        save_settings(self.s)
        self._mark_ctx()
        ok(T("Prompt de sistema actualizado (se aplica desde el próximo mensaje; la conversación se conserva).", "System prompt updated (applies from the next message; the conversation is kept)."))

    def cmd_memory(self, arg: str):
        sub, _, rest = arg.partition(" ")
        sub = sub.lower() or "show"
        if sub == "show":
            files = memory_files()
            if not files:
                say(T(f"[dim]Sin memoria. Crea instrucciones con /memory add <texto> (global: {GLOBAL_MEMORY_FILE}) "
                    f"o /memory addp <texto> (proyecto: {project_memory_file()}).[/]", f"[dim]No memory. Create instructions with /memory add <text> (global: {GLOBAL_MEMORY_FILE}) "
                    f"or /memory addp <text> (project: {project_memory_file()}).[/]"))
                return
            for f in files:
                console.print(Panel(render.md(f.read_text(encoding="utf-8")), title=str(f), border_style="border"))
        elif sub in ("add", "addp"):
            if not rest:
                raise ValueError(T(f"Uso: /memory {sub} <texto>", f"Usage: /memory {sub} <text>"))
            f = GLOBAL_MEMORY_FILE if sub == "add" else project_memory_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            prev = f.read_text(encoding="utf-8") if f.exists() else T("# Memoria de Clay-Myrrh\n", "# Clay-Myrrh memory\n")
            f.write_text(prev.rstrip() + f"\n- {rest.strip()}\n", encoding="utf-8")
            self.memory_text = load_memory_text()
            self._mark_ctx()
            ok(T(f"Añadido a {f}", f"Added to {f}"))
        elif sub == "edit":
            f = project_memory_file() if rest.strip().startswith("proy") else GLOBAL_MEMORY_FILE
            if not f.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(T("# Memoria de Clay-Myrrh\n\n- \n", "# Clay-Myrrh memory\n\n- \n"), encoding="utf-8")
            editor = os.environ.get("EDITOR", "notepad")
            say(T(f"[dim]Abriendo {f} con {editor}… (guarda y cierra para continuar)[/]", f"[dim]Opening {f} with {editor}… (save and close to continue)[/]"))
            subprocess.call([editor, str(f)])
            self.memory_text = load_memory_text()
            self._mark_ctx()
            ok(T("Memoria recargada.", "Memory reloaded."))
        elif sub == "reload":
            self.memory_text = load_memory_text()
            self._mark_ctx()
            ok(T(f"Memoria recargada ({len(self.memory_text)} caracteres de {len(memory_files())} archivo(s)).", f"Memory reloaded ({len(self.memory_text)} characters from {len(memory_files())} file(s))."))
        elif sub == "path":
            say(T(f"Global:   {GLOBAL_MEMORY_FILE} {'[ok](existe)[/]' if GLOBAL_MEMORY_FILE.exists() else '[dim](no existe)[/]'}", f"Global:   {GLOBAL_MEMORY_FILE} {'[ok](exists)[/]' if GLOBAL_MEMORY_FILE.exists() else '[dim](does not exist)[/]'}"))
            pf = project_memory_file()
            say(T(f"Proyecto: {pf} {'[ok](existe)[/]' if pf.exists() else '[dim](no existe)[/]'}", f"Project:  {pf} {'[ok](exists)[/]' if pf.exists() else '[dim](does not exist)[/]'}"))
        else:
            raise ValueError(T("Uso: /memory [show|add <texto>|addp <texto>|edit [proyecto]|reload|path]", "Usage: /memory [show|add <text>|addp <text>|edit [project]|reload|path]"))

    def cmd_stats(self, arg: str):
        s = self.stats
        el = time.time() - s.started
        tb = Table(box=None, padding=(0, 2), show_header=False)
        tb.add_column(style="dim")
        tb.add_column(style="accent")
        avg_tps = s.tokens_out / s.gen_time if s.gen_time else 0
        sep = (lambda n: f"{n:,}") if i18n.LANG == "en" else (lambda n: f"{n:,}".replace(",", "."))
        rows = [(T("Modelo", "Model"), f"{self.engine.name if self.engine else '-'} ({getattr(self.engine, 'kind', '-')})"),
                (T("Duración de la sesión", "Session length"), f"{int(el // 60)} min {int(el % 60)} s"),
                (T("Turnos", "Turns"), T(f"{s.turns} ({s.interrupted} interrumpidos)", f"{s.turns} ({s.interrupted} interrupted)")),
                (T("Tokens de entrada (prompt evaluado)", "Input tokens (prompt evaluated)"), sep(s.tokens_in)),
                (T("Tokens de salida", "Output tokens"), sep(s.tokens_out)),
                (T("Velocidad media de generación", "Average generation speed"), f"{avg_tps:.1f} t/s"),
                (T("Tiempo procesando prompts / generando", "Time processing prompts / generating"),
                 f"{s.prompt_time:.1f} s / {s.gen_time:.1f} s"),
                (T("Contexto", "Context"), f"~{self.context_used()} / {self.n_ctx} tokens"),
                (T("Mensajes en el historial", "Messages in the history"), str(len(self.history)))]
        if s.last:
            rows.append((T("Último turno", "Last turn"), f"{s.last.prompt_tokens} in · {s.last.completion_tokens} out · "
                                         f"{s.last.tps:.1f} t/s · {s.last.t_total:.1f} s"))
        if isinstance(self.engine, LlamaEngine):
            rows.append((T("Carga del modelo", "Model load"), f"{self.engine.load_time:.1f} s"))
            rows.append((T("VRAM real (buffers) / estimada", "Actual VRAM (buffers) / estimated"), f"{render.gb(self.engine.real.gpu)} / "
                                                          f"{render.gb(self.plan.est.vram - self.plan.est.overhead_gpu)} GB"))
        for k, v in rows:
            tb.add_row(k, v)
        console.print(Panel(tb, title=T("📊 Estadísticas", "📊 Statistics"), title_align="left", border_style="border"))

    def cmd_theme(self, arg: str):
        name = arg.strip().lower()
        if not name:
            from .inputbox import Choice, select
            name = select(T("Tema", "Theme"), [Choice(n, n, T(f"código: {t.code}", f"code: {t.code}")) for n, t in render.THEMES.items()],
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
        ok(T(f"Tema '{name}' aplicado.", f"Theme '{name}' applied."))
        console.print(render.md("```python\ndef hola(nombre: str) -> str:\n    return f\"Hola, {nombre}\"  # vista previa\n```"))

    # ---------------------------------------------------------------- adjuntos
    def _attach(self, path: str, **kw):
        att = load_any(path, vision=self.can_see(), settings=self.s, **kw)
        self.pending.append(att)
        render.tool("ok", T("Adjuntar", "Attach"), att.name, (att.note or att.summary()) + T(" · va con tu próximo mensaje", " · goes with your next message"))

    def cmd_file(self, arg: str):
        if not arg:
            raise ValueError(T(f"Uso: {self.cmd_name('file')} <ruta>  (o escribe @ruta en el mensaje)", f"Usage: {self.cmd_name('file')} <path>  (or type @path in the message)"))
        self._attach(arg)

    def cmd_image(self, arg: str):
        if not arg:
            raise ValueError(T(f"Uso: {self.cmd_name('image')} <ruta>", f"Usage: {self.cmd_name('image')} <path>"))
        self._attach(arg)

    def cmd_pdf(self, arg: str):
        m = re.match(r'^("[^"]+"|\S+)(?:\s+(auto|texto|text|imagenes|images))?(?:\s+([\d,\- ]+))?$', arg.strip())
        if not m:
            raise ValueError(T("Uso: /pdf <ruta> [auto|texto|imagenes] [páginas, p. ej. 1-5,8]", "Usage: /pdf <path> [auto|text|images] [pages, e.g. 1-5,8]"))
        mode = {"texto": "text", "imagenes": "images"}.get(m.group(2) or "auto", m.group(2) or "auto")
        self._attach(m.group(1).strip('"'), pdf_mode=mode, pages=(m.group(3) or "").replace(" ", "") or None)

    def cmd_attachments(self, arg: str):
        if arg == "clear":
            self.pending = []
            ok(T("Adjuntos pendientes eliminados.", "Pending attachments removed."))
            return
        if not self.pending:
            say(T("[dim]No hay adjuntos pendientes.[/]", "[dim]There are no pending attachments.[/]"))
            return
        for a in self.pending:
            say(f"  📎 {a.summary()}")

    def cmd_mmproj(self, arg: str):
        if self.entry is None or self.entry.kind != "gguf":
            raise ValueError(T("Solo aplica a modelos GGUF.", "Only applies to GGUF models."))
        if not arg:
            cur = self.entry.mmproj
            say(T(f"mmproj actual: [accent]{Path(cur.path).name if cur else 'ninguno'}[/] "
                f"[dim]({cur.projector_type if cur else self.entry.vision_note or 'sin visión'})[/]", f"current mmproj: [accent]{Path(cur.path).name if cur else 'none'}[/] "
                f"[dim]({cur.projector_type if cur else self.entry.vision_note or 'no vision'})[/]"))
            for p in self.catalog.projectors:
                users = self.catalog.users_of(p)
                say(T(f"  · {Path(p.path).name}: {p.projector_type}, projection_dim {p.projection_dim} "
                    f"[dim]→ {', '.join(users) or 'ningún modelo compatible'}[/]", f"  · {Path(p.path).name}: {p.projector_type}, projection_dim {p.projection_dim} "
                    f"[dim]→ {', '.join(users) or 'no compatible model'}[/]"))
            return
        ov = None if arg == "auto" else arg
        mc = self.s.model_cfg(self.entry.name)
        if ov is None:
            mc.pop("mmproj", None)
        else:
            mc["mmproj"] = ov
        pair_mmproj(self.entry, self.catalog.projectors, ov)
        say(T(f"[dim]Visión: {self.entry.vision} {self.entry.vision_note}[/]", f"[dim]Vision: {self.entry.vision} {self.entry.vision_note}[/]"))
        self.reload_with()

    def cmd_debug(self, arg: str):
        if arg in ("on", "off"):
            llamalog.echo = arg == "on"
            ok(T(f"Log nativo en vivo {'activado' if llamalog.echo else 'desactivado'}.", f"Live native log {'enabled' if llamalog.echo else 'disabled'}."))
            return
        noise = ("tensor[", "create_tensor", "llama_model_loader: - ", "load_tensors: layer", "llama_kv_cache: layer")
        lines = [l for l in llamalog.tail(1500) if not any(n in l for n in noise)][-80:]
        console.print(Panel(Text("\n".join(lines) or T("(vacío)", "(empty)")), title=T("Últimas líneas del log de llama.cpp/mtmd", "Last lines of the llama.cpp/mtmd log"),
                            border_style="dim"))

    def cmd_setup(self, arg: str):
        if arg.strip() == "cli":
            from .setup_wizard import run_wizard
            done = run_wizard()
        else:
            from .setup_gui import run_gui
            say(T("[dim]Se abre el asistente en el navegador; cuando termines pulsa «Cerrar asistente» (o Ctrl+C aquí).[/]", "[dim]The assistant opens in the browser; when you're done press «Close assistant» (or Ctrl+C here).[/]"))
            done = run_gui()
        if done:
            from .config import load_settings as _ls
            new = _ls()
            self.s.profiles, self.s.vision_model = new.profiles, new.vision_model
            self.catalog = build_catalog(self.s)
            say(T(f"[dim]Usa {self.cmd_name('modo')} <categoría> para cambiar a uno de tus perfiles.[/]", f"[dim]Use {self.cmd_name('modo')} <category> to switch to one of your profiles.[/]"))

    def cmd_modo(self, arg: str):
        profiles = self.s.profiles or {}
        if not profiles:
            raise ValueError(T(f"Aún no tienes perfiles: ejecuta {self.cmd_name('setup')} (o instalar-clay-myrrh.bat).", f"You have no profiles yet: run {self.cmd_name('setup')} (or instalar-clay-myrrh.bat)."))
        key = arg.strip().lower()
        if not key:
            from .inputbox import Choice, select
            from .model_catalog import category
            names = {c: category(c).name for c in profiles}
            key = select(T("¿Qué perfil?", "Which profile?"), [Choice(k, names[k], v.get("model", "")) for k, v in profiles.items()],
                         render.current)
            if key is None:
                return
        prof = next((v for k, v in profiles.items() if k.startswith(key)), None)
        if prof is None:
            raise ValueError(T(f"No hay perfil '{key}'. Tienes: {', '.join(profiles)}", f"There is no profile '{key}'. You have: {', '.join(profiles)}"))
        for k in ("max_tokens", "temperature", "thinking"):
            if k in prof:
                setattr(self.s, k, prof[k])
        save_settings(self.s)
        entry = self.catalog.find(prof["model"])
        if entry is None:
            raise ValueError(T(f"El modelo del perfil ({prof['model']}) no está en models/. ¿Falta descargarlo? "
                             f"(clay-myrrh --download)", f"The profile's model ({prof['model']}) is not in models/. Does it still need downloading? "
                             f"(clay-myrrh --download)"))
        if self.entry is not None and entry.name == self.entry.name:
            ok(T(f"Perfil aplicado ({prof.get('max_tokens')} tokens, temp {prof.get('temperature')}, "
               f"razonamiento {'on' if prof.get('thinking') else 'off'}).", f"Profile applied ({prof.get('max_tokens')} tokens, temp {prof.get('temperature')}, "
               f"reasoning {'on' if prof.get('thinking') else 'off'})."))
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
            ok(T(f"Demo con {len(rep.components)} componentes abierta en el navegador · {render.short_path(d / 'pagina.lai')} "
               "(mira su código para ver cómo se escribe cada uno)", f"Demo with {len(rep.components)} components opened in the browser · {render.short_path(d / 'pagina.lai')} "
               "(look at its code to see how each one is written)"))
            return
        if a.startswith("chat"):
            mode = a.split()[-1] if len(a.split()) > 1 else "auto"
            if mode not in ("auto", "on", "off"):
                warn(T("Uso: /componentes chat auto|on|off", "Usage: /components chat auto|on|off"))
                return
            self.s.components_in_chat = mode
            save_settings(self.s)
            self._mark_ctx()
            ok(T({"auto": "Componentes en el chat: automáticos (solo cuando pides una página o plantilla)",
                "on": "Componentes en el chat: siempre (≈3k tokens de contexto en cada mensaje)",
                "off": "Componentes en el chat: desactivados (siguen en /plantilla y /zero)"}, {"auto": "Components in the chat: automatic (only when you ask for a page or template)",
                "on": "Components in the chat: always (≈3k context tokens in every message)",
                "off": "Components in the chat: off (still used in /template and /zero)"})[mode])
            return
        tb = Table(box=None, padding=(0, 2), show_header=False)
        tb.add_column(style="command", no_wrap=True)
        tb.add_column()
        for c in REG.values():
            tb.add_row(f"<{c.name}>", escape(c.desc))
        console.print(Panel(tb, title=T("Componentes prediseñados", "Prebuilt components"), title_align="left", border_style="border"))
        console.print(Panel(Text(webkit.EXAMPLE), title=T("Ejemplo (pagina.lai)", "Example (pagina.lai)"), title_align="left", border_style="dim"))
        say(T(f"[dim]{len(REG)} componentes · /plantilla <descripción> crea una página con ellos · /zero (elige "
            f"«Componentes») · en el chat: {self.s.components_in_chat} (/componentes chat auto|on|off)[/]", f"[dim]{len(REG)} components · /template <description> builds a page with them · /zero (choose "
            f"«Componentes») · in the chat: {self.s.components_in_chat} (/components chat auto|on|off)[/]"))

    def cmd_zero(self, arg: str):
        from .zero import run_zero
        run_zero(self, resume=arg.strip().lower().startswith("cont"))

    def cmd_doctor(self, arg: str):
        from .doctor import run_doctor
        run_doctor(self)


SETTABLE = ("pdf_max_pages", "pdf_max_image_pages", "image_max_side", "keep_images_turns",
            "vram_margin_mb", "auto_ctx_cap", "shell", "save_files", "project_rounds", "project_context",
            "language")


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------

def _edit_like(code: str) -> bool:
    """True si el «archivo» de un bloque son en realidad ediciones (pares BUSCAR/REEMPLAZAR o <editar>)."""
    return bool(wsmod._PAIR.search(code) or re.search(r"<editar\s", code, re.I))


def chain_steps(text: str) -> list[str]:
    """Separa una cadena de prompts por líneas que solo contienen --- (o ===)."""
    return [x.strip() for x in re.split(r"(?m)^[ \t]*(?:-{3,}|={3,})[ \t]*$", text) if x.strip()]


def parse_args(argv=None):
    # el idioma hace falta antes de analizar los argumentos (textos de --help)
    i18n.init_from_settings()
    av = list(sys.argv[1:] if argv is None else argv)
    if "--lang" in av and av.index("--lang") + 1 < len(av):
        i18n.set_lang(i18n.resolve(av[av.index("--lang") + 1]))
    o = lambda es, en: (en, es) if i18n.LANG == "en" else (es, en)    # nombre visible primero
    p = argparse.ArgumentParser(prog="clay-myrrh", description=T("Clay-Myrrh — tu IA, en casa (llama.cpp / Ollama)", "Clay-Myrrh — your AI, at home (llama.cpp / Ollama)"),
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("prompt", nargs="?", help=T("pregunta directa (modo one-shot; también acepta stdin)", "one-shot question (also accepts stdin)"))
    p.add_argument("-m", "--model", help=T("archivo GGUF (nombre o ruta) o modelo de Ollama", "GGUF file (name or path) or Ollama model"))
    p.add_argument("-i", "--image", action="append", help=T("imagen a adjuntar (repetible)", "image to attach (repeatable)"))
    p.add_argument("-p", "--pdf", help=T("PDF a adjuntar", "PDF to attach"))
    p.add_argument("-f", "--file", action="append", help=T("archivo de texto a adjuntar (repetible)", "text file to attach (repeatable)"))
    p.add_argument("-s", "--system", help=T("prompt de sistema", "system prompt"))
    p.add_argument("-d", "--dir", help=T("abrir una carpeta como proyecto (el modelo lee y modifica sus archivos)", "open a folder as a project (the model reads and edits its files)"))
    p.add_argument(*o("--cadena", "--chain"), dest="cadena", metavar=T("ARCHIVO", "FILE"),
                   help=T("ejecutar una cadena de prompts (separados por ---) al arrancar", "run a prompt chain (separated by ---) at startup"))
    p.add_argument(*o("--desde", "--from"), dest="desde", metavar="N", type=int, default=1, help=T("con --cadena: empezar en el paso N", "with --chain: start at step N"))
    p.add_argument(*o("--salir", "--exit"), dest="salir", action="store_true",
                   help=T("con --cadena: cerrar al terminar en lugar de quedarse en el chat", "with --chain: exit when finished instead of staying in the chat"))
    p.add_argument("--lang", choices=["auto", "es", "en"],
                   help=T("idioma de los comandos solo para esta sesión (/set language lo cambia para siempre)", "interface language for this session only (/set language changes it permanently)"))
    p.add_argument("--gpu", help=T("capas en GPU: n, auto o max", "GPU layers: n, auto or max"))
    p.add_argument("--ctx", help=T("contexto: n, 32k o auto", "context: n, 32k or auto"))
    p.add_argument("--tokens", type=int, help=T("máximo de tokens por respuesta", "maximum tokens per answer"))
    p.add_argument("--kv", choices=["f16", "q8_0", "q4_0"], help=T("tipo de caché KV", "KV cache type"))
    p.add_argument("--think", action="store_true", help=T("activar razonamiento", "enable reasoning"))
    p.add_argument("--theme", choices=list(render.THEMES), help=T("tema de colores", "colour theme"))
    p.add_argument("--list", action="store_true", help=T("listar modelos y salir", "list models and exit"))
    p.add_argument("--doctor", action="store_true", help=T("diagnóstico y salir", "diagnostics and exit"))
    p.add_argument(*o("--grafo", "--graph"), dest="grafo", nargs="?", const="", metavar=T("CARPETA", "FOLDER"),
                   help=T("crear o actualizar la red del proyecto en Clay-Myrrh.md (sin cargar modelo) y salir", "create or update the project map in Clay-Myrrh.md (without loading a model) and exit"))
    p.add_argument("--setup", action="store_true", help=T("asistente de instalación visual (se abre en el navegador)", "visual setup assistant (opens in the browser)"))
    p.add_argument("--setup-cli", action="store_true", help=T("asistente de instalación en la consola", "setup assistant in the console"))
    p.add_argument("--port", type=int, default=0, help=T("puerto del asistente visual (0 = libre)", "port of the visual assistant (0 = any free one)"))
    p.add_argument("--no-browser", action="store_true", help=T("no abrir el navegador automáticamente", "don't open the browser automatically"))
    p.add_argument("--download", action="store_true", help=T("descargar los modelos elegidos en el asistente", "download the models chosen in the assistant"))
    p.add_argument("--mem", action="store_true", help=T("estimación de memoria del modelo elegido y salir", "memory estimate for the chosen model and exit"))
    p.add_argument("-y", "--yes", action="store_true",
                   help=T("no pedir confirmación (cargar aunque no quepa; en one-shot, guardar archivos propuestos)", "don't ask for confirmation (load even if it doesn't fit; in one-shot, save proposed files)"))
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
            raise ValueError(T(f"No encuentro el modelo '{args.model}' (clay-myrrh --list)", f"I can't find the model '{args.model}' (clay-myrrh --list)"))
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
    if args.lang:
        override("language", args.lang)
    app = App(s, args)

    if args.list:
        app.cmd_models("")
        return
    if args.doctor:
        app.cmd_doctor("")
        return
    if args.grafo is not None:
        folder = Path(os.path.expandvars(args.grafo or args.dir or ".")).expanduser().resolve()
        if not folder.is_dir():
            error(T(f"No existe la carpeta {folder}.", f"The folder {folder} does not exist."))
            return
        os.chdir(folder)
        try:
            app.cmd_grafo("")
        except ValueError as e:
            error(str(e))
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
        error(T("No hay modelos: copia archivos .gguf en models/ o arranca Ollama.", "No models: copy .gguf files into models/ or start Ollama."))
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
            say(T("[dim]Cancelado. Usa /model dentro del CLI o --gpu/--ctx para otros valores.[/]", "[dim]Cancelled. Use /model inside the CLI or --gpu/--ctx for other values.[/]"))
            app.engine = None
            entry = None
    if entry is not None and not app.load(entry, plan):
        if one_shot:
            return
    if args.cadena or args.yes:
        app.assume_yes = args.yes or app.assume_yes
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
            say(T("[dim]No se envía la pregunta porque falló un adjunto (evita respuestas inventadas).[/]", "[dim]The question is not sent because an attachment failed (avoids made-up answers).[/]"))
            return
        if app.engine is None:
            return
        prompt = "\n\n".join(x for x in (piped, args.prompt or "") if x)
        app.assume_yes = args.yes        # one-shot: con -y se guardan los archivos propuestos
        app.send(prompt)
        return
    if args.cadena:
        if app.engine is None:
            error(T("No hay modelo cargado: la cadena no se puede ejecutar.", "No model is loaded: the chain cannot run."))
            return
        app.print_banner()
        try:
            text = Path(args.cadena).expanduser().read_text(encoding="utf-8")
        except OSError as e:
            error(T(f"No se pudo leer la cadena: {e}", f"Could not read the chain: {e}"))
            return
        app.run_chain(chain_steps(text), str(Path(args.cadena).expanduser()), args.desde)
        if args.salir:
            save_settings(app.s)
            return
        app.repl(banner=False)
        return
    app.repl()
