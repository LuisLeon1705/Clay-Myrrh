<img src="localai/assets/clay-myrrh.png" alt="" width="96" align="right">

[Español](README.md) · **English**

# Clay-Myrrh

**Your AI, at home.** *Clay* is shaped by hand and takes whatever form you give it. *Myrrh* is a resin
that was kept like a treasure. Here, an AI that you fit to your own computer: it lives on your machine,
works offline and shares nothing with anyone.

A terminal assistant that runs **local AI models**, in the style of Claude Code or Gemini CLI. It runs
GGUF models with [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) or
[Ollama](https://ollama.com), reads images and PDFs, creates files and chains long answers without
losing the thread. Clay-Myrrh doesn't ship a model of its own: it uses the ones you choose, and the setup
assistant recommends the ones that best fit your hardware.

**Website:** [luisleon1705.github.io/Clay-Myrrh/en](https://luisleon1705.github.io/Clay-Myrrh/en/), with a
[live demo of the components](https://luisleon1705.github.io/Clay-Myrrh/demo/). It is published by
`.github/workflows/pages.yml` from `site/`.

> **Language note:** the interface, the commands and the messages are in **Spanish** (for example
> `/proyecto`, `/deshacer`, `<leer/>`). This guide explains each one in English. The models themselves can
> answer in any language you write to them.

Windows · Python 3.10+ (tested with 3.14).

## What it does
- **Terminal chat** with streamed markdown, collapsible reasoning (`/think`), themes, sessions
  (`/resume`, `/chat`) and persistent memory (`Clay-Myrrh.md`, like a CLAUDE.md or GEMINI.md).
- **Automatic memory fitting**: estimates VRAM and RAM from each model's architecture (`/mem`) and picks
  the GPU layers and context size (`/auto`).
- **Vision through a "reader"**: even if your main model has no vision, a temporary vision model
  transcribes images and scanned PDFs for it. The mmproj file is matched automatically, by metadata.
- **Files**: whatever the model writes (HTML, code, documents) is saved automatically (`/archivos`,
  `/open`, `/save`).
- **Project mode** (`/proyecto <folder>` or `clay-myrrh -d <folder>`): the model works directly on one of
  your folders, like a Claude Code project. It reads, searches, creates and edits files with small edits
  (BUSCAR/REEMPLAZAR, i.e. search/replace). Every change keeps a backup: `/deshacer` (undo) reverts it and
  `/cambios diff` (changes) shows it.
- **Automatic relay**: if an answer doesn't fit in `/tokens`, the program splits it into parts and chains
  them. Each continuation only gets the goal, the outline and the last lines, never everything written so
  far, and everything is joined at the end.
- **Prompt-Zero** (`/zero`): builds web pages from a questionnaire, with a token budget and tasks that fit
  in each answer.
- **Web components** (57, see `/componentes` and `/componentes demo`): the model writes `.lai` tags and the
  program generates a tested `index.html`, CSS and JS. It uses about 4 times fewer tokens than writing the
  page from scratch. They kick in automatically when you ask for a page or template in the chat, and also
  with `/plantilla <description>`.
- **Visual setup assistant**: analyses your hardware, recommends models by use and level, and downloads
  them with resume support. Before installing it shows a **step-by-step guide** (short steps that advance
  on their own) comparing Claude, Gemini, Codex and local AI. It also has a **Comparison** tab (prices,
  required hardware, payback calculator, benefits, drawbacks and real uses) and a **Components** tab with a
  live demo.

## Installation
1. Install Python 3.10 or later.
2. Run **`instalar-clay-myrrh.bat`**. It checks the dependencies and opens the assistant in your browser.
   The console window closes by itself when it finishes (or when you close the page, except during a
   download). You can also install by hand:
   ```bash
   pip install -r requirements.txt
   pip install llama-cpp-python
   ```
   `pip install llama-cpp-python` installs the CPU engine, or a prebuilt wheel for Vulkan or CUDA
   depending on your GPU.
3. Put the `.gguf` models in `models/` (or download them from the assistant):
   ```
   models/
     llm/        text models
     vision/     vision models (reader)
     mmproj/     vision projectors (mmproj-*.gguf)
     diffusion/  ignored by the chat
   ```
   If Ollama is running, its models also show up in `/model`.
4. Check that everything is in order:
   ```bash
   python local_ai.py --doctor
   ```

## Usage
Open the **Clay-Myrrh** shortcut (the installer creates it, with its icon) or, from a terminal:
```bash
clay-myrrh                                # opens the REPL (clay-myrrh.cmd or the shortcut)
clay-myrrh -m Qwen3.6 --ctx 32k            # model and context for this session
clay-myrrh -m Qwen3.6 "explain this error" -f log.txt   # one-shot question
clay-myrrh --mem -m Qwen3.6                # how much VRAM/RAM it needs, without loading it
clay-myrrh -d C:\projects\my-site          # open a folder as a project
clay-myrrh --grafo C:\projects\my-site     # create its Clay-Myrrh.md with the map and warnings (no model)
clay-myrrh --setup                         # setup assistant
```
Command-line options only apply to that session. To change something permanently, use `/settings`.

### Main commands
| Command | What for |
|---|---|
| `/model`, `/models` | choose a model (with size, vision and memory) · see the table |
| `/ctx`, `/gpu`, `/kv`, `/auto` | context, GPU layers, KV cache, automatic fitting |
| `/mem` | memory estimate before loading |
| `/tokens`, `/temp`, `/top_p`, `/top_k`, `/min_p`, `/params` | length and sampling |
| `/think on\|off\|show\|hide\|last` | reasoning |
| `/file`, `/image`, `/pdf`, `@path` | attach files |
| `/vision` | visual reader: mode, model, last transcription |
| `/archivos`, `/open`, `/save`, `/copy`, `/export` | created files and answers |
| `/proyecto [path\|cerrar\|recargar]` | work on a folder (the model edits its files) |
| `/grafo [ver\|avisos]` | project map in `Clay-Myrrh.md`: connections and warnings with few tokens |
| `/cambios [diff]`, `/deshacer` | see and undo what the model changed in the project |
| `/cadena <file \| prompts>` | chain several prompts separated by `---` (see below) |
| `/plantilla <description>` | page or template with components (generated HTML, CSS and JS) |
| `/zero`, `/componentes [chat auto\|on\|off]` | guided websites and component catalogue |
| `/resume`, `/chat`, `/compact`, `/clear` | sessions and context |
| `/memory`, `/system` | persistent instructions and system prompt |
| `/settings`, `/modo`, `/theme` | settings, usage profiles, colours |
| `/stats`, `/doctor`, `/debug` | metrics and diagnostics |

Shortcuts: **Shift+Enter** new line · **Esc** cancel · **t** show the reasoning live · **?** list of
shortcuts · **/** autocompletes commands.

### Automatic relay
It is on by default. Each answer keeps 15 % of `/tokens` (configurable between 10 and 25 % in
`/settings`) to leave an outline of what is missing. Then:
- If the outline makes sense, the program asks for each pending part separately. With Ollama,
  independent parts run in parallel.
- If it doesn't, it continues from the last lines.

Either way, each relay prompt has a constant size (~600 tokens) and the final result is joined in order.
Each chain is logged in `~/.local_ai/relevos/`.

### Project mode
```text
/proyecto C:\projects\my-shop
> change the title to "Aurora Shop" and make the buy button show an alert
```
- **Context:** the model gets the project map (see below) or the folder structure, without
  `node_modules`, `.git`, build output or anything in your `.gitignore`.
- **Text tools:** any model understands them; no function calling needed. The program returns the results
  and the model carries on, for up to `project_rounds` rounds per message (4 by default).
  - `<leer ruta="x"/>` reads a file, `<listar ruta="x"/>` lists a folder and `<grep>text</grep>` searches
    the whole project.
  - `<editar ruta="x">` with BUSCAR/REEMPLAZAR (search/replace) pairs changes only what is needed.
  - ```` ```language path ```` blocks or `<crear ruta="x">…</crear>` create new files; `<borrar ruta="x"/>`
    deletes one.
- **Tolerance:** it also accepts search/replace pairs without a path (it finds the file by its content)
  and JSON calls like `read_file` or `str_replace`. If the model announces a change without making it, it
  gets a reminder.
- **Safety:**
  - Nothing is written outside the folder.
  - Rewriting a file with "… rest unchanged" is rejected.
  - A truncated block never overwrites an existing file.
  - Backups are kept in `~/.local_ai/respaldos/`.
- **Project instructions:** `Clay-Myrrh.md` in the folder (`/grafo` creates it, or `/memory edit
  proyecto`). The older `LOCAL_AI.md` is still read.

### Project map (`/grafo`)
In the style of Graphify: a compact map of the project so the model knows what is there and how it fits
together **without reading everything**. The program builds it by reading the code (no model, in under a
second):

- **One line per file:** what it uses (`require`/`import`), what it exports, its routes (with the
  `app.use` prefix), views, forms, SQL tables and environment variables. It also covers Docker,
  docker-compose, `.env`, `package.json`, EJS/HTML templates and Python (Flask/FastAPI).
- **Warnings:** it checks that the pieces fit. For example, a form posting to a route that doesn't exist
  (and whether a mount prefix is missing), a view using a variable that `res.render` doesn't pass, a table
  without the columns a library needs, a Dockerfile `COPY` with no source, a script pointing to a missing
  file or an undefined environment variable.
- **Savings:** on the test web app, ≈1,100 tokens instead of ≈9,500 to read it all. On this repository,
  ≈2,100 instead of ≈249,000.

| Command | What it does |
|---|---|
| `/grafo` | creates or updates the map inside `Clay-Myrrh.md` (your instructions are kept) |
| `/grafo ver` · `/grafo avisos` | shows the map or only the warnings |
| `/set project_context grafo\|archivos` | what the model gets in a project (`grafo` by default) |

With `project_context grafo`, the model gets the map when the project opens. With each message it only
receives the map lines that changed, the files it already knew that changed, and the ones you mention. It
reads the rest with `<leer/>` when it needs them. Very small projects are attached whole, because that is
cheaper. With `archivos` you get the previous behaviour: every small new or changed file is attached.

### Prompt chains
For long tasks done in steps (when you don't want to wait in front of the screen):

```text
/cadena my-steps.txt
```

- **Format:** the file has the prompts separated by a `---` line. You can also type or paste them after
  `/cadena`, using Shift+Enter for line breaks.
- **Commands:** the first lines of a step that start with `/` run as commands (for example
  `/tokens 8000`).
- **When it stops:** each step is sent when the previous one finishes, with the same history and the same
  project. If a step fails or you press Esc, the chain stops and tells you how to resume
  (`/cadena my-steps.txt desde 3`).
- **Report:** when it finishes, a report is saved in `~/.local_ai/cadenas/` with each prompt, its answer
  and how long it took.
- **From the terminal:** `clay-myrrh -d folder --cadena steps.txt [--desde N] [--salir] -y`. With
  `--salir` it exits at the end; without it, it stays in the chat.

### Components for templates
`/plantilla landing page for a barbershop with services, team, bookings and map` makes the model write
only `pagina.lai` and the program generates the site. Besides the classics (navbar, hero, cards, pricing,
faq, gallery, products + cart, table, gantt, crud…) there are:

| Type | Components |
|---|---|
| Structure | `banner`, `float` (WhatsApp, back to top, dark mode), `cookies`, `modal` |
| Content | `features`, `team`, `logos`, `tabs`, `carousel`, `beforeafter`, `video`, `map` |
| Business | `menu`, `hours` ("open now"), `compare`, `invoice` (printable quote), `progress` (goals), `countdown`, `newsletter`, `pricing` with a monthly/yearly switch |
| Forms | `form`, `wizard` (multi-step), `login` (simulated), `quiz`, `poll`, `calculator` (custom formula), `chatbot` (FAQ) |
| Data and apps | `chart` (bar, line, pie, donut), `kanban`, `todo`, `calendar`, `expenses` |

- **In the chat**, components switch on automatically when you ask for a page (`/componentes chat auto`).
  Only the basic ones, the ones that match your request and the ones the page already uses are documented
  in full; for the rest, only the name is sent. That is ≈2,500 tokens instead of ≈5,000.
- **`/componentes demo`** opens an example page with 36 components. Its `pagina.lai` works as a cheat
  sheet.
- **`/zero`** has 12 website types (restaurant, appointment-based business, event, course, NGO,
  productivity app…). It also offers optional extras (WhatsApp, chat, cookies, terms, quote) and a
  simulated login without a server.

## Where things are stored
All state lives in `~/.local_ai/` (outside this repository):

| Path | Contents |
|---|---|
| `config.json` | settings |
| `sessions/` | saved and autosaved conversations |
| `archivos/<session>/` | files created by the model |
| `proyectos/` | `/zero` projects |
| `transcripciones/` | visual reader cache |
| `relevos/` | relay logs |
| `cadenas/` | `/cadena` reports |
| `respaldos/<session>/` | copies of project files before each change by the model |
| `crash.log` | unhandled errors |

## Troubleshooting
- **AMD or Intel GPU**: it is detected through the Windows registry, and free VRAM through the system
  counters. Note: `pip install llama-cpp-python` installs the CPU-only build; `/doctor` explains how to
  get the Vulkan one (or use Ollama).
- **Slow, or not enough VRAM**: use `/mem` and `/auto`. A game or an animated wallpaper on the GPU can
  slow it down a lot.
- **The model can't see images**: `/doctor` tells you which mmproj is missing and which one to download.
  The program never downloads anything without your permission.
- **An answer gets cut off**: the relay continues it automatically. If you turned it off, raise `/tokens`.

## Identity
- **Name:** Clay-Myrrh (formerly Querencia and Local AI) · **tagline:** "tu IA, en casa" (your AI, at
  home) · **colours:** violet `#7c3aed` → cyan `#06b6d4`.
- **Icon:** a chip with a house (`localai/assets/clay-myrrh.ico`, `.png` and `.svg`). It is the same in
  the shortcuts, the assistant's browser tab and the guide.
- **Shortcuts:** `.bat` files can't have their own icon, so the installer creates `Clay-Myrrh.lnk` and
  `Instalar Clay-Myrrh.lnk` with the icon. To also create them on the desktop:
  `powershell -ExecutionPolicy Bypass -File localai\assets\accesos.ps1 -Escritorio`.
- **Internal names that don't change** (so no data is lost and no paths break): the `localai/` package,
  the `local_ai.py` entry point and the `~/.local_ai/` data folder.

## Code structure
The `localai/` package holds the application, the engines, memory, vision, the relay, the assistant and
the web components. Each module is described in [CLAUDE.md](CLAUDE.md), and diagnostics and the decision
history in [LEEME.md](LEEME.md) (both in Spanish).
