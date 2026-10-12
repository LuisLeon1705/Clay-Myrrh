"""Sesiones: /chat save|list|load|delete, /resume (autoguardado) y /export."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .config import AUTOSAVE_DIR, SESSIONS_DIR
from .i18n import T

MAX_AUTOSAVES = 40


@dataclass
class SessionInfo:
    name: str
    path: Path
    updated: float
    model: str
    turns: int
    preview: str
    auto: bool


def _safe(name: str) -> str:
    n = re.sub(r"[^\w.-]+", "_", name.strip())[:80].strip("._")
    if not n:
        raise ValueError(T("Nombre de sesión vacío o no válido", "Empty or invalid session name"))
    return n


def _strip_images(history: list[dict]) -> list[dict]:
    """Las imágenes no se guardan (pesan MB); se deja una nota en su lugar."""
    out = []
    for m in history:
        m2 = {k: v for k, v in m.items() if k != "images"}
        if m.get("images"):
            names = ", ".join(i.get("name", "imagen") for i in m["images"])
            m2["content"] = f"[imágenes no guardadas: {names}]\n{m.get('content', '')}"
        out.append(m2)
    return out


def save(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(state)
    data["history"] = _strip_images(state.get("history", []))
    data["updated"] = time.time()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def save_named(name: str, state: dict) -> Path:
    p = SESSIONS_DIR / f"{_safe(name)}.json"
    save(p, state)
    return p


def autosave_path(session_id: str) -> Path:
    return AUTOSAVE_DIR / f"{session_id}.json"


def prune_autosaves():
    files = sorted(AUTOSAVE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[MAX_AUTOSAVES:]:
        try:
            f.unlink()
        except Exception:
            pass


def _info(p: Path, auto: bool) -> SessionInfo | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    hist = d.get("history", [])
    users = [m for m in hist if m.get("role") == "user"]
    first = users[0]["content"] if users else ""
    first = re.sub(r"\s+", " ", first.split("\n")[-1] if first.startswith("[") else first)[:70]
    return SessionInfo(p.stem, p, d.get("updated", p.stat().st_mtime), d.get("model", "?"),
                       len(users), first, auto)


def list_sessions(include_auto: bool = True) -> list[SessionInfo]:
    out = []
    for p in SESSIONS_DIR.glob("*.json"):
        i = _info(p, False)
        if i:
            out.append(i)
    if include_auto:
        for p in AUTOSAVE_DIR.glob("*.json"):
            i = _info(p, True)
            if i and i.turns:
                out.append(i)
    return sorted(out, key=lambda s: s.updated, reverse=True)


def load(name_or_path: str | Path) -> dict:
    p = Path(name_or_path)
    if not p.exists():
        p = SESSIONS_DIR / f"{_safe(str(name_or_path))}.json"
    if not p.exists():
        raise FileNotFoundError(T(f"No existe la sesión '{name_or_path}' (/chat list)", f"The session '{name_or_path}' does not exist (/chat list)"))
    return json.loads(p.read_text(encoding="utf-8"))


def delete(name: str):
    p = SESSIONS_DIR / f"{_safe(name)}.json"
    if not p.exists():
        raise FileNotFoundError(T(f"No existe la sesión '{name}'", f"The session '{name}' does not exist"))
    p.unlink()


def export_markdown(path: Path, model: str, system: str, history: list[dict], thoughts: dict[int, str] | None = None):
    lines = [T(f"# Conversación con {model}", f"# Conversation with {model}"), "", T(f"_Exportado el {time.strftime('%Y-%m-%d %H:%M')}_", f"_Exported on {time.strftime('%Y-%m-%d %H:%M')}_"), ""]
    if system:
        lines += [T("<details><summary>Prompt de sistema</summary>", "<details><summary>System prompt</summary>"), "", system, "", "</details>", ""]
    for i, m in enumerate(history):
        role = m.get("role")
        if role == "system":
            continue
        title = T({"user": "🧑 Usuario", "assistant": "🤖 Asistente"}, {"user": "🧑 User", "assistant": "🤖 Assistant"}).get(role, role)
        lines += [f"## {title}", ""]
        if m.get("images"):
            lines += [T(f"_Imágenes: {', '.join(x.get('name', 'imagen') for x in m['images'])}_", f"_Images: {', '.join(x.get('name', 'image') for x in m['images'])}_"), ""]
        if thoughts and i in thoughts:
            lines += ["<details><summary>Razonamiento</summary>", "", thoughts[i], "", "</details>", ""]
        lines += [m.get("content", ""), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
