"""
Modo proyecto: el modelo trabaja directamente sobre una carpeta del usuario, como un
proyecto de Claude Code.

- El prompt de sistema lleva la estructura de la carpeta y unas «herramientas» de texto
  que cualquier modelo local puede usar (no hace falta function calling):
      <leer ruta="src/app.js"/>   <leer ruta="x" desde="100" hasta="200"/>
      <listar ruta="src"/>        <grep>texto</grep>
      <editar ruta="x"> <<<<<<< BUSCAR … ======= … >>>>>>> REEMPLAZAR </editar>
      <borrar ruta="x"/>          y bloques ```lenguaje ruta``` para archivos nuevos
- Las lecturas y búsquedas se devuelven al modelo en el mensaje siguiente (bucle corto).
- Todo cambio guarda antes una copia: /deshacer revierte el turno completo y /cambios
  muestra qué se tocó. Nada se escribe fuera de la carpeta del proyecto.
"""

from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import APP_DIR

BACKUP_DIR = APP_DIR / "respaldos"
IGNORE_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build", ".next",
               ".nuxt", ".cache", ".idea", ".vscode", "target", ".pytest_cache", ".mypy_cache", ".ruff_cache",
               "coverage", ".gradle", "bin", "obj", ".zero", ".local_ai"}
BINARY_EXT = {".gguf", ".bin", ".safetensors", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".avif",
              ".pdf", ".zip", ".7z", ".rar", ".gz", ".tar", ".exe", ".dll", ".so", ".dylib", ".pyc", ".class",
              ".jar", ".mp3", ".mp4", ".wav", ".ogg", ".webm", ".mov", ".woff", ".woff2", ".ttf", ".otf", ".eot",
              ".sqlite", ".db", ".psd", ".blend", ".fbx", ".obj", ".stl", ".part"}
MAX_READ = 16000                       # caracteres por lectura (se puede pedir por rangos de líneas)

INSTRUCTIONS = """# Proyecto abierto: {name}
Trabajas DIRECTAMENTE sobre los archivos de la carpeta {root}. Lo que escribas se aplica al disco al terminar tu \
respuesta (con copia de seguridad: el usuario puede deshacerlo con /deshacer).

Estructura actual (sin carpetas de dependencias ni compilados):
{tree}

No puedes ejecutar comandos. Tus herramientas son estas etiquetas; el programa te responde en el mensaje siguiente:
- <leer ruta="src/app.js"/> lee un archivo · <leer ruta="x" desde="120" hasta="220"/> solo esas líneas.
- <listar ruta="src"/> lista una carpeta · <grep>texto</grep> busca un texto en todo el proyecto.
Si el contenido de un archivo ya aparece en la conversación (el programa adjunta los archivos pequeños nuevos o cambiados), úsalo directamente. Si no lo has visto, léelo antes de modificarlo; nunca inventes su contenido.
Cuando necesites leer, pide las lecturas y TERMINA tu respuesta: el resultado llegará en el siguiente mensaje.

Para MODIFICAR un archivo existente usa ediciones (gastan muchos menos tokens que reescribirlo):
<editar ruta="src/app.js">
<<<<<<< BUSCAR
líneas actuales copiadas EXACTAMENTE (con su sangría), las justas para que sean únicas
=======
líneas nuevas
>>>>>>> REEMPLAZAR
</editar>
Puedes poner varios pares BUSCAR/REEMPLAZAR en un <editar>. Con BUSCAR vacío, el texto se añade al final.
Para CREAR un archivo (o reescribir uno pequeño entero) usa un bloque de código con la ruta: ```js src/nuevo.js
Escribe siempre el archivo COMPLETO en esos bloques: nunca «...», «resto igual» ni fragmentos.
Para borrar un archivo: <borrar ruta="x"/>. Rutas siempre relativas a la carpeta del proyecto."""


# ---------------------------------------------------------------------------
# Acciones que escribe el modelo
# ---------------------------------------------------------------------------

_ATTR = r'(?:ruta|path|archivo|file)\s*=\s*["\']([^"\']+)["\']'


@dataclass
class Actions:
    reads: list[tuple[str, int | None, int | None]] = field(default_factory=list)
    lists: list[str] = field(default_factory=list)
    greps: list[str] = field(default_factory=list)
    edits: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    writes: list[tuple[str, str]] = field(default_factory=list)      # de llamadas JSON (write_file…)
    loose: list[tuple[str, str, str]] = field(default_factory=list)   # BUSCAR/REEMPLAZAR sin <editar>: (buscar, reemplazo, pista)

    @property
    def needs_reply(self) -> bool:
        return bool(self.reads or self.lists or self.greps)

    @property
    def empty(self) -> bool:
        return not (self.needs_reply or self.edits or self.deletes or self.writes or self.loose)


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v else None
    except ValueError:
        return None


def _unfence(s: str) -> str:
    """Quita una valla ``` que envuelva el texto de BUSCAR/REEMPLAZAR."""
    s = s.strip("\n")
    lines = s.split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


_PAIR = re.compile(r"<{5,9}\s*(?:BUSCAR|SEARCH)[^\n]*\n(.*?)\n?={5,9}[^\n]*\n(.*?)\n?>{5,9}\s*(?:REEMPLAZAR|REPLACE)",
                   re.S)
_FILE_HINT = re.compile(r"([\w.\-/\\]+\.[A-Za-z0-9]{1,6})\b")


def _pairs(body: str) -> list[tuple[str, str]]:
    pairs = []
    for m in _PAIR.finditer(body):
        pairs.append((_unfence(m.group(1)), _unfence(m.group(2))))
    if not pairs:
        for m in re.finditer(r"<buscar>(.*?)</buscar>\s*<reemplazar>(.*?)</reemplazar>", body, re.S | re.I):
            pairs.append((_unfence(m.group(1)), _unfence(m.group(2))))
    return pairs


def parse_actions(text: str) -> Actions:
    a = Actions()
    rest = text
    for m in re.finditer(rf"<editar\s+{_ATTR}\s*>(.*?)(?:</editar>|$)", text, re.S | re.I):
        pairs = _pairs(m.group(2))
        if pairs:
            a.edits.append((m.group(1).strip(), pairs))
    rest = re.sub(r"<editar\b.*?(?:</editar>|$)", "", rest, flags=re.S | re.I)
    for m in re.finditer(rf"<leer\s+{_ATTR}([^>]*)/?>", rest, re.I):
        extra = m.group(2)
        a.reads.append((m.group(1).strip(), _int((re.search(r'(?:desde|from)\s*=\s*["\']?(\d+)', extra) or [None, None])[1]),
                        _int((re.search(r'(?:hasta|to)\s*=\s*["\']?(\d+)', extra) or [None, None])[1])))
    for m in re.finditer(r"<leer>\s*([^<\n]+?)\s*</leer>", rest, re.I):
        a.reads.append((m.group(1).strip(), None, None))
    for m in re.finditer(rf"<listar(?:\s+{_ATTR})?\s*/?>", rest, re.I):
        a.lists.append((m.group(1) or ".").strip())
    for m in re.finditer(r"<grep>(.*?)</grep>", rest, re.S | re.I):
        if m.group(1).strip():
            a.greps.append(m.group(1).strip())
    for m in re.finditer(rf"<borrar\s+{_ATTR}\s*/?>", rest, re.I):
        a.deletes.append(m.group(1).strip())
    _json_calls(rest, a)
    for m in _PAIR.finditer(rest):          # pares sueltos: el archivo se deduce después (locate)
        hints = _FILE_HINT.findall(rest[max(0, m.start() - 240):m.start()])
        a.loose.append((_unfence(m.group(1)), _unfence(m.group(2)), hints[-1] if hints else ""))
    a.reads = list(dict.fromkeys(a.reads))
    return a


def _json_objects(text: str):
    """Objetos JSON {"name": …} sueltos en el texto (llamadas a herramientas de modelos «agente»)."""
    for m in re.finditer(r'\{\s*"(?:name|tool|function)"\s*:', text):
        depth, in_str, esc = 0, False, False
        for j in range(m.start(), len(text)):
            ch = text[j]
            if in_str:
                esc = (ch == "\\" and not esc)
                if ch == '"' and not esc:
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        yield json.loads(text[m.start():j + 1])
                    except ValueError:
                        pass
                    break


def _json_calls(text: str, a: Actions):
    """Traduce read_file / write_file / str_replace / list / grep / bash(cat, ls) a acciones."""
    for obj in _json_objects(text):
        name = str(obj.get("name") or obj.get("tool") or obj.get("function") or "").lower()
        args = obj.get("arguments") or obj.get("parameters") or obj.get("input") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"command": args}
        if not isinstance(args, dict):
            continue
        path = str(args.get("path") or args.get("file_path") or args.get("filename") or args.get("file") or "")
        if name in ("bash", "shell", "run_command", "terminal", "execute"):
            cmd = str(args.get("command") or args.get("cmd") or "")
            m = re.match(r"\s*(?:cat|type|Get-Content)\s+['\"]?([^\s'\"|;&>]+)", cmd)
            if m:
                a.reads.append((m.group(1), None, None))
            elif re.match(r"\s*(?:ls|dir|Get-ChildItem)\b", cmd):
                a.lists.append(".")
            continue
        if any(k in name for k in ("str_replace", "edit", "replace", "patch")) and path:
            old = args.get("old_str") or args.get("old_string") or args.get("old") or args.get("search") or ""
            new = args.get("new_str") or args.get("new_string") or args.get("new") or args.get("replace") or ""
            a.edits.append((path, [(str(old), str(new))]))
        elif any(k in name for k in ("write", "create", "save")) and path:
            a.writes.append((path, str(args.get("content") or args.get("file_text") or args.get("text") or "")))
        elif any(k in name for k in ("read", "view", "open", "cat", "get_file")) and path:
            a.reads.append((path, None, None))
        elif any(k in name for k in ("delete", "remove")) and path:
            a.deletes.append(path)
        elif any(k in name for k in ("grep", "search", "find")):
            q = str(args.get("pattern") or args.get("query") or args.get("text") or "")
            if q:
                a.greps.append(q)
        elif any(k in name for k in ("list", "ls", "glob", "dir")):
            a.lists.append(path or str(args.get("directory") or "."))


_ANNOUNCE = re.compile(r"\b(voy a|vamos a|déjame|dejame|ahora|primero|procedo a|necesito)\s+(?:a\s+)?"
                       r"(leer|revisar|ver|mirar|abrir|examinar|modificar|cambiar|editar|actualizar|crear|aplicar)",
                       re.I)


def announces_without_acting(answer: str) -> bool:
    """El modelo dice «voy a leer/modificar…» pero no escribió ninguna etiqueta ni bloque."""
    return bool(_ANNOUNCE.search(answer)) and "```" not in answer


TOOL_TAGS = re.compile(r"<(?:leer|listar|borrar)\b[^>]*/?>(?:[^<\n]*</leer>)?|<grep>.*?</grep>|</?editar\b[^>]*>",
                       re.S | re.I)


# ---------------------------------------------------------------------------
# Cambios y copias de seguridad
# ---------------------------------------------------------------------------

@dataclass
class Change:
    rel: str
    backup: Path | None          # copia previa (None si el archivo no existía)
    kind: str = ""               # creado | modificado | borrado
    added: int = 0
    removed: int = 0


@dataclass
class ChangeSet:
    label: str
    when: float = field(default_factory=time.time)
    changes: dict[str, Change] = field(default_factory=dict)


def _lines(p: Path | None) -> list[str]:
    if p is None or not p.exists():
        return []
    try:
        return p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


class Workspace:
    def __init__(self, root: Path, session_id: str):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"'{root}' no es una carpeta.")
        self.name = self.root.name or str(self.root)
        self.session_id = session_id
        self.history: list[ChangeSet] = []
        self.current: ChangeSet | None = None
        self.read_files: set[str] = set()
        self.sent: dict[str, int] = {}          # versión (mtime) de cada archivo que ya vio el modelo
        self._ignore = self._load_gitignore()
        self.tree_cache = ""

    # -- rutas --
    def rel(self, p: Path) -> str:
        return p.resolve().relative_to(self.root).as_posix()

    def resolve(self, rel: str) -> Path:
        p = Path(rel.strip().strip("'\"").replace("\\", "/")).expanduser()
        target = (p if p.is_absolute() else self.root / p).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError(f"'{rel}' queda fuera del proyecto ({self.root}).")
        return target

    def _load_gitignore(self) -> list[str]:
        f = self.root / ".gitignore"
        try:
            return [l.strip().rstrip("/") for l in f.read_text(encoding="utf-8", errors="replace").splitlines()
                    if l.strip() and not l.startswith(("#", "!"))]
        except OSError:
            return []

    def ignored(self, rel: str, is_dir: bool) -> bool:
        name = rel.rsplit("/", 1)[-1]
        if is_dir and (name in IGNORE_DIRS or name.endswith(".egg-info")):
            return True
        for pat in self._ignore:
            pat = pat.lstrip("/")
            if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat) or rel.startswith(pat + "/"):
                return True
        return False

    def walk(self, sub: str = ".", limit: int = 4000) -> list[tuple[str, int]]:
        """(ruta relativa, bytes) de los archivos no ignorados."""
        base = self.resolve(sub)
        out = []
        for d, dirs, files in os.walk(base):
            rd = Path(d).resolve().relative_to(self.root).as_posix()
            rd = "" if rd == "." else rd
            dirs[:] = sorted(x for x in dirs if not self.ignored(f"{rd}/{x}".lstrip("/"), True))
            for f in sorted(files):
                r = f"{rd}/{f}".lstrip("/")
                if self.ignored(r, False):
                    continue
                try:
                    out.append((r, (Path(d) / f).stat().st_size))
                except OSError:
                    continue
                if len(out) >= limit:
                    return out
        return out

    def tree(self, max_entries: int = 250) -> str:
        files = self.walk()
        if not files:
            return "(carpeta vacía)"

        def size(n: int) -> str:
            return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB" if n < 1024 ** 2 else f"{n / 1024 ** 2:.1f} MB"

        if len(files) > max_entries:            # proyecto grande: resumen por carpeta
            counts: dict[str, int] = {}
            for r, _ in files:
                top = r.split("/", 2)
                key = "/".join(top[:2]) + "/" if len(top) > 2 else (top[0] + "/" if len(top) == 2 else "(raíz)")
                counts[key] = counts.get(key, 0) + 1
            lines = [f"{k}  ({v} archivos)" for k, v in sorted(counts.items())]
            return (f"{len(files)} archivos; resumen por carpeta (usa <listar ruta=\"…\"/> para ver una):\n"
                    + "\n".join(lines[:max_entries]))
        lines, prev = [], []
        for r, n in files:
            parts = r.split("/")
            for i, d in enumerate(parts[:-1]):
                if i >= len(prev) or prev[i] != d:
                    lines.append("  " * i + d + "/")
                    prev = prev[:i] + [d]
            prev = prev[:len(parts) - 1]
            lines.append("  " * (len(parts) - 1) + f"{parts[-1]}  ({size(n)})")
        return "\n".join(lines)

    def instructions(self) -> str:
        if not self.tree_cache:
            self.tree_cache = self.tree()
        return INSTRUCTIONS.format(name=self.name, root=self.root, tree=self.tree_cache)

    def refresh(self):
        self.tree_cache = ""
        self._ignore = self._load_gitignore()

    # -- herramientas de lectura --
    def read(self, rel: str, start: int | None = None, end: int | None = None) -> str:
        p = self.resolve(rel)
        if not p.exists():
            near = [r for r, _ in self.walk() if r.rsplit("/", 1)[-1].lower() == p.name.lower()][:3]
            raise ValueError(f"no existe '{rel}'" + (f" (¿quizá {', '.join(near)}?)" if near else ""))
        if p.is_dir():
            return self.listing(rel)
        if p.suffix.lower() in BINARY_EXT:
            raise ValueError(f"'{rel}' es binario ({p.stat().st_size:,} bytes): no se puede leer como texto")
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        n = len(lines)
        a = max(1, start or 1)
        b = min(n, end or n)
        chunk = "\n".join(lines[a - 1:b])
        note = ""
        if len(chunk) > MAX_READ:
            cut = chunk[:MAX_READ].rfind("\n")
            shown = chunk[:cut].count("\n") + 1
            chunk = chunk[:cut]
            note = (f"\n[…recortado: se muestran las líneas {a}–{a + shown - 1} de {n}. Pide más con "
                    f"<leer ruta=\"{rel}\" desde=\"{a + shown}\"/>]")
            b = a + shown - 1
        self.read_files.add(self.rel(p))
        if a == 1 and b == n:
            self.sent[self.rel(p)] = p.stat().st_mtime_ns      # ya lo vio entero: no se vuelve a adjuntar
        lang = p.suffix.lstrip(".") or "text"
        rng = f" (líneas {a}–{b} de {n})" if (a > 1 or b < n) else f" ({n} líneas)"
        return f"### {self.rel(p)}{rng}\n```{lang}\n{chunk}\n```{note}"

    def fresh_context(self, max_chars: int, per_file: int = 12000) -> tuple[str, list[str], list[str]]:
        """
        Contenido de los archivos de texto nuevos o cambiados desde la última vez que los vio el
        modelo, de menor a mayor hasta max_chars. Así, en proyectos pequeños no hace falta pedir
        lecturas. Se omiten los generados desde un .lai (se edita el .lai). Devuelve
        (texto, incluidos, omitidos).
        """
        files = self.walk()
        lai_dirs = {str(Path(r).parent) for r, _ in files if r.endswith(".lai")}
        generated = {str(Path(d) / x).replace("\\", "/").lstrip("./") for d in lai_dirs
                     for x in ("index.html", "css/styles.css", "js/app.js")}
        cands, skipped = [], []
        for r, n in files:
            if Path(r).suffix.lower() in BINARY_EXT or r in generated or Path(r).name == ".gitignore":
                continue
            try:
                mt = (self.root / r).stat().st_mtime_ns
            except OSError:
                continue
            if self.sent.get(r) == mt:
                continue
            (cands if n <= per_file else skipped).append((r, n, mt))
        out, used, names = [], 0, []
        for r, n, mt in sorted(cands, key=lambda x: x[1]):
            if used + n > max_chars:
                skipped.append((r, n, mt))
                continue
            try:
                text = (self.root / r).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            used += len(text)
            self.sent[r] = mt
            self.read_files.add(r)
            names.append(r)
            out.append(f"### {r}\n```{Path(r).suffix.lstrip('.') or 'text'}\n{text.rstrip()}\n```")
        if not out:
            return "", [], [r for r, _, _ in skipped]
        head = ("[Contenido ACTUAL de los archivos del proyecto" + (" que cambiaron desde la última vez" if len(self.sent) > len(names) else "")
                + " · adjuntado por el programa]")
        tail = (f"\n\n(No incluidos por tamaño, pídelos con <leer/>: {', '.join(r for r, _, _ in skipped[:12])})"
                if skipped else "")
        return head + "\n\n" + "\n\n".join(out) + tail, names, [r for r, _, _ in skipped]

    def locate(self, search: str) -> list[str]:
        """Archivos que contienen el texto (ignorando sangría y líneas vacías)."""
        key = "\n".join(l.strip() for l in search.split("\n") if l.strip())
        if not key:
            return []
        hits = []
        for r, n in self.walk():
            if n > 2_000_000 or Path(r).suffix.lower() in BINARY_EXT:
                continue
            try:
                text = (self.root / r).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if search in text or key in "\n".join(l.strip() for l in text.split("\n") if l.strip()):
                hits.append(r)
        return hits

    def listing(self, rel: str = ".") -> str:
        base = self.resolve(rel)
        if not base.is_dir():
            raise ValueError(f"'{rel}' no es una carpeta")
        sub = self.rel(base) if base != self.root else "."
        files = self.walk(sub, limit=400)
        body = "\n".join(f"{r}  ({n:,} B)".replace(",", ".") for r, n in files) or "(vacía)"
        return f"### Contenido de {sub}/\n{body}"

    def grep(self, pattern: str, max_hits: int = 40) -> str:
        hits = []
        low = pattern.lower()
        for r, n in self.walk():
            if n > 2_000_000 or Path(r).suffix.lower() in BINARY_EXT:
                continue
            try:
                lines = (self.root / r).read_text(encoding="utf-8", errors="replace").split("\n")
            except OSError:
                continue
            for i, l in enumerate(lines, 1):
                if low in l.lower():
                    hits.append(f"{r}:{i}: {l.strip()[:160]}")
                    if len(hits) >= max_hits:
                        return f"### Búsqueda «{pattern}» (primeros {max_hits})\n" + "\n".join(hits)
        return f"### Búsqueda «{pattern}»\n" + ("\n".join(hits) if hits else "(sin resultados)")

    # -- cambios --
    def begin(self, label: str):
        self.current = ChangeSet(label[:80])

    def _touch(self, p: Path):
        """Antes de modificar: copia de seguridad (una por archivo y turno)."""
        if self.current is None:
            self.begin("cambios")
        rel = self.rel(p)
        if rel in self.current.changes:
            return
        backup = None
        if p.exists():
            backup = BACKUP_DIR / self.session_id / f"{len(self.history) + 1:03d}" / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, backup)
        self.current.changes[rel] = Change(rel, backup)

    def track(self, paths: list[Path]):
        """Para archivos que escribe otro código (p. ej. la página generada desde .lai)."""
        for p in paths:
            self._touch(p.resolve())

    def write(self, rel: str, content: str) -> Path:
        p = self.resolve(rel)
        if p.is_dir():
            raise ValueError(f"'{rel}' es una carpeta")
        old = p.read_text(encoding="utf-8", errors="replace") if p.exists() else None
        if old is not None and _looks_truncated(old, content):
            raise ValueError(f"el nuevo contenido de '{rel}' parece incompleto (tiene «…» o «resto igual» y es mucho "
                             "más corto): para cambiar partes de un archivo existente usa <editar>")
        self._touch(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8", newline="\n")
        return p

    def edit(self, rel: str, pairs: list[tuple[str, str]]) -> tuple[Path | None, list[str]]:
        """Aplica pares BUSCAR/REEMPLAZAR. Devuelve (ruta si cambió algo, errores)."""
        p = self.resolve(rel)
        errors = []
        if not p.exists():
            if all(not s.strip() for s, _ in pairs):
                return self.write(rel, "\n".join(r for _, r in pairs)), []
            return None, [f"{rel}: no existe (para crearlo usa un bloque ```lenguaje {rel}```)"]
        text = p.read_text(encoding="utf-8", errors="replace")
        crlf = "\r\n" in text
        text = text.replace("\r\n", "\n")
        new = text
        for k, (search, repl) in enumerate(pairs, 1):
            if not search.strip():
                new = new.rstrip("\n") + "\n" + repl + "\n"
                continue
            res = _replace(new, search, repl)
            if isinstance(res, str):
                new = res
            else:
                errors.append(f"{rel}, edición {k}: {res[1]}")
        if new == text:
            return None, errors
        self._touch(p)
        p.write_text(new.replace("\n", "\r\n") if crlf else new, encoding="utf-8", newline="")
        return p, errors

    def delete(self, rel: str) -> Path:
        p = self.resolve(rel)
        if not p.exists() or p.is_dir():
            raise ValueError(f"no existe el archivo '{rel}'")
        self._touch(p)
        p.unlink()
        return p

    def commit(self) -> ChangeSet | None:
        cs, self.current = self.current, None
        if cs is None or not cs.changes:
            return None
        for ch in cs.changes.values():
            p = self.root / ch.rel
            before, after = _lines(ch.backup), _lines(p)
            ch.kind = "borrado" if not p.exists() else ("creado" if ch.backup is None else "modificado")
            for l in difflib.ndiff(before, after):
                ch.added += l.startswith("+ ")
                ch.removed += l.startswith("- ")
        cs.changes = {k: v for k, v in cs.changes.items() if v.kind != "modificado" or v.added or v.removed}
        if not cs.changes:
            return None
        self.history.append(cs)
        if any(c.kind in ("creado", "borrado") for c in cs.changes.values()):
            self.refresh()
        return cs

    def undo(self) -> ChangeSet | None:
        if not self.history:
            return None
        cs = self.history.pop()
        for ch in reversed(list(cs.changes.values())):
            p = self.root / ch.rel
            if ch.backup is not None and ch.backup.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ch.backup, p)
            elif p.exists():
                p.unlink()
        self.refresh()
        return cs

    def diff(self, cs: ChangeSet | None = None, max_lines: int = 400) -> str:
        cs = cs or (self.history[-1] if self.history else None)
        if cs is None:
            return ""
        out = []
        for ch in cs.changes.values():
            p = self.root / ch.rel
            out += difflib.unified_diff(_lines(ch.backup), _lines(p), f"a/{ch.rel}", f"b/{ch.rel}", lineterm="", n=2)
        return "\n".join(out[:max_lines]) + (f"\n… ({len(out) - max_lines} líneas más)" if len(out) > max_lines else "")


def summary(cs: ChangeSet) -> str:
    parts = []
    for ch in cs.changes.values():
        if ch.kind == "creado":
            parts.append(f"{ch.rel} (nuevo, {ch.added} líneas)")
        elif ch.kind == "borrado":
            parts.append(f"{ch.rel} (borrado)")
        else:
            parts.append(f"{ch.rel} +{ch.added} −{ch.removed}")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Búsqueda tolerante para BUSCAR/REEMPLAZAR
# ---------------------------------------------------------------------------

def _replace(text: str, search: str, repl: str) -> str | tuple[None, str]:
    """Sustituye una vez. Exacto; si no, ignorando espacios finales; si no, ignorando la sangría."""
    starts = [m.start() for m in re.finditer(re.escape(search), text)]
    if search[:1].isspace():               # con sangría: solo vale si empieza al principio de una línea
        starts = [i for i in starts if i == 0 or text[i - 1] == "\n"]
    if len(starts) == 1:
        i = starts[0]
        return text[:i] + repl + text[i + len(search):]
    if len(starts) > 1:
        return None, f"el texto de BUSCAR aparece {len(starts)} veces: añade líneas de contexto para que sea único"
    lines = text.split("\n")
    s_lines = search.strip("\n").split("\n")
    r_lines = repl.strip("\n").split("\n") if repl.strip("\n") else []
    for norm in (str.rstrip, str.strip):
        key = [norm(l) for l in s_lines]
        hits = [i for i in range(len(lines) - len(key) + 1)
                if all(norm(lines[i + k]) == key[k] for k in range(len(key)))]
        if len(hits) > 1:
            return None, f"el texto de BUSCAR aparece {len(hits)} veces: añade contexto para que sea único"
        if len(hits) == 1:
            i = hits[0]
            if norm is str.strip:              # re-sangrar el reemplazo como el original
                ind = lambda s: s[:len(s) - len(s.lstrip())]      # noqa: E731
                delta = ind(lines[i]), ind(s_lines[0])
                r_lines = [(delta[0] + l[len(delta[1]):] if l.startswith(delta[1]) else delta[0] + l.lstrip())
                           if l.strip() else l for l in r_lines]
            return "\n".join(lines[:i] + r_lines + lines[i + len(key):])
    first = next((l.strip() for l in s_lines if l.strip()), "")
    near = [i + 1 for i, l in enumerate(lines) if first and first[:40] in l][:3]
    return None, ("no se encontró el texto de BUSCAR" + (f" (la primera línea aparece en la línea {near})"
                                                          if near else "") + ": léelo de nuevo con <leer> y copia exacto")


_PLACEHOLDER = re.compile(r"(\.\.\.|…)\s*$|(resto|rest of|existing|igual que antes|sin cambios|unchanged)", re.I | re.M)


def _looks_truncated(old: str, new: str) -> bool:
    o, n = old.count("\n") + 1, new.count("\n") + 1
    if o < 40 or n > o * 0.6:
        return False
    comments = [l for l in new.split("\n") if re.match(r"\s*(//|#|/\*|<!--|\*)", l) or l.strip() in ("...", "…")]
    return any(_PLACEHOLDER.search(l) for l in comments)
