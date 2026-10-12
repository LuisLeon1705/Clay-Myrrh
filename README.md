<img src="localai/assets/clay-myrrh.png" alt="" width="96" align="right">

**Español** · [English](README.en.md)

# Clay-Myrrh

**Tu IA, en casa.** *Clay* es arcilla: se moldea a mano y toma la forma que tú le das. *Myrrh*, mirra:
una resina que se guardaba como un tesoro. Aquí, una IA que ajustas a tu equipo, vive en tu ordenador,
funciona sin internet y no comparte nada con nadie.

Un asistente para la terminal que usa **modelos de IA locales**, al estilo de Claude Code o Gemini CLI.
Ejecuta modelos GGUF con [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) o con
[Ollama](https://ollama.com), lee imágenes y PDFs, crea archivos y encadena respuestas largas sin perder
el hilo. Clay-Myrrh no trae un modelo propio: usa los que tú elijas, y el asistente te recomienda los
que mejor van con tu equipo.

**Web:** [luisleon1705.github.io/Clay-Myrrh](https://luisleon1705.github.io/Clay-Myrrh/), con la
[demo en vivo de los componentes](https://luisleon1705.github.io/Clay-Myrrh/demo/). La publica
`.github/workflows/pages.yml` desde `site/`.

Interfaz en español · Windows · Python 3.10+ (probado con 3.14).

## Qué hace
- **Chat en la terminal** con streaming en markdown, razonamiento plegable (`/think`), temas, sesiones
  (`/resume`, `/chat`) y memoria persistente (`Clay-Myrrh.md`, como un CLAUDE.md o GEMINI.md).
- **Ajuste automático de memoria**: estima VRAM y RAM según la arquitectura de cada modelo (`/mem`) y
  elige capas en GPU y contexto (`/auto`).
- **Visión con «lector»**: aunque tu modelo principal no tenga visión, un modelo de visión temporal
  transcribe imágenes y PDFs escaneados para él. El mmproj se empareja solo, por metadatos.
- **Archivos**: lo que escribe el modelo (HTML, código, documentos) se guarda solo (`/archivos`, `/open`,
  `/save`).
- **Modo proyecto** (`/proyecto <carpeta>` o `clay-myrrh -d <carpeta>`): el modelo trabaja directamente sobre
  una carpeta tuya, como un proyecto de Claude Code. Lee, busca, crea y edita archivos con ediciones pequeñas
  (BUSCAR/REEMPLAZAR). Cada cambio guarda una copia: `/deshacer` lo revierte y `/cambios diff` lo muestra.
- **Relevo automático**: si una respuesta no cabe en `/tokens`, el programa la divide en partes y las
  encadena. Cada continuación recibe solo el objetivo, el índice y las últimas líneas, nunca todo lo ya
  escrito, y al final se une todo.
- **Prompt-Zero** (`/zero`): crea páginas web desde un cuestionario, con un análisis de tokens y tareas
  que caben en cada respuesta.
- **Componentes web** (57, ver `/componentes` y `/componentes demo`): el modelo escribe etiquetas `.lai` y el
  programa genera `index.html`, CSS y JS ya probados. Gasta unas 4 veces menos tokens que escribir la web desde
  cero. Se usan solos en el chat cuando pides una página o plantilla, y también con `/plantilla <descripción>`.
- **Asistente de instalación** visual: analiza tu hardware, recomienda modelos por uso y nivel, y los
  descarga con reanudación. Antes de instalar muestra una **guía paso a paso** (pasos cortos que avanzan solos) que compara
  Claude, Gemini, Codex y la IA local. Tiene además una pestaña **Comparativa** (precios, equipo necesario,
  calculadora de amortización, beneficios, problemas y usos reales) y otra **Componentes** con demo en vivo.

## Instalación
1. Instala Python 3.10 o superior.
2. Ejecuta **`instalar-clay-myrrh.bat`**. Comprueba las dependencias y abre el asistente en el navegador. La
   ventana de consola se cierra sola al terminar (o al cerrar la página, salvo durante una descarga).
   También puedes instalar a mano:
   ```bash
   pip install -r requirements.txt
   pip install llama-cpp-python
   ```
   Con `pip install llama-cpp-python` se instala el motor para CPU, o una wheel precompilada para Vulkan o
   CUDA según tu GPU.
3. Coloca los modelos `.gguf` en `models/` (o descárgalos desde el asistente):
   ```
   models/
     llm/        modelos de texto
     vision/     modelos de visión (lector)
     mmproj/     proyectores de visión (mmproj-*.gguf)
     diffusion/  se ignora en el chat
   ```
   Si tienes Ollama abierto, sus modelos también aparecen en `/model`.
4. Comprueba que todo esté en orden:
   ```bash
   python local_ai.py --doctor
   ```

## Uso
Abre el acceso directo **Clay-Myrrh** (lo crea el instalador, con su icono) o, desde una terminal:
```bash
clay-myrrh                                # abre el REPL (clay-myrrh.cmd o el acceso directo)
clay-myrrh -m Qwen3.6 --ctx 32k            # modelo y contexto para esta sesión
clay-myrrh -m Qwen3.6 "explica este error" -f log.txt   # pregunta directa
clay-myrrh --mem -m Qwen3.6                # cuánta VRAM/RAM necesita, sin cargarlo
clay-myrrh -d C:\proyectos\mi-web          # abrir una carpeta como proyecto
clay-myrrh --grafo C:\proyectos\mi-web     # crear su Clay-Myrrh.md con la red y los avisos (sin modelo)
clay-myrrh --setup                         # asistente de instalación
```
Las opciones de la línea de comandos solo
valen para esa sesión. Para cambiar algo de forma permanente,
usa `/settings`.

### Comandos principales
| Comando | Para qué |
|---|---|
| `/model`, `/models` | elegir modelo (con tamaño, visión y memoria) · ver la tabla |
| `/ctx`, `/gpu`, `/kv`, `/auto` | contexto, capas en GPU, caché KV, ajuste automático |
| `/mem` | estimación de memoria antes de cargar |
| `/tokens`, `/temp`, `/top_p`, `/top_k`, `/min_p`, `/params` | longitud y muestreo |
| `/think on\|off\|show\|hide\|last` | razonamiento |
| `/file`, `/image`, `/pdf`, `@ruta` | adjuntar archivos |
| `/vision` | lector visual: modo, modelo, última transcripción |
| `/archivos`, `/open`, `/save`, `/copy`, `/export` | archivos creados y respuestas |
| `/proyecto [ruta\|cerrar\|recargar]` | trabajar sobre una carpeta (el modelo edita sus archivos) |
| `/grafo [ver\|avisos]` | red del proyecto en `Clay-Myrrh.md`: conexiones y avisos con pocos tokens |
| `/cambios [diff]`, `/deshacer` | ver y revertir lo que cambió el modelo en el proyecto |
| `/cadena <archivo \| prompts>` | encadenar varios prompts separados por `---` (ver abajo) |
| `/plantilla <descripción>` | página o plantilla con componentes (HTML, CSS y JS generados) |
| `/zero`, `/componentes [chat auto\|on\|off]` | webs guiadas y catálogo de componentes |
| `/resume`, `/chat`, `/compact`, `/clear` | sesiones y contexto |
| `/memory`, `/system` | instrucciones persistentes y prompt de sistema |
| `/settings`, `/modo`, `/theme` | ajustes, perfiles de uso, colores |
| `/stats`, `/doctor`, `/debug` | métricas y diagnóstico |

Atajos: **Shift+Enter** nueva línea · **Esc** cancelar · **t** mostrar el razonamiento en vivo ·
**?** lista de atajos · **/** autocompleta comandos.

### Relevo automático
Está activo por defecto. Cada respuesta reserva un 15 % de `/tokens` (configurable entre el 10 y el 25 %
en `/settings`) para dejar un índice de lo que falta. Después:
- Si el índice es coherente, el programa pide cada parte pendiente por separado. Con Ollama, las partes
  independientes van en paralelo.
- Si no lo es, continúa desde las últimas líneas.

En ambos casos, el prompt de cada relevo tiene un tamaño constante (~600 tokens) y el resultado final se
une en orden. El registro de cada cadena se guarda en `~/.local_ai/relevos/`.

### Modo proyecto
```text
/proyecto C:\proyectos\mi-tienda
> cambia el título a «Tienda Aurora» y haz que el botón de comprar muestre una alerta
```
- **Contexto:** el modelo recibe la red del proyecto (ver abajo) o la estructura de la carpeta, sin
  `node_modules`, `.git`, compilados ni lo que ignore tu `.gitignore`.
- **Herramientas de texto:** las entiende cualquier modelo, no hace falta function calling. El programa
  devuelve los resultados y el modelo sigue, hasta `project_rounds` rondas por mensaje (4 por defecto).
  - `<leer ruta="x"/>` lee un archivo, `<listar ruta="x"/>` lista una carpeta y `<grep>texto</grep>`
    busca en todo el proyecto.
  - `<editar ruta="x">` con pares BUSCAR/REEMPLAZAR cambia solo lo necesario.
  - Los bloques ```` ```lenguaje ruta ```` o `<crear ruta="x">…</crear>` crean archivos nuevos;
    `<borrar ruta="x"/>` borra uno.
- **Tolerancia:** también acepta pares BUSCAR/REEMPLAZAR sin ruta (deduce el archivo por su contenido) y
  llamadas JSON tipo `read_file` o `str_replace`. Si el modelo anuncia un cambio sin hacerlo, se lo
  recuerda.
- **Seguridad:**
  - Nada se escribe fuera de la carpeta.
  - Se rechaza reescribir un archivo con «… resto igual».
  - Un bloque cortado no sobrescribe un archivo existente.
  - Las copias se guardan en `~/.local_ai/respaldos/`.
- **Instrucciones del proyecto:** `Clay-Myrrh.md` en la carpeta (`/grafo` lo crea, o `/memory edit proyecto`).
  El antiguo `LOCAL_AI.md` se sigue leyendo.

### Red del proyecto (`/grafo`)
Al estilo de Graphify: un mapa compacto del proyecto para que el modelo sepa qué hay y cómo encaja **sin
leerlo todo**. Lo genera el programa (sin modelo y en menos de un segundo) leyendo el código:

- **Cada archivo en una línea:** qué usa (`require`/`import`), qué exporta, sus rutas (con el prefijo de
  `app.use`), vistas, formularios, tablas SQL y variables de entorno. También Docker, docker-compose,
  `.env`, `package.json`, plantillas EJS/HTML y Python (Flask/FastAPI).
- **Avisos:** comprueba que las piezas encajan. Por ejemplo, un formulario que envía a una ruta que no
  existe (y si falta un prefijo de montaje), una vista que usa una variable que `res.render` no le pasa, una
  tabla sin las columnas que pide una librería, un `COPY` del Dockerfile sin origen, un script que apunta a
  un archivo inexistente o una variable de entorno sin definir.
- **Ahorro:** en el Tablón de prueba, ≈1.100 tokens frente a ≈9.500 de leerlo todo. En este repositorio,
  ≈2.100 frente a ≈249.000.

| Comando | Qué hace |
|---|---|
| `/grafo` | crea o actualiza la red dentro de `Clay-Myrrh.md` (tus instrucciones se conservan) |
| `/grafo ver` · `/grafo avisos` | muestra la red o solo los avisos |
| `/set project_context grafo\|archivos` | qué recibe el modelo en un proyecto (por defecto, `grafo`) |

Con `project_context grafo`, el modelo recibe la red al abrir el proyecto. En cada mensaje le llegan solo
las líneas de la red que cambiaron, los archivos que ya conocía y han cambiado y los que nombras tú. El
resto los lee con `<leer/>` cuando los necesita. En proyectos muy pequeños se adjunta todo, porque sale
más barato. Con `archivos` se vuelve al comportamiento anterior: se adjuntan todos los archivos pequeños
nuevos o cambiados.

### Cadenas de prompts
Para tareas largas que se hacen por pasos (y no quieres esperar delante de la pantalla):

```text
/cadena mis-pasos.txt
```

- **Formato:** el archivo lleva los prompts separados por una línea `---`. También puedes escribirlos o
  pegarlos tras `/cadena`, con Shift+Enter para los saltos de línea.
- **Comandos:** las primeras líneas de un paso que empiezan por `/` se ejecutan como comandos (por ejemplo
  `/tokens 8000`).
- **Cuándo se detiene:** cada paso se envía cuando termina el anterior, con el mismo historial y el mismo
  proyecto. Si un paso falla o pulsas Esc, la cadena se detiene y te dice cómo seguir
  (`/cadena mis-pasos.txt desde 3`).
- **Informe:** al terminar se guarda en `~/.local_ai/cadenas/`, con cada prompt, su respuesta y cuánto tardó.
- **Desde la terminal:** `clay-myrrh -d carpeta --cadena pasos.txt [--desde N] [--salir] -y`. Con `--salir`
  se cierra al acabar; sin él, se queda en el chat para seguir.

### Componentes para plantillas
`/plantilla landing de una barbería con servicios, equipo, reservas y mapa` hace que el modelo escriba solo
`pagina.lai` y el programa genere la web. Además de los clásicos (navbar, hero, cards, pricing, faq,
gallery, products + carrito, table, gantt, crud…) hay:

| Tipo | Componentes |
|---|---|
| Estructura | `banner`, `float` (WhatsApp, volver arriba, modo oscuro), `cookies`, `modal` |
| Contenido | `features`, `team`, `logos`, `tabs`, `carousel`, `beforeafter`, `video`, `map` |
| Negocio | `menu` (carta), `hours` («abierto ahora»), `compare`, `invoice` (presupuesto imprimible), `progress` (metas), `countdown`, `newsletter`, `pricing` con interruptor mensual/anual |
| Formularios | `form`, `wizard` (por pasos), `login` (simulado), `quiz`, `poll`, `calculator` (fórmula propia), `chatbot` (preguntas frecuentes) |
| Datos y apps | `chart` (barras, línea, tarta, donut), `kanban`, `todo`, `calendar`, `expenses` (gastos) |

- **En el chat**, los componentes se activan solos cuando pides una página (`/componentes chat auto`).
  Solo se documentan en detalle los básicos, los que encajan con tu petición y los que ya usa la página;
  del resto va solo el nombre. Son ≈2.500 tokens en lugar de ≈5.000.
- **`/componentes demo`** abre una página de ejemplo con 36 componentes. Su `pagina.lai` sirve de chuleta.
- **`/zero`** tiene 12 tipos de web (restaurante, negocio con citas, evento, curso, ONG, app de
  productividad…). Además, ofrece extras opcionales (WhatsApp, chat, cookies, condiciones, presupuesto) y un
  login simulado sin servidor.

## Dónde se guardan las cosas
Todo el estado vive en `~/.local_ai/` (fuera de este repositorio):

| Ruta | Contenido |
|---|---|
| `config.json` | ajustes |
| `sessions/` | conversaciones guardadas y autoguardadas |
| `archivos/<sesión>/` | archivos creados por el modelo |
| `proyectos/` | proyectos de `/zero` |
| `transcripciones/` | caché del lector visual |
| `relevos/` | registros del relevo |
| `cadenas/` | informes de `/cadena` |
| `respaldos/<sesión>/` | copias de los archivos del proyecto antes de cada cambio del modelo |
| `crash.log` | errores no controlados |

## Problemas frecuentes
- **GPU AMD o Intel**: se detecta por el registro de Windows y la VRAM libre por los contadores del
  sistema. Ojo: `pip install llama-cpp-python` instala la versión solo CPU; `/doctor` explica cómo
  conseguir la de Vulkan (o usar Ollama).
- **Va lento o falta VRAM**: usa `/mem` y `/auto`. Un juego o un fondo animado en la GPU puede reducir
  mucho la velocidad.
- **El modelo no ve imágenes**: `/doctor` indica qué mmproj falta y cuál descargar. El programa nunca
  descarga nada sin tu permiso.
- **Una respuesta se corta**: el relevo la continúa solo. Si lo has desactivado, sube `/tokens`.

## Identidad
- **Nombre:** Clay-Myrrh (antes Querencia y Local AI) · **lema:** «tu IA, en casa» · **colores:** violeta `#7c3aed` → cian `#06b6d4`.
- **Icono:** un chip con una casa (`localai/assets/clay-myrrh.ico`, `.png` y `.svg`). Es el mismo en los
  accesos directos, la barra del navegador del asistente y la guía.
- **Accesos directos:** los `.bat` no admiten icono propio, así que el instalador crea `Clay-Myrrh.lnk` e
  `Instalar Clay-Myrrh.lnk` con el icono. Para crearlos también en el escritorio:
  `powershell -ExecutionPolicy Bypass -File localai\assets\accesos.ps1 -Escritorio`.
- **Nombres internos que no cambian** (para no perder datos ni romper rutas): el paquete `localai/`, la
  entrada `local_ai.py` y la carpeta de datos `~/.local_ai/`.

## Estructura del código
El paquete `localai/` contiene la aplicación, los motores, la memoria, la visión, el relevo, el
asistente y los componentes web. El detalle de cada módulo está en [CLAUDE.md](CLAUDE.md), y los
diagnósticos e historial de decisiones en [LEEME.md](LEEME.md).
