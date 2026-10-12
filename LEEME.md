# Clay-Myrrh v4.0 · notas de desarrollo

> Antes se llamaba «Local AI». Esta página es el diario técnico; la guía de uso está en README.md.

## Asistente de instalación (`instalar-clay-myrrh.bat` o `clay-myrrh --setup`)
Se abre como **página web local en el navegador** (solo en 127.0.0.1): pasos visuales, carriles VRAM/RAM animados, demostración de velocidad que escribe en vivo a N tokens/s, tarjetas por nivel con el reparto GPU/RAM dibujado, glosario (token, VRAM, MoE, cuantización, mmproj…) y descarga con barras de progreso, pausa y reanudación. `clay-myrrh --setup-cli` (o `/setup cli`) abre la versión de consola.

1. Comprueba Python y las librerías (las instala con pip si aceptas).
2. Lee tu hardware: CPU (núcleos, GHz), RAM (GB, tipo, **MT/s**, canales), GPU (VRAM y ancho de banda) y
   disco libre. Puedes corregir cualquier dato o **simular otro equipo**, desde 8 GB sin GPU hasta un servidor
   de 512 GB o más.
3. Eliges usos: Utilidad, Narración, Programación, Diseño, CAD, Lectura de archivos y Multifuncional.
4. Para cada uso ves los niveles **Básico / Moderado / Extensivo**, cada uno con el modelo que corresponde a
   TU equipo, su tamaño, dónde corre (GPU, GPU+RAM o CPU), la velocidad aproximada en t/s y cuándo te
   conviene más una IA en la nube como Claude. También puedes reutilizar un modelo que ya tengas.
5. Guarda el plan, descarga lo elegido (`clay-myrrh --download`, se puede reanudar) y deja la configuración:
   modelo principal, lector visual y un perfil por uso. Con `/modo programacion` (o la categoría que sea)
   cambias de modelo y ajustes.

Las velocidades se estiman por ancho de banda de memoria, con eficiencias calibradas en este equipo
(denso en GPU ≈55 %, denso en RAM ≈50 %, MoE en RAM ≈18 %). Son aproximadas (±30 %).

## Prompt-Zero (`/zero`): webs desde cero, guiadas
- Preguntas con flechas: tipo de web, frontend, login, base de datos, servidor (solo si hace falta), cómo
  construirla, estilo, secciones e idioma.
- **Análisis de tokens**: contexto, salida máxima y velocidad de tu modelo; reparte el trabajo en tareas
  que caben en una respuesta y estima el tiempo.
- **Componentes prediseñados** (recomendado en webs sin servidor): el modelo solo escribe `pagina.lai` con
  etiquetas y contenido, y el programa genera `index.html` + `css/styles.css` + `js/app.js`. En las pruebas
  fueron **≈2.100 tokens frente a ≈9.300** desde cero: 33 s frente a 9,5 min con el mismo modelo.
- **Desde cero**: tarea 0 = `CONTRATO.md` (ids, clases, endpoints) y luego tareas de hasta 4 archivos, cada
  una con la especificación, el contrato y un resumen de lo ya hecho. Si una respuesta se corta, se reintenta.
- Los proyectos se guardan en `~/.local_ai/proyectos/<nombre>/` (las respuestas en bruto en `.zero/`).
  `/zero continuar` retoma uno a medias. Después, los cambios que pidas en el chat se guardan en esa carpeta.

## Componentes (`/componentes`, `/componentes demo`, `/plantilla`)
57 componentes en 5 grupos (estructura, contenido, negocio, formularios, datos y apps); la lista completa está
en el README y en la pestaña «Componentes» del asistente. 4 temas (`minimal`, `dark`, `corporate`, `colorful`)
más `accent`, `font` y `currency`.
- `<gantt>` solo necesita duración y precedencias (`<task id="3" days="15" after="2">`): calcula inicios,
  fines, **ruta crítica** y fechas laborables (con `start-date` y `holidays`).
- `<crud>`, `<kanban>`, `<todo>` y `<expenses>` guardan sus datos en el navegador.
- Cualquier `pagina.lai` que escriba el modelo se convierte solo en la página. En el chat los componentes se
  activan solos cuando pides una página (`/componentes chat auto|on|off`); el catálogo es selectivo (≈2.500
  tokens). El lector de `.lai` corrige errores típicos de los modelos pequeños (`=item`, `<title>` sueltos,
  secciones que envuelven componentes, textos en atributos).

## Relevo automático (sin comando)
Cada respuesta reserva un 15 % de `/tokens` (ajustable 10–25 % en `/settings`). Si la tarea no cabe:
1. El modelo deja (o se le pide con la reserva) un `<relevo>` con el **índice**: `id | título | hecho/cortada/pendiente`
   y el formato. Para pedirlo solo recibe el objetivo, los encabezados escritos y las últimas líneas.
2. Si el índice es coherente, el programa **encarga cada parte pendiente por separado** (objetivo + índice +
   «escribe la parte N»); las partes de `<ramas>` van en paralelo con Ollama. Si el índice no sirve (modelos
   pequeños), sigue en **modo continuación**: objetivo + encabezados ya escritos + últimas líneas.
3. Ninguna ejecución relee lo ya escrito: el prompt de cada relevo es constante (~600 tokens). Lo cortado se
   continúa desde su última palabra (no se descarta), se quitan solapes y secciones repetidas, y todo se
   **unifica al final** en el orden del índice.
- Registro de cada cadena (tokens de salida y de prompt por ejecución): `~/.local_ai/relevos/<sesión>/`.
- Prueba (4B, `/tokens 600`, guía de 6 secciones): 4 ejecuciones, 2.269 tokens de prompt en total; antes
  eran 7 ejecuciones y 8.563 tokens, y crecían con cada relevo porque cada una releía todo lo anterior.

## Detección de GPU unificada · AMD e Intel (octubre 2026)
- Bug: con una RX 6800 el asistente veía 16 GB (registro) y el programa 0 GB (solo NVML/nvidia-smi), así que
  el ajuste automático ponía 0 capas en la GPU. Ahora ambos usan `localai/gpu.py`.
- VRAM libre de AMD/Intel con PDH en inglés (funciona con Windows en español); comprobado aquí contra NVML:
  1.591 MiB del contador frente a 1.756 MiB de NVML (se descuentan 192 MiB de reserva del controlador).
- Prueba simulada (RX 6800 + integrada de 512 MB): 33/33 capas del 9B en GPU, 25/41 del 35B MoE; `/doctor`
  explica cómo conseguir el motor Vulkan si el instalado es solo CPU. La RTX 3070 real sigue por NVML.

## 4.1 · interfaz en inglés y arreglo del asistente (octubre 2026)
- Todos los mensajes de la consola, `/doctor`, `/zero`, el asistente de consola y el asistente web (guía,
  comparativa, catálogo de modelos) en español o inglés según `language`. Técnica: `T(es, en)` junto a cada
  texto (Python) y `data-en` / `t(es, en)` (web), para que nunca se desincronicen. Con la interfaz en inglés,
  el prompt de sistema por defecto pide respuestas en inglés. Las instrucciones al modelo siguen en español.
- Asistente: si la pestaña estaba en segundo plano durante la descarga, el navegador frenaba los latidos
  (1 por minuto) y el servidor se apagaba al terminar, antes de que la página viera el final. Ahora espera a
  que la página vea el resultado (hasta 30 min), el margen sin latidos es de 3 min y la página se pone al día
  al volver a la pestaña. La consola avisa cuando termina la descarga.

## 4.0.1 · comandos en español o inglés (octubre 2026)
- Ajuste `language` (auto = idioma de Windows · es · en), en `/ajustes`, `/set language` y `--lang`. Menú,
  autocompletado, `/ayuda`, consejos del banner y caja de entrada en el idioma elegido; los nombres del otro
  idioma siguen funcionando como alias ocultos (`/deshacer` = `/undo`, `/vision lector` = `/vision reader`).
- Todos los comandos tienen nombre en los dos idiomas (`/ayuda`·`/help`, `/modelo`·`/model`,
  `/ajustes`·`/settings`…). Opciones en inglés: `--chain`, `--from`, `--exit`, `--graph`.
- El autocompletado de comandos con ruta (`/proyecto`, `/cadena`…) ya sugiere también sus opciones.

## Red del proyecto y Clay-Myrrh.md (octubre 2026)
- `/grafo` genera una red del proyecto al estilo de Graphify, sin modelo. Incluye una línea por archivo con
  sus conexiones y avisos de piezas que no encajan, y la guarda en `Clay-Myrrh.md`. Ese archivo sustituye
  a `LOCAL_AI.md`, que se sigue leyendo.
- Motivo: en la cadena del Tablón, el paso de revisión no detectó un router montado sin prefijo, una tabla
  `session` con columnas inventadas, un `package.json` en `src/` ni un `csrfToken` que no llegaba a las
  vistas. Los avisos de la red detectan los cuatro.
- Robustez del modo proyecto (vista en la prueba): un bloque sin cerrar ya no se traga el siguiente, los
  bloques que solo traen ediciones no se escriben como archivo, «borro y reescribo» no acaba borrando, un
  README con bloques dentro no se corta y se acepta `<crear ruta="x">…</crear>`.
- `clay-myrrh --grafo carpeta` genera la red sin cargar modelo.
- `project_context=grafo` (por defecto): la red sustituye al árbol y ya no se adjuntan todos los archivos,
  así que la primera petición en un proyecto mediano gasta bastantes menos tokens.

## Cadenas de prompts (octubre 2026)
- `/cadena archivo` y `--cadena archivo`: varios prompts separados por `---` que se ejecutan seguidos, con
  parada si un paso falla, reanudación con `desde N` e informe en `~/.local_ai/cadenas/`. Primera prueba
  real: la app «Tablón» (Express + PostgreSQL + Docker, login y comentarios) en 4 pasos, en modo proyecto.

## Capas reales en la GPU (octubre 2026)
- Caso real: RX 6800 con todo en RAM y ~1 GB de VRAM al cargar Qwen 27B/30B. Clay-Myrrh mostraba las capas
  *planificadas* y además guardaba una calibración falsa (0,8). Ahora lee el log de llama.cpp
  (`ggml_vulkan: … uma`, `using device … MiB free`, `offloaded N/M layers`): el mensaje de «listo» muestra las
  capas reales y, si quedaron muchas menos de las previstas, explica la causa (motor solo CPU → cómo conseguir
  Vulkan; Vulkan que solo ve la integrada → controlador / GGML_VK_VISIBLE_DEVICES; poca VRAM libre). Si el
  motor es solo CPU, avisa también antes de cargar.

## Nombre e icono (octubre 2026)
- «Local AI» pasó a llamarse **Querencia** y después **Clay-Myrrh** («tu IA, en casa»; arcilla que se
  moldea y mirra que se guarda), versión 4.0. Lanzador `clay-myrrh.cmd` (los alias
  `querencia.cmd` y `local-ai.cmd` se quitaron antes del primer release), instalador `instalar-clay-myrrh.bat` y accesos directos con icono.
- Icono generado con Pillow (`localai/assets/clay-myrrh.ico`, 16–256 px; sin patillas en 16–32 px para que
  se lea en la barra de tareas) y el mismo dibujo en SVG como favicon del asistente.

## Asistente con guía y comparativa · 57 componentes (octubre 2026)
- `instalar-clay-myrrh.bat`: antes de instalar se abre una guía paso a paso (pasos cortos que avanzan solos) con Claude, Gemini,
  Codex y la IA local. Enlaces directos: `#guia-3`, `#comparativa`, `#componentes`, `#instalacion`.
- Precios a octubre de 2026 de fuentes de terceros (verificar): Claude Pro 20 $ · Max 100/200 $; Gemini AI Plus
  4,99 $ (modelos Flash) · AI Pro 19,99 $ en adelante para el resto; Codex con ChatGPT Go 8 $ · Plus 20 $ · Pro 100/200 $.
- Equipo equiparable (octubre de 2026, la memoria y las GPU subieron mucho): gama media 2.500–5.000 $ (RTX 3090
  usada ≈ 1.000–1.500 $), gama alta 3.300–6.000 $ (mini PC Ryzen AI Max+ de 128 GB ≈ 3.300–3.900 $). Cada nivel
  indica con qué modelo de la nube compite (aprox.; p. ej. ~30B ≈ Gemini 1.5 Flash / GPT-4o mini). El primer
  nivel es el equipo detectado o simulado.
- La ventana del `.bat` se cierra sola al terminar la instalación o 30 s después de cerrar la página (salvo
  durante una descarga).
- Componentes probados con Edge sin ventana: 21/21 comprobaciones nuevas + 15/15 anteriores.

## Modo proyecto y plantillas (octubre 2026)
- `/proyecto <carpeta>` o `clay-myrrh -d <carpeta>`: el modelo edita esa carpeta directamente. Detalles en el
  README («Modo proyecto»). Prueba con el 4B en CPU: título, color y alerta en 3 archivos, aplicados en un
  solo turno. El modelo escribió los pares BUSCAR/REEMPLAZAR sin `<editar>` y el programa dedujo cada archivo
  por su contenido.
- 17 componentes nuevos (44 en ese momento; hoy son 57). Se probaron todos en una página de demo con Edge sin ventana:
  15/15 comprobaciones de interacción (pestañas, kanban, tareas, calendario, validación, login, filtro de
  carta…) y diseño revisado a 1280 px y a 390 px.
- Relevo: la continuación recorta hasta la última frase completa (no a mitad de palabra) y recibe el inicio
  de cada párrafo de la sección en curso, para no repetir ideas.

## Novedades de la v3.1

**Lector visual** (`/vision`). Tu modelo de chat no necesita mmproj. Al adjuntar una imagen o un PDF escaneado:
1. se descarga temporalmente tu modelo (si no caben los dos en la GPU);
2. se enciende el mejor modelo con visión instalado (ahora Qwen2.5-VL-7B) con un prompt maestro de
   transcripción literal: tablas completas, `[ilegible]`, `[tachado]`, figuras descritas, sin resolver nada;
3. se apaga y vuelve tu modelo, que trabaja con ese texto (queda en la conversación para preguntas posteriores).

Las transcripciones se guardan en `~/.local_ai/transcripciones/`: el mismo archivo no se vuelve a leer.
`/vision directo` envía las imágenes al modelo de chat si tiene mmproj; `/vision modelo <nombre>` elige el lector;
`/vision ver` muestra la última transcripción.

**Archivos sin preguntar.** Lo que crea el modelo se guarda solo en `~/.local_ai/archivos/<sesión>/`.
`/open` abre el último archivo (un HTML se abre en el navegador), `/archivos` los lista y `/save <ruta>` copia
el código a otra carpeta. Si la respuesta se corta por longitud, el archivo se marca como **incompleto**.

**Interfaz al estilo Gemini CLI:**
- logo y consejos al arrancar; la tabla de memoria solo aparece si el modelo no cabe;
- respuestas con `✦` y tu mensaje en un bloque gris;
- cajas de herramienta (leer, transcribir, crear archivo);
- frases de espera con "esc para cancelar";
- pie de una línea (carpeta | modelo · % de contexto libre · VRAM);
- `?` muestra los atajos y las confirmaciones se eligen con flechas;
- `/help` por categorías y menú `/settings`.

```
clay-myrrh                      # interactivo (último modelo usado)
clay-myrrh -m Qwen2-VL-2B       # elegir modelo (nombre parcial, ruta .gguf o modelo de Ollama "llava:7b")
clay-myrrh "pregunta" -p doc.pdf -i foto.png -f code.py   # one-shot
clay-myrrh --list | --doctor | --mem -m <modelo> --ctx 16k
```

Opciones de una sola ejecución (no se guardan): `--ctx n|32k|auto`, `--gpu n|auto|max`, `--kv f16|q8_0|q4_0`,
`--tokens n`, `--think`, `--theme`, `-s "prompt de sistema"`, `-d <carpeta>` (proyecto), `-y` (no pedir confirmación).

## 1. Diagnóstico de visión (con el código anterior)

| Modelo | Imagen | PDF escaneado (`samples/a.pdf`) |
|---|---|---|
| Qwen2-VL-2B | cargaba, pero respuesta **vacía** | `Prompt exceeds n_ctx: 2734 > 2048` |
| MiniCPM-V 2.6 | sin mmproj → texto en silencio → **alucinaba** ("cielo nublado") | 0 caracteres extraídos → **alucinaba** un libro |
| Qwen2.5-VL-7B | se le emparejaba el mmproj **de 2B** (por el nombre) → `Failed to load mtmd context` | igual |

Causas y arreglos:
- **Handler**: en llama-cpp-python 0.3.34 todos los handlers usan libmtmd. `Qwen25VLChatHandler` usa una
  plantilla genérica (texto antes de la imagen) con la que Qwen2-VL, a temperature 0.7, cierra el turno al
  instante (vacío 7/7 veces). `MTMDChatHandler` usa la plantilla Jinja **del propio GGUF**: 6/6 correctas.
  Ahora se usa siempre `MTMDChatHandler`, con las imágenes antes del texto.
- **Emparejamiento por metadatos**: un GGUF con `general.architecture = clip` es un mmproj; encaja si
  `clip.vision.projection_dim == <arch>.embedding_length` y `clip.projector_type` es compatible con la
  arquitectura. Si falta, se avisa y **no** se marca como visión.
- El contexto de mtmd se inicializa **al cargar**: si el mmproj no sirve, lo sabes en ese momento.
- PDF: por página, el texto extraíble se usa como texto; solo las páginas escaneadas se pasan a imagen
  (límite `pdf_max_image_pages`). Si no caben, se reducen o se procesan **por lotes** y se resumen.
  Si el PDF está escaneado y el modelo no tiene visión → error claro, no se envía nada.
- `n_ctx` y `n_gpu_layers` se eligen con el estimador de memoria (`auto`), no con 2048/24 fijos.
- Arquitecturas `qwen35`, `gemma4` y `gemma3n`: **todas cargan** con 0.3.34. `mtmd.dll` soporta los
  proyectores `gemma4v`, `gemma3nv`, `qwen3vl_merger`, `qwen2.5vl_merger` y `minicpmv`.

## 2. mmproj que faltan (a `models/mmproj/`; no se ha descargado nada)

| Modelo | Repo de Hugging Face | Archivo |
|---|---|---|
| MiniCPM-V-2_6-Q6_K | `openbmb/MiniCPM-V-2_6-gguf` | `mmproj-model-f16.gguf` (996 MiB) |
| Qwen2.5-VL-7B | `ggml-org/Qwen2.5-VL-7B-Instruct-GGUF` | ✅ **instalado** el 2026-10-10: `mmproj-Qwen2.5-VL-7B-Instruct-Q8_0.gguf` (probado con imagen y `a.pdf`) |
| Gemma 4 12B (y gemma4-v2) | `unsloth/gemma-4-12b-it-GGUF` | `mmproj-F16.gguf` (167 MiB) — alt.: `ggml-org/gemma-4-12B-it-GGUF` · `mmproj-gemma-4-12B-it-Q8_0.gguf` |
| Qwen3.5-9B | `unsloth/Qwen3.5-9B-GGUF` | `mmproj-F16.gguf` (876 MiB) |
| Qwen3.5-4B (Opus4.7 distill) | `unsloth/Qwen3.5-4B-GGUF` | `mmproj-F16.gguf` (641 MiB, fine-tune: probable pero no garantizado) |
| Qwen3.6-27B / Qwen3.8-27B | `unsloth/Qwen3.6-27B-GGUF` / `unsloth/Qwen3.8-27B-GGUF` | `mmproj-F16.gguf` (885 MiB) |
| Gemma 3n E4B | solo no oficial: `mradermacher/gemma-3n-E4B-it-abliterated-GGUF` | `gemma-3n-E4B-it-abliterated.mmproj-Q8_0.gguf` (experimental) |

`/doctor` muestra esta tabla con lo que falte en cada momento.

## 3. Rendimiento: lo que vi en este equipo

- llama-cpp-python es un build **Vulkan**, no CUDA. Funciona con todos tus modelos, pero en NVIDIA un build
  CUDA suele procesar prompts e imágenes bastante más rápido.
- Hay un juego (Generation Zero) y Wallpaper Engine usando la GPU: **51–69 % de uso y ~3.5 GB de VRAM** sin
  ningún modelo. El mismo turno de Qwen2-VL-2B osciló entre 2 s y 30 s, y la generación entre 12 y 116 t/s.
  Para medir de verdad, ciérralos.

**Alternativas si algún modelo no te va bien** (ahora mismo no hace falta cambiar nada):
| Opción | Pros | Contras |
|---|---|---|
| llama-cpp-python (actual) | todo en un proceso, control fino (KV, capas, mmproj), ya funciona | build Vulkan; las versiones nuevas de llama.cpp tardan en llegar |
| llama-cpp-python con CUDA | mismo código, más rápido en NVIDIA | hay que compilarlo o encontrar una wheel cu12x para Python 3.14 |
| llama-server (llama.cpp) | lo último de llama.cpp, CUDA precompilado, `--mmproj`, API OpenAI | proceso aparte; habría que añadir un motor HTTP |
| Ollama (ya soportado: `-m nombre`) | muy cómodo, CUDA, gestiona memoria y visión | menos control (KV, capas); hay que importar los GGUF con un Modelfile |

## 4. Comandos y prueba rápida de cada uno

| Comando | Qué hace | Prueba rápida |
|---|---|---|
| `/help` | comandos y atajos | `/help` |
| `/` | menú emergente filtrado | escribe `/c` → aparecen /chat, /clear, /compact, /copy, /ctx |
| `/model [nombre]` | selector con flechas: tamaño, visión, memoria estimada; muestra `/mem` y confirma si no cabe | `/model` → ↓ → Enter |
| `/models` | tabla con valores auto | `/models` |
| `/mem [modelo] [ctx] [gpu]` (`/estimate`) | estimación desglosada (pesos, KV, recurrente, mmproj, cómputo) + barras + real vs estimado | `/mem gemma-4-12B 32k auto` |
| `/ctx [n\|32k\|auto]` | sin argumento: actual, máximo del GGUF y tabla de lo que cabe; con argumento recarga conservando la conversación | `/ctx` y luego `/ctx 8k` |
| `/gpu [n\|auto\|max]` | capas en GPU (recarga) | `/gpu 20` |
| `/kv f16\|q8_0\|q4_0` | tipo de caché KV | `/kv q8_0` y `/mem` |
| `/swa min\|full` | caché SWA (Gemma): mínima o completa | `/swa` |
| `/auto` | capas y contexto máximos que caben (margen `vram_margin_mb`) | `/auto` |
| `/tokens /temp /top_p /top_k /min_p /repeat /seed` | muestreo | `/temp 0.3` |
| `/params` · `/set clave valor` | ver todo · ajustes (pdf, imágenes, margen, shell) | `/set pdf_max_image_pages 4` |
| `/think on\|off\|show\|hide\|last` | pedir razonamiento (Qwen3.5/Gemma 4) y mostrarlo plegado o desplegado | `/think on` + `¿17*23?` + `/think last` |
| `@ruta` | adjuntar texto, imagen o PDF (Tab autocompleta) | `@samples/a.pdf ¿título de la tabla 1?` |
| `/file /image /pdf` | lo mismo por comando; `/pdf ruta auto\|texto\|imagenes 1-5` | `/pdf samples/a.pdf auto 1` |
| `/attachments [clear]` | adjuntos pendientes | `/attachments` |
| `!cmd` · `!!cmd` | shell (PowerShell) · y añadir la salida al contexto; `!cd ruta` cambia de directorio | `!!git status` + "resúmelo" |
| `/compact [instr.]` | resume la conversación (no la toca si el resumen no ahorra) | tras varios turnos: `/compact` |
| `/chat save\|list\|load\|delete` · `/resume` | sesiones con nombre · selector que incluye las autoguardadas | `/chat save p1`, `/clear`, `/resume` |
| `/memory show\|add\|addp\|edit\|reload\|path` | `Clay-Myrrh.md` global (`~/.local_ai`) o del proyecto | `/memory add Responde siempre en español` |
| `/stats` | tokens de entrada/salida, t/s, tiempos, VRAM real vs estimada | `/stats` |
| *(automático)* | si el modelo crea un archivo (```` ```html index.html ````, o nombras el archivo/tipo en tu mensaje: "crea index.html", "hazme un html"), ofrece guardarlo; nunca fuera del directorio actual | `crea un archivo saludo.html con un saludo centrado` → `s` |
| `/save [ruta] [nº]` | guarda un bloque de código de la última respuesta (el mayor, o el nº indicado) | `/save calculadora.html` · `/save ejemplo.py 2` |
| `/open [ruta]` | abre el último archivo guardado (HTML en el navegador) | `/open` |
| `/copy` · `/export [ruta.md]` | portapapeles · Markdown (con razonamiento plegado) | `/export` |
| `/system [texto\|reset]` | prompt de sistema | `/system` |
| `/theme [nombre]` | oscuro, claro, dracula, nord, solarizado | `/theme dracula` |
| `/mmproj [archivo\|auto\|none]` | ver o forzar el proyector | `/mmproj` |
| `/doctor` · `/debug [on\|off]` | diagnóstico completo · log nativo de llama.cpp/mtmd | `/doctor` |
| `/clear` · `/exit` (`/quit`) | borrar la conversación · salir (autoguardado) | |

Atajos: Enter envía · Alt+Enter / Shift+Enter / Ctrl+J / `\`+Enter: nueva línea · ↑↓ historial ·
Ctrl+R búsqueda · → acepta la sugerencia gris · **Esc interrumpe la generación** · Ctrl+K copia ·
Ctrl+L limpia · Ctrl+C borra la línea (dos veces: salir).

Si algo falla, el CLI ya no se cierra: muestra el error y guarda la traza en `~/.local_ai/crash.log`
(incluye también los fallos nativos de llama.cpp vía faulthandler). `/set save_files never` desactiva
la oferta de guardar archivos.

Nota: Esc corta entre tokens. El procesado inicial del prompt o de una imagen no se puede interrumpir a
medias; se para justo al terminar ese paso.
