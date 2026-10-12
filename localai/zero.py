"""
Prompt-Zero (/zero): crear una página web desde cero de forma guiada.

1. Cuestionario (opciones limitadas a propósito): tipo de web, frontend, login, base
   de datos, backend (solo si hace falta), estilo, secciones e idioma.
2. Plan de archivos determinista según las respuestas, con tokens estimados.
3. Análisis de tokens del modelo cargado (contexto, salida máxima, velocidad) y
   reparto en TAREAS que caben en una respuesta. Se ajusta solo el máximo de tokens
   de cada tarea; no hace falta tocar /tokens.
4. Ejecución tipo "Goal": tarea 0 = CONTRATO.md (ids, clases, endpoints, datos) que
   siguen todas las demás; cada tarea recibe especificación + contrato + un resumen
   de lo ya creado (no todo el historial). Archivos incompletos se reintentan.
   El estado se guarda en zero.json: /zero continuar retoma un proyecto a medias.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import files as filemod
from . import render
from . import webkit
from .config import APP_DIR
from .engine import EngineError, GenParams
from .render import console, error, ok, say, warn

PROJECTS_DIR = APP_DIR / "proyectos"

TYPES = [
    ("landing", "Landing / presentación", "una página con secciones para un negocio o producto"),
    ("restaurante", "Restaurante / cafetería", "carta con precios, horario, reservas y ubicación"),
    ("servicios", "Negocio con citas", "peluquería, clínica, taller…: servicios, equipo y reservas"),
    ("evento", "Evento / lanzamiento", "cuenta regresiva, agenda, inscripción y preguntas"),
    ("curso", "Curso / academia", "temario, profesores, test, precios y matrícula"),
    ("ong", "ONG / campaña", "la causa, metas de recaudación y cómo colaborar"),
    ("portafolio", "Portafolio personal", "presentarte, tus proyectos y contacto"),
    ("blog", "Blog", "listado de entradas, entrada individual, categorías"),
    ("tienda", "Tienda / catálogo", "productos, filtros y carrito"),
    ("panel", "Panel / dashboard", "indicadores, gráficos y tablas"),
    ("productividad", "App de productividad", "tablero kanban, tareas, calendario y gastos"),
    ("app", "App de gestión (CRUD)", "listar, crear, editar y borrar registros"),
]
SECTIONS = {
    "landing": ["Hero con llamada a la acción", "Logos de clientes", "Servicios / características",
                "Características con imagen", "Beneficios", "Testimonios", "Precios", "Comparativa de planes",
                "Preguntas frecuentes", "Formulario de contacto", "Pie de página"],
    "restaurante": ["Hero con llamada a la acción", "Carta con precios", "Horario (abierto ahora)", "Galería del local",
                    "Equipo", "Opiniones", "Reserva de mesa por pasos", "Mapa", "Pie de página"],
    "servicios": ["Hero con llamada a la acción", "Servicios con precios", "Equipo", "Antes y después",
                  "Opiniones", "Horario (abierto ahora)", "Reserva de cita por pasos", "Calculadora de presupuesto",
                  "Mapa", "Pie de página"],
    "evento": ["Hero con llamada a la acción", "Cuenta regresiva", "Ponentes / artistas", "Agenda (calendario)",
               "Entradas y precios", "Inscripción por pasos", "Preguntas frecuentes", "Mapa", "Pie de página"],
    "curso": ["Hero con llamada a la acción", "Temario en pestañas", "Profesores", "Test de nivel", "Precios",
              "Opiniones", "Matrícula por pasos", "Preguntas frecuentes", "Pie de página"],
    "ong": ["Hero con llamada a la acción", "Nuestra causa", "Metas de recaudación", "Cifras de impacto",
            "Historias (testimonios)", "Hazte voluntario (formulario)", "Boletín", "Pie de página"],
    "portafolio": ["Hero", "Sobre mí", "Proyectos (galería con filtro)", "Habilidades", "Experiencia",
                   "Formulario de contacto", "Pie de página"],
    "blog": ["Cabecera y menú", "Listado de entradas", "Entrada individual", "Categorías", "Buscador",
             "Suscripción al boletín", "Encuesta a los lectores", "Pie de página"],
    "tienda": ["Cabecera con carrito", "Catálogo con filtros", "Ficha de producto", "Carrito",
               "Checkout simulado", "Pie de página"],
    "panel": ["Menú lateral", "Tarjetas de indicadores", "Gráfico de barras (SVG, sin librerías)",
              "Gráfico de líneas", "Gráfico circular", "Tabla con búsqueda y orden", "Metas y progreso",
              "Ajustes de usuario"],
    "productividad": ["Menú lateral", "Tablero kanban", "Lista de tareas", "Calendario", "Control de gastos",
                      "Calculadora"],
    "app": ["Listado con búsqueda", "Formulario crear/editar", "Borrar con confirmación", "Filtros",
            "Exportar a CSV"],
}
# extras que sirven para cualquier tipo (no vienen marcados)
EXTRAS = ["Aviso superior (oferta o novedad)", "Botón de WhatsApp y modo oscuro", "Chat de preguntas frecuentes",
          "Aviso de cookies", "Ventana con condiciones / aviso legal", "Presupuesto o factura imprimible"]
FRONT = [
    ("vanilla", "HTML + CSS + JavaScript", "sin instalar nada: se abre con doble clic"),
    ("tailwind", "HTML + Tailwind (CDN)", "clases utilitarias, sin compilar"),
    ("react", "React + Vite", "necesita Node.js (npm install, npm run dev)"),
    ("vue", "Vue 3 + Vite", "necesita Node.js (npm install, npm run dev)"),
]
LOGIN = [
    ("none", "No", ""),
    ("email", "Email y contraseña", "registro, inicio y cierre de sesión"),
    ("google", "Google (OAuth)", "botón 'Entrar con Google'"),
    ("demo", "Simulado (prototipo)", "pantalla de acceso de prueba en el navegador, sin servidor"),
]
DB = [
    ("none", "Ninguna", "contenido fijo en el código"),
    ("local", "localStorage (navegador)", "sin servidor; los datos viven en ese navegador"),
    ("sqlite", "SQLite", "un archivo de base de datos; necesita backend"),
    ("postgres", "PostgreSQL", "servidor de base de datos; necesita backend"),
    ("supabase", "Supabase", "base de datos + login en la nube, sin backend propio"),
]
BACKEND = [
    ("node", "Node.js + Express", "JavaScript también en el servidor"),
    ("python", "Python + Flask", "sencillo y legible"),
]
STYLE = [
    ("minimal", "Minimalista claro", "mucho espacio en blanco, tipografía limpia"),
    ("dark", "Oscuro moderno", "fondo oscuro, acentos de color, glassmorphism suave"),
    ("corporate", "Corporativo", "sobrio, azules, confianza"),
    ("colorful", "Colorido y juvenil", "degradados, formas redondeadas"),
]

BUILD = [
    ("components", "Componentes prediseñados (recomendado)",
     "la IA solo elige componentes y escribe el contenido: ~3–4× menos tokens y diseño probado"),
    ("scratch", "Desde cero", "la IA escribe todo el HTML, CSS y JS (más libre, más lento y más frágil)"),
]
SECTION_COMPONENTS = {
    "Hero con llamada a la acción": "hero", "Servicios / características": "cards", "Beneficios": "cards o stats",
    "Testimonios": "testimonials", "Precios": "pricing (yearly=\"…\" si hay plan anual)", "Preguntas frecuentes": "faq",
    "Formulario de contacto": "contact", "Pie de página": "footer", "Hero": "hero", "Sobre mí": "about",
    "Proyectos (galería con filtro)": "gallery", "Habilidades": "skills", "Experiencia": "timeline",
    "Cabecera y menú": "navbar", "Listado de entradas": "posts", "Entrada individual": "article",
    "Categorías": "posts (filter)", "Buscador": "posts (search)", "Suscripción al boletín": "newsletter",
    "Cabecera con carrito": "navbar (el carrito se añade solo)", "Catálogo con filtros": "products",
    "Ficha de producto": "products (texto de cada item)", "Carrito": "products cart=\"yes\"",
    "Checkout simulado": "cart checkout=\"yes\"", "Menú lateral": "sidebar", "Tarjetas de indicadores": "kpis",
    "Gráfico de barras (SVG, sin librerías)": "chart type=\"bar\"", "Tabla con búsqueda y orden": "table",
    "Ajustes de usuario": "form", "Listado con búsqueda": "crud", "Formulario crear/editar": "crud",
    "Borrar con confirmación": "crud", "Filtros": "crud (búsqueda)", "Exportar a CSV": "crud csv=\"yes\"",
    # nuevos
    "Logos de clientes": "logos variant=\"marquee\"", "Características con imagen": "features",
    "Comparativa de planes": "compare", "Carta con precios": "menu", "Horario (abierto ahora)": "hours",
    "Galería del local": "carousel", "Equipo": "team", "Opiniones": "testimonials",
    "Reserva de mesa por pasos": "wizard", "Mapa": "map", "Servicios con precios": "cards (price en cada item) o menu",
    "Antes y después": "beforeafter", "Reserva de cita por pasos": "wizard", "Calculadora de presupuesto": "calculator",
    "Cuenta regresiva": "countdown", "Ponentes / artistas": "team", "Agenda (calendario)": "calendar",
    "Entradas y precios": "pricing", "Inscripción por pasos": "wizard", "Temario en pestañas": "tabs",
    "Profesores": "team", "Test de nivel": "quiz", "Matrícula por pasos": "wizard", "Nuestra causa": "features",
    "Metas de recaudación": "progress", "Cifras de impacto": "stats", "Historias (testimonios)": "testimonials",
    "Hazte voluntario (formulario)": "form", "Boletín": "newsletter", "Encuesta a los lectores": "poll",
    "Gráfico de líneas": "chart type=\"line\"", "Gráfico circular": "chart type=\"donut\"",
    "Metas y progreso": "progress", "Tablero kanban": "kanban", "Lista de tareas": "todo", "Calendario": "calendar",
    "Control de gastos": "expenses", "Calculadora": "calculator",
    "Acceso de usuarios (simulado)": "login",
    "Aviso superior (oferta o novedad)": "banner", "Botón de WhatsApp y modo oscuro": "float",
    "Chat de preguntas frecuentes": "chatbot", "Aviso de cookies": "cookies",
    "Ventana con condiciones / aviso legal": "modal", "Presupuesto o factura imprimible": "invoice",
}
FRONT_RULES = {
    "vanilla": "HTML, CSS y JavaScript puros: sin Tailwind, Bootstrap ni ninguna librería o CDN.",
    "tailwind": "Tailwind por CDN (<script src=\"https://cdn.tailwindcss.com\"></script>) y JavaScript puro; nada más.",
    "react": "React 18 + Vite, componentes funcionales con hooks, sin librerías de UI.",
    "vue": "Vue 3 + Vite con <script setup>, sin librerías de UI.",
}

SYSTEM = (
    "Eres un desarrollador web senior. Escribes código limpio, moderno, accesible y responsive (móvil primero), "
    "con contenido realista (nada de 'lorem ipsum') en el idioma indicado. Sigues el CONTRATO técnico al pie de la "
    "letra para que todos los archivos encajen entre sí. Respondes solo con los archivos pedidos, completos.\n\n"
    + filemod.FILE_INSTRUCTIONS
)


@dataclass
class FilePlan:
    path: str
    desc: str
    est: int                     # tokens de salida estimados


@dataclass
class Task:
    title: str
    files: list[FilePlan]
    status: str = "pendiente"     # pendiente | hecha | incompleta | fallida

    @property
    def est(self) -> int:
        return sum(f.est for f in self.files)


@dataclass
class Project:
    name: str
    slug: str
    desc: str
    answers: dict
    tasks: list[Task] = field(default_factory=list)
    created: str = ""
    model: str = ""

    @property
    def dir(self) -> Path:
        return PROJECTS_DIR / self.slug

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        (self.dir / "zero.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Project":
        d = json.loads(path.read_text(encoding="utf-8"))
        tasks = [Task(t["title"], [FilePlan(**f) for f in t["files"]], t["status"]) for t in d.pop("tasks")]
        return cls(tasks=tasks, **d)


# ---------------------------------------------------------------------------
# Cuestionario
# ---------------------------------------------------------------------------

def _sel(title, items, step, total, default=0):
    from .inputbox import Choice, select
    return select(f"Prompt-Zero · {step}/{total} · {title}", [Choice(v, l, m) for v, l, m in items],
                  render.current, default)


def questionnaire() -> dict | None:
    total = 8
    try:
        name = console.input("[accent]Nombre del proyecto[/] [dim](p. ej. «Café Aurora»)[/]: ").strip()
        if not name:
            return None
        desc = console.input("[accent]Descríbelo en una frase[/] [dim](qué es y para quién)[/]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    a = {"name": name, "desc": desc}
    a["type"] = _sel("¿Qué tipo de web?", TYPES, 1, total)
    if a["type"] is None:
        return None
    a["front"] = _sel("¿Qué frontend?", FRONT, 2, total)
    if a["front"] is None:
        return None
    a["login"] = _sel("¿Necesita login?", LOGIN, 3, total)
    if a["login"] is None:
        return None
    dbs = DB if a["login"] in ("none", "demo") else [d for d in DB if d[0] not in ("none", "local")]
    a["db"] = _sel("¿Base de datos?", dbs, 4, total)
    if a["db"] is None:
        return None
    needs_backend = a["db"] in ("sqlite", "postgres") or (a["login"] not in ("none", "demo") and a["db"] != "supabase")
    a["build"] = "scratch"
    if not needs_backend and a["login"] in ("none", "demo") and a["db"] in ("none", "local") and a["front"] in ("vanilla", "tailwind"):
        a["build"] = _sel("¿Cómo la construimos?", BUILD, 5, total) or "components"
    a["backend"] = "none"
    if needs_backend:
        a["backend"] = _sel("Hace falta un servidor: ¿con qué?", BACKEND, 5, total)
        if a["backend"] is None:
            return None
    a["style"] = _sel("¿Estilo visual?", STYLE, 6, total)
    if a["style"] is None:
        return None
    from .inputbox import Choice, select_multi
    secs = SECTIONS[a["type"]] + (["Acceso de usuarios (simulado)"] if a["login"] == "demo" else [])
    extras = [e for e in EXTRAS if e not in secs]
    if a.get("build") == "components":
        choices = [Choice(s, s, f"<{SECTION_COMPONENTS.get(s, 'section').split()[0]}>") for s in secs] + \
                  [Choice(e, e, f"extra · <{SECTION_COMPONENTS.get(e, 'section').split()[0]}>") for e in extras]
    else:
        choices = [Choice(s, s) for s in secs] + [Choice(e, e, "extra") for e in extras]
    picked = select_multi(f"Prompt-Zero · 7/{total} · Secciones y funciones (los extras no vienen marcados)", choices,
                          render.current, set(secs))
    if not picked:
        return None
    a["sections"] = picked
    a["lang"] = _sel("Idioma del contenido", [("es", "Español", ""), ("en", "Inglés", "")], 8, total) or "es"
    return a


def spec_text(a: dict) -> str:
    lab = lambda opts, k: next((l for v, l, _ in opts if v == k), k)   # noqa: E731
    lines = [f"Nombre: {a['name']}", f"Descripción: {a['desc'] or '-'}",
             f"Tipo: {lab(TYPES, a['type'])}", f"Frontend: {lab(FRONT, a['front'])}",
             f"Login: {lab(LOGIN, a['login'])}", f"Base de datos: {lab(DB, a['db'])}"]
    if a["backend"] != "none":
        lines.append(f"Backend: {lab(BACKEND, a['backend'])}")
    if a["login"] == "demo":
        lines.append("Login simulado: registro e inicio de sesión guardados en localStorage (solo demostración, sin servidor)")
    if a.get("build") == "components":
        lines.append("Construcción: componentes prediseñados (archivo pagina.lai)")
    else:
        lines.append("Reglas del frontend: " + FRONT_RULES.get(a["front"], ""))
    lines += [f"Estilo: {lab(STYLE, a['style'])} — {next(m for v, _, m in STYLE if v == a['style'])}",
              "Secciones y funciones: " + "; ".join(a["sections"]),
              f"Idioma del contenido: {'español' if a['lang'] == 'es' else 'inglés'}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plan de archivos y tareas
# ---------------------------------------------------------------------------

def file_plan(a: dict) -> list[FilePlan]:
    n = len(a["sections"])
    if a.get("build") == "components":
        return [FilePlan("pagina.lai", "toda la página con componentes y su contenido", 300 + 230 * n)]
    interactive = sum(1 for s in a["sections"] if re.search(r"formulario|filtro|carrito|buscador|crear|borrar|"
                                                             r"checkout|orden|exportar|tabla|gráfico", s, re.I))
    auth = a["login"] != "none"
    data = a["db"] != "none"
    fp: list[FilePlan] = []
    f = a["front"]
    if f in ("vanilla", "tailwind"):
        big = 1.4 if f == "tailwind" else 1.0
        fp.append(FilePlan("index.html", "estructura HTML de todas las secciones", int((1000 + 380 * n) * big)))
        if f == "vanilla":
            fp.append(FilePlan("css/styles.css", "estilos, variables de color, responsive", 900 + 230 * n))
        fp.append(FilePlan("js/app.js", "interactividad, datos y lógica del cliente",
                           500 + 320 * interactive + (600 if auth else 0) + (400 if data else 0)))
        if a["db"] == "supabase":
            fp.append(FilePlan("js/supabase.js", "cliente de Supabase (URL y clave de ejemplo)", 400))
    else:
        ext, comp = ("jsx", "componentes React") if f == "react" else ("vue", "componentes Vue")
        fp += [FilePlan("package.json", "dependencias y scripts", 250),
               FilePlan("vite.config.js", "configuración de Vite", 150),
               FilePlan("index.html", "punto de entrada", 150),
               FilePlan(f"src/main.{'jsx' if f == 'react' else 'js'}", "arranque de la app", 150),
               FilePlan(f"src/App.{ext}", "composición de secciones y rutas", 700 + 100 * n),
               FilePlan("src/index.css" if f == "react" else "src/style.css", "estilos globales", 900 + 160 * n)]
        for s in a["sections"]:
            cname = "".join(w.capitalize() for w in re.findall(r"[a-záéíóúñ]+", s.lower())[:3]) or "Seccion"
            cname = cname.translate(str.maketrans("áéíóúñ", "aeioun"))
            fp.append(FilePlan(f"src/components/{cname}.{ext}", f"componente: {s}", 450))
        if auth or data:
            fp.append(FilePlan("src/lib/api.js", "llamadas al servidor o a Supabase", 500))
    if a["backend"] == "node":
        fp += [FilePlan("server/package.json", "dependencias del servidor", 250),
               FilePlan("server/server.js", "API REST con Express", 900 + (700 if auth else 0) + (600 if data else 0))]
        if a["db"] in ("sqlite", "postgres"):
            fp.append(FilePlan("server/db.js", f"conexión {a['db']} y creación de tablas", 500))
        fp.append(FilePlan("server/.env.example", "variables de entorno de ejemplo", 120))
    elif a["backend"] == "python":
        fp += [FilePlan("backend/requirements.txt", "dependencias", 80),
               FilePlan("backend/app.py", "API con Flask", 1000 + (700 if auth else 0) + (600 if data else 0))]
        if a["db"] in ("sqlite", "postgres"):
            fp.append(FilePlan("backend/db.py", f"conexión {a['db']} y creación de tablas", 500))
        fp.append(FilePlan("backend/.env.example", "variables de entorno de ejemplo", 120))
    if a["db"] == "supabase":
        fp.append(FilePlan("supabase/schema.sql", "tablas y políticas RLS", 450))
    fp.append(FilePlan("README.md", "cómo instalar, ejecutar y configurar", 600))
    return fp


def split_tasks(plan: list[FilePlan], budget: int) -> list[Task]:
    """Agrupa archivos consecutivos de la misma zona mientras quepan en ~70% del presupuesto."""
    if len(plan) == 1 and plan[0].path.endswith(webkit.EXT):
        return [Task("Página con componentes", plan)]
    tasks = [Task("Contrato técnico", [FilePlan("CONTRATO.md", "ids, clases, datos, endpoints y textos clave", 1100)])]
    cur: list[FilePlan] = []

    def area(p):
        return p.split("/")[0] if "/" in p and p.split("/")[0] in ("server", "backend", "supabase") else "front"

    # límites prudentes: los modelos locales pierden calidad y se cortan con respuestas enormes
    cap = min(budget * 0.6, 6000)
    for fpl in plan:
        big = fpl.est > 2500
        if cur and (len(cur) >= 4 or sum(x.est for x in cur) + fpl.est > cap or area(cur[-1].path) != area(fpl.path)
                    or big or cur[-1].est > 2500):
            tasks.append(Task(", ".join(x.path for x in cur), cur))
            cur = []
        cur.append(fpl)
    if cur:
        tasks.append(Task(", ".join(x.path for x in cur), cur))
    return tasks


# ---------------------------------------------------------------------------
# Análisis de tokens
# ---------------------------------------------------------------------------

def measured_tps(app) -> tuple[float, str]:
    st = app.stats
    if st.gen_time > 5 and st.tokens_out > 50:
        return st.tokens_out / st.gen_time, "medida en esta sesión"
    try:
        from . import hardware as H
        info = app.entry.info
        size = info.file_size / 2 ** 30
        label = str(info.meta.get("general.size_label", ""))
        m = re.match(r"([\d.]+)", label)
        params = float(m.group(1)) if m else size
        n_exp, used = info.get("expert_count"), info.get("expert_used_count")
        active = params * (used / n_exp) * 1.3 if n_exp and used else None
        f = H.estimate(H.detect(), size, params, active)
        return f.tps, "estimada por hardware"
    except Exception:
        return 10.0, "supuesta"


def show_analysis(app, proj: Project, budget: int, tps: float, how: str):
    total = sum(t.est for t in proj.tasks)
    tb = Table(border_style="border", padding=(0, 1))
    for c, j in (("#", "right"), ("Tarea", "left"), ("Tokens est.", "right"), ("Tiempo aprox.", "right")):
        tb.add_column(c, justify=j)
    for i, t in enumerate(proj.tasks):
        mins = t.est / max(tps, 0.1) / 60
        tb.add_row(str(i), t.title, f"{t.est:,}".replace(",", "."), f"{mins:.0f} min" if mins >= 1 else "<1 min")
    console.print(Text("Análisis de tokens", style="accent"))
    say(f"  [dim]Modelo[/] {app.engine.name} · [dim]contexto[/] {render.human_tokens(app.n_ctx)} · "
        f"[dim]salida máx. por tarea[/] {render.human_tokens(budget)} · [dim]velocidad[/] {tps:.1f} t/s ({how})")
    nf, nt = sum(len(t.files) for t in proj.tasks), len(proj.tasks)
    lo, hi = total / max(tps, 0.1) / 60 * 0.8, total / max(tps, 0.1) / 60 * 1.5
    tiempo = f"{lo:.0f}–{hi:.0f} min" if hi >= 1.5 else "menos de 1–2 min"
    say(f"  [dim]Proyecto:[/] {nf} archivo{'s' if nf != 1 else ''} ≈ " + f"{total:,}".replace(",", ".")
        + f" tokens de salida en {nt} tarea{'s' if nt != 1 else ''} · [dim]tiempo total ≈[/] {tiempo}")
    console.print(tb)
    if proj.answers.get("build") == "components":
        scratch = sum(f.est for f in file_plan({**proj.answers, "build": "scratch"})) + 1100
        say(f"  [ok]Con componentes:[/] ≈{total:,} tokens frente a ≈{scratch:,} desde cero".replace(",", ".")
            + f" ([ok]{scratch / max(total, 1):.1f}× menos[/]); el HTML, CSS y JS los genera el programa.")
    else:
        say("  [dim]Cada tarea recibe la especificación, el CONTRATO y un resumen de lo ya creado (no toda la "
            "conversación), así el contexto no se llena.[/]")


def output_budget(app) -> int:
    """Salida máxima por tarea: lo que deje libre el contexto tras el prompt (~3k), con tope de 16k."""
    return max(1024, min(16384, app.n_ctx - 3500))


# ---------------------------------------------------------------------------
# Contexto de cada tarea
# ---------------------------------------------------------------------------

def summarize_file(path: Path, rel: str) -> str:
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"- {rel}: (no se pudo leer)"
    lines = txt.count("\n") + 1
    info = []
    if rel.endswith((".css", ".html", ".jsx", ".vue")):
        classes = list(dict.fromkeys(re.findall(r"\.([a-zA-Z][\w-]{2,})\s*[{,:]", txt)
                                     + re.findall(r'class(?:Name)?="([^"]+)"', txt)))
        flat = list(dict.fromkeys(" ".join(classes).split()))[:60]
        if flat:
            info.append("clases: " + " ".join(flat))
        ids = list(dict.fromkeys(re.findall(r'id="([\w-]+)"', txt)))[:40]
        if ids:
            info.append("ids: " + " ".join(ids))
        vars_ = list(dict.fromkeys(re.findall(r"(--[\w-]+)\s*:", txt)))[:25]
        if vars_:
            info.append("variables CSS: " + " ".join(vars_))
    if rel.endswith((".js", ".jsx", ".vue", ".py")):
        fns = list(dict.fromkeys(re.findall(r"(?:function|def|const|export\s+default\s+function)\s+([A-Za-z_]\w*)", txt)))[:40]
        if fns:
            info.append("funciones/constantes: " + " ".join(fns))
        routes = list(dict.fromkeys(re.findall(r"""(?:app|router)\.(?:get|post|put|patch|delete)\(\s*['"]([^'"]+)""", txt)
                                    + re.findall(r"""@app\.route\(\s*['"]([^'"]+)""", txt)))[:30]
        if routes:
            info.append("rutas API: " + " ".join(routes))
    return f"- {rel} ({lines} líneas)" + ("".join(f"\n    {x}" for x in info) if info else "")


def components_prompt(proj: Project) -> str:
    a = proj.answers
    hints = "\n".join(f"- {s} → <{SECTION_COMPONENTS.get(s, 'section')}>" for s in a["sections"])
    theme = {"minimal": "minimal", "dark": "dark", "corporate": "corporate", "colorful": "colorful"}[a["style"]]
    names = ["navbar", "footer", "section"] + [SECTION_COMPONENTS.get(s, "section").split()[0] for s in a["sections"]]
    if a["type"] == "panel":
        names.append("sidebar")
    return "\n\n".join([
        "# Proyecto", spec_text(a),
        "# Cómo construirla",
        webkit.catalog_doc(list(dict.fromkeys(names))),
        "# Ejemplo de pagina.lai (solo para ver el formato)", webkit.EXAMPLE,
        "# Componentes sugeridos para las secciones elegidas", hints
        + ("\n- Menú → <sidebar> (es un panel)" if a["type"] == "panel" else "\n- Menú → <navbar>  · Pie → <footer>"),
        "# Tarea",
        f'Escribe pagina.lai completo para «{a["name"]}». Primera línea: <page title="…" theme="{theme}" '
        f'lang="{a["lang"]}" …/> (elige accent y font acordes al estilo). Usa los componentes sugeridos en ese orden, '
        "con contenido REAL, concreto y atractivo para ESTE negocio (nombres, precios, textos; nada de «lorem» ni "
        "«Producto 1»). NO copies los textos del ejemplo: es de otro negocio. Pon nav=\"…\" en las secciones principales. Entre 3 y 6 ítems por componente.",
        "Responde SOLO con un bloque ```lai pagina.lai … ``` (sin HTML, CSS ni JS).",
    ])


def task_prompt(proj: Project, idx: int) -> str:
    t = proj.tasks[idx]
    if proj.answers.get("build") == "components":
        return components_prompt(proj)
    parts = ["# Proyecto", spec_text(proj.answers),
             "# Estructura completa de archivos del proyecto",
             "\n".join(f"- {f.path}: {f.desc}" for tk in proj.tasks for f in tk.files)]
    contract = proj.dir / "CONTRATO.md"
    if idx > 0 and contract.exists():
        parts += ["# CONTRATO técnico (obligatorio)", contract.read_text(encoding="utf-8")[:9000]]
    done = [f for tk in proj.tasks[1:idx] for f in tk.files if (proj.dir / f.path).exists()]
    if done:
        parts += ["# Archivos ya creados (respeta sus nombres de clases, ids, funciones y rutas)",
                  "\n".join(summarize_file(proj.dir / f.path, f.path) for f in done)]
    if idx == 0:
        req = ("Escribe CONTRATO.md: el documento técnico que seguirán todos los archivos. Incluye: paleta y "
               "variables CSS (nombres y valores), tipografías, ids de cada sección, nombres de las clases "
               "principales y de los componentes, estructura de los datos (ejemplo JSON o tablas con sus columnas), "
               "endpoints de la API (método, ruta, cuerpo y respuesta) si hay servidor, funciones JavaScript "
               "principales con su responsabilidad, y los textos clave (títulos, llamadas a la acción). Sé concreto "
               "y breve: listas y tablas, sin código completo.")
    else:
        req = ("Crea estos archivos, completos y funcionales:\n"
               + "\n".join(f"- {f.path}: {f.desc}" for f in t.files)
               + "\nUsa exactamente las rutas indicadas y lo definido en el CONTRATO.")
    parts += [f"# Tarea {idx} de {len(proj.tasks) - 1}", req,
              "Responde SOLO con los bloques de código, con la ruta en la cabecera (```lenguaje ruta)."]
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------

def _save(proj: Project, task: Task, answer: str) -> tuple[list[str], list[str]]:
    """Guarda los bloques esperados. Devuelve (guardados, incompletos/faltantes)."""
    raw_dir = proj.dir / ".zero"
    raw_dir.mkdir(parents=True, exist_ok=True)
    k = proj.tasks.index(task) if task in proj.tasks else 0
    (raw_dir / f"tarea-{k}.md").write_text(answer, encoding="utf-8")
    blocks = filemod.extract_blocks(answer)
    want = {f.path.replace("\\", "/"): f for f in task.files}
    by_path = {}
    for b in blocks:
        if b.path:
            p = b.path.replace("\\", "/").lstrip("./")
            if p in want:
                by_path[p] = b
            else:  # el modelo puso otra carpeta: buscar por nombre de archivo
                m = [w for w in want if w.split("/")[-1] == p.split("/")[-1]]
                if len(m) == 1:
                    by_path[m[0]] = b
    loose = [b for b in blocks if not b.path]
    for w in want:
        if w not in by_path:
            ext = w.rsplit(".", 1)[-1]
            c = [b for b in loose if filemod.EXT.get(b.lang.lower(), b.lang.lower()) == ext]
            if len(c) == 1:
                by_path[w] = c[0]
                loose.remove(c[0])
    saved, bad = [], []
    for w in want:
        b = by_path.get(w)
        if b is None:
            bad.append(w)
            continue
        target = filemod.safe_target(w, proj.dir)
        filemod.write(target, b.code)
        (saved if b.closed else bad).append(w)
        if target.suffix == webkit.EXT and b.closed:
            build_page(target)
    return saved, bad


def build_page(lai: Path):
    """Convierte pagina.lai en index.html + css/styles.css + js/app.js y lo muestra."""
    rep = webkit.render_file(lai)
    render.tool("ok" if not rep.warnings else "warn", "Página generada con componentes",
                ", ".join(p.name for p in rep.files),
                f"{len(rep.components)} componentes: {', '.join(rep.components)}"
                + (f" · avisos: {'; '.join(rep.warnings[:4])}" if rep.warnings else ""))
    return rep


def run_task(app, proj: Project, idx: int, budget: int) -> bool:
    task = proj.tasks[idx]
    prompt = task_prompt(proj, idx)
    p = app.params
    max_out = min(budget, max(2048, int(task.est * 1.8) + 1024))
    params = GenParams(max_tokens=max_out, temperature=0.4 if idx == 0 else 0.5, top_p=0.95, top_k=40,
                       min_p=0.05, repeat_penalty=1.05, seed=-1, thinking=False)
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    for attempt in range(2):
        title = f"Tarea {idx}/{len(proj.tasks) - 1} · {task.title}" + (" (reintento)" if attempt else "")
        try:
            ans, _, stopped = app.relay_chain(msgs, prompt, app.engine.name, params, tool_title=title)
        except EngineError as e:
            render.tool("err", f"Tarea {idx}", task.title, str(e))
            task.status = "fallida"
            return False
        if stopped:
            render.tool("warn", f"Tarea {idx} cancelada", task.title, "retómala con /zero continuar")
            return False
        saved, bad = _save(proj, task, ans)
        for w in saved:
            n_lines = (proj.dir / w).read_text(encoding="utf-8").count("\n") + 1
            render.tool("ok", "Archivo creado", w, f"{n_lines} líneas · {render.short_path(proj.dir / w)}")
        if not bad:
            task.status = "hecha"
            proj.save()
            return True
        if attempt == 0:
            warn(f"Faltan o están cortados: {', '.join(bad)} — reintentando de forma más compacta.")
            task_files = [f for f in task.files if f.path in bad]
            task.files = [f for f in task.files if f.path not in bad] + task_files
            msgs = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt + "\n\nIMPORTANTE: en el intento anterior faltaron o se "
                     f"cortaron: {', '.join(bad)}. Escríbelos COMPLETOS y más compactos (sin comentarios largos ni "
                     "texto fuera de los bloques)."}]
            params.max_tokens = budget
    task.status = "incompleta"
    proj.save()
    render.tool("warn", f"Tarea {idx} incompleta", task.title,
                f"revisa {', '.join(bad)} · sube /ctx y usa /zero continuar")
    return False


def _latest_unfinished() -> Project | None:
    if not PROJECTS_DIR.exists():
        return None
    for f in sorted(PROJECTS_DIR.glob("*/zero.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            pr = Project.load(f)
        except Exception:
            continue
        if any(t.status != "hecha" for t in pr.tasks):
            return pr
    return None


def run_zero(app, resume: bool = False):
    if app.engine is None:
        error("Carga primero un modelo (/model). Para webs va mejor uno de programación (/modo programacion).")
        return
    if resume:
        proj = _latest_unfinished()
        if proj is None:
            say("[dim]No hay proyectos a medias.[/]")
            return
        ok(f"Retomando «{proj.name}» ({sum(t.status == 'hecha' for t in proj.tasks)}/{len(proj.tasks)} tareas hechas)")
    else:
        console.print(Panel(Text.from_markup(
            "[accent]Prompt-Zero[/] te hace unas preguntas, planifica los archivos, reparte el trabajo en tareas que "
            "quepan en tu modelo y las ejecuta una a una guardando el resultado.\n[dim]Esc cancela en cualquier "
            "momento; /zero continuar retoma.[/]"), border_style="border"))
        a = questionnaire()
        if not a:
            say("[dim]Cancelado.[/]")
            return
        slug = re.sub(r"[^\w-]+", "-", a["name"].lower()).strip("-")[:40] or "proyecto"
        base, n = slug, 2
        while (PROJECTS_DIR / slug).exists():
            slug, n = f"{base}-{n}", n + 1
        proj = Project(a["name"], slug, a["desc"], a, created=time.strftime("%Y-%m-%d %H:%M"), model=app.engine.name)
        console.print(Panel(spec_text(a), title="Especificación", title_align="left", border_style="border"))
    if app.n_ctx and app.n_ctx < 8192:
        warn(f"El contexto actual ({app.n_ctx}) es pequeño para generar archivos: recomiendo /ctx 16k o más.")
    budget = output_budget(app)
    if not resume:
        proj.tasks = split_tasks(file_plan(proj.answers), budget)
    tps, how = measured_tps(app)
    show_analysis(app, proj, budget, tps, how)
    pending = [i for i, t in enumerate(proj.tasks) if t.status != "hecha"]
    if not app.confirm(f"¿Empezar? ({len(pending)} tareas)", default=True):
        proj.save()
        say(f"[dim]Plan guardado en {render.short_path(proj.dir)}. /zero continuar cuando quieras.[/]")
        return
    proj.save()
    app.project_dir = proj.dir
    for i in pending:
        if not run_task(app, proj, i, budget):
            break
    if proj.answers.get("build") == "components" and (proj.dir / "index.html").exists():
        (proj.dir / "README.md").write_text(
            f"# {proj.name}\n\n{proj.desc}\n\nPágina generada por Local AI con componentes prediseñados.\n\n"
            "- Abre `index.html` en el navegador (no necesita instalar nada).\n"
            "- Para cambiar textos, colores o secciones edita `pagina.lai` (o pídeselo al modelo) y se regenera.\n"
            "- Tema y color: primera línea `<page theme=\"…\" accent=\"#hex\" font=\"…\">`.\n", encoding="utf-8")
    done = sum(t.status == "hecha" for t in proj.tasks)
    files = sorted(str(p.relative_to(proj.dir)) for p in proj.dir.rglob("*") if p.is_file() and p.name != "zero.json")
    status = "ok" if done == len(proj.tasks) else "warn"
    render.tool(status, f"Proyecto «{proj.name}»", f"{done}/{len(proj.tasks)} tareas",
                f"{len(files)} archivos en {render.short_path(proj.dir)}")
    if done == len(proj.tasks):
        index = proj.dir / "index.html"
        if proj.answers["front"] in ("vanilla", "tailwind") and index.exists():
            app.last_saved = [index]
            say("  [dim]/open lo abre en el navegador · /archivos lista el proyecto · pide cambios con normalidad: "
                "los archivos nuevos se guardan en la carpeta del proyecto[/]")
        else:
            say("  [dim]Sigue el README.md para instalar y arrancar (npm install / pip install…). /archivos abrir "
                "abre la carpeta.[/]")
        app.history += [
            {"role": "user", "content": f"[Prompt-Zero] Crea el proyecto «{proj.name}».\n{spec_text(proj.answers)}"},
            {"role": "assistant", "content": "Proyecto creado. Archivos:\n" + "\n".join(f"- {f}" for f in files)
             + "\nSi quieres cambiar algo, dime qué y adjunta el archivo con @ruta si necesito ver su contenido."},
        ]
        app._mark_ctx()
    else:
        say("  [dim]Ajusta lo necesario (/ctx, /model) y usa /zero continuar.[/]")
