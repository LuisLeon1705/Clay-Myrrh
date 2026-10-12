"""
Descubrimiento de modelos y emparejamiento de mmproj por METADATOS GGUF.

Reglas:
- Un GGUF con general.architecture == "clip" es un proyector (mmproj), esté donde esté.
- Un mmproj encaja con un modelo si su clip.vision.projection_dim coincide con el
  embedding_length del modelo y su clip.projector_type es compatible con la
  arquitectura. Si hay varios candidatos, desempata la similitud de nombres.
- "Visión" solo se marca como disponible si hay un mmproj emparejado. Si la
  arquitectura es multimodal pero falta el mmproj, se marca como "falta mmproj".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import MODELS_DIR, NON_CHAT_DIRS
from .gguf import GGUFError, GGUFInfo, read_gguf
from .i18n import T

# Arquitectura del LLM -> prefijos de projector_type compatibles
PROJECTOR_COMPAT: dict[str, tuple[str, ...]] = {
    "qwen2vl": ("qwen2vl", "qwen2.5vl", "qwen25vl"),
    "qwen25vl": ("qwen2.5vl", "qwen25vl", "qwen2vl"),
    "qwen3vl": ("qwen3vl",),
    "qwen3vlmoe": ("qwen3vl",),
    "qwen35": ("qwen3vl", "qwen35", "qwen3.5"),
    "qwen35moe": ("qwen3vl", "qwen35", "qwen3.5"),
    "gemma3": ("gemma3",),
    "gemma3n": ("gemma3n",),
    "gemma4": ("gemma4",),
    "llama4": ("llama4",),
    "mistral3": ("pixtral", "mistral"),
    "llama": ("mlp", "ldp", "pixtral", "llama"),
    "qwen2": ("resampler", "minicpmv", "internvl", "mlp"),
    "qwen3": ("resampler", "minicpmv", "internvl", "lfm"),
    "glm4": ("glm4v",),
    "lfm2": ("lfm2",),
}

# Arquitecturas que existen en versión multimodal (si no hay mmproj -> aviso).
VISION_FAMILIES = {"qwen2vl", "qwen25vl", "qwen3vl", "qwen3vlmoe", "gemma3", "gemma3n",
                   "gemma4", "llama4", "mistral3"}

# Dónde conseguir el mmproj que falta (verificado en la API de Hugging Face).
MMPROJ_SOURCES: list[tuple[str, str, str, str]] = [
    # (regex sobre nombre+general.name, repo, archivo, nota)
    (r"minicpm-?v-?2[._]?6", "openbmb/MiniCPM-V-2_6-gguf", "mmproj-model-f16.gguf", "996 MiB"),
    (r"qwen2\.5-?vl-?7b", "ggml-org/Qwen2.5-VL-7B-Instruct-GGUF",
     "mmproj-Qwen2.5-VL-7B-Instruct-Q8_0.gguf", "814 MiB (o -f16.gguf, 1291 MiB)"),
    (r"qwen2-?vl-?2b", "ggml-org/Qwen2-VL-2B-Instruct-GGUF", "mmproj-Qwen2-VL-2B-Instruct-f16.gguf", ""),
    (r"gemma[-_ ]?4", "unsloth/gemma-4-12b-it-GGUF", "mmproj-F16.gguf",
     "167 MiB (alternativa: ggml-org/gemma-4-12B-it-GGUF · mmproj-gemma-4-12B-it-Q8_0.gguf)"),
    (r"qwen3\.5-?9b", "unsloth/Qwen3.5-9B-GGUF", "mmproj-F16.gguf", "876 MiB"),
    (r"qwen3\.5-?4b|4b.*qwen3\.5|distill\.4b", "unsloth/Qwen3.5-4B-GGUF", "mmproj-F16.gguf",
     "641 MiB (fine-tune: puede funcionar si no se tocó la torre visual)"),
    (r"qwen3\.6-?27b", "unsloth/Qwen3.6-27B-GGUF", "mmproj-F16.gguf", "885 MiB"),
    (r"qwen3\.8-?27b", "unsloth/Qwen3.8-27B-GGUF", "mmproj-F16.gguf", "885 MiB"),
    (r"gemma-?3n", "mradermacher/gemma-3n-E4B-it-abliterated-GGUF",
     "gemma-3n-E4B-it-abliterated.mmproj-Q8_0.gguf",
     "1262 MiB · NO oficial (los repos oficiales no publican mmproj de Gemma 3n); experimental"),
]


@dataclass
class ModelEntry:
    name: str                       # nombre corto (archivo o nombre de Ollama)
    kind: str                       # "gguf" | "ollama"
    size_bytes: int
    path: str | None = None
    info: GGUFInfo | None = None
    mmproj: GGUFInfo | None = None
    vision: str = "no"              # "si" | "falta_mmproj" | "no"
    vision_note: str = ""
    error: str | None = None        # GGUF ilegible, etc.
    extra: dict[str, Any] = field(default_factory=dict)  # datos de Ollama

    @property
    def arch(self) -> str:
        return self.info.arch if self.info else self.extra.get("family", "?")

    @property
    def ctx_max(self) -> int:
        if self.info:
            return self.info.context_length
        return int(self.extra.get("context_length", 0) or 0)

    @property
    def size_gb(self) -> float:
        return self.size_bytes / 1024 ** 3

    @property
    def has_vision(self) -> bool:
        return self.vision == "si"

    def mmproj_hint(self) -> tuple[str, str, str] | None:
        hay = f"{self.name} {self.info.name if self.info else ''}".lower()
        for rx, repo, fname, note in MMPROJ_SOURCES:
            if re.search(rx, hay):
                return repo, fname, T(note, note.replace("alternativa", "alternative").replace(
                    "fine-tune: puede funcionar si no se tocó la torre visual", "fine-tune: may work if the vision tower was untouched").replace(
                    "NO oficial (los repos oficiales no publican mmproj de Gemma 3n); experimental",
                    "NOT official (the official repos don't publish a Gemma 3n mmproj); experimental"))
        return None


def _is_multimodal_family(info: GGUFInfo, filename: str) -> bool:
    arch = info.arch
    if arch in VISION_FAMILIES:
        return True
    # Qwen3.5/3.6 son nativamente multimodales: M-RoPE (rope.dimension_sections) lo delata
    if arch.startswith("qwen35") and info.get("rope.dimension_sections"):
        return True
    # MiniCPM-V guarda la parte de texto como "qwen2" sin ninguna marca visual en el
    # GGUF: aquí el nombre es la única pista posible.
    hay = f"{filename} {info.name}".lower()
    return bool(re.search(r"minicpm-?v|llava|-vl-|vision", hay))


def _name_tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9.]+", s.lower()) if t and t not in {"gguf", "mmproj", "f16", "bf16", "f32", "q8", "0"}}


def _projector_compatible(arch: str, ptype: str | None) -> bool | None:
    """True/False si se puede decidir; None si no hay tabla para esta arquitectura."""
    if not ptype:
        return None
    compat = PROJECTOR_COMPAT.get(arch)
    if compat is None:
        return None
    p = ptype.lower()
    return any(p.startswith(c) for c in compat)


def score_mmproj(model: GGUFInfo, model_file: str, proj: GGUFInfo) -> int:
    """Puntuación de emparejamiento (<= 0: incompatible)."""
    if not proj.has_vision_encoder:
        return 0
    n_embd = model.n_embd
    pdim = proj.projection_dim
    if pdim and n_embd:
        if pdim == n_embd:
            score = 100
        elif pdim % n_embd == 0:          # p. ej. deepstack de Qwen3-VL
            score = 60
        else:
            return 0
    else:
        score = 20
    compat = _projector_compatible(model.arch, proj.projector_type)
    if compat is False:
        return 0
    if compat is True:
        score += 50
    # desempate por nombre (Qwen2-VL-7B vs Qwen2.5-VL-7B tienen el mismo n_embd)
    mt = _name_tokens(f"{model_file} {model.name} {model.meta.get('general.basename', '')}")
    pt = _name_tokens(f"{Path(proj.path).name} {proj.name}")
    score += 5 * len(mt & pt)
    # mismo directorio: pequeño extra
    if Path(proj.path).parent == Path(model.path).parent:
        score += 3
    return score


def scan_gguf(models_dir: Path = MODELS_DIR) -> tuple[list[ModelEntry], list[GGUFInfo], list[tuple[str, str]]]:
    """Devuelve (modelos de chat, proyectores, errores)."""
    models: list[ModelEntry] = []
    projectors: list[GGUFInfo] = []
    errors: list[tuple[str, str]] = []
    if not models_dir.exists():
        return models, projectors, errors
    for f in sorted(models_dir.rglob("*.gguf")):
        rel = f.relative_to(models_dir).parts
        if rel and rel[0] in NON_CHAT_DIRS:
            continue
        sp = _SPLIT_RE.search(f.name)
        if sp and int(sp.group(1)) != 1:
            continue                       # partes 2..N de un modelo dividido
        try:
            info = read_gguf(f)
        except (GGUFError, OSError) as e:
            errors.append((f.name, str(e)))
            continue
        if info.is_clip:
            projectors.append(info)
            continue
        if info.meta.get("general.type") not in (None, "model"):
            continue  # adaptadores LoRA, etc.
        if sp:
            _merge_split_parts(info, f, int(sp.group(2)))
        models.append(ModelEntry(name=f.name, kind="gguf", size_bytes=info.file_size, path=str(f), info=info))
    return models, projectors, errors


_SPLIT_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.I)


def _merge_split_parts(info: GGUFInfo, first: Path, count: int):
    """Suma tamaño y tensores de las partes 2..N (los pesos están repartidos entre todas)."""
    for i in range(2, count + 1):
        part = first.with_name(_SPLIT_RE.sub(f"-{i:05d}-of-{count:05d}.gguf", first.name))
        if not part.exists():
            continue
        try:
            pi = read_gguf(part)
        except Exception:
            continue
        info.file_size += pi.file_size
        for k, v in pi.layer_bytes.items():
            info.layer_bytes[k] = info.layer_bytes.get(k, 0) + v
        for k, v in pi.other_bytes.items():
            info.other_bytes[k] = info.other_bytes.get(k, 0) + v


def pair_mmproj(entry: ModelEntry, projectors: list[GGUFInfo], override: str | None = None):
    """Asigna entry.mmproj / entry.vision según metadatos (o el override del usuario)."""
    info = entry.info
    if info is None:
        return
    entry.mmproj, entry.vision, entry.vision_note = None, "no", ""
    if override == "none":
        entry.vision_note = T("visión desactivada manualmente (/mmproj none)", "vision disabled manually (/mmproj none)")
        return
    if override:
        p = next((x for x in projectors if Path(x.path).name == override or x.path == override), None)
        if p is None and Path(override).exists():
            try:
                p = read_gguf(override)
            except Exception:
                p = None
        if p is not None:
            entry.mmproj, entry.vision = p, "si"
            if score_mmproj(info, entry.name, p) <= 0:
                entry.vision_note = T((f"forzado; dimensiones/tipo no cuadran "
                                     f"(proj_dim={p.projection_dim}, n_embd={info.n_embd})"), (f"forced; dimensions/type don't match "
                                     f"(proj_dim={p.projection_dim}, n_embd={info.n_embd})"))
            return
    scored = sorted(((score_mmproj(info, entry.name, p), p) for p in projectors),
                    key=lambda t: t[0], reverse=True)
    if scored and scored[0][0] > 0:
        entry.mmproj, entry.vision = scored[0][1], "si"
        return
    if _is_multimodal_family(info, entry.name):
        entry.vision = "falta_mmproj"
        entry.vision_note = T((f"arquitectura multimodal ({info.arch}, n_embd={info.n_embd}) "
                             f"pero no hay ningún mmproj con projection_dim={info.n_embd}"), (f"multimodal architecture ({info.arch}, n_embd={info.n_embd}) "
                             f"but there is no mmproj with projection_dim={info.n_embd}"))


def ollama_models(host: str, timeout: float = 1.0) -> list[ModelEntry]:
    try:
        import requests
        r = requests.get(f"{host}/api/tags", timeout=timeout)
        r.raise_for_status()
    except Exception:
        return []
    out = []
    for m in r.json().get("models", []):
        details = m.get("details", {}) or {}
        out.append(ModelEntry(
            name=m.get("name", "?"), kind="ollama", size_bytes=int(m.get("size", 0)),
            vision="?", extra={"family": details.get("family", "?"),
                                "quant": details.get("quantization_level", ""),
                                "params": details.get("parameter_size", "")},
        ))
    return out


@dataclass
class Catalog:
    models: list[ModelEntry]
    projectors: list[GGUFInfo]
    errors: list[tuple[str, str]]

    def find(self, name: str) -> ModelEntry | None:
        n = name.strip().strip("'\"")
        for m in self.models:
            if m.name == n or (m.path and Path(m.path) == Path(n)):
                return m
        low = n.lower()
        cands = [m for m in self.models if low in m.name.lower()]
        return cands[0] if len(cands) == 1 else None

    def users_of(self, proj: GGUFInfo) -> list[str]:
        return [m.name for m in self.models if m.mmproj is not None and m.mmproj.path == proj.path]


def build_catalog(settings, include_ollama: bool = True, host: str | None = None) -> Catalog:
    from .config import OLLAMA_HOST
    models, projectors, errors = scan_gguf()
    for m in models:
        pair_mmproj(m, projectors, settings.models.get(m.name, {}).get("mmproj"))
    # rutas sueltas usadas antes con -m (fuera de models/)
    if include_ollama:
        models += ollama_models(host or OLLAMA_HOST)
    return Catalog(models, projectors, errors)


def entry_from_path(path: str, projectors: list[GGUFInfo]) -> ModelEntry:
    info = read_gguf(path)
    if info.is_clip:
        raise GGUFError(T(f"{Path(path).name} es un proyector mmproj, no un modelo de chat", f"{Path(path).name} is an mmproj projector, not a chat model"))
    e = ModelEntry(name=Path(path).name, kind="gguf", size_bytes=info.file_size, path=str(Path(path).resolve()), info=info)
    pair_mmproj(e, projectors)
    return e
