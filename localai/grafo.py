"""
Red del proyecto (al estilo de Graphify): un mapa compacto de los archivos y de cómo se conectan,
para que el modelo sepa qué hay y dónde sin leerlo todo. Se genera SIN modelo, con análisis
estático ligero y sin dependencias:

- JS/TS: require/import, exportaciones, rutas Express con el prefijo de app.use, res.render,
  formularios, fetch y redirecciones, process.env y tablas SQL de las consultas.
- Python: imports locales, funciones y clases, rutas Flask/FastAPI, render_template, os.environ.
- SQL (CREATE TABLE), plantillas EJS/HTML (include, formularios, CSS/JS enlazados, variables),
  Dockerfile, docker-compose, .env, package.json y Markdown.

Además comprueba que las piezas encajan («avisos»): rutas que no existen, vistas, parciales o
archivos que faltan, variables de plantilla que no se pasan, tablas sin crear, variables de
entorno sin definir, COPY de Docker sin origen…

La red se guarda en Clay-Myrrh.md entre las marcas MARK_START / MARK_END (la regenera /grafo y
se actualiza sola en modo proyecto), así que la puede leer cualquier herramienta.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import posixpath
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

MARK_START = "<!-- red:inicio · generada por Clay-Myrrh con /grafo; se actualiza sola, no la edites a mano -->"
MARK_END = "<!-- red:fin -->"
_SECTION = re.compile(r"\n*<!-- red:inicio\b.*?<!-- red:fin -->\n?", re.S)

MEMORY_NAMES = ("Clay-Myrrh.md", "LOCAL_AI.md")     # no son nodos de la red: son su «memoria»
MAX_FILE = 300_000                                  # bytes: los archivos más grandes solo se nombran
MAX_ITEMS = 6                                       # elementos por lista en cada línea
MAX_AVISOS = 20

JS_EXT = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}
TPL_EXT = {".ejs", ".html", ".htm", ".hbs", ".njk", ".jinja", ".jinja2", ".j2", ".vue", ".svelte", ".php", ".pug"}
STATIC_EXT = {"css", "js", "mjs", "png", "jpg", "jpeg", "svg", "ico", "webp", "gif", "avif", "woff", "woff2", "map"}
ENV_BUILTIN = {"NODE_ENV", "PATH", "HOME", "USERPROFILE", "PWD", "TEMP", "TMP", "CI", "DEBUG", "HOSTNAME", "LANG"}
SQL_WORDS = {"SELECT", "WHERE", "SET", "VALUES", "AS", "ON", "AND", "OR", "NOT", "NULL", "DUAL", "LATERAL"}
CONSTRAINTS = {"PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "CHECK", "KEY", "INDEX", "EXCLUDE"}
# tablas que exigen librerías conocidas: (paquete npm, tabla por defecto, columnas obligatorias)
KNOWN_TABLES = [("connect-pg-simple", "session", {"sid", "sess", "expire"})]
JS_WORDS = set("""break case catch class const continue debugger default delete do else export extends false finally
for function if import in instanceof let new null return super switch this throw true try typeof var void while with
yield async await of undefined NaN Infinity Date JSON Math Number String Boolean Array Object parseInt parseFloat
isNaN encodeURIComponent decodeURIComponent console window document include locals __append escapeFn
__output __line include""".split())


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------

@dataclass
class Node:
    rel: str
    kind: str
    lines: int = 0
    facts: dict = field(default_factory=dict)

    def add(self, key: str, *vals: str):
        lst = self.facts.setdefault(key, [])
        for v in vals:
            if v and v not in lst:
                lst.append(v)


@dataclass
class Route:
    method: str
    path: str          # sin prefijo de montaje
    rel: str
    line: int
    router: bool       # definida en un Router (el prefijo lo pone app.use)


@dataclass
class Use:
    method: str
    path: str
    rel: str
    how: str           # formulario | fetch | redirección | enlace


@dataclass
class Red:
    root: Path
    nodes: dict = field(default_factory=dict)        # rel → Node
    routes: list = field(default_factory=list)       # Route
    full_routes: list = field(default_factory=list)  # (método, ruta completa, rel, línea)
    uses: list = field(default_factory=list)         # Use
    avisos: list = field(default_factory=list)
    stack: list = field(default_factory=list)
    commands: list = field(default_factory=list)
    total_chars: int = 0
    # material para las comprobaciones
    requires: list = field(default_factory=list)     # (rel, spec, resuelto | None)
    mounts: list = field(default_factory=list)       # (rel, prefijo, destino rel | None, texto)
    renders: list = field(default_factory=list)      # (rel, línea, vista, claves | None)
    includes: list = field(default_factory=list)     # (rel, spec, resuelto | None)
    static_refs: list = field(default_factory=list)  # (rel, ref)
    static_dirs: set = field(default_factory=set)
    views_dirs: set = field(default_factory=set)
    locals_global: set = field(default_factory=set)
    tpl_vars: dict = field(default_factory=dict)     # rel → variables usadas
    env_used: dict = field(default_factory=dict)     # nombre → [rel]
    env_defined: set = field(default_factory=set)
    env_sources: list = field(default_factory=list)
    tables: dict = field(default_factory=dict)       # tabla → (rel, columnas)
    tables_used: dict = field(default_factory=dict)  # tabla → [rel]
    lib_tables: list = field(default_factory=list)   # (paquete, tabla, rel)
    npm_deps: set = field(default_factory=set)
    docker: list = field(default_factory=list)       # (rel Dockerfile, [orígenes COPY])
    compose: dict = field(default_factory=dict)      # servicio → dict
    compose_rel: str = ""
    scripts: list = field(default_factory=list)      # (rel package.json, nombre, comando)

    # -- salida --
    def node_lines(self) -> dict[str, str]:
        return {rel: _node_line(n) for rel, n in sorted(self.nodes.items(), key=lambda x: _sort_key(x[0]))
                if n.facts or n.kind not in ("otro",)}

    def text(self) -> str:
        """Bloque Markdown de la red (con sus marcas), listo para Clay-Myrrh.md o el prompt."""
        lines = [MARK_START, "## Red del proyecto"]
        if self.stack:
            lines.append("Stack: " + " · ".join(self.stack))
        if self.commands:
            lines.append("Comandos: " + " · ".join(self.commands))
        nl = self.node_lines()
        others = sorted(r for r, n in self.nodes.items() if r not in nl)
        lines.append(f"Archivos ({len(self.nodes)}) · formato: ruta [tipo líneas] hechos · «→» = usa")
        lines += [f"- {l}" for l in nl.values()]
        if others:
            lines.append("- otros: " + _cut(others, 30))
        lines.append("### Avisos (comprobaciones automáticas: piezas que no encajan)")
        lines += [f"- {a}" for a in self.avisos[:MAX_AVISOS]] or ["- ninguno"]
        if len(self.avisos) > MAX_AVISOS:
            lines.append(f"- … y {len(self.avisos) - MAX_AVISOS} más (/grafo avisos)")
        lines.append(MARK_END)
        return "\n".join(lines)

    def tokens(self) -> int:
        return int(len(self.text()) / 3.5)


def _sort_key(rel: str):
    return (rel.count("/") > 0, rel.lower())


def _clean(p: str) -> str:
    """Ruta relativa normalizada sin «./» ni «../» delante (ojo: lstrip("./") se comería el punto de .env)."""
    p = posixpath.normpath(p.replace("\\", "/")) if p else ""
    while p.startswith("../"):
        p = p[3:]
    return "" if p in (".", "..") else p


def _cut(items, n: int = MAX_ITEMS) -> str:
    items = list(items)
    return ", ".join(items[:n]) + (f" +{len(items) - n}" if len(items) > n else "")


def _node_line(n: Node) -> str:
    parts = [f"{n.rel} [{n.kind} {n.lines}]"]
    for k, v in n.facts.items():
        if not v:
            continue
        parts.append(f"→ {_cut(v)}" if k == "usa" else f"{k}: {_cut(v)}")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Construcción
# ---------------------------------------------------------------------------

def kind_of(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1]
    low, ext = name.lower(), Path(name).suffix.lower()
    if low == "dockerfile" or low.endswith(".dockerfile"):
        return "docker"
    if low in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        return "compose"
    if low.startswith(".env"):
        return "env"
    if low == "package.json":
        return "npm"
    if ext in JS_EXT:
        return "js"
    if ext == ".py":
        return "py"
    if ext == ".sql":
        return "sql"
    if ext in TPL_EXT:
        return ext.lstrip(".")
    if ext in (".css", ".scss", ".sass", ".less"):
        return "css"
    if ext in (".md", ".markdown"):
        return "md"
    if ext == ".lai":
        return "lai"
    if low == "requirements.txt":
        return "pip"
    return "otro"


def build(root: Path, files: list[tuple[str, int]]) -> Red:
    """Construye la red a partir de (ruta relativa, bytes) de los archivos del proyecto."""
    red = Red(Path(root))
    texts: dict[str, str] = {}
    fileset = {r for r, _ in files}
    for rel, size in files:
        if rel.rsplit("/", 1)[-1] in MEMORY_NAMES:
            continue
        kind = kind_of(rel)
        node = Node(rel, kind)
        red.nodes[rel] = node
        if kind == "otro" or size > MAX_FILE:
            continue
        try:
            text = (red.root / rel).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        except OSError:
            continue
        node.lines = text.count("\n") + (0 if text.endswith("\n") else 1)
        red.total_chars += len(text)
        texts[rel] = text
    # primero lo que da contexto a los demás (carpetas de vistas y estáticos, dependencias)
    for rel, text in texts.items():
        k = red.nodes[rel].kind
        if k == "npm":
            _parse_package(red, rel, text)
        elif k == "env":
            _parse_env(red, rel, text)
        elif k == "compose":
            _parse_compose(red, rel, text)
        elif k == "js":
            for m in re.finditer(r"app\.set\(\s*['\"]views['\"]\s*,([^;\n]*)", text):
                lits = re.findall(r"['\"]([\w.\-/]+)['\"]", m.group(1))
                if lits:
                    red.views_dirs.add(_clean(lits[-1]) or "views")
            for m in re.finditer(r"express\.static\(([^;\n]*)", text):
                lits = re.findall(r"['\"]([\w.\-/]+)['\"]", m.group(1))
                if lits:
                    red.static_dirs.add(_clean(lits[-1]))
    if not red.views_dirs:
        red.views_dirs.add("views")
    parsers = {"js": _parse_js, "py": _parse_py, "sql": _parse_sql, "docker": _parse_dockerfile,
               "md": _parse_md, "css": _parse_css, "lai": _parse_lai, "pip": _parse_pip}
    parsers.update({e.lstrip("."): _parse_tpl for e in TPL_EXT})
    for rel, text in texts.items():
        node = red.nodes[rel]
        parse = parsers.get(node.kind)
        if parse is None:
            continue
        try:
            parse(red, rel, text, fileset)
        except Exception as e:          # un archivo raro nunca debe romper la red entera
            node.add("no analizado", type(e).__name__)
    _resolve_routes(red)
    _stack_and_commands(red)
    _checks(red, fileset)
    return red


# -- utilidades de análisis --------------------------------------------------

_STR_OR_COMMENT = re.compile(r"(\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)|//[^\n]*|/\*.*?\*/", re.S)


def _strip_js_comments(text: str) -> str:
    return _STR_OR_COMMENT.sub(lambda m: m.group(1) or (" " if m.group(0).startswith("//") else
                                                        "\n" * m.group(0).count("\n")), text)


def _line_at(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _balanced(text: str, start: int, open_ch: str = "{", close_ch: str = "}") -> str | None:
    """Contenido entre el carácter de apertura en `start` y su cierre (sin ellos)."""
    if start >= len(text) or text[start] != open_ch:
        return None
    depth, i, quote = 0, start, ""
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return None


def _split_top(body: str) -> list[str]:
    parts, depth, cur, quote = [], 0, [], ""
    for ch in body:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def _object_keys(body: str) -> set[str] | None:
    """Claves de primer nivel de un literal {…}; None si hay «...spread» (no se puede saber)."""
    keys = set()
    for part in _split_top(body):
        p = part.strip()
        if not p:
            continue
        if p.startswith("..."):
            return None
        m = re.match(r"['\"]?([A-Za-z_$][\w$]*)['\"]?\s*(?::|$|\()", p)
        if m:
            keys.add(m.group(1))
    return keys


def _resolve_js(fileset: set[str], rel: str, spec: str) -> str | None:
    base = posixpath.dirname(rel)
    target = posixpath.normpath(posixpath.join(base, spec))
    for cand in (target, *(target + e for e in (".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx", ".json")),
                 *(target + "/index" + e for e in (".js", ".ts", ".mjs", ".jsx", ".tsx"))):
        if cand in fileset:
            return cand
    return None


def _pkg_name(spec: str) -> str:
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


def _norm_path(p: str) -> str:
    """Ruta usada en una plantilla o código, con las expresiones sustituidas por :x."""
    p = re.sub(r"<%[=-]?.*?%>|\$\{.*?\}|\{\{.*?\}\}|\{%.*?%\}|<\?.*?\?>", ":x", p)
    p = p.split("?")[0].split("#")[0]
    if len(p) > 1:
        p = p.rstrip("/")
    return p or "/"


def _route_regex(path: str) -> re.Pattern:
    out = []
    for seg in path.strip("/").split("/"):
        if seg.startswith(":") or (seg.startswith("{") and seg.endswith("}")) or (seg.startswith("<") and seg.endswith(">")):
            out.append(r"[^/]+")
        elif seg == "*":
            out.append(r".*")
        else:
            out.append(re.escape(seg))
    return re.compile("^/" + "/".join(out) + "/?$" if path.strip("/") else r"^/?$")


def _join(prefix: str, path: str) -> str:
    p = "/" + "/".join(x for x in (prefix.strip("/"), path.strip("/")) if x)
    return p


def _sql_tables_used(red: Red, rel: str, text: str):
    for m in re.finditer(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+[\"`]?([A-Za-z_]\w*)", text):
        t = m.group(1)
        if t.upper() in SQL_WORDS:
            continue
        red.tables_used.setdefault(t.lower(), [])
        if rel not in red.tables_used[t.lower()]:
            red.tables_used[t.lower()].append(rel)
        red.nodes[rel].add("tablas", t)


def _env_used(red: Red, rel: str, name: str):
    lst = red.env_used.setdefault(name, [])
    if rel not in lst:
        lst.append(rel)
    red.nodes[rel].add("env", name)


# -- analizadores por tipo ---------------------------------------------------

def _parse_js(red: Red, rel: str, text: str, fileset: set[str]):
    node = red.nodes[rel]
    code = _strip_js_comments(text)
    var_mod: dict[str, str] = {}
    pkgs = []
    for m in re.finditer(r"(?:(?:const|let|var)\s+(\w+)\s*=\s*)?require\(\s*['\"]([^'\"]+)['\"]\s*\)"
                         r"|import\s+(?:(\w+)\s*,?\s*)?(?:\{[^}]*\}|\*\s+as\s+\w+)?\s*(?:from\s+)?['\"]([^'\"]+)['\"]"
                         r"|import\(\s*['\"]([^'\"]+)['\"]\s*\)", code):
        var = m.group(1) or m.group(3)
        spec = m.group(2) or m.group(4) or m.group(5)
        if spec.startswith("."):
            res = _resolve_js(fileset, rel, spec)
            red.requires.append((rel, spec, res))
            if res:
                node.add("usa", res)
                if var:
                    var_mod[var] = res
        else:
            pkgs.append(_pkg_name(spec))
            if var:
                var_mod[var] = "npm:" + _pkg_name(spec)
    if pkgs:
        node.add("npm", *dict.fromkeys(p for p in pkgs if not p.startswith("node:") and p not in
                                       ("path", "fs", "crypto", "os", "http", "https", "url", "util", "events")))
    # exportaciones y funciones
    for m in re.finditer(r"module\.exports\s*=\s*(\{)", code):
        body = _balanced(code, m.start(1))
        keys = _object_keys(body or "") or set()
        node.add("exporta", *sorted(keys))
    for m in re.finditer(r"module\.exports\s*=\s*(\w+)\s*;?", code):
        if m.group(1) not in ("require", "function", "class", "async"):
            node.add("exporta", m.group(1))
    for m in re.finditer(r"\bexports\.(\w+)\s*=|export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let|var)\s+(\w+)", code):
        node.add("exporta", m.group(1) or m.group(2))
    for m in re.finditer(r"(?m)^(?:export\s+)?(?:async\s+)?function\s+(\w+)|^(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>", code):
        node.add("funciones", m.group(1) or m.group(2))
    # Express: rutas, montajes, vistas
    is_router = bool(re.search(r"\b(?:express\.)?Router\s*\(", code))
    app_vars = set(re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*(?:express|fastify|koa|new\s+Koa)\s*\(", code))
    for m in re.finditer(r"\b(\w+)\.(get|post|put|patch|delete|all)\(\s*(['\"`])(/[^'\"`]*|\*)\3", code):
        obj, meth, path = m.group(1), m.group(2).upper(), m.group(4)
        if obj in ("req", "res", "axios", "http", "client", "api", "request", "map", "cache", "headers"):
            continue
        router = obj not in app_vars and (is_router or obj.lower().endswith("router"))
        red.routes.append(Route(meth, path, rel, _line_at(code, m.start()), router))
    for m in re.finditer(r"\b(\w+)\.use\(\s*(?:(['\"`])([^'\"`]*)\2\s*,\s*)?(?:[\w.]+\s*,\s*)*"
                         r"(?:require\(\s*['\"]([^'\"]+)['\"]\s*\)|(\w+))(?=\s*[,)])", code):
        prefix, spec, var = m.group(3) or "", m.group(4), m.group(5)
        target = _resolve_js(fileset, rel, spec) if spec and spec.startswith(".") else var_mod.get(var or "")
        if target and not target.startswith("npm:"):
            red.mounts.append((rel, prefix, target, m.group(0)))
    for m in re.finditer(r"\bres\.render\(\s*['\"]([^'\"]+)['\"]\s*(,\s*)?", code):
        keys: set[str] | None = set()
        after = m.end()
        if m.group(2):
            if after < len(code) and code[after] == "{":
                keys = _object_keys(_balanced(code, after) or "")
            else:
                keys = None
        red.renders.append((rel, _line_at(code, m.start()), m.group(1), keys))
        node.add("vistas", m.group(1))
    for m in re.finditer(r"\b(?:res|app)\.locals\.(\w+)\s*=", code):
        red.locals_global.add(m.group(1))
    for m in re.finditer(r"\bres\.redirect\(\s*(?:\d+\s*,\s*)?(['\"`])([^'\"`]+)\1", code):
        if m.group(2) != "back":
            red.uses.append(Use("GET", _norm_path(m.group(2)), rel, "redirección"))
    _fetch_uses(red, rel, code)
    for m in re.finditer(r"process\.env\.([A-Z_][A-Z0-9_]*)|process\.env\[\s*['\"]([A-Z_][A-Z0-9_]*)['\"]\s*\]"
                         r"|import\.meta\.env\.([A-Z_][A-Z0-9_]*)", code):
        _env_used(red, rel, m.group(1) or m.group(2) or m.group(3))
    _sql_tables_used(red, rel, text)
    for m in re.finditer(r"tableName\s*:\s*['\"](\w+)['\"]", code):
        red.lib_tables.append(("", m.group(1), rel))


def _fetch_uses(red: Red, rel: str, code: str):
    for m in re.finditer(r"\bfetch\(\s*(['\"`])([^'\"`]+)\1([^;]{0,240})", code):
        meth = re.search(r"method\s*:\s*['\"](\w+)['\"]", m.group(3))
        red.uses.append(Use((meth.group(1) if meth else "GET").upper(), _norm_path(m.group(2)), rel, "fetch"))
    for m in re.finditer(r"\baxios\.(get|post|put|patch|delete)\(\s*(['\"`])([^'\"`]+)\2", code):
        red.uses.append(Use(m.group(1).upper(), _norm_path(m.group(3)), rel, "fetch"))


def _parse_py(red: Red, rel: str, text: str, fileset: set[str]):
    node = red.nodes[rel]
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        node.add("error de sintaxis", f"línea {e.lineno}")
        red.avisos.append(f"{rel}: error de sintaxis en la línea {e.lineno} ({e.msg})")
        return
    base = posixpath.dirname(rel)
    pkgs = []

    def resolve(mod: str, level: int) -> str | None:
        parts = mod.split(".") if mod else []
        dirs = []
        if level:
            d = base
            for _ in range(level - 1):
                d = posixpath.dirname(d)
            dirs.append(d)
        else:
            dirs += ["", base]
        for d in dirs:
            p = posixpath.join(d, *parts) if parts else d
            for cand in (p + ".py", posixpath.join(p, "__init__.py")):
                cand = cand.lstrip("/")
                if cand in fileset:
                    return cand
        return None

    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.add("funciones", n.name)
        elif isinstance(n, ast.ClassDef):
            node.add("clases", n.name)
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                r = resolve(a.name, 0)
                node.add("usa", r) if r else pkgs.append(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            r = resolve(n.module or "", n.level)
            if r and not r.endswith("__init__.py"):
                node.add("usa", r)
                continue
            found = [x for x in (resolve(((n.module + ".") if n.module else "") + a.name, n.level)
                                 for a in n.names) if x]          # from paquete import modulo
            if found or r:
                node.add("usa", *(found or [r]))
            elif n.level:
                red.avisos.append(f"{rel}: importa «{'.' * n.level}{n.module or ''}», que no existe en el proyecto")
            else:
                pkgs.append((n.module or "").split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.args \
                        and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str) \
                        and d.args[0].value.startswith("/"):
                    attr = d.func.attr.lower()
                    if attr in ("get", "post", "put", "patch", "delete"):
                        methods = [attr.upper()]
                    elif attr in ("route", "api_route"):
                        methods = ["GET"]
                        for kw in d.keywords:
                            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                                methods = [str(e.value).upper() for e in kw.value.elts if isinstance(e, ast.Constant)]
                    else:
                        continue
                    for meth in methods:
                        red.routes.append(Route(meth, d.args[0].value, rel, n.lineno, False))
        elif isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            arg0 = n.args[0].value if n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str) else None
            if name == "render_template" and arg0:
                node.add("vistas", arg0)
                red.renders.append((rel, n.lineno, arg0, None))
            elif name in ("getenv",) and arg0 or (name == "get" and arg0 and isinstance(f, ast.Attribute)
                                                  and isinstance(f.value, ast.Attribute) and f.value.attr == "environ"):
                _env_used(red, rel, arg0)
            elif name == "redirect" and arg0 and arg0.startswith("/"):
                red.uses.append(Use("GET", _norm_path(arg0), rel, "redirección"))
        elif isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute) and n.value.attr == "environ" \
                and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
            _env_used(red, rel, n.slice.value)
    std = {"os", "sys", "re", "json", "time", "math", "typing", "pathlib", "dataclasses", "collections", "itertools",
           "functools", "subprocess", "datetime", "random", "logging", "shutil", "argparse", "asyncio", "abc", "enum",
           "io", "copy", "hashlib", "uuid", "tempfile", "threading", "__future__", "unittest", "string", "struct"}
    ext = [p for p in dict.fromkeys(pkgs) if p and p not in std]
    if ext:
        node.add("paquetes", *ext)
    _sql_tables_used(red, rel, text)


def _parse_sql(red: Red, rel: str, text: str, fileset: set[str]):
    node = red.nodes[rel]
    for m in re.finditer(r"CREATE\s+(?:UNLOGGED\s+|TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                         r"(?:[\"`]?\w+[\"`]?\.)?[\"`]?(\w+)[\"`]?\s*\(", text, re.I):
        body = _balanced(text, m.end() - 1, "(", ")") or ""
        cols = []
        for part in _split_top(body):
            w = re.match(r"\s*[\"`]?(\w+)", part)
            if w and w.group(1).upper() not in CONSTRAINTS:
                cols.append(w.group(1))
        name = m.group(1)
        red.tables[name.lower()] = (rel, cols)
        node.add("tablas", f"{name}({', '.join(cols[:8])}{'…' if len(cols) > 8 else ''})")
    for m in re.finditer(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?(\w+)", text, re.I):
        node.add("índices", m.group(1))


_TAG = r"<{}\b((?:<%.*?%>|\{{\{{.*?\}}\}}|[^>])*)>"     # etiqueta que puede llevar <%= … %> o {{ … }} dentro


def _parse_tpl(red: Red, rel: str, text: str, fileset: set[str]):
    node = red.nodes[rel]
    base = posixpath.dirname(rel)
    for m in re.finditer(r"include\(\s*['\"]([^'\"]+)['\"]|\{%\s*(?:include|extends|import)\s+['\"]([^'\"]+)['\"]", text):
        spec = m.group(1) or m.group(2)
        res = _resolve_tpl(red, fileset, rel, spec)
        red.includes.append((rel, spec, res))
        node.add("incluye", res or spec)
    for m in re.finditer(_TAG.format("form"), text, re.I | re.S):
        attrs = m.group(1)
        act = re.search(r"\baction\s*=\s*(['\"])((?:<%.*?%>|.)*?)\1", attrs, re.S)
        meth = re.search(r"\bmethod\s*=\s*['\"]?(\w+)", attrs)
        if act and act.group(2).strip():
            method = (meth.group(1) if meth else "GET").upper()
            path = _norm_path(act.group(2).strip())
            red.uses.append(Use(method, path, rel, "formulario"))
            node.add("formularios", f"{method} {path}")
    for m in re.finditer(r"<(?:link|script|img|source)\b(?:<%.*?%>|[^>])*?\b(?:href|src)\s*=\s*(['\"])([^'\"]+)\1",
                         text, re.I | re.S):
        ref = m.group(2).strip()
        if re.match(r"(?:[a-z]+:)?//|data:|mailto:|#|<%|\{\{", ref, re.I):
            continue
        ext = ref.split("?")[0].rsplit(".", 1)[-1].lower() if "." in ref.rsplit("/", 1)[-1] else ""
        if ext in STATIC_EXT:
            red.static_refs.append((rel, ref.split("?")[0]))
            node.add("enlaza", ref.split("?")[0])
    for m in re.finditer(r"<a\b(?:<%.*?%>|[^>])*?\bhref\s*=\s*(['\"])(/(?:<%.*?%>|[^'\"])*)\1", text, re.I | re.S):
        p = m.group(2)
        last = p.split("?")[0].rsplit("/", 1)[-1]
        if "." not in last:
            red.uses.append(Use("GET", _norm_path(p), rel, "enlace"))
    for sm in re.finditer(r"<script\b[^>]*>(.*?)</script>", text, re.I | re.S):
        _fetch_uses(red, rel, sm.group(1))
    if node.kind == "ejs":
        used = _ejs_vars(text)
        red.tpl_vars[rel] = used
        if used:
            node.add("variables", *sorted(used))


def _resolve_tpl(red: Red, fileset: set[str], rel: str, spec: str) -> str | None:
    base = posixpath.dirname(rel)
    ext = Path(rel).suffix
    cands = []
    for d in [base, *red.views_dirs, "templates", ""]:
        p = _clean(posixpath.join(d, spec))
        cands += [p, p + ext]
    return next((c for c in cands if c in fileset), None)


def _ejs_vars(text: str) -> set[str]:
    """Variables «libres» que usa una plantilla EJS (las que deben llegar de res.render o res.locals)."""
    code = "\n".join(m.group(1) for m in re.finditer(r"<%[=_-]?(.*?)[-_]?%>", text, re.S))
    code = re.sub(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`", "''", code)
    declared = set(re.findall(r"\b(?:const|let|var|function)\s+(\w+)", code))
    for m in re.finditer(r"\(\s*([\w\s,]*)\)\s*=>|\b(\w+)\s*=>|function\s*\w*\s*\(([\w\s,]*)\)", code):
        for x in re.split(r"[\s,]+", m.group(1) or m.group(2) or m.group(3) or ""):
            if x:
                declared.add(x)
    for m in re.finditer(r"\bfor\s*\(\s*(?:const|let|var)\s+(\w+)", code):
        declared.add(m.group(1))
    optional = set(re.findall(r"typeof\s+(\w+)", code)) | set(re.findall(r"locals\.(\w+)", code))
    used = set()
    for m in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)(?!\s*:(?!:))", code):
        w = m.group(1)
        if w not in JS_WORDS and w not in declared and w not in optional and not w.startswith("__"):
            used.add(w)
    return used


def _parse_dockerfile(red: Red, rel: str, text: str, fileset: set[str]):
    node = red.nodes[rel]
    copies = []
    for line in re.sub(r"\\\n", " ", text).splitlines():
        s = line.strip()
        m = re.match(r"(FROM|WORKDIR|EXPOSE|CMD|ENTRYPOINT|COPY|ADD)\s+(.*)", s, re.I)
        if not m:
            continue
        op, rest = m.group(1).upper(), m.group(2).strip()
        if op == "FROM":
            node.add("base", rest.split()[0])
        elif op == "EXPOSE":
            node.add("expone", *rest.split())
        elif op in ("CMD", "ENTRYPOINT"):
            try:
                cmd = " ".join(json.loads(rest)) if rest.startswith("[") else rest
            except ValueError:
                cmd = rest
            node.add("arranca", cmd)
        elif op in ("COPY", "ADD"):
            if "--from" in rest:
                continue
            try:
                args = json.loads(rest) if rest.startswith("[") else rest.split()
            except ValueError:
                args = rest.split()
            args = [a for a in args if not a.startswith("--")]
            if len(args) >= 2:
                copies += args[:-1]
                node.add("copia", *args[:-1])
    red.docker.append((rel, copies))


def _parse_compose(red: Red, rel: str, text: str):
    """Lector mínimo de docker-compose (sin PyYAML): servicio → {clave: [valores]}."""
    red.compose_rel = rel
    services: dict[str, dict] = {}
    in_services, svc_indent, svc = False, None, None
    key, key_indent, child_indent = None, 0, None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        ind = len(raw) - len(raw.lstrip())
        s = raw.strip()
        if ind == 0:
            in_services, svc = s.startswith("services:"), None
            continue
        if not in_services:
            continue
        if svc_indent is None:
            svc_indent = ind
        if ind == svc_indent:
            svc = s.rstrip(":").strip().strip("'\"")
            services[svc], key = {}, None
            continue
        if svc is None:
            continue
        m = re.match(r"([\w.-]+)\s*:\s*(.*)$", s)
        if key is not None and ind > key_indent:
            if child_indent is None:
                child_indent = ind
            if s.startswith("- "):                 # lista: - valor
                services[svc].setdefault(key, []).append(s[2:].strip().strip("'\""))
            elif m and ind == child_indent:        # mapa: clave: valor (depends_on, environment, build…)
                if key == "build" and m.group(1) == "context":
                    services[svc]["build"] = [m.group(2).strip().strip("'\"")]
                elif key == "environment":
                    services[svc].setdefault(key, []).append(m.group(1))
                elif key != "build":
                    services[svc].setdefault(key, []).append(m.group(1))
            continue
        if m:
            key, key_indent, child_indent = m.group(1), ind, None
            val = m.group(2).strip().strip("'\"")
            if val and not val.startswith(("{", "[")):
                services[svc].setdefault(key, []).append(val)
            elif val.startswith("["):
                services[svc].setdefault(key, []).extend(x.strip().strip("'\"") for x in val.strip("[]").split(",") if x.strip())
    red.compose = services
    for name, d in services.items():
        for k in ("environment",):
            for v in d.get(k, []):
                red.env_defined.add(v.split("=")[0].split(":")[0].strip())
        if d.get("environment"):
            red.env_sources.append(f"{rel}:{name}")
    node = red.nodes[rel]
    for name, d in services.items():
        bits = [b for b in (d.get("image", [""])[0] or (("build " + d["build"][0]) if d.get("build") else ""),) if b]
        if d.get("ports"):
            bits.append("puertos " + " ".join(d["ports"]))
        if d.get("depends_on"):
            bits.append("depende de " + " ".join(d["depends_on"]))
        if d.get("env_file"):
            bits.append("env_file " + " ".join(d["env_file"]))
        node.add("servicios", f"{name}({', '.join(bits)})")


def _parse_env(red: Red, rel: str, text: str):
    names = re.findall(r"(?m)^\s*(?:export\s+)?([A-Za-z_]\w*)\s*=", text)
    red.env_defined.update(names)
    red.env_sources.append(rel)
    red.nodes[rel].add("variables", *names)


def _parse_package(red: Red, rel: str, text: str):
    node = red.nodes[rel]
    try:
        data = json.loads(text)
    except ValueError as e:
        red.avisos.append(f"{rel}: JSON no válido ({e})")
        return
    if data.get("main"):
        node.add("main", data["main"])
    for k, v in (data.get("scripts") or {}).items():
        node.add("scripts", f"{k}={v}")
        red.scripts.append((rel, k, str(v)))
    deps = list((data.get("dependencies") or {}).keys())
    red.npm_deps.update(deps)
    red.npm_deps.update((data.get("devDependencies") or {}).keys())
    if deps:
        node.add("dependencias", *deps)


def _parse_pip(red: Red, rel: str, text: str, fileset: set[str]):
    pk = [re.split(r"[<>=!~\[; ]", l.strip())[0] for l in text.splitlines() if l.strip() and not l.startswith(("#", "-"))]
    red.nodes[rel].add("paquetes", *pk)


def _parse_md(red: Red, rel: str, text: str, fileset: set[str]):
    clean = re.sub(r"```.*?```", "", text, flags=re.S)
    red.nodes[rel].add("secciones", *[h.strip() for h in re.findall(r"(?m)^#{1,2}\s+(.+)$", clean)])


def _parse_css(red: Red, rel: str, text: str, fileset: set[str]):
    node = red.nodes[rel]
    n_vars = len(set(re.findall(r"(--[\w-]+)\s*:", text)))
    if n_vars:
        node.add("variables CSS", str(n_vars))
    if "prefers-color-scheme" in text:
        node.add("modo", "claro/oscuro")
    media = len(re.findall(r"@media", text))
    if media:
        node.add("@media", str(media))


def _parse_lai(red: Red, rel: str, text: str, fileset: set[str]):
    red.nodes[rel].add("componentes", *dict.fromkeys(re.findall(r"<([a-z][\w-]*)\b", text)))


# ---------------------------------------------------------------------------
# Rutas, stack y comprobaciones
# ---------------------------------------------------------------------------

def _resolve_routes(red: Red):
    mounted: dict[str, list[str]] = {}
    for src, prefix, target, _ in red.mounts:
        mounted.setdefault(target, []).append(prefix)
        red.nodes[src].add("monta", f"{prefix or '/'}={target}")
    for r in red.routes:
        prefixes = mounted.get(r.rel, [""]) if r.router else [""]
        for p in prefixes:
            full = _join(p, r.path)
            red.full_routes.append((r.method, full, r.rel, r.line))
            red.nodes[r.rel].add("rutas", f"{r.method} {full}")


def _stack_and_commands(red: Red):
    deps = red.npm_deps
    st = []
    if any(n.kind == "js" for n in red.nodes.values()) or deps:
        fw = [d for d in ("express", "fastify", "koa", "@nestjs/core", "next", "react", "vue", "svelte", "ejs", "pug")
              if d in deps]
        st.append("Node.js" + (f" ({', '.join(fw)})" if fw else ""))
    py = [n for n in red.nodes.values() if n.kind == "py"]
    if py:
        pk = {p.lower() for n in red.nodes.values() for p in n.facts.get("paquetes", [])}
        fw = [d for d in ("flask", "fastapi", "django", "streamlit", "torch", "pandas") if d in pk]
        st.append("Python" + (f" ({', '.join(fw)})" if fw else ""))
    images = " ".join(v for d in red.compose.values() for v in d.get("image", []))
    for keys, name in ((("pg", "postgres", "pg-promise"), "PostgreSQL"), (("mysql", "mysql2"), "MySQL"),
                       (("sqlite3", "better-sqlite3"), "SQLite"), (("mongoose", "mongodb"), "MongoDB"),
                       (("redis", "ioredis"), "Redis")):
        if any(k in deps for k in keys) or any(k in images for k in keys if len(k) > 3):
            st.append(name)
    if red.compose:
        st.append("Docker Compose")
    elif red.docker:
        st.append("Docker")
    red.stack = list(dict.fromkeys(st))
    cmds = []
    for rel, name, _ in red.scripts:
        if "/" not in rel:          # solo los del package.json de la raíz
            cmds.append("npm start" if name == "start" else "npm test" if name == "test" else f"npm run {name}")
    if red.compose:
        cmds.append("docker compose up --build")
    if "requirements.txt" in red.nodes:
        cmds.append("pip install -r requirements.txt")
    red.commands = cmds[:6]


def _checks(red: Red, fileset: set[str]):
    av = red.avisos
    # imports relativos que no existen
    for rel, spec, res in red.requires:
        if res is None:
            av.append(f"{rel}: requiere «{spec}», que no existe")
    # routers sin montar
    mounted_targets = {t for _, _, t, _ in red.mounts}
    for rel in dict.fromkeys(r.rel for r in red.routes if r.router):
        if rel not in mounted_targets:
            av.append(f"{rel} define rutas, pero ningún archivo lo monta con app.use(…)")
    # rutas duplicadas
    seen: dict[tuple, tuple] = {}
    for meth, full, rel, line in red.full_routes:
        k = (meth, full)
        if k in seen and seen[k][0] != rel:
            av.append(f"{meth} {full} está definida dos veces ({seen[k][0]}:{seen[k][1]} y {rel}:{line}): "
                      "solo responde la primera que se registre")
        seen.setdefault(k, (rel, line))
    # usos (formularios, fetch, redirecciones, enlaces) sin ruta
    if red.full_routes:
        static_files = _static_paths(red, fileset)
        compiled = [(m, p, _route_regex(p), rel) for m, p, rel, _ in red.full_routes]
        done = set()
        for u in red.uses:
            if u.path.startswith(":x") or not u.path.startswith("/") or u.path in static_files:
                continue
            key = (u.method, u.path, u.rel)
            if key in done:
                continue
            done.add(key)
            ok = any((m == u.method or m == "ALL" or (u.method == "HEAD" and m == "GET")) and rx.match(u.path)
                     for m, _, rx, _ in compiled)
            if ok:
                continue
            hint = ""
            for m, p, rx, rel in compiled:      # ¿coincide con el final? → falta un prefijo de montaje
                if m != u.method or p == "/":
                    continue
                segs = u.path.strip("/").split("/")
                for i in range(1, len(segs)):
                    if rx.match("/" + "/".join(segs[i:])):
                        hint = (f" (¿falta montar {rel} con el prefijo /{'/'.join(segs[:i])}? hoy esa ruta es {m} {p})")
                        break
                if hint:
                    break
            if not hint and u.method != "GET":
                same = [f"{p}" for m, p, _, _ in compiled if m == u.method]
                if same:
                    hint = f" (rutas {u.method} existentes: {_cut(same, 4)})"
            av.append(f"{u.rel}: {u.how} {u.method} {u.path} → no hay ninguna ruta que lo atienda{hint}")
    # vistas y parciales (las variables que faltan se agrupan por archivo y vista)
    lacking: dict[tuple, dict] = {}
    for rel, line, view, keys in red.renders:
        vf = _view_file(red, fileset, view)
        if vf is None:
            av.append(f"{rel}:{line}: renderiza «{view}», pero no existe esa vista en {'/'.join(sorted(red.views_dirs))}/")
            continue
        if keys is None or not vf.endswith(".ejs"):
            continue
        missing = _vars_with_partials(red, vf, set()) - keys - red.locals_global
        if missing:
            g = lacking.setdefault((rel, view, vf), {"lines": [], "vars": set()})
            g["lines"].append(str(line))
            g["vars"] |= missing
    for (rel, view, vf), g in lacking.items():
        in_partial = not g["vars"] <= red.tpl_vars.get(vf, set())
        av.append(f"{rel} (línea {_cut(g['lines'], 4)}): res.render('{view}') no pasa {_cut(sorted(g['vars']), 5)}, "
                  f"que usa {vf}" + (" o uno de sus parciales" if in_partial else "") + " → error 500 al abrirla")
    for rel, spec, res in red.includes:
        if res is None:
            av.append(f"{rel}: incluye «{spec}», que no existe")
    # CSS/JS/imágenes enlazados
    for rel, ref in red.static_refs:
        if not _static_exists(red, fileset, rel, ref):
            near = [f for f in fileset if f.rsplit("/", 1)[-1].split(".")[0] == ref.rsplit("/", 1)[-1].split(".")[0]
                    and f.rsplit(".", 1)[-1] == ref.rsplit(".", 1)[-1]][:2]
            av.append(f"{rel}: enlaza «{ref}», que no existe" + (f" (¿{', '.join(near)}?)" if near else ""))
    # variables de entorno
    if red.env_sources:
        for name, rels in sorted(red.env_used.items()):
            if name not in red.env_defined and name not in ENV_BUILTIN:
                av.append(f"{_cut(rels, 2)} usa la variable de entorno {name}, que no está en "
                          f"{_cut(sorted(set(s.split(':')[0] for s in red.env_sources)), 3)}")
    # tablas
    if red.tables:
        for t, rels in sorted(red.tables_used.items()):
            if t not in red.tables and t not in {"information_schema", "pg_catalog", "sqlite_master"}:
                av.append(f"{_cut(rels, 2)} consulta la tabla «{t}», que no se crea en ningún .sql "
                          f"(hay: {_cut(sorted(red.tables), 5)})")
    for pkg, table, cols in KNOWN_TABLES:
        if pkg not in red.npm_deps:
            continue
        names = [t for p, t, _ in red.lib_tables] or [table]
        for name in names:
            info = red.tables.get(name.lower())
            if info and not cols <= {c.lower() for c in info[1]}:
                av.append(f"{info[0]}: la tabla «{name}» no tiene las columnas que exige {pkg} "
                          f"({', '.join(sorted(cols))}; tiene {', '.join(info[1]) or 'ninguna'})")
            elif info is None and red.tables:
                av.append(f"{pkg} guarda las sesiones en la tabla «{name}», pero ningún .sql la crea")
    # Docker
    for rel, srcs in red.docker:
        ctx = _docker_context(red, rel)
        for s in srcs:
            if s in (".", "./"):
                continue
            pat = _clean(posixpath.join(ctx, s) if ctx else s)
            if not any(fnmatch.fnmatch(f, pat) or f.startswith(pat.rstrip("/") + "/") for f in fileset):
                base = s.rstrip("/").rsplit("/", 1)[-1]
                near = [f for f in fileset if fnmatch.fnmatch(f.rsplit("/", 1)[-1], base)][:2]
                av.append(f"{rel}: COPY {s} no encuentra nada en {ctx or 'la raíz'}"
                          + (f" (hay {', '.join(near)})" if near else ""))
    if red.compose:
        for name, d in red.compose.items():
            for ef in d.get("env_file", []):
                if _clean(ef) not in fileset:
                    ex = [f for f in fileset if f.startswith(".env") and f != _clean(ef)]
                    av.append(f"{red.compose_rel}: el servicio {name} usa env_file {ef}, que no existe"
                              + (f": créalo copiando {ex[0]}" if ex else ""))
            for vol in d.get("volumes", []):
                host = vol.split(":")[0]
                if host.startswith(".") and host not in (".", "./"):
                    h = _clean(host)
                    if h not in fileset and not any(f.startswith(h + "/") for f in fileset):
                        av.append(f"{red.compose_rel}: el servicio {name} monta {host}, que no existe")
    for rel, name, cmd in red.scripts:
        base = posixpath.dirname(rel)
        for m in re.finditer(r"\b(?:node|nodemon|ts-node|tsx)\s+([\w./-]+\.\w+)", cmd):
            target = _clean(posixpath.join(base, m.group(1)))
            if target not in fileset:
                where = f" desde {base}/" if base else ""
                av.append(f"{rel}: el script «{name}» ejecuta {m.group(1)}, que no existe{where}"
                          + (" (¿package.json debería estar en la raíz?)" if base and m.group(1) in fileset else ""))
    red.avisos = list(dict.fromkeys(av))


def _static_paths(red: Red, fileset: set[str]) -> set[str]:
    out = set()
    for f in fileset:
        for d in red.static_dirs:
            if f.startswith(d.rstrip("/") + "/"):
                out.add("/" + f[len(d.rstrip("/")) + 1:])
    return out


def _static_exists(red: Red, fileset: set[str], rel: str, ref: str) -> bool:
    if ref.startswith("/"):
        cands = [posixpath.join(d, ref.lstrip("/")) for d in red.static_dirs] + [ref.lstrip("/")]
        if not red.static_dirs and any(rel.startswith(v.rstrip("/") + "/") for v in red.views_dirs):
            return True           # vistas sin carpeta estática conocida: no se puede saber
    else:
        if any(rel.startswith(v.rstrip("/") + "/") for v in red.views_dirs) and red.static_dirs:
            cands = [posixpath.normpath(posixpath.join(d, ref)) for d in red.static_dirs]
        else:
            cands = [posixpath.normpath(posixpath.join(posixpath.dirname(rel), ref))]
    return any(_clean(c) in fileset for c in cands)


def _view_file(red: Red, fileset: set[str], view: str) -> str | None:
    exts = ("", ".ejs", ".html", ".pug", ".hbs", ".njk")
    for d in [*red.views_dirs, "templates", ""]:
        for ext in exts:
            c = _clean(posixpath.join(d, view + ext))
            if c in fileset:
                return c
    # carpeta de plantillas dentro de un paquete (app/templates/x.html) o de vistas anidada
    for ext in exts:
        tail = "/" + view.lstrip("/") + ext
        hits = sorted(f for f in fileset if f.endswith(tail) and any(
            f"/{d}/" in "/" + f for d in (*red.views_dirs, "templates")))
        if hits:
            return hits[0]
    return None


def _vars_with_partials(red: Red, rel: str, seen: set[str]) -> set[str]:
    if rel in seen:
        return set()
    seen.add(rel)
    out = set(red.tpl_vars.get(rel, set()))
    for src, _, res in red.includes:
        if src == rel and res:
            out |= _vars_with_partials(red, res, seen)
    return out


def _docker_context(red: Red, dockerfile: str) -> str:
    base = posixpath.dirname(dockerfile)
    for d in red.compose.values():
        for b in d.get("build", []):
            ctx = _clean(posixpath.join(posixpath.dirname(red.compose_rel), b))
            if ctx in ("", ".") or ctx == base:
                return "" if ctx in ("", ".") else ctx
    return base


# ---------------------------------------------------------------------------
# Clay-Myrrh.md
# ---------------------------------------------------------------------------

def strip_section(md: str) -> str:
    """El texto de Clay-Myrrh.md sin la red (cuando la red va aparte, siempre actualizada)."""
    return _SECTION.sub("\n", md).strip()


def has_section(md: str) -> bool:
    return bool(_SECTION.search(md))


def strip_marks(text: str) -> str:
    """La red sin las marcas HTML (para el prompt: no aportan nada al modelo)."""
    return "\n".join(l for l in text.splitlines() if l not in (MARK_START, MARK_END))


TEMPLATE = """# {name} · Clay-Myrrh

Instrucciones del proyecto para Clay-Myrrh (como CLAUDE.md o GEMINI.md): el modelo las lee siempre que
trabaja en esta carpeta. Escribe aquí el objetivo, las convenciones y lo que no se debe tocar.
La red de abajo la genera el programa con /grafo y se actualiza sola.

## Instrucciones
- (añade aquí tus notas)
"""


def update_file(path: Path, red: Red) -> bool:
    """Crea o actualiza la red dentro de Clay-Myrrh.md. Devuelve True si el archivo cambió."""
    old = path.read_text(encoding="utf-8") if path.exists() else TEMPLATE.format(name=path.parent.name)
    body = strip_section(old)
    new = body.rstrip() + "\n\n" + red.text() + "\n"
    if new == old:
        return False
    path.write_text(new, encoding="utf-8", newline="\n")
    return True


def delta(prev: dict[str, str], prev_avisos: list[str], red: Red) -> str:
    """Cambios de la red desde la última vez que la vio el modelo (para no reenviarla entera)."""
    cur = red.node_lines()
    out = []
    for rel, line in cur.items():
        if prev.get(rel) != line:
            out.append(("~ " if rel in prev else "+ ") + line)
    for rel in prev:
        if rel not in cur:
            out.append(f"− {rel} (ya no existe)")
    if red.avisos != prev_avisos:
        out.append("Avisos actuales:" if red.avisos else "Avisos: ninguno (todo encaja)")
        out += [f"  - {a}" for a in red.avisos[:MAX_AVISOS]]
    if not out:
        return ""
    return f"[Red del proyecto: cambios desde tu último mensaje · {time.strftime('%H:%M')}]\n" + "\n".join(out)
