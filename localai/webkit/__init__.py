"""
webkit: páginas web a partir de componentes prediseñados.

El modelo escribe un archivo .lai con etiquetas (<hero>, <cards>, <pricing>, <gantt>…)
y su contenido; este paquete genera index.html + css/styles.css + js/app.js con
código ya probado. Así un modelo local gasta ~3–4 veces menos tokens y no puede
romper el maquetado, el CSS ni la lógica (p. ej. el Gantt calcula él la ruta crítica).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from .components import REG, Ctx, E
from .parser import Node, inline, parse

ASSETS = Path(__file__).parent / "assets"
EXT = ".lai"

THEMES = {
    "minimal": dict(bg="#ffffff", surface="#f6f7f9", card="#ffffff", text="#14161a", muted="#5f6673", border="#e6e8ec",
                    accent="#2563eb", accent2="#7c3aed", shadow="0 10px 30px -18px rgba(16,24,40,.25)", radius="18px"),
    "dark": dict(bg="#0d0f14", surface="#141821", card="rgba(255,255,255,.04)", text="#eef0f4", muted="#9aa3b2",
                 border="rgba(255,255,255,.09)", accent="#c8a165", accent2="#e07a5f",
                 shadow="0 20px 50px -30px rgba(0,0,0,.8)", radius="18px"),
    "corporate": dict(bg="#ffffff", surface="#f3f6fb", card="#ffffff", text="#0f1b2d", muted="#55627a", border="#dfe5ef",
                      accent="#0b5cab", accent2="#00a3a3", shadow="0 12px 32px -20px rgba(11,92,171,.35)", radius="12px"),
    "colorful": dict(bg="#fffaf5", surface="#fff1e6", card="#ffffff", text="#1d1630", muted="#6b6380", border="#f1e4d8",
                     accent="#ff4d6d", accent2="#7b2cbf", shadow="0 18px 40px -22px rgba(123,44,191,.35)", radius="24px"),
}
FONTS = {"inter": "Inter", "poppins": "Poppins", "montserrat": "Montserrat", "nunito": "Nunito", "dm sans": "DM Sans",
         "space grotesk": "Space Grotesk", "playfair": "Playfair Display", "playfair display": "Playfair Display",
         "lora": "Lora", "raleway": "Raleway", "outfit": "Outfit"}


def _contrast(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return "#ffffff"
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#111111" if lum > 0.6 else "#ffffff"


def theme_css(page) -> tuple[str, str]:
    """(variables CSS, <link> de la fuente)."""
    name = (page.a("theme", "minimal") if page else "minimal").lower()
    aliases = {"oscuro": "dark", "claro": "minimal", "minimalista": "minimal", "corporativo": "corporate",
               "colorido": "colorful", "light": "minimal"}
    t = dict(THEMES.get(aliases.get(name, name), THEMES["minimal"]))
    if page is not None:
        for k, attr in (("accent", "accent"), ("accent2", "accent2"), ("bg", "background"), ("radius", "radius")):
            v = page.a(attr)
            if v and re.fullmatch(r"#[0-9a-fA-F]{3,8}|\d+px|[a-z]+\(.*\)", v):
                t[k] = v
    font_key = (page.a("font") if page else "").strip().lower()
    family = FONTS.get(font_key) or (page.a("font").strip() if page and page.a("font") else "")
    link = ""
    stack = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    if family:
        link = (f'<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="stylesheet" '
                f'href="https://fonts.googleapis.com/css2?family={family.replace(" ", "+")}:wght@400;500;600;700;800&display=swap">')
        stack = f"'{family}', {stack}"
    css = (":root {\n" + "".join(f"  --{k.replace('accent2', 'accent-2')}: {v};\n" for k, v in t.items())
           + f"  --accent-contrast: {_contrast(t['accent'])};\n  --font: {stack};\n  --max-width: 1140px;\n}}\n")
    return css, link


@dataclass
class Report:
    files: list[Path] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def render(source: str) -> tuple[str, str, str, Report]:
    """Devuelve (html, css, js, informe) a partir del texto .lai."""
    rep = Report()
    nodes, warns = parse(source, set(REG))
    rep.warnings += warns
    page = next((n for n in nodes if n.tag == "page"), None)
    title = page.a("title", "Mi página") if page else "Mi página"
    ctx = Ctx(brand=page.a("brand", title) if page else title)
    if page is not None and page.a("currency"):
        ctx.currency = page.a("currency")
    body_nodes = [n for n in nodes if n.tag != "page"]
    for n in body_nodes:   # el carrito necesita saberse antes de pintar la barra
        if n.tag == "products" and n.yes("cart", True):
            ctx.has_cart = True
    parts: list[str] = []
    top: list[str] = []          # avisos (<banner>) por encima de todo
    used: list[str] = []
    for n in body_nodes:
        comp = REG.get(n.tag)
        if comp is None:
            rep.warnings.append(f"componente desconocido <{n.tag}>: ignorado")
            continue
        try:
            (top if n.tag == "banner" else parts).append(comp.render(n, ctx))
        except Exception as e:  # un componente roto no debe tumbar la página
            rep.warnings.append(f"<{n.tag}>: {e.__class__.__name__}: {e}")
            continue
        if n.tag not in used:
            used.append(n.tag)
    if ctx.has_cart and "cart" not in used:
        parts.append(REG["cart"].render(Node("cart", {}), ctx))
        used.append("cart")
    nav_html = "".join(f'<a href="{E(h)}">{E(t)}</a>' for t, h in ctx.nav)
    if not ctx.nav:  # sin nav="…": usar las secciones con título
        auto = re.findall(r'<section id="([^"]+)" class="section [^"]*"><div class="container"><div class="section-head reveal">'
                          r'(?:<span class="eyebrow">[^<]*</span>)?<h2>([^<]+)</h2>', "".join(parts))
        nav_html = "".join(f'<a href="#{E(i)}">{t}</a>' for i, t in auto[:6])
    html_body = "\n".join(parts).replace("{{NAV}}", nav_html)
    dashboard = "sidebar" in used
    if dashboard:
        # la barra lateral va fuera de <main>
        m = re.search(r'<aside class="sidebar">.*?</aside>', html_body, re.S)
        sidebar_html = m.group(0) if m else ""
        html_body = "".join(top) + sidebar_html + "<main>" + html_body.replace(sidebar_html, "") + "</main>"
    else:
        # navbar fuera de <main>, footer después
        m_nav = re.search(r'<header class="navbar">.*?</header>', html_body, re.S)
        m_foot = re.search(r'<footer class="footer">.*?</footer>', html_body, re.S)
        nav_part = m_nav.group(0) if m_nav else ""
        foot_part = m_foot.group(0) if m_foot else ""
        middle = html_body.replace(nav_part, "").replace(foot_part, "")
        html_body = "".join(top) + f"{nav_part}\n<main>\n{middle}\n</main>\n{foot_part}"
    vars_css, font_link = theme_css(page)
    css = (vars_css + (ASSETS / "base.css").read_text(encoding="utf-8") + "\n"
           + "\n".join(f"/* --- {u} --- */{REG[u].css}" for u in used if REG[u].css))
    js = (ASSETS / "core.js").read_text(encoding="utf-8")
    lang = page.a("lang", "es") if page else "es"
    desc = page.a("description", "") if page else ""
    doc = (f'<!doctype html>\n<html lang="{E(lang)}">\n<head>\n<meta charset="utf-8">\n'
           f'<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>{E(title)}</title>\n'
           + (f'<meta name="description" content="{E(desc)}">\n' if desc else "")
           + f'{font_link}\n<link rel="stylesheet" href="css/styles.css">\n</head>\n'
           f'<body{" class=\"dashboard\"" if dashboard else ""}>\n{html_body}\n<script src="js/app.js" defer></script>\n'
           f'<!-- Generado con Clay-Myrrh a partir de pagina.lai: edita ese archivo y se regenera -->\n</body>\n</html>\n')
    rep.components = used
    rep.warnings += ctx.warnings
    return doc, css, js, rep


def render_file(lai_path: Path, out_dir: Path | None = None) -> Report:
    """Genera index.html, css/styles.css y js/app.js junto al .lai (o en out_dir)."""
    out = out_dir or lai_path.parent
    doc, css, js, rep = render(lai_path.read_text(encoding="utf-8"))
    name = "index.html" if lai_path.stem.lower() in ("pagina", "index", "page", "web") else f"{lai_path.stem}.html"
    (out / "css").mkdir(parents=True, exist_ok=True)
    (out / "js").mkdir(parents=True, exist_ok=True)
    for path, data in ((out / name, doc), (out / "css" / "styles.css", css), (out / "js" / "app.js", js)):
        path.write_text(data, encoding="utf-8", newline="\n")
        rep.files.append(path)
    return rep


CHAT_NOTE = ("PÁGINAS WEB Y PLANTILLAS: no escribas HTML, CSS ni JS a mano. Escribe un único bloque ```lai pagina.lai``` "
             "con los componentes de abajo y el programa genera index.html + css/styles.css + js/app.js, ya probados "
             "(gastas ~4 veces menos tokens). Para cambiar una página ya creada, vuelve a escribir pagina.lai completo. "
             "Usa <raw> solo para lo que ningún componente cubra.")

_WEB_NOUN = (r"p[aá]gina|web|landing|sitio|plantilla|template|html|portafolio|portfolio|tienda (?:online|virtual)|"
             r"e-?commerce|dashboard|panel de (?:control|administraci[oó]n)|blog|men[uú] (?:de|para) (?:un )?restaurante|"
             r"formulario|inicio de sesi[oó]n|login|kanban|calendario|cuenta regresiva|newsletter")
_WEB_VERB = r"crea|cr[eé]ame|haz|hazme|genera|gen[eé]rame|dise[ñn]a|dise[ñn]ame|monta|construye|arma|quiero|necesito|prepara"


def wants_components(text: str, last_reply: str = "") -> bool:
    """¿Pide el usuario una página o plantilla web? (para incluir el catálogo solo cuando hace falta)."""
    low = text.lower()
    if re.search(rf"\b({_WEB_VERB})\b.{{0,80}}\b({_WEB_NOUN})\b", low, re.S):
        return True
    # cambios sobre una página hecha con componentes en el turno anterior
    if "pagina.lai" in last_reply.lower() or "```lai" in last_reply.lower():
        return bool(re.search(r"\b(cambia|a[ñn]ade|agrega|quita|elimina|pon|modifica|mueve|usa|color|secci[oó]n|"
                              r"tema|bot[oó]n|men[uú]|precio|texto)\b", low))
    return False


GROUPS = {
    "Estructura": ["navbar", "hero", "banner", "section", "footer", "sidebar", "float", "cookies", "modal", "raw"],
    "Contenido": ["cards", "features", "about", "stats", "steps", "timeline", "skills", "team", "logos", "tabs",
                  "faq", "testimonials", "gallery", "carousel", "beforeafter", "video", "map", "article", "posts", "cta"],
    "Negocio": ["pricing", "compare", "menu", "hours", "products", "cart", "invoice", "progress", "countdown", "newsletter"],
    "Formularios": ["contact", "form", "wizard", "login", "quiz", "poll", "calculator", "chatbot"],
    "Datos y apps": ["kpis", "chart", "table", "gantt", "crud", "kanban", "todo", "calendar", "expenses"],
}


def groups() -> list[dict]:
    """Componentes agrupados para mostrarlos (asistente, /componentes)."""
    seen, out = set(), []
    for g, names in GROUPS.items():
        items = [REG[n] for n in names if n in REG]
        seen.update(c.name for c in items)
        out.append({"group": g, "items": [{"name": c.name, "desc": c.desc, "attrs": c.attrs, "items": c.items} for c in items]})
    rest = [c for c in REG.values() if c.name not in seen]
    if rest:
        out.append({"group": "Otros", "items": [{"name": c.name, "desc": c.desc, "attrs": c.attrs, "items": c.items} for c in rest]})
    return out


def build_demo(out_dir: Path) -> Report:
    """Genera la página de demostración con (casi) todos los componentes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    lai = out_dir / "pagina.lai"
    lai.write_text((ASSETS / "demo.lai").read_text(encoding="utf-8"), encoding="utf-8")
    return render_file(lai)


CORE = ["navbar", "hero", "section", "cards", "features", "about", "stats", "testimonials", "pricing", "faq", "cta",
        "contact", "footer"]
# componente → palabras de la petición que lo hacen relevante (documentación completa solo de esos)
KEYWORDS = {
    "menu": r"carta|men[uú]\b|restaurante|cafeter|bar\b|comida|plato|pizzer|panader",
    "hours": r"horario|abiert|restaurante|cafeter|pizzer|tienda|peluquer|barber|cl[ií]nica|negocio|local\b|taller",
    "map": r"mapa|ubicaci|direcci|d[oó]nde|local\b|restaurante|cafeter|pizzer|cl[ií]nica|tienda f",
    "wizard": r"reserva|cita|inscripci|matr[ií]cula|alta\b|pasos|presupuesto",
    "form": r"formulario|encuesta|inscripci|reserva|solicitud|voluntari",
    "calendar": r"calendario|agenda|evento|citas|planific",
    "countdown": r"cuenta regresiva|lanzamiento|evento|apertura|oferta|boda|concierto",
    "team": r"equipo|profesor|ponente|barber|doctor|staff|nosotros",
    "gallery": r"galer[ií]a|portafolio|portfolio|proyectos|fotos|trabajos",
    "carousel": r"carrusel|slider|galer[ií]a|fotos|local\b",
    "tabs": r"pesta[ñn]as|temario|servicios|horarios",
    "products": r"tienda|producto|cat[aá]logo|carrito|venta|e-?commerce",
    "kpis": r"panel|dashboard|indicador|kpi|m[eé]tricas",
    "chart": r"gr[aá]fic|estad[ií]stic|panel|dashboard|ventas",
    "table": r"tabla|listado|datos|panel|dashboard",
    "crud": r"gesti[oó]n|crud|registros|inventario|clientes",
    "sidebar": r"panel|dashboard|men[uú] lateral|admin",
    "kanban": r"kanban|tablero|tareas|proyecto",
    "todo": r"tareas|checklist|lista de|pendientes",
    "expenses": r"gastos|ingresos|finanzas|presupuesto personal|ahorro|caja",
    "invoice": r"factura|presupuesto|cotizaci",
    "calculator": r"calculadora|calcular|simulador|cu[aá]nto cuesta|pr[eé]stamo|imc",
    "quiz": r"quiz|test|examen|cuestionario|trivia",
    "poll": r"encuesta|votaci|opini[oó]n",
    "login": r"login|acceso|iniciar sesi|registro|usuarios|socios",
    "chatbot": r"chat|bot|asistente virtual|atenci[oó]n al cliente",
    "progress": r"recaudaci|donaci|campa[ñn]a|meta|objetivo|ong|crowdfunding",
    "compare": r"compar|planes|versus|\bvs\b",
    "logos": r"clientes|marcas|patrocinador|partners|conf[ií]an",
    "beforeafter": r"antes y despu[eé]s|reforma|transformaci",
    "video": r"v[ií]deo|youtube|vimeo",
    "posts": r"blog|noticias|art[ií]culos|entradas",
    "article": r"blog|art[ií]culo|noticia|entrada",
    "timeline": r"historia|trayectoria|experiencia|cronolog",
    "skills": r"habilidades|portafolio|cv|curr[ií]cul",
    "newsletter": r"bolet[ií]n|newsletter|suscri",
    "gantt": r"gantt|cronograma|ruta cr[ií]tica",
    "banner": r"aviso|oferta|promoci|anuncio",
    "float": r"whatsapp|modo oscuro|volver arriba",
    "cookies": r"cookies|rgpd|gdpr|privacidad",
    "modal": r"condiciones|aviso legal|ventana|popup|emergente",
    "steps": r"pasos|proceso|c[oó]mo funciona",
}


def relevant(text: str) -> list[str]:
    low = (text or "").lower()
    return [n for n, rx in KEYWORDS.items() if n in REG and re.search(rx, low)]


def chat_doc(request: str = "", previous: str = "") -> str:
    """
    Catálogo para el chat: documentación completa de los básicos, de los que encajan con la
    petición y de los que ya usa la página anterior; del resto, solo el nombre (≈2–3k tokens
    en vez de ≈5k). Incluye un ejemplo completo (a los modelos pequeños les ayuda mucho).
    """
    used = [t for t in re.findall(r"<([a-z]+)\b", previous or "") if t in REG]
    names = list(dict.fromkeys(CORE + relevant(request) + used))
    return CHAT_NOTE + "\n" + catalog_doc(names) + "\n\nEjemplo completo:\n```lai pagina.lai\n" + EXAMPLE + "\n```"


def catalog_doc(names: list[str] | None = None) -> str:
    """Documentación compacta para el prompt del modelo (todo, o solo `names` en detalle)."""
    lines = ["Componentes disponibles (escribe SOLO estas etiquetas; el programa genera HTML, CSS y JS):",
             '<page title="…" brand="…" theme="minimal|dark|corporate|colorful" accent="#hex" font="Inter|Poppins|…" '
             'lang="es" description="…" currency="$"/>  (primera línea, opcional)']
    detail = [REG[n] for n in names if n in REG] if names else list(REG.values())
    for c in detail:
        lines.append(f"\n<{c.name} {c.attrs}>  — {c.desc}" if c.attrs else f"\n<{c.name}>  — {c.desc}")
        if c.items:
            lines.append(f"   ítems: {c.items}")
    if names:
        rest = [c for c in REG.values() if c not in detail]
        if rest:
            lines.append("\nTambién existen (si los necesitas, úsalos con atributos evidentes como title=\"…\" e <item>): "
                         + "; ".join(f"<{c.name}> {c.desc.split(':')[0].split('(')[0].strip()[:70]}" for c in rest))
    lines += ["",
              "Reglas: atributos entre comillas; botones/enlaces como \"Texto|#destino\"; image acepta un emoji "
              "(se dibuja un fondo de color) o una URL; nav=\"Texto\" añade la sección al menú; el texto dentro de una "
              "etiqueta admite Markdown (**negrita**, - listas, [enlace](url)). Cierra cada componente con </nombre> o "
              "usa /> si no tiene contenido. No escribas HTML, CSS ni JS salvo dentro de <raw>."]
    return "\n".join(lines)


EXAMPLE = """<page title="Estudio Lumen" theme="minimal" accent="#0f766e" font="Outfit" description="Yoga y pilates en Valencia"/>
<navbar brand="Estudio Lumen" logo="🧘" cta="Clase gratis|#contacto"/>
<hero title="Muévete mejor, respira mejor" subtitle="Clases reducidas de yoga y pilates para todos los niveles." button="Ver horarios|#clases" button2="Precios|#precios" image="🧘" align="left"/>
<cards id="clases" nav="Clases" title="Nuestras clases" columns="3">
  <item icon="🌅" title="Hatha suave">Posturas lentas y respiración. Ideal para empezar.</item>
  <item icon="🔥" title="Vinyasa">Secuencias dinámicas que conectan movimiento y respiración.</item>
  <item icon="🤸" title="Pilates máquina">Fuerza del core con reformer, grupos de 6 personas.</item>
</cards>
<pricing id="precios" nav="Precios" title="Bonos" currency="€">
  <item name="4 clases" price="40" period="/mes" features="Cualquier horario; Esterilla incluida">Para probar.</item>
  <item name="Ilimitado" price="75" period="/mes" featured="yes" badge="Popular" features="Clases ilimitadas; 1 sesión privada">El favorito.</item>
</pricing>
<faq id="faq" nav="FAQ" title="Preguntas frecuentes">
  <item q="¿Necesito experiencia?">No, adaptamos cada postura a tu nivel.</item>
</faq>
<contact id="contacto" nav="Contacto" title="Reserva tu clase gratis" email="hola@estudiolumen.example" address="C/ Colón 12, Valencia" hours="L–V 7:00–21:00"/>
<footer brand="Estudio Lumen" text="Yoga y pilates en el centro de Valencia"/>"""
