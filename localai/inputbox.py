"""
Entrada estilo Claude Code / Gemini CLI con prompt_toolkit:
- caja con borde redondeado, multilínea (Alt+Enter, Shift+Enter, Ctrl+J o "\\" + Enter)
- menú emergente al escribir "/" con descripción de cada comando
- autocompletado de rutas tras "@" y en /file, /pdf, /image, /export
- historial (↑/↓) y búsqueda inversa (Ctrl+R), sugerencia gris desde el historial (→)
- barra inferior fija con modelo, contexto, VRAM/RAM, adjuntos y atajos
- selector con flechas reutilizable (/model, /resume, /theme…)
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from prompt_toolkit.application import Application
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_completions, is_searching
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (ConditionalContainer, Float, FloatContainer, HSplit, Layout, VSplit,
                                   Window, WindowAlign)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import (AppendAutoSuggestion, Processor, Transformation,
                                              TransformationInput)
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import SearchToolbar

from .i18n import T
from .render import ThemeDef

# E/S por defecto (las pruebas automáticas inyectan aquí una entrada simulada)
DEFAULT_IO: dict[str, Any] = {}


def enable_shift_enter():
    """
    En Windows 11 prompt_toolkit lee la consola en modo VT, donde Shift+Enter llega
    como un Enter normal. El lector clásico (eventos de consola) sí trae los
    modificadores: lo forzamos y convertimos Shift+Enter en Alt+Enter (nueva línea).
    LOCALAI_VT_INPUT=1 desactiva este ajuste.
    """
    if sys.platform != "win32" or os.environ.get("LOCALAI_VT_INPUT") == "1":
        return
    try:
        import prompt_toolkit.input.win32 as w32
        from prompt_toolkit.key_binding.key_processor import KeyPress
        from prompt_toolkit.keys import Keys

        w32._is_win_vt100_input_enabled = lambda: False
        orig = w32.ConsoleInputReader._event_to_key_presses
        if getattr(orig, "_localai", False):
            return

        def patched(self, ev):
            res = orig(self, ev)
            if (ev.ControlKeyState & self.SHIFT_PRESSED and len(res) == 1
                    and res[0].key in (Keys.ControlM, Keys.ControlJ)):
                return [KeyPress(Keys.Escape, ""), KeyPress(Keys.ControlM, "\r")]
            return res

        patched._localai = True
        w32.ConsoleInputReader._event_to_key_presses = patched
    except Exception:
        pass


def refresh_console_handle(output):
    """
    prompt_toolkit guarda el handle de la consola al crearse. Si alguna librería
    redirige stdout con dup2 (en Windows eso cierra el handle original), hay que
    volver a pedirlo o el siguiente dibujado falla con NoConsoleScreenBufferError.
    """
    if sys.platform != "win32" or output is None:
        return
    try:
        from ctypes import windll
        from ctypes.wintypes import HANDLE
        from prompt_toolkit.win32_types import STD_OUTPUT_HANDLE
        h = HANDLE(windll.kernel32.GetStdHandle(STD_OUTPUT_HANDLE))
        for o in (output, getattr(output, "win32_output", None)):
            if o is None:
                continue
            if hasattr(o, "hconsole"):
                o.hconsole = h
            if hasattr(o, "_hconsole"):
                o._hconsole = h
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Autocompletado
# ---------------------------------------------------------------------------

@dataclass
class CmdSpec:
    name: str
    desc: str
    args: str = ""                         # texto de ayuda de argumentos
    choices: Callable[[], Iterable[tuple[str, str]]] | None = None   # (valor, meta)
    paths: bool = False
    aliases: tuple[str, ...] = ()
    hidden: bool = False


class LocalCompleter(Completer):
    def __init__(self, commands: dict[str, CmdSpec]):
        self.commands = commands
        self.paths = PathCompleter(expanduser=True)

    def _path(self, text: str, complete_event):
        sub = Document(text, len(text))
        yield from self.paths.get_completions(sub, complete_event)

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        # @ruta en cualquier parte del mensaje
        word = text.split(" ")[-1] if not text.endswith(" ") else ""
        if word.startswith("@") and "\n" not in word:
            yield from self._path(word[1:].strip('"'), complete_event)
            return
        if not text.startswith("/") or "\n" in text:
            return
        if " " not in text:
            q = text.lower()
            seen = set()
            ranked = sorted(self.commands.values(), key=lambda c: c.name)
            for c in ranked:
                if c.hidden or c.name in seen:
                    continue
                if c.name.startswith(q):
                    seen.add(c.name)
                    yield Completion(c.name, -len(text), display=f"{c.name} {c.args}".strip(), display_meta=c.desc)
            for c in ranked:   # coincidencias por contenido (p. ej. "/ctx" -> "/compact"? no; "/mem")
                if c.hidden or c.name in seen:
                    continue
                if len(q) > 2 and q[1:] in c.name:
                    seen.add(c.name)
                    yield Completion(c.name, -len(text), display=f"{c.name} {c.args}".strip(), display_meta=c.desc)
            return
        cmd, _, rest = text.partition(" ")
        spec = self.commands.get(cmd.lower())
        if spec is None:
            return
        if spec.choices:
            # subcomando de 2º nivel (p. ej. "/chat load <nombre>"); en comandos con rutas, antes que las rutas
            q = rest.lower()
            for value, meta in spec.choices():
                if value.lower().startswith(q) or (q and q in value.lower() and len(q) > 1):
                    yield Completion(value, -len(rest), display_meta=meta)
        if spec.paths:
            last = rest.split(" ")[-1] if rest.count(" ") else rest
            yield from self._path(last.strip('"'), complete_event)


class InputLexer(Lexer):
    """Colorea /comandos, @rutas y !shell en la caja de entrada."""

    def lex_document(self, document: Document):
        lines = document.lines

        def get_line(i: int) -> StyleAndTextTuples:
            line = lines[i]
            if i == 0 and line.startswith("/"):
                cmd, sp, rest = line.partition(" ")
                return [("class:cmd", cmd), ("", sp + rest)]
            if i == 0 and line.startswith("!"):
                return [("class:shell", line)]
            out: StyleAndTextTuples = []
            for j, tok in enumerate(line.split(" ")):
                if j:
                    out.append(("", " "))
                out.append(("class:mention" if tok.startswith("@") and len(tok) > 1 else "", tok))
            return out
        return get_line


class Placeholder(Processor):
    def __init__(self, text: str | Callable[[], str]):
        self.text = text                       # texto fijo o función (cambia con el idioma)

    def apply_transformation(self, ti: TransformationInput) -> Transformation:
        if ti.document.text == "" and ti.lineno == 0:
            text = self.text() if callable(self.text) else self.text
            return Transformation(fragments=[("class:placeholder", text)])
        return Transformation(fragments=ti.fragments)


def _style(t: ThemeDef) -> Style:
    return Style.from_dict({
        "frame": t.frame, "frame.active": t.accent, "prompt": f"bold {t.accent}",
        "cmd": f"bold {t.accent}", "mention": f"underline {t.user}", "shell": t.warn,
        "placeholder": f"italic {t.dim}",
        "toolbar": t.bar_fg, "toolbar.dim": t.dim, "toolbar.user": t.user,
        "toolbar.accent": f"bold {t.accent}", "toolbar.ok": t.ok,
        "toolbar.mid": t.warn, "toolbar.bad": t.err, "toolbar.notice": f"bold {t.warn}",
        "keys.key": f"bold {t.accent}", "keys.desc": t.dim,
        "completion-menu": f"bg:{t.menu_bg} {t.bar_fg}",
        "completion-menu.completion": f"bg:{t.menu_bg} {t.bar_fg}",
        "completion-menu.completion.current": f"bg:{t.menu_sel} bold {t.accent}",
        "completion-menu.meta.completion": f"bg:{t.menu_bg} {t.dim}",
        "completion-menu.meta.completion.current": f"bg:{t.menu_sel} {t.bar_fg}",
        "scrollbar.background": f"bg:{t.menu_bg}", "scrollbar.button": f"bg:{t.dim}",
        "auto-suggestion": t.dim, "search": f"bg:{t.menu_sel}", "search-toolbar": f"bold {t.warn}",
        "sel": f"bg:{t.menu_sel} bold {t.accent}", "sel.meta": f"bg:{t.menu_sel} {t.bar_fg}",
        "item": "", "item.meta": t.dim, "title": f"bold {t.accent}", "hint": f"italic {t.dim}",
    })


def _rounded(body, active: Callable[[], bool]):
    def st():
        return "class:frame.active" if active() else "class:frame"

    def corner(ch):
        return Window(FormattedTextControl(lambda: [(st(), ch)]), width=1, height=1)

    top = VSplit([corner("╭"), Window(char="─", height=1, style=st), corner("╮")], height=1)
    bottom = VSplit([corner("╰"), Window(char="─", height=1, style=st), corner("╯")], height=1)
    mid = VSplit([Window(width=1, char="│", style=st), Window(width=1), body,
                  Window(width=1), Window(width=1, char="│", style=st)])
    return HSplit([top, mid, bottom])


# ---------------------------------------------------------------------------
# Caja de entrada
# ---------------------------------------------------------------------------

class InputBox:
    def __init__(self, commands: dict[str, CmdSpec], history_file: Path | None,
                 toolbar: Callable[[], tuple[StyleAndTextTuples, StyleAndTextTuples]], theme: ThemeDef,
                 on_copy: Callable[[], bool] | None = None, no_arg_cmds: set[str] | None = None,
                 input=None, output=None, lang: str = "es"):
        enable_shift_enter()
        self.lang = lang                       # es | en: textos de la caja y del panel de atajos
        self.commands = commands
        self.no_arg_cmds = no_arg_cmds or set()
        self.toolbar_fn = toolbar
        self.on_copy = on_copy
        self._notice = ("", 0.0)
        self._ctrl_c_at = 0.0
        self.show_keys = False
        try:
            hist = FileHistory(str(history_file)) if history_file else InMemoryHistory()
        except Exception:
            hist = InMemoryHistory()
        self.search = SearchToolbar(text_if_not_searching="", backward_search_prompt=T("búsqueda (Ctrl+R): ", "search (Ctrl+R): "),
                                    forward_search_prompt=T("búsqueda: ", "search: "))
        self.buffer = Buffer(multiline=True, history=hist, completer=LocalCompleter(commands),
                             complete_while_typing=True, auto_suggest=AutoSuggestFromHistory(),
                             enable_history_search=False)
        def _hide_keys(_):
            self.show_keys = False
        self.buffer.on_text_changed += _hide_keys
        control = BufferControl(
            buffer=self.buffer, search_buffer_control=self.search.control, lexer=InputLexer(),
            input_processors=[AppendAutoSuggestion(),
                              Placeholder(lambda: "Type your message or @path/to/file  ·  ? shortcuts"
                                          if self.lang == "en" else
                                          "Escribe tu mensaje o @ruta/al/archivo  ·  ? atajos")])

        def prefix(lineno, wrap_count):
            return [("class:prompt", "› ")] if lineno == 0 and wrap_count == 0 else [("", "  ")]

        self.input_window = Window(control, height=D(min=1, max=14), wrap_lines=True,
                                   get_line_prefix=prefix, dont_extend_height=True)
        status = Window(FormattedTextControl(self._status_line), style="class:toolbar", height=1)
        notice = ConditionalContainer(Window(FormattedTextControl(self._notice_text), height=1),
                                      filter=Condition(lambda: bool(self._notice_active())))
        keys = ConditionalContainer(Window(FormattedTextControl(self._keys), dont_extend_height=True),
                                    filter=Condition(lambda: self.show_keys))
        body = HSplit([_rounded(self.input_window, lambda: True), self.search, notice, status, keys])
        root = FloatContainer(content=body, floats=[
            Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=12, scroll_offset=1))])
        self.app: Application = Application(
            layout=Layout(root, focused_element=self.input_window), key_bindings=self._bindings(),
            style=_style(theme), erase_when_done=True, refresh_interval=1.0, mouse_support=False,
            full_screen=False, input=input or DEFAULT_IO.get("input"), output=output or DEFAULT_IO.get("output"))
        self.app.ttimeoutlen = 0.05
        self.app.timeoutlen = 0.4

    def set_theme(self, theme: ThemeDef):
        self.app.style = _style(theme)

    def notice(self, text: str, seconds: float = 3.0):
        self._notice = (text, time.monotonic() + seconds)
        try:
            self.app.invalidate()
        except Exception:
            pass

    def _notice_active(self) -> str:
        txt, until = self._notice
        return txt if txt and time.monotonic() < until else ""

    def _notice_text(self) -> StyleAndTextTuples:
        return [("class:toolbar.notice", f" {self._notice_active()}")]

    def _status_line(self) -> StyleAndTextTuples:
        """
        Izquierda y derecha en una línea. Si no cabe: primero se quita la VRAM de la
        derecha y después se recorta la izquierda (la carpeta). Mide en columnas reales
        (los emojis ocupan dos).
        """
        from prompt_toolkit.utils import get_cwidth
        left, right = self.toolbar_fn()
        try:
            width = self.app.output.get_size().columns
        except Exception:
            width = 100

        def w(frags):
            return sum(get_cwidth(t) for _, t in frags)

        if w(left) + w(right) + 1 > width:
            cut = next((i for i, (_, t) in enumerate(right) if "VRAM" in t), None)
            if cut is not None:
                right = right[:cut] + [("class:toolbar", " ")]

        def clip(frags, room):
            out, n = [], 0
            for st, t in frags:
                for ch in t:
                    cw = get_cwidth(ch)
                    if n + cw > room:
                        return out, n
                    out.append((st, ch))
                    n += cw
            return out, n

        right, rlen = clip(right, max(0, width - 2))
        room = width - rlen - 1
        if w(left) > room:
            left, n = clip(left, max(0, room - 1))
            left.append(("class:toolbar.dim", "…"))
            n += 1
        else:
            n = w(left)
        return list(left) + [("class:toolbar", " " * max(1, width - n - rlen))] + list(right)

    def _keys(self) -> StyleAndTextTuples:
        if self.lang == "en":
            rows = [("Enter", "send"), ("Alt/Shift+Enter, Ctrl+J, \\+Enter", "new line"),
                    ("/", "commands"), ("@path", "attach a file, image or PDF"),
                    ("!cmd / !!cmd", "shell / shell + output to context"), ("↑ ↓", "history"),
                    ("Ctrl+R", "search the history"), ("→", "accept the grey suggestion"),
                    ("Esc", "cancel the answer"), ("Ctrl+K", "copy the last answer"),
                    ("Ctrl+L", "clear the screen"), ("Ctrl+C ×2 / Ctrl+D", "quit")]
        else:
            rows = [("Enter", "enviar"), ("Alt/Shift+Enter, Ctrl+J, \\+Enter", "nueva línea"),
                    ("/", "comandos"), ("@ruta", "adjuntar archivo, imagen o PDF"),
                    ("!cmd / !!cmd", "shell / shell + salida al contexto"), ("↑ ↓", "historial"),
                    ("Ctrl+R", "buscar en el historial"), ("→", "aceptar sugerencia gris"),
                    ("Esc", "cancelar la respuesta"), ("Ctrl+K", "copiar la última respuesta"),
                    ("Ctrl+L", "limpiar pantalla"), ("Ctrl+C ×2 / Ctrl+D", "salir")]
        out: StyleAndTextTuples = [("", "\n")]
        half = (len(rows) + 1) // 2
        for i in range(half):
            for k, d in (rows[i], rows[i + half] if i + half < len(rows) else ("", "")):
                if k:
                    out += [("class:keys.key", f"  {k:<30}"), ("class:keys.desc", f"{d:<34}")]
            out.append(("", "\n"))
        return out

    def _bindings(self) -> KeyBindings:
        kb = KeyBindings()
        buf = self.buffer
        not_search = ~is_searching

        def submit(event):
            text = buf.text
            if text.strip():
                buf.append_to_history()
            event.app.exit(result=text)

        @kb.add("enter", filter=not_search)
        def _enter(event):
            cs = buf.complete_state
            if cs and cs.current_completion:
                buf.apply_completion(cs.current_completion)
                t = buf.text.strip()
                if t.startswith("/") and " " not in t and t.lower() in self.no_arg_cmds:
                    submit(event)
                return
            if buf.document.text_before_cursor.endswith("\\"):
                buf.delete_before_cursor(1)
                buf.insert_text("\n")
                return
            submit(event)

        @kb.add("escape", "enter", filter=not_search)
        @kb.add("c-j", filter=not_search)
        def _newline(event):
            buf.insert_text("\n")

        @kb.add("escape", filter=has_completions)
        def _close_menu(event):
            buf.cancel_completion()

        @kb.add("c-c")
        def _ctrl_c(event):
            if buf.text:
                buf.reset()
                return
            now = time.monotonic()
            if now - self._ctrl_c_at < 2.0:
                event.app.exit(result=None)
            else:
                self._ctrl_c_at = now
                self.notice(T("Pulsa Ctrl+C otra vez para salir (o escribe /exit)", "Press Ctrl+C again to quit (or type /exit)"), 2.0)

        @kb.add("c-d")
        def _ctrl_d(event):
            if not buf.text:
                event.app.exit(result=None)
            else:
                buf.delete()

        @kb.add("c-l")
        def _clear(event):
            event.app.renderer.clear()
            event.app.exit(result="/__clear_screen__")

        @kb.add("c-k")
        def _copy(event):
            if self.on_copy and self.on_copy():
                self.notice(T("✔ Última respuesta copiada al portapapeles", "✔ Last answer copied to the clipboard"))
            else:
                self.notice(T("No hay respuesta que copiar", "There is no answer to copy"))

        @kb.add("?", filter=Condition(lambda: not buf.text))
        def _keys(event):
            self.show_keys = not self.show_keys

        @kb.add("tab", filter=~has_completions)
        def _tab(event):
            buf.start_completion(select_first=True)

        return kb

    def prompt(self, default: str = "") -> str | None:
        self.buffer.reset(Document(default, len(default)))
        refresh_console_handle(self.app.output)
        return self.app.run()


# ---------------------------------------------------------------------------
# Selector con flechas
# ---------------------------------------------------------------------------

@dataclass
class Choice:
    value: Any
    label: str
    meta: str = ""
    disabled: bool = False


def select(title: str, choices: list[Choice], theme: ThemeDef, default: int = 0,
           hint: str | None = None, input=None, output=None) -> Any:
    hint = hint or T("↑/↓ mover · Enter elegir · Esc cancelar", "↑/↓ move · Enter choose · Esc cancel")
    if not choices:
        return None
    idx = [max(0, min(default, len(choices) - 1))]
    max_rows = 14
    width_label = max(len(c.label) for c in choices)

    def text():
        out: StyleAndTextTuples = [("class:title", f" {title}\n")]
        n = len(choices)
        start = min(max(0, idx[0] - max_rows // 2), max(0, n - max_rows))
        for i in range(start, min(n, start + max_rows)):
            c = choices[i]
            cur = i == idx[0]
            mark = "❯ " if cur else "  "
            st = "class:sel" if cur else ("class:item.meta" if c.disabled else "class:item")
            out.append((st, f" {mark}{c.label.ljust(width_label)} "))
            out.append(("class:sel.meta" if cur else "class:item.meta", f" {c.meta}"))
            out.append(("", "\n"))
        if n > max_rows:
            out.append(("class:hint", f"   ({idx[0] + 1}/{n})\n"))
        out.append(("class:hint", f" {hint}"))
        return out

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def _up(e):
        idx[0] = (idx[0] - 1) % len(choices)

    @kb.add("down")
    @kb.add("j")
    def _down(e):
        idx[0] = (idx[0] + 1) % len(choices)

    @kb.add("pageup")
    def _pu(e):
        idx[0] = max(0, idx[0] - max_rows)

    @kb.add("pagedown")
    def _pd(e):
        idx[0] = min(len(choices) - 1, idx[0] + max_rows)

    @kb.add("enter")
    def _ok(e):
        c = choices[idx[0]]
        if not c.disabled:
            e.app.exit(result=c.value)

    @kb.add("escape")
    @kb.add("c-c")
    @kb.add("q")
    def _cancel(e):
        e.app.exit(result=None)

    app = Application(layout=Layout(Window(FormattedTextControl(text, focusable=True, show_cursor=False),
                                           dont_extend_height=True)),
                      key_bindings=kb, style=_style(theme), erase_when_done=True, full_screen=False,
                      input=input or DEFAULT_IO.get("input"), output=output or DEFAULT_IO.get("output"))
    app.ttimeoutlen = 0.05
    return app.run()


def select_multi(title: str, choices: list[Choice], theme: ThemeDef, preselected: set | None = None,
                 hint: str | None = None, input=None, output=None) -> list | None:
    """Selector de casillas: devuelve la lista de valores marcados (None si se cancela)."""
    hint = hint or T("↑/↓ mover · Espacio marcar · a todos · Enter confirmar · Esc cancelar",
                     "↑/↓ move · Space tick · a all · Enter confirm · Esc cancel")
    if not choices:
        return []
    idx = [0]
    marked = {i for i, c in enumerate(choices) if preselected and c.value in preselected}
    max_rows = 14
    width_label = max(len(c.label) for c in choices)

    def text():
        out: StyleAndTextTuples = [("class:title", f" {title}\n")]
        n = len(choices)
        start = min(max(0, idx[0] - max_rows // 2), max(0, n - max_rows))
        for i in range(start, min(n, start + max_rows)):
            c = choices[i]
            cur = i == idx[0]
            box = "◉" if i in marked else "○"
            st = "class:sel" if cur else "class:item"
            out.append((st, f" {'❯' if cur else ' '} {box} {c.label.ljust(width_label)} "))
            out.append(("class:sel.meta" if cur else "class:item.meta", f" {c.meta}"))
            out.append(("", "\n"))
        out.append(("class:hint", f" {hint}"))
        return out

    kb = KeyBindings()

    @kb.add("up")
    def _up(e):
        idx[0] = (idx[0] - 1) % len(choices)

    @kb.add("down")
    def _down(e):
        idx[0] = (idx[0] + 1) % len(choices)

    @kb.add("space")
    def _toggle(e):
        marked.symmetric_difference_update({idx[0]})

    @kb.add("a")
    def _all(e):
        if len(marked) == len(choices):
            marked.clear()
        else:
            marked.update(range(len(choices)))

    @kb.add("enter")
    def _ok(e):
        e.app.exit(result=[choices[i].value for i in sorted(marked)])

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(e):
        e.app.exit(result=None)

    app = Application(layout=Layout(Window(FormattedTextControl(text, focusable=True, show_cursor=False),
                                           dont_extend_height=True)),
                      key_bindings=kb, style=_style(theme), erase_when_done=True, full_screen=False,
                      input=input or DEFAULT_IO.get("input"), output=output or DEFAULT_IO.get("output"))
    app.ttimeoutlen = 0.05
    return app.run()
