"""
Lector rápido de cabeceras GGUF: metadatos + tabla de tensores, sin cargar pesos.

Solo lee la cabecera (unos MB aunque el modelo pese 17 GB). Los arrays grandes
(vocabulario, merges) se saltan y solo se guarda su longitud. Los resultados se
cachean en ~/.local_ai/gguf_cache.json indexados por ruta + tamaño + mtime.
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import APP_DIR

_SCALAR = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}
_MAX_ARRAY_KEPT = 1024
_CACHE_FILE = APP_DIR / "gguf_cache.json"
_CACHE_VERSION = 3
_BLK_RE = re.compile(r"^blk\.(\d+)\.")


class GGUFError(Exception):
    pass


@dataclass
class GGUFInfo:
    path: str
    file_size: int
    meta: dict[str, Any]
    layer_bytes: dict[int, int] = field(default_factory=dict)   # blk.N -> bytes
    other_bytes: dict[str, int] = field(default_factory=dict)   # tensores fuera de bloques

    # ----- accesos cómodos -------------------------------------------------
    @property
    def arch(self) -> str:
        return str(self.meta.get("general.architecture", "?"))

    @property
    def is_clip(self) -> bool:
        return self.arch in ("clip", "mtmd")

    def get(self, key: str, default: Any = None) -> Any:
        """Lee '<arch>.<key>' (o la clave tal cual si empieza por general./tokenizer./clip.)."""
        if key.startswith(("general.", "tokenizer.", "clip.")):
            return self.meta.get(key, default)
        return self.meta.get(f"{self.arch}.{key}", default)

    @property
    def name(self) -> str:
        return str(self.meta.get("general.name") or Path(self.path).stem)

    @property
    def context_length(self) -> int:
        return int(self.get("context_length", 0) or 0)

    @property
    def block_count(self) -> int:
        return int(self.get("block_count", 0) or 0)

    @property
    def n_embd(self) -> int:
        return int(self.get("embedding_length", 0) or 0)

    @property
    def n_vocab(self) -> int:
        v = self.meta.get("tokenizer.ggml.tokens")
        if isinstance(v, dict) and "__len__" in v:
            return int(v["__len__"])
        if isinstance(v, list):
            return len(v)
        return int(self.get("vocab_size", 0) or 0)

    @property
    def chat_template(self) -> str | None:
        t = self.meta.get("tokenizer.chat_template")
        return t if isinstance(t, str) and t else None

    # ----- proyectores clip/mmproj ------------------------------------------
    @property
    def projector_type(self) -> str | None:
        for k in ("clip.projector_type", "clip.vision.projector_type"):
            if self.meta.get(k):
                return str(self.meta[k])
        if self.meta.get("clip.has_qwen2vl_merger"):
            return "qwen2vl_merger"
        if self.meta.get("clip.has_minicpmv_projector"):
            return "resampler"
        if self.meta.get("clip.has_llava_projector"):
            return "mlp"
        return None

    @property
    def projection_dim(self) -> int:
        return int(self.meta.get("clip.vision.projection_dim") or 0)

    @property
    def has_vision_encoder(self) -> bool:
        return bool(self.meta.get("clip.has_vision_encoder", True))


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class _Reader:
    def __init__(self, f):
        self.f = f

    def unpack(self, fmt: str):
        size = struct.calcsize("<" + fmt)
        data = self.f.read(size)
        if len(data) != size:
            raise GGUFError("Cabecera GGUF truncada")
        return struct.unpack("<" + fmt, data)

    def u32(self) -> int:
        return self.unpack("I")[0]

    def u64(self) -> int:
        return self.unpack("Q")[0]

    def string(self) -> str:
        n = self.u64()
        if n > 50_000_000:
            raise GGUFError("Cadena GGUF demasiado larga (archivo corrupto)")
        return self.f.read(n).decode("utf-8", "replace")

    def skip_string(self):
        self.f.seek(self.u64(), 1)

    def value(self, t: int):
        if t in _SCALAR:
            return self.unpack(_SCALAR[t])[0]
        if t == 8:
            return self.string()
        if t == 9:
            it = self.u32()
            n = self.u64()
            if it == 8:
                if n > _MAX_ARRAY_KEPT:
                    for _ in range(n):
                        self.skip_string()
                    return {"__len__": n}
                return [self.string() for _ in range(n)]
            if it in _SCALAR:
                sz = struct.calcsize("<" + _SCALAR[it])
                if n > _MAX_ARRAY_KEPT:
                    self.f.seek(sz * n, 1)
                    return {"__len__": n}
                return list(self.unpack(_SCALAR[it] * n)) if n else []
            if it == 9:
                return [self.value(9) for _ in range(n)]
            raise GGUFError(f"Tipo de array GGUF desconocido: {it}")
        raise GGUFError(f"Tipo GGUF desconocido: {t}")


def _parse(path: Path) -> GGUFInfo:
    file_size = path.stat().st_size
    with open(path, "rb") as f:
        r = _Reader(f)
        if f.read(4) != b"GGUF":
            raise GGUFError(f"{path.name} no es un archivo GGUF")
        version = r.u32()
        if version < 2:
            raise GGUFError(f"Versión GGUF {version} no soportada")
        n_tensors = r.u64()
        n_kv = r.u64()
        meta: dict[str, Any] = {"__gguf_version__": version}
        for _ in range(n_kv):
            key = r.string()
            meta[key] = r.value(r.u32())

        tensors: list[tuple[str, int]] = []
        for _ in range(n_tensors):
            name = r.string()
            n_dims = r.u32()
            f.seek(8 * n_dims + 4, 1)  # dims + tipo
            tensors.append((name, r.u64()))
        header_end = f.tell()

    alignment = int(meta.get("general.alignment", 32) or 32)
    data_start = (header_end + alignment - 1) // alignment * alignment
    data_size = file_size - data_start

    # El tamaño de cada tensor = diferencia entre offsets consecutivos (independiente
    # del tipo de cuantización, así funciona incluso con tipos nuevos).
    ordered = sorted(tensors, key=lambda t: t[1])
    layer_bytes: dict[int, int] = {}
    other_bytes: dict[str, int] = {}
    for i, (name, off) in enumerate(ordered):
        nxt = ordered[i + 1][1] if i + 1 < len(ordered) else data_size
        size = max(0, nxt - off)
        m = _BLK_RE.match(name)
        if m:
            idx = int(m.group(1))
            layer_bytes[idx] = layer_bytes.get(idx, 0) + size
        else:
            base = name.rsplit(".", 1)[0] if name.endswith((".weight", ".bias")) else name
            other_bytes[base] = other_bytes.get(base, 0) + size
    return GGUFInfo(str(path), file_size, meta, layer_bytes, other_bytes)


# ---------------------------------------------------------------------------
# Caché
# ---------------------------------------------------------------------------

_cache: dict[str, Any] | None = None


def _load_cache() -> dict[str, Any]:
    global _cache
    if _cache is None:
        try:
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            _cache = data if data.get("__v") == _CACHE_VERSION else {"__v": _CACHE_VERSION}
        except Exception:
            _cache = {"__v": _CACHE_VERSION}
    return _cache


def _save_cache():
    if _cache is None:
        return
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(_cache), encoding="utf-8")
    except Exception:
        pass


def read_gguf(path: str | Path) -> GGUFInfo:
    """Lee (o recupera de caché) la cabecera de un GGUF."""
    p = Path(path).resolve()
    st = p.stat()
    key = str(p).lower()
    stamp = f"{st.st_size}:{int(st.st_mtime)}"
    cache = _load_cache()
    hit = cache.get(key)
    if hit and hit.get("stamp") == stamp:
        return GGUFInfo(
            hit["path"], hit["file_size"], hit["meta"],
            {int(k): v for k, v in hit["layer_bytes"].items()}, hit["other_bytes"],
        )
    info = _parse(p)
    cache[key] = {
        "stamp": stamp, "path": info.path, "file_size": info.file_size, "meta": info.meta,
        "layer_bytes": info.layer_bytes, "other_bytes": info.other_bytes,
    }
    _save_cache()
    return info
