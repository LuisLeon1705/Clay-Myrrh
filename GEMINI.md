# Local AI CLI: contexto para Gemini CLI

> Mismo contenido que `CLAUDE.md`. Si cambias uno, actualiza el otro.

CLI de IA local en Python (estilo Claude Code / Gemini CLI) que ejecuta modelos GGUF con llama-cpp-python
o con Ollama. Lee imágenes y PDFs, crea archivos y encadena respuestas largas con el «relevo».
La interfaz y los textos son **en español**. Guía de uso completa: `README.md`. Diagnósticos e historial
de decisiones: `LEEME.md`.

## Entorno
- Windows 11, Python 3.14, consola real de Windows (PowerShell / cmd).
- llama-cpp-python 0.3.34 **compilado con Vulkan**, no con CUDA. No asumas CUDA.
- GPU de referencia: RTX 3070 8 GB, 32 GB de RAM DDR4-3600. Ollama opcional en `127.0.0.1:11434`.
- Dependencias: `requirements.txt` (rich, prompt_toolkit, requests, psutil, pymupdf, pillow, nvidia-ml-py).
  El motor (llama-cpp-python) se instala aparte.

## Comandos
```bash
python local_ai.py                      # REPL (equivale a local-ai.cmd)
python local_ai.py --doctor             # diagnóstico: dependencias, backend GPU, modelos, mmproj
python local_ai.py --list               # modelos encontrados
python local_ai.py --mem -m <modelo>    # estimación de VRAM/RAM sin cargar
python local_ai.py -m <modelo> "pregunta"   # one-shot
python local_ai.py --setup              # asistente visual (navegador, 127.0.0.1)
python local_ai.py --setup-cli          # asistente en consola
python local_ai.py -d <carpeta>         # abrir una carpeta como proyecto
python -c "from localai import app"     # comprobación rápida de imports
```
No hay suite de tests formal. Prueba con scripts propios y **aísla la configuración** apuntando
`USERPROFILE` a una carpeta temporal (todo el estado vive en `~/.local_ai`).

## Estructura
```
local_ai.py            entrada mínima → localai.app.main
local-ai.cmd           lanzador de Windows
setup-local-ai.bat     comprueba Python y dependencias, abre el asistente
localai/
  app.py               REPL, comandos (/model, /settings…), run_generation, relay_chain
  config.py            rutas, dataclass Settings, migraciones (settings_version), TRANSIENT
  engine.py            LlamaEngine / OllamaEngine, ThinkParser (canales think/text/relay), FlatMTMDChatHandler
  gguf.py              lector de cabeceras GGUF
  catalog.py           descubrimiento de modelos y emparejamiento de mmproj por metadatos
  memory.py            estimación de VRAM/RAM por arquitectura y auto-ajuste
  hardware.py          detección de hardware y velocidad estimada por ancho de banda
  model_catalog.py     catálogo verificado de modelos para el asistente
  setup_wizard.py      asistente en consola · setup_gui.py + setup_ui/: asistente web
                       (setup_ui/guide.js: guía paso a paso, comparativa y galería; /demo sirve webkit/assets/demo.lai)
  attachments.py       imágenes, PDFs (texto o escaneados) y @menciones
  reader.py            «lector» visual: un VLM transcribe para un modelo de texto (con caché)
  files.py             extracción de bloques/archivos de las respuestas y autoguardado
  relay.py             relevo automático (ver abajo)
  workspace.py         modo proyecto: estructura, herramientas de texto (<leer/>, <editar>…), copias y /deshacer
  zero.py              Prompt-Zero (/zero): cuestionario → plan → tareas
  webkit/              57 componentes web (.lai → index.html + css + js); assets/core.js lleva todo el JS,
                       assets/demo.lai es el escaparate; parser.py corrige errores típicos de modelos pequeños
  render.py            temas, banner, StreamView (streaming con markdown)
  inputbox.py          caja de entrada (prompt_toolkit), selectores, barra inferior
  sessions.py, doctor.py, llamalog.py
models/                llm/ · vision/ · mmproj/ · diffusion/ (los .gguf NO van al repositorio)
```
Estado del usuario (fuera del repositorio): `~/.local_ai/` contiene config.json, sessions/,
archivos/ (lo que crea el modelo), relevos/ (registros de cadenas), respaldos/ (copias del modo
proyecto), transcripciones/, proyectos/ (/zero) y crash.log.

## Reglas
- **Nunca descargues modelos ni mmproj sin preguntar al usuario.** Recomiéndalos y espera su sí.
- Textos visibles, mensajes de error y comentarios en **español**. Los errores deben decir qué hacer
  (`EngineError(msg, hints)`).
- La UI usa **rich** (salida) y **prompt_toolkit** (entrada). Mantén el estilo de `render.tool(...)`,
  `say/warn/error` y StreamView.
- Las opciones de línea de comandos son **transitorias**: no se guardan en config.json
  (`config.TRANSIENT`).
- Si añades un campo a `Settings`, dale un valor por defecto. Si cambia el significado de uno existente,
  sube `settings_version` y migra.
- Edita con herramientas de edición de archivos. **No uses heredocs de shell con escapes** (`\n`, `\b`,
  `\t`) para parchear Python: en Windows han corrompido archivos varias veces (saltos de línea literales,
  caracteres de control, bytes nulos). Después de un parche, comprueba con `ast.parse`.
- Respeta el estilo del código: módulos pequeños, funciones con docstring breve en español y sin
  dependencias nuevas salvo que sea necesario.

## Particularidades técnicas (no las «arregles» sin entenderlas)
- **Consola de Windows**: `suppress_stdout_stderr` de llama-cpp hace `dup2` y cierra el handle de la
  consola (NoConsoleScreenBufferError tras una respuesta con visión). Por eso está el parche `_NoRedirect`
  y `inputbox.refresh_console_handle()`. Los fallos de consola **solo aparecen en una consola real**: la
  E/S simulada no los reproduce.
- **Visión**: se usa `MTMDChatHandler` (libmtmd). `Qwen25VLChatHandler` daba respuestas vacías.
  `FlatMTMDChatHandler` aplana el contenido en lista para plantillas que no lo aceptan (MiniCPM).
  El mmproj se empareja por metadatos (`clip.vision.projection_dim` == `embedding_length`, más
  `projector_type`), no por nombre.
- **Lector visual** (`vision_mode="lector"`): el modelo principal puede no tener visión. Un VLM temporal
  transcribe imágenes y PDFs escaneados con un prompt maestro, y el resultado se guarda en caché.
- **Memoria**: la estimación depende de la arquitectura (qwen35 híbrido SSM, Gemma SWA, KV compartido de
  gemma3n). `ngl` cuenta primero la capa de salida.
- **Relevo** (`relay.py` + `App.relay_chain`), automático y sin comando:
  - Reserva `relay_reserve` % (10–20) de `max_tokens`.
  - Principio de diseño pedido por el usuario: **ninguna continuación relee lo ya escrito**. Cada
    ejecución recibe solo el objetivo, el índice o los encabezados y las últimas líneas. Las partes se
    guardan aparte y se **unifican al final** por id.
  - Si el índice del modelo no es coherente (`plan_ok`), se pasa al modo continuación. Los modelos
    pequeños devuelven índices basura.
  - Registro de cada cadena en `~/.local_ai/relevos/`.
- **Modo proyecto** (`/proyecto`, `-d`): `App.workspace` (workspace.Workspace) hace `chdir` a la carpeta,
  pone su estructura en el prompt de sistema y adjunta al mensaje los archivos pequeños nuevos o cambiados
  (`fresh_context`, por mtime). `App._project_turn` aplica bloques, `<editar>` y llamadas JSON, y devuelve
  lecturas y errores al modelo en rondas (`project_rounds`). Todo cambio pasa por `Workspace._touch`
  (copia en respaldos/) y se agrupa por turno para `/deshacer`. Nunca escribas en el proyecto sin
  pasar por Workspace. Los modelos pequeños no siguen bien el formato: la tolerancia (pares sin ruta,
  JSON, recordatorio si anuncian sin actuar) es deliberada.
- **Componentes en el chat**: `components_in_chat` = auto | on | off. En «auto», `webkit.wants_components`
  detecta si se pide una página y `webkit.chat_doc(petición, página anterior)` entra solo en ese turno:
  detalle de CORE + `KEYWORDS` + los ya usados; del resto solo el nombre (≈2.500 tokens). Si añades un
  componente, ponle palabras clave en `KEYWORDS`, un grupo en `GROUPS` y, si encaja, una sección en
  `zero.SECTION_COMPONENTS`. Un componente
  nuevo = `@comp(...)` en components.py (doc, atributos, CSS y render) + su JS en core.js protegido por
  la presencia de su elemento. Ojo con los `data-*` que ya usa core.js (p. ej. `data-year` pinta el año) y
  con los ids duplicados. Pruébalo renderizando un .lai y abriéndolo con Edge sin ventana.
- **Asistente web**: la página envía `/api/ping` cada 5 s; `run_gui` se apaga si deja de recibirlo 30 s (no
  durante una descarga) y al terminar la instalación la página llama a `/api/quit`. El `.bat` sale sin
  `pause` si todo fue bien, así la ventana se cierra sola.
- **Precios del asistente**: los de Claude/Gemini/Codex y del hardware de guide.js son orientativos y con fecha; si los
  actualizas, cambia también `DATE`. Las mascotas son ilustraciones propias, no logotipos oficiales.
- **Archivos del modelo**: se guardan solos en `~/.local_ai/archivos/<sesión>/`. La ruta puede ir en la
  cabecera del bloque o en su primera línea, y también se extraen heredocs y `tool_call`.
- El rendimiento varía mucho si un juego o Wallpaper Engine ocupa la GPU: eso no es un bug del código.
