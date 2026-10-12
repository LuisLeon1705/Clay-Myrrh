"""
Clay-Myrrh — tu IA, en casa. CLI de IA local (llama.cpp / Ollama) estilo Claude Code / Gemini CLI.

Se lanza con clay-myrrh.cmd o el acceso directo «Clay-Myrrh» (el asistente, con instalar-clay-myrrh.bat).
El código vive en el paquete localai/; la descripción de cada módulo está en CLAUDE.md.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from localai.app import main  # noqa: E402

if __name__ == "__main__":
    main()
