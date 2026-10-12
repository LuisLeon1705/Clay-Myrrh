"""
Guardar en disco los archivos que propone el modelo.

El modelo solo produce texto. Para crear archivos se le pide (en el prompt de
sistema) que ponga la ruta en la cabecera del bloque de código:

    ```html index.html
    ...
    ```

También se reconocen ```html:index.html, ```html title="index.html" y una línea
justo antes del bloque con el nombre (**index.html**, `index.html`:, ### index.html),
que es lo que suelen hacer los modelos pequeños aunque no sigan la convención.
Nada se escribe sin confirmación y nunca fuera del directorio de trabajo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

FILE_INSTRUCTIONS = (
    "Cuando el usuario te pida crear, generar o modificar un archivo, escribe su contenido COMPLETO "
    "(nunca fragmentos ni «...») en un bloque de código cuya cabecera lleve el lenguaje y la ruta "
    "relativa, por ejemplo:\n```html index.html\n<!doctype html>...\n```\n"
    "Un bloque por archivo. El programa lo guarda automáticamente en disco, así que SÍ puedes crear "
    "archivos: nunca digas que no puedes. Para fragmentos de ejemplo que no son archivos, usa bloques sin ruta.\n"
    "IMPORTANTE: no tienes herramientas: no puedes ejecutar comandos, código ni llamadas a funciones "
    "(nada de tool_call, Bash, python, ni `cat <<EOF > archivo`). Haz tú mismo los cálculos, paso a paso, "
    "en el texto de la respuesta, y escribe los archivos directamente en bloques de código con su ruta."
)

EXT = {
    "html": "html", "htm": "html", "css": "css", "javascript": "js", "js": "js", "jsx": "jsx",
    "typescript": "ts", "ts": "ts", "tsx": "tsx", "python": "py", "py": "py", "json": "json",
    "markdown": "md", "md": "md", "svg": "svg", "xml": "xml", "yaml": "yaml", "yml": "yml",
    "toml": "toml", "sql": "sql", "bash": "sh", "sh": "sh", "shell": "sh", "powershell": "ps1",
    "ps1": "ps1", "bat": "bat", "cmd": "cmd", "batch": "bat", "c": "c", "cpp": "cpp", "c++": "cpp",
    "csharp": "cs", "cs": "cs", "java": "java", "go": "go", "rust": "rs", "rs": "rs", "php": "php",
    "ruby": "rb", "lua": "lua", "kotlin": "kt", "swift": "swift", "ini": "ini", "csv": "csv",
    "text": "txt", "txt": "txt", "dockerfile": "Dockerfile", "makefile": "Makefile", "vue": "vue",
}

_FENCE = re.compile(r"^(?P<ind>[ \t]{0,3})(?P<fence>`{3,}|~{3,})(?P<info>[^\n`]*)$")
_NAME = r"[\w.\-/\\]+\.[A-Za-z0-9]{1,8}|Dockerfile|Makefile"
_PRE = re.compile(rf"^\s*(?:#+\s*|[-*]\s*)?(?:\*\*|`|_)?(?:archivo|fichero|file)?\s*:?\s*(?:\*\*|`)?"
                  rf"(?P<name>{_NAME})(?:\*\*|`|_)?\s*:?\s*$", re.I)


@dataclass
class Block:
    lang: str
    code: str
    path: str | None      # ruta propuesta por el modelo (si la hay)
    index: int            # 1, 2, 3… en orden de aparición
    closed: bool = True   # False si la respuesta se cortó antes de cerrar el bloque

    @property
    def lines(self) -> int:
        return self.code.count("\n") + 1

    def default_name(self, stem: str = "respuesta") -> str:
        ext = EXT.get(self.lang.lower(), "txt")
        return ext if ext in ("Dockerfile", "Makefile") else f"{stem}.{ext}"


def _path_from_info(info: str) -> tuple[str, str | None]:
    info = info.strip()
    if not info:
        return "", None
    m = re.match(r'^([\w+#.\-]*)\s*[: ]\s*(?:title|file|filename|path)?\s*=?\s*["\']?(' + _NAME + r')["\']?\s*$', info)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^(" + _NAME + r")$", info)      # solo la ruta: ```index.html
    if m and "." in info and info.split(".")[-1].lower() in EXT.values():
        return info.split(".")[-1].lower(), info
    return info.split()[0], None


def extract_blocks(text: str) -> list[Block]:
    lines = text.split("\n")
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        m = _FENCE.match(lines[i])
        if not m:
            i += 1
            continue
        fence, info = m.group("fence"), m.group("info")
        lang, path = _path_from_info(info)
        body = []
        j = i + 1
        while j < len(lines):
            m2 = _FENCE.match(lines[j])
            if m2 and m2.group("fence")[0] == fence[0] and len(m2.group("fence")) >= len(fence) and not m2.group("info").strip():
                break
            body.append(lines[j])
            j += 1
        if path is None:   # ¿línea anterior con el nombre del archivo?
            k = i - 1
            while k >= 0 and not lines[k].strip():
                k -= 1
            if k >= 0 and i - k <= 2:
                pm = _PRE.match(lines[k])
                if pm:
                    path = pm.group("name")
        if body:
            # ruta en la PRIMERA línea del código: "js/app.js" (se quita: no es código válido) o en un
            # comentario "// js/app.js", "/* … */", "<!-- … -->", "# …" (se usa y se conserva)
            first = body[0].strip()
            bare = re.fullmatch(rf"`?({_NAME})`?:?", first)
            com = re.fullmatch(rf"(?://|#|/\*|<!--)\s*(?:archivo:?\s*)?({_NAME})\s*(?:\*/|-->)?", first, re.I)
            if bare and "." in bare.group(1):
                path = path or bare.group(1)
                body = body[1:]
            elif com and path is None:
                path = com.group(1)
        blocks.append(Block(lang, "\n".join(body), path, len(blocks) + 1, closed=j < len(lines)))
        i = j + 1
    named = {b.path for b in blocks if b.path}
    for h in extract_heredocs(text):
        if h.path in named:
            continue
        # el bloque de shell que contenía el heredoc deja de contar como bloque suelto
        blocks = [b for b in blocks if b.path or h.code[:120] not in b.code]
        h.index = len(blocks) + 1
        blocks.append(h)
        named.add(h.path)
    return blocks


# Modelos entrenados como "agentes" a veces escriben el archivo como un comando de shell
# (cat <<'EOF' > ruta … EOF), a menudo dentro de una llamada a herramienta inventada
# (Gemma 4: <|tool_call>call:Bash{command:<|"|>…<|"|>}<tool_call|>). Se rescata igualmente.
_HEREDOC = re.compile(r"""cat\s*<<-?\s*(['"]?)(?P<tag>[A-Za-z_]\w*)\1\s*>\s*['"]?(?P<path>[^\s'"<>|;&]+)['"]?[^\n]*\n""")
_STOPS = ('<|"|>', "<tool_call|>", "<|tool_call>", "\n```")


def extract_heredocs(text: str) -> list[Block]:
    out: list[Block] = []
    for m in _HEREDOC.finditer(text):
        rest = text[m.end():]
        end = re.search(rf"^\s*{re.escape(m.group('tag'))}\s*$", rest, re.M)
        closed = end is not None
        body = rest[:end.start()] if end else rest
        if not closed:   # sin terminador: cortar al final de la llamada o del bloque
            cuts = [i for i in (body.find(sm) for sm in _STOPS) if i >= 0]
            body = body[:min(cuts)] if cuts else body
        path = m.group("path")
        if path.lower().endswith((".html", ".htm")) and "</html>" in body.lower():
            i = body.lower().rfind("</html>")
            body, closed = body[:i + len("</html>")], True
        lang = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        out.append(Block(lang, body.rstrip("\n"), path, 0, closed=closed))
    return out


def names_from_request(blocks: list[Block], user_text: str):
    """
    Si el usuario nombró el archivo («crea index.html…») y el modelo devolvió un único
    bloque sin nombre de ese tipo, se le asigna ese nombre.
    """
    used = {b.path for b in blocks if b.path}
    for name in dict.fromkeys(re.findall(rf"(?<![\w@/\\.])({_NAME})", user_text)):
        if name in used or "." not in name:
            continue
        ext = name.rsplit(".", 1)[1].lower()
        cands = [b for b in blocks if not b.path and EXT.get(b.lang.lower()) == ext]
        if len(cands) == 1:
            cands[0].path = name
            used.add(name)
    # «hazme un html», «genera un css»…: tipo pedido sin nombre -> nombre por defecto
    low = user_text.lower()
    for word, default in (("html", "pagina.html"), ("css", "estilos.css"), ("json", "datos.json"),
                          ("svg", "imagen.svg"), ("markdown", "documento.md"), ("csv", "datos.csv")):
        if not re.search(rf"\b{word}\b", low):
            continue
        cands = [b for b in blocks if not b.path and EXT.get(b.lang.lower()) == EXT[word]]
        if len(cands) == 1 and default not in used:
            cands[0].path = default
            used.add(default)


def safe_target(path: str, cwd: Path) -> Path:
    """Ruta dentro del directorio de trabajo; error si se sale de él."""
    p = Path(path.strip().strip("'\"").replace("\\", "/")).expanduser()
    target = (p if p.is_absolute() else cwd / p).resolve()
    root = cwd.resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"'{path}' queda fuera del directorio de trabajo ({root}); usa /save <ruta> dentro de él.")
    if target.is_dir():
        raise ValueError(f"'{path}' es una carpeta.")
    return target


def write(target: Path, code: str) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    data = code if code.endswith("\n") else code + "\n"
    target.write_text(data, encoding="utf-8", newline="\n")
    return len(data.encode("utf-8"))
