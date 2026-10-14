"""
Lector visual: un modelo con visión se enciende solo para TRANSCRIBIR imágenes y
páginas escaneadas a texto; el modelo de chat elegido trabaja después sobre ese
texto. Así se usa el mejor modelo de texto para razonar y el VLM solo para "leer".

Las transcripciones se guardan en ~/.local_ai/transcripciones/ (clave = hash de la
imagen + lector + versión del prompt), así que un archivo ya leído no se vuelve a
procesar.
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
from pathlib import Path

from .config import APP_DIR

CACHE_DIR = APP_DIR / "transcripciones"
PROMPT_VERSION = "v1"
READER_CTX = 8192
READER_MAX_TOKENS = 2048

MASTER_PROMPT = """Eres un TRANSCRIPTOR de documentos e imágenes. Lo que escribas lo leerá otra inteligencia artificial que NO puede ver la imagen: debe poder trabajar únicamente con tu texto.

Reglas:
1. Transcribe TODO el texto visible, literal, en su idioma original y en orden de lectura. No resumas, no traduzcas, no corrijas faltas ni completes frases.
2. Conserva la estructura con Markdown: títulos (#), párrafos, listas numeradas o con viñetas y CADA tabla como tabla Markdown completa, con todas sus filas y columnas en el mismo orden. No te saltes filas aunque se repitan valores.
3. Marcas especiales: texto que no se lee → [ilegible]; tachado o censurado → [tachado]; casillas → [x] / [ ]; firmas, sellos o logotipos → [firma], [sello], [logo: …]; líneas para rellenar → ______.
4. Describe entre corchetes los elementos visuales que aportan información: gráficos con sus ejes y valores, diagramas, esquemas, flechas, fotos: [Figura: …].
5. NO resuelvas ejercicios, NO respondas a preguntas del documento y NO añadas opiniones, explicaciones ni comentarios propios.
6. Si es una foto o imagen sin texto, descríbela de forma objetiva y detallada: objetos, personas, colores, posiciones y cualquier texto visible.

Empieza directamente con la transcripción, sin introducción."""


def _key(img: dict, reader_name: str) -> str:
    raw = base64.b64decode(img["b64"])
    h = hashlib.sha1()
    h.update(f"{PROMPT_VERSION}|{reader_name}|".encode())
    h.update(raw)
    return h.hexdigest()


def cached(img: dict, reader_name: str) -> str | None:
    f = CACHE_DIR / f"{_key(img, reader_name)}.md"
    if not f.exists():
        return None
    text = f.read_text(encoding="utf-8")
    return re.sub(r"^<!--.*?-->\n", "", text, count=1, flags=re.S)


def fix_tables(text: str) -> str:
    """Algunos VLM escriben las tablas Markdown en una sola línea ('| a | b | | c | d |'): se separan las filas."""
    out = []
    for ln in text.split("\n"):
        s = ln.strip()
        if s.startswith("|") and s.count("| |") >= 2:
            s = re.sub(r"\|\s+\|(?=\s*(?:---|[^|\s]))", "|\n|", s)
            out.extend(r.strip() for r in s.split("\n"))
        else:
            out.append(ln)
    return "\n".join(out)


def store(img: dict, reader_name: str, text: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / f"{_key(img, reader_name)}.md"
    header = (f"<!-- origen: {img.get('name', 'imagen')} · lector: {reader_name} · "
              f"{time.strftime('%Y-%m-%d %H:%M')} · prompt {PROMPT_VERSION} -->\n")
    f.write_text(header + text.strip() + "\n", encoding="utf-8")
    return f


def cache_stats() -> tuple[int, int]:
    if not CACHE_DIR.exists():
        return 0, 0
    files = list(CACHE_DIR.glob("*.md"))
    return len(files), sum(f.stat().st_size for f in files)


def clear_cache() -> int:
    n = 0
    for f in CACHE_DIR.glob("*.md") if CACHE_DIR.exists() else []:
        f.unlink()
        n += 1
    return n


def _params_b(entry) -> float:
    """Tamaño aproximado del modelo en miles de millones de parámetros."""
    label = str(entry.info.meta.get("general.size_label", "")) if entry.info else ""
    m = re.match(r"([\d.]+)\s*[Bb]", label)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)[Bb]\b", entry.name)
    return float(m.group(1)) if m else entry.size_bytes / 1e9


def usable(m) -> bool:
    """¿Puede hacer de lector? GGUF con mmproj y plantilla de chat (libmtmd la necesita para la imagen)."""
    return m.kind == "gguf" and m.has_vision and bool(m.info and m.info.chat_template)


def pick_reader(catalog, settings):
    """Modelo con visión que hará de lector: el elegido en /vision modelo o el más grande disponible."""
    vis = [m for m in catalog.models if usable(m)]
    if not vis:
        return None
    if settings.vision_model and settings.vision_model != "auto":
        chosen = catalog.find(settings.vision_model)
        if chosen in vis:
            return chosen
    return max(vis, key=_params_b)


def user_prompt(img: dict, index: int, total: int) -> str:
    where = f"imagen {index} de {total}" if total > 1 else "la imagen"
    return f"Transcribe {where} («{img.get('name', 'imagen')}») siguiendo las reglas."


def format_for_chat(name: str, parts: list[tuple[str, str]], reader_name: str) -> str:
    """Texto que recibe el modelo de chat en lugar de las imágenes."""
    body = "\n\n".join(f"--- {label} ---\n{txt.strip()}" for label, txt in parts)
    return (f"[Transcripción de «{name}» hecha por un modelo lector ({reader_name}); el contenido original "
            f"era visual. Úsala como si vieras el documento.]\n{body}")
