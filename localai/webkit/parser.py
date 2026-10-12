"""Parser del formato .lai (etiquetas tipo HTML) y Markdown mínimo para los textos."""

from __future__ import annotations

import html
import re
import textwrap
from dataclasses import dataclass, field
from html.parser import HTMLParser

ITEM_TAGS = {"item", "row", "task", "link"}
INLINE = {"b": "**", "strong": "**", "i": "*", "em": "*", "code": "`"}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    text: str = ""

    def a(self, name: str, default: str = "") -> str:
        v = self.attrs.get(name)
        return default if v is None or v == "" else v

    def yes(self, name: str, default: bool = False) -> bool:
        v = self.attrs.get(name)
        if v is None:
            return default
        return v.strip().lower() in ("", "yes", "si", "sí", "true", "1", "on")

    @property
    def items(self) -> list["Node"]:
        return [c for c in self.children if c.tag in ITEM_TAGS]


class _P(HTMLParser):
    def __init__(self, components: set[str]):
        super().__init__(convert_charrefs=True)
        self.components = components | {"page", "raw"}
        self.top: list[Node] = []
        self.comp: Node | None = None
        self.item: Node | None = None
        self.raw_depth = 0
        self.warnings: list[str] = []
        self._link_href: str | None = None

    # --- utilidades ---
    def _target(self) -> Node | None:
        return self.item or self.comp

    def _append_text(self, s: str):
        t = self._target()
        if t is not None:
            t.text += s

    def _close_comp(self):
        self.item = None
        self.comp = None
        self.raw_depth = 0

    # --- eventos ---
    def handle_starttag(self, tag, attrs):
        if self.comp is not None and self.comp.tag == "raw":
            self.comp.text += self.get_starttag_text()
            self.raw_depth += 1 if tag == "raw" else 0
            return
        ad = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if tag in self.components:
            self._close_comp()
            node = Node(tag, ad)
            self.top.append(node)
            self.comp = node
            return
        if self.comp is None:
            self.warnings.append(f"etiqueta <{tag}> fuera de un componente: ignorada")
            return
        if tag in ITEM_TAGS:
            self.item = Node(tag, ad)
            self.comp.children.append(self.item)
            return
        if tag in INLINE:
            self._append_text(INLINE[tag])
        elif tag == "br":
            self._append_text("\n")
        elif tag == "a":
            self._link_href = ad.get("href", "#")
            self._append_text("[")
        elif tag in ("p", "li"):
            self._append_text("\n\n" if tag == "p" else "\n- ")
        else:
            self.warnings.append(f"<{tag}> dentro de <{self.comp.tag}>: se ignora la etiqueta (se conserva el texto)")

    def handle_startendtag(self, tag, attrs):
        if self.comp is not None and self.comp.tag == "raw":
            self.comp.text += self.get_starttag_text()
            return
        ad = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if tag in self.components:
            self._close_comp()
            self.top.append(Node(tag, ad))
            return
        if self.comp is not None and tag in ITEM_TAGS:
            self.comp.children.append(Node(tag, ad))
            return
        if tag == "br":
            self._append_text("\n")

    def handle_endtag(self, tag):
        if self.comp is not None and self.comp.tag == "raw":
            if tag == "raw" and self.raw_depth == 0:
                self._close_comp()
            else:
                if tag == "raw":
                    self.raw_depth -= 1
                self.comp.text += f"</{tag}>"
            return
        if tag in ITEM_TAGS and self.item is not None:
            self.item = None
        elif tag in self.components and self.comp is not None and tag == self.comp.tag:
            self._close_comp()
        elif tag in INLINE:
            self._append_text(INLINE[tag])
        elif tag == "a" and self._link_href is not None:
            self._append_text(f"]({self._link_href})")
            self._link_href = None

    def handle_data(self, data):
        if self.comp is not None and self.comp.tag == "raw":
            self.comp.text += data
            return
        self._append_text(data)


_META = ("title", "brand", "theme", "accent", "font", "lang", "description", "currency")
_TEXT_ATTRS = ("text", "description", "desc", "content", "body")


def _normalize(src: str) -> str:
    """Errores típicos de los modelos pequeños que se pueden corregir sin adivinar."""
    src = re.sub(r"(?m)^(\s*)=(item|row|task|link)\b", r"\1<\2", src)            # «=item …» → «<item …»
    src = re.sub(r"</?(?:pages|html|body|main)\b[^>]*>", "", src, flags=re.I)      # envoltorios
    if not re.search(r"<page\b", src, re.I):                                       # <title>X</title> sueltos → <page/>
        meta = {}
        for tag in _META:
            m = re.search(rf"<{tag}>\s*([^<]*?)\s*</{tag}>", src, re.I)
            if m:
                meta[tag] = m.group(1)
                src = src[:m.start()] + src[m.end():]
        if meta:
            attrs = " ".join(f'{k}="{html.escape(v, quote=True)}"' for k, v in meta.items())
            src = f"<page {attrs}/>\n" + src
    return src


def _repair(nodes: list[Node]) -> list[Node]:
    out = []
    for i, n in enumerate(nodes):
        nxt = nodes[i + 1] if i + 1 < len(nodes) else None
        # <section id title> envolviendo otro componente: la sección queda vacía → sus datos pasan al de dentro
        if (n.tag == "section" and not n.text.strip() and not n.children and nxt is not None
                and nxt.tag not in ("section", "page", "raw")):
            for k in ("id", "nav", "title", "subtitle", "eyebrow", "alt", "currency"):
                if n.attrs.get(k) and not nxt.attrs.get(k):
                    nxt.attrs[k] = n.attrs[k]
            continue
        for c in n.children:      # texto puesto en un atributo (description="…", text="…")
            if not c.text:
                c.text = next((c.attrs[k] for k in _TEXT_ATTRS if c.attrs.get(k)), "")
        out.append(n)
    return out


def parse(source: str, components: set[str]) -> tuple[list[Node], list[str]]:
    src = re.sub(r"^\s*```[\w.-]*.*$", "", source, flags=re.M)      # por si viene con vallas de código
    p = _P(components)
    p.feed(_normalize(src))
    p.close()
    for n in p.top:
        n.text = textwrap.dedent(n.text).strip("\n")
        for c in n.children:
            c.text = textwrap.dedent(c.text).strip()
    return _repair(p.top), p.warnings


# ---------------------------------------------------------------------------
# Markdown mínimo (seguro: se escapa todo y solo se generan etiquetas propias)
# ---------------------------------------------------------------------------

def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)

    def link(m):
        url = m.group(2).strip()
        if re.match(r"^(javascript|data|vbscript):", url, re.I):
            url = "#"
        return f'<a href="{html.escape(url, quote=True)}">{m.group(1)}</a>'

    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, s)
    return s.replace("\n", "<br>")


def md(text: str) -> str:
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l.rstrip() for l in block.strip().split("\n") if l.strip()]
        if not lines:
            continue
        if all(re.match(r"^\s*[-*•]\s+", l) for l in lines):
            out.append("<ul>" + "".join(f"<li>{inline(re.sub(r'^\s*[-*•]\s+', '', l))}</li>" for l in lines) + "</ul>")
        elif all(re.match(r"^\s*\d+[.)]\s+", l) for l in lines):
            out.append("<ol>" + "".join(f"<li>{inline(re.sub(r'^\s*\d+[.)]\s+', '', l))}</li>" for l in lines) + "</ol>")
        elif lines[0].startswith("#"):
            level = min(4, len(lines[0]) - len(lines[0].lstrip("#")) + 1)
            out.append(f"<h{level}>{inline(lines[0].lstrip('#').strip())}</h{level}>")
            if len(lines) > 1:
                out.append(f"<p>{inline(chr(10).join(lines[1:]))}</p>")
        else:
            out.append(f"<p>{inline(chr(10).join(lines))}</p>")
    return "\n".join(out)
