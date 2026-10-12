"""
Motores de inferencia: llama-cpp-python (GGUF local) y Ollama.

Decisiones tomadas tras el diagnóstico (ver README/resumen):
- Visión: MTMDChatHandler (plantilla Jinja del propio GGUF + libmtmd). El antiguo
  Qwen25VLChatHandler usa una plantilla genérica que hacía que Qwen2-VL respondiera
  vacío con temperature=0.7. Las imágenes van ANTES del texto (orden de Qwen/Gemma).
- El contexto de mtmd se inicializa al cargar: si el mmproj no encaja se avisa en ese
  momento (antes fallaba en silencio o en mitad de la conversación).
- MTMDChatHandler vacía la caché KV en cada llamada, así que solo se usa cuando hay
  imágenes en el contexto; el resto de turnos van por la ruta de texto, que reutiliza
  el prefijo ya evaluado (mucho más rápido en conversaciones largas).
"""

from __future__ import annotations

import base64
import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import llamalog
from .catalog import ModelEntry
from .memory import KV_GGML_TYPE, DeviceLog, RealUsage, parse_buffer_logs, parse_device_logs, probe

_llama_mod = None


class EngineError(Exception):
    """Error con mensaje ya preparado para el usuario (y pistas)."""

    def __init__(self, msg: str, hints: list[str] | None = None, log: list[str] | None = None):
        super().__init__(msg)
        self.hints = hints or []
        self.log = log or []


class _NoRedirect:
    """Sustituto inocuo de llama_cpp._utils.suppress_stdout_stderr."""

    def __init__(self, disable: bool = True):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _disable_fd_redirection():
    """
    llama-cpp-python silencia stdout/stderr con os.dup2 en cada imagen que codifica.
    En Windows dup2 CIERRA el handle original de la consola, que prompt_toolkit tiene
    guardado: al volver a la caja de entrada saltaba NoConsoleScreenBufferError y el
    CLI se cerraba tras cada respuesta con imagen. Los logs nativos ya llegan por
    callback (llamalog), así que esa redirección sobra.
    """
    import llama_cpp._utils as U
    import llama_cpp.llama as LL
    import llama_cpp.llama_chat_format as F
    for mod in (U, LL, F):
        if hasattr(mod, "suppress_stdout_stderr"):
            mod.suppress_stdout_stderr = _NoRedirect


def llama():
    """Importa llama_cpp silenciando los mensajes de arranque del backend."""
    global _llama_mod
    if _llama_mod is None:
        with llamalog.capture_stderr():
            import llama_cpp  # noqa: F401
            import llama_cpp.llama_chat_format  # noqa: F401
        llamalog.install()
        _disable_fd_redirection()
        _llama_mod = llama_cpp
    return _llama_mod


# ---------------------------------------------------------------------------
# Datos comunes
# ---------------------------------------------------------------------------

@dataclass
class GenParams:
    max_tokens: int = 2048
    temperature: float = 0.6
    top_p: float = 0.95
    top_k: int = 40
    min_p: float = 0.05
    repeat_penalty: float = 1.05
    seed: int = -1
    thinking: bool = False


@dataclass
class GenStats:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ctx_used: int = 0
    t_first: float = 0.0       # segundos hasta el primer token (procesar prompt/imagen)
    t_total: float = 0.0
    stopped: bool = False
    used_vision: bool = False

    @property
    def tps(self) -> float:
        gen_t = self.t_total - self.t_first
        return self.completion_tokens / gen_t if gen_t > 0 and self.completion_tokens > 1 else 0.0


# ---------------------------------------------------------------------------
# Razonamiento <think> … </think> y canal de pensamiento de Gemma 4
# ---------------------------------------------------------------------------

THINK_OPEN = ("<think>", "<|channel>thought")
THINK_CLOSE = ("</think>", "<channel|>")
_STRIP_TOKENS = ("<|im_end|>", "<turn|>", "<end_of_turn>", "<|eot_id|>", "<|endoftext|>")


RELAY_OPEN, RELAY_CLOSE = ("<relevo>",), ("</relevo>",)


class ThinkParser:
    """
    Separa en streaming tres canales: razonamiento ("think"), texto final ("text") y el
    bloque de relevo ("relay", nunca se muestra). Tolera marcadores partidos entre trozos.
    """

    def __init__(self, start_in_think: bool = False):
        self.in_think = start_in_think
        self.in_relay = False
        self.buf = ""

    def _kind(self) -> str:
        return "relay" if self.in_relay else ("think" if self.in_think else "text")

    def _markers(self) -> tuple[str, ...]:
        if self.in_relay:
            return RELAY_CLOSE
        if self.in_think:
            return THINK_CLOSE
        return THINK_OPEN + RELAY_OPEN

    def feed(self, text: str) -> list[tuple[str, str]]:
        self.buf += text
        out: list[tuple[str, str]] = []
        while self.buf:
            markers = self._markers()
            strip_tokens = () if self.in_relay else _STRIP_TOKENS
            hits = [(self.buf.find(m), m) for m in markers + strip_tokens if m in self.buf]
            if hits:
                idx, m = min(hits)
                if idx:
                    out.append((self._kind(), self.buf[:idx]))
                self.buf = self.buf[idx + len(m):]
                if m in THINK_OPEN:
                    self.in_think = True
                    self.buf = self.buf.lstrip("\n")
                elif m in THINK_CLOSE:
                    self.in_think = False
                    self.buf = self.buf.lstrip("\n")
                elif m in RELAY_OPEN:
                    self.in_relay = True
                    out.append(("relay", "<relevo>"))
                elif m in RELAY_CLOSE:
                    out.append(("relay", "</relevo>"))
                    self.in_relay = False
                continue
            # ¿el final del buffer puede ser el comienzo de un marcador?
            keep = 0
            for m in markers + strip_tokens:
                for k in range(min(len(m) - 1, len(self.buf)), 0, -1):
                    if self.buf.endswith(m[:k]):
                        keep = max(keep, k)
                        break
            emit = self.buf[:len(self.buf) - keep] if keep else self.buf
            if emit:
                out.append((self._kind(), emit))
            self.buf = self.buf[len(emit):]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        if not self.buf:
            return []
        out = [(self._kind(), self.buf)]
        self.buf = ""
        return out


# ---------------------------------------------------------------------------
# Tokens de imagen (para presupuestar el contexto antes de enviar)
# ---------------------------------------------------------------------------

def image_tokens(projector: str | None, w: int, h: int) -> int:
    p = (projector or "").lower()
    if p.startswith(("qwen2vl", "qwen2.5vl", "qwen25vl")):
        return math.ceil(w / 28) * math.ceil(h / 28) + 2
    if p.startswith(("qwen3vl", "qwen35", "qwen3.5")):
        return math.ceil(w / 32) * math.ceil(h / 32) + 2
    if p.startswith(("resampler", "minicpmv")):
        slices = min(9, max(1, round(w * h / (448 * 448))))
        return 64 * (1 + (slices if slices > 1 else 0)) + 16 * (slices + 1)
    if p.startswith(("gemma3", "gemma4")):
        return 280
    if p.startswith("llama4"):
        return 144 * min(16, max(1, round(w * h / (336 * 336)))) + 20
    if p.startswith("pixtral"):
        return math.ceil(w / 16) * math.ceil(h / 16) + math.ceil(h / 16)
    return 600


# ---------------------------------------------------------------------------
# llama-cpp-python
# ---------------------------------------------------------------------------

def _hints_from_log(lines: list[str], entry: ModelEntry) -> tuple[str, list[str]]:
    txt = "\n".join(lines[-200:]).lower()
    if "outofdevicememory" in txt or "out of memory" in txt or "failed to allocate" in txt or "cudamalloc" in txt:
        return ("VRAM insuficiente al reservar los buffers del modelo.",
                ["/mem para ver el desglose", "/gpu auto o /gpu <n> para dejar capas en CPU",
                 "/ctx <n> más pequeño o /kv q8_0 para reducir la caché KV",
                 "cierra juegos/apps que usen la GPU (restan VRAM libre)"])
    if "unknown model architecture" in txt or "unknown architecture" in txt:
        return (f"Esta versión de llama.cpp no reconoce la arquitectura '{entry.arch}'.",
                ["actualiza llama-cpp-python", "o usa llama-server (llama.cpp) / Ollama con este GGUF"])
    if "wrong number of tensors" in txt or "missing tensor" in txt or "unexpected tensor" in txt:
        return ("El GGUF no coincide con lo que espera llama.cpp (tensores distintos).",
                ["el archivo puede estar incompleto o ser de una versión más nueva de llama.cpp"])
    return ("llama.cpp no pudo cargar el modelo.", ["/debug muestra el log nativo completo"])


_FLAT_CLS = None


def _flat_handler_class():
    """
    MTMDChatHandler que entrega a la plantilla el contenido como TEXTO (con la marca
    de imagen en su sitio) en vez de una lista de partes. Algunas plantillas de los
    GGUF (p. ej. MiniCPM-V) solo saben concatenar texto y fallaban con
    "can only concatenate str (not list)". libmtmd añade alrededor de la marca los
    tokens de imagen propios de cada modelo, así que el resultado es el mismo.
    """
    global _FLAT_CLS
    if _FLAT_CLS is None:
        from llama_cpp.llama_chat_format import MTMDChatHandler

        class FlatMTMDChatHandler(MTMDChatHandler):
            def _get_template_messages(self, messages, media_marker):
                out = []
                for m in messages:
                    d = dict(m)
                    c = d.get("content")
                    if isinstance(c, list):
                        pieces = []
                        for part in c:
                            if isinstance(part, dict) and part.get("type") == "image_url":
                                pieces.append(media_marker + "\n")
                            elif isinstance(part, dict):
                                pieces.append(str(part.get("text", "")))
                            else:
                                pieces.append(str(part))
                        d["content"] = "".join(pieces)
                    out.append(d)
                return out

        _FLAT_CLS = FlatMTMDChatHandler
    return _FLAT_CLS


class LlamaEngine:
    kind = "gguf"

    def __init__(self, entry: ModelEntry, n_ctx: int, ngl: int, kv_type: str = "f16",
                 swa_full: bool = False, flash_attn: bool = True, use_vision: bool = True):
        L = llama()
        from llama_cpp.llama_chat_format import Jinja2ChatFormatter, MTMDChatHandler

        self.entry = entry
        self.name = entry.name
        self.n_ctx_req, self.ngl, self.kv_type = n_ctx, ngl, kv_type
        self.swa_full, self.flash_attn = swa_full, flash_attn
        self.vision = False
        self.vision_error: str | None = None
        self.projector = entry.mmproj.projector_type if entry.mmproj else None
        self._vision_handler = None
        self.last_stats = GenStats()
        self.load_time = 0.0

        if kv_type != "f16" and not flash_attn:
            raise EngineError("La caché KV cuantizada requiere flash-attn.", ["/kv f16 o activa flash-attn"])

        kw: dict[str, Any] = {}
        if kv_type != "f16":
            kw["type_k"] = kw["type_v"] = KV_GGML_TYPE[kv_type]
        pos = llamalog.mark()
        m0 = probe()
        t0 = time.time()
        try:
            with llamalog.capture_stderr():
                self.llm = L.Llama(model_path=entry.path, n_gpu_layers=ngl, n_ctx=n_ctx,
                                   flash_attn=flash_attn, swa_full=swa_full, verbose=False, **kw)
        except Exception as e:
            log = llamalog.since(pos)
            msg, hints = _hints_from_log(log, entry)
            raise EngineError(f"{msg} ({e})", hints, log) from None

        # --- visión -------------------------------------------------------------
        if use_vision and entry.mmproj is not None:
            try:
                h = _flat_handler_class()(clip_model_path=entry.mmproj.path, verbose=False)
                with llamalog.capture_stderr():
                    h._init_mtmd_context(self.llm)
                self._vision_handler = h
                self.vision = True
            except Exception as e:
                log = "\n".join(l for l in llamalog.since(pos)[-40:] if "clip" in l.lower() or "mtmd" in l.lower() or "error" in l.lower())
                self.vision_error = (f"No se pudo cargar el mmproj '{Path(entry.mmproj.path).name}': {e}. "
                                     f"Se usa el modelo solo como texto.\n{log[-800:]}")
        elif entry.vision == "falta_mmproj":
            self.vision_error = f"Modelo multimodal sin mmproj: se carga solo como texto. {entry.vision_note}"

        self.load_time = time.time() - t0
        self.real: RealUsage = parse_buffer_logs(llamalog.since(pos))
        self.devlog: DeviceLog = parse_device_logs(llamalog.since(pos))     # capas reales en GPU, dispositivos
        m1 = probe()
        self.real.vram_delta = (None if m0.free_estimated or not m0.vram_total
                                else max(0, m0.vram_free - m1.vram_free))

        # --- plantilla de chat para la ruta de texto ----------------------------
        tmpl = self.llm.metadata.get("tokenizer.chat_template")
        self._fmt = None
        self._text_handler = None
        if tmpl:
            eos = self._special(self.llm.token_eos())
            bos = self._special(self.llm.token_bos())
            try:
                self._fmt = Jinja2ChatFormatter(template=tmpl, eos_token=eos, bos_token=bos,
                                                stop_token_ids=[self.llm.token_eos()])
                self._text_handler = self._fmt.to_chat_handler()
            except Exception:
                self._fmt = None
        self.can_think = bool(tmpl and ("enable_thinking" in tmpl or "<think>" in tmpl))

    # ------------------------------------------------------------------ util
    def _special(self, tok: int) -> str:
        try:
            if tok < 0:
                return ""
            return self.llm.detokenize([tok], special=True).decode("utf-8", "ignore")
        except Exception:
            return ""

    @property
    def n_ctx(self) -> int:
        return self.llm.n_ctx()

    @property
    def ctx_used(self) -> int:
        return int(self.llm.n_tokens)

    def count_tokens(self, text: str) -> int:
        try:
            return len(self.llm.tokenize(text.encode("utf-8"), add_bos=False, special=True))
        except Exception:
            return len(text) // 3

    def _convert(self, messages: list[dict], with_images: bool) -> list[dict]:
        out = []
        for m in messages:
            imgs = m.get("images") or []
            content = m.get("content", "")
            if imgs and with_images:
                parts: list[dict] = [{"type": "image_url", "image_url": {"url": f"data:{i.get('mime', 'image/png')};base64,{i['b64']}"}}
                                     for i in imgs]
                parts.append({"type": "text", "text": content})
                out.append({"role": m["role"], "content": parts})
            else:
                if imgs:
                    names = ", ".join(i.get("name", "imagen") for i in imgs)
                    content = f"[{len(imgs)} imagen(es) omitida(s) del contexto: {names}]\n{content}"
                out.append({"role": m["role"], "content": content})
        return out

    def prompt_tokens(self, messages: list[dict], thinking: bool = False) -> int:
        """Tokens que ocuparía el prompt (texto exacto con la plantilla + imágenes estimadas)."""
        text_msgs = self._convert(messages, with_images=False)
        n_img = 0
        for m in messages:
            for i in m.get("images") or []:
                n_img += image_tokens(self.projector, i.get("w", 1024), i.get("h", 1024))
        if self._fmt is not None:
            try:
                prompt = self._fmt(messages=text_msgs, enable_thinking=thinking).prompt
                return self.count_tokens(prompt) + (n_img if self.vision else 0)
            except Exception:
                pass
        return sum(self.count_tokens(m["content"]) + 6 for m in text_msgs) + (n_img if self.vision else 0)

    def starts_in_think(self, messages: list[dict], thinking: bool) -> bool:
        if self._fmt is None:
            return False
        try:
            tail = self._fmt(messages=self._convert(messages[-2:], False), enable_thinking=thinking).prompt.rstrip()
        except Exception:
            return False
        return tail.endswith("<think>") or tail.endswith("<|channel>thought")

    # ------------------------------------------------------------- generación
    def stream(self, messages: list[dict], p: GenParams, stop: threading.Event) -> Iterator[tuple[str, str]]:
        has_imgs = any(m.get("images") for m in messages)
        use_vision = self.vision and has_imgs
        conv = self._convert(messages, with_images=use_vision)
        kwargs = dict(stream=True, max_tokens=p.max_tokens, temperature=p.temperature, top_p=p.top_p,
                      top_k=p.top_k, min_p=p.min_p, repeat_penalty=p.repeat_penalty,
                      seed=None if p.seed < 0 else p.seed, enable_thinking=p.thinking)
        st = GenStats(used_vision=use_vision)
        self.last_stats = st
        parser = ThinkParser(self.starts_in_think(messages, p.thinking))
        t0 = time.time()
        try:
            if use_vision:
                gen = self._vision_handler(llama=self.llm, messages=conv, **kwargs)
            elif self._text_handler is not None:
                gen = self._text_handler(llama=self.llm, messages=conv, **kwargs)
            else:
                kwargs.pop("enable_thinking")
                gen = self.llm.create_chat_completion(messages=conv, **kwargs)
            first = True
            for chunk in gen:
                if first:
                    st.t_first = time.time() - t0
                    st.prompt_tokens = int(self.llm.n_tokens)
                    first = False
                delta = chunk["choices"][0].get("delta", {}) or {}
                piece = delta.get("content") or ""
                if piece:
                    st.completion_tokens += 1
                    yield from parser.feed(piece)
                if stop.is_set():
                    st.stopped = True
                    try:
                        gen.close()
                    except Exception:
                        pass
                    break
            yield from parser.flush()
        except ValueError as e:
            msg = str(e)
            m = re.search(r"(\d+)\s*>\s*(\d+)", msg) or re.search(r"\((\d+)\) exceed context window of (\d+)", msg)
            if "exceed" in msg.lower() and m:
                raise EngineError(
                    f"El mensaje no cabe en el contexto: {m.group(1)} tokens > n_ctx {m.group(2)}.",
                    ["/ctx 16k (o mayor) para ampliar el contexto", "/compact para resumir la conversación",
                     "adjunta menos páginas o imágenes más pequeñas"]) from None
            if "mtmd" in msg.lower() or "bitmap" in msg.lower() or "chunk" in msg.lower():
                raise EngineError(f"Fallo del codificador de visión: {msg}", ["/debug para ver el log de mtmd"],
                                  llamalog.tail(40)) from None
            raise
        finally:
            st.t_total = time.time() - t0
            st.ctx_used = int(self.llm.n_tokens)
            if st.prompt_tokens == 0:
                st.prompt_tokens = st.ctx_used

    def close(self):
        try:
            if self._vision_handler is not None:
                self._vision_handler._exit_stack.close()
        except Exception:
            pass
        try:
            self.llm.close()
        except Exception:
            pass
        self.llm = None
        import gc
        gc.collect()


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

class OllamaEngine:
    kind = "ollama"

    def __init__(self, name: str, host: str, n_ctx: int = 8192):
        try:
            import requests
        except ImportError:
            raise EngineError("Falta 'requests' para hablar con Ollama.", ["pip install requests"]) from None
        self.requests = requests
        self.name = name
        self.host = host.rstrip("/")
        self.n_ctx = n_ctx
        self._used = 0
        self.vision_error = None
        self.projector = None
        self.last_stats = GenStats()
        self.load_time = 0.0
        self.real = RealUsage()
        try:
            requests.get(f"{self.host}/api/tags", timeout=2).raise_for_status()
        except Exception as e:
            raise EngineError(f"No se pudo conectar a Ollama en {self.host} ({e.__class__.__name__}).",
                              ["arranca 'ollama serve' o abre la app de Ollama"]) from None
        try:
            info = requests.post(f"{self.host}/api/show", json={"model": name}, timeout=10).json()
        except Exception:
            info = {}
        if "error" in info:
            raise EngineError(f"Ollama: {info['error']}", ["/models para ver los modelos disponibles"])
        caps = info.get("capabilities") or []
        self.vision = "vision" in caps
        self.can_think = "thinking" in caps
        mi = info.get("model_info") or {}
        self.ctx_max = next((int(v) for k, v in mi.items() if k.endswith(".context_length")), 0)
        if not caps:  # Ollama antiguo: heurística por nombre
            self.vision = bool(re.search(r"vision|-vl|llava|minicpm-v|moondream|gemma3|qwen2\.5vl", name.lower()))

    @property
    def ctx_used(self) -> int:
        return self._used

    def count_tokens(self, text: str) -> int:
        return len(text) // 3

    def prompt_tokens(self, messages: list[dict], thinking: bool = False) -> int:
        n = sum(len(m.get("content", "")) // 3 + 6 for m in messages)
        n += sum(300 for m in messages for _ in (m.get("images") or [])) if self.vision else 0
        return n

    def stream(self, messages: list[dict], p: GenParams, stop: threading.Event) -> Iterator[tuple[str, str]]:
        msgs = []
        for m in messages:
            d = {"role": m["role"], "content": m.get("content", "")}
            if m.get("images"):
                if self.vision:
                    d["images"] = [i["b64"] for i in m["images"]]
                else:
                    d["content"] = f"[{len(m['images'])} imagen(es) omitida(s)]\n" + d["content"]
            msgs.append(d)
        opts = {"num_ctx": self.n_ctx, "num_predict": p.max_tokens, "temperature": p.temperature,
                "top_p": p.top_p, "top_k": p.top_k, "min_p": p.min_p, "repeat_penalty": p.repeat_penalty}
        if p.seed >= 0:
            opts["seed"] = p.seed
        payload: dict[str, Any] = {"model": self.name, "messages": msgs, "stream": True, "options": opts}
        if self.can_think:
            payload["think"] = p.thinking
        st = GenStats(used_vision=self.vision and any(m.get("images") for m in messages))
        self.last_stats = st
        parser = ThinkParser(False)
        t0 = time.time()
        try:
            res = self.requests.post(f"{self.host}/api/chat", json=payload, stream=True, timeout=(5, 600))
            if res.status_code != 200:
                raise EngineError(f"Ollama respondió {res.status_code}: {res.text[:300]}")
            for line in res.iter_lines():
                if stop.is_set():
                    st.stopped = True
                    res.close()
                    break
                if not line:
                    continue
                data = json.loads(line)
                if data.get("error"):
                    raise EngineError(f"Ollama: {data['error']}")
                msg = data.get("message") or {}
                if msg.get("thinking"):
                    if not st.t_first:
                        st.t_first = time.time() - t0
                    st.completion_tokens += 1
                    yield ("think", msg["thinking"])
                if msg.get("content"):
                    if not st.t_first:
                        st.t_first = time.time() - t0
                    st.completion_tokens += 1
                    yield from parser.feed(msg["content"])
                if data.get("done"):
                    st.prompt_tokens = int(data.get("prompt_eval_count") or 0)
                    st.completion_tokens = int(data.get("eval_count") or st.completion_tokens)
                    ed = (data.get("eval_duration") or 0) / 1e9
                    if ed:
                        st.t_first = max(0.0, (time.time() - t0) - ed)
            yield from parser.flush()
        except self.requests.RequestException as e:
            raise EngineError(f"Error de red con Ollama: {e}") from None
        finally:
            st.t_total = time.time() - t0
            st.ctx_used = st.prompt_tokens + st.completion_tokens
            self._used = st.ctx_used

    def unload(self):
        """Libera la VRAM de Ollama (keep_alive=0); se recarga solo en el siguiente mensaje."""
        try:
            self.requests.post(f"{self.host}/api/generate", json={"model": self.name, "keep_alive": 0}, timeout=10)
        except Exception:
            pass

    def close(self):
        pass


def encode_image_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
