"""
Relevo automático orquestado por el programa: el modelo nunca relee lo ya escrito.

1. Primera ejecución: el modelo trabaja con un presupuesto (máx. tokens − reserva del 10–20 %).
   Si la tarea no cabe, deja un <relevo> con el ÍNDICE del trabajo:
       <relevo>
         <indice>1 | Título | hecho
                 2 | Título | cortada
                 3 | Título | pendiente</indice>
         <siguiente>formato a respetar (encabezados, longitud, estilo)</siguiente>
         <ramas>(opcional) partes independientes entre sí</ramas>
       </relevo>
   Si se queda sin presupuesto sin dejarlo, se le pide con la reserva dándole solo el
   objetivo, los encabezados ya escritos y las últimas líneas (nunca el texto entero).
2. El programa toma el índice como plan y encarga cada parte pendiente por separado con
   un contexto mínimo y constante: objetivo + índice + formato + «escribe la parte N».
   Las partes cortadas continúan desde sus últimas líneas, sin repetir ni descartar nada.
3. Cada salida se guarda etiquetada con el id de su parte y todo se unifica al final,
   en el orden del índice. Las partes de <ramas> pueden ejecutarse en paralelo.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import APP_DIR

LOG_DIR = APP_DIR / "relevos"
_TAGS = re.compile(r"</?parte\b[^>]*>[ \t]*\n?", re.I)


@dataclass
class Relay:
    hecho: str = ""
    pendiente: str = ""
    siguiente: str = ""
    indice: str = ""
    ramas: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.pendiente.strip() or self.siguiente.strip() or self.ramas
                    or any(i.status != "hecho" for i in plan_from(self.indice)))


@dataclass
class Item:
    id: str
    title: str
    status: str = "pendiente"          # hecho | cortada | pendiente


def _tag(text: str, name: str) -> str:
    m = re.search(rf"<{name}>(.*?)(?:</{name}>|(?=<\w+>)|$)", text, re.S | re.I)
    return m.group(1).strip() if m else ""


def parse(raw: str) -> Relay | None:
    """Interpreta un <relevo> (tolerante con etiquetas sin cerrar)."""
    if not raw or not raw.strip():
        return None
    m = re.search(r"<relevo>(.*?)(?:</relevo>|$)", raw, re.S | re.I)
    body = m.group(1) if m else raw
    r = Relay(_tag(body, "hecho"), _tag(body, "pendiente"), _tag(body, "siguiente"), _tag(body, "indice"))
    r.ramas = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", l).strip() for l in _tag(body, "ramas").splitlines() if l.strip()]
    r.ramas = [x for x in r.ramas if x and not x.lower().startswith("(opcional")]
    if r.empty and body.strip() and not any(_tag(body, t) for t in ("hecho", "indice")):
        r.siguiente = body.strip()                      # sin sub-etiquetas: todo es «siguiente»
    return None if r.empty else r


def strip(text: str) -> str:
    return re.sub(r"<relevo>.*?(?:</relevo>|$)", "", text, flags=re.S | re.I).rstrip()


def display(text: str) -> str:
    """Texto final: sin bloques de relevo ni etiquetas de parte."""
    return _TAGS.sub("", strip(text))


# ---------------------------------------------------------------------------
# Plan (índice)
# ---------------------------------------------------------------------------

def _status(s: str) -> str:
    s = s.lower()
    if "cort" in s or "incomplet" in s or "a medias" in s:
        return "cortada"
    if "pend" in s or "falta" in s or "por hacer" in s:
        return "pendiente"
    return "hecho"


def plan_from(indice: str) -> list[Item]:
    """«id | título | estado» por línea → lista de partes (ignora cabeceras y adornos)."""
    items: list[Item] = []
    for line in (indice or "").splitlines():
        cells = [c.strip(" -*•`") for c in line.split("|")]
        cells = [c for c in cells if c]
        if len(cells) < 2 or cells[0].lower() in ("id", "n", "nº", "#"):
            continue
        if len(cells) == 2:
            cells = [str(len(items) + 1)] + cells
        pid = re.sub(r"[^\w.]", "", cells[0]) or str(len(items) + 1)
        if any(i.id == pid for i in items):
            continue
        items.append(Item(pid, cells[1], _status(" ".join(cells[2:]))))
    return items


def plan_text(items: list[Item]) -> str:
    return "\n".join(f"{i.id} | {i.title} | {i.status}" for i in items)


def tail_of(text: str, chars: int = 400) -> str:
    t = display(text).rstrip()
    if len(t) <= chars:
        return t
    cut = t.find("\n", len(t) - chars)
    return t[cut + 1:] if 0 < cut < len(t) - 40 else t[-chars:]


def trim_cut(text: str) -> str:
    """
    Recorta lo que quedó a medias al agotarse el presupuesto hasta la última frase completa
    (o la última línea completa dentro de código), para que la continuación no empiece a
    mitad de palabra. Se pierden unas pocas palabras que el relevo vuelve a escribir.
    """
    t = text.rstrip()
    if t.count("```") % 2 == 1:
        i = t.rfind("\n")
        return t[:i] if i > t.rfind("```") else t
    ends = list(re.finditer(r"[.!?…»](?=\s|$)|\n+", t))
    if ends and 0 < len(t) - ends[-1].end() < 600:
        return t[:ends[-1].end()]                   # conserva el salto si el corte cae entre párrafos
    return t


def digest(text: str, max_par: int = 8) -> str:
    """Inicio de cada párrafo de la sección en curso: el relevo sabe qué se dijo sin releerlo entero."""
    t = display(text)
    hs = list(re.finditer(r"^\s{0,3}(?:#{1,4}\s+.+|\*\*\d+[.)].+\*\*|\d{1,2}[.)]\s+[^\n]{2,70})$", t, re.M))
    sec = t[hs[-1].start():] if hs else t[-3000:]
    out = []
    for p in [p.strip() for p in re.split(r"\n\s*\n", sec) if p.strip()][-max_par:]:
        w = p.split()
        out.append("- " + " ".join(w[:12]) + ("…" if len(w) > 12 else ""))
    return "\n".join(out)


def headings(text: str) -> list[str]:
    return [h.strip() for h in re.findall(r"^\s{0,3}(#{1,4}\s+.+|\*\*\d+[.)].+\*\*|\d{1,2}[.)]\s+[^\n]{2,70}[^.\n:;,])\s*$",
                                          display(text), re.M)]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def instructions(work_budget: int) -> str:
    return (
        f"Tienes un presupuesto de unos {work_budget} tokens por respuesta. Si la tarea completa no cabe, escribe "
        "lo que puedas y termina con este bloque (el usuario no lo ve; el programa encargará el resto):\n"
        "<relevo>\n<indice>una línea por parte del trabajo completo: id | título | hecho, cortada o pendiente</indice>\n"
        "<siguiente>formato que deben respetar las partes que faltan (encabezados, longitud, estilo)</siguiente>\n"
        "<ramas>(opcional) ids de partes pendientes independientes entre sí</ramas>\n</relevo>\n"
        "Si todo cabe, no escribas el bloque."
    )


ITEM_NOTE = ("Estás escribiendo UNA parte de un trabajo mayor que el programa unirá después. Escribe solo la parte "
             "que se te pide, completa, sin saludos, sin resúmenes de lo anterior y sin bloque <relevo>.")


def request_prompt(goal: str, done: list[str], tail: str) -> str:
    """Petición del índice con la reserva: objetivo + encabezados + últimas líneas (no el texto entero)."""
    return ("[Relevo] Se agotó el presupuesto de la respuesta anterior. NO escribas contenido de la tarea.\n\n"
            "OBJETIVO:\n" + goal.strip()
            + "\n\nENCABEZADOS YA ESCRITOS:\n" + ("\n".join(done) if done else "(ninguno)")
            + "\n\nÚLTIMAS LÍNEAS ESCRITAS (ahí se cortó):\n«" + tail + "»"
            + "\n\nEscribe SOLO el bloque <relevo> con <indice> (todas las partes del objetivo: id | título | "
              "hecho, cortada o pendiente; la que se cortó, «cortada») y <siguiente> (formato de lo que falta).")


def _context(goal: str, items: list[Item], fmt: str, example: str) -> str:
    s = "OBJETIVO GENERAL (no lo cambies):\n" + goal.strip()
    if items:
        s += "\n\nÍNDICE DEL TRABAJO:\n" + plan_text(items)
    if fmt:
        s += "\n\nFORMATO:\n" + fmt.strip()
    if example:
        s += f"\nLos encabezados ya escritos tienen esta forma: «{example}»"
    return s


def item_prompt(goal: str, items: list[Item], item: Item, fmt: str = "", example: str = "") -> str:
    return (f"[Relevo · parte {item.id}]\n" + _context(goal, items, fmt, example)
            + f"\n\nESCRIBE AHORA SOLO LA PARTE {item.id}: {item.title}\n"
              "Empieza por su encabezado y termínala. No escribas otras partes.")


def _said(said: str) -> str:
    return f"\n\nYA DICHO EN ESTA SECCIÓN (inicio de cada párrafo; no lo repitas):\n{said}" if said else ""


def resume_prompt(goal: str, items: list[Item], item: Item, tail: str, fmt: str = "", said: str = "") -> str:
    return (f"[Relevo · continuación de la parte {item.id}]\nOBJETIVO GENERAL (no lo cambies):\n{goal.strip()}"
            + _said(said)
            + f"\n\nLa parte {item.id} ({item.title}) se cortó. Sus últimas líneas son:\n{tail}\n\n"
              "Continúa EXACTAMENTE desde ahí, sin repetir nada de lo anterior, hasta terminar esa "
              "parte. No escribas otras partes.")


def continue_prompt(goal: str, done: list[str], tail: str, n: int, said: str = "") -> str:
    """Modo continuación: objetivo + encabezados ya escritos + últimas líneas. Nunca el texto entero."""
    return (f"[Relevo · continuación {n}]\nOBJETIVO (no lo cambies):\n{goal.strip()}"
            + "\n\nYA ESCRITO Y GUARDADO (solo encabezados):\n" + ("\n".join(done) if done else "(sin encabezados)")
            + _said(said)
            + f"\n\nTu respuesta anterior se cortó aquí:\n{tail}\n\n"
              "Continúa EXACTAMENTE desde la última palabra (termina primero la frase cortada), sin repetir nada, y "
              "sigue hasta completar el objetivo con el mismo formato"
            + (f"; los encabezados nuevos con la misma forma que «{done[-1]}»" if done else "")
            + ". No escribas resúmenes, índices, JSON ni bloques <relevo>.")


def plan_ok(items: list[Item], written_headings: int, fmt: str) -> bool:
    """¿El índice del modelo es utilizable? (los modelos pequeños a veces devuelven basura)."""
    return (len(items) >= 2 and len(items) >= written_headings - 1
            and any(i.status != "hecho" for i in items) and not fmt.lstrip().startswith(("[", "{")))


# ---------------------------------------------------------------------------
# Unión de trozos de una misma parte (continuaciones)
# ---------------------------------------------------------------------------

def _norm_line(l: str) -> str:
    return re.sub(r"[\W_]+", " ", l.lower()).strip()


def _skip_written(prev: str, new: str) -> str:
    """Si la continuación vuelve a empezar secciones ya escritas, se saltan hasta la primera nueva."""
    have = {_norm_line(h) for h in headings(prev)}
    if not have:
        return new
    blocks = re.split(r"(?m)^(?=\s{0,3}#{1,4}\s)", new)
    if len(blocks) < 2 and not any(_norm_line(l) in have for l in new.strip().splitlines()[:1]):
        return new
    out, skipping = [], False
    for b in blocks:
        first = b.strip().splitlines()[0] if b.strip() else ""
        if re.match(r"\s{0,3}#{1,4}\s", first):
            skipping = _norm_line(first) in have
        if not skipping:
            out.append(b)
    return "".join(out)


def _dedupe(prev: str, new: str) -> tuple[str, str, bool]:
    new = re.sub(r"^\s*[«\"“]+", "", new)                # el modelo a veces cita la cola entre comillas
    for k in range(min(len(prev), len(new), 1500), 39, -1):
        if prev.endswith(new[:k]):
            return prev, new[k:], True
    pw, nw = prev.split(), new.split()                   # solape corto, palabra a palabra
    for k in range(min(len(pw), len(nw), 30), 1, -1):
        if [w.lower() for w in pw[-k:]] == [w.lower() for w in nw[:k]] and sum(map(len, nw[:k])) >= 6:
            m = re.match(r"\s*(?:\S+\s+){%d}\S+" % (k - 1), new)
            return prev, new[m.end():], True
    skipped = _skip_written(prev, new)
    if skipped != new:
        return prev, skipped, False
    first = next((l for l in new.strip().splitlines() if l.strip()), "")
    key = _norm_line(first)
    if len(key) >= 4:
        lines = prev.rstrip().splitlines()
        for i in range(len(lines) - 1, max(-1, len(lines) - 40), -1):
            if _norm_line(lines[i]) == key:
                return "\n".join(lines[:i]).rstrip(), new, False
    return prev, new, False


_SENTENCE_END = re.compile(r"""[.!?:;)\]»"'`>|*_-]\s*$|\n\s*$""")


def merge(chunks: list[str]) -> str:
    """Une los trozos de una parte cortada: quita solapes y repara la frase o el bloque partido."""
    out = ""
    for p in chunks:
        p = _TAGS.sub("", re.sub(r"<relevo>.*?(?:</relevo>|$)", "", p, flags=re.S | re.I))   # sin rstrip
        if not out:
            out = p
            continue
        out, p, literal = _dedupe(out, p)
        if not out.strip():
            out = p.lstrip()
            continue
        if literal:
            out += re.sub(r"^[»\"”]+", "", p)
            continue
        open_fence = out.count("```") % 2 == 1
        if open_fence and p.lstrip().startswith("```"):
            prev = out[:out.rfind("```")].rstrip()
            out = (prev + "\n\n" + p.lstrip()) if prev else p.lstrip()
        elif open_fence:
            out = out.rstrip("\n") + "\n" + p.lstrip("\n")
        elif not _SENTENCE_END.search(out) and re.match(r"\s*(#|\*\*|\d{1,2}[.)]\s)", p):
            # la continuación abrió otra sección sin terminar la frase cortada: se quita el fragmento colgante
            ends = [m.end() for m in re.finditer(r"[.!?:»)](?=\s)|\n", out)]
            if ends and len(out) - ends[-1] < 400:
                out = out[:ends[-1]]
            out = out.rstrip() + "\n\n" + p.lstrip()
        elif not _SENTENCE_END.search(out) and re.match(r"\s*[a-záéíóúñ,]", p):
            out = out.rstrip() + " " + p.lstrip()
        elif (re.search(r"[.!?…»)]$", out) and re.match(r"\s*[A-ZÁÉÍÓÚÑ¿¡]", p)
              and not re.match(r"\s*(#|\*\*|\d{1,2}[.)]\s|[-*]\s|\|)", p)):
            out = out + " " + p.lstrip()              # la frase siguiente del mismo párrafo
        else:
            out = out.rstrip() + "\n\n" + p.lstrip()
    return out


def unify(parts: dict[str, str], items: list[Item]) -> str:
    """Une las partes etiquetadas en el orden del índice ("0" = la primera ejecución)."""
    order = ["0"] + [i.id for i in items]
    order += [k for k in parts if k not in order]
    return "\n\n".join(display(parts[k]).strip() for k in order if k in parts and display(parts[k]).strip())


@dataclass
class ChainLog:
    goal: str
    started: float = field(default_factory=time.time)
    steps: list[dict] = field(default_factory=list)

    def add(self, kind: str, tokens: int, prompt_tokens: int = 0, note: str = "", relay: Relay | None = None):
        self.steps.append({"t": round(time.time() - self.started, 1), "kind": kind, "tokens": tokens,
                           "prompt_tokens": prompt_tokens, "note": note, "relay": asdict(relay) if relay else None})

    def save(self, session_id: str) -> Path | None:
        if len(self.steps) < 2:
            return None
        d = LOG_DIR / session_id
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{time.strftime('%H%M%S')}.json"
        p.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8")
        return p
