"""
Idioma de los comandos (Settings.language = auto | es | en).

Cada comando tiene un nombre en español y otro en inglés. El menú, el autocompletado y la ayuda
muestran los del idioma elegido, pero los dos juegos funcionan siempre (/deshacer y /undo hacen lo
mismo), así nadie pierde sus costumbres al cambiar de idioma. Los subcomandos en inglés se traducen
al español antes de llegar al método (/vision reader → /vision lector).

Los mensajes del programa siguen en español; aquí solo vive la capa de comandos.
"""

from __future__ import annotations

import locale
import re

LANGS = ("es", "en")

# clave interna (= método App.cmd_<clave>) → (nombre en español, nombre en inglés)
NAMES: dict[str, tuple[str, str]] = {
    "help": ("/ayuda", "/help"),
    "clear": ("/limpiar", "/clear"),
    "exit": ("/salir", "/exit"),
    "model": ("/modelo", "/model"),
    "models": ("/modelos", "/models"),
    "compact": ("/compactar", "/compact"),
    "resume": ("/retomar", "/resume"),
    "chat": ("/chat", "/chat"),
    "memory": ("/memoria", "/memory"),
    "stats": ("/estadisticas", "/stats"),
    "copy": ("/copiar", "/copy"),
    "save": ("/guardar", "/save"),
    "open": ("/abrir", "/open"),
    "export": ("/exportar", "/export"),
    "system": ("/sistema", "/system"),
    "theme": ("/tema", "/theme"),
    "think": ("/razonar", "/think"),
    "ctx": ("/ctx", "/ctx"),
    "gpu": ("/gpu", "/gpu"),
    "kv": ("/kv", "/kv"),
    "swa": ("/swa", "/swa"),
    "auto": ("/auto", "/auto"),
    "mem": ("/mem", "/mem"),
    "tokens": ("/tokens", "/tokens"),
    "temp": ("/temp", "/temp"),
    "top_p": ("/top_p", "/top_p"),
    "top_k": ("/top_k", "/top_k"),
    "min_p": ("/min_p", "/min_p"),
    "repeat": ("/repeticion", "/repeat"),
    "seed": ("/semilla", "/seed"),
    "params": ("/parametros", "/params"),
    "set": ("/set", "/set"),
    "file": ("/adjuntar", "/file"),
    "image": ("/imagen", "/image"),
    "pdf": ("/pdf", "/pdf"),
    "attachments": ("/adjuntos", "/attachments"),
    "mmproj": ("/mmproj", "/mmproj"),
    "vision": ("/vision", "/vision"),
    "archivos": ("/archivos", "/files"),
    "settings": ("/ajustes", "/settings"),
    "modo": ("/modo", "/mode"),
    "setup": ("/asistente", "/setup"),
    "componentes": ("/componentes", "/components"),
    "plantilla": ("/plantilla", "/template"),
    "proyecto": ("/proyecto", "/project"),
    "cambios": ("/cambios", "/changes"),
    "deshacer": ("/deshacer", "/undo"),
    "cadena": ("/cadena", "/chain"),
    "grafo": ("/grafo", "/graph"),
    "zero": ("/zero", "/zero"),
    "doctor": ("/doctor", "/doctor"),
    "debug": ("/depurar", "/debug"),
}

# atajos y variantes que funcionan siempre (ocultos en el menú)
EXTRA_ALIASES: dict[str, str] = {
    "/?": "help", "/h": "help", "/reset": "clear", "/quit": "exit", "/q": "exit", "/estimate": "mem",
    "/img": "image", "/carpeta": "proyecto", "/red": "grafo", "/prompt-zero": "zero",
    "/estadísticas": "stats", "/parámetros": "params", "/repetición": "repeat", "/visión": "vision",
}

# subcomandos en inglés → los que entiende el método (en español)
_SUB_EN: dict[str, dict[str, str]] = {
    "vision": {"reader": "lector", "direct": "directo", "model": "modelo", "show": "ver", "clear": "limpiar"},
    "archivos": {"open": "abrir"},
    "proyecto": {"close": "cerrar", "reload": "recargar"},
    "grafo": {"show": "ver", "warnings": "avisos"},
    "zero": {"continue": "continuar"},
}


def detect() -> str:
    """Idioma del sistema: «es» si Windows (o el locale) está en español; si no, «en»."""
    try:
        import ctypes
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "es" if (langid & 0x3FF) == 0x0A else "en"
    except Exception:
        pass
    try:
        name = (locale.getlocale()[0] or "").lower()
        if name:
            return "es" if name.startswith(("es", "spanish")) else "en"
    except Exception:
        pass
    return "es"


def resolve(setting: str | None) -> str:
    """auto | es | en → es | en."""
    s = (setting or "auto").lower()
    return s if s in LANGS else detect()


def name(key: str, lang: str) -> str:
    """Nombre visible de un comando en el idioma dado (/deshacer o /undo)."""
    es, en = NAMES[key]
    return en if lang == "en" else es


def other(key: str, lang: str) -> str:
    """El nombre del otro idioma (alias oculto)."""
    return name(key, "es" if lang == "en" else "en")


def key_of(cmd: str) -> str | None:
    """Clave interna de cualquier nombre o alias de comando, en cualquier idioma."""
    cmd = cmd.lower()
    for key, names in NAMES.items():
        if cmd in names:
            return key
    return EXTRA_ALIASES.get(cmd)


def normalize_arg(key: str, arg: str) -> str:
    """Traduce los subcomandos en inglés a los que espera el método (que están en español)."""
    a = arg.strip()
    if not a:
        return a
    if key == "cadena":                                     # «… from 3» → «… desde 3»
        return re.sub(r"\s+from\s+(\d+)\s*$", r" desde \1", a, flags=re.I)
    if key == "memory":                                     # «edit project» → «edit proyecto»
        return re.sub(r"^(edit)\s+project\b", r"\1 proyecto", a, flags=re.I)
    table = _SUB_EN.get(key)
    if not table:
        return a
    first, sep, rest = a.partition(" ")
    return table.get(first.lower(), first) + sep + rest


def t(lang: str, es: str, en: str) -> str:
    """Texto según el idioma."""
    return en if lang == "en" else es


# idioma activo de los mensajes del programa (lo fija App al arrancar y con /set language)
LANG = "es"


def set_lang(lang: str):
    global LANG
    LANG = lang if lang in LANGS else "es"


def T(es: str, en: str) -> str:
    """Mensaje en el idioma activo: T("Modelo cargado", "Model loaded")."""
    return en if LANG == "en" else es


def init_from_settings():
    """Para módulos que se ejecutan sin App (asistente de instalación): idioma según config.json."""
    try:
        from .config import load_settings
        set_lang(resolve(load_settings().language))
    except Exception:
        set_lang(detect())
