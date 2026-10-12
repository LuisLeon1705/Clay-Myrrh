"""
Componentes prediseñados del webkit. Cada uno: documentación para el modelo (atributos
e ítems), su CSS y su función de renderizado. El modelo solo escribe etiquetas con
contenido; el HTML, CSS y JS los pone este módulo, ya probados.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from typing import Callable

from .parser import Node, inline, md

E = lambda s: html.escape(str(s or ""), quote=True)   # noqa: E731


@dataclass
class Ctx:
    nav: list[tuple[str, str]] = field(default_factory=list)
    has_cart: bool = False
    currency: str = "$"
    warnings: list[str] = field(default_factory=list)
    alt: bool = False
    used: set[str] = field(default_factory=set)
    brand: str = ""


@dataclass
class Comp:
    name: str
    desc: str
    attrs: str
    items: str
    css: str
    render: Callable[[Node, Ctx], str]
    section: bool = True


REG: dict[str, Comp] = {}


def comp(name, desc, attrs="", items="", css="", section=True):
    def deco(fn):
        REG[name] = Comp(name, desc, attrs, items, css, fn, section)
        return fn
    return deco


# ---------------------------------------------------------------------------
# Ayudas
# ---------------------------------------------------------------------------

def link(val: str, default_href="#") -> tuple[str, str]:
    if not val:
        return "", default_href
    if "|" in val:
        t, h = val.split("|", 1)
        return t.strip(), (h.strip() or default_href)
    v = val.strip()
    if re.match(r"^(#|/|https?://|mailto:|tel:)", v):        # solo el destino: «#reservar» → «Reservar»
        name = re.sub(r"^#|^https?://(www\.)?|^mailto:|^tel:", "", v).split("/")[0].replace("-", " ").replace("_", " ")
        return (name[:1].upper() + name[1:]) if name else "Ver más", v
    return v, default_href


def button(val: str, cls="btn-primary", extra="") -> str:
    t, h = link(val)
    return f'<a class="btn {cls}" href="{E(h)}"{extra}>{E(t)}</a>' if t else ""


def is_url(v: str) -> bool:
    return bool(re.match(r"^(https?://|/|\./|[\w-]+/)?[\w./%-]+\.(png|jpe?g|webp|gif|svg|avif)(\?.*)?$", v or "", re.I))


def hue(s: str) -> int:
    return int(hashlib.md5(s.encode()).hexdigest()[:4], 16) % 360


def visual(v: str, alt: str = "", cls: str = "visual") -> str:
    if is_url(v):
        return f'<img class="{cls}" src="{E(v)}" alt="{E(alt)}" loading="lazy">'
    h = hue(alt or v or "x")
    return (f'<div class="{cls} ph" style="--h:{h}" role="img" aria-label="{E(alt)}">'
            f'<span>{E(v) if v else E((alt or "?")[:1].upper())}</span></div>')


def split_list(v: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;\n]|,(?=\s*[A-ZÁÉÍÓÚÑ¿¡0-9])", v or "") if x.strip()]


def head(n: Node) -> str:
    t, sub, eye = n.a("title"), n.a("subtitle"), n.a("eyebrow")
    if not (t or sub or eye):
        return ""
    return ('<div class="section-head reveal">' + (f'<span class="eyebrow">{E(eye)}</span>' if eye else "")
            + (f"<h2>{inline(t)}</h2>" if t else "") + (f"<p>{inline(sub)}</p>" if sub else "") + "</div>")


def section(n: Node, ctx: Ctx, inner: str, cls: str, default_id: str) -> str:
    sid = n.a("id", default_id)
    alt = n.attrs.get("alt")
    use_alt = ctx.alt if alt is None else n.yes("alt")
    ctx.alt = not use_alt
    nav = n.a("nav")
    if nav:
        ctx.nav.append((nav if nav.lower() not in ("yes", "si", "sí") else (n.a("title") or sid), f"#{sid}"))
    return (f'<section id="{E(sid)}" class="section {cls}{" alt" if use_alt else ""}">'
            f'<div class="container">{head(n)}{inner}</div></section>')


def body(n: Node) -> str:
    return md(n.text) if n.text.strip() else ""


# ---------------------------------------------------------------------------
# Página y navegación
# ---------------------------------------------------------------------------

@comp("navbar", "Barra de navegación fija con menú móvil. Si no pones enlaces, usa los componentes con nav=\"…\".",
      'brand="Nombre" logo="☕|logo.png" cta="Texto|#destino"', '<link href="#id">Texto</link> (opcional)',
      section=False, css="""
.navbar { position: sticky; top: 0; z-index: 50; backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
  background: color-mix(in srgb, var(--bg) 78%, transparent); border-bottom: 1px solid transparent; transition: border-color .2s; }
.navbar.scrolled { border-bottom-color: var(--border); }
.nav-inner { display: flex; align-items: center; gap: 1.5rem; height: 68px; }
.brand { display: flex; align-items: center; gap: .55rem; font-weight: 800; font-size: 1.2rem; color: var(--text) !important; text-decoration: none !important; }
.brand img { height: 34px; width: auto; }
.nav-links { display: flex; gap: 1.6rem; margin-left: auto; }
.nav-links a { color: var(--muted); font-weight: 500; text-decoration: none; }
.nav-links a:hover { color: var(--text); }
.nav-actions { display: flex; align-items: center; gap: .6rem; }
.nav-toggle { display: none; background: none; border: 0; padding: .4rem; cursor: pointer; }
.nav-toggle span { display: block; width: 22px; height: 2px; background: var(--text); margin: 5px 0; transition: .2s; }
.cart-open { position: relative; background: none; border: 1px solid var(--border); border-radius: 999px; padding: .45rem .8rem; cursor: pointer; color: var(--text); font: inherit; }
.cart-count { position: absolute; top: -6px; right: -6px; background: var(--accent); color: var(--accent-contrast); border-radius: 999px; font-size: .7rem; padding: 0 .4rem; font-weight: 700; }
@media (max-width: 820px) {
  .nav-links { position: absolute; top: 68px; left: 0; right: 0; flex-direction: column; gap: 0; padding: .5rem 1.25rem 1rem;
    background: var(--bg); border-bottom: 1px solid var(--border); display: none; }
  .nav-links a { padding: .75rem 0; border-bottom: 1px solid var(--border); }
  .navbar.open .nav-links { display: flex; }
  .nav-toggle { display: block; }
  .nav-actions .btn { display: none; }
}""")
def r_navbar(n, ctx):
    brand = n.a("brand", ctx.brand or "Inicio")
    logo = n.a("logo")
    logo_html = (f'<img src="{E(logo)}" alt="">' if is_url(logo) else f"<span>{E(logo)}</span>") if logo else ""
    links = [(c.text or c.a("label"), c.a("href", "#")) for c in n.items]
    links_html = "".join(f'<a href="{E(h)}">{E(t)}</a>' for t, h in links) if links else "{{NAV}}"
    cart = ('<button class="cart-open" aria-label="Abrir carrito">🛒 <span class="cart-count" hidden>0</span></button>'
            if ctx.has_cart else "")
    return (f'<header class="navbar"><div class="container nav-inner"><a class="brand" href="#">{logo_html}{E(brand)}</a>'
            f'<nav class="nav-links" aria-label="Principal">{links_html}</nav><div class="nav-actions">{cart}'
            f'{button(n.a("cta"), "btn-primary btn-sm")}<button class="nav-toggle" aria-label="Abrir menú" '
            f'aria-expanded="false"><span></span><span></span><span></span></button></div></div></header>')


@comp("hero", "Portada grande con título, subtítulo y botones.",
      'title="…" subtitle="…" eyebrow="…" button="Texto|#id" button2="Texto|#id" image="☕|foto.jpg" '
      'align="center|left" variant="gradient|plain|image"', "", section=False, css="""
.hero { position: relative; overflow: hidden; padding: clamp(4.5rem, 12vw, 8rem) 0 clamp(4rem, 9vw, 6.5rem); }
.hero.gradient::before { content: ""; position: absolute; inset: -40% -10% auto; height: 120%; z-index: -1;
  background: radial-gradient(60% 60% at 20% 30%, color-mix(in srgb, var(--accent) 35%, transparent), transparent 70%),
              radial-gradient(50% 50% at 85% 20%, color-mix(in srgb, var(--accent-2) 30%, transparent), transparent 70%); }
.hero.image { color: #fff; background-size: cover; background-position: center; }
.hero.image::before { content: ""; position: absolute; inset: 0; background: linear-gradient(180deg, rgba(0,0,0,.55), rgba(0,0,0,.65)); }
.hero.image .container { position: relative; }
.hero-inner { display: grid; gap: 3rem; align-items: center; }
.hero.center .hero-inner { text-align: center; max-width: 820px; margin-inline: auto; }
.hero.left .hero-inner { grid-template-columns: 1.15fr .85fr; }
.hero p.lead { font-size: clamp(1.05rem, 2.2vw, 1.3rem); color: var(--muted); max-width: 640px; }
.hero.center p.lead { margin-inline: auto; }
.hero.image p.lead { color: rgba(255,255,255,.85); }
.hero-actions { display: flex; gap: .8rem; flex-wrap: wrap; margin-top: 1.8rem; }
.hero.center .hero-actions { justify-content: center; }
.hero .visual { width: 100%; aspect-ratio: 1; border-radius: 32px; object-fit: cover; box-shadow: var(--shadow); }
.ph { display: grid; place-items: center; background: linear-gradient(135deg, hsl(var(--h) 75% 62%), hsl(calc(var(--h) + 50) 70% 45%)); }
.ph span { font-size: clamp(3rem, 10vw, 7rem); filter: drop-shadow(0 8px 18px rgba(0,0,0,.25)); }
@media (max-width: 820px) { .hero.left .hero-inner { grid-template-columns: 1fr; } .hero.left .visual { max-width: 340px; } }""")
def r_hero(n, ctx):
    variant = n.a("variant", "image" if is_url(n.a("image")) and n.a("align", "center") == "center" else "gradient")
    align = n.a("align", "left" if (n.a("image") and variant != "image") else "center")
    style = f' style="background-image:url(\'{E(n.a("image"))}\')"' if variant == "image" and is_url(n.a("image")) else ""
    eye = f'<span class="eyebrow">{E(n.a("eyebrow"))}</span>' if n.a("eyebrow") else ""
    actions = button(n.a("button")) + button(n.a("button2"), "btn-ghost")
    text = (f'<div class="reveal">{eye}<h1>{inline(n.a("title", ctx.brand))}</h1>'
            + (f'<p class="lead">{inline(n.a("subtitle"))}</p>' if n.a("subtitle") else "") + body(n)
            + (f'<div class="hero-actions">{actions}</div>' if actions else "") + "</div>")
    vis = visual(n.a("image"), n.a("title")) if (align == "left" and n.a("image") and variant != "image") else ""
    return (f'<section id="{E(n.a("id", "inicio"))}" class="hero {variant} {align}"{style}><div class="container">'
            f'<div class="hero-inner">{text}{vis}</div></div></section>')


# ---------------------------------------------------------------------------
# Contenido
# ---------------------------------------------------------------------------

@comp("cards", "Rejilla de tarjetas (servicios, características, beneficios, menú…).",
      'id="servicios" nav="Servicios" title="…" subtitle="…" eyebrow="…" columns="2|3|4" variant="card|plain"',
      '<item icon="☕" title="…" badge="Nuevo" link="Ver más|#id">texto</item>', css="""
.cards.plain .card { background: transparent; border: 0; box-shadow: none; padding: .5rem; }
.card h3 { margin-bottom: .4rem; }
.card p:last-child { margin-bottom: 0; }
.card .card-link { display: inline-block; margin-top: .8rem; font-weight: 600; }
.card .badge { margin-bottom: .6rem; }""")
def r_cards(n, ctx):
    cols = n.a("columns", str(min(4, max(2, len(n.items))) if len(n.items) != 4 else 2))
    cards = []
    for it in n.items:
        icon = it.a("icon")
        ic = (f'<img class="icon" src="{E(icon)}" alt="">' if is_url(icon) else f'<div class="icon">{E(icon)}</div>') if icon else ""
        lk = link(it.a("link"))
        price = it.a("price")
        if re.fullmatch(r"\d+([.,]\d+)?", price):
            price = f"{n.a('currency', ctx.currency)}{price}"
        cards.append(f'<article class="card reveal">{ic}' + (f'<span class="badge">{E(it.a("badge"))}</span>' if it.a("badge") else "")
                     + f'<h3>{inline(it.a("title"))}</h3>'
                     + (f'<div class="price-tag" style="font-size:1.3rem;font-weight:800;color:var(--accent);margin-bottom:.4rem">'
                        f'{E(price)}</div>' if price else "")
                     + f'{md(it.text) if it.text else ""}'
                     + (f'<a class="card-link" href="{E(lk[1])}">{E(lk[0])} →</a>' if lk[0] else "") + "</article>")
    return section(n, ctx, f'<div class="grid" style="--cols:{E(cols)}">{"".join(cards)}</div>',
                   f'cards {n.a("variant", "card")}', "servicios")


@comp("section", "Bloque de texto libre (Markdown: **negrita**, listas con -, ## subtítulos, [enlace](url)).",
      'id="…" nav="…" title="…" subtitle="…" eyebrow="…"', "texto Markdown dentro de la etiqueta", css="""
.prose { max-width: 760px; margin-inline: auto; font-size: 1.05rem; }
.prose h2, .prose h3 { margin-top: 1.6em; }""")
def r_section(n, ctx):
    return section(n, ctx, f'<div class="prose reveal">{body(n)}</div>', "text", "seccion")


@comp("about", "Sobre nosotros / sobre mí: imagen + texto + puntos destacados.",
      'id="nosotros" nav="Nosotros" title="…" eyebrow="…" image="👩‍🍳|foto.jpg" reverse="yes"',
      '<item icon="✔">punto destacado</item> (opcional) + texto Markdown', css="""
.about-inner { display: grid; grid-template-columns: .9fr 1.1fr; gap: 3rem; align-items: center; }
.about.reverse .about-inner > :first-child { order: 2; }
.about .visual { width: 100%; aspect-ratio: 4/5; border-radius: var(--radius); object-fit: cover; }
.about .section-head { text-align: left; margin: 0 0 1rem; }
.points { list-style: none; padding: 0; display: grid; gap: .6rem; margin-top: 1.2rem; }
.points li { display: flex; gap: .6rem; align-items: baseline; }
@media (max-width: 820px) { .about-inner { grid-template-columns: 1fr; } .about.reverse .about-inner > :first-child { order: 0; } }""")
def r_about(n, ctx):
    pts = "".join(f'<li><span>{E(it.a("icon", "✔"))}</span><span>{inline(it.text)}</span></li>' for it in n.items)
    text = f'<div class="reveal">{head(n)}{body(n)}' + (f'<ul class="points">{pts}</ul>' if pts else "") + "</div>"
    n2 = Node(n.tag, {k: v for k, v in n.attrs.items() if k not in ("title", "subtitle", "eyebrow")}, n.children, "")
    return section(n2, ctx, f'<div class="about-inner">{visual(n.a("image", "✨"), n.a("title"))}{text}</div>',
                   "about" + (" reverse" if n.yes("reverse") else ""), "nosotros")


@comp("stats", "Cifras destacadas con contador animado.", 'id="…" title="…"',
      '<item value="1200" prefix="" suffix="+">clientes felices</item>', css="""
.stats .grid { text-align: center; }
.stat-value { font-size: clamp(2.2rem, 5vw, 3.2rem); font-weight: 800; color: var(--accent); line-height: 1; }
.stat-label { color: var(--muted); margin-top: .4rem; }""")
def r_stats(n, ctx):
    items = "".join(f'<div class="reveal"><div class="stat-value">{E(it.a("prefix"))}<span data-count="{E(it.a("value", "0"))}">'
                    f'{E(it.a("value", "0"))}</span>{E(it.a("suffix"))}</div><div class="stat-label">{inline(it.text)}</div></div>'
                    for it in n.items)
    return section(n, ctx, f'<div class="grid" style="--cols:{min(4, len(n.items)) or 1}">{items}</div>', "stats", "cifras")


@comp("steps", "Pasos numerados de un proceso.", 'id="…" nav="…" title="…" subtitle="…"',
      '<item title="Paso">explicación</item>', css="""
.steps .grid { counter-reset: paso; }
.step { position: relative; padding-top: 2.6rem; }
.step::before { counter-increment: paso; content: counter(paso); position: absolute; top: 0; left: 0; width: 2.1rem; height: 2.1rem;
  border-radius: 50%; display: grid; place-items: center; background: var(--accent); color: var(--accent-contrast); font-weight: 800; }""")
def r_steps(n, ctx):
    items = "".join(f'<div class="step reveal"><h3>{inline(it.a("title"))}</h3>{md(it.text)}</div>' for it in n.items)
    return section(n, ctx, f'<div class="grid" style="--cols:{min(4, len(n.items)) or 1}">{items}</div>', "steps", "pasos")


@comp("testimonials", "Opiniones de clientes con estrellas.", 'id="testimonios" nav="Opiniones" title="…" subtitle="…"',
      '<item name="Ana R." role="Clienta" rating="5">opinión</item>', css="""
.quote { display: flex; flex-direction: column; gap: 1rem; }
.quote blockquote { margin: 0; font-size: 1.04rem; }
.stars { color: #f5b301; letter-spacing: 2px; }
.who { display: flex; align-items: center; gap: .75rem; margin-top: auto; }
.avatar { width: 44px; height: 44px; border-radius: 50%; display: grid; place-items: center; font-weight: 700; color: #fff;
  background: linear-gradient(135deg, hsl(var(--h) 70% 55%), hsl(calc(var(--h) + 40) 65% 45%)); }
.who small { display: block; color: var(--muted); }""")
def r_testimonials(n, ctx):
    out = []
    for it in n.items:
        name = it.a("name", "Cliente")
        try:
            stars = max(0, min(5, int(float(it.a("rating", "5")))))
        except ValueError:
            stars = 5
        out.append(f'<figure class="card quote reveal"><div class="stars" aria-label="{stars} de 5">{"★" * stars}{"☆" * (5 - stars)}</div>'
                   f'<blockquote>{inline(it.text)}</blockquote><figcaption class="who"><span class="avatar" style="--h:{hue(name)}">'
                   f'{E("".join(w[0] for w in name.split()[:2]).upper())}</span><span><strong>{E(name)}</strong>'
                   f'<small>{E(it.a("role"))}</small></span></figcaption></figure>')
    return section(n, ctx, f'<div class="grid" style="--cols:{min(3, len(n.items)) or 1}">{"".join(out)}</div>',
                   "testimonials", "testimonios")


@comp("pricing", "Tabla de precios / planes. Con yearly=\"…\" en los planes aparece el interruptor mensual/anual.",
      'id="precios" nav="Precios" title="…" subtitle="…" currency="$" yearly-label="Anual (2 meses gratis)"',
      '<item name="Básico" price="9" yearly="90" period="/mes" featured="yes" badge="Popular" features="Uno; Dos; Tres" '
      'button="Elegir|#contacto">descripción breve</item>', css="""
.bill-toggle { display: flex; justify-content: center; margin: -1rem 0 2rem; }
.bill-toggle div { display: inline-flex; padding: .3rem; border-radius: 999px; background: var(--surface); border: 1px solid var(--border); }
.bill-toggle button { border: 0; background: none; padding: .5rem 1.1rem; border-radius: 999px; font: inherit; font-weight: 600; color: var(--muted); cursor: pointer; }
.bill-toggle button[aria-pressed="true"] { background: var(--accent); color: var(--accent-contrast); }
.plan { display: flex; flex-direction: column; }
.plan.featured { border-color: var(--accent); box-shadow: 0 20px 50px -25px var(--accent); transform: scale(1.03); }
.price { font-size: 2.6rem; font-weight: 800; margin: .3rem 0; }
.price small { font-size: 1rem; color: var(--muted); font-weight: 500; }
.plan ul { list-style: none; padding: 0; margin: 1rem 0 1.5rem; display: grid; gap: .55rem; }
.plan li::before { content: "✓ "; color: var(--accent); font-weight: 800; }
.plan .btn { margin-top: auto; justify-content: center; }""")
def r_pricing(n, ctx):
    cur = n.a("currency", ctx.currency)
    out = []
    for it in n.items:
        feats = "".join(f"<li>{inline(f)}</li>" for f in split_list(it.a("features")))
        out.append(f'<div class="card plan reveal{" featured" if it.yes("featured") else ""}">'
                   + (f'<span class="badge">{E(it.a("badge"))}</span>' if it.a("badge") else "")
                   + f'<h3>{E(it.a("name"))}</h3><div class="price" data-vm="{E(it.a("price"))}" data-vy="{E(it.a("yearly"))}" '
                     f'data-pm="{E(it.a("period"))}" data-py="{E(it.a("yearly-period", "/año"))}">{E(cur)}<span>{E(it.a("price"))}</span>'
                     f'<small>{E(it.a("period"))}</small></div>'
                   + (f'<p class="muted">{inline(it.text)}</p>' if it.text else "") + f"<ul>{feats}</ul>"
                   + button(it.a("button", "Elegir|#contacto"), "btn-primary" if it.yes("featured") else "btn-ghost") + "</div>")
    toggle = ""
    if any(it.a("yearly") for it in n.items):
        toggle = ('<div class="bill-toggle" data-billing><div><button type="button" aria-pressed="true" data-bill="month">Mensual'
                  f'</button><button type="button" aria-pressed="false" data-bill="year">{E(n.a("yearly-label", "Anual"))}</button></div></div>')
    return section(n, ctx, toggle + f'<div class="grid" style="--cols:{min(4, len(n.items)) or 1}">{"".join(out)}</div>',
                   "pricing", "precios")


@comp("faq", "Preguntas frecuentes desplegables.", 'id="faq" nav="FAQ" title="…" subtitle="…"',
      '<item q="¿Pregunta?">respuesta</item>', css="""
.faq-list { max-width: 780px; margin-inline: auto; display: grid; gap: .8rem; }
.faq details { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 1rem 1.25rem; }
.faq summary { cursor: pointer; font-weight: 600; list-style: none; display: flex; justify-content: space-between; gap: 1rem; }
.faq summary::-webkit-details-marker { display: none; }
.faq summary::after { content: "+"; font-size: 1.4rem; line-height: 1; color: var(--accent); transition: transform .2s; }
.faq details[open] summary::after { transform: rotate(45deg); }
.faq details > div { margin-top: .75rem; color: var(--muted); }""")
def r_faq(n, ctx):
    out = "".join(f'<details class="reveal"><summary>{inline(it.a("q") or it.a("question") or it.a("title"))}</summary>'
                  f'<div>{md(it.text)}</div></details>' for it in n.items)
    return section(n, ctx, f'<div class="faq-list">{out}</div>', "faq", "faq")


@comp("cta", "Banda de llamada a la acción.", 'title="…" text="…" button="Texto|#id" button2="Texto|#id"', "", css="""
.cta-box { border-radius: calc(var(--radius) * 1.5); padding: clamp(2rem, 5vw, 3.5rem); text-align: center; color: var(--accent-contrast);
  background: linear-gradient(135deg, var(--accent), var(--accent-2)); box-shadow: 0 30px 60px -30px var(--accent); }
.cta-box p { opacity: .9; max-width: 620px; margin: 0 auto 1.5rem; }
.cta-box .btn-primary { background: var(--accent-contrast); color: var(--accent); }
.cta-box .btn-ghost { color: var(--accent-contrast); border-color: color-mix(in srgb, var(--accent-contrast) 50%, transparent); }""")
def r_cta(n, ctx):
    inner = (f'<div class="cta-box reveal"><h2>{inline(n.a("title"))}</h2>'
             + (f'<p>{inline(n.a("text"))}</p>' if n.a("text") else body(n))
             + f'<div class="hero-actions" style="justify-content:center">{button(n.a("button"))}{button(n.a("button2"), "btn-ghost")}</div></div>')
    n2 = Node(n.tag, {k: v for k, v in n.attrs.items() if k != "title"}, n.children, "")
    n2.attrs.setdefault("alt", "no")
    return section(n2, ctx, inner, "cta", "accion")


@comp("gallery", "Galería/portafolio con filtro por categoría.", 'id="proyectos" nav="Proyectos" title="…" filter="yes"',
      '<item title="…" category="Web" image="🎨|img.jpg" link="Ver|url">descripción</item>', css="""
.tile { padding: 0; overflow: hidden; }
.tile .visual { width: 100%; aspect-ratio: 4/3; object-fit: cover; }
.tile .ph span { font-size: 3.5rem; }
.tile-body { padding: 1.1rem 1.25rem 1.3rem; }
.tile-body h3 { margin-bottom: .25rem; }""")
def r_gallery(n, ctx):
    cats = list(dict.fromkeys(it.a("category") for it in n.items if it.a("category")))
    filt = ('<div class="filters" data-filter-group><button class="active" data-filter="*">Todos</button>'
            + "".join(f'<button data-filter="{E(c)}">{E(c)}</button>' for c in cats) + "</div>") if n.yes("filter", True) and cats else ""
    tiles = []
    for it in n.items:
        lk = link(it.a("link"))
        tiles.append(f'<article class="card tile reveal" data-category="{E(it.a("category"))}">{visual(it.a("image"), it.a("title"))}'
                     f'<div class="tile-body">' + (f'<span class="badge">{E(it.a("category"))}</span>' if it.a("category") else "")
                     + f'<h3>{inline(it.a("title"))}</h3>{md(it.text) if it.text else ""}'
                     + (f'<a class="card-link" href="{E(lk[1])}">{E(lk[0])} →</a>' if lk[0] else "") + "</div></article>")
    return section(n, ctx, filt + f'<div class="grid" style="--cols:{E(n.a("columns", "3"))}">{"".join(tiles)}</div>',
                   "gallery", "galeria")


@comp("timeline", "Línea de tiempo (experiencia, historia).", 'id="…" nav="…" title="…"',
      '<item date="2024" title="…">texto</item>', css="""
.timeline-list { position: relative; max-width: 760px; margin-inline: auto; padding-left: 2rem; }
.timeline-list::before { content: ""; position: absolute; left: .45rem; top: .3rem; bottom: .3rem; width: 2px; background: var(--border); }
.tl-item { position: relative; margin-bottom: 2rem; }
.tl-item::before { content: ""; position: absolute; left: -1.83rem; top: .35rem; width: .9rem; height: .9rem; border-radius: 50%;
  background: var(--accent); box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 25%, transparent); }
.tl-date { font-size: .85rem; font-weight: 700; color: var(--accent); }""")
def r_timeline(n, ctx):
    out = "".join(f'<div class="tl-item reveal"><div class="tl-date">{E(it.a("date"))}</div><h3>{inline(it.a("title"))}</h3>'
                  f'{md(it.text)}</div>' for it in n.items)
    return section(n, ctx, f'<div class="timeline-list">{out}</div>', "timeline", "trayectoria")


@comp("skills", "Barras de habilidades.", 'id="…" nav="…" title="…"', '<item level="85">Diseño UI</item>', css="""
.skill { margin-bottom: 1.1rem; }
.skill-top { display: flex; justify-content: space-between; font-weight: 600; margin-bottom: .35rem; }
.skill-bar { height: 10px; border-radius: 999px; background: var(--border); overflow: hidden; }
.skill-bar span { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--accent), var(--accent-2)); }""")
def r_skills(n, ctx):
    out = []
    for it in n.items:
        lvl = re.sub(r"[^\d.]", "", it.a("level", "50")) or "50"
        out.append(f'<div class="skill reveal"><div class="skill-top"><span>{inline(it.text)}</span><span>{E(lvl)}%</span></div>'
                   f'<div class="skill-bar"><span style="width:{min(100, float(lvl)):.0f}%"></span></div></div>')
    return section(n, ctx, f'<div style="max-width:720px;margin-inline:auto">{"".join(out)}</div>', "skills", "habilidades")


@comp("posts", "Listado de entradas de blog con buscador y filtro.", 'id="blog" nav="Blog" title="…" search="yes" filter="yes"',
      '<item title="…" date="2026-05-01" category="Café" image="📝" link="Leer|#articulo">resumen</item>', css="""
.post-search { display: block; max-width: 420px; margin: 0 auto 1.2rem; }
.post-meta { font-size: .82rem; color: var(--muted); display: flex; gap: .6rem; margin-bottom: .4rem; }""")
def r_posts(n, ctx):
    cats = list(dict.fromkeys(it.a("category") for it in n.items if it.a("category")))
    top = ""
    if n.yes("search", True):
        top += '<div class="field post-search"><input type="search" placeholder="Buscar…" data-search aria-label="Buscar"></div>'
    if n.yes("filter", True) and cats:
        top += ('<div class="filters" data-filter-group><button class="active" data-filter="*">Todas</button>'
                + "".join(f'<button data-filter="{E(c)}">{E(c)}</button>' for c in cats) + "</div>")
    elif n.yes("search", True):
        top += '<div data-filter-group hidden></div>'
    out = []
    for it in n.items:
        lk = link(it.a("link", "Leer|#"))
        out.append(f'<article class="card tile reveal" data-category="{E(it.a("category"))}">{visual(it.a("image", "📝"), it.a("title"))}'
                   f'<div class="tile-body"><div class="post-meta"><span>{E(it.a("date"))}</span>'
                   + (f'<span class="badge">{E(it.a("category"))}</span>' if it.a("category") else "")
                   + f'</div><h3>{inline(it.a("title"))}</h3>{md(it.text)}<a class="card-link" href="{E(lk[1])}">{E(lk[0])} →</a></div></article>')
    return section(n, ctx, top + f'<div class="grid" style="--cols:{E(n.a("columns", "3"))}">{"".join(out)}</div>',
                   "posts", "blog")


@comp("article", "Artículo largo (entrada de blog, condiciones…) en Markdown.",
      'id="articulo" title="…" date="…" author="…" image="📝|foto.jpg"', "texto Markdown dentro de la etiqueta", css="""
.article { max-width: 760px; margin-inline: auto; }
.article .visual { width: 100%; aspect-ratio: 16/8; border-radius: var(--radius); object-fit: cover; margin-bottom: 1.5rem; }
.article-meta { color: var(--muted); margin-bottom: 1.5rem; }""")
def r_article(n, ctx):
    meta = " · ".join(x for x in (n.a("author"), n.a("date")) if x)
    inner = (f'<article class="article prose reveal">' + (visual(n.a("image"), n.a("title")) if n.a("image") else "")
             + f'<h1>{inline(n.a("title"))}</h1>' + (f'<div class="article-meta">{E(meta)}</div>' if meta else "") + body(n) + "</article>")
    n2 = Node(n.tag, {k: v for k, v in n.attrs.items() if k != "title"}, n.children, "")
    return section(n2, ctx, inner, "article-sec", "articulo")


# ---------------------------------------------------------------------------
# Tienda
# ---------------------------------------------------------------------------

@comp("products", "Catálogo de productos con filtro y botón «Añadir al carrito» (añade el carrito solo).",
      'id="tienda" nav="Tienda" title="…" currency="$" cart="yes" filter="yes"',
      '<item name="…" price="12.5" category="Café" image="☕|foto.jpg" badge="Nuevo">descripción</item>', css="""
.product .price-row { display: flex; justify-content: space-between; align-items: center; margin-top: 1rem; gap: .5rem; }
.product .price-tag { font-size: 1.3rem; font-weight: 800; }""")
def r_products(n, ctx):
    cur = n.a("currency", ctx.currency)
    ctx.currency = cur
    cart = n.yes("cart", True)
    ctx.has_cart = ctx.has_cart or cart
    cats = list(dict.fromkeys(it.a("category") for it in n.items if it.a("category")))
    filt = ('<div class="filters" data-filter-group><button class="active" data-filter="*">Todo</button>'
            + "".join(f'<button data-filter="{E(c)}">{E(c)}</button>' for c in cats) + "</div>") if n.yes("filter", True) and cats else ""
    out = []
    for it in n.items:
        try:
            price = float(re.sub(r"[^\d.]", "", it.a("price", "0").replace(",", ".")) or 0)
        except ValueError:
            price = 0.0
        data = E(json.dumps({"name": it.a("name"), "price": price}, ensure_ascii=False))
        btn = f'<button class="btn btn-primary btn-sm" data-add-cart="{data}">Añadir</button>' if cart else ""
        out.append(f'<article class="card tile product reveal" data-category="{E(it.a("category"))}">{visual(it.a("image", "🛍️"), it.a("name"))}'
                   f'<div class="tile-body">' + (f'<span class="badge">{E(it.a("badge"))}</span>' if it.a("badge") else "")
                   + f'<h3>{E(it.a("name"))}</h3>{md(it.text) if it.text else ""}<div class="price-row">'
                   f'<span class="price-tag">{E(cur)}{price:.2f}</span>{btn}</div></div></article>')
    return section(n, ctx, filt + f'<div class="grid" style="--cols:{E(n.a("columns", "3"))}">{"".join(out)}</div>',
                   "products", "tienda")


@comp("cart", "Carrito lateral con checkout simulado (se añade solo si hay products con cart=\"yes\").",
      'currency="$" checkout="yes" title="Tu carrito"', "", section=False, css="""
.cart { position: fixed; inset: 0 0 0 auto; width: min(420px, 100%); background: var(--bg); border-left: 1px solid var(--border);
  z-index: 80; transform: translateX(100%); transition: transform .3s ease; display: flex; flex-direction: column; padding: 1.4rem;
  box-shadow: -20px 0 60px rgba(0,0,0,.25); }
.cart.open { transform: none; }
.cart-head { display: flex; justify-content: space-between; align-items: center; }
.cart-close { background: none; border: 0; font-size: 1.6rem; cursor: pointer; color: var(--text); }
.cart-items { list-style: none; padding: 0; margin: 1rem 0; display: grid; gap: .8rem; overflow: auto; }
.cart-items li { display: grid; grid-template-columns: 1fr auto auto; gap: .8rem; align-items: center; }
.qty { display: inline-flex; align-items: center; gap: .4rem; }
.qty button { width: 26px; height: 26px; border-radius: 50%; border: 1px solid var(--border); background: none; color: var(--text); cursor: pointer; }
.cart-sum { display: flex; justify-content: space-between; font-size: 1.15rem; font-weight: 800; padding: 1rem 0; border-top: 1px solid var(--border); margin-top: auto; }""")
def r_cart(n, ctx):
    cur = n.a("currency", ctx.currency)
    co = ""
    if n.yes("checkout", True):
        co = ('<form class="checkout"><div class="field"><label for="co-n">Nombre</label><input id="co-n" required></div>'
              '<div class="field"><label for="co-e">Correo</label><input id="co-e" type="email" required></div>'
              '<button class="btn btn-primary" style="width:100%;justify-content:center">Finalizar pedido</button></form>')
    return (f'<aside class="cart" data-currency="{E(cur)}" aria-label="Carrito"><div class="cart-head"><h3>{E(n.a("title", "Tu carrito"))}</h3>'
            f'<button class="cart-close" aria-label="Cerrar">×</button></div><ul class="cart-items"></ul>'
            f'<div class="cart-sum"><span>Total</span><span class="cart-total">{E(cur)}0.00</span></div>{co}</aside>')


# ---------------------------------------------------------------------------
# Datos y paneles
# ---------------------------------------------------------------------------

@comp("kpis", "Tarjetas de indicadores (dashboard).", 'id="…" title="…"',
      '<item icon="💰" value="$12.4k" delta="+8%">Ventas del mes</item>', css="""
.kpi { display: flex; flex-direction: column; gap: .3rem; }
.kpi-top { display: flex; justify-content: space-between; color: var(--muted); font-size: .9rem; }
.kpi-value { font-size: 2rem; font-weight: 800; }
.delta.up { color: #12b76a; } .delta.down { color: #e5484d; }""")
def r_kpis(n, ctx):
    out = []
    for it in n.items:
        d = it.a("delta")
        cls = "down" if d.strip().startswith("-") else "up"
        out.append(f'<div class="card kpi reveal"><div class="kpi-top"><span>{inline(it.text)}</span><span>{E(it.a("icon"))}</span></div>'
                   f'<div class="kpi-value">{E(it.a("value"))}</div>' + (f'<span class="delta {cls}">{E(d)}</span>' if d else "") + "</div>")
    return section(n, ctx, f'<div class="grid" style="--cols:{min(4, len(n.items)) or 1}">{"".join(out)}</div>', "kpis", "indicadores")


_PALETTE = ["var(--accent)", "var(--accent-2)", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#0ea5e9", "#ec4899"]


@comp("chart", "Gráfico en SVG sin librerías: barras, línea, tarta o donut.", 'id="…" title="…" type="bar|line|pie|donut" unit="€"',
      '<item value="120">Enero</item>', css="""
.chart-box { padding: 1.5rem; }
.chart-box svg { width: 100%; height: auto; }
.chart-box text { fill: var(--muted); font-size: 12px; font-family: var(--font); }
.chart-box .bar { fill: var(--accent); }
.chart-box .bar:hover { fill: var(--accent-2); }
.chart-box .grid-line { stroke: var(--border); }
.chart-box .line { fill: none; stroke: var(--accent); stroke-width: 3; stroke-linejoin: round; stroke-linecap: round; }
.chart-box .area { fill: color-mix(in srgb, var(--accent) 14%, transparent); }
.chart-box .dot { fill: var(--bg); stroke: var(--accent); stroke-width: 3; }
.pie-wrap { display: grid; grid-template-columns: minmax(0, 320px) 1fr; gap: 2rem; align-items: center; }
.pie-wrap svg { max-width: 320px; }
.legend { list-style: none; padding: 0; margin: 0; display: grid; gap: .5rem; }
.legend li { display: flex; align-items: center; gap: .6rem; }
.legend i { width: 14px; height: 14px; border-radius: 4px; flex: none; }
.legend b { margin-left: auto; }
@media (max-width: 700px) { .pie-wrap { grid-template-columns: 1fr; justify-items: center; } }""")
def r_chart(n, ctx):
    import math
    vals = []
    for it in n.items:
        try:
            vals.append((inline(it.text) or it.a("label"), float(re.sub(r"[^\d.\-]", "", it.a("value", "0")) or 0)))
        except ValueError:
            vals.append((it.text, 0.0))
    if not vals:
        return ""
    unit = n.a("unit")
    kind = n.a("type", "bar").lower()
    plain = lambda lab: re.sub("<[^>]+>", "", lab)          # noqa: E731
    if kind in ("pie", "donut", "tarta", "dona", "rosco"):
        total = sum(max(0, v) for _, v in vals) or 1
        cx = cy = 100
        r, hole = 90, (52 if kind in ("donut", "dona", "rosco") else 0)
        ang, parts, legend = -math.pi / 2, [], []
        for i, (lab, v) in enumerate(vals):
            frac = max(0, v) / total
            a2 = ang + frac * 2 * math.pi
            col = _PALETTE[i % len(_PALETTE)]
            large = 1 if frac > .5 else 0
            x1, y1, x2, y2 = cx + r * math.cos(ang), cy + r * math.sin(ang), cx + r * math.cos(a2), cy + r * math.sin(a2)
            if frac >= .999:
                path = f'<circle cx="{cx}" cy="{cy}" r="{r}" style="fill:{col}"/>'
            else:
                path = (f'<path d="M{cx},{cy} L{x1:.2f},{y1:.2f} A{r},{r} 0 {large} 1 {x2:.2f},{y2:.2f} Z" style="fill:{col}">'
                        f'<title>{E(plain(lab))}: {v:g}{E(unit)} ({frac * 100:.0f}%)</title></path>')
            parts.append(path)
            val = f"{v:g}{E(unit)}" + ("" if unit.strip() == "%" else f" · {frac * 100:.0f}%")
            legend.append(f'<li><i style="background:{col}"></i><span>{lab}</span><b>{val}</b></li>')
            ang = a2
        if hole:
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{hole}" style="fill:var(--card)"/>'
                         f'<text x="{cx}" y="{cy + 6}" text-anchor="middle" style="font-size:20px;font-weight:800;fill:var(--text)">'
                         f'{total:g}{E(unit)}</text>')
        svg = f'<svg viewBox="0 0 200 200" role="img" aria-label="{E(n.a("title", "Gráfico"))}">{"".join(parts)}</svg>'
        return section(n, ctx, f'<div class="card chart-box reveal"><div class="pie-wrap">{svg}<ul class="legend">{"".join(legend)}'
                               f'</ul></div></div>', "chart", "grafico")
    W, H, pad, top = 640, 300, 40, 20
    mx = max(v for _, v in vals) or 1
    parts = [f'<line class="grid-line" x1="{pad}" x2="{W - pad}" y1="{top + (H - top - 40) * k / 4:.1f}" '
             f'y2="{top + (H - top - 40) * k / 4:.1f}"/>' for k in range(5)]
    if kind in ("line", "linea", "línea", "area"):
        step = (W - pad * 2) / max(1, len(vals) - 1)
        pts = [(pad + i * step, H - 40 - (H - top - 40) * v / mx) for i, (_, v) in enumerate(vals)]
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        parts.append(f'<polygon class="area" points="{pad},{H - 40} {poly} {pts[-1][0]:.1f},{H - 40}"/>'
                     f'<polyline class="line" points="{poly}"/>')
        for (x, y), (lab, v) in zip(pts, vals):
            parts.append(f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="5"><title>{E(plain(lab))}: {v:g}{E(unit)}</title></circle>'
                         f'<text x="{x:.1f}" y="{y - 10:.1f}" text-anchor="middle">{v:g}{E(unit)}</text>'
                         f'<text x="{x:.1f}" y="{H - 18}" text-anchor="middle">{E(plain(lab))[:14]}</text>')
    else:
        bw = (W - pad * 2) / len(vals)
        for i, (lab, v) in enumerate(vals):
            h = (H - top - 40) * v / mx
            x = pad + i * bw + bw * 0.18
            y = H - 40 - h
            parts.append(f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bw * 0.64:.1f}" height="{h:.1f}" rx="6">'
                         f'<title>{E(plain(lab))}: {v:g}{E(unit)}</title></rect>'
                         f'<text x="{x + bw * 0.32:.1f}" y="{y - 6:.1f}" text-anchor="middle">{v:g}{E(unit)}</text>'
                         f'<text x="{x + bw * 0.32:.1f}" y="{H - 18}" text-anchor="middle">{E(plain(lab))[:14]}</text>')
    svg = f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{E(n.a("title", "Gráfico"))}">{"".join(parts)}</svg>'
    return section(n, ctx, f'<div class="card chart-box reveal">{svg}</div>', "chart", "grafico")


@comp("table", "Tabla de datos con búsqueda y orden al pulsar la cabecera.",
      'id="…" title="…" columns="Nombre, Cargo, Costo" search="yes" sort="yes"', "<row>Ana | Analista | 290</row>")
def r_table(n, ctx):
    cols = [c.strip() for c in n.a("columns").split(",") if c.strip()]
    sort = n.yes("sort", True)
    th = "".join(f'<th{" data-sort" if sort else ""} scope="col">{E(c)}</th>' for c in cols)
    rows = []
    for it in n.items:
        cells = [c.strip() for c in it.text.split("|")] if it.text else []
        rows.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
    search = ('<div class="field tsearch"><input type="search" placeholder="Buscar en la tabla…" data-table-search '
              'aria-label="Buscar"></div>') if n.yes("search", True) else ""
    return section(n, ctx, f'<div class="data-table reveal">{search}<table><thead><tr>{th}</tr></thead>'
                           f'<tbody>{"".join(rows)}</tbody></table></div>', "table-sec", "tabla")


def _workdays(start: dt.date, holidays: set[dt.date]):
    d = start
    while True:
        if d.weekday() < 5 and d not in holidays:
            yield d
        d += dt.timedelta(days=1)


@comp("gantt", "Diagrama de Gantt. Calcula SOLO inicios, fines y ruta crítica a partir de duraciones y precedencias "
               "(no hace falta poner start). Con start-date convierte a fechas laborables.",
      'id="gantt" nav="Gantt" title="…" unit="día" start-date="2010-06-01" holidays="2010-06-24, 2010-07-05" table="yes"',
      '<task id="1" days="1">Reunión de inicio</task>  <task id="2" days="12" after="1">Recopilación</task>  '
      '(after = ids previos separados por coma; start="0" opcional)', css="""
.gantt-wrap { overflow-x: auto; }
.gantt-inner { min-width: 720px; }
.gantt-row, .gantt-scale { display: grid; grid-template-columns: 240px 1fr; align-items: center; }
.gantt-row { height: 34px; }
.gantt-row:nth-child(even) { background: color-mix(in srgb, var(--surface) 70%, transparent); }
.gantt-label { padding: 0 .75rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: .92rem; }
.gantt-track { position: relative; height: 18px; margin-right: .75rem; background: color-mix(in srgb, var(--border) 45%, transparent); border-radius: 5px; }
.gantt-bar { position: absolute; top: 0; height: 100%; min-width: 3px; border-radius: 5px; background: #2ecc71; }
.gantt-bar.critical { background: #e74c3c; }
.gantt-ruler { position: relative; height: 26px; margin-right: .75rem; border-bottom: 1px solid var(--border); }
.gantt-ruler span { position: absolute; bottom: 4px; transform: translateX(-50%); font-size: .75rem; color: var(--muted); }
.gantt-legend { display: flex; gap: 1.2rem; margin-top: 1rem; font-size: .9rem; color: var(--muted); }
.gantt-legend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: .4rem; vertical-align: -1px; }""")
def r_gantt(n, ctx):
    tasks = []
    for k, it in enumerate(n.items, 1):
        tid = it.a("id", str(k))
        try:
            days = max(0.0, float(it.a("days", it.a("duration", "1")).replace(",", ".")))
        except ValueError:
            days = 1.0
        after = [a.strip() for a in re.split(r"[,;\s]+", it.a("after", it.a("deps"))) if a.strip()]
        st = it.a("start")
        tasks.append({"id": tid, "name": it.text or it.a("name") or f"Tarea {tid}", "days": days, "after": after,
                      "start": float(st) if re.fullmatch(r"\d+(\.\d+)?", st) else None, "crit": it.attrs.get("critical")})
    by = {t["id"]: t for t in tasks}
    for t in tasks:
        for a in t["after"]:
            if a not in by:
                ctx.warnings.append(f"gantt: la tarea {t['id']} depende de '{a}', que no existe")
    # CPM: inicio temprano (ES), fin temprano (EF)
    order, seen = [], set()

    def visit(t, stack=()):
        if t["id"] in seen:
            return
        if t["id"] in stack:
            ctx.warnings.append(f"gantt: dependencia circular en {t['id']}")
            return
        for a in t["after"]:
            if a in by:
                visit(by[a], stack + (t["id"],))
        seen.add(t["id"])
        order.append(t)

    for t in tasks:
        visit(t)
    for t in order:
        es = max([by[a]["ef"] for a in t["after"] if a in by and "ef" in by[a]] + [0.0])
        t["es"] = t["start"] if t["start"] is not None else es
        t["ef"] = t["es"] + t["days"]
    total = max((t["ef"] for t in tasks), default=1) or 1
    succ = {t["id"]: [s for s in tasks if t["id"] in s["after"]] for t in tasks}
    for t in reversed(order):
        nxt = [s["ls"] for s in succ[t["id"]] if "ls" in s]
        t["lf"] = min(nxt) if nxt else total
        t["ls"] = t["lf"] - t["days"]
    for t in tasks:
        c = t["crit"]
        t["critical"] = (c.strip().lower() in ("yes", "si", "sí", "true", "1")) if c is not None else abs(t["ls"] - t["es"]) < 1e-6
    # fechas laborables
    dates = None
    sd = n.a("start-date")
    if sd:
        try:
            start = dt.date.fromisoformat(sd)
            hol = set()
            for h in split_list(n.a("holidays").replace(",", ";")):
                try:
                    hol.add(dt.date.fromisoformat(h.strip()))
                except ValueError:
                    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", h.strip())
                    if m:
                        hol.add(dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))))
            gen = _workdays(start, hol)
            dates = [next(gen) for _ in range(int(total) + 2)]
        except ValueError:
            ctx.warnings.append("gantt: start-date debe ser AAAA-MM-DD")
    fmt = (lambda d: dates[int(d)].strftime("%d/%m/%Y")) if dates else (lambda d: f"{n.a('unit', 'día')} {d:g}")
    step = max(1, round(total / 10 / 5) * 5) if total > 20 else max(1, round(total / 10))
    ticks = "".join(f'<span style="left:{d / total * 100:.2f}%">{d}</span>' for d in range(0, int(total) + 1, int(step)))
    rows = []
    for t in tasks:
        left, width = t["es"] / total * 100, t["days"] / total * 100
        end_day = t["ef"] - 1 if t["days"] >= 1 else t["ef"]
        tip = f'{t["name"]}: {fmt(t["es"])} → {fmt(max(t["es"], end_day))} ({t["days"]:g} {n.a("unit", "día")}s)'
        rows.append(f'<div class="gantt-row"><div class="gantt-label" title="{E(t["name"])}">{E(t["id"])}. {E(t["name"])}</div>'
                    f'<div class="gantt-track"><div class="gantt-bar{" critical" if t["critical"] else ""}" '
                    f'style="left:{left:.2f}%;width:{width:.2f}%" title="{E(tip)}"></div></div></div>')
    crit = [t["id"] for t in order if t["critical"]]
    legend = (f'<div class="gantt-legend"><span><i style="background:#e74c3c"></i>Ruta crítica: {E(" → ".join(crit))}</span>'
              f'<span><i style="background:#2ecc71"></i>Con holgura</span><span>Duración total: {total:g} {E(n.a("unit", "día"))}s'
              + (f' · fin {E(fmt(total - 1))}' if dates else "") + "</span></div>")
    table = ""
    if n.yes("table"):
        tr = "".join(f'<tr><td>{E(t["id"])}</td><td>{E(t["name"])}</td><td>{t["days"]:g}</td><td>{E(", ".join(t["after"]) or "—")}</td>'
                     f'<td>{E(fmt(t["es"]))}</td><td>{E(fmt(max(t["es"], t["ef"] - 1)))}</td><td>{t["ls"] - t["es"]:g}</td>'
                     f'<td>{"Sí" if t["critical"] else "No"}</td></tr>' for t in tasks)
        table = ('<div class="data-table" style="margin-top:1.5rem"><table><thead><tr><th>Nº</th><th>Actividad</th><th>Duración</th>'
                 '<th>Precede</th><th>Inicio</th><th>Fin</th><th>Holgura</th><th>Crítica</th></tr></thead>'
                 f'<tbody>{tr}</tbody></table></div>')
    inner = (f'<div class="card reveal"><div class="gantt-wrap"><div class="gantt-inner"><div class="gantt-scale">'
             f'<div class="gantt-label muted">Actividad</div><div class="gantt-ruler">{ticks}</div></div>{"".join(rows)}</div></div>'
             f'{legend}</div>{table}')
    return section(n, ctx, inner, "gantt", "gantt")


@comp("crud", "Gestión de registros (listar, buscar, crear, editar, borrar, exportar CSV) guardados en el navegador.",
      'id="…" nav="…" title="…" entity="cliente" plural="clientes" key="clientes" '
      'fields="nombre:texto*, email:email*, telefono:tel, estado:Activo|Inactivo" csv="yes"',
      "<row>Ana | ana@mail.com | 555-1234 | Activo</row> (datos de ejemplo opcionales)", css="""
.crud-bar { display: flex; flex-wrap: wrap; gap: .8rem; align-items: center; justify-content: space-between; margin-bottom: 1rem; }
.crud-bar .field { margin: 0; min-width: 240px; flex: 1; max-width: 360px; }
.crud .actions { white-space: nowrap; text-align: right; }
.crud .danger:hover { color: #e5484d; border-color: #e5484d; }
.crud .empty { text-align: center; padding: 2rem; }
.crud dialog { border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg); color: var(--text); width: min(520px, 92vw); padding: 1.5rem; }
.crud dialog::backdrop { background: rgba(0,0,0,.5); }
.dlg-actions { display: flex; gap: .6rem; justify-content: flex-end; }""")
def r_crud(n, ctx):
    fields = []
    for spec in n.a("fields", "nombre:texto*, descripcion:texto").split(","):
        spec = spec.strip()
        if not spec:
            continue
        name, _, typ = spec.partition(":")
        req = typ.endswith("*") or name.endswith("*")
        name, typ = name.strip(" *"), (typ or "texto").strip(" *")
        key = re.sub(r"[^\w]", "_", name.lower())
        fields.append({"name": key, "label": name.strip().capitalize(), "type": typ, "required": req})
    seed = []
    for it in n.items:
        cells = [c.strip() for c in it.text.split("|")]
        seed.append({f["name"]: (cells[i] if i < len(cells) else "") for i, f in enumerate(fields)})
    ent = n.a("entity", "registro")
    cfg = {"key": n.a("key", re.sub(r"\W", "", n.a("plural", ent + "s").lower()) or "datos"), "singular": ent,
           "plural": n.a("plural", ent + "s"), "fields": [{k: f[k] for k in ("name", "label", "required")} for f in fields],
           "seed": seed}

    def control(f):
        t = f["type"].lower()
        req = " required" if f["required"] else ""
        if "|" in t or "|" in f["type"]:
            opts = "".join(f"<option>{E(o.strip())}</option>" for o in f["type"].split("|"))
            return f'<select name="{f["name"]}" id="f-{f["name"]}"{req}>{opts}</select>'
        typ = {"email": "email", "tel": "tel", "telefono": "tel", "numero": "number", "number": "number", "fecha": "date",
               "date": "date", "url": "url"}.get(t, "text")
        return f'<input name="{f["name"]}" id="f-{f["name"]}" type="{typ}"{req}>'

    th = "".join(f"<th>{E(f['label'])}</th>" for f in fields) + "<th></th>"
    form = "".join(f'<div class="field"><label for="f-{f["name"]}">{E(f["label"])}{" *" if f["required"] else ""}</label>{control(f)}</div>'
                   for f in fields)
    csv = '<button class="btn btn-ghost btn-sm crud-csv">Exportar CSV</button>' if n.yes("csv", True) else ""
    inner = (f'<div class="crud reveal" data-config="{E(json.dumps(cfg, ensure_ascii=False))}"><div class="crud-bar">'
             f'<div class="field"><input type="search" placeholder="Buscar…" data-crud-search aria-label="Buscar"></div>'
             f'<span class="muted crud-count"></span><div style="display:flex;gap:.5rem">{csv}'
             f'<button class="btn btn-primary btn-sm crud-new">+ Nuevo {E(ent)}</button></div></div>'
             f'<div class="data-table"><table><thead><tr>{th}</tr></thead><tbody></tbody></table></div>'
             f'<dialog><form method="dialog"><h3 class="dlg-title"></h3>{form}<div class="dlg-actions">'
             f'<button type="button" class="btn btn-ghost crud-cancel">Cancelar</button><button class="btn btn-primary">Guardar</button>'
             f'</div></form></dialog></div>')
    return section(n, ctx, inner, "crud-sec", "gestion")


@comp("sidebar", "Menú lateral: convierte la página en un panel (dashboard).", 'brand="Mi panel"',
      '<link href="#id" icon="📊">Texto</link>', section=False, css="""
body.dashboard { display: grid; grid-template-columns: 250px 1fr; min-height: 100vh; }
.sidebar { position: sticky; top: 0; height: 100vh; overflow: auto; background: var(--surface); border-right: 1px solid var(--border); padding: 1.4rem 1rem; }
.sidebar .brand { margin-bottom: 1.6rem; padding: 0 .6rem; }
.sidebar nav { display: grid; gap: .25rem; }
.sidebar nav a { display: flex; gap: .7rem; align-items: center; padding: .65rem .8rem; border-radius: 10px; color: var(--muted); text-decoration: none; }
.sidebar nav a:hover { background: var(--card); color: var(--text); }
body.dashboard main .section { padding: 2rem 0; }
body.dashboard main .section-head { text-align: left; margin: 0 0 1.2rem; }
@media (max-width: 860px) { body.dashboard { grid-template-columns: 1fr; } .sidebar { position: static; height: auto; } }""")
def r_sidebar(n, ctx):
    links = "".join(f'<a href="{E(it.a("href", "#"))}"><span>{E(it.a("icon"))}</span>{E(it.text or it.a("label"))}</a>'
                    for it in n.items)
    return (f'<aside class="sidebar"><a class="brand" href="#">{E(n.a("brand", ctx.brand or "Panel"))}</a>'
            f'<nav aria-label="Panel">{links}</nav></aside>')


# ---------------------------------------------------------------------------
# Contacto y pie
# ---------------------------------------------------------------------------

@comp("contact", "Contacto: datos + formulario con validación (guarda en el navegador y abre el correo si pones email).",
      'id="contacto" nav="Contacto" title="…" subtitle="…" email="hola@…" phone="…" address="…" hours="…" '
      'whatsapp="58412…" fields="nombre, email, telefono, mensaje" button="Enviar"', "", css="""
.contact-inner { display: grid; grid-template-columns: .85fr 1.15fr; gap: 2.5rem; align-items: start; }
.contact-info { display: grid; gap: 1rem; }
.contact-info div { display: flex; gap: .8rem; align-items: flex-start; }
.contact-info b { display: block; }
@media (max-width: 820px) { .contact-inner { grid-template-columns: 1fr; } }""")
def r_contact(n, ctx):
    info = []
    for icon, key, label in (("✉️", "email", "Correo"), ("📞", "phone", "Teléfono"), ("📍", "address", "Dirección"),
                             ("🕒", "hours", "Horario")):
        if n.a(key):
            val = n.a(key)
            val_html = f'<a href="mailto:{E(val)}">{E(val)}</a>' if key == "email" else E(val)
            info.append(f"<div><span>{icon}</span><span><b>{label}</b>{val_html}</span></div>")
    if n.a("whatsapp"):
        num = re.sub(r"\D", "", n.a("whatsapp"))
        info.append(f'<div><span>💬</span><span><b>WhatsApp</b><a href="https://wa.me/{num}">+{num}</a></span></div>')
    ctrl = []
    for spec in n.a("fields", "nombre, email, mensaje").split(","):
        name, _, typ = spec.strip().partition(":")
        name = name.strip(" *")
        if not name:
            continue
        low = name.lower()
        if not typ:          # tipo deducido del nombre: «fecha», «hora», «mensaje», «email»…
            typ = ("area" if low in ("mensaje", "message", "comentario", "comentarios") else
                   "email" if low in ("email", "correo") else "tel" if low in ("telefono", "teléfono", "phone") else
                   "fecha" if low in ("fecha", "dia", "día") else "hora" if low == "hora" else "texto")
        req = spec.strip().endswith("*") or low not in ("telefono", "teléfono", "phone", "comentarios", "empresa")
        ctrl.append(_field(name, typ.strip(" *"), req, "c"))
    mail = f' data-mailto="{E(n.a("email"))}"' if n.a("email") else ""
    label = n.a("button", "Enviar")
    label = "Enviar" if re.match(r"^(#|/|https?://)", label) else label
    form = (f'<form class="card" data-validate novalidate{mail} data-ok="{E(n.a("ok", "¡Gracias! Te responderemos pronto."))}">'
            f'{"".join(ctrl)}<button class="btn btn-primary" type="submit">{E(label)}</button></form>')
    return section(n, ctx, f'<div class="contact-inner"><div class="contact-info reveal">{body(n)}{"".join(info)}</div>'
                           f'<div class="reveal">{form}</div></div>', "contact", "contacto")


@comp("footer", "Pie de página con enlaces y copyright.", 'brand="…" text="…" copyright="…"',
      '<link href="#id">Texto</link>', section=False, css="""
.footer { border-top: 1px solid var(--border); padding: 2.5rem 0; color: var(--muted); font-size: .92rem; }
.footer-inner { display: flex; flex-wrap: wrap; gap: 1.5rem; justify-content: space-between; align-items: center; }
.footer nav { display: flex; flex-wrap: wrap; gap: 1.2rem; }
.footer nav a { color: var(--muted); }
.footer strong { color: var(--text); }""")
def r_footer(n, ctx):
    brand = n.a("brand", ctx.brand)
    links = "".join(f'<a href="{E(it.a("href", "#"))}">{E(it.text or it.a("label"))}</a>' for it in n.items) or "{{NAV}}"
    copy_ = E(n.a("copyright")) if n.a("copyright") else f'© <span data-year></span> {E(brand)}'
    contact = " · ".join(x for x in ((f'<a href="mailto:{E(n.a("email"))}">{E(n.a("email"))}</a>' if n.a("email") else ""),
                                     (f'<a href="tel:{E(re.sub(r"[^+0-9]", "", n.a("phone")))}">{E(n.a("phone"))}</a>'
                                      if n.a("phone") else "")) if x)
    return (f'<footer class="footer"><div class="container footer-inner"><div><strong>{E(brand)}</strong>'
            + (f'<div>{inline(n.a("text"))}</div>' if n.a("text") else "") + (f"<div>{contact}</div>" if contact else "")
            + f'</div><nav aria-label="Pie">{links}</nav>'
            f'<div>{copy_}</div></div></footer>')


# ---------------------------------------------------------------------------
# Más contenido
# ---------------------------------------------------------------------------

@comp("features", "Características en filas alternas: imagen a un lado y texto al otro.",
      'id="caracteristicas" nav="Características" title="…" subtitle="…" eyebrow="…"',
      '<item title="…" image="📱|foto.jpg" eyebrow="…" button="Texto|#id">texto Markdown</item>', css="""
.feature-row { display: grid; grid-template-columns: 1fr 1fr; gap: clamp(2rem, 5vw, 4rem); align-items: center; margin-bottom: clamp(3rem, 7vw, 5rem); }
.feature-row:last-child { margin-bottom: 0; }
.feature-row:nth-child(even) > .visual { order: 2; }
.feature-row .visual { width: 100%; aspect-ratio: 4/3; border-radius: var(--radius); object-fit: cover; box-shadow: var(--shadow); }
.feature-row .ph span { font-size: clamp(3rem, 8vw, 5.5rem); }
.feature-row h3 { font-size: clamp(1.4rem, 3vw, 1.9rem); }
@media (max-width: 820px) { .feature-row { grid-template-columns: 1fr; } .feature-row:nth-child(even) > .visual { order: 0; } }""")
def r_features(n, ctx):
    rows = []
    for it in n.items:
        eye = f'<span class="eyebrow">{E(it.a("eyebrow"))}</span>' if it.a("eyebrow") else ""
        btn = button(it.a("button"), "btn-ghost")
        rows.append(f'<div class="feature-row reveal">{visual(it.a("image", "✨"), it.a("title"))}<div>{eye}'
                    f'<h3>{inline(it.a("title"))}</h3>{md(it.text)}'
                    + (f'<div class="hero-actions">{btn}</div>' if btn else "") + "</div></div>")
    return section(n, ctx, "".join(rows), "features", "caracteristicas")


@comp("team", "Equipo: foto o iniciales, nombre, cargo, bio breve y enlaces.",
      'id="equipo" nav="Equipo" title="…" subtitle="…" columns="3|4"',
      '<item name="Ana Ruiz" role="Directora" image="👩‍💼|foto.jpg" links="LinkedIn|https://…; Correo|mailto:…">bio</item>',
      css="""
.member { text-align: center; }
.member .visual, .avatar-lg { width: 120px; height: 120px; border-radius: 50%; margin: 0 auto 1rem; object-fit: cover; }
.member .ph span { font-size: 3rem; }
.avatar-lg { display: grid; place-items: center; font-size: 2.4rem; font-weight: 800; color: #fff;
  background: linear-gradient(135deg, hsl(var(--h) 70% 55%), hsl(calc(var(--h) + 40) 65% 45%)); }
.member .role { color: var(--accent); font-weight: 600; font-size: .92rem; margin-bottom: .6rem; }
.member-links { display: flex; gap: .8rem; justify-content: center; flex-wrap: wrap; margin-top: .6rem; font-size: .9rem; }""")
def r_team(n, ctx):
    out = []
    for it in n.items:
        name = it.a("name", "Nombre")
        img = it.a("image")
        pic = (visual(img, name) if img else f'<div class="avatar-lg" style="--h:{hue(name)}">'
               f'{E("".join(w[0] for w in name.split()[:2]).upper())}</div>')
        links = "".join(f'<a href="{E(link(l)[1])}">{E(link(l)[0])}</a>' for l in split_list(it.a("links")))
        out.append(f'<div class="card member reveal">{pic}<h3>{E(name)}</h3><div class="role">{E(it.a("role"))}</div>'
                   + (md(it.text) if it.text else "") + (f'<div class="member-links">{links}</div>' if links else "") + "</div>")
    cols = n.a("columns", str(min(4, len(n.items)) or 1))
    return section(n, ctx, f'<div class="grid" style="--cols:{E(cols)}">{"".join(out)}</div>', "team", "equipo")


@comp("logos", "Logos o nombres de clientes y marcas («confían en nosotros»), en fila o en cinta animada.",
      'title="Confían en nosotros" variant="row|marquee"', '<item image="logo.png">Nombre</item> (sin image se ve el nombre)',
      css="""
.logos { padding: 2.5rem 0; }
.logos .section-head { margin-bottom: 1.5rem; }
.logos .section-head h2 { font-size: .85rem; text-transform: uppercase; letter-spacing: .14em; color: var(--muted); }
.logo-row { display: flex; flex-wrap: wrap; justify-content: center; align-items: center; gap: 2rem 3.5rem; }
.logo-row img { height: 38px; width: auto; filter: grayscale(1); opacity: .7; transition: .2s; }
.logo-row img:hover { filter: none; opacity: 1; }
.wordmark { font-size: 1.35rem; font-weight: 800; letter-spacing: -.02em; color: var(--muted); white-space: nowrap; }
.marquee { overflow: hidden; -webkit-mask-image: linear-gradient(90deg, transparent, #000 10%, #000 90%, transparent);
  mask-image: linear-gradient(90deg, transparent, #000 10%, #000 90%, transparent); }
.marquee .logo-row { flex-wrap: nowrap; width: max-content; padding-right: 3.5rem; animation: marquee 30s linear infinite; }
.marquee:hover .logo-row { animation-play-state: paused; }
.logo-dup { display: contents; }
@keyframes marquee { to { transform: translateX(-50%); } }
@media (prefers-reduced-motion: reduce) { .marquee .logo-row { animation: none; flex-wrap: wrap; width: auto; } .logo-dup { display: none; } }""")
def r_logos(n, ctx):
    one = "".join(f'<img src="{E(it.a("image"))}" alt="{E(it.text)}" loading="lazy">' if is_url(it.a("image"))
                  else f'<span class="wordmark">{E((it.a("image") + " ") if it.a("image") else "")}{E(it.text)}</span>'
                  for it in n.items)
    if n.a("variant") == "marquee":
        inner = f'<div class="marquee reveal"><div class="logo-row">{one}<span class="logo-dup" aria-hidden="true">{one}</span></div></div>'
    else:
        inner = f'<div class="logo-row reveal">{one}</div>'
    n.attrs.setdefault("alt", "no")
    return section(n, ctx, inner, "logos", "clientes")


# ---------------------------------------------------------------------------
# Interactivos
# ---------------------------------------------------------------------------

@comp("tabs", "Pestañas: contenido agrupado que se cambia con botones (servicios, horarios, detalles…).",
      'id="…" nav="…" title="…" subtitle="…"', '<item title="Pestaña" icon="📦">texto Markdown</item>', css="""
.tabs-box { max-width: 860px; margin-inline: auto; }
.tab-panel[hidden] { display: none; }""")
def r_tabs(n, ctx):
    sid = re.sub(r"[^\w-]", "", n.a("id", "pestanas")) or "pestanas"
    btns, panels = [], []
    for i, it in enumerate(n.items):
        tid = f"{sid}-t{i}"
        label = (it.a("icon") + " " if it.a("icon") else "") + it.a("title", f"Pestaña {i + 1}")
        btns.append(f'<button type="button" role="tab" id="{tid}" aria-controls="{tid}-p" '
                    f'aria-selected="{"true" if i == 0 else "false"}" tabindex="{0 if i == 0 else -1}">{E(label)}</button>')
        panels.append(f'<div class="tab-panel prose" role="tabpanel" id="{tid}-p" aria-labelledby="{tid}"'
                      f'{"" if i == 0 else " hidden"}>{md(it.text)}</div>')
    return section(n, ctx, f'<div class="tabs-box reveal" data-tabs><div class="tab-list" role="tablist">{"".join(btns)}'
                           f'</div>{"".join(panels)}</div>', "tabs-sec", "pestanas")


@comp("carousel", "Carrusel de diapositivas con flechas, puntos, deslizamiento y autoplay.",
      'id="…" title="…" autoplay="6" height="420px"', '<item title="…" image="🏖️|foto.jpg" button="Texto|#id">texto</item>',
      css="""
.carousel { position: relative; }
.car-track { display: flex; overflow-x: auto; scroll-snap-type: x mandatory; scroll-behavior: smooth; border-radius: var(--radius);
  scrollbar-width: none; box-shadow: var(--shadow); }
.car-track::-webkit-scrollbar { display: none; }
.slide { position: relative; flex: 0 0 100%; scroll-snap-align: start; min-height: var(--car-h, 420px); display: grid; align-items: end;
  color: #fff; overflow: hidden; }
.slide > .visual { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.slide > .ph span { font-size: clamp(4rem, 12vw, 8rem); }
.slide-body { position: relative; padding: clamp(1.5rem, 4vw, 3rem); background: linear-gradient(0deg, rgba(0,0,0,.72), transparent); }
.slide-body h3 { font-size: clamp(1.4rem, 3vw, 2rem); }
.slide-body p { color: rgba(255,255,255,.88); max-width: 640px; }
.car-btn { position: absolute; top: calc(50% - 1.5rem); width: 44px; height: 44px; border-radius: 50%; border: 0; cursor: pointer;
  background: rgba(255,255,255,.9); color: #111; font-size: 1.5rem; line-height: 1; box-shadow: var(--shadow); z-index: 2; }
.car-prev { left: .8rem; } .car-next { right: .8rem; }
.car-dots { display: flex; gap: .5rem; justify-content: center; margin-top: 1rem; }
.car-dots button { width: 10px; height: 10px; border-radius: 999px; border: 0; background: var(--border); cursor: pointer; padding: 0; transition: width .2s; }
.car-dots button[aria-current="true"] { background: var(--accent); width: 26px; }""")
def r_carousel(n, ctx):
    total = len(n.items)
    slides = []
    for i, it in enumerate(n.items):
        slides.append(f'<div class="slide" role="group" aria-roledescription="diapositiva" aria-label="{i + 1} de {total}">'
                      f'{visual(it.a("image", "🖼️"), it.a("title"))}<div class="slide-body">'
                      + (f'<h3>{inline(it.a("title"))}</h3>' if it.a("title") else "")
                      + (f"<p>{inline(it.text)}</p>" if it.text else "") + button(it.a("button")) + "</div></div>")
    h = n.a("height", "420px")
    h = h if re.fullmatch(r"\d{2,4}(px|vh|rem)", h) else "420px"
    arrows = ('<button type="button" class="car-btn car-prev" aria-label="Anterior">‹</button>'
              '<button type="button" class="car-btn car-next" aria-label="Siguiente">›</button>') if total > 1 else ""
    inner = (f'<div class="carousel reveal" data-carousel data-autoplay="{E(n.a("autoplay", "6"))}" style="--car-h:{h}" '
             f'aria-roledescription="carrusel"><div class="car-track">{"".join(slides)}</div>{arrows}'
             f'<div class="car-dots"></div></div>')
    return section(n, ctx, inner, "carousel-sec", "carrusel")


_FIELD_TYPES = {"email": "email", "correo": "email", "tel": "tel", "telefono": "tel", "teléfono": "tel", "numero": "number",
                "número": "number", "number": "number", "fecha": "date", "date": "date", "hora": "time", "time": "time",
                "url": "url", "web": "url", "password": "password", "contraseña": "password"}


def _field(name: str, typ: str, req: bool, prefix: str) -> str:
    key = re.sub(r"\W", "_", name.lower()).strip("_") or "campo"
    fid = f"{prefix}-{key}"
    label = E(name.replace("_", " ").strip().capitalize()) + (" *" if req else "")
    r = " required" if req else ""
    t = typ.lower().strip()
    if "|" in typ:
        opts = "".join(f"<option>{E(o.strip())}</option>" for o in typ.split("|") if o.strip())
        return (f'<div class="field"><label for="{fid}">{label}</label><select id="{fid}" name="{key}"{r}>'
                f'<option value="">Elige…</option>{opts}</select><span class="error"></span></div>')
    if t in ("area", "textarea", "texto largo", "mensaje", "comentarios"):
        return (f'<div class="field full"><label for="{fid}">{label}</label><textarea id="{fid}" name="{key}" rows="4"{r}>'
                f'</textarea><span class="error"></span></div>')
    if t in ("check", "checkbox", "casilla", "acepto"):
        return (f'<div class="field full check"><input type="checkbox" id="{fid}" name="{key}"{r}>'
                f'<label for="{fid}">{label}</label><span class="error"></span></div>')
    return (f'<div class="field"><label for="{fid}">{label}</label><input id="{fid}" name="{key}" '
            f'type="{_FIELD_TYPES.get(t, "text")}"{r}><span class="error"></span></div>')


@comp("form", "Formulario a medida (reservas, inscripciones, encuestas, pedidos) con validación.",
      'id="…" nav="…" title="…" subtitle="…" fields="nombre:texto*, email:email*, fecha:fecha*, personas:numero, '
      'servicio:Corte|Tinte|Peinado, comentarios:area, acepto:check*" button="Enviar" ok="¡Recibido!" email="destino@…"',
      "texto opcional encima del formulario", css="""
.form-box { max-width: 660px; margin-inline: auto; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 1rem; }
.form-grid .full { grid-column: 1 / -1; }
.field.check { display: flex; flex-wrap: wrap; align-items: center; gap: .6rem; }
.field.check input { width: 1.15rem; height: 1.15rem; accent-color: var(--accent); }
.field.check .error { flex-basis: 100%; }
@media (max-width: 600px) { .form-grid { grid-template-columns: 1fr; } }""")
def r_form(n, ctx):
    sid = re.sub(r"[^\w-]", "", n.a("id", "formulario")) or "formulario"
    ctrls = []
    for spec in n.a("fields", "nombre:texto*, email:email*, mensaje:area*").split(","):
        if not spec.strip():
            continue
        name, _, typ = spec.strip().partition(":")
        req = name.strip().endswith("*") or typ.strip().endswith("*")
        ctrls.append(_field(name.strip(" *"), (typ or "texto").strip(" *"), req, sid))
    mail = f' data-mailto="{E(n.a("email"))}" data-subject="{E(n.a("title", "Formulario"))}"' if n.a("email") else ""
    form = (f'<form class="card form-box reveal" data-validate novalidate data-store="lai-{E(sid)}"{mail} '
            f'data-ok="{E(n.a("ok", "¡Enviado! Gracias."))}">{body(n)}<div class="form-grid">{"".join(ctrls)}</div>'
            f'<button class="btn btn-primary" type="submit">{E(n.a("button", "Enviar"))}</button></form>')
    return section(n, ctx, form, "form-sec", sid)


@comp("login", "Acceso de usuarios (iniciar sesión / crear cuenta) SIMULADO en el navegador: para prototipos y demos.",
      'id="acceso" title="Bienvenido" subtitle="…" register="yes" redirect="#panel"', "", css="""
.auth { max-width: 430px; margin-inline: auto; }
.auth h2 { text-align: center; font-size: 1.6rem; }
.auth .lead { text-align: center; color: var(--muted); }
.auth .tab-list button { flex: 1; }
.auth form .btn { width: 100%; justify-content: center; }
.auth-note { font-size: .8rem; color: var(--muted); text-align: center; margin: 1rem 0 0; }
.auth-welcome { text-align: center; }""")
def r_login(n, ctx):
    reg = n.yes("register", True)

    def fld(fid, name, label, typ, extra=""):
        return (f'<div class="field"><label for="{fid}">{label}</label><input id="{fid}" name="{name}" type="{typ}" required'
                f'{extra}><span class="error"></span></div>')

    login = ('<form data-auth="login" novalidate>' + fld("au-e", "email", "Correo", "email", ' autocomplete="username"')
             + fld("au-p", "password", "Contraseña", "password", ' minlength="6" autocomplete="current-password"')
             + '<button class="btn btn-primary">Entrar</button></form>')
    register = ('<form data-auth="register" novalidate hidden>' + fld("ar-n", "name", "Nombre", "text", ' autocomplete="name"')
                + fld("ar-e", "email", "Correo", "email", ' autocomplete="username"')
                + fld("ar-p", "password", "Contraseña (mín. 6)", "password", ' minlength="6" autocomplete="new-password"')
                + fld("ar-p2", "password2", "Repite la contraseña", "password", ' minlength="6" autocomplete="new-password"')
                + '<button class="btn btn-primary">Crear cuenta</button></form>') if reg else ""
    tabs = ('<div class="tab-list" role="tablist"><button type="button" role="tab" data-auth-tab="login" aria-selected="true">'
            'Iniciar sesión</button><button type="button" role="tab" data-auth-tab="register" aria-selected="false">'
            'Crear cuenta</button></div>') if reg else ""
    inner = (f'<div class="card auth reveal" data-auth-box data-redirect="{E(n.a("redirect"))}"><div class="auth-forms">'
             f'<h2>{inline(n.a("title", "Bienvenido"))}</h2>' + (f'<p class="lead">{inline(n.a("subtitle"))}</p>' if n.a("subtitle") else "")
             + tabs + login + register + '<p class="auth-note">Demostración: las cuentas se guardan solo en este navegador.</p>'
             '</div><div class="auth-welcome" hidden><h2>Hola, <span data-auth-name></span> 👋</h2>'
             '<p class="muted">Has iniciado sesión.</p><button type="button" class="btn btn-ghost" data-auth-logout>Cerrar sesión'
             '</button></div></div>')
    n2 = Node(n.tag, {k: v for k, v in n.attrs.items() if k not in ("title", "subtitle", "eyebrow")}, n.children, "")
    return section(n2, ctx, inner, "login-sec", "acceso")


@comp("kanban", "Tablero Kanban: arrastrar y soltar, añadir, mover y borrar tarjetas (se guarda en el navegador).",
      'id="tablero" nav="Tablero" title="…" columns="Por hacer, En curso, Hecho" key="tablero"',
      '<item col="Por hacer" tag="Diseño">Texto de la tarjeta</item> (tarjetas iniciales)', css="""
.kanban { display: grid; grid-template-columns: repeat(var(--cols, 3), minmax(230px, 1fr)); gap: 1rem; overflow-x: auto; align-items: start; padding-bottom: .5rem; }
.kb-col { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: .9rem; min-height: 260px; transition: .15s; }
.kb-col.over { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, var(--surface)); }
.kb-head { display: flex; justify-content: space-between; align-items: center; font-weight: 700; margin-bottom: .8rem; }
.kb-count { font-size: .78rem; color: var(--muted); background: var(--card); border: 1px solid var(--border); border-radius: 999px; padding: 0 .55rem; }
.kb-list { display: grid; gap: .6rem; min-height: 40px; }
.kb-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: .7rem .8rem; cursor: grab;
  box-shadow: var(--shadow); display: flex; gap: .5rem; justify-content: space-between; align-items: flex-start; }
.kb-card.dragging { opacity: .45; }
.kb-card .badge { margin-bottom: .35rem; }
.kb-tools { display: flex; gap: .15rem; }
.kb-tools button { background: none; border: 0; color: var(--muted); cursor: pointer; font-size: 1rem; line-height: 1; padding: .1rem .25rem; border-radius: 6px; }
.kb-tools button:hover { color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); }
.kb-add { width: 100%; margin-top: .7rem; background: none; border: 1px dashed var(--border); border-radius: 12px; padding: .55rem; color: var(--muted); cursor: pointer; font: inherit; }
.kb-add:hover { border-color: var(--accent); color: var(--accent); }""")
def r_kanban(n, ctx):
    cols = [c.strip() for c in n.a("columns", "Por hacer, En curso, Hecho").split(",") if c.strip()] or ["Por hacer"]
    cards = [{"col": it.a("col") if it.a("col") in cols else cols[0], "text": it.text, "tag": it.a("tag")} for it in n.items]
    cfg = {"key": re.sub(r"\W", "", n.a("key", n.a("id", "tablero")).lower()) or "tablero", "cols": cols, "seed": cards}
    inner = (f'<div class="kanban reveal" style="--cols:{len(cols)}" data-kanban="{E(json.dumps(cfg, ensure_ascii=False))}">'
             f'<p class="muted">Cargando tablero…</p></div>')
    return section(n, ctx, inner, "kanban-sec", "tablero")


@comp("calendar", "Calendario mensual con eventos (clic en un día para verlos; flechas para cambiar de mes).",
      'id="agenda" nav="Agenda" title="…" month="2026-10"', '<item date="2026-10-15" time="18:00" color="#e11d48">Evento</item>',
      css="""
.cal { padding: 1.2rem; max-width: 920px; margin-inline: auto; }
.cal-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; font-size: 1.15rem; }
.cal-nav { width: 38px; height: 38px; border-radius: 50%; border: 1px solid var(--border); background: none; color: var(--text); cursor: pointer; font-size: 1.2rem; }
.cal-nav:hover { border-color: var(--accent); color: var(--accent); }
.cal-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 4px; }
.cal-dow { text-align: center; font-size: .78rem; font-weight: 700; color: var(--muted); padding: .3rem 0; }
.cal-cell { min-height: 88px; border: 1px solid var(--border); border-radius: 10px; background: var(--card); padding: .35rem; text-align: left;
  display: flex; flex-direction: column; gap: 3px; cursor: pointer; font: inherit; color: var(--text); overflow: hidden; }
.cal-cell.empty { background: transparent; border-color: transparent; cursor: default; }
.cal-cell:not(.empty):hover { border-color: var(--accent); }
.cal-cell.today .cal-n { background: var(--accent); color: var(--accent-contrast); border-radius: 999px; padding: 0 .45rem; }
.cal-cell.sel { box-shadow: 0 0 0 2px var(--accent) inset; }
.cal-n { font-weight: 700; font-size: .85rem; align-self: flex-start; }
.cal-ev { font-size: .72rem; background: color-mix(in srgb, var(--ev, var(--accent)) 18%, transparent); border-left: 3px solid var(--ev, var(--accent));
  border-radius: 4px; padding: 0 .3rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cal-more { font-size: .7rem; color: var(--muted); }
.cal-detail { margin-top: 1rem; } .cal-detail ul { padding-left: 1.1rem; margin: 0; }
@media (max-width: 600px) { .cal-cell { min-height: 54px; } .cal-ev, .cal-more { display: none; }
  .cal-cell.has::after { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--accent); margin-top: auto; } }""")
def r_calendar(n, ctx):
    evs = []
    for it in n.items:
        d = it.a("date")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            ctx.warnings.append(f"calendar: fecha '{d}' no válida (usa AAAA-MM-DD)")
            continue
        color = it.a("color")
        evs.append({"date": d, "time": it.a("time"), "text": it.text or it.a("title"),
                    "color": color if re.fullmatch(r"#[0-9a-fA-F]{3,8}", color) else ""})
    month = n.a("month")
    month = month if re.fullmatch(r"\d{4}-\d{2}", month) else (evs[0]["date"][:7] if evs else "")
    cfg = {"month": month, "events": evs}
    inner = (f'<div class="card cal reveal" data-calendar="{E(json.dumps(cfg, ensure_ascii=False))}">'
             f'<p class="muted">Cargando calendario…</p></div>')
    return section(n, ctx, inner, "calendar-sec", "agenda")


@comp("countdown", "Cuenta regresiva hasta una fecha (lanzamientos, eventos, ofertas).",
      'title="…" to="2026-12-31T20:00" text="…" done="¡Ya comenzó!" button="Texto|#id"', "", css="""
.countdown { text-align: center; }
.cd-grid { display: flex; justify-content: center; gap: clamp(.6rem, 2vw, 1.2rem); margin: 1.5rem 0; flex-wrap: wrap; }
.cd-box { min-width: 92px; padding: 1rem .8rem; border-radius: var(--radius); background: var(--card); border: 1px solid var(--border); box-shadow: var(--shadow); }
.cd-num { font-size: clamp(2rem, 6vw, 3rem); font-weight: 800; line-height: 1; color: var(--accent); font-variant-numeric: tabular-nums; }
.cd-lab { font-size: .78rem; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; margin-top: .35rem; }
.cd-done { font-size: 1.3rem; font-weight: 700; color: var(--accent); }
@media (max-width: 520px) { .cd-grid { flex-wrap: nowrap; gap: .45rem; } .cd-box { min-width: 0; flex: 1; padding: .8rem .3rem; }
  .cd-lab { font-size: .62rem; letter-spacing: .02em; } }""")
def r_countdown(n, ctx):
    to = n.a("to")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?", to):
        ctx.warnings.append(f"countdown: to='{to}' no es una fecha AAAA-MM-DDTHH:MM")
    boxes = "".join(f'<div class="cd-box"><div class="cd-num" data-cd="{k}">00</div><div class="cd-lab">{lab}</div></div>'
                    for k, lab in (("d", "días"), ("h", "horas"), ("m", "minutos"), ("s", "segundos")))
    inner = (f'<div class="countdown reveal" data-countdown="{E(to)}" data-done="{E(n.a("done", "¡Ya comenzó!"))}">'
             + (f'<h2>{inline(n.a("title"))}</h2>' if n.a("title") else "")
             + (f'<p class="muted">{inline(n.a("text"))}</p>' if n.a("text") else "")
             + f'<div class="cd-grid">{boxes}</div><p class="cd-done" hidden></p>' + button(n.a("button")) + "</div>")
    n2 = Node(n.tag, {k: v for k, v in n.attrs.items() if k != "title"}, n.children, "")
    return section(n2, ctx, inner, "countdown-sec", "cuenta")


@comp("newsletter", "Suscripción al boletín (correo con validación).",
      'title="…" text="…" button="Suscribirme" placeholder="tu@correo.com" ok="¡Gracias por suscribirte!"', "", css="""
.newsletter-box { max-width: 680px; margin-inline: auto; text-align: center; }
.nl-form { display: flex; gap: .6rem; max-width: 500px; margin: 1.2rem auto 0; align-items: flex-start; }
.nl-form .field { flex: 1; margin: 0; text-align: left; }
@media (max-width: 520px) { .nl-form { flex-direction: column; align-items: stretch; } }""")
def r_newsletter(n, ctx):
    inner = (f'<div class="newsletter-box reveal">' + (f'<h2>{inline(n.a("title"))}</h2>' if n.a("title") else "")
             + (f'<p class="muted">{inline(n.a("text"))}</p>' if n.a("text") else body(n))
             + f'<form class="nl-form" data-validate novalidate data-store="lai-suscriptores" '
               f'data-ok="{E(n.a("ok", "¡Gracias por suscribirte!"))}"><div class="field"><input type="email" name="email" '
               f'required placeholder="{E(n.a("placeholder", "tu@correo.com"))}" aria-label="Correo"><span class="error">'
               f'</span></div><button class="btn btn-primary" type="submit">{E(n.a("button", "Suscribirme"))}</button></form></div>')
    n2 = Node(n.tag, {k: v for k, v in n.attrs.items() if k != "title"}, n.children, "")
    return section(n2, ctx, inner, "newsletter", "boletin")


# ---------------------------------------------------------------------------
# Negocios
# ---------------------------------------------------------------------------

@comp("menu", "Carta de restaurante o cafetería por categorías, con precios, etiquetas y filtro.",
      'id="carta" nav="Carta" title="…" subtitle="…" currency="€" filter="yes" columns="1|2"',
      '<item name="Flat white" price="3.20" category="Cafés" tags="Vegano, Nuevo" image="☕">descripción</item>', css="""
.menu-cat { margin-bottom: 2.4rem; }
.menu-cat > h3 { font-size: 1.35rem; border-bottom: 2px solid var(--accent); display: inline-block; padding-bottom: .2rem; margin-bottom: 1.1rem; }
.menu-list { display: grid; grid-template-columns: repeat(var(--cols, 2), minmax(0, 1fr)); gap: 1.2rem 2.5rem; }
.dish { display: grid; grid-template-columns: auto 1fr; gap: .9rem; align-items: start; }
.dish-icon { font-size: 1.6rem; width: 2.8rem; height: 2.8rem; display: grid; place-items: center; border-radius: 12px;
  background: color-mix(in srgb, var(--accent) 12%, transparent); overflow: hidden; }
.dish-icon img { width: 100%; height: 100%; object-fit: cover; }
.dish-top { display: flex; align-items: baseline; gap: .5rem; font-weight: 700; }
.dish-top .dots { flex: 1; border-bottom: 2px dotted var(--border); transform: translateY(-4px); min-width: 1rem; }
.dish-price { color: var(--accent); white-space: nowrap; }
.dish p { margin: .2rem 0 0; color: var(--muted); font-size: .95rem; }
.dish .tags { display: flex; gap: .3rem; flex-wrap: wrap; margin-top: .4rem; }
@media (max-width: 700px) { .menu-list { grid-template-columns: 1fr; } }""")
def r_menu(n, ctx):
    cur = n.a("currency", ctx.currency)
    cats = list(dict.fromkeys(it.a("category", "Carta") for it in n.items))
    filt = ('<div class="filters" data-filter-group><button class="active" data-filter="*">Todo</button>'
            + "".join(f'<button data-filter="{E(c)}">{E(c)}</button>' for c in cats) + "</div>") \
        if n.yes("filter", True) and len(cats) > 1 else ""
    blocks = []
    for c in cats:
        dishes = []
        for it in (x for x in n.items if x.a("category", "Carta") == c):
            price = it.a("price")
            if re.fullmatch(r"\d+([.,]\d+)?", price):
                price = f"{cur}{float(price.replace(',', '.')):.2f}"
            img = it.a("image")
            pic = f'<img src="{E(img)}" alt="" loading="lazy">' if is_url(img) else E(img)
            icon = f'<div class="dish-icon">{pic}</div>' if img else ""
            tags = "".join(f'<span class="badge">{E(t)}</span>' for t in split_list(it.a("tags").replace(",", ";")))
            style = "" if icon else ' style="grid-template-columns:1fr"'
            dishes.append(f'<div class="dish"{style}>{icon}<div>'
                          f'<div class="dish-top"><span>{E(it.a("name"))}</span><span class="dots"></span>'
                          f'<span class="dish-price">{E(price)}</span></div>' + (f"<p>{inline(it.text)}</p>" if it.text else "")
                          + (f'<div class="tags">{tags}</div>' if tags else "") + "</div></div>")
        blocks.append(f'<div class="menu-cat reveal" data-category="{E(c)}">' + (f"<h3>{E(c)}</h3>" if len(cats) > 1 or c != "Carta" else "")
                      + f'<div class="menu-list" style="--cols:{E(n.a("columns", "2"))}">{"".join(dishes)}</div></div>')
    return section(n, ctx, filt + "".join(blocks), "menu", "carta")


@comp("video", "Vídeo de YouTube, Vimeo o archivo .mp4, adaptable.",
      'id="…" title="…" subtitle="…" src="https://youtu.be/…|video.mp4" caption="…"', "", css="""
.video-box { max-width: 920px; margin: 0 auto; }
.video-frame { position: relative; aspect-ratio: 16/9; border-radius: var(--radius); overflow: hidden; box-shadow: var(--shadow); background: #000; }
.video-frame iframe, .video-frame video { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; }
.video-box figcaption { text-align: center; color: var(--muted); margin-top: .8rem; font-size: .92rem; }
.yt-poster { position: absolute; inset: 0; display: grid; place-items: center; }
.yt-poster img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.yt-poster span { position: relative; width: 72px; height: 50px; border-radius: 14px; background: #e00; color: #fff; display: grid; place-items: center; font-size: 1.4rem; }""")
def r_video(n, ctx):
    src = n.a("src")
    yt = re.search(r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/))([\w-]{6,})", src)
    vm = re.search(r"vimeo\.com/(?:video/)?(\d+)", src)
    title = E(n.a("title", "Vídeo"))
    if yt:
        media = (f'<iframe src="https://www.youtube-nocookie.com/embed/{E(yt.group(1))}" title="{title}" loading="lazy" '
                 f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>')
    elif vm:
        media = (f'<iframe src="https://player.vimeo.com/video/{E(vm.group(1))}" title="{title}" loading="lazy" '
                 f'allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe>')
    elif re.search(r"\.(mp4|webm|ogv|ogg)(\?.*)?$", src, re.I):
        media = f'<video src="{E(src)}" controls preload="metadata"></video>'
    else:
        ctx.warnings.append(f"video: src '{src}' no es YouTube, Vimeo ni .mp4/.webm")
        media = f'<div class="visual ph" style="--h:{hue(src or "v")};position:absolute;inset:0"><span>▶</span></div>'
    cap = f"<figcaption>{inline(n.a('caption'))}</figcaption>" if n.a("caption") else ""
    return section(n, ctx, f'<figure class="video-box reveal"><div class="video-frame">{media}</div>{cap}</figure>',
                   "video-sec", "video")


@comp("map", "Mapa: OpenStreetMap con lat/lon, o Google Maps buscando la dirección. Texto opcional al lado.",
      'id="ubicacion" nav="Ubicación" title="…" address="Calle Mayor 1, Madrid" lat="40.4168" lon="-3.7038" zoom="15"',
      "texto Markdown opcional (cómo llegar, horario…)", css="""
.map-inner { display: grid; grid-template-columns: .8fr 1.2fr; gap: 2rem; align-items: stretch; }
.map-inner.solo { grid-template-columns: 1fr; }
.map-frame { min-height: 360px; border-radius: var(--radius); overflow: hidden; border: 1px solid var(--border); box-shadow: var(--shadow); }
.map-frame iframe { width: 100%; height: 100%; min-height: 360px; border: 0; display: block; }
@media (max-width: 820px) { .map-inner { grid-template-columns: 1fr; } }""")
def r_map(n, ctx):
    from urllib.parse import quote
    try:
        zoom = max(3, min(19, int(n.a("zoom", "15"))))
    except ValueError:
        zoom = 15
    addr = n.a("address")
    try:
        lat, lon = float(n.a("lat")), float(n.a("lon"))
        d = 0.012 * 2 ** (15 - zoom)
        src = (f"https://www.openstreetmap.org/export/embed.html?bbox={lon - d:.5f}%2C{lat - d / 2:.5f}%2C{lon + d:.5f}%2C"
               f"{lat + d / 2:.5f}&layer=mapnik&marker={lat:.5f}%2C{lon:.5f}")
        open_url = f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map={zoom}/{lat:.5f}/{lon:.5f}"
    except ValueError:
        if not addr:
            ctx.warnings.append("map: pon address=\"…\" o lat y lon")
        src = f"https://maps.google.com/maps?q={quote(addr)}&z={zoom}&output=embed"
        open_url = f"https://www.google.com/maps/search/?api=1&query={quote(addr)}"
    frame = (f'<div class="map-frame"><iframe src="{E(src)}" title="Mapa: {E(addr or n.a("title", "ubicación"))}" '
             f'loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe></div>')
    side = body(n) + (f"<p>📍 {E(addr)}</p>" if addr else "") + f'<a class="card-link" href="{E(open_url)}" target="_blank" rel="noopener">Abrir en el mapa →</a>'
    solo = not n.text.strip()
    inner = (f'<div class="map-inner reveal{" solo" if solo else ""}">' + ("" if solo else f"<div>{side}</div>") + frame + "</div>"
             + (f'<p style="text-align:center;margin-top:.8rem">{side}</p>' if solo else ""))
    return section(n, ctx, inner, "map-sec", "ubicacion")


@comp("banner", "Barra de aviso encima de todo (ofertas, avisos) que el visitante puede cerrar.",
      'text="Envío gratis desde 30 €" link="Ver ofertas|#tienda" variant="accent|dark" dismiss="yes"', "", section=False,
      css="""
.banner { background: var(--accent); color: var(--accent-contrast); font-size: .92rem; font-weight: 600; }
.banner.dark { background: var(--text); color: var(--bg); }
.banner-inner { display: flex; align-items: center; justify-content: center; gap: .8rem; padding: .55rem 2.6rem; position: relative; text-align: center; flex-wrap: wrap; }
.banner a { color: inherit; text-decoration: underline; }
.banner-close { position: absolute; right: .6rem; top: 50%; transform: translateY(-50%); background: none; border: 0; color: inherit; font-size: 1.3rem; cursor: pointer; line-height: 1; }""")
def r_banner(n, ctx):
    text = n.a("text") or n.text
    t, h = link(n.a("link"))
    key = hashlib.md5(text.encode()).hexdigest()[:8]
    dismiss = n.yes("dismiss", True)
    return (f'<div class="banner {E(n.a("variant", "accent"))}"' + (f' data-dismiss="{key}"' if dismiss else "") + ' role="region" '
            f'aria-label="Aviso"><div class="banner-inner"><span>{inline(text)}</span>'
            + (f'<a href="{E(h)}">{E(t)}</a>' if t else "")
            + ('<button type="button" class="banner-close" aria-label="Cerrar aviso">×</button>' if dismiss else "") + "</div></div>")


@comp("compare", "Tabla comparativa de planes o productos con ✓ / ✗ y una columna destacada.",
      'id="comparar" nav="Comparar" title="…" columns="Básico, Pro, Empresa" highlight="Pro"',
      "<row>Soporte 24/7 | no | sí | sí</row>  (primera celda = característica; sí/no o texto)", css="""
.compare table td:not(:first-child), .compare table th:not(:first-child) { text-align: center; }
.compare td:first-child { font-weight: 600; }
.compare .hl { background: color-mix(in srgb, var(--accent) 9%, transparent); }
.compare thead th.hl { background: var(--accent); color: var(--accent-contrast); }
.yes { color: #12b76a; font-weight: 800; font-size: 1.1rem; } .no { color: var(--muted); }""")
def r_compare(n, ctx):
    cols = [c.strip() for c in n.a("columns").split(",") if c.strip()]
    hl = n.a("highlight").strip().lower()
    hli = {i for i, c in enumerate(cols) if c.lower() == hl}
    yes = {"sí", "si", "yes", "✓", "✔", "true", "incluido", "ok"}
    no = {"no", "✗", "✕", "×", "-", "—", "false", ""}

    def cell(v: str) -> str:
        low = v.strip().lower()
        if low in yes:
            return '<span class="yes" aria-label="Sí">✓</span>'
        if low in no:
            return '<span class="no" aria-label="No">✗</span>'
        return inline(v.strip())

    hl_attr = ' class="hl"'
    head = f'<th scope="col">{E(n.a("label", ""))}</th>' + "".join(
        f'<th scope="col"{hl_attr if i in hli else ""}>{E(c)}</th>' for i, c in enumerate(cols))
    rows = []
    for it in n.items:
        cells = it.text.split("|")
        feat, vals = (cells[0].strip(), cells[1:]) if cells else ("", [])
        vals += [""] * (len(cols) - len(vals))
        rows.append(f"<tr><td>{inline(feat)}</td>" + "".join(
            f'<td{hl_attr if i in hli else ""}>{cell(v)}</td>' for i, v in enumerate(vals[:len(cols)])) + "</tr>")
    return section(n, ctx, f'<div class="data-table compare reveal"><table><thead><tr>{head}</tr></thead>'
                           f'<tbody>{"".join(rows)}</tbody></table></div>', "compare-sec", "comparar")


@comp("todo", "Lista de tareas: añadir, marcar, borrar y filtrar (se guarda en el navegador).",
      'id="tareas" nav="Tareas" title="…" key="tareas"', '<item done="yes">Tarea inicial</item>', css="""
.todo { max-width: 640px; margin-inline: auto; }
.todo-form { display: flex; gap: .6rem; margin-bottom: 1rem; }
.todo-form .field { flex: 1; margin: 0; }
.todo-list { list-style: none; padding: 0; margin: 0; display: grid; gap: .5rem; }
.todo-list li { display: flex; align-items: center; gap: .7rem; padding: .7rem .9rem; background: var(--card); border: 1px solid var(--border); border-radius: 12px; }
.todo-list li input { width: 1.15rem; height: 1.15rem; accent-color: var(--accent); }
.todo-list li span { flex: 1; }
.todo-list li.done span { text-decoration: line-through; color: var(--muted); }
.todo-del { background: none; border: 0; color: var(--muted); cursor: pointer; font-size: 1.2rem; line-height: 1; }
.todo-del:hover { color: #e5484d; }
.todo-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 1rem; color: var(--muted); font-size: .9rem; flex-wrap: wrap; gap: .6rem; }
.todo-foot .filters { margin: 0; }""")
def r_todo(n, ctx):
    seed = [{"text": it.text, "done": it.yes("done")} for it in n.items if it.text]
    cfg = {"key": re.sub(r"\W", "", n.a("key", n.a("id", "tareas")).lower()) or "tareas", "seed": seed}
    inner = (f'<div class="todo reveal" data-todo="{E(json.dumps(cfg, ensure_ascii=False))}">'
             '<form class="todo-form"><div class="field"><input placeholder="Nueva tarea…" aria-label="Nueva tarea"></div>'
             '<button class="btn btn-primary" type="submit">Añadir</button></form><ul class="todo-list"></ul>'
             '<div class="todo-foot"><span class="todo-left"></span><div class="filters"><button type="button" class="active" '
             'data-tf="all">Todas</button><button type="button" data-tf="todo">Pendientes</button><button type="button" '
             'data-tf="done">Hechas</button></div><button type="button" class="btn btn-ghost btn-sm todo-clear">Borrar hechas'
             '</button></div></div>')
    return section(n, ctx, inner, "todo-sec", "tareas")


# ---------------------------------------------------------------------------
# Negocios, herramientas y utilidades (tercera tanda)
# ---------------------------------------------------------------------------

_DAYS = {"lunes": 1, "lun": 1, "monday": 1, "mon": 1, "martes": 2, "mar": 2, "tuesday": 2, "tue": 2,
         "miércoles": 3, "miercoles": 3, "mié": 3, "mie": 3, "wednesday": 3, "wed": 3, "jueves": 4, "jue": 4,
         "thursday": 4, "thu": 4, "viernes": 5, "vie": 5, "friday": 5, "fri": 5, "sábado": 6, "sabado": 6, "sáb": 6,
         "sab": 6, "saturday": 6, "sat": 6, "domingo": 7, "dom": 7, "sunday": 7, "sun": 7}


def _days(spec: str) -> list[int]:
    """«Lunes a viernes», «Lun-Vie», «Sábado y domingo», «Martes» → [1..7]."""
    low = spec.lower()
    found = [(m.start(), _DAYS[m.group(0)]) for m in re.finditer("|".join(sorted(_DAYS, key=len, reverse=True)), low)]
    found = sorted({p: d for p, d in found}.items())
    days = [d for _, d in found]
    if len(days) == 2 and re.search(r"\s(a|al|hasta|to)\s|[-–]", low):
        a, b = days
        return list(range(a, b + 1)) if a <= b else list(range(a, 8)) + list(range(1, b + 1))
    return sorted(set(days))


def _slots(text: str) -> list[str]:
    return [f"{int(a):02d}:{b or '00'}-{int(c):02d}:{d or '00'}"
            for a, b, c, d in re.findall(r"(\d{1,2})(?:[:.h](\d{2}))?\s*(?:-|–|a|hasta)\s*(\d{1,2})(?:[:.h](\d{2}))?", text)]


@comp("hours", "Horario semanal: resalta el día de hoy y dice si está «Abierto ahora».",
      'id="horario" nav="Horario" title="…" note="Festivos: cerrado"',
      '<item day="Lunes a viernes" hours="9:00-14:00, 17:00-20:00"/>  <item day="Domingo" closed="yes"/>', css="""
.hours-box { max-width: 560px; margin-inline: auto; padding: 1.2rem 1.4rem; }
.hours-status { display: inline-flex; align-items: center; gap: .5rem; font-weight: 700; margin-bottom: .8rem; }
.hours-status::before { content: ""; width: .65rem; height: .65rem; border-radius: 50%; background: var(--muted); }
.hours-status.open { color: #12b76a; } .hours-status.open::before { background: #12b76a; box-shadow: 0 0 0 4px rgba(18,183,106,.2); }
.hours-status.closed { color: #e5484d; } .hours-status.closed::before { background: #e5484d; }
.hours-row { display: flex; justify-content: space-between; gap: 1rem; padding: .6rem .4rem; border-bottom: 1px dashed var(--border); }
.hours-row:last-child { border-bottom: 0; }
.hours-row.today { font-weight: 800; color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); border-radius: 8px; }
.hours-note { margin: .8rem 0 0; font-size: .88rem; color: var(--muted); }""")
def r_hours(n, ctx):
    rows = []
    for it in n.items:
        day = it.a("day") or it.a("days") or it.text
        src = it.a("hours") or (f'{it.a("open")}-{it.a("close")}' if it.a("open") else it.text)
        closed = it.yes("closed") or src.strip().lower() in ("cerrado", "closed")
        slots = [] if closed else _slots(src)
        days = _days(day)
        if not days:
            ctx.warnings.append(f"hours: no entiendo el día '{day}'")
        label = "Cerrado" if closed or not slots else " · ".join(s.replace("-", "–") for s in slots)
        rows.append(f'<div class="hours-row" data-days="{",".join(map(str, days))}" data-slots="{E(",".join(slots))}">'
                    f"<span>{E(day)}</span><span>{E(label)}</span></div>")
    inner = (f'<div class="card hours-box reveal" data-hours><div class="hours-status" aria-live="polite">Horario</div>'
             f'{"".join(rows)}' + (f'<p class="hours-note">{inline(n.a("note"))}</p>' if n.a("note") else "") + "</div>")
    return section(n, ctx, inner, "hours-sec", "horario")


@comp("beforeafter", "Comparador «antes / después» con un deslizador sobre dos imágenes.",
      'id="…" title="…" before="antes.jpg|🏚️" after="despues.jpg|🏡" label-before="Antes" label-after="Después"',
      "texto opcional debajo", css="""
.ba { position: relative; max-width: 900px; margin-inline: auto; aspect-ratio: 16/9; border-radius: var(--radius); overflow: hidden;
  box-shadow: var(--shadow); --pos: 50%; user-select: none; }
.ba > .visual, .ba .ba-after > .visual { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.ba .ph span { font-size: clamp(4rem, 12vw, 8rem); }
.ba-after { position: absolute; inset: 0; clip-path: inset(0 0 0 var(--pos)); }
.ba-line { position: absolute; top: 0; bottom: 0; left: var(--pos); width: 3px; background: #fff; transform: translateX(-50%); box-shadow: 0 0 12px rgba(0,0,0,.4); pointer-events: none; }
.ba-line::after { content: "⇆"; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 44px; height: 44px;
  border-radius: 50%; background: #fff; color: #111; display: grid; place-items: center; font-weight: 800; box-shadow: var(--shadow); }
.ba input { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; cursor: ew-resize; margin: 0; }
.ba-tag { position: absolute; top: .8rem; padding: .25rem .7rem; border-radius: 999px; background: rgba(0,0,0,.6); color: #fff; font-size: .8rem; font-weight: 700; pointer-events: none; }
.ba-tag.l { left: .8rem; } .ba-tag.r { right: .8rem; }""")
def r_beforeafter(n, ctx):
    inner = (f'<div class="ba reveal" data-ba>{visual(n.a("before", "🌑"), n.a("label-before", "Antes"))}'
             f'<div class="ba-after">{visual(n.a("after", "🌕"), n.a("label-after", "Después") + " ")}</div>'
             f'<div class="ba-line"></div><span class="ba-tag l">{E(n.a("label-before", "Antes"))}</span>'
             f'<span class="ba-tag r">{E(n.a("label-after", "Después"))}</span>'
             f'<input type="range" min="0" max="100" value="50" aria-label="Comparar antes y después"></div>'
             + (f'<div class="prose" style="margin-top:1rem;text-align:center">{body(n)}</div>' if n.text.strip() else ""))
    return section(n, ctx, inner, "ba-sec", "antes-despues")


@comp("modal", "Botón que abre una ventana emergente con texto (condiciones, detalles, aviso legal…).",
      'button="Ver condiciones" title="Título de la ventana" align="center|left"', "texto Markdown de la ventana", css="""
.modal-sec { padding: 1.5rem 0; }
.lai-dialog { border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg); color: var(--text);
  width: min(620px, 92vw); max-height: 85vh; padding: 1.6rem; box-shadow: 0 30px 80px rgba(0,0,0,.35); }
.lai-dialog::backdrop { background: rgba(10,12,20,.55); backdrop-filter: blur(3px); }
.lai-dialog h3 { margin-right: 2rem; }
.dlg-x { position: absolute; top: .8rem; right: .9rem; background: none; border: 0; font-size: 1.6rem; cursor: pointer; color: var(--muted); }""")
def r_modal(n, ctx):
    sec_id = "m-" + hashlib.md5((n.a("title") + n.text).encode()).hexdigest()[:6]
    mid = sec_id + "-dlg"
    align = "left" if n.a("align") == "left" else "center"
    inner = (f'<div style="text-align:{align}"><button type="button" class="btn btn-ghost" data-modal-open="{mid}">'
             f'{E(n.a("button", "Más información"))}</button></div>'
             f'<dialog class="lai-dialog" id="{mid}" aria-labelledby="{mid}-t"><button type="button" class="dlg-x" '
             f'data-modal-close aria-label="Cerrar">×</button><h3 id="{mid}-t">{inline(n.a("title", n.a("button", "Información")))}</h3>'
             f'<div class="prose" style="max-width:none">{body(n)}</div></dialog>')
    n2 = Node(n.tag, {k: v for k, v in n.attrs.items() if k not in ("title",)}, n.children, "")
    n2.attrs.setdefault("alt", "no")
    return section(n2, ctx, inner, "modal-sec", sec_id)


@comp("wizard", "Formulario por pasos con barra de progreso (reservas, altas, presupuestos, encuestas largas).",
      'id="…" nav="…" title="…" subtitle="…" button="Enviar" ok="¡Recibido!" email="destino@…"',
      '<item title="Tus datos" fields="nombre:texto*, email:email*"/>  <item title="Detalles" fields="fecha:fecha*, plan:Básico|Pro"/>',
      css="""
.wizard { max-width: 660px; margin-inline: auto; }
.wz-progress { display: flex; gap: .4rem; margin-bottom: 1.4rem; counter-reset: wz; }
.wz-progress span { flex: 1; font-size: .78rem; color: var(--muted); padding-top: .55rem; border-top: 4px solid var(--border); transition: .25s; }
.wz-progress span::before { counter-increment: wz; content: counter(wz) ". "; font-weight: 800; }
.wz-progress span.done, .wz-progress span.cur { border-top-color: var(--accent); color: var(--text); }
.wz-step[hidden] { display: none; }
.wz-nav { display: flex; justify-content: space-between; gap: .6rem; margin-top: .6rem; }""")
def r_wizard(n, ctx):
    sid = re.sub(r"[^\w-]", "", n.a("id", "pasos")) or "pasos"
    steps, labels = [], []
    for i, it in enumerate(n.items):
        ctrls = []
        for spec in (it.a("fields") or "").split(","):
            if spec.strip():
                name, _, typ = spec.strip().partition(":")
                req = name.strip().endswith("*") or typ.strip().endswith("*")
                ctrls.append(_field(name.strip(" *"), (typ or "texto").strip(" *"), req, f"{sid}{i}"))
        title = it.a("title", f"Paso {i + 1}")
        labels.append(f"<span>{E(title)}</span>")
        steps.append(f'<fieldset class="wz-step" style="border:0;padding:0;margin:0"{"" if i == 0 else " hidden"}>'
                     f'<legend class="sr-only">{E(title)}</legend>' + (md(it.text) if it.text else "")
                     + f'<div class="form-grid">{"".join(ctrls)}</div></fieldset>')
    mail = f' data-mailto="{E(n.a("email"))}" data-subject="{E(n.a("title", "Formulario"))}"' if n.a("email") else ""
    form = (f'<form class="card wizard reveal" data-wizard novalidate data-store="lai-{E(sid)}"{mail} '
            f'data-ok="{E(n.a("ok", "¡Enviado! Gracias."))}"><div class="wz-progress">{"".join(labels)}</div>{"".join(steps)}'
            f'<div class="wz-nav"><button type="button" class="btn btn-ghost" data-wz-back>← Atrás</button>'
            f'<button type="button" class="btn btn-primary" data-wz-next>Siguiente →</button>'
            f'<button type="submit" class="btn btn-primary" data-wz-send hidden>{E(n.a("button", "Enviar"))}</button></div></form>')
    return section(n, ctx, form, "form-sec wizard-sec", sid)


@comp("quiz", "Cuestionario con corrección y puntuación (test de conocimientos, «¿qué producto es para ti?»).",
      'id="…" nav="…" title="…" subtitle="…"',
      '<item q="¿Pregunta?" options="Opción A; Opción B; Opción C" answer="2" explain="Por qué es la correcta"/>', css="""
.quiz { max-width: 720px; margin-inline: auto; display: grid; gap: 1rem; }
.quiz-q { padding: 1.2rem 1.3rem; }
.quiz-q h3 { font-size: 1.05rem; }
.quiz-opt { display: flex; gap: .6rem; align-items: center; padding: .55rem .7rem; border: 1px solid var(--border); border-radius: 10px; margin-top: .45rem; cursor: pointer; }
.quiz-opt input { accent-color: var(--accent); }
.quiz-opt.right { border-color: #12b76a; background: rgba(18,183,106,.1); }
.quiz-opt.wrong { border-color: #e5484d; background: rgba(229,72,77,.1); }
.quiz-explain { margin: .6rem 0 0; font-size: .9rem; color: var(--muted); }
.quiz-result { text-align: center; font-size: 1.3rem; font-weight: 800; }""")
def r_quiz(n, ctx):
    sid = re.sub(r"[^\w-]", "", n.a("id", "quiz")) or "quiz"
    qs = []
    for i, it in enumerate(n.items):
        opts = [o.strip() for o in re.split(r"[;|]", it.a("options")) if o.strip()]
        ans = it.a("answer", "1").strip()
        idx = int(ans) - 1 if ans.isdigit() else next((k for k, o in enumerate(opts) if o.lower() == ans.lower()), 0)
        if not opts:
            ctx.warnings.append(f"quiz: la pregunta {i + 1} no tiene options")
        radios = "".join(f'<label class="quiz-opt"><input type="radio" name="{sid}-{i}" value="{k}"> {inline(o)}</label>'
                         for k, o in enumerate(opts))
        qs.append(f'<div class="card quiz-q" data-answer="{idx}"><h3>{i + 1}. {inline(it.a("q") or it.text)}</h3>{radios}'
                  f'<p class="quiz-explain" hidden>{inline(it.a("explain"))}</p></div>')
    inner = (f'<div class="quiz reveal" data-quiz>{"".join(qs)}<div style="text-align:center"><button type="button" '
             f'class="btn btn-primary" data-quiz-check>Comprobar respuestas</button></div>'
             f'<p class="quiz-result" aria-live="polite"></p></div>')
    return section(n, ctx, inner, "quiz-sec", sid)


@comp("poll", "Encuesta de una pregunta con resultados en barras (un voto por navegador).",
      'id="…" title="¿Pregunta?" key="encuesta1"', '<item votes="12">Opción</item>  (votes = votos iniciales, opcional)', css="""
.poll { max-width: 560px; margin-inline: auto; padding: 1.3rem; }
.poll-opt { position: relative; display: block; width: 100%; text-align: left; margin-top: .55rem; padding: .75rem 1rem; border: 1px solid var(--border);
  border-radius: 12px; background: var(--card); color: var(--text); font: inherit; cursor: pointer; overflow: hidden; }
.poll-opt:hover { border-color: var(--accent); }
.poll-opt .fill { position: absolute; inset: 0 auto 0 0; width: 0; background: color-mix(in srgb, var(--accent) 18%, transparent); transition: width .6s ease; }
.poll-opt span { position: relative; display: flex; justify-content: space-between; gap: 1rem; }
.poll-opt.mine { border-color: var(--accent); font-weight: 700; }
.poll.voted .poll-opt { cursor: default; }
.poll-total { margin: .8rem 0 0; font-size: .85rem; color: var(--muted); text-align: right; }""")
def r_poll(n, ctx):
    opts = [{"text": it.text or it.a("label"), "votes": int(it.a("votes", "0")) if it.a("votes", "0").isdigit() else 0}
            for it in n.items]
    cfg = {"key": re.sub(r"\W", "", n.a("key", n.a("id", "encuesta")).lower()) or "encuesta", "opts": opts}
    btns = "".join(f'<button type="button" class="poll-opt" data-i="{i}"><i class="fill"></i><span><b>{E(o["text"])}</b>'
                   f'<em class="pct"></em></span></button>' for i, o in enumerate(opts))
    inner = (f'<div class="card poll reveal" data-poll="{E(json.dumps(cfg, ensure_ascii=False))}">{btns}'
             f'<p class="poll-total"></p></div>')
    return section(n, ctx, inner, "poll-sec", "encuesta")


@comp("calculator", "Calculadora con fórmula propia (presupuesto, préstamo, IMC, ahorro…). Resultado en vivo.",
      'id="…" title="…" formula="precio * cantidad * (1 + iva / 100)" result="Total" unit="€" decimals="2"',
      '<item name="precio" label="Precio" value="100" min="0" step="0.01"/>  <item name="iva" label="IVA" options="21|10|4"/>  '
      "(fórmula: + - * / ^ ( ) y round, min, max, pow, sqrt, abs)", css="""
.calc { max-width: 620px; margin-inline: auto; padding: 1.4rem; }
.calc-out { margin-top: .8rem; padding: 1.1rem; border-radius: 14px; text-align: center;
  background: linear-gradient(135deg, color-mix(in srgb, var(--accent) 16%, transparent), color-mix(in srgb, var(--accent-2) 16%, transparent)); }
.calc-out small { display: block; color: var(--muted); font-weight: 600; }
.calc-out strong { font-size: clamp(1.8rem, 5vw, 2.6rem); }""")
def r_calculator(n, ctx):
    sid = re.sub(r"[^\w-]", "", n.a("id", "calculadora")) or "calculadora"
    names, ctrls = [], []
    for it in n.items:
        name = re.sub(r"\W", "_", it.a("name", "x")).strip("_") or "x"
        names.append(name)
        label = E(it.a("label", name.capitalize()))
        if it.a("options"):
            opts = "".join(f'<option value="{E(v.strip())}">{E(l.strip())}</option>' for l, _, v in
                           (o.partition(":") if ":" in o else (o, "", o) for o in it.a("options").split("|")))
            ctrl = f'<select id="{sid}-{name}" data-var="{name}">{opts}</select>'
        else:
            extra = "".join(f' {k}="{E(it.a(k))}"' for k in ("min", "max", "step") if it.a(k))
            ctrl = f'<input id="{sid}-{name}" type="number" data-var="{name}" value="{E(it.a("value", "0"))}"{extra}>'
        ctrls.append(f'<div class="field"><label for="{sid}-{name}">{label}</label>{ctrl}</div>')
    formula = n.a("formula")
    unknown = [w for w in re.findall(r"[A-Za-z_]\w*", formula) if w not in names and w not in
               ("round", "min", "max", "pow", "sqrt", "abs", "floor", "ceil")]
    if unknown:
        ctx.warnings.append(f"calculator: la fórmula usa variables sin <item>: {', '.join(sorted(set(unknown)))}")
    inner = (f'<div class="card calc reveal" data-calc data-formula="{E(formula)}" data-decimals="{E(n.a("decimals", "2"))}" '
             f'data-unit="{E(n.a("unit"))}"><div class="form-grid">{"".join(ctrls)}</div><div class="calc-out" aria-live="polite">'
             f'<small>{E(n.a("result", "Resultado"))}</small><strong>—</strong></div>'
             + (f'<div class="prose" style="margin-top:1rem;font-size:.92rem">{body(n)}</div>' if n.text.strip() else "") + "</div>")
    return section(n, ctx, inner, "calc-sec", sid)


@comp("expenses", "Control de gastos e ingresos: añadir movimientos, saldo y gasto por categoría (se guarda en el navegador).",
      'id="gastos" nav="Gastos" title="…" currency="€" categories="Comida, Casa, Transporte, Ocio" key="gastos"',
      '<item type="ingreso|gasto" category="Casa" amount="650" date="2026-10-01">Alquiler</item> (movimientos iniciales)', css="""
.exp { display: grid; grid-template-columns: 1.1fr .9fr; gap: 1.2rem; align-items: start; }
.exp-kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: .7rem; margin-bottom: 1rem; }
.exp-kpis div { padding: .9rem; border-radius: 14px; background: var(--card); border: 1px solid var(--border); }
.exp-kpis small { display: block; color: var(--muted); font-size: .78rem; }
.exp-kpis b { font-size: 1.25rem; }
.exp-in { color: #12b76a; } .exp-out { color: #e5484d; }
.exp-form { display: grid; grid-template-columns: 2fr 1fr 1fr 1fr auto; gap: .5rem; margin-bottom: 1rem; }
.exp-form .field { margin: 0; }
.exp-list { list-style: none; padding: 0; margin: 0; display: grid; gap: .4rem; max-height: 420px; overflow: auto; }
.exp-list li { display: grid; grid-template-columns: 1fr auto auto; gap: .8rem; align-items: center; padding: .55rem .8rem; border-radius: 10px; background: var(--card); border: 1px solid var(--border); font-size: .92rem; }
.exp-list small { color: var(--muted); }
.exp-bars { padding: 1.2rem; }
.exp-bar { margin-bottom: .8rem; } .exp-bar div { display: flex; justify-content: space-between; font-size: .88rem; margin-bottom: .25rem; }
.exp-bar i { display: block; height: 10px; border-radius: 999px; background: linear-gradient(90deg, var(--accent), var(--accent-2)); }
@media (max-width: 860px) { .exp { grid-template-columns: 1fr; } .exp-form { grid-template-columns: 1fr 1fr; } }""")
def r_expenses(n, ctx):
    cats = [c.strip() for c in n.a("categories", "Comida, Casa, Transporte, Ocio, Otros").split(",") if c.strip()]
    seed = []
    for it in n.items:
        try:
            amt = float(re.sub(r"[^\d.,-]", "", it.a("amount", "0")).replace(",", ".") or 0)
        except ValueError:
            amt = 0.0
        seed.append({"text": it.text, "amount": abs(amt), "type": "ingreso" if it.a("type", "gasto").lower().startswith("ing") else "gasto",
                     "category": it.a("category", cats[0] if cats else "Otros"), "date": it.a("date")})
    cfg = {"key": re.sub(r"\W", "", n.a("key", n.a("id", "gastos")).lower()) or "gastos", "cats": cats,
           "currency": n.a("currency", ctx.currency), "seed": seed}
    opts = "".join(f"<option>{E(c)}</option>" for c in cats)
    inner = (f'<div class="exp reveal" data-expenses="{E(json.dumps(cfg, ensure_ascii=False))}"><div>'
             '<div class="exp-kpis"><div><small>Ingresos</small><b class="exp-in" data-k="in">0</b></div><div><small>Gastos</small>'
             '<b class="exp-out" data-k="out">0</b></div><div><small>Saldo</small><b data-k="bal">0</b></div></div>'
             '<form class="exp-form"><div class="field"><input name="text" placeholder="Concepto" required aria-label="Concepto"></div>'
             '<div class="field"><input name="amount" type="number" step="0.01" min="0" placeholder="Importe" required aria-label="Importe"></div>'
             '<div class="field"><select name="type" aria-label="Tipo"><option value="gasto">Gasto</option><option value="ingreso">Ingreso</option></select></div>'
             f'<div class="field"><select name="category" aria-label="Categoría">{opts}</select></div>'
             '<button class="btn btn-primary btn-sm" type="submit">Añadir</button></form><ul class="exp-list"></ul></div>'
             '<div class="card exp-bars"><h3>Gasto por categoría</h3><div class="exp-chart"></div></div></div>')
    return section(n, ctx, inner, "expenses-sec", "gastos")


@comp("float", "Botones flotantes: WhatsApp, volver arriba y modo claro/oscuro.",
      'whatsapp="34…" message="Hola, quería información" top="yes" theme="yes"', "", section=False, css="""
.float-btns { position: fixed; right: 1rem; bottom: 1rem; z-index: 70; display: grid; gap: .6rem; }
.float-btns a, .float-btns button { width: 52px; height: 52px; border-radius: 50%; border: 0; display: grid; place-items: center; font-size: 1.4rem;
  cursor: pointer; box-shadow: 0 10px 28px -10px rgba(0,0,0,.45); text-decoration: none !important; background: var(--card); color: var(--text); }
.float-btns .wa { background: #25d366; color: #fff; }
.float-btns [data-totop] { opacity: 0; pointer-events: none; transition: opacity .2s; }
.float-btns [data-totop].show { opacity: 1; pointer-events: auto; }
html[data-mode="dark"] { --bg: #0e1117; --surface: #151a23; --card: #1a202b; --text: #eef1f6; --muted: #9aa4b5; --border: #2a3140;
  --shadow: 0 18px 40px -26px rgba(0,0,0,.9); color-scheme: dark; }""")
def r_float(n, ctx):
    out = []
    if n.a("whatsapp"):
        from urllib.parse import quote
        num = re.sub(r"\D", "", n.a("whatsapp"))
        msg = f"?text={quote(n.a('message'))}" if n.a("message") else ""
        out.append(f'<a class="wa" href="https://wa.me/{num}{msg}" target="_blank" rel="noopener" aria-label="Escribir por WhatsApp">💬</a>')
    if n.yes("theme", True):
        out.append('<button type="button" data-theme-toggle aria-label="Cambiar a modo oscuro">🌙</button>')
    if n.yes("top", True):
        out.append('<button type="button" data-totop aria-label="Volver arriba">↑</button>')
    return f'<div class="float-btns">{"".join(out)}</div>'


@comp("cookies", "Aviso de cookies con «Aceptar» y «Rechazar» (recuerda la elección).",
      'text="Usamos cookies propias para…" link="Política de privacidad|#privacidad" accept="Aceptar" reject="Rechazar"', "",
      section=False, css="""
.cookie { position: fixed; left: 1rem; right: 1rem; bottom: 1rem; z-index: 75; max-width: 720px; margin-inline: auto; display: flex; gap: 1rem;
  align-items: center; flex-wrap: wrap; padding: 1rem 1.2rem; border-radius: var(--radius); background: var(--card); border: 1px solid var(--border);
  box-shadow: 0 24px 60px -24px rgba(0,0,0,.5); font-size: .92rem; }
.cookie p { margin: 0; flex: 1 1 300px; }
.cookie .acts { display: flex; gap: .5rem; }""")
def r_cookies(n, ctx):
    t, h = link(n.a("link"))
    return (f'<div class="cookie" role="dialog" aria-label="Aviso de cookies" hidden><p>🍪 {inline(n.a("text") or n.text or "Usamos cookies para que la web funcione y para mejorarla.")}'
            + (f' <a href="{E(h)}">{E(t)}</a>' if t else "") + '</p><div class="acts">'
            f'<button type="button" class="btn btn-ghost btn-sm" data-cookie="no">{E(n.a("reject", "Rechazar"))}</button>'
            f'<button type="button" class="btn btn-primary btn-sm" data-cookie="si">{E(n.a("accept", "Aceptar"))}</button></div></div>')


@comp("chatbot", "Asistente de chat simulado: responde preguntas frecuentes por palabras clave (sin IA ni servidor).",
      'name="Asistente" greeting="¡Hola! ¿En qué te ayudo?" fallback="No lo sé; escríbenos a hola@…"',
      '<item q="horario, abierto, hora" label="Horario">Abrimos de 9 a 20 h.</item>', section=False, css="""
.bot-btn { position: fixed; left: 1rem; bottom: 1rem; z-index: 72; width: 58px; height: 58px; border-radius: 50%; border: 0; cursor: pointer;
  background: var(--accent); color: var(--accent-contrast); font-size: 1.6rem; box-shadow: 0 14px 30px -12px var(--accent); }
.bot { position: fixed; left: 1rem; bottom: 5.2rem; z-index: 72; width: min(360px, calc(100vw - 2rem)); height: min(480px, 70vh); display: flex;
  flex-direction: column; border-radius: var(--radius); background: var(--bg); border: 1px solid var(--border); box-shadow: 0 30px 70px -25px rgba(0,0,0,.55); overflow: hidden; }
.bot[hidden] { display: none; }
.bot-head { padding: .8rem 1rem; background: var(--accent); color: var(--accent-contrast); font-weight: 700; display: flex; justify-content: space-between; }
.bot-head button { background: none; border: 0; color: inherit; font-size: 1.3rem; cursor: pointer; }
.bot-log { flex: 1; overflow: auto; padding: .9rem; display: flex; flex-direction: column; gap: .5rem; }
.bot-msg { max-width: 85%; padding: .55rem .8rem; border-radius: 14px; font-size: .92rem; background: var(--surface); }
.bot-msg.me { align-self: flex-end; background: var(--accent); color: var(--accent-contrast); }
.bot-chips { display: flex; flex-wrap: wrap; gap: .35rem; padding: 0 .9rem .6rem; }
.bot-chips button { border: 1px solid var(--border); background: var(--card); color: var(--text); border-radius: 999px; padding: .3rem .7rem; font: inherit; font-size: .8rem; cursor: pointer; }
.bot form { display: flex; gap: .4rem; padding: .7rem; border-top: 1px solid var(--border); }
.bot input { flex: 1; padding: .6rem .8rem; border-radius: 999px; border: 1px solid var(--border); background: var(--card); color: var(--text); font: inherit; }""")
def r_chatbot(n, ctx):
    faq = [{"q": [k.strip() for k in (it.a("q") or it.a("keywords")).split(",") if k.strip()],
            "label": it.a("label") or (it.a("q").split(",")[0].strip().capitalize() if it.a("q") else ""), "a": it.text}
           for it in n.items]
    cfg = {"name": n.a("name", "Asistente"), "greeting": n.a("greeting", "¡Hola! ¿En qué te puedo ayudar?"),
           "fallback": n.a("fallback", "No tengo esa respuesta. Escríbenos por el formulario de contacto."), "faq": faq}
    return (f'<button type="button" class="bot-btn" data-bot-open aria-label="Abrir el chat">💬</button>'
            f'<div class="bot" data-bot="{E(json.dumps(cfg, ensure_ascii=False))}" role="dialog" aria-label="{E(cfg["name"])}" hidden>'
            f'<div class="bot-head"><span>🤖 {E(cfg["name"])}</span><button type="button" data-bot-close aria-label="Cerrar">×</button></div>'
            '<div class="bot-log" aria-live="polite"></div><div class="bot-chips"></div>'
            '<form><input placeholder="Escribe tu pregunta…" aria-label="Tu pregunta"><button class="btn btn-primary btn-sm">Enviar</button></form></div>')


@comp("invoice", "Presupuesto o factura: líneas con cantidad y precio, impuesto, total y botón de imprimir/PDF.",
      'id="presupuesto" title="Presupuesto" number="P-2026-014" client="…" date="2026-10-11" currency="€" tax="21" '
      'tax-label="IVA" notes="Validez: 30 días" editable="yes"', "<row>Diseño web | 1 | 450</row>  (concepto | cantidad | precio)",
      css="""
.invoice { max-width: 860px; margin-inline: auto; padding: clamp(1.2rem, 3vw, 2.2rem); }
.inv-head { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.4rem; }
.inv-head h3 { font-size: 1.6rem; margin: 0; }
.inv-meta { text-align: right; color: var(--muted); font-size: .92rem; }
.invoice table { width: 100%; border-collapse: collapse; }
.invoice th, .invoice td { padding: .6rem .5rem; border-bottom: 1px solid var(--border); text-align: left; }
.invoice td.n, .invoice th.n { text-align: right; white-space: nowrap; }
.invoice input { width: 5rem; padding: .3rem .4rem; border-radius: 8px; border: 1px solid var(--border); background: var(--bg); color: var(--text); font: inherit; text-align: right; }
.inv-tot { margin-left: auto; width: min(320px, 100%); margin-top: 1rem; }
.inv-tot div { display: flex; justify-content: space-between; padding: .3rem 0; }
.inv-tot .grand { font-size: 1.3rem; font-weight: 800; border-top: 2px solid var(--text); margin-top: .3rem; padding-top: .5rem; }
.inv-notes { margin-top: 1.2rem; color: var(--muted); font-size: .9rem; }
@media print { .navbar, .footer, .float-btns, .banner, .cookie, .bot, .bot-btn, [data-print] { display: none !important; }
  .section { padding: 0 !important; } .invoice { box-shadow: none; border: 0; } }""")
def r_invoice(n, ctx):
    cur = n.a("currency", ctx.currency)
    try:
        tax = float(n.a("tax", "0").replace(",", "."))
    except ValueError:
        tax = 0.0
    edit = n.yes("editable", True)
    rows, sub = [], 0.0
    for it in n.items:
        cells = [c.strip() for c in it.text.split("|")]
        concept = cells[0] if cells else ""
        try:
            qty = float((cells[1] if len(cells) > 1 else "1").replace(",", "."))
            price = float(re.sub(r"[^\d.,-]", "", cells[2] if len(cells) > 2 else "0").replace(",", ".") or 0)
        except ValueError:
            qty, price = 1.0, 0.0
        sub += qty * price
        q = (f'<input type="number" min="0" step="1" value="{qty:g}" data-qty aria-label="Cantidad">' if edit else f"{qty:g}")
        rows.append(f'<tr data-price="{price}"><td>{inline(concept)}</td><td class="n">{q}</td><td class="n">{E(cur)}{price:.2f}</td>'
                    f'<td class="n" data-line>{E(cur)}{qty * price:.2f}</td></tr>')
    t_lab = n.a("tax-label", "IVA")
    meta = "<br>".join(x for x in (f"Nº {E(n.a('number'))}" if n.a("number") else "", E(n.a("date")),
                                   f"Cliente: <b>{E(n.a('client'))}</b>" if n.a("client") else "") if x)
    inner = (f'<div class="card invoice reveal" data-invoice data-tax="{tax}" data-currency="{E(cur)}"><div class="inv-head">'
             f'<h3>{inline(n.a("title", "Presupuesto"))}</h3><div class="inv-meta">{meta}</div></div>'
             f'<div class="data-table" style="overflow-x:auto"><table><thead><tr><th>Concepto</th><th class="n">Cantidad</th>'
             f'<th class="n">Precio</th><th class="n">Importe</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
             f'<div class="inv-tot"><div><span>Subtotal</span><span data-sub>{E(cur)}{sub:.2f}</span></div>'
             f'<div><span>{E(t_lab)} ({tax:g}%)</span><span data-tax-v>{E(cur)}{sub * tax / 100:.2f}</span></div>'
             f'<div class="grand"><span>Total</span><span data-total>{E(cur)}{sub * (1 + tax / 100):.2f}</span></div></div>'
             + (f'<p class="inv-notes">{inline(n.a("notes"))}</p>' if n.a("notes") else "")
             + '<div style="text-align:right;margin-top:1rem" data-print><button type="button" class="btn btn-ghost btn-sm" '
               'onclick="window.print()">🖨 Imprimir o guardar PDF</button></div></div>')
    n2 = Node(n.tag, {k: v for k, v in n.attrs.items() if k != "title"}, n.children, "")
    return section(n2, ctx, inner, "invoice-sec", "presupuesto")


@comp("progress", "Metas con barra de progreso (recaudación, objetivos, campañas).",
      'id="…" title="…" subtitle="…" currency="€" button="Colaborar|#donar"',
      '<item goal="10000" current="7250" deadline="31 dic">Recaudado para el comedor</item>', css="""
.goals { max-width: 760px; margin-inline: auto; display: grid; gap: 1.1rem; }
.goal { padding: 1.2rem 1.3rem; }
.goal-top { display: flex; justify-content: space-between; gap: 1rem; align-items: baseline; margin-bottom: .6rem; }
.goal-top b { font-size: 1.4rem; color: var(--accent); }
.goal-bar { height: 14px; border-radius: 999px; background: var(--border); overflow: hidden; }
.goal-bar i { display: block; height: 100%; width: 0; border-radius: inherit; background: linear-gradient(90deg, var(--accent), var(--accent-2)); transition: width 1.4s cubic-bezier(.2,.8,.2,1); }
.reveal.is-visible .goal-bar i { width: var(--p); }
.goal-meta { display: flex; justify-content: space-between; font-size: .85rem; color: var(--muted); margin-top: .45rem; }""")
def r_progress(n, ctx):
    cur = n.a("currency", "")
    out = []
    for it in n.items:
        try:
            goal = float(re.sub(r"[^\d.]", "", it.a("goal", "100")) or 100)
            now = float(re.sub(r"[^\d.]", "", it.a("current", "0")) or 0)
        except ValueError:
            goal, now = 100.0, 0.0
        pct = max(0.0, min(100.0, now / goal * 100 if goal else 0))
        fmt = lambda v: f"{cur}{v:,.0f}".replace(",", ".")      # noqa: E731
        out.append(f'<div class="card goal reveal"><div class="goal-top"><span>{inline(it.text)}</span><b>{pct:.0f}%</b></div>'
                   f'<div class="goal-bar" role="progressbar" aria-valuenow="{pct:.0f}" aria-valuemin="0" aria-valuemax="100">'
                   f'<i style="--p:{pct:.1f}%"></i></div><div class="goal-meta"><span>{E(fmt(now))} de {E(fmt(goal))}</span>'
                   f'<span>{E(it.a("deadline"))}</span></div></div>')
    btn = button(n.a("button"))
    return section(n, ctx, f'<div class="goals">{"".join(out)}' + (f'<div style="text-align:center">{btn}</div>' if btn else "")
                   + "</div>", "progress-sec", "metas")


@comp("raw", "HTML libre para lo que no cubren los componentes (puede incluir <style>). Úsalo poco.", "", "HTML dentro",
      section=False)
def r_raw(n, ctx):
    return n.text
