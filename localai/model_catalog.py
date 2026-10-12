"""
Catálogo curado para el asistente de instalación (local-ai --setup).

Repositorios y tamaños verificados en la API de Hugging Face (octubre de 2026). Los
archivos exactos se resuelven al descargar (pueden venir divididos en partes).
Todas las arquitecturas están soportadas por llama-cpp-python 0.3.34.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CatModel:
    key: str
    name: str
    repo: str
    pattern: str                 # cuantización o nombre exacto del archivo
    size_gb: float
    params_b: float
    active_b: float | None = None      # MoE: parámetros activos por token
    arch: str = ""
    mmproj: str | None = None          # archivo mmproj del repo (si hay)
    mmproj_gb: float = 0.0
    kind: str = "llm"                  # carpeta destino: llm | vision
    note: str = ""


M = {m.key: m for m in [
    # --- Qwen3.5 / 3.6 (texto + visión con mmproj, razonamiento opcional) ---------------
    CatModel("qwen35-0.8b", "Qwen3.5 0.8B", "unsloth/Qwen3.5-0.8B-GGUF", "Q8_0", 0.76, 0.8, None, "qwen35",
             "mmproj-F16.gguf", 0.19, note="diminuto: clasificar, extraer datos, autocompletar"),
    CatModel("qwen35-2b", "Qwen3.5 2B", "unsloth/Qwen3.5-2B-GGUF", "Q8_0", 1.87, 1.9, None, "qwen35",
             "mmproj-F16.gguf", 0.62, note="utilidades rápidas incluso sin GPU"),
    CatModel("qwen35-4b", "Qwen3.5 4B", "unsloth/Qwen3.5-4B-GGUF", "UD-Q4_K_XL", 2.71, 4.2, None, "qwen35",
             "mmproj-F16.gguf", 0.63, note="el mejor pequeño: chat, resúmenes, código sencillo"),
    CatModel("qwen35-9b", "Qwen3.5 9B", "unsloth/Qwen3.5-9B-GGUF", "UD-Q4_K_XL", 5.56, 9.0, None, "qwen35",
             "mmproj-F16.gguf", 0.86, note="todoterreno de gama media; con /think razona bien"),
    CatModel("qwen36-27b", "Qwen3.6 27B", "unsloth/Qwen3.6-27B-GGUF", "UD-Q4_K_XL", 16.4, 26.9, None, "qwen35",
             "mmproj-F16.gguf", 0.86, note="denso: máxima calidad de su familia, pero lento si no cabe en VRAM"),
    CatModel("qwen36-35b-a3b", "Qwen3.6 35B-A3B", "unsloth/Qwen3.6-35B-A3B-GGUF", "IQ4_XS", 16.51, 34.7, 3.0,
             "qwen35moe", "mmproj-F16.gguf", 0.84, note="MoE: calidad de ~30B a velocidad de ~3B; ideal con poca VRAM"),
    CatModel("qwen35-122b-a10b", "Qwen3.5 122B-A10B", "unsloth/Qwen3.5-122B-A10B-GGUF", "UD-Q4_K_XL", 71.74,
             122.1, 10.0, "qwen35moe", "mmproj-F16.gguf", 0.85, note="nivel estación de trabajo (≥96 GB)"),
    CatModel("qwen35-397b-a17b", "Qwen3.5 397B-A17B", "unsloth/Qwen3.5-397B-A17B-GGUF", "UD-Q4_K_XL", 228.43,
             396.3, 17.0, "qwen35moe", "mmproj-F16.gguf", 0.86, note="buque insignia abierto (≥256 GB)"),
    # --- Gemma 4 (redacción natural, multimodal) -------------------------------------
    CatModel("gemma4-e2b", "Gemma 4 E2B", "unsloth/gemma-4-E2B-it-GGUF", "UD-Q4_K_XL", 2.97, 4.6, None, "gemma4",
             "mmproj-F16.gguf", 0.92, note="ligero, buen español natural"),
    CatModel("gemma4-e4b", "Gemma 4 E4B", "unsloth/gemma-4-E4B-it-GGUF", "UD-Q4_K_XL", 4.77, 7.5, None, "gemma4",
             "mmproj-F16.gguf", 0.92, note="multimodal ligero, buena prosa"),
    CatModel("gemma4-12b", "Gemma 4 12B", "unsloth/gemma-4-12b-it-GGUF", "UD-Q4_K_XL", 6.86, 11.9, None, "gemma4",
             "mmproj-F16.gguf", 0.16, note="muy buena redacción y diálogo"),
    CatModel("gemma4-31b", "Gemma 4 31B", "unsloth/gemma-4-31B-it-GGUF", "UD-Q4_K_XL", 17.53, 30.7, None, "gemma4",
             "mmproj-F16.gguf", 1.12, note="denso de alta calidad; lento sin 24 GB de VRAM"),
    # --- Código ----------------------------------------------------------------------
    CatModel("qwen3-coder-30b", "Qwen3-Coder 30B-A3B", "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF", "UD-Q4_K_XL",
             16.45, 30.5, 3.3, "qwen3moe", note="especialista en código, MoE rápido"),
    CatModel("glm47-flash", "GLM-4.7 Flash (30B-A3B)", "unsloth/GLM-4.7-Flash-GGUF", "UD-Q4_K_XL", 16.32, 29.9,
             3.0, "deepseek2", note="MoE rápido, bueno en código y front-end"),
    CatModel("devstral2", "Devstral Small 2 (24B)", "unsloth/Devstral-Small-2-24B-Instruct-2512-GGUF",
             "UD-Q4_K_XL", 13.51, 23.6, None, "mistral3", "mmproj-F16.gguf", 0.82,
             note="denso, muy bueno en proyectos de varios archivos"),
    CatModel("qwen3-coder-next", "Qwen3-Coder-Next (80B-A3B)", "unsloth/Qwen3-Coder-Next-GGUF", "UD-Q4_K_XL",
             46.2, 79.7, 3.0, "qwen3next", note="el mejor coder MoE para 64 GB+"),
    CatModel("glm47", "GLM-4.7 (358B-A32B)", "unsloth/GLM-4.7-GGUF", "UD-Q4_K_XL", 190.52, 358.3, 32.0,
             "glm4moe", note="frontera abierta: código y escritura (≥256 GB)"),
    CatModel("mistral-small-32", "Mistral Small 3.2 (24B)", "unsloth/Mistral-Small-3.2-24B-Instruct-2506-GGUF",
             "UD-Q4_K_XL", 13.55, 23.6, None, "llama", "mmproj-F16.gguf", 0.82, note="redacción equilibrada"),
    # --- Lectores visuales (OCR / documentos) ---------------------------------------------
    CatModel("paddleocr-vl", "PaddleOCR-VL 1.6 (0.9B)", "PaddlePaddle/PaddleOCR-VL-1.6-GGUF",
             "PaddleOCR-VL-1.6-GGUF.gguf", 0.87, 0.9, None, "paddleocr", "PaddleOCR-VL-1.6-GGUF-mmproj.gguf", 0.82,
             kind="vision", note="OCR de documentos muy ligero (experimental con el prompt maestro)"),
    CatModel("qwen3vl-4b", "Qwen3-VL 4B", "Qwen/Qwen3-VL-4B-Instruct-GGUF", "Q4_K_M", 2.33, 4.0, None, "qwen3vl",
             "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf", 0.42, kind="vision", note="lector rápido y fiable"),
    CatModel("qwen25vl-7b", "Qwen2.5-VL 7B", "ggml-org/Qwen2.5-VL-7B-Instruct-GGUF", "Q4_K_M", 4.36, 7.6, None,
             "qwen2vl", "mmproj-Qwen2.5-VL-7B-Instruct-Q8_0.gguf", 0.79, kind="vision",
             note="muy bueno con tablas escaneadas (probado en este equipo)"),
    CatModel("qwen3vl-8b", "Qwen3-VL 8B", "Qwen/Qwen3-VL-8B-Instruct-GGUF", "Q4_K_M", 4.68, 8.2, None, "qwen3vl",
             "mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf", 0.7, kind="vision", note="lector de gama media, mejor OCR"),
    CatModel("qwen3vl-30b-a3b", "Qwen3-VL 30B-A3B", "Qwen/Qwen3-VL-30B-A3B-Instruct-GGUF", "Q4_K_M", 17.28, 30.5,
             3.0, "qwen3vlmoe", "mmproj-Qwen3VL-30B-A3B-Instruct-Q8_0.gguf", 0.66, kind="vision",
             note="lector MoE de alta calidad"),
    CatModel("qwen3vl-32b", "Qwen3-VL 32B", "Qwen/Qwen3-VL-32B-Instruct-GGUF", "Q4_K_M", 18.4, 32.8, None,
             "qwen3vl", "mmproj-Qwen3VL-32B-Instruct-Q8_0.gguf", 0.72, kind="vision",
             note="el mejor lector denso (necesita 24 GB de VRAM para ir fluido)"),
]}


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    desc: str
    candidates: list[str]            # de menor a mayor calidad
    needs_vision: bool = False
    profile: dict = field(default_factory=dict)
    cloud: str = ""


CATEGORIES = [
    Category("utilidad", "Utilidad",
             "Asistente del día a día: preguntas rápidas, resúmenes, traducir, reescribir correos, extraer datos.",
             ["qwen35-0.8b", "qwen35-2b", "qwen35-4b", "qwen35-9b", "qwen36-35b-a3b", "qwen35-122b-a10b",
              "qwen35-397b-a17b"],
             profile={"max_tokens": 2048, "temperature": 0.7, "thinking": False},
             cloud="En local va muy bien. Una IA en la nube (p. ej. Claude) solo aporta si necesitas información "
                   "actualizada de internet o respuestas sobre temas muy especializados."),
    Category("narracion", "Narración",
             "Escritura creativa: relatos, rol, guiones, diálogos, mundos. Prima el estilo y la coherencia.",
             ["gemma4-e2b", "gemma4-e4b", "gemma4-12b", "mistral-small-32", "gemma4-31b", "qwen35-122b-a10b",
              "glm47"],
             profile={"max_tokens": 4096, "temperature": 0.9, "thinking": False},
             cloud="Local es ideal para borradores, rol y privacidad. Para novelas largas con continuidad entre "
                   "capítulos o edición de estilo muy fina, un modelo como Claude mantiene mejor la coherencia."),
    Category("programacion", "Programación",
             "Escribir, explicar y corregir código; generar páginas y scripts (también con /zero).",
             ["qwen35-4b", "qwen35-9b", "qwen3-coder-30b", "glm47-flash", "qwen36-35b-a3b", "devstral2",
              "qwen36-27b", "qwen3-coder-next", "glm47", "qwen35-397b-a17b"],
             profile={"max_tokens": 12288, "temperature": 0.6, "thinking": False},
             cloud="Local sirve para archivos sueltos, snippets, páginas sencillas y código privado. Para proyectos "
                   "de varios archivos, refactors grandes, depuración en un repositorio real o agentes que ejecutan "
                   "comandos, Claude (o Claude Code) es claramente superior."),
    Category("diseno", "Diseño",
             "Diseño web/UI: maquetar páginas, revisar capturas y mockups (usa visión). La generación de imágenes "
             "(Stable Diffusion, FLUX) necesita otra herramienta como ComfyUI.",
             ["qwen35-4b", "qwen35-9b", "qwen36-35b-a3b", "gemma4-31b", "qwen36-27b", "qwen35-122b-a10b",
              "qwen35-397b-a17b"],
             needs_vision=True,
             profile={"max_tokens": 12288, "temperature": 0.6, "thinking": False},
             cloud="Local maqueta páginas correctas; el buen gusto visual, los layouts complejos y la crítica de "
                   "diseño a partir de capturas siguen siendo mucho mejores en modelos como Claude."),
    Category("cad", "CAD",
             "Modelado paramétrico por código (OpenSCAD, CadQuery, FreeCAD Python), cálculos de piezas y "
             "tolerancias. Necesita razonamiento espacial: se activa el pensamiento.",
             ["qwen35-9b", "qwen36-35b-a3b", "qwen36-27b", "qwen3-coder-next", "qwen35-122b-a10b",
              "qwen35-397b-a17b"],
             profile={"max_tokens": 8192, "temperature": 0.4, "thinking": True},
             cloud="El razonamiento espacial es el punto débil de los modelos locales: úsalos para piezas simples y "
                   "scripts. Para ensamblajes, geometría precisa o revisar un diseño, usa Claude o el propio "
                   "software CAD. La generación 3D por IA (Hunyuan3D) va por ComfyUI, no por este CLI."),
    Category("lectura", "Lectura de archivos",
             "El 'lector visual': transcribe imágenes y PDFs escaneados para que tu modelo de chat los trabaje.",
             ["paddleocr-vl", "qwen3vl-4b", "qwen25vl-7b", "qwen3vl-8b", "qwen3vl-30b-a3b", "qwen3vl-32b"],
             needs_vision=True,
             cloud="Local funciona muy bien con escaneos limpios y protege tu privacidad. Para manuscritos, "
                   "tablas muy complejas o PDFs de cientos de páginas, Claude lee con más fiabilidad."),
    Category("multi", "Multifuncional",
             "Un solo modelo que lo hace todo razonablemente: chat, código, visión y redacción.",
             ["gemma4-e4b", "qwen35-9b", "qwen36-35b-a3b", "gemma4-31b", "qwen35-122b-a10b", "qwen35-397b-a17b"],
             needs_vision=True,
             profile={"max_tokens": 4096, "temperature": 0.7, "thinking": False},
             cloud="Un multifuncional local cubre el 80% del uso diario. Para trabajo crítico (legal, médico, "
                   "financiero) o tareas largas de varios pasos, compara con Claude."),
]

TIERS = {
    "basico": ("Básico", "Rápido y ligero: respuestas al instante, deja la máquina libre. Menos profundo."),
    "moderado": ("Moderado", "Equilibrio: la mejor calidad que aún va cómoda (≈8 t/s o más)."),
    "extensivo": ("Extensivo", "Máxima calidad que cabe en tu memoria, aunque sea lento. Para tareas que dejas "
                               "trabajando o donde la calidad importa más que el tiempo."),
}


def category(key: str) -> Category:
    return next(c for c in CATEGORIES if c.key == key)
