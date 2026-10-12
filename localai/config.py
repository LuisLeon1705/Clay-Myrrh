"""Rutas, ajustes persistentes (~/.local_ai/config.json) y archivos de memoria."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_DIR / "models"
NON_CHAT_DIRS = {"diffusion"}          # carpetas ignoradas (no son modelos de chat)

APP_DIR = Path.home() / ".local_ai"
CONFIG_FILE = APP_DIR / "config.json"
HISTORY_FILE = APP_DIR / "history"
CRASH_LOG = APP_DIR / "crash.log"
SESSIONS_DIR = APP_DIR / "sessions"
AUTOSAVE_DIR = SESSIONS_DIR / "auto"
FILES_DIR = APP_DIR / "archivos"            # archivos que crea el modelo (uno por sesión)
MEMORY_FILENAME = "LOCAL_AI.md"
GLOBAL_MEMORY_FILE = APP_DIR / MEMORY_FILENAME
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
if not OLLAMA_HOST.startswith("http"):
    OLLAMA_HOST = "http://" + OLLAMA_HOST

DEFAULT_SYSTEM_PROMPT = (
    "Eres un asistente de IA local experto y conciso. Responde en español con Markdown "
    "limpio. Si te adjuntan imágenes o documentos, básate solo en su contenido real y di "
    "claramente si algo no se lee o no aparece."
)


@dataclass
class Settings:
    last_model: str | None = None
    theme: str = "oscuro"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    # carga del modelo
    ctx: int | str = "auto"            # int o "auto"
    gpu: int | str = "auto"            # int, "auto" o "max"
    kv_type: str = "f16"               # f16 | q8_0 | q4_0
    swa_full: bool = False             # caché SWA completa (más VRAM) o mínima
    flash_attn: bool = True
    vram_margin_mb: int = 600          # margen de seguridad para "auto"
    auto_ctx_cap: int = 32768          # tope del n_ctx que elige "auto"
    # muestreo
    max_tokens: int = 4096
    temperature: float = 0.6
    top_p: float = 0.95
    top_k: int = 40
    min_p: float = 0.05
    repeat_penalty: float = 1.05
    seed: int = -1
    # razonamiento
    thinking: bool = False             # pedir razonamiento a la plantilla (enable_thinking)
    show_thinking: bool = False        # mostrarlo desplegado
    # adjuntos
    pdf_max_pages: int = 30            # páginas de texto
    pdf_max_image_pages: int = 8       # páginas escaneadas a imagen
    image_max_side: int = 1280         # px del lado mayor al adjuntar imágenes
    keep_images_turns: int = 1         # turnos con imágenes que se mantienen
    shell: str = "powershell"          # powershell | cmd
    save_files: str = "auto"           # auto (carpeta de memoria, sin preguntar) | ask (cwd) | never
    # visión
    vision_mode: str = "lector"        # lector: un VLM transcribe y el modelo de chat trabaja con el texto
                                       # directo: las imágenes van al modelo de chat (si tiene mmproj)
    vision_model: str = "auto"         # modelo lector ("auto" = el modelo con visión más grande)
    # componentes web (.lai) en el chat: auto (solo cuando pides una página) | on (siempre) | off
    components_in_chat: str = "auto"
    # relevo automático: parte del presupuesto de cada respuesta se reserva para continuar en la siguiente
    relay_auto: bool = True
    relay_reserve: int = 15            # % del máximo de tokens de cada respuesta (10–20 recomendado)
    relay_max: int = 8                 # relevos máximos por turno
    relay_parallel: int = 2            # ramas a la vez cuando el motor lo permite (Ollama)
    # modo proyecto (/proyecto): carpetas recientes y rondas de herramientas (leer/buscar) por mensaje
    recent_projects: list[str] = field(default_factory=list)
    project_rounds: int = 4
    settings_version: int = 4
    # perfiles de uso creados por el asistente: {"programacion": {"model": "x.gguf", "max_tokens": …}}
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    # por modelo: {"archivo.gguf": {"mmproj": "...", "calib": 1.02}}
    models: dict[str, dict[str, Any]] = field(default_factory=dict)

    def model_cfg(self, name: str) -> dict[str, Any]:
        return self.models.setdefault(name, {})


def load_settings() -> Settings:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    s = Settings()
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        for k, v in data.items():
            if hasattr(s, k):
                setattr(s, k, v)
        ver = data.get("settings_version", 1)
        if ver < 2:
            # v2: los archivos generados se guardan solos en la carpeta de memoria
            s.save_files = "auto"
        if ver < 3 and s.max_tokens == 2048:
            s.max_tokens = 4096        # v3: 2048 cortaba los archivos generados
        if ver < 4 or not isinstance(s.components_in_chat, str):
            # v4: componentes por intención («auto») en lugar de sí/no
            s.components_in_chat = "on" if s.components_in_chat is True else "auto"
        s.settings_version = 4
    except FileNotFoundError:
        pass
    except Exception as e:  # config corrupta: no bloquear el arranque
        print(f"Aviso: no se pudo leer {CONFIG_FILE}: {e}")
    # migrar el historial antiguo (~/.local_ai_history)
    old = Path.home() / ".local_ai_history"
    if old.exists() and not HISTORY_FILE.exists():
        try:
            shutil.copy(old, HISTORY_FILE)
        except Exception:
            pass
    return s


# Valores originales de ajustes cambiados solo para esta ejecución (opciones --tokens, --kv…)
TRANSIENT: dict[str, Any] = {}


def save_settings(s: Settings):
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        data = asdict(s)
        data.update(TRANSIENT)
        CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Memoria persistente (tipo CLAUDE.md / GEMINI.md)
# ---------------------------------------------------------------------------

def project_memory_file(cwd: Path | None = None) -> Path:
    """LOCAL_AI.md más cercano subiendo desde cwd; si no hay, el del cwd."""
    cwd = (cwd or Path.cwd()).resolve()
    for d in [cwd, *cwd.parents]:
        f = d / MEMORY_FILENAME
        if f.exists():
            return f
    return cwd / MEMORY_FILENAME


def memory_files(cwd: Path | None = None) -> list[Path]:
    files = []
    if GLOBAL_MEMORY_FILE.exists():
        files.append(GLOBAL_MEMORY_FILE)
    pf = project_memory_file(cwd)
    if pf.exists() and pf.resolve() != GLOBAL_MEMORY_FILE.resolve():
        files.append(pf)
    return files


def load_memory_text(cwd: Path | None = None) -> str:
    parts = []
    for f in memory_files(cwd):
        try:
            txt = f.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if txt:
            parts.append(f"<!-- {f} -->\n{txt}")
    return "\n\n".join(parts)


def parse_size(text: str) -> int:
    """'32k' -> 32768, '8192' -> 8192, '1.5k' -> 1536."""
    t = text.strip().lower().replace("_", "")
    mult = 1
    if t.endswith("k"):
        mult, t = 1024, t[:-1]
    elif t.endswith("m"):
        mult, t = 1024 * 1024, t[:-1]
    return int(float(t) * mult)
