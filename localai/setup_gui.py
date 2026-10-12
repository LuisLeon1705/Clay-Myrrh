"""
Asistente de instalación visual: una página web local (solo en 127.0.0.1) que se abre
en el navegador. Reutiliza el detector de hardware, el catálogo y el estimador del
asistente de consola; aquí solo hay la API y la descarga con progreso.

    local-ai --setup          (lo abre setup-local-ai.bat)
    local-ai --setup-cli      (versión de consola, por si no hay navegador)
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import webbrowser
from dataclasses import fields as dc_fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import hardware as H
from .config import MODELS_DIR
from .model_catalog import CATEGORIES, M, TIERS, category
from .setup_wizard import (PLAN_FILE, PRESETS, apply_plan, evaluate, installed_path, mmproj_installed, pick_tiers,
                           resolve_files)

UI_DIR = Path(__file__).parent / "setup_ui"
ICONS = {"utilidad": "🧰", "narracion": "✍️", "programacion": "💻", "diseno": "🎨", "cad": "📐", "lectura": "📄",
         "multi": "🧩"}
HF = "https://huggingface.co"


def _hw_from(d: dict | None, base: H.Hardware) -> H.Hardware:
    hw = H.Hardware(**{f.name: getattr(base, f.name) for f in dc_fields(H.Hardware)})
    for k, v in (d or {}).items():
        if hasattr(hw, k) and k != "detected":
            try:
                setattr(hw, k, type(getattr(hw, k))(v))
            except (TypeError, ValueError):
                pass
    return hw


def _fit_json(m, f: H.Fit, vision: bool) -> dict:
    size = m.size_gb + (m.mmproj_gb if vision else 0)
    return {"key": m.key, "name": m.name, "note": m.note, "size_gb": round(size, 2), "params_b": m.params_b,
            "active_b": m.active_b, "moe": bool(m.active_b), "fits": f.fits, "where": f.where,
            "gpu_frac": round(f.gpu_frac, 3), "tps_low": f.tps_low, "tps_high": f.tps_high, "tps": round(f.tps, 1),
            "speed_words": H.speed_words(f.tps), "mem_gb": f.mem_needed,
            "installed": installed_path(m) is not None,
            "mmproj_missing": bool(vision and installed_path(m) is not None and not mmproj_installed(m))}


class Setup:
    def __init__(self):
        self.token = secrets.token_urlsafe(18)
        self.detected = H.detect()
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.progress = {"state": "idle", "files": [], "total": 0, "done": 0, "speed": 0.0, "eta": None, "message": ""}
        self.last_plan: dict | None = None
        self._demo: Path | None = None
        self.last_ping = 0.0          # la página avisa cada pocos segundos; si deja de hacerlo, se cerró la pestaña

    def demo_dir(self) -> Path:
        """Página de demostración de los componentes (se genera la primera vez que se pide)."""
        with self.lock:
            if self._demo is None:
                import tempfile
                from . import webkit
                d = Path(tempfile.mkdtemp(prefix="lai_demo_"))
                webkit.build_demo(d)
                self._demo = d
            return self._demo

    # ------------------------------------------------------------------ API
    def state(self) -> dict:
        try:
            import llama_cpp  # noqa: F401
            engine = True
        except Exception:
            engine = False
        plan = None
        if PLAN_FILE.exists():
            try:
                plan = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
            except Exception:
                plan = None
        return {
            "hardware": self.detected.to_json(), "ram_bw": self.detected.ram_bw,
            "presets": [{"label": l, "hw": p} for l, p in PRESETS],
            "gpus": sorted(([n.upper(), bw] for n, bw in H.GPU_BANDWIDTH.items()), key=lambda x: x[0]),
            "categories": [{"key": c.key, "name": c.name, "desc": c.desc, "icon": ICONS.get(c.key, "•"),
                            "needs_vision": c.needs_vision, "cloud": c.cloud} for c in CATEGORIES],
            "tiers": {k: {"name": v[0], "desc": v[1]} for k, v in TIERS.items()},
            "engine_ok": engine, "models_dir": str(MODELS_DIR), "plan": plan,
        }

    def recommend(self, body: dict) -> dict:
        hw = _hw_from(body.get("hw"), self.detected)
        out = {}
        for c in CATEGORIES:
            vision = c.needs_vision
            tiers = pick_tiers(hw, c)
            chosen = {v[0].key for v in tiers.values() if v}
            alts = []
            for k in c.candidates:
                m = M[k]
                if k in chosen or not installed_path(m):
                    continue
                f = evaluate(hw, m, vision)
                if f.fits:
                    alts.append(_fit_json(m, f, vision))
            out[c.key] = {"tiers": {t: (_fit_json(v[0], v[1], vision) if v else None) for t, v in tiers.items()},
                          "installed": alts}
        return {"hw": hw.to_json(), "ram_bw": hw.ram_bw, "vram_usable": hw.vram_usable, "ram_usable": hw.ram_usable,
                "class": H.tier_of(hw), "categories": out}

    def _plan_files(self, plan: dict) -> list[tuple[str, str, int, Path]]:
        todo = []
        for ck, mk in plan["choices"].items():
            m = M[mk]
            vision = category(ck).needs_vision or ck == "lectura"
            have = installed_path(m) is not None
            if have and (not vision or mmproj_installed(m)):
                continue
            for path, size, dest in resolve_files(m, vision):
                if have and "mmproj" not in dest.name.lower():
                    continue
                todo.append((m.repo, path, size, dest))
        seen, files = set(), []
        for t in todo:
            if t[3] not in seen:
                seen.add(t[3])
                files.append(t)
        return files

    def summary(self, body: dict) -> dict:
        plan = {"created": time.strftime("%Y-%m-%d %H:%M"), "hardware": body.get("hw") or {},
                "choices": body.get("choices") or {}, "primary": body.get("primary")}
        for ck, mk in list(plan["choices"].items()):
            if mk not in M or ck not in {c.key for c in CATEGORIES}:
                plan["choices"].pop(ck)
        PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
        PLAN_FILE.write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
        self.last_plan = plan
        try:
            files = self._plan_files(plan)
            err = ""
        except Exception as e:
            files, err = [], f"No se pudo consultar Hugging Face: {e}"
        import shutil
        free = shutil.disk_usage(str(MODELS_DIR.parent)).free
        return {"files": [{"name": d.name, "size": s, "folder": d.parent.name} for _, _, s, d in files],
                "total": sum(f[2] for f in files), "disk_free": free, "error": err}

    # ------------------------------------------------------------------ descarga
    def start_download(self) -> dict:
        with self.lock:
            if self.progress["state"] == "running":
                return {"ok": False, "message": "Ya hay una descarga en curso"}
            if not self.last_plan:
                return {"ok": False, "message": "No hay plan"}
            self.cancel.clear()
            self.progress = {"state": "running", "files": [], "total": 0, "done": 0, "speed": 0.0, "eta": None,
                             "message": "Preparando…"}
        threading.Thread(target=self._download, daemon=True).start()
        return {"ok": True}

    def _download(self):
        import requests
        p = self.progress
        try:
            files = self._plan_files(self.last_plan)
            with self.lock:
                p["files"] = [{"name": d.name, "size": s, "done": 0, "state": "pendiente"} for _, _, s, d in files]
                p["total"] = sum(s for _, _, s, _ in files)
            t0, base_done = time.time(), 0
            for i, (repo, path, size, dest) in enumerate(files):
                dest.parent.mkdir(parents=True, exist_ok=True)
                fstate = p["files"][i]
                if dest.exists() and dest.stat().st_size == size:
                    fstate.update(done=size, state="hecho")
                    base_done += size
                    continue
                part = dest.with_suffix(dest.suffix + ".part")
                have = part.stat().st_size if part.exists() else 0
                headers = {"Range": f"bytes={have}-"} if have else {}
                fstate.update(state="descargando", done=have)
                with requests.get(f"{HF}/{repo}/resolve/main/{path}", headers=headers, stream=True,
                                  timeout=(15, 120)) as r:
                    if r.status_code not in (200, 206):
                        raise RuntimeError(f"{dest.name}: HTTP {r.status_code}")
                    if r.status_code == 200:
                        have = 0
                    with open(part, "ab" if have else "wb") as fh:
                        for chunk in r.iter_content(chunk_size=1 << 20):
                            if self.cancel.is_set():
                                raise InterruptedError
                            fh.write(chunk)
                            have += len(chunk)
                            with self.lock:
                                fstate["done"] = have
                                p["done"] = base_done + have
                                el = max(time.time() - t0, 0.5)
                                p["speed"] = p["done"] / el
                                p["eta"] = (p["total"] - p["done"]) / p["speed"] if p["speed"] else None
                                p["message"] = f"Descargando {dest.name}"
                if part.stat().st_size != size:
                    raise RuntimeError(f"{dest.name}: tamaño inesperado")
                part.replace(dest)
                fstate["state"] = "hecho"
                base_done += size
            apply_plan(self.last_plan)
            with self.lock:
                p.update(state="done", message="¡Listo! Modelos descargados y configuración aplicada.")
        except InterruptedError:
            with self.lock:
                p.update(state="cancelled", message="Descarga pausada. Puedes reanudarla luego (se conserva lo bajado).")
        except Exception as e:
            with self.lock:
                p.update(state="error", message=str(e))

    def apply_only(self) -> dict:
        if not self.last_plan:
            return {"ok": False}
        s = apply_plan(self.last_plan)
        return {"ok": True, "primary": s.last_model, "vision": s.vision_model, "profiles": s.profiles}


# ---------------------------------------------------------------------------
# Servidor HTTP
# ---------------------------------------------------------------------------

def make_handler(app: Setup, stop: threading.Event):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silencioso
            pass

        def _send(self, code: int, data, ctype="application/json"):
            body = data if isinstance(data, bytes) else (json.dumps(data, ensure_ascii=False).encode()
                                                          if ctype == "application/json" else data.encode())
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            return self.headers.get("X-Setup-Token") == app.token

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                html = (UI_DIR / "index.html").read_text(encoding="utf-8").replace("{{TOKEN}}", app.token)
                return self._send(200, html, "text/html")
            static = {".css": "text/css", ".js": "application/javascript", ".svg": "image/svg+xml"}
            name = path[1:]
            if "/" not in name and Path(name).suffix in static and (UI_DIR / name).is_file():
                return self._send(200, (UI_DIR / name).read_text(encoding="utf-8"), static[Path(name).suffix])
            if path == "/demo" or path.startswith("/demo/"):        # demo de componentes (estática, sin token)
                rel = path[len("/demo/"):] or "index.html"
                demo = app.demo_dir()
                target = (demo / rel).resolve()
                if demo.resolve() in target.parents and target.is_file():
                    ctypes = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}
                    return self._send(200, target.read_text(encoding="utf-8"), ctypes.get(target.suffix, "text/plain"))
                return self._send(404, {"error": "no existe"})
            if not self._authorized():
                return self._send(403, {"error": "token"})
            if path == "/api/ping":
                app.last_ping = time.time()
                return self._send(200, {"ok": True})
            if path == "/api/state":
                return self._send(200, app.state())
            if path == "/api/components":
                from . import webkit
                return self._send(200, {"groups": webkit.groups(), "total": sum(len(g["items"]) for g in webkit.groups())})
            if path == "/api/progress":
                with app.lock:
                    return self._send(200, dict(app.progress))
            self._send(404, {"error": "no existe"})

        def do_POST(self):
            if not self._authorized():
                return self._send(403, {"error": "token"})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = {}
            routes = {"/api/recommend": lambda: app.recommend(body), "/api/summary": lambda: app.summary(body),
                      "/api/download": app.start_download, "/api/apply": app.apply_only,
                      "/api/cancel": lambda: (app.cancel.set(), {"ok": True})[1],
                      "/api/quit": lambda: (stop.set(), {"ok": True})[1]}
            fn = routes.get(self.path)
            if fn is None:
                return self._send(404, {"error": "no existe"})
            try:
                self._send(200, fn())
            except Exception as e:
                self._send(500, {"error": f"{e.__class__.__name__}: {e}"})

    return Handler


def run_gui(port: int = 0, open_browser: bool = True) -> bool:
    from .render import console, say
    with console.status("[dim]Leyendo tu hardware…[/]", spinner="dots"):
        app = Setup()
    stop = threading.Event()
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, stop))
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    say(f"\n  [accent]Asistente de instalación abierto en tu navegador[/]\n  {url}\n"
        f"  [dim]Si no se abrió solo, copia esa dirección en el navegador. No cierres esta ventana hasta "
        f"terminar (Ctrl+C para salir).[/]\n")
    if open_browser:
        webbrowser.open(url)
    try:
        while not stop.wait(0.5):
            # pestaña cerrada (sin latidos) y nada descargándose: se cierra también esta ventana
            if app.last_ping and time.time() - app.last_ping > 30 and app.progress.get("state") != "running":
                say("[dim]Se cerró la página del asistente.[/]")
                break
    except KeyboardInterrupt:
        pass
    srv.shutdown()
    say("[dim]Asistente cerrado.[/]")
    return True
