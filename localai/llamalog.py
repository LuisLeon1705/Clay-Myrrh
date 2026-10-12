"""
Captura de logs nativos de llama.cpp / libmtmd.

- Evita que los mensajes de depuración (add_text:, clip_image_batch_encode:, …)
  ensucien la terminal: se guardan en un buffer circular (/debug los muestra).
- Permite medir los buffers reales ("Vulkan0 model buffer size = …").
"""

from __future__ import annotations

import collections
import contextlib
import ctypes
import os
import sys
import tempfile
import threading

_lines: collections.deque[str] = collections.deque(maxlen=4000)
_partial = [""]
_lock = threading.Lock()
_installed = False
_refs = []           # mantener vivos los callbacks de ctypes
echo = False         # /debug on -> además imprimir en stderr


def _push(text: str):
    with _lock:
        buf = _partial[0] + text
        *full, rest = buf.split("\n")
        for ln in full:
            _lines.append(ln)
            _total[0] += 1
            if echo:
                if _capturing[0]:
                    _echo_buf.append(ln)        # stderr redirigido: se muestra al restaurarlo
                else:
                    try:
                        sys.__stderr__.write(ln + "\n")
                    except Exception:
                        pass
        _partial[0] = rest


_total = [0]
_capturing = [0]
_echo_buf: list[str] = []


def mark() -> int:
    """Posición actual (para leer solo lo nuevo con since())."""
    with _lock:
        return _total[0]


def since(pos: int) -> list[str]:
    with _lock:
        n_new = _total[0] - pos
        if n_new <= 0:
            return []
        return list(_lines)[-n_new:]


def tail(n: int = 60) -> list[str]:
    with _lock:
        return list(_lines)[-n:]


def install():
    """Instala los callbacks de log de llama.cpp y mtmd (idempotente)."""
    global _installed
    if _installed:
        return
    import llama_cpp

    @llama_cpp.llama_log_callback
    def _cb(level, text, user_data):  # noqa: ARG001
        try:
            _push(text.decode("utf-8", "replace"))
        except Exception:
            pass

    _refs.append(_cb)
    llama_cpp.llama_log_set(_cb, ctypes.c_void_p(0))
    try:
        import llama_cpp.mtmd_cpp as mtmd
        mtmd.mtmd_log_set(_cb, ctypes.c_void_p(0))
        mtmd.mtmd_helper_log_set(_cb, ctypes.c_void_p(0))
    except Exception:
        pass
    _installed = True


@contextlib.contextmanager
def capture_stderr():
    """
    Redirige el descriptor 2 (stderr nativo) a un archivo temporal mientras dura el
    bloque y vuelca su contenido al buffer de logs. Necesario para los mensajes que
    las DLL escriben con fprintf(stderr) sin pasar por el callback.
    """
    try:
        fd = sys.stderr.fileno()
    except Exception:
        yield
        return
    sys.stderr.flush()
    saved = os.dup(fd)
    tmp = tempfile.TemporaryFile(mode="w+b")
    os.dup2(tmp.fileno(), fd)
    _capturing[0] += 1
    try:
        yield
    finally:
        _capturing[0] -= 1
        try:
            sys.stderr.flush()
        except Exception:
            pass
        os.dup2(saved, fd)
        os.close(saved)
        try:
            tmp.seek(0)
            data = tmp.read().decode("utf-8", "replace")
            if data:
                _push(data if data.endswith("\n") else data + "\n")
            if echo and _echo_buf and not _capturing[0]:
                with _lock:
                    pending, _echo_buf[:] = list(_echo_buf), []
                sys.__stderr__.write("\n".join(pending) + "\n")
        finally:
            tmp.close()
