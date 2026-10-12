"""/doctor: diagnóstico del entorno, backend, modelos, mmproj y memoria."""

from __future__ import annotations

import importlib.metadata as md
import re
import sys
from pathlib import Path

from rich.panel import Panel
from rich.table import Table

from . import render
from .config import APP_DIR, MODELS_DIR, OLLAMA_HOST, memory_files
from .memory import max_gpu_layers, probe
from .render import console, say

_dll_words: dict[str, set[str]] = {}


def _words_in_dll(path: Path) -> set[str]:
    key = str(path)
    if key not in _dll_words:
        try:
            data = path.read_bytes()
            _dll_words[key] = {m.decode() for m in re.findall(rb"[a-z0-9_.]{3,40}", data)}
        except Exception:
            _dll_words[key] = set()
    return _dll_words[key]


def _ver(pkg: str) -> str | None:
    try:
        return md.version(pkg)
    except Exception:
        return None


def run_doctor(app):
    ok_s, bad_s, warn_s = "[ok]✔[/]", "[bad]✖[/]", "[mid]⚠[/]"
    from . import gpu as gpumod
    sm = probe()

    # ---------------------------------------------------------- dependencias
    tb = Table(box=None, padding=(0, 2), show_header=False)
    tb.add_column()
    tb.add_column(style="accent")
    tb.add_column(style="dim")
    tb.add_row(ok_s, "Python", f"{sys.version.split()[0]} · {sys.executable}")
    for pkg, need, why in [("llama_cpp_python", True, "motor GGUF"), ("rich", True, "interfaz"),
                           ("prompt_toolkit", True, "entrada"), ("pymupdf", False, "PDFs"),
                           ("pillow", False, "imágenes"), ("psutil", False, "RAM"),
                           ("nvidia-ml-py", False, "VRAM (NVML)"), ("requests", False, "Ollama")]:
        v = _ver(pkg)
        if pkg == "nvidia-ml-py" and not v and sm.vendor in ("amd", "intel"):
            tb.add_row(ok_s, pkg, f"no hace falta con GPU {gpumod.VENDOR_NAME[sm.vendor]} (se usa el registro y el contador de Windows)")
            continue
        if pkg == "llama_cpp_python" and not v:
            summary, steps = gpumod.engine_advice(sm.vendor)
            tb.add_row(bad_s, pkg, f"NO instalado · {why} → {summary}")
            for st in steps:
                tb.add_row("", "", f"  · {st}")
            continue
        tb.add_row(ok_s if v else (bad_s if need else warn_s), pkg, f"{v or 'NO instalado'} · {why}"
                   + ("" if v else f" → pip install {pkg}"))
    console.print(Panel(tb, title="Dependencias", title_align="left", border_style="border"))

    # ---------------------------------------------------------- backend
    tb = Table(box=None, padding=(0, 2), show_header=False)
    tb.add_column()
    tb.add_column(style="accent")
    tb.add_column()
    llama_dll = mtmd_dll = None
    try:
        from .engine import llama
        L = llama()
        lib = Path(L.__file__).parent / "lib"
        dlls = {p.name.lower() for p in lib.glob("*.dll")} | {p.name.lower() for p in lib.glob("*.so")}
        backends = [n for n, k in (("CUDA", "ggml-cuda"), ("Vulkan", "ggml-vulkan"), ("HIP/ROCm", "ggml-hip"),
                                   ("SYCL", "ggml-sycl"), ("Metal", "ggml-metal")) if any(k in d for d in dlls)]
        gpu_ok = bool(L.llama_supports_gpu_offload())
        tb.add_row(ok_s if gpu_ok else bad_s, "llama-cpp-python", f"{L.__version__} · backend GPU: "
                   f"{', '.join(backends) or 'ninguno (solo CPU)'}")
        if not gpu_ok and sm.vram_total:
            summary, steps = gpumod.engine_advice(sm.vendor)
            tb.add_row(bad_s, "Aceleración GPU", f"tienes {sm.gpu_name}, pero este llama-cpp-python es solo CPU: "
                       "los modelos no usarán la GPU. " + summary)
            for st in steps:
                tb.add_row("", "", f"  · {st}")
        if "CUDA" not in backends and "Vulkan" in backends and sm.vendor == "nvidia":
            tb.add_row(warn_s, "CUDA", "No es un build CUDA (es Vulkan). Funciona, pero en NVIDIA CUDA suele ser "
                                       "más rápido al procesar prompts/imágenes y no tiene el «calentamiento» de "
                                       "shaders de Vulkan en los primeros cientos de tokens.")
        llama_dll = next(iter(lib.glob("llama.dll")), None)
        mtmd_dll = next(iter(lib.glob("mtmd.dll")), None)
        tb.add_row(ok_s if mtmd_dll else bad_s, "libmtmd (visión)", "presente" if mtmd_dll else "falta mtmd.dll: sin visión")
    except Exception as e:
        tb.add_row(bad_s, "llama-cpp-python", f"no se pudo importar: {e}")
    if sm.vram_total:
        libre = ("libre ≈" if sm.free_estimated else "libre") + f" {render.gb(sm.vram_free)} GB"
        tb.add_row(ok_s, "GPU", f"{sm.gpu_name} · {render.gb(sm.vram_total)} GB · {libre} ({sm.source})")
        used_others = sm.vram_used - app._current_vram()
        if used_others > 1.5 * 1024 ** 3 and not sm.free_estimated:
            tb.add_row(warn_s, "VRAM ocupada", f"{render.gb(used_others)} GB usados por otros programas "
                       "(juegos, navegador, fondos animados…): restan VRAM y rendimiento")
    else:
        tb.add_row(warn_s, "GPU", "no se detectó GPU dedicada (NVML, nvidia-smi ni registro de Windows): los modelos irán en la CPU")
    tb.add_row(ok_s if sm.ram_total else warn_s, "RAM", f"{render.gb(sm.ram_total)} GB · disponible {render.gb(sm.ram_avail)} GB")
    try:
        import requests
        r = requests.get(f"{OLLAMA_HOST}/api/version", timeout=1.5)
        tb.add_row(ok_s, "Ollama", f"{OLLAMA_HOST} · v{r.json().get('version', '?')}")
    except Exception:
        tb.add_row(warn_s, "Ollama", f"no responde en {OLLAMA_HOST} (opcional: 'ollama serve')")
    console.print(Panel(tb, title="Backend y hardware", title_align="left", border_style="border"))

    # ---------------------------------------------------------- modelos
    arch_words = _words_in_dll(llama_dll) if llama_dll else set()
    proj_words = _words_in_dll(mtmd_dll) if mtmd_dll else set()
    tb = Table(border_style="border", padding=(0, 1))
    for c in ("Modelo", "Arquitectura", "Soportada", "Visión", "mmproj", "Ctx máx"):
        tb.add_column(c)
    missing = []
    for m in app.catalog.models:
        if m.kind != "gguf":
            continue
        sup = ("[ok]sí[/]" if m.arch in arch_words else "[bad]no[/]") if arch_words else "?"
        if m.vision == "si":
            pt = m.mmproj.projector_type or "?"
            psup = (pt in proj_words) if proj_words else True
            vis = "[ok]👁 sí[/]" if psup else f"[bad]proyector {pt} no soportado[/]"
            mp = f"{Path(m.mmproj.path).name} ({pt})"
        elif m.vision == "falta_mmproj":
            vis, mp = "[mid]⚠ falta mmproj[/]", "[dim]—[/]"
            missing.append(m)
        else:
            vis, mp = "[dim]texto[/]", "[dim]—[/]"
        tb.add_row(m.name, m.arch, sup, vis, mp, render.human_tokens(m.ctx_max))
    console.print(Panel(tb, title=f"Modelos en {MODELS_DIR}", title_align="left", border_style="border"))
    for name, err in app.catalog.errors:
        say(f"  {bad_s} {name}: {err}")

    if app.catalog.projectors:
        tb = Table(box=None, padding=(0, 2))
        for c in ("mmproj", "projector_type", "projection_dim", "Soportado", "Emparejado con"):
            tb.add_column(c)
        for p in app.catalog.projectors:
            pt = p.projector_type or "?"
            tb.add_row(Path(p.path).name, pt, str(p.projection_dim),
                       ("[ok]sí[/]" if pt in proj_words else "[bad]no[/]") if proj_words else "?",
                       ", ".join(app.catalog.users_of(p)) or "[mid]ninguno[/]")
        console.print(Panel(tb, title="Proyectores (mmproj)", title_align="left", border_style="border"))

    if missing:
        tb = Table(box=None, padding=(0, 2), expand=True)
        for c in ("Modelo", "Repositorio de Hugging Face", "Archivo", "Notas"):
            tb.add_column(c, overflow="fold", ratio=1 if c != "Notas" else 2)
        for m in missing:
            h = m.mmproj_hint()
            if h:
                tb.add_row(m.name, f"huggingface.co/{h[0]}", f"[accent]{h[1]}[/]", f"[dim]{h[2]}[/]")
            else:
                tb.add_row(m.name, "[dim]desconocido[/]", "", f"[dim]busca '<modelo> mmproj gguf' con projection_dim={m.info.n_embd}[/]")
        console.print(Panel(tb, title="mmproj que faltan (descárgalos a models/mmproj/; no se descarga nada automáticamente)",
                            title_align="left", border_style="mid"))

    # ---------------------------------------------------------- memoria actual
    if app.engine is not None and app.plan is not None:
        say(f"\n[accent]Modelo cargado:[/] {app.engine.name} · ctx {app.n_ctx} · gpu {app.plan.ngl}/"
            f"{max_gpu_layers(app.entry.info)} · VRAM real {render.gb(app.engine.real.gpu)} GB "
            f"(est. {render.gb(app.plan.est.vram - app.plan.est.overhead_gpu)} GB)")
    mf = memory_files()
    say(f"[dim]Config: {APP_DIR} · memoria: {', '.join(map(str, mf)) or 'ninguna'}[/]")
