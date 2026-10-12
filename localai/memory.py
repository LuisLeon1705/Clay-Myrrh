"""
Estimación de VRAM/RAM antes de cargar un modelo y medición real después.

Todo sale del GGUF:
- pesos por capa (tamaño real de los tensores blk.N.*), embeddings y salida
- caché KV por capa = celdas × n_kv_heads(capa) × (key_length + value_length) × bytes/elem
  teniendo en cuenta: capas SWA (ventana deslizante), capas híbridas recurrentes
  (qwen35: solo 1 de cada full_attention_interval capas tiene KV) y capas con KV
  compartida (gemma3n: attention.shared_kv_layers)
- estado recurrente (SSM) de las capas no-atención
- mmproj (pesos + buffer de cómputo del codificador de imagen)
- buffer de cómputo ≈ n_ubatch × 4 B × (n_vocab + 2·n_ff) (+ matriz KQ si no hay flash-attn)

Calibrado contra los logs de llama.cpp 0.3.34 (Vulkan) en este equipo.
"""

from __future__ import annotations

import ctypes
import math
import time
from dataclasses import dataclass, field

from .gguf import GGUFInfo
from .i18n import T

MiB = 1024 * 1024
GiB = 1024 * MiB

KV_BYTES = {"f32": 4.0, "f16": 2.0, "bf16": 2.0, "q8_0": 34 / 32, "q5_1": 24 / 32,
            "q5_0": 22 / 32, "q4_1": 20 / 32, "q4_0": 18 / 32, "iq4_nl": 18 / 32}
KV_GGML_TYPE = {"f32": 0, "f16": 1, "q4_0": 2, "q4_1": 3, "q5_0": 6, "q5_1": 7, "q8_0": 8,
                "iq4_nl": 20, "bf16": 30}

GPU_OVERHEAD = 250 * MiB      # contexto Vulkan/CUDA, buffers de salida, etc.
CPU_OVERHEAD = 450 * MiB      # proceso Python + librerías
N_UBATCH = 512
KV_PAD = 256


# ---------------------------------------------------------------------------
# Memoria del sistema
# ---------------------------------------------------------------------------

@dataclass
class SysMem:
    gpu_name: str = ""
    vram_total: int = 0
    vram_free: int = 0
    ram_total: int = 0
    ram_avail: int = 0
    source: str = ""
    vendor: str = ""               # nvidia | amd | intel | ""
    free_estimated: bool = False   # VRAM libre no medida (total − margen)

    @property
    def vram_used(self) -> int:
        return self.vram_total - self.vram_free

    @property
    def ram_used(self) -> int:
        return self.ram_total - self.ram_avail


_cache: tuple[float, SysMem] | None = None


def _ram() -> tuple[int, int]:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return vm.total, vm.available
    except Exception:
        pass
    try:  # Windows sin psutil
        class MEMSTAT(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEMSTAT()
        m.dwLength = ctypes.sizeof(MEMSTAT)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys, m.ullAvailPhys
    except Exception:
        return 0, 0


def probe(max_age: float = 0.0) -> SysMem:
    """VRAM (detección única de localai.gpu: NVML → nvidia-smi → registro de Windows) y RAM. Cacheado max_age s."""
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] <= max_age:
        return _cache[1]
    from . import gpu
    g = gpu.info(max_age=max(2.0, max_age))
    s = SysMem(gpu_name=g.name, vram_total=g.total, vram_free=g.free, source=g.source, vendor=g.vendor,
               free_estimated=g.free_estimated)
    s.ram_total, s.ram_avail = _ram()
    _cache = (now, s)
    return s


# ---------------------------------------------------------------------------
# Parámetros de arquitectura
# ---------------------------------------------------------------------------

def _per_layer(v, n: int, default: int = 0) -> list[int]:
    if isinstance(v, list) and v:
        return [int(x) for x in (v + [v[-1]] * n)[:n]]
    if isinstance(v, (int, float)) and v:
        return [int(v)] * n
    return [default] * n


@dataclass
class ArchParams:
    n_layer: int
    n_embd: int
    n_vocab: int
    n_ff: int
    n_head: list[int]
    n_head_kv: list[int]
    k_len: list[int]
    v_len: list[int]
    is_swa: list[bool]
    has_kv: list[bool]
    is_recurrent: list[bool]
    n_swa: int
    skip_layers: set[int]          # capas cuyo peso no se carga (MTP/nextn)
    ssm_r_bytes: int = 0           # estado conv por capa recurrente
    ssm_s_bytes: int = 0           # estado SSM por capa recurrente
    notes: list[str] = field(default_factory=list)


def arch_params(info: GGUFInfo) -> ArchParams:
    n = info.block_count or max(info.layer_bytes, default=-1) + 1
    n_embd = info.n_embd
    n_head = _per_layer(info.get("attention.head_count"), n, 1)
    n_head_kv = _per_layer(info.get("attention.head_count_kv"), n, n_head[0])
    head_dim = n_embd // max(n_head[0], 1) if n_embd else 128
    k_full = int(info.get("attention.key_length") or head_dim)
    v_full = int(info.get("attention.value_length") or head_dim)
    k_swa = int(info.get("attention.key_length_swa") or k_full)
    v_swa = int(info.get("attention.value_length_swa") or v_full)
    n_ff = info.get("feed_forward_length") or 4 * n_embd
    n_ff = max(n_ff) if isinstance(n_ff, list) else int(n_ff)
    n_swa = int(info.get("attention.sliding_window") or 0)
    notes: list[str] = []

    # --- patrón SWA -----------------------------------------------------------
    pat = info.get("attention.sliding_window_pattern")
    if isinstance(pat, list) and pat:
        is_swa = [bool(x) for x in (pat + [pat[-1]] * n)[:n]]
    elif isinstance(pat, int) and pat > 0:
        is_swa = [(i + 1) % pat != 0 for i in range(n)]
    elif n_swa and info.arch in ("gemma3",):
        is_swa = [(i + 1) % 6 != 0 for i in range(n)]
    elif n_swa and info.arch in ("gemma2", "cohere2"):
        is_swa = [i % 2 == 0 for i in range(n)]
    else:
        is_swa = [False] * n
    if not n_swa:
        is_swa = [False] * n
    if any(is_swa):
        notes.append(T(f"SWA: {sum(is_swa)}/{n} capas con ventana de {n_swa} tokens", f"SWA: {sum(is_swa)}/{n} layers with a {n_swa}-token window"))

    # --- capas recurrentes (híbridos tipo Qwen3.5/Qwen3-Next) -------------------
    interval = int(info.get("full_attention_interval") or 0)
    if interval > 0:
        is_rec = [(i + 1) % interval != 0 for i in range(n)]
        notes.append(T(f"híbrido: {n - sum(is_rec)}/{n} capas de atención (KV), resto recurrentes", f"hybrid: {n - sum(is_rec)}/{n} attention (KV) layers, the rest recurrent"))
    else:
        is_rec = [False] * n

    # --- MTP / nextn: capas extra que no se cargan ------------------------------
    nextn = int(info.get("nextn_predict_layers") or 0)
    skip = set(range(n - nextn, n)) if nextn else set()

    # --- KV compartida (gemma3n) ------------------------------------------------
    shared = int(info.get("attention.shared_kv_layers") or 0)
    has_kv = [not is_rec[i] and i not in skip for i in range(n)]
    if shared:
        for i in range(max(0, n - shared), n):
            has_kv[i] = False
        notes.append(T(f"KV compartida: las últimas {shared} capas reutilizan caché", f"shared KV: the last {shared} layers reuse the cache"))

    k_len = [k_swa if is_swa[i] else k_full for i in range(n)]
    v_len = [v_swa if is_swa[i] else v_full for i in range(n)]

    r_bytes = s_bytes = 0
    if any(is_rec):
        d_conv = int(info.get("ssm.conv_kernel") or 4)
        d_state = int(info.get("ssm.state_size") or 128)
        d_inner = int(info.get("ssm.inner_size") or 2 * n_embd)
        n_group = int(info.get("ssm.group_count") or 1)
        r_bytes = (d_conv - 1) * (d_inner + 2 * n_group * d_state) * 4
        s_bytes = d_state * d_inner * 4

    return ArchParams(n, n_embd, info.n_vocab, n_ff, n_head, n_head_kv, k_len, v_len,
                      is_swa, has_kv, is_rec, n_swa, skip, r_bytes, s_bytes, notes)


# ---------------------------------------------------------------------------
# Estimación
# ---------------------------------------------------------------------------

@dataclass
class Estimate:
    n_ctx: int
    ngl: int
    n_layer: int
    kv_type: str
    weights_gpu: int = 0
    weights_cpu: int = 0
    kv_gpu: int = 0
    kv_cpu: int = 0
    rs_gpu: int = 0
    rs_cpu: int = 0
    mmproj_gpu: int = 0
    compute_gpu: int = 0
    compute_cpu: int = 0
    overhead_gpu: int = 0
    overhead_cpu: int = 0
    calib: float = 1.0
    notes: list[str] = field(default_factory=list)

    @property
    def vram(self) -> int:
        raw = (self.weights_gpu + self.kv_gpu + self.rs_gpu + self.mmproj_gpu
               + self.compute_gpu + self.overhead_gpu)
        return int(raw * self.calib)

    @property
    def ram(self) -> int:
        return self.weights_cpu + self.kv_cpu + self.rs_cpu + self.compute_cpu + self.overhead_cpu

    @property
    def kv_per_token(self) -> float:
        return (self.kv_gpu + self.kv_cpu) / max(self.n_ctx, 1)


def max_gpu_layers(info: GGUFInfo) -> int:
    """n_gpu_layers que lo mete todo (capas + salida)."""
    return (info.block_count or 0) + 1


def estimate(info: GGUFInfo, n_ctx: int, ngl: int, kv_type: str = "f16", swa_full: bool = False,
             flash_attn: bool = True, mmproj: GGUFInfo | None = None, n_ubatch: int = N_UBATCH,
             calib: float = 1.0) -> Estimate:
    ap = arch_params(info)
    n = ap.n_layer
    ngl = max(0, min(ngl, n + 1))
    est = Estimate(n_ctx=n_ctx, ngl=ngl, n_layer=n, kv_type=kv_type, calib=calib, notes=list(ap.notes))
    # llama.cpp: i_gpu_start = n_layer + 1 - ngl -> la 1ª "capa" que sube a GPU es la de
    # salida y después las ÚLTIMAS capas repetidas (ngl=20 -> salida + 19 capas).
    n_off = min(max(ngl - 1, 0), n)
    out_gpu = ngl >= 1
    first_gpu = n - n_off
    on_gpu = [i >= first_gpu for i in range(n)]

    # --- pesos ----------------------------------------------------------------
    for i, b in info.layer_bytes.items():
        if i in ap.skip_layers:
            continue
        if i < n and on_gpu[i]:
            est.weights_gpu += b
        else:
            est.weights_cpu += b
    other = dict(info.other_bytes)
    tok_embd = other.pop("token_embd", 0)
    # embeddings de entrada (incl. per_layer_token_embd de gemma3n) se quedan en CPU
    embd_cpu = tok_embd + sum(other.pop(k) for k in [k for k in other if "token_embd" in k])
    out_w = other.pop("output", 0)
    misc = sum(other.values())           # output_norm, altup, rope_freqs…
    est.weights_cpu += embd_cpu
    if out_gpu:
        # si la salida está atada a los embeddings, llama.cpp la duplica en GPU
        est.weights_gpu += (out_w or tok_embd) + misc
    else:
        est.weights_cpu += out_w + misc

    # --- caché KV -------------------------------------------------------------
    bpe = KV_BYTES.get(kv_type, 2.0)
    ctx_pad = math.ceil(n_ctx / KV_PAD) * KV_PAD
    swa_cells = ctx_pad if swa_full else min(ctx_pad, math.ceil((ap.n_swa + n_ubatch) / KV_PAD) * KV_PAD)
    for i in range(n):
        if not ap.has_kv[i]:
            continue
        cells = swa_cells if ap.is_swa[i] else ctx_pad
        b = int(cells * ap.n_head_kv[i] * (ap.k_len[i] + ap.v_len[i]) * bpe)
        if on_gpu[i]:
            est.kv_gpu += b
        else:
            est.kv_cpu += b
        # estado recurrente
    for i in range(n):
        if ap.is_recurrent[i] and i not in ap.skip_layers:
            b = ap.ssm_r_bytes + ap.ssm_s_bytes
            if on_gpu[i]:
                est.rs_gpu += b
            else:
                est.rs_cpu += b
    if any(ap.is_swa) and not swa_full:
        est.notes.append(T(f"caché SWA mínima ({swa_cells} celdas en capas SWA)", f"minimal SWA cache ({swa_cells} cells in SWA layers)"))
    if kv_type != "f16":
        est.notes.append(T(f"caché KV cuantizada {kv_type} (requiere flash-attn)", f"quantised KV cache {kv_type} (requires flash-attn)"))

    # --- buffers de cómputo -----------------------------------------------------
    # medido: el buffer de cómputo ≈ logits del ubatch + unas decenas de MiB
    logits = n_ubatch * 4 * ap.n_vocab
    kq = 0 if flash_attn else 3 * n_ubatch * ctx_pad * max(ap.n_head) * 4
    if out_gpu:
        est.compute_gpu = logits + kq + 48 * MiB
        est.overhead_gpu = GPU_OVERHEAD
        est.compute_cpu = n_ubatch * 4 * ap.n_embd * 8
    else:
        est.compute_cpu = logits + kq + 48 * MiB
        # ngl=0: el backend GPU sigue procesando los lotes del prompt (op offload) y
        # reserva un buffer ≈ logits + matriz de salida (medido: 856 MiB en Qwen2.5-VL-7B)
        est.compute_gpu = logits + (out_w or tok_embd) + 48 * MiB
        est.overhead_gpu = GPU_OVERHEAD
    est.overhead_cpu = CPU_OVERHEAD

    # --- mmproj ---------------------------------------------------------------
    if mmproj is not None:
        est.mmproj_gpu = mmproj.file_size + mmproj_compute(mmproj)
    return est


def mmproj_compute(p: GGUFInfo) -> int:
    """Buffer de cómputo del codificador visual (aprox., depende del tamaño de imagen)."""
    # libmtmd reserva el buffer con una imagen ficticia (1288x1288 en Qwen-VL):
    # medido 267 MiB para Qwen2-VL (dim 1280, patch 14) -> ~6.5 floats por patch·dim
    dim = int(p.meta.get("clip.vision.embedding_length") or 1024)
    patch = int(p.meta.get("clip.vision.patch_size") or 14)
    img = int(p.meta.get("clip.vision.image_size") or 448)
    ptype = (p.projector_type or "").lower()
    side = 1288 if ptype.startswith(("qwen", "glm", "kimi", "pixtral", "youtu")) else max(img, 448)
    n_patch = (side // patch) ** 2
    return int(n_patch * dim * 4 * 6.5 + 16 * MiB)


# ---------------------------------------------------------------------------
# Auto-ajuste
# ---------------------------------------------------------------------------

CTX_STEPS = [2048, 4096, 6144, 8192, 12288, 16384, 24576, 32768, 49152, 65536,
             98304, 131072, 196608, 262144]


@dataclass
class Fit:
    ngl: int
    n_ctx: int
    est: Estimate
    fits_vram: bool
    fits_ram: bool
    reason: str = ""


def budget(sysmem: SysMem, margin_mb: int, reclaim: int = 0) -> tuple[int, int]:
    """(VRAM, RAM) disponibles para el modelo. reclaim = memoria que liberará el modelo actual."""
    vram = sysmem.vram_free + reclaim - margin_mb * MiB
    ram = sysmem.ram_avail + (reclaim // 4) - 512 * MiB
    return max(vram, 0), max(ram, 0)


def auto_fit(info: GGUFInfo, vram_budget: int, ram_budget: int, *, kv_type="f16", swa_full=False,
             flash_attn=True, mmproj: GGUFInfo | None = None, ctx: int | None = None,
             ngl: int | None = None, ctx_cap: int = 32768, min_ctx: int = 4096, calib: float = 1.0) -> Fit:
    """
    Elige los valores "auto": primero el máximo de capas en GPU (velocidad) con un
    contexto mínimo razonable y después el mayor n_ctx que quepa.
    ctx / ngl fijados por el usuario se respetan.
    """
    ctx_max = info.context_length or 4096
    nmax = max_gpu_layers(info)

    def est(c, g):
        return estimate(info, c, g, kv_type, swa_full, flash_attn, mmproj, calib=calib)

    if ngl is None:
        probe_ctx = min(ctx or min_ctx, ctx_max)
        ngl = 0
        for g in range(nmax, -1, -1):
            if est(probe_ctx, g).vram <= vram_budget:
                ngl = g
                break
    if ctx is None:
        cap = min(ctx_cap, ctx_max)
        steps = [c for c in CTX_STEPS if c <= cap] or [min(2048, ctx_max)]
        ctx = steps[0]
        for c in steps:
            e = est(c, ngl)
            if e.vram <= vram_budget and e.ram <= ram_budget:
                ctx = c
    e = est(ctx, ngl)
    return Fit(ngl, ctx, e, e.vram <= vram_budget, e.ram <= ram_budget)


def suggestions(info: GGUFInfo, vram_budget: int, ram_budget: int, n_ctx: int, ngl: int, **kw) -> list[str]:
    """Si no cabe: n_gpu_layers máximo para este ctx y n_ctx máximo para estas capas."""
    out = []
    nmax = max_gpu_layers(info)
    best_g = next((g for g in range(min(ngl, nmax), -1, -1)
                   if estimate(info, n_ctx, g, **kw).vram <= vram_budget), None)
    if best_g is not None and best_g < ngl:
        out.append(T(f"/gpu {best_g}  → cabe con ctx={n_ctx} (más lento: {nmax - best_g} capas en CPU)", f"/gpu {best_g}  → fits with ctx={n_ctx} (slower: {nmax - best_g} layers on the CPU)"))
    ctx_ok = None
    for c in sorted({c for c in CTX_STEPS if c < n_ctx} | {2048}, reverse=True):
        if estimate(info, c, ngl, **kw).vram <= vram_budget:
            ctx_ok = c
            break
    if ctx_ok:
        out.append(T(f"/ctx {ctx_ok}  → cabe con las {min(ngl, nmax)} capas en GPU", f"/ctx {ctx_ok}  → fits with the {min(ngl, nmax)} layers on the GPU"))
    if kw.get("kv_type", "f16") == "f16":
        e8 = estimate(info, n_ctx, ngl, **{**kw, "kv_type": "q8_0"})
        if e8.vram <= vram_budget:
            out.append(T("/kv q8_0  → caché KV a la mitad, cabe sin tocar ctx ni capas", "/kv q8_0  → half-size KV cache, fits without changing ctx or layers"))
    if not out:
        out.append(T("/gpu auto  → elegir capas y contexto automáticamente", "/gpu auto  → pick layers and context automatically"))
    est_ram = estimate(info, n_ctx, ngl, **kw).ram
    if est_ram > ram_budget:
        out.append(T("RAM justa: cierra programas o baja /ctx; con mmap Windows paginará al disco (muy lento)", "Tight on RAM: close programs or lower /ctx; with mmap Windows will page to disk (very slow)"))
    return out


# ---------------------------------------------------------------------------
# Medición real (logs de llama.cpp)
# ---------------------------------------------------------------------------

@dataclass
class RealUsage:
    gpu: int = 0
    cpu: int = 0
    parts: dict[str, float] = field(default_factory=dict)   # "Vulkan0 model" -> MiB
    vram_delta: int | None = None     # VRAM libre antes − después de cargar (None si no se pudo medir)


def parse_buffer_logs(lines: list[str]) -> RealUsage:
    import re
    ru = RealUsage()
    rx = re.compile(r"(\w[\w-]*?)\s+(model|KV|RS|compute|output)\s+buffer size\s*=\s*([\d.]+)\s*MiB")
    clip_rx = re.compile(r"(?:clip_model_loader|load_hparams|clip_ctx).*?model size:\s*([\d.]+)\s*MiB", re.I)
    clip_comp = re.compile(r"(?:compute (?:buffer|allocated memory)).*?([\d.]+)\s*MiB", re.I)
    for ln in lines:
        m = rx.search(ln)
        if m:
            dev, kind, mib = m.group(1), m.group(2), float(m.group(3))
            if ln.lstrip().startswith("~"):  # mensajes de verificación al liberar
                continue
            if ln.startswith("reserve_compute_meta"):   # codificador de visión (clip)
                dev = "mmproj" if dev.lower().startswith(("vulkan", "cuda", "rocm", "metal", "hip", "sycl")) else "mmproj-cpu"
            key = f"{dev} {kind}"
            ru.parts[key] = ru.parts.get(key, 0.0) + mib
            continue
        m = clip_rx.search(ln)
        if m:
            ru.parts["mmproj model"] = ru.parts.get("mmproj model", 0.0) + float(m.group(1))
            continue
        if "clip" in ln.lower() or "mtmd" in ln.lower():
            m = clip_comp.search(ln)
            if m:
                ru.parts["mmproj compute"] = max(ru.parts.get("mmproj compute", 0.0), float(m.group(1)))
    for k, mib in ru.parts.items():
        dev = k.split()[0].lower()
        b = int(mib * MiB)
        if dev.startswith(("vulkan", "cuda", "rocm", "metal", "hip", "sycl")) and "host" not in dev or dev == "mmproj":
            ru.gpu += b
        else:
            ru.cpu += b
    return ru


@dataclass
class DeviceLog:
    """Lo que llama.cpp dice en su log sobre dónde ha puesto el modelo."""
    devices: list[tuple[str, bool]] = field(default_factory=list)   # (nombre, integrada/UMA) que ve el backend GPU
    used: str = ""                     # dispositivo que usa el modelo
    free_mib: int | None = None        # VRAM libre según llama.cpp al cargar
    offloaded: int | None = None       # capas subidas a la GPU (incluida la de salida)
    total: int | None = None

    @property
    def gpu_backend(self) -> bool:
        return bool(self.devices)


def parse_device_logs(lines: list[str]) -> DeviceLog:
    import re
    d = DeviceLog()
    for ln in lines:
        m = re.search(r"ggml_vulkan:\s*\d+\s*=\s*(.+?)\s*\|\s*uma:\s*(\d)", ln)
        if m:
            d.devices.append((m.group(1).strip(), m.group(2) == "1"))
            continue
        m = re.search(r"Device\s+\d+:\s*(.+?),\s*compute capability", ln)          # CUDA
        if m:
            d.devices.append((m.group(1).strip(), False))
            continue
        m = re.search(r"using device (\S+) \(([^)]*)\).*?-\s*(\d+)\s*MiB free", ln)
        if m:
            d.used, d.free_mib = f"{m.group(1)} ({m.group(2)})", int(m.group(3))
            continue
        m = re.search(r"offloaded (\d+)/(\d+) layers to GPU", ln)
        if m:
            d.offloaded, d.total = int(m.group(1)), int(m.group(2))
    return d


def fmt_bytes(b: float, unit: str = "auto") -> str:
    if unit == "G" or (unit == "auto" and abs(b) >= GiB):
        return f"{b / GiB:.2f} GB"
    return f"{b / MiB:.0f} MB"
