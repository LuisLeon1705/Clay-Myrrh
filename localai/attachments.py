"""
Adjuntos: archivos de texto, imágenes y PDFs, y referencias @ruta en el mensaje.

PDF (modo "auto"):
- páginas con texto extraíble -> se extrae el texto (barato y exacto)
- páginas escaneadas (sin texto) -> se renderizan como imagen, solo si el modelo
  tiene visión; si no, se avisa claramente en lugar de enviar texto vacío
  (antes el modelo recibía "--- Página 1 ---" vacío y se inventaba el contenido).
"""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
TEXT_LIMIT = 400_000          # caracteres por archivo de texto
MIN_PAGE_CHARS = 40           # menos que esto => página escaneada


class AttachError(Exception):
    pass


@dataclass
class Attachment:
    name: str
    path: str
    kind: str                                   # "text" | "image" | "pdf"
    text: str = ""
    images: list[dict] = field(default_factory=list)   # {"b64","mime","w","h","name"}
    note: str = ""

    def summary(self) -> str:
        bits = []
        if self.text:
            bits.append(f"{len(self.text):,} caracteres".replace(",", "."))
        if self.images:
            bits.append(f"{len(self.images)} imagen(es)")
        return f"{self.name} ({', '.join(bits) or 'vacío'})" + (f" — {self.note}" if self.note else "")


def _pil():
    try:
        from PIL import Image
        return Image
    except ImportError:
        raise AttachError("Falta Pillow para procesar imágenes: pip install pillow") from None


def image_from_bytes(data: bytes, name: str, max_side: int) -> dict:
    """Redimensiona (lado mayor <= max_side) y devuelve el dict de imagen."""
    Image = _pil()
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:
        raise AttachError(f"No se pudo abrir la imagen {name}: {e}") from None
    if getattr(im, "n_frames", 1) > 1:
        im.seek(0)
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, "white")
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1])
        else:
            bg.paste(im.convert("RGB"))
        im = bg
    w, h = im.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    w, h = im.size
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=92)
    return {"b64": base64.b64encode(buf.getvalue()).decode("ascii"), "mime": "image/jpeg",
            "w": w, "h": h, "name": name}


def shrink_image(img: dict, factor: float) -> dict:
    """Reduce una imagen ya adjuntada (para que quepa en el contexto)."""
    data = base64.b64decode(img["b64"])
    side = max(64, int(max(img["w"], img["h"]) * factor))
    return image_from_bytes(data, img.get("name", "imagen"), side)


def load_image(path: Path, max_side: int) -> Attachment:
    img = image_from_bytes(path.read_bytes(), path.name, max_side)
    return Attachment(path.name, str(path), "image", images=[img], note=f"{img['w']}×{img['h']} px")


def load_text(path: Path) -> Attachment:
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise AttachError(f"{path.name} parece binario; solo se adjuntan textos, imágenes y PDFs.")
    text = raw.decode("utf-8", errors="replace")
    note = ""
    if len(text) > TEXT_LIMIT:
        text, note = text[:TEXT_LIMIT], f"truncado a {TEXT_LIMIT:,} caracteres".replace(",", ".")
    lang = path.suffix.lstrip(".")
    return Attachment(path.name, str(path), "text", text=f"[Archivo '{path.name}']\n```{lang}\n{text}\n```", note=note)


def _parse_pages(spec: str, total: int) -> list[int]:
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            pages.extend(range(int(a or 1), int(b or total) + 1))
        else:
            pages.append(int(part))
    return [p - 1 for p in pages if 1 <= p <= total]


def load_pdf(path: Path, *, vision: bool, mode: str = "auto", max_pages: int = 30,
             max_image_pages: int = 8, max_side: int = 1280, pages: str | None = None) -> Attachment:
    try:
        import pymupdf
    except ImportError:
        raise AttachError("Falta PyMuPDF para leer PDFs: pip install pymupdf") from None
    try:
        doc = pymupdf.open(str(path))
    except Exception as e:
        raise AttachError(f"No se pudo abrir el PDF: {e}") from None
    total = len(doc)
    if doc.needs_pass:
        raise AttachError("El PDF está protegido con contraseña.")
    idx = _parse_pages(pages, total) if pages else list(range(total))
    att = Attachment(path.name, str(path), "pdf")
    texts, scanned, skipped = [], [], 0
    for n, i in enumerate(idx):
        page = doc[i]
        t = page.get_text().strip() if mode != "images" else ""
        if mode != "images" and len(t) >= MIN_PAGE_CHARS:
            if len(texts) < max_pages:
                texts.append(f"--- Página {i + 1} ---\n{t}")
            else:
                skipped += 1
        elif mode != "text":
            scanned.append(i)
        else:
            texts.append(f"--- Página {i + 1} --- (sin texto extraíble)")
    notes = []
    if texts:
        att.text = f"[Contenido de texto de '{path.name}' ({total} págs.)]\n" + "\n\n".join(texts)
        notes.append(f"{len(texts)} pág. de texto")
    if skipped:
        notes.append(f"{skipped} pág. de texto omitidas (límite {max_pages}, usa /set pdf_max_pages)")
    if scanned:
        if not vision:
            doc.close()
            if not texts:
                raise AttachError(
                    f"'{path.name}' es un PDF escaneado ({len(scanned)} pág. sin texto) y el modelo actual "
                    f"no tiene visión. Cambia a un modelo con visión (/model) o instala su mmproj (/doctor).")
            notes.append(f"{len(scanned)} pág. escaneadas IGNORADAS (el modelo no tiene visión)")
        else:
            use = scanned[:max_image_pages]
            for i in use:
                pix = doc[i].get_pixmap(dpi=150)
                img = image_from_bytes(pix.tobytes("png"), f"{path.name} p.{i + 1}", max_side)
                att.images.append(img)
            notes.append(f"{len(use)} pág. escaneadas como imagen")
            if len(scanned) > len(use):
                notes.append(f"{len(scanned) - len(use)} escaneadas omitidas (límite {max_image_pages}, "
                             f"usa /set pdf_max_image_pages o /pdf ruta páginas 5-9)")
    doc.close()
    att.note = ", ".join(notes)
    return att


def load_any(path_str: str, *, vision: bool, settings, pdf_mode: str = "auto", pages: str | None = None) -> Attachment:
    path = Path(path_str.strip().strip("'\"")).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        raise AttachError(f"No existe: {path}")
    if path.is_dir():
        raise AttachError(f"{path} es una carpeta; adjunta archivos concretos.")
    ext = path.suffix.lower()
    if ext == ".pdf":
        return load_pdf(path, vision=vision, mode=pdf_mode, max_pages=settings.pdf_max_pages,
                        max_image_pages=settings.pdf_max_image_pages, max_side=settings.image_max_side, pages=pages)
    if ext in IMAGE_EXT:
        if not vision:
            raise AttachError(f"El modelo actual no tiene visión: no puede ver '{path.name}'. "
                              f"Usa /model para elegir uno con 👁 o /doctor para ver qué mmproj falta.")
        return load_image(path, settings.image_max_side)
    return load_text(path)


# @ruta, @"ruta con espacios", @'ruta'
MENTION_RE = re.compile(r'(?<![\w@])@(?:"([^"]+)"|\'([^\']+)\'|([^\s"\']+))')


def find_mentions(text: str) -> list[tuple[str, tuple[int, int]]]:
    out = []
    for m in MENTION_RE.finditer(text):
        p = m.group(1) or m.group(2) or m.group(3)
        p = p.rstrip(".,;:!?)")
        cand = Path(p).expanduser()
        if not cand.is_absolute():
            cand = Path.cwd() / cand
        if cand.exists() and cand.is_file():
            out.append((p, m.span()))
    return out
