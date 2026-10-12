"""Salida con rich: temas, banner, tablas, informe de memoria y vista de streaming."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from .memory import GiB, MiB, Estimate, RealUsage, SysMem


# ---------------------------------------------------------------------------
# Temas
# ---------------------------------------------------------------------------

@dataclass
class ThemeDef:
    name: str
    accent: str
    user: str
    assistant: str
    warn: str
    err: str
    ok: str
    dim: str
    think: str
    code: str               # estilo pygments para bloques de código
    bar_bg: str             # fondo de la barra inferior (prompt_toolkit)
    bar_fg: str
    frame: str
    menu_bg: str
    menu_sel: str


THEMES: dict[str, ThemeDef] = {
    "oscuro": ThemeDef("oscuro", "#89b4fa", "#94e2d5", "#a6e3a1", "#f9e2af", "#f38ba8", "#a6e3a1",
                       "#7f849c", "#9399b2", "monokai", "#181825", "#cdd6f4", "#585b70", "#1e1e2e", "#45475a"),
    "claro": ThemeDef("claro", "#1e66f5", "#179299", "#40a02b", "#df8e1d", "#d20f39", "#40a02b",
                      "#8c8fa1", "#7c7f93", "friendly", "#e6e9ef", "#4c4f69", "#9ca0b0", "#eff1f5", "#bcc0cc"),
    "dracula": ThemeDef("dracula", "#bd93f9", "#8be9fd", "#50fa7b", "#f1fa8c", "#ff5555", "#50fa7b",
                        "#6272a4", "#7c86b4", "dracula", "#21222c", "#f8f8f2", "#6272a4", "#282a36", "#44475a"),
    "nord": ThemeDef("nord", "#88c0d0", "#8fbcbb", "#a3be8c", "#ebcb8b", "#bf616a", "#a3be8c",
                     "#616e88", "#7b88a1", "nord", "#2e3440", "#d8dee9", "#4c566a", "#3b4252", "#4c566a"),
    "solarizado": ThemeDef("solarizado", "#268bd2", "#2aa198", "#859900", "#b58900", "#dc322f", "#859900",
                           "#586e75", "#657b83", "solarized-dark", "#002b36", "#93a1a1", "#586e75", "#073642", "#586e75"),
}


def rich_theme(t: ThemeDef) -> Theme:
    return Theme({
        "accent": f"bold {t.accent}", "info": t.accent, "user": f"bold {t.user}",
        "assistant": f"bold {t.assistant}", "warning": t.warn, "error": f"bold {t.err}",
        "success": f"bold {t.ok}", "ok": t.ok, "mid": t.warn, "bad": t.err, "dim": t.dim,
        "think": f"italic {t.think}", "command": f"bold {t.accent}", "border": t.frame,
        "markdown.code": f"bold {t.accent}", "markdown.h1": f"bold {t.accent}",
        "markdown.h2": f"bold {t.accent}", "markdown.link": t.user,
        "prompt.choices": t.accent,
    })


console = Console(highlight=False, legacy_windows=False)
current = THEMES["oscuro"]


_theme_pushed = [False]


def set_theme(name: str) -> ThemeDef:
    global current
    current = THEMES.get(name, THEMES["oscuro"])
    if _theme_pushed[0]:
        console.pop_theme()
    console.push_theme(rich_theme(current))
    _theme_pushed[0] = True
    return current


set_theme("oscuro")


def say(msg: str, style: str = ""):
    console.print(msg, style=style, highlight=False)


def ok(msg: str):
    console.print(f"[success]✔[/] {escape(msg)}")


def warn(msg: str):
    console.print(f"[warning]⚠ {escape(msg)}[/]")


def error(msg: str, hints: list[str] | None = None):
    console.print(f"[error]✖ {escape(msg)}[/]")
    for h in hints or []:
        console.print(f"   [dim]→[/] {escape(h)}")


def md(text: str) -> Markdown:
    return Markdown(text, code_theme=current.code, inline_code_theme=current.code, hyperlinks=True)


def human_tokens(n: int) -> str:
    if n >= 1000 and n % 1000 == 0:
        return f"{n // 1000}k"
    if n >= 1024 and n % 1024 == 0:
        return f"{n // 1024}k"
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def short_path(p) -> str:
    """Ruta con ~ en lugar de la carpeta de usuario."""
    from pathlib import Path
    s = str(p)
    home = str(Path.home())
    return "~" + s[len(home):] if s.lower().startswith(home.lower()) else s


def gb(b: float) -> str:
    return f"{b / GiB:.1f}"


# ---------------------------------------------------------------------------
# Barras de uso
# ---------------------------------------------------------------------------

def level_style(ratio: float) -> str:
    return "ok" if ratio <= 0.80 else ("mid" if ratio <= 1.0 else "bad")


def bar(ratio: float, width: int = 24) -> Text:
    r = max(0.0, ratio)
    filled = min(width, int(round(min(r, 1.0) * width)))
    t = Text()
    t.append("█" * filled, style=level_style(r))
    t.append("░" * (width - filled), style="dim")
    return t


def mini_bar(ratio: float, width: int = 8) -> str:
    filled = min(width, int(round(max(0.0, min(ratio, 1.0)) * width)))
    return "▰" * filled + "▱" * (width - filled)


# ---------------------------------------------------------------------------
# Banner (estilo Gemini CLI: logo con degradado + consejos)
# ---------------------------------------------------------------------------

LOGO = ["█▀█ █ █ █▀▀ █▀█ █▀▀ █▄ █ █▀▀ █ ▄▀█",
        "▀▀█ █▄█ ██▄ █▀▄ ██▄ █ ▀█ █▄▄ █ █▀█"]
NAME, TAGLINE = "Querencia", "tu IA, en casa"


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def gradient(text: str, c1: str, c2: str, bold: bool = True) -> Text:
    a, b = _hex(c1), _hex(c2)
    n = max(len(text) - 1, 1)
    t = Text()
    for i, ch in enumerate(text):
        r, g, bl = (int(a[k] + (b[k] - a[k]) * i / n) for k in range(3))
        t.append(ch, style=f"{'bold ' if bold else ''}#{r:02x}{g:02x}{bl:02x}")
    return t


def banner(version: str, status: str, tips: list[str]):
    console.print()
    for ln in LOGO:
        console.print(gradient(ln, current.accent, current.user))
    console.print(Text(f"{TAGLINE} · v{version}", style="dim"))
    console.print()
    console.print("Consejos para empezar:", style="bold")
    for i, tip in enumerate(tips, 1):
        console.print(Text.assemble((f"{i}. ", "dim"), Text.from_markup(tip)))
    console.print()
    console.print(Text.from_markup(status))
    console.print()


def user_echo(text: str):
    """Mensaje del usuario como bloque gris con '>' (como Gemini CLI)."""
    lines = text.split("\n")
    t = Text()
    t.append("> ", style="user")
    t.append(lines[0][:2000])
    for ln in lines[1:12]:
        t.append("\n  " + ln[:2000])
    if len(lines) > 12:
        t.append(f"\n  … (+{len(lines) - 12} líneas)", style="dim")
    console.print(Padding(t, (0, 1), style=f"on {current.menu_bg}", expand=True))


TOOL_ICON = {"ok": ("✓", "ok"), "err": ("✗", "bad"), "run": ("◌", "accent"), "info": ("●", "info"),
             "warn": ("!", "mid")}


def tool(status: str, name: str, target: str = "", detail: str = ""):
    """Caja de 'herramienta' (leer archivo, transcribir, crear archivo…) como en Gemini CLI."""
    icon, style = TOOL_ICON.get(status, TOOL_ICON["info"])
    t = Text()
    t.append(f"{icon} ", style=style)
    t.append(name, style="bold")
    if target:
        t.append(f"  {target}", style="")
    if detail:
        t.append("\n  ")
        t.append(detail, style="dim")
    console.print(Panel(t, border_style="border" if status != "err" else "bad", padding=(0, 1), expand=True))


def select_style_hint() -> str:
    return "[dim]↑/↓ elegir · Enter confirmar · Esc cancelar[/]"


# ---------------------------------------------------------------------------
# Informe de memoria
# ---------------------------------------------------------------------------

def mem_report(est: Estimate, sysmem: SysMem, vram_budget: int, ram_budget: int, *, title: str,
               real: RealUsage | None = None, reclaim: int = 0):
    console.print(Text(title, style="accent"))
    tb = Table(border_style="border", show_edge=False, pad_edge=False, box=None, padding=(0, 2))
    tb.add_column("Componente", style="dim")
    tb.add_column("VRAM", justify="right")
    tb.add_column("RAM", justify="right")

    def mb(b):
        return "—" if not b else (f"{b / GiB:.2f} GB" if b >= GiB else f"{b / MiB:.0f} MB")

    n_on = min(max(est.ngl - 1, 0), est.n_layer)
    tb.add_row(f"Pesos ({n_on}/{est.n_layer} capas + salida en GPU)" if est.ngl > 0 else "Pesos (todo en CPU)",
               mb(est.weights_gpu), mb(est.weights_cpu))
    tb.add_row(f"Caché KV ({human_tokens(est.n_ctx)} ctx, {est.kv_type})", mb(est.kv_gpu), mb(est.kv_cpu))
    if est.rs_gpu or est.rs_cpu:
        tb.add_row("Estado recurrente (capas SSM)", mb(est.rs_gpu), mb(est.rs_cpu))
    if est.mmproj_gpu:
        tb.add_row("mmproj (codificador de visión)", mb(est.mmproj_gpu), "—")
    tb.add_row("Buffers de cómputo", mb(est.compute_gpu), mb(est.compute_cpu))
    tb.add_row("Contexto GPU / proceso", mb(est.overhead_gpu), mb(est.overhead_cpu))
    if est.calib != 1.0:
        tb.add_row(f"Calibración (×{est.calib:.2f}, medido antes)", "", "")
    tb.add_row(Text("TOTAL estimado", style="bold"), Text(mb(est.vram), style="bold"), Text(mb(est.ram), style="bold"))
    console.print(tb)

    vr = est.vram / vram_budget if vram_budget else 9.9
    rr = est.ram / ram_budget if ram_budget else 9.9
    line = Text("  VRAM ")
    line.append_text(bar(vr))
    line.append(f" {gb(est.vram)} / {gb(vram_budget)} GB disponibles ({vr:.0%})", style=level_style(vr))
    console.print(line)
    line = Text("  RAM  ")
    line.append_text(bar(rr))
    line.append(f" {gb(est.ram)} / {gb(ram_budget)} GB disponibles ({rr:.0%})", style=level_style(rr))
    console.print(line)
    if not sysmem.vram_total:
        console.print(f"  [dim]Sin GPU dedicada detectada: todo irá a la RAM · {sysmem.source}[/]")
    elif sysmem.free_estimated:
        console.print(f"  [dim]GPU {sysmem.gpu_name or '?'} · {gb(sysmem.vram_total)} GB · VRAM libre estimada (no se pudo "
                      f"medir lo que usan otros programas) · margen de seguridad incluido · fuente {sysmem.source}[/]")
    else:
        others = sysmem.vram_used - reclaim
        console.print(f"  [dim]GPU {sysmem.gpu_name or '?'} · {gb(sysmem.vram_total)} GB · otros procesos usan "
                      f"{gb(max(others, 0))} GB · margen de seguridad incluido · fuente {sysmem.source}[/]")
    for n in est.notes:
        console.print(f"  [dim]· {n}[/]")
    if real is not None and (real.gpu or real.cpu):
        est_buf = est.vram - est.overhead_gpu
        dev = (real.gpu - est_buf) / est_buf if est_buf else 0
        console.print(f"  [dim]Real (buffers de llama.cpp): GPU {mb(real.gpu)} · CPU {mb(real.cpu)} "
                      f"(mapeado con mmap) · estimado sin overhead {mb(est_buf)} → desviación {dev:+.0%}[/]")


# ---------------------------------------------------------------------------
# Vista de streaming
# ---------------------------------------------------------------------------

# etiquetas que no se muestran: partes del relevo y herramientas del modo proyecto (<leer/>, <grep>…)
_PART_TAG = re.compile(r"</?parte\b[^>]*>[ \t]*\n?|<(?:leer|listar|borrar)\b[^>]*/>[ \t]*\n?|<leer>[^<\n]*</leer>"
                       r"|<grep>.*?</grep>[ \t]*\n?|</?editar\b[^>]*>[ \t]*\n?", re.I | re.S)
_EDIT_OPEN = re.compile(r'<editar\s+(?:ruta|path|archivo|file)\s*=\s*["\']([^"\']+)["\']\s*>', re.I)
_PAIR_WRAP = re.compile(r"```[^\n]*\n(\s*<{5,9}\s*(?:BUSCAR|SEARCH).*?>{5,9}[^\n]*)\n\s*```", re.S)
_PAIR = re.compile(r"<{5,9}\s*(?:BUSCAR|SEARCH)[^\n]*\n(.*?)\n?={5,9}[^\n]*\n(.*?)\n?>{5,9}\s*(?:REEMPLAZAR|REPLACE)[^\n]*",
                   re.S)


def pretty(text: str) -> str:
    """Texto para la consola: ediciones BUSCAR/REEMPLAZAR como diff y sin etiquetas internas."""
    if "<<<<<" in text or "<editar" in text:
        text = _EDIT_OPEN.sub(lambda m: f"\n**✎ {m.group(1)}**\n", text)
        text = _PAIR_WRAP.sub(lambda m: m.group(1), text)
        text = _PAIR.sub(lambda m: "```diff\n" + "\n".join("- " + l for l in m.group(1).split("\n"))
                         + "\n" + "\n".join("+ " + l for l in m.group(2).split("\n")) + "\n```", text)
    return _PART_TAG.sub("", text)

PHRASES = ["Pensando", "Ordenando ideas", "Consultando a las neuronas", "Leyendo entre líneas",
           "Atando cabos", "Calentando la GPU", "Buscando las palabras justas", "Repasando el contexto",
           "Afinando la respuesta", "Conectando puntos"]


class StreamView:
    """
    Renderiza la respuesta en vivo:
    - los bloques de markdown terminados (separados por línea en blanco fuera de
      bloques de código) se imprimen de forma permanente con resaltado y el
      primero lleva el marcador ✦;
    - el bloque en curso y la línea de estado viven en un Live transitorio;
    - tool=True: modo "herramienta" (p. ej. el lector visual): el texto se ve
      atenuado mientras se genera y no se imprime al terminar.
    """

    def __init__(self, model_label: str, show_thinking: bool, *, header: bool = True, tool: bool = False,
                 tool_title: str = ""):
        self.model_label = model_label
        self.show_thinking = show_thinking
        self.answer = ""
        self.printed = 0
        self.think = ""
        self.think_done = False
        self.relay = ""
        self.t0 = time.time()
        self.t_first: float | None = None
        self.tokens = 0
        self.phase = ""
        self.stopping = False
        self.tool = tool
        self.tool_title = tool_title
        self._prefixed = not header
        self._phrase_i = int(self.t0) % len(PHRASES)
        self.spinner = Spinner("dots", style="accent")
        self.live = Live(console=console, get_renderable=self._render, refresh_per_second=12,
                         transient=True, redirect_stdout=False, redirect_stderr=False)

    # -- ciclo de vida --
    def __enter__(self):
        self.live.start()
        return self

    def __exit__(self, *exc):
        self.live.stop()

    def set_phase(self, text: str):
        self.phase = text

    # -- entrada --
    def feed(self, kind: str, text: str):
        if self.t_first is None:
            self.t_first = time.time()
        self.tokens += 1
        if kind == "think":
            self.think += text
            self.phase = "Razonando"
            return
        if kind == "relay":
            self.relay += text
            self.phase = "Preparando el relevo"
            return
        if self.think and not self.think_done and not self.tool:
            self._finish_think()
        if not self.answer and not text.strip():
            return
        self.answer += text
        self.phase = "Escribiendo"
        if not self.tool:
            self._flush_stable()

    def finish(self, footer: str = ""):
        if self.tool:
            self.live.stop()
            return
        if self.think and not self.think_done:
            self._finish_think()
        rest = self.answer[self.printed:]
        if rest.strip():
            self._print_md(rest)
        self.printed = len(self.answer)
        self.live.stop()
        if footer:
            console.print(Padding(Text(footer, style="dim"), (0, 0, 0, 2)))
        console.print()

    # -- internos --
    def _print_md(self, text: str):
        text = pretty(text)
        if not text.strip():
            return
        grid = Table.grid(padding=0)
        grid.add_column(width=2, no_wrap=True)
        grid.add_column(ratio=1)
        mark = Text("✦ ", style="accent") if not self._prefixed else Text("  ")
        self._prefixed = True
        grid.add_row(mark, md(text))
        console.print(grid)

    def _finish_think(self):
        self.think_done = True
        n_words = len(self.think.split())
        if self.show_thinking:
            body = Text(self.think.strip(), style="think")
            console.print(Padding(Panel(body, title="💭 razonamiento", title_align="left",
                                        border_style="dim", padding=(0, 1)), (0, 0, 0, 2)))
        else:
            console.print(Padding(Text(f"💭 razonó {n_words} palabras · /think last para verlo · "
                                       f"/think show para desplegarlo siempre", style="think"), (0, 0, 0, 2)))

    def _flush_stable(self):
        pending = self.answer[self.printed:]
        in_fence = in_pair = False
        cut = 0
        offset = 0
        lines = pending.split("\n")
        for ln in lines[:-1]:          # la última línea puede estar incompleta
            s = ln.strip()
            offset += len(ln) + 1
            if s.startswith("<<<<<"):  # edición BUSCAR/REEMPLAZAR: se imprime entera (como diff)
                in_pair = True
            elif s.startswith(">>>>>"):
                in_pair = False
                if not in_fence:
                    cut = offset
            elif s.startswith(("```", "~~~")):
                in_fence = not in_fence
                if not in_fence and not in_pair:
                    cut = offset
            elif not in_fence and not in_pair and s == "" and offset > 1:
                cut = offset
        if cut and pending[:cut].strip():
            self._print_md(pending[:cut])
            self.printed += cut
        elif cut:
            self.printed += cut

    def _status(self) -> Text:
        el = time.time() - self.t0
        t = Text()
        if self.stopping:
            t.append(" Cancelando… (termina el paso actual)", style="warning")
            return t
        if self.phase:
            label = self.phase
        else:
            label = PHRASES[(self._phrase_i + int(el // 4)) % len(PHRASES)]
        t.append(f" {label}…", style="accent")
        info = " (esc para cancelar" + (" · t ver razonamiento" if self.think and not self.think_done else "")
        info += f", {el:.0f}s"
        if self.t_first and self.tokens > 1:
            dt = time.time() - self.t_first
            if dt > 0.3:
                info += f" · {self.tokens / dt:.1f} t/s"
        if self.think and not self.think_done and not self.show_thinking:
            info += f" · 💭 {len(self.think.split())} palabras"
        t.append(info + ")", style="dim")
        return t

    def _render(self):
        parts = []
        h = max(6, min(14, console.size.height - 6))     # área en vivo pequeña: al borrarse no deja huecos
        w = max(20, console.size.width - 4)
        if self.tool:
            if self.tool_title:
                parts.append(Text.assemble(("◌ ", "accent"), (self.tool_title, "bold")))
            tail = self.answer.splitlines()[-6:]
            if tail:
                parts.append(Padding(Text("\n".join(l[:w] for l in tail), style="dim"), (0, 0, 0, 2)))
        else:
            if self.think and not self.think_done and self.show_thinking:
                lines = self.think.splitlines()[-min(10, h):]
                parts.append(Padding(Text("\n".join(lines), style="think"), (0, 0, 0, 2)))
            tail = pretty(self.answer[self.printed:])
            if tail.strip():
                n_rows = sum(max(1, math.ceil(len(l) / w)) for l in tail.split("\n"))
                if n_rows > h:
                    last = tail.split("\n")[-h:]
                    parts.append(Padding(Text("…\n" + "\n".join(last), style="dim"), (0, 0, 0, 2)))
                else:
                    grid = Table.grid(padding=0)
                    grid.add_column(width=2, no_wrap=True)
                    grid.add_column(ratio=1)
                    grid.add_row(Text("✦ ", style="accent") if not self._prefixed else Text("  "), md(tail))
                    parts.append(grid)
        row = Table.grid()
        row.add_row(self.spinner, self._status())
        parts.append(row)
        return Group(*parts)
