"""
Asistente de instalación interactivo (local-ai --setup / setup-local-ai.bat).

1. Lee el hardware (CPU, GHz, RAM y MT/s, canales, GPU y VRAM) o permite corregirlo o
   simular otro equipo (de 8 GB sin GPU hasta 512 GB+).
2. Explica las categorías y los niveles Básico/Moderado/Extensivo con el modelo que
   corresponde a TU equipo, cuánto ocupa, dónde corre y a qué velocidad aproximada,
   y cuándo conviene más una IA en la nube.
3. Guarda el plan, descarga lo elegido (local-ai --download) y deja la configuración
   ajustada (perfiles para /modo, modelo principal y lector visual).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import hardware as H
from . import render
from .config import APP_DIR, MODELS_DIR, load_settings, save_settings
from .model_catalog import CATEGORIES, M, TIERS, CatModel, category
from .render import console, error, ok, say, warn

PLAN_FILE = APP_DIR / "setup_plan.json"
HF = "https://huggingface.co"

PRESETS = [
    ("8 GB de RAM, sin GPU (portátil básico)", dict(ram_gb=8, ram_mts=3200, ram_channels=1, vram_gb=0, gpu_bw=0, gpu_name="")),
    ("16 GB de RAM, sin GPU", dict(ram_gb=16, ram_mts=3200, ram_channels=2, vram_gb=0, gpu_bw=0, gpu_name="")),
    ("16 GB + GPU 8 GB (RTX 4060)", dict(ram_gb=16, ram_mts=4800, ram_channels=2, vram_gb=8, gpu_bw=272, gpu_name="RTX 4060")),
    ("32 GB + GPU 12 GB (RTX 3060)", dict(ram_gb=32, ram_mts=3200, ram_channels=2, vram_gb=12, gpu_bw=360, gpu_name="RTX 3060")),
    ("32 GB + GPU 16 GB (RTX 4070 Ti Super)", dict(ram_gb=32, ram_mts=6000, ram_channels=2, vram_gb=16, gpu_bw=672, gpu_name="RTX 4070 Ti Super")),
    ("64 GB + GPU 24 GB (RTX 4090)", dict(ram_gb=64, ram_mts=6000, ram_channels=2, vram_gb=24, gpu_bw=1008, gpu_name="RTX 4090")),
    ("128 GB + GPU 32 GB (RTX 5090)", dict(ram_gb=128, ram_mts=6000, ram_channels=2, vram_gb=32, gpu_bw=1792, gpu_name="RTX 5090")),
    ("128 GB memoria unificada (Ryzen AI Max / Mac)", dict(ram_gb=32, ram_mts=8000, ram_channels=4, vram_gb=96, gpu_bw=256, gpu_name="iGPU memoria unificada")),
    ("256 GB + 2× GPU 48 GB (estación de trabajo)", dict(ram_gb=256, ram_mts=4800, ram_channels=8, vram_gb=96, gpu_bw=960, gpu_name="2× RTX 6000 Ada")),
    ("512 GB+ servidor (mini centro de datos)", dict(ram_gb=512, ram_mts=4800, ram_channels=12, vram_gb=160, gpu_bw=2000, gpu_name="2× A100 80 GB")),
]


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def installed_path(m: CatModel) -> Path | None:
    """¿Ya está en models/? (misma familia y tamaño aunque sea otra cuantización)."""
    if not MODELS_DIR.exists():
        return None
    stem = _norm(m.repo.split("/")[-1].replace("-GGUF", "").replace("-gguf", "").replace("-it", ""))
    for f in MODELS_DIR.rglob("*.gguf"):
        n = _norm(f.name)
        if "mmproj" in n:
            continue
        if m.pattern.endswith(".gguf") and f.name == m.pattern:
            return f
        if stem and stem in n:
            return f
    return None


def mmproj_installed(m: CatModel) -> bool:
    d = MODELS_DIR / "mmproj"
    if not m.mmproj or not d.exists():
        return True
    return any(f.name == m.mmproj or f.name.startswith(f"mmproj-{m.key}-") for f in d.glob("*.gguf"))


def _ask(prompt: str, default=None, cast=str):
    try:
        raw = console.input(f"{prompt} [dim]({default})[/] ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    if not raw:
        return default
    try:
        return cast(raw.replace(",", "."))
    except ValueError:
        warn("Valor no válido; se mantiene el anterior.")
        return default


def _select(title, choices, default=0):
    from .inputbox import Choice, select
    return select(title, [Choice(v, l, m) for v, l, m in choices], render.current, default)


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------

def show_hardware(hw: H.Hardware):
    tb = Table(box=None, padding=(0, 2), show_header=False)
    tb.add_column(style="dim")
    tb.add_column(style="bold")
    tb.add_column(style="dim")
    tb.add_row("CPU", hw.cpu_name, f"{hw.cpu_cores} núcleos / {hw.cpu_threads} hilos · {hw.cpu_ghz:.1f} GHz")
    tb.add_row("RAM", f"{hw.ram_gb:.0f} GB {hw.ram_type}-{hw.ram_mts or '?'}",
               f"{hw.ram_channels} canal(es) → ~{hw.ram_bw:.0f} GB/s")
    if hw.has_gpu:
        tb.add_row("GPU", f"{hw.gpu_name} · {hw.vram_gb:.0f} GB", f"~{hw.gpu_bw:.0f} GB/s")
    else:
        tb.add_row("GPU", "sin GPU dedicada (o no detectada)", "todo correrá en la CPU")
    tb.add_row("Disco libre", f"{hw.disk_free_gb:.0f} GB", str(MODELS_DIR))
    console.print(Panel(tb, title="Tu equipo", title_align="left", border_style="border"))
    say("[dim]· La velocidad al escribir depende sobre todo del [bold]ancho de banda de memoria[/]: lo que cabe en la "
        "VRAM va unas 5–10 veces más rápido que en la RAM.[/]")
    say("[dim]· Los GHz y núcleos de la CPU influyen al [bold]leer[/] textos largos (procesar el prompt) cuando el "
        "modelo no cabe en la GPU; los MT/s y canales de la RAM, al [bold]escribir[/].[/]")
    say(f"[dim]· Clase: [/][accent]{H.tier_of(hw)}[/]")


def edit_hardware(hw: H.Hardware) -> H.Hardware:
    while True:
        field = _select("¿Qué dato quieres corregir?", [
            ("ram_gb", f"RAM total: {hw.ram_gb:.0f} GB", ""),
            ("ram_mts", f"Velocidad RAM: {hw.ram_mts} MT/s", "DDR4 típica 2666–3600 · DDR5 4800–8000"),
            ("ram_channels", f"Canales de RAM: {hw.ram_channels}", "1 módulo = 1 canal · 2–4 módulos en escritorio = 2"),
            ("gpu", f"GPU: {hw.gpu_name or 'ninguna'} ({hw.vram_gb:.0f} GB, {hw.gpu_bw:.0f} GB/s)", ""),
            ("cpu", f"CPU: {hw.cpu_cores} núcleos, {hw.cpu_ghz:.1f} GHz", ""),
            (None, "✓ Listo", ""),
        ], default=5)
        if field is None:
            return hw
        if field == "ram_gb":
            hw.ram_gb = _ask("RAM total en GB", hw.ram_gb, float)
        elif field == "ram_mts":
            hw.ram_mts = _ask("MT/s de la RAM", hw.ram_mts, int)
        elif field == "ram_channels":
            hw.ram_channels = _ask("Canales", hw.ram_channels, int)
        elif field == "cpu":
            hw.cpu_cores = _ask("Núcleos", hw.cpu_cores, int)
            hw.cpu_ghz = _ask("GHz máximos", hw.cpu_ghz, float)
        elif field == "gpu":
            names = sorted(H.GPU_BANDWIDTH)
            g = _select("Elige tu GPU", [("none", "Sin GPU", "")] + [("other", "Otra (introducir datos)", "")]
                        + [(n, n.upper(), f"{H.GPU_BANDWIDTH[n]} GB/s") for n in names])
            if g == "none":
                hw.gpu_name, hw.vram_gb, hw.gpu_bw = "", 0, 0
            elif g == "other":
                hw.gpu_name = _ask("Nombre de la GPU", hw.gpu_name or "GPU")
                hw.vram_gb = _ask("VRAM en GB", hw.vram_gb, float)
                hw.gpu_bw = _ask("Ancho de banda en GB/s (ficha técnica)", hw.gpu_bw or 300, float)
            elif g:
                hw.gpu_name = g.upper()
                hw.gpu_bw = float(H.GPU_BANDWIDTH[g])
                hw.vram_gb = _ask("VRAM en GB", hw.vram_gb or 8, float)


def choose_hardware() -> H.Hardware:
    with console.status("[dim]Leyendo tu hardware…[/]", spinner="dots"):
        hw = H.detect()
    show_hardware(hw)
    act = _select("¿Los datos son correctos?", [
        ("ok", "Sí, usar estos datos", ""),
        ("edit", "Corregir algún dato", "p. ej. MT/s o canales si no se detectaron bien"),
        ("sim", "Simular otro equipo", "para orientar a otra persona o planear una compra"),
    ])
    if act == "edit":
        hw = edit_hardware(hw)
        show_hardware(hw)
    elif act == "sim":
        p = _select("Equipo a simular", [(i, lbl, "") for i, (lbl, _) in enumerate(PRESETS)])
        if p is not None:
            base = H.Hardware(cpu_name="(simulado)", cpu_cores=8, cpu_threads=16, cpu_ghz=4.0, ram_type="DDR",
                              disk_free_gb=hw.disk_free_gb)
            for k, v in PRESETS[p][1].items():
                setattr(base, k, v)
            hw = base
            show_hardware(hw)
    return hw


# ---------------------------------------------------------------------------
# Recomendaciones por categoría
# ---------------------------------------------------------------------------

def evaluate(hw: H.Hardware, m: CatModel, vision: bool) -> H.Fit:
    return H.estimate(hw, m.size_gb, m.params_b, m.active_b, extra_gb=m.mmproj_gb if vision else 0.0)


def pick_tiers(hw: H.Hardware, cat) -> dict[str, tuple[CatModel, H.Fit] | None]:
    ev = [(M[k], evaluate(hw, M[k], cat.needs_vision)) for k in cat.candidates]   # de peor a mejor
    fits = [(m, f) for m, f in ev if f.fits]

    def best(min_tps):
        c = [(m, f) for m, f in fits if f.tps >= min_tps]
        return c[-1] if c else None

    basico = best(25) or (fits[0] if fits else None)
    moderado = best(8) or best(4) or basico
    extensivo = best(1.0) or moderado
    return {"basico": basico, "moderado": moderado, "extensivo": extensivo}


def show_category(hw: H.Hardware, cat, tiers) -> list:
    console.print()
    console.print(Text(cat.name, style="accent"))
    say(f"[dim]{cat.desc}[/]")
    tb = Table(border_style="border", padding=(0, 1))
    for c in ("Nivel", "Modelo", "Tamaño", "Corre en", "Velocidad aprox.", "Estado"):
        tb.add_column(c)
    for tkey, (tname, _) in TIERS.items():
        pick = tiers.get(tkey)
        if pick is None:
            tb.add_row(tname, "[bad]nada cabe[/]", "", "", "", "")
            continue
        m, f = pick
        inst = installed_path(m)
        size = m.size_gb + (m.mmproj_gb if cat.needs_vision else 0)
        state = "[ok]instalado ✓[/]" if inst else f"descargar {size:.1f} GB"
        tb.add_row(tname, f"{m.name}\n[dim]{m.note}[/]", f"{size:.1f} GB",
                   f"{f.where}" + (f" ({f.gpu_frac:.0%} GPU)" if f.where == "GPU+RAM" else ""),
                   f"{f.tps_low:.0f}–{f.tps_high:.0f} t/s\n[dim]{H.speed_words(f.tps)}[/]", state)
    console.print(tb)
    for tkey, (tname, tdesc) in TIERS.items():
        say(f"  [accent]{tname}[/][dim]: {tdesc}[/]")
    console.print(Panel(Text(cat.cloud), title="¿Local o nube?", title_align="left", border_style="dim"))
    opts = [(tkey, f"{TIERS[tkey][0]}: {tiers[tkey][0].name}", "") for tkey in TIERS if tiers.get(tkey)]
    chosen = {v[0].key for v in tiers.values() if v}
    for k in cat.candidates:          # modelos de esta categoría que ya tienes: sin descargar nada
        m = M[k]
        if k in chosen or not installed_path(m):
            continue
        f = evaluate(hw, m, cat.needs_vision)
        if f.fits:
            opts.append((f"inst:{k}", f"Usar el que ya tienes: {m.name}",
                         f"{f.tps_low:.0f}–{f.tps_high:.0f} t/s · sin descarga"))
    return opts + [(None, "Omitir esta categoría", "")]


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------

def _get_json(url: str):
    import requests
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def resolve_files(m: CatModel, with_mmproj: bool) -> list[tuple[str, int, Path]]:
    """(ruta en el repo, bytes, destino local) de los archivos a descargar."""
    tree = _get_json(f"{HF}/api/models/{m.repo}/tree/main?recursive=1")
    out = []
    sub = "vision" if m.kind == "vision" else "llm"
    for f in tree:
        p = f.get("path", "")
        if not p.endswith(".gguf"):
            continue
        size = int(f.get("size") or (f.get("lfs") or {}).get("size") or 0)
        name = p.split("/")[-1]
        if "mmproj" in name.lower():
            if with_mmproj and m.mmproj and name == m.mmproj:
                generic = re.fullmatch(r"mmproj-(F16|BF16|F32|Q8_0|model-f16)\.gguf", name)
                local = f"mmproj-{m.key}-{name[7:]}" if generic else name
                out.append((p, size, MODELS_DIR / "mmproj" / local))
            continue
        if m.pattern.endswith(".gguf"):
            if name == m.pattern:
                out.append((p, size, MODELS_DIR / sub / name))
        elif re.search(rf"(?<![A-Za-z0-9]){re.escape(m.pattern)}(?![A-Za-z0-9_])", p):
            out.append((p, size, MODELS_DIR / sub / name))
    return out


def download_files(files: list[tuple[str, str, int, Path]]) -> bool:
    """files: (repo, ruta, bytes, destino). Reanuda descargas a medias (.part)."""
    import requests
    from rich.progress import (BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn,
                               TransferSpeedColumn)
    total = sum(s for _, _, s, _ in files)
    with Progress(TextColumn("[accent]{task.description}"), BarColumn(), DownloadColumn(), TransferSpeedColumn(),
                  TimeRemainingColumn(), console=console) as prog:
        overall = prog.add_task("Total", total=total)
        for repo, path, size, dest in files:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and dest.stat().st_size == size:
                prog.advance(overall, size)
                continue
            part = dest.with_suffix(dest.suffix + ".part")
            have = part.stat().st_size if part.exists() else 0
            task = prog.add_task(dest.name[:40], total=size, completed=have)
            prog.advance(overall, have)
            headers = {"Range": f"bytes={have}-"} if have else {}
            for attempt in range(3):
                try:
                    with requests.get(f"{HF}/{repo}/resolve/main/{path}", headers=headers, stream=True,
                                      timeout=(15, 120)) as r:
                        if r.status_code not in (200, 206):
                            raise RuntimeError(f"HTTP {r.status_code}")
                        mode = "ab" if r.status_code == 206 else "wb"
                        if mode == "wb" and have:
                            prog.advance(overall, -have)
                            prog.update(task, completed=0)
                        with open(part, mode) as fh:
                            for chunk in r.iter_content(chunk_size=1 << 20):
                                fh.write(chunk)
                                prog.advance(task, len(chunk))
                                prog.advance(overall, len(chunk))
                    break
                except KeyboardInterrupt:
                    warn(f"Descarga interrumpida; se reanudará con local-ai --download ({part.name})")
                    return False
                except Exception as e:
                    have = part.stat().st_size if part.exists() else 0
                    headers = {"Range": f"bytes={have}-"}
                    if attempt == 2:
                        error(f"No se pudo descargar {dest.name}: {e}")
                        return False
                    time.sleep(3)
            if part.stat().st_size != size:
                error(f"{dest.name}: tamaño inesperado ({part.stat().st_size} de {size} bytes)")
                return False
            part.replace(dest)
            prog.update(task, visible=False)
    return True


# ---------------------------------------------------------------------------
# Plan y configuración
# ---------------------------------------------------------------------------

def apply_plan(plan: dict):
    """Deja la configuración ajustada a lo elegido (perfiles de /modo, modelo principal y lector)."""
    s = load_settings()
    profiles = {}
    for ck, mk in plan["choices"].items():
        m = M[mk]
        p = installed_path(m)
        name = p.name if p else None
        cat = category(ck)
        if ck == "lectura":
            if name:
                s.vision_model = name
            continue
        if name:
            profiles[ck] = {"model": name, **cat.profile}
    s.profiles = profiles
    prim = plan.get("primary")
    if prim and prim in profiles:
        s.last_model = profiles[prim]["model"]
        for k, v in category(prim).profile.items():
            setattr(s, k, v)
    s.ctx, s.gpu, s.vision_mode = "auto", "auto", "lector"
    save_settings(s)
    return s


def run_download(plan: dict | None = None) -> bool:
    plan = plan or (json.loads(PLAN_FILE.read_text(encoding="utf-8")) if PLAN_FILE.exists() else None)
    if not plan:
        error("No hay ningún plan de instalación. Ejecuta primero: local-ai --setup")
        return False
    todo = []
    with console.status("[dim]Consultando Hugging Face…[/]", spinner="dots"):
        for ck, mk in plan["choices"].items():
            m = M[mk]
            vision = category(ck).needs_vision or ck == "lectura"
            have_model = installed_path(m) is not None
            if have_model and (not vision or mmproj_installed(m)):
                continue
            try:
                for path, size, dest in resolve_files(m, vision):
                    if have_model and "mmproj" not in dest.name.lower():
                        continue          # el modelo ya está: solo falta su mmproj
                    todo.append((m.repo, path, size, dest))
            except Exception as e:
                error(f"No se pudo consultar {m.repo}: {e}")
                return False
    seen, files = set(), []
    for t in todo:
        if t[3] not in seen:
            seen.add(t[3])
            files.append(t)
    if files:
        total = sum(f[2] for f in files) / 2 ** 30
        say(f"[accent]Descargando {len(files)} archivo(s), {total:.1f} GB[/] [dim](puedes cortar con Ctrl+C y "
            f"reanudar con local-ai --download)[/]")
        if not download_files(files):
            return False
    else:
        say("[dim]Todo lo elegido ya está instalado.[/]")
    s = apply_plan(plan)
    ok("Configuración aplicada.")
    say(f"  [dim]Modelo principal:[/] {s.last_model} · [dim]lector visual:[/] {s.vision_model}")
    for ck, p in (s.profiles or {}).items():
        say(f"  [dim]/modo {ck}[/] → {p['model']}")
    return True


# ---------------------------------------------------------------------------
# Asistente
# ---------------------------------------------------------------------------

def run_wizard() -> bool:
    render.banner("setup", "[accent]Asistente de instalación[/] [dim]· te recomiendo modelos según tu equipo y para "
                  "qué los quieres, y dejo todo configurado[/]",
                  ["Leo tu hardware (puedes corregirlo o simular otro equipo).",
                   "Eliges para qué quieres la IA; te explico cada nivel con su velocidad aproximada.",
                   "Al final descargo lo elegido y ajusto la configuración (perfiles para [accent]/modo[/])."])
    try:
        import llama_cpp  # noqa: F401
    except Exception:
        warn("llama-cpp-python no está instalado: es el motor que ejecuta los modelos.")
        say("  [dim]CPU o cualquier GPU (Vulkan):[/] pip install llama-cpp-python  [dim](o una wheel precompilada)[/]")
        say("  [dim]NVIDIA (CUDA, más rápido al leer prompts):[/] busca una wheel 'cu12x' para tu versión de Python")
    hw = choose_hardware()
    console.print()
    sel = _multi("¿Para qué quieres usar la IA? (marca con Espacio)",
                 [(c.key, c.name, c.desc[:70] + ("…" if len(c.desc) > 70 else "")) for c in CATEGORIES],
                 {"utilidad", "programacion", "lectura"})
    if not sel:
        say("[dim]Cancelado.[/]")
        return False
    choices: dict[str, str] = {}
    for ck in sel:
        cat = category(ck)
        tiers = pick_tiers(hw, cat)
        opts = show_category(hw, cat, tiers)
        default = next((i for i, o in enumerate(opts) if o[0] == "moderado"), 0)
        t = _select(f"{cat.name}: ¿qué nivel?", opts, default)
        if t and t.startswith("inst:"):
            choices[ck] = t[5:]
        elif t:
            choices[ck] = tiers[t][0].key
    if not choices:
        say("[dim]No elegiste nada.[/]")
        return False
    usable = [c for c in choices if c != "lectura"]
    primary = None
    if usable:
        primary = _select("¿Cuál será tu uso principal? (el modelo que se carga al abrir local-ai)",
                          [(c, category(c).name, M[choices[c]].name) for c in usable]) or usable[0]
    # resumen
    console.print()
    tb = Table(title="Resumen", title_style="accent", border_style="border")
    for c in ("Categoría", "Modelo", "Tamaño", "Estado"):
        tb.add_column(c)
    need = {}
    for ck, mk in choices.items():
        m = M[mk]
        size = m.size_gb + (m.mmproj_gb if category(ck).needs_vision or ck == "lectura" else 0)
        inst = installed_path(m)
        vis = category(ck).needs_vision or ck == "lectura"
        if not inst:
            need[mk] = max(need.get(mk, 0), size)
        elif vis and not mmproj_installed(m):
            need[mk + ":mmproj"] = m.mmproj_gb
        state = ("[ok]instalado[/]" if inst and (not vis or mmproj_installed(m)) else
                 "[mid]falta su mmproj[/]" if inst else "[mid]por descargar[/]")
        tb.add_row(category(ck).name + (" ★" if ck == primary else ""), m.name, f"{size:.1f} GB", state)
    console.print(tb)
    total = sum(need.values())
    say(f"Descarga total: [accent]{total:.1f} GB[/] · disco libre {hw.disk_free_gb:.0f} GB"
        + (f" · [dim]≈{total * 1024 / 50 / 60:.0f} min a 50 MB/s[/]" if total else ""))
    if total > hw.disk_free_gb * 0.95:
        warn("No hay espacio suficiente en el disco para todo lo elegido.")
    plan = {"created": time.strftime("%Y-%m-%d %H:%M"), "hardware": hw.to_json(), "choices": choices,
            "primary": primary}
    APP_DIR.mkdir(parents=True, exist_ok=True)
    PLAN_FILE.write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
    act = _select("¿Qué hacemos?", [
        ("now", "Descargar ahora y aplicar la configuración", f"{total:.1f} GB"),
        ("later", "Solo guardar el plan (descargar luego con: local-ai --download)", ""),
        ("cancel", "Cancelar", ""),
    ])
    if act == "now":
        return run_download(plan)
    if act == "later":
        ok(f"Plan guardado en {PLAN_FILE}. Cuando quieras: local-ai --download")
        return True
    return False


def _multi(title, items, pre):
    from .inputbox import Choice, select_multi
    return select_multi(title, [Choice(v, l, m) for v, l, m in items], render.current, pre)
