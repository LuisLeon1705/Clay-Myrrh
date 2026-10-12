"""
local_ai.py - CLI de IA local (llama.cpp / Ollama) estilo Claude Code / Gemini CLI.

Se lanza con local-ai.cmd (y el asistente de instalación con setup-local-ai.bat).
El código vive en el paquete localai/; la descripción de cada módulo está en CLAUDE.md.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from localai.app import main  # noqa: E402

if __name__ == "__main__":
    main()
