/* Clay-Myrrh · guía paso a paso (pasos cortos que avanzan solos), comparativa local/nube y galería de componentes.
   Las mascotas son ilustraciones propias inspiradas en el estilo de cada servicio, no logotipos oficiales.
   Precios orientativos (octubre de 2026, fuentes de terceros): cambian a menudo, verifica antes de pagar. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* sin almacenamiento */ } } };
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
  // idioma: lo fija setup.js (t(español, inglés)); números con . en español y , en inglés
  const EN = window.LAI_LANG === "en";
  const t = window.LAI_T || ((es) => es);
  const num = (n) => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, EN ? "," : ".");      // 1500 → «1.500»
  const money = (n) => (EN ? "$" + num(n) : num(n) + " $");
  const range = (a, b) => (EN ? `$${num(a)}–${num(b)}` : `${num(a)}–${num(b)} $`);
  const DATE = t("octubre de 2026", "October 2026");

  /* ------------------------------------------------------------------ mascotas */
  const RAW_MASCOT = {
    claude: `<svg class="m m-claude" viewBox="0 0 16 13" shape-rendering="crispEdges" aria-hidden="true">
      <g class="m-body" fill="#D97757"><rect x="2" y="2" width="12" height="6"/><rect x="0" y="4" width="2" height="2"/><rect x="14" y="4" width="2" height="2"/></g>
      <g class="m-legs" fill="#D97757"><rect class="l1" x="3" y="8" width="1" height="3"/><rect class="l2" x="5" y="8" width="1" height="3"/>
        <rect class="l1" x="10" y="8" width="1" height="3"/><rect class="l2" x="12" y="8" width="1" height="3"/></g>
      <g class="m-eyes" fill="#1b1b1b"><rect x="4" y="4" width="1" height="2"/><rect x="11" y="4" width="1" height="2"/></g></svg>`,
    gemini: `<svg class="m m-gemini" viewBox="0 0 100 100" aria-hidden="true"><defs><linearGradient id="gg__ID__" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#4285F4"/><stop offset=".55" stop-color="#9B72CB"/><stop offset="1" stop-color="#D96570"/></linearGradient></defs>
      <path class="m-star" d="M50 2 C54 34 66 46 98 50 C66 54 54 66 50 98 C46 66 34 54 2 50 C34 46 46 34 50 2Z" fill="url(#gg__ID__)"/></svg>`,
    codex: `<svg class="m m-codex" viewBox="0 0 100 100" aria-hidden="true">
      <rect x="10" y="18" width="80" height="64" rx="16" fill="#0f0f10" stroke="#e7e7e7" stroke-width="3"/>
      <circle cx="24" cy="30" r="3" fill="#ff5f57"/><circle cx="34" cy="30" r="3" fill="#febc2e"/><circle cx="44" cy="30" r="3" fill="#28c840"/>
      <text x="24" y="66" font-family="ui-monospace,Consolas,monospace" font-size="26" font-weight="700" fill="#f2f2f2">&gt;</text>
      <rect class="m-cursor" x="44" y="46" width="16" height="22" rx="2" fill="#f2f2f2"/>
      <rect x="46" y="8" width="8" height="10" rx="3" fill="#e7e7e7"/></svg>`,
    local: `<svg class="m m-local" viewBox="0 0 100 100" aria-hidden="true"><defs><linearGradient id="lg__ID__" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#7c3aed"/><stop offset="1" stop-color="#06b6d4"/></linearGradient></defs>
      <g fill="#9aa4b5">${[26, 40, 54, 68].map((v) => `<rect x="${v - 2.5 + 3}" y="5" width="5" height="11" rx="2"/><rect x="${v - 2.5 + 3}" y="84" width="5" height="11" rx="2"/><rect x="5" y="${v - 2.5 + 3}" width="11" height="5" rx="2"/><rect x="84" y="${v - 2.5 + 3}" width="11" height="5" rx="2"/>`).join("")}</g>
      <rect x="14" y="14" width="72" height="72" rx="14" fill="url(#lg__ID__)"/>
      <rect class="m-die" x="29" y="29" width="42" height="42" rx="9" fill="#0f0b1f" opacity=".32"/>
      <path d="M50 35 L65 48 H61 V64 H54 V55 H46 V64 H39 V48 H35 Z" fill="#fff"/></svg>`,
  };
  // cada copia lleva ids propios: si el primer <linearGradient> está oculto, los demás no se pintarían
  let uid = 0;
  const MASCOT = new Proxy(RAW_MASCOT, { get: (t, k) => (typeof t[k] === "string" ? t[k].replaceAll("__ID__", String(++uid)) : t[k]) });
  const WHO = {
    claude: { name: "Claude", by: "Anthropic", color: "#D97757" },
    gemini: { name: "Gemini", by: "Google", color: "#8b7cf6" },
    codex: { name: "Codex", by: "OpenAI", color: "#e7e7e7" },
    local: { name: t("Tu IA local", "Your local AI"), by: t("en tu PC", "on your PC"), color: "#7c3aed" },
  };

  /* ------------------------------------------------------------------ tu equipo */
  function myHardware() {
    const hw = window.LAI?.hw; if (!hw) return null;
    const v = +hw.vram_gb || 0, r = +hw.ram_gb || 0;
    let level, what;
    if (v >= 48 || r >= 128) { level = 3; what = t("modelos de 70–120B y MoE grandes: el nivel más alto en casa", "70–120B models and large MoE: the highest level at home"); }
    else if (v >= 20 || r >= 64) { level = 2; what = t("modelos de 27–35B en la GPU: buen nivel para programar y escribir", "27–35B models on the GPU: a good level for programming and writing"); }
    else if (v >= 8) { level = 1; what = t("hasta ~14B en la GPU y MoE de 30–35B apoyándose en la RAM", "up to ~14B on the GPU and 30–35B MoE leaning on RAM"); }
    else if (r >= 16) { level = 0; what = t("modelos de 4–9B y MoE pequeños en la CPU (más lento)", "4–9B models and small MoE on the CPU (slower)"); }
    else { level = 0; what = t("modelos pequeños de 1–4B: útiles, pero limitados", "small 1–4B models: useful, but limited"); }
    const gpu = hw.gpu_name ? `${hw.gpu_name} ${v} GB` : t("sin GPU dedicada", "no dedicated GPU");
    // nivel de la tabla de precios (HW_TIERS): 0 lo que ya tienes · 1 gama media · 2 gama alta · 3 casi frontera
    const tier = v >= 160 || r >= 384 ? 3 : v >= 48 || r >= 96 ? 2 : v >= 16 || r >= 64 ? 1 : 0;
    const vs = t(["≈ GPT-3.5 (2023) en tareas sencillas",
      "≈ GPT-3.5; con un MoE de 30B, cerca de Gemini 1.5 Flash (más lento)",
      "≈ Gemini 1.5 Flash / GPT-4o mini (2024)", "≈ GPT-4o / Gemini 1.5 Pro (2024) en muchas tareas"],
      ["≈ GPT-3.5 (2023) on simple tasks", "≈ GPT-3.5; with a 30B MoE, close to Gemini 1.5 Flash (slower)",
      "≈ Gemini 1.5 Flash / GPT-4o mini (2024)", "≈ GPT-4o / Gemini 1.5 Pro (2024) on many tasks"])[level];
    return { level, tier, what, vs, label: t(`${gpu} + ${r} GB de RAM`, `${gpu} + ${r} GB of RAM`) };
  }

  /* ------------------------------------------------------------------ datos de la comparativa */
  const SERVICES_EN = [
    { k: "claude", entry: "Free (without Claude Code)", plans: "Pro $20/month, or $200 a year (≈ $17/month) · Max $100 or $200/month",
      cli: "Claude Code (included from Pro)", strong: "Programming and agents, long tasks, careful writing, large documents",
      limits: "Quota per 5-hour window and weekly; the web plan doesn't cover the API", privacy: "Your data goes to their servers (under privacy policies)",
      offline: "No", runs: "Anthropic data centres" },
    { k: "gemini", entry: "Free (light model)", plans: "AI Plus $4.99 (Flash models) · AI Pro $19.99 · Ultra from ~$100/month",
      cli: "Gemini CLI (open source), with the paid plans", strong: "Very long context (Pro plans), multimodal, built into Gmail/Docs/Drive",
      limits: "Quotas depending on the plan; the API is paid separately", privacy: "Your data goes to Google (depending on your settings)",
      offline: "No", runs: "Google data centres" },
    { k: "codex", entry: "Limited in ChatGPT Free", plans: "ChatGPT Go $8 · Plus $20 · Pro $100 / $200 (and above)",
      cli: "Codex CLI (open source, free; uses your ChatGPT plan or an API key)", strong: "Code agent in the terminal and in the cloud, tasks delegated in parallel",
      limits: "Quotas per 5-hour window depending on the plan", privacy: "Your data goes to OpenAI (depending on your settings)",
      offline: "No", runs: "OpenAI data centres" },
    { k: "local", entry: "$0/month (once downloaded)", plans: "Just the hardware and electricity (≈ $2–15/month depending on use)",
      cli: "Clay-Myrrh (this program), Ollama, llama.cpp…", strong: "Total privacy, offline, no limits or quotas, always the same model",
      limits: "Whatever your hardware allows: model size, speed and context", privacy: "Nothing leaves your computer",
      offline: "Yes", runs: "Your PC" },
  ];
  const HW_TIERS_EN = [
    { name: "Your current machine", price: [0, 0], spec: "What you already have", runs: "4–9B models; 30B MoE with patience",
      vs: "≈ GPT-3.5 (2023) on simple tasks", like: "Summaries, emails, questions and drafts without spending anything" },
    { name: "Mid-range", price: [2500, 5000], spec: "PC with a 24 GB GPU (used RTX 3090 ≈ $1,000–1,500; a 5090 sends the price soaring) or a 64 GB Mac",
      runs: "27–35B models at a good speed", vs: "≈ Gemini 1.5 Flash / GPT-4o mini (2024)",
      like: "A good programming assistant on bounded projects; behind today's cloud on long tasks" },
    { name: "High-end", price: [3300, 6000], spec: "128 GB of unified memory (Ryzen AI Max+ mini PC ≈ $3,300–3,900 or Mac Studio) or 2–4 GPUs",
      runs: "70–120B models and large MoE", vs: "≈ GPT-4o / Gemini 1.5 Pro (2024) on many tasks",
      like: "Close to mid-range cloud, but slower" },
    { name: "Near «frontier»", price: [10000, 300000], spec: "512 GB Mac Studio (slow) or a server with 8 × 80 GB GPUs",
      runs: "Giant open models (600B–1T)", vs: "≈ the cloud of 6–12 months ago (Gemini 2.5 Pro / Claude Sonnet 4 level)",
      like: "The closest thing to Claude/Gemini/GPT at home; even so they usually lag behind on agents and very long tasks" },
  ];
  const PROS_EN = [["🔒", "Total privacy", "Contracts, medical records, company code… nothing leaves your machine."],
    ["📡", "Offline", "On a plane, in the countryside, during a network outage or on isolated networks."],
    ["♾️", "No limits or quotas", "No 5-hour windows or weekly caps: use it as much as you like."],
    ["🧊", "Always the same", "The model doesn't change from one day to the next: reproducible results."],
    ["🛠️", "Full control", "You choose the model, settings and instructions, and plug it into your scripts."],
    ["🎓", "You learn how it works", "Tokens, context, quantisation… you understand what you use."]];
  const CONS_EN = [["🧠", "Less capability", "On large projects, research and long tasks, the cloud is still ahead."],
    ["💸", "Expensive hardware", "Memory (VRAM/RAM) is what matters, and it's the priciest part of the machine."],
    ["🔌", "Power, heat and noise", "A GPU at full load draws 200–450 W."],
    ["🐢", "Speed", "On modest machines it writes slowly, or you have to use small models."],
    ["🧩", "Setup", "Downloading multi-GB models and tuning memory and context (this assistant does it for you)."],
    ["🌐", "No web search", "It doesn't look things up online on its own: it knows what it learned in training."]];
  const USES_EN = [["✈️", "On a plane or without coverage", "local"], ["🏥", "Private documents (health, legal, business)", "local"],
    ["🔁", "Repetitive tasks: summarise, classify, translate", "local"], ["📄", "Reading scanned PDFs and photos of documents", "local"],
    ["🧪", "Learning and experimenting with AI", "local"], ["🧱", "Websites and templates with components", "local"],
    ["🏗️", "Large code projects and long agents", "nube"], ["🔎", "Research with up-to-date information from the internet", "nube"],
    ["⚖️", "Critical decisions (legal, medical): always with a professional", "nube"]];
  const SERVICES = EN ? SERVICES_EN : [
    { k: "claude", entry: "Gratis (sin Claude Code)", plans: "Pro 20 $/mes, o 200 $ al año (≈ 17 $/mes) · Max 100 $ o 200 $/mes",
      cli: "Claude Code (incluido desde Pro)", strong: "Programación y agentes, tareas largas, escritura cuidada, documentos grandes",
      limits: "Cupo por ventanas de 5 h y semanal; el plan de la web no cubre la API", privacy: "Tus datos van a sus servidores (con políticas de privacidad)",
      offline: "No", runs: "Centros de datos de Anthropic" },
    { k: "gemini", entry: "Gratis (modelo ligero)", plans: "AI Plus 4,99 $ (modelos Flash) · AI Pro 19,99 $ · Ultra desde ~100 $/mes",
      cli: "Gemini CLI (código abierto), con los planes de pago", strong: "Contexto muy largo (planes Pro), multimodal, integrado en Gmail/Docs/Drive",
      limits: "Cupos según el plan; la API se paga aparte", privacy: "Tus datos van a Google (según tu configuración)",
      offline: "No", runs: "Centros de datos de Google" },
    { k: "codex", entry: "Limitado en ChatGPT Free", plans: "ChatGPT Go 8 $ · Plus 20 $ · Pro 100 $ / 200 $ (y superiores)",
      cli: "Codex CLI (código abierto, gratis; usa tu plan de ChatGPT o una clave de API)", strong: "Agente de código en terminal y en la nube, tareas delegadas en paralelo",
      limits: "Cupos por ventanas de 5 h según el plan", privacy: "Tus datos van a OpenAI (según tu configuración)",
      offline: "No", runs: "Centros de datos de OpenAI" },
    { k: "local", entry: "0 $/mes (ya descargada)", plans: "Solo el equipo y la luz (≈ 2–15 $/mes según uso)",
      cli: "Clay-Myrrh (este programa), Ollama, llama.cpp…", strong: "Privacidad total, sin internet, sin límites ni cuotas, siempre el mismo modelo",
      limits: "Los que ponga tu hardware: tamaño del modelo, velocidad y contexto", privacy: "Nada sale de tu ordenador",
      offline: "Sí", runs: "Tu PC" },
  ];
  // Precios de equipo completo, orientativos a octubre de 2026 (la memoria y las GPU han subido mucho este año).
  // «Compite con»: comparación aproximada con modelos de la nube según pruebas públicas; varía por tarea.
  const HW_TIERS = EN ? HW_TIERS_EN : [
    { name: "Tu equipo actual", price: [0, 0], spec: "Lo que ya tienes", runs: "Modelos de 4–9B; MoE de 30B con paciencia",
      vs: "≈ GPT-3.5 (2023) en tareas sencillas", like: "Resúmenes, correos, dudas y borradores sin gastar nada" },
    { name: "Gama media", price: [2500, 5000], spec: "PC con GPU de 24 GB (RTX 3090 usada ≈ 1.000–1.500 $; una 5090 dispara el precio) o Mac de 64 GB",
      runs: "Modelos de 27–35B a buena velocidad", vs: "≈ Gemini 1.5 Flash / GPT-4o mini (2024)",
      like: "Buen asistente de programación en proyectos acotados; por detrás de la nube actual en tareas largas" },
    { name: "Gama alta", price: [3300, 6000], spec: "128 GB de memoria unificada (mini PC Ryzen AI Max+ ≈ 3.300–3.900 $ o Mac Studio) o 2–4 GPUs",
      runs: "Modelos de 70–120B y MoE grandes", vs: "≈ GPT-4o / Gemini 1.5 Pro (2024) en muchas tareas",
      like: "Cerca de la nube de gama media, con menos velocidad" },
    { name: "Casi «frontera»", price: [10000, 300000], spec: "Mac Studio de 512 GB (lento) o servidor con 8 GPUs de 80 GB",
      runs: "Modelos abiertos gigantes (600B–1T)", vs: "≈ la nube de hace 6–12 meses (nivel Gemini 2.5 Pro / Claude Sonnet 4)",
      like: "Lo más parecido a Claude/Gemini/GPT en casa; aun así suelen ir por detrás en agentes y tareas muy largas" },
  ];
  // el primer nivel es el equipo de la persona (detectado o simulado)
  function tiers() {
    const h = myHardware(), t = HW_TIERS.map((x) => ({ ...x }));
    if (h) Object.assign(t[0], { spec: h.label, runs: h.what.charAt(0).toUpperCase() + h.what.slice(1), vs: h.vs });
    return t;
  }
  const PROS = EN ? PROS_EN : [["🔒", "Privacidad total", "Contratos, historiales médicos, código de la empresa… nada sale de tu equipo."],
    ["📡", "Sin internet", "En el avión, en el campo, en un corte de red o en redes aisladas."],
    ["♾️", "Sin límites ni cuotas", "No hay ventanas de 5 horas ni topes semanales: úsala todo lo que quieras."],
    ["🧊", "Siempre igual", "El modelo no cambia de un día para otro: resultados reproducibles."],
    ["🛠️", "Control total", "Eliges modelo, ajustes, instrucciones y lo integras en tus scripts."],
    ["🎓", "Aprendes cómo funciona", "Tokens, contexto, cuantización… entiendes lo que usas."]];
  const CONS = EN ? CONS_EN : [["🧠", "Menos capacidad", "En proyectos grandes, investigación y tareas largas, la nube sigue por delante."],
    ["💸", "Hardware caro", "La memoria (VRAM/RAM) manda, y es lo más caro del equipo."],
    ["🔌", "Luz, calor y ruido", "Una GPU a tope consume 200–450 W."],
    ["🐢", "Velocidad", "En equipos modestos escribe despacio o hay que usar modelos pequeños."],
    ["🧩", "Montaje", "Descargar modelos de varios GB y ajustar memoria y contexto (este asistente lo hace por ti)."],
    ["🌐", "Sin búsqueda web", "No consulta internet por sí sola: sabe lo que aprendió al entrenarse."]];
  const USES = EN ? USES_EN : [["✈️", "En el avión o sin cobertura", "local"], ["🏥", "Documentos privados (salud, legal, empresa)", "local"],
    ["🔁", "Tareas repetitivas: resumir, clasificar, traducir", "local"], ["📄", "Leer PDFs escaneados y fotos de documentos", "local"],
    ["🧪", "Aprender y experimentar con IA", "local"], ["🧱", "Webs y plantillas con componentes", "local"],
    ["🏗️", "Proyectos de código grandes y agentes largos", "nube"], ["🔎", "Investigar con información actual de internet", "nube"],
    ["⚖️", "Decisiones críticas (legales, médicas): siempre con un profesional", "nube"]];

  /* ------------------------------------------------------------------ guía paso a paso */
  // Ritmo de vídeo corto: una idea por paso, pocos segundos, avanza sola (se puede pausar o ir a mano).
  const chip = (t) => `<span class="g-chip">${t}</span>`;
  const squad = (ks, cls = "") => `<div class="g-squad ${cls}">${ks.map((k, i) => `<div class="g-mascot pop" style="--d:${i * .12}s">${MASCOT[k]}<b>${WHO[k].name}</b></div>`).join("")}</div>`;
  const one = (k) => `<div class="g-mascot solo pop">${MASCOT[k]}</div>`;
  const icon = (e) => `<div class="g-icon pop">${e}</div>`;
  const ALL = ["claude", "gemini", "codex", "local"];
  const SLIDES = [
    { tone: "violet", dur: 7000, art: () => squad(ALL), title: t("¿IA en la nube o en tu PC?", "AI in the cloud or on your PC?"),
      body: () => `<p class="g-lead">${t("En 11 pasos cortos: qué ofrece cada opción, cuánto cuesta tener algo parecido en casa y para qué sirve de verdad una IA local.", "In 11 short steps: what each option offers, what it costs to have something similar at home and what a local AI is really for.")}</p>`,
      tip: t("Avanza sola; puedes pausar o usar las flechas.", "It moves on by itself; you can pause or use the arrows.") },
    { tone: "claude", dur: 8000, art: () => one("claude"), title: "Claude", sub: "Anthropic",
      body: () => `<div class="g-chips">${chip(t("💻 Muy fuerte programando, con Claude Code", "💻 Very strong at programming, with Claude Code"))}${chip(t("📚 Tareas largas y documentos enormes", "📚 Long tasks and huge documents"))}${chip(t("✍️ Escribe con mucho cuidado", "✍️ Writes very carefully"))}${chip(t("💳 Pro 20 $/mes · Max 100–200 $", "💳 Pro $20/month · Max $100–200"))}</div>`,
      tip: t("Destaca en proyectos grandes.", "It shines on large projects.") },
    { tone: "gemini", dur: 8000, art: () => one("gemini"), title: "Gemini", sub: "Google",
      body: () => `<div class="g-chips">${chip(t("🧠 Contexto muy largo en los planes Pro", "🧠 Very long context on the Pro plans"))}${chip(t("🔗 Integrado con Gmail, Docs y Drive", "🔗 Built into Gmail, Docs and Drive"))}${chip(t("🖼️ Fuerte con imágenes y vídeo", "🖼️ Strong with images and video"))}${chip(t("💳 Desde 4,99 $ (modelos Flash) · Pro 19,99 $", "💳 From $4.99 (Flash models) · Pro $19.99"))}</div>`,
      tip: t("Ideal si vives dentro de Google.", "Ideal if you live inside Google.") },
    { tone: "codex", dur: 8000, art: () => one("codex"), title: "Codex", sub: "OpenAI",
      body: () => `<div class="g-chips">${chip(t("⌨️ Agente de código en tu terminal", "⌨️ Code agent in your terminal"))}${chip(t("🆓 La CLI es libre; usa tu plan de ChatGPT", "🆓 The CLI is open; it uses your ChatGPT plan"))}${chip(t("🧵 Delega varias tareas a la vez", "🧵 Delegate several tasks at once"))}${chip(t("💳 Go 8 $ · Plus 20 $ · Pro 100–200 $", "💳 Go $8 · Plus $20 · Pro $100–200"))}</div>`,
      tip: t("Para delegar tareas de programación.", "For delegating programming tasks.") },
    { tone: "local", dur: 9000, art: () => one("local"), title: t("Tu IA local", "Your local AI"), sub: t("en tu PC", "on your PC"),
      body: () => { const h = myHardware(); return `<div class="g-chips">${chip(t("🔒 Nada sale de tu ordenador", "🔒 Nothing leaves your computer"))}${chip(t("📡 Funciona sin internet", "📡 Works offline"))}${chip(t("♾️ Sin cuotas ni límites de uso", "♾️ No fees or usage limits"))}${chip(t("⚠️ Depende de tu hardware", "⚠️ Depends on your hardware"))}</div>
        ${h ? `<div class="g-note">${t("Tu equipo", "Your machine")}: <b>${esc(h.label)}</b> → ${esc(h.what)}.</div>` : ""}`; },
      tip: t("Privada, sin conexión y sin cuotas.", "Private, offline and with no fees.") },
    { tone: "slate", dur: 9500, art: () => squad(ALL, "mini"), title: t("Cara a cara", "Face to face"),
      body: () => { const rows = [[t("Calidad máxima", "Top quality"), [5, 5, 5, 3]], [t("Privacidad", "Privacy"), [2, 2, 2, 5]], [t("Sin internet", "Offline"), [0, 0, 0, 5]],
        [t("Sin pagar cada mes", "No monthly fee"), [1, 2, 1, 5]], [t("Facilidad", "Ease of use"), [5, 5, 4, 3]]];
        return `<div class="g-battle">${rows.map(([l, v], i) => `<div class="g-brow" style="--d:${i * .2}s"><span>${l}</span>${v.map((x, j) =>
          `<i title="${WHO[ALL[j]].name}" style="--w:${x * 20}%;--c:${WHO[ALL[j]].color}"></i>`).join("")}</div>`).join("")}
          <div class="g-brow legend"><span></span>${ALL.map((k) => `<em>${WHO[k].name}</em>`).join("")}</div></div>
          <p class="g-small">${t("Valoración orientativa; la calidad local depende de tu equipo.", "A rough rating; local quality depends on your machine.")}</p>`; },
      tip: t("No hay una ganadora para todo.", "There's no winner for everything.") },
    { tone: "green", dur: 10000, art: () => icon("🖥️"), title: t("¿Cuánto cuesta igualarlas en casa?", "What does it cost to match them at home?"),
      body: () => `<div class="g-prices">${tiers().map((x, i) => `<div class="g-price pop" style="--d:${i * .25}s"><b data-count="${x.price[0]}" data-to="${x.price[1]}">${money(0)}</b>
          <span>${x.name}${i === 0 ? ` <em>${esc(x.spec)}</em>` : ""}</span><small>${esc(x.runs)}</small><small class="g-vs">${t("Compite con", "Competes with")}: ${esc(x.vs)}</small></div>`).join("")}</div>
        <p class="g-small">${t(`Precios orientativos (${DATE}); comparaciones aproximadas, varían según la tarea.`, `Rough prices (${DATE}); approximate comparisons that vary by task.`)}</p>`,
      tip: t("La memoria es lo que manda (y lo que cuesta).", "Memory is what matters (and what costs).") },
    { tone: "amber", dur: 9000, art: () => icon("🧮"), title: t("Las cuentas", "The maths"),
      body: () => t(`<div class="g-math"><div><b>20 $/mes</b><span>240 $ al año</span></div><div class="op">frente a</div><div><b>PC de 3.000 $</b><span>+ la luz</span></div></div>
        <div class="g-verdict pop" style="--d:.5s">≈ <b>12 años</b> para amortizarlo <small>sin contar la luz</small></div>
        <p class="g-lead">Una IA local no se compra para ahorrar: se elige por <mark>privacidad</mark>, por <mark>no depender de internet</mark> y por <mark>no tener límites</mark>.</p>`,
        `<div class="g-math"><div><b>$20/month</b><span>$240 a year</span></div><div class="op">versus</div><div><b>$3,000 PC</b><span>+ electricity</span></div></div>
        <div class="g-verdict pop" style="--d:.5s">≈ <b>12 years</b> to pay it off <small>not counting electricity</small></div>
        <p class="g-lead">You don't buy a local AI to save money: you choose it for <mark>privacy</mark>, for <mark>not depending on the internet</mark> and for <mark>having no limits</mark>.</p>`),
      tip: t("Si ya tienes el PC, solo pagas la luz.", "If you already have the PC, you only pay for electricity.") },
    { tone: "blue", dur: 10000, art: () => icon("🌍"), title: t("En cualquier lado, pero no en cualquier hardware", "Anywhere, but not on any hardware"),
      body: () => { const h = myHardware(); return `<div class="g-uses">${USES.filter((u) => u[2] === "local").map((u, i) => `<div class="pop" style="--d:${i * .12}s"><span>${u[0]}</span>${u[1]}</div>`).join("")}</div>
        ${h ? `<div class="g-note">${t("Con", "With")} <b>${esc(h.label)}</b>: ${esc(h.what)}.</div>` : ""}`; },
      tip: t("Para esto se usa de verdad una IA local.", "This is what a local AI is really used for.") },
    { tone: "violet", dur: 0, art: () => icon("🤔"), title: t("¿Qué te importa más?", "What matters most to you?"),
      body: () => `<div class="g-quiz">${[["privacy", t("🔒 Que nada salga de mi PC", "🔒 Nothing leaving my PC")], ["offline", t("📡 Usarla sin internet", "📡 Using it offline")], ["money", t("💸 No pagar cada mes", "💸 Not paying every month")],
        ["quality", t("🧠 La máxima calidad", "🧠 The highest quality")], ["code", t("🏗️ Programar proyectos grandes", "🏗️ Programming large projects")]].map(([k, l]) => `<button type="button" data-pick="${k}">${l}</button>`).join("")}</div>
        <div class="g-result" aria-live="polite"></div>`, tip: t("Elige una opción para ver la recomendación.", "Choose an option to see the recommendation.") },
    { tone: "violet", dur: 0, art: () => squad(["local", "claude", "gemini", "codex"]), title: t("La combinación ganadora", "The winning combination"),
      body: () => `<p class="g-lead">${t("<mark>Local</mark> para lo privado y lo rutinario. <mark>Nube</mark> para lo difícil y lo enorme.", "<mark>Local</mark> for the private and the routine. <mark>Cloud</mark> for the hard and the huge.")}</p>
        <div class="g-actions"><button type="button" class="btn primary" data-guide-start>${t("Empezar la instalación →", "Start the setup →")}</button>
        <button type="button" class="btn ghost" data-guide-compare>${t("Ver la comparativa completa", "See the full comparison")}</button></div>`, tip: t("Ahora sí: a instalar.", "Now then: let's install.") },
  ];
  const RESULTS = EN ? {
    privacy: ["local", "Local is for you", "For confidential things, nothing beats your data never leaving your machine. Use the cloud only with information you can share."],
    offline: ["local", "Local is for you", "Without a connection, the only AI that answers is the one you have installed."],
    money: ["local", "Local (if you already have the machine)", "With the PC you have, the cost is electricity. Buying hardware just to save money rarely pays off."],
    quality: ["claude", "The cloud, and local for private things", "For top quality, Claude, Gemini or Codex. Your local AI covers the routine and the private."],
    code: ["codex", "Cloud for big things, local for small ones", "Claude Code or Codex shine on large projects; the local AI does well on bounded tasks and with project mode."],
  } : {
    privacy: ["local", "Local es lo tuyo", "Para lo confidencial, nada como que los datos no salgan de tu equipo. Usa la nube solo con información que puedas compartir."],
    offline: ["local", "Local es lo tuyo", "Sin conexión, la única IA que responde es la que tienes instalada."],
    money: ["local", "Local (si ya tienes el equipo)", "Con el PC que tienes, el coste es la luz. Comprar hardware solo para ahorrar rara vez compensa."],
    quality: ["claude", "La nube, y local para lo privado", "Para la máxima calidad, Claude, Gemini o Codex. Tu IA local cubre lo rutinario y lo privado."],
    code: ["codex", "Nube para lo grande, local para lo pequeño", "Claude Code o Codex brillan en proyectos grandes; la IA local va bien en tareas acotadas y con el modo /proyecto."],
  };

  const G = { i: 0, timer: null, t0: 0, paused: false, elapsed: 0 };
  const root = $("#guide");

  function buildGuide() {
    root.innerHTML = `<div class="g-backdrop" data-guide-close></div>
      <div class="g-card" tabindex="-1">
        <div class="g-head"><span><b>${t("Guía rápida", "Quick guide")}</b> · <span class="g-count"></span></span>
          <button type="button" class="g-x" data-guide-close aria-label="${t("Cerrar la guía", "Close the guide")}">✕</button></div>
        <div class="g-progress">${SLIDES.map((_, k) => `<button type="button" data-goto="${k}" aria-label="${t("Ir al paso", "Go to step")} ${k + 1}"><i></i></button>`).join("")}</div>
        <div class="g-art"></div>
        <div class="g-body" aria-live="polite"></div>
        <div class="g-foot">
          <label class="g-never"><input type="checkbox" id="g-never"> ${t("No mostrar al abrir", "Don't show on start")}</label>
          <div class="g-ctrl"><button type="button" class="btn ghost small" data-step="-1">${t("← Atrás", "← Back")}</button>
            <button type="button" class="btn ghost small g-pause" aria-label="${t("Pausar", "Pause")}">${t("⏸ Pausa", "⏸ Pause")}</button>
            <button type="button" class="btn primary small" data-step="1">${t("Siguiente →", "Next →")}</button></div>
        </div>
      </div>`;
    $("#g-never").checked = store.get("lai-guide-never", false);
    $("#g-never").addEventListener("change", (e) => store.set("lai-guide-never", e.target.checked));
  }

  const slideHTML = (s) => `<div class="g-slide"><h2 class="g-title">${s.title}${s.sub ? ` <small>${s.sub}</small>` : ""}</h2>${s.body()}
      <p class="g-tip">💡 ${esc(s.tip)}</p></div>`;

  // La tarjeta mide lo mismo en todos los pasos: la altura del paso más alto (con la respuesta del test incluida).
  function fitHeight() {
    const card = $(".g-card", root), body = $(".g-body", root), art = $(".g-art", root);
    card.style.height = "auto";
    let max = 0;
    const longest = Object.values(RESULTS).reduce((a, r) => (r[2].length > a[2].length ? r : a));
    SLIDES.forEach((s) => {
      art.innerHTML = s.art();
      body.innerHTML = slideHTML(s);
      const res = $(".g-result", body);
      if (res) res.innerHTML = `<div class="g-res"><div class="g-mascot">${MASCOT[longest[0]]}</div><div><b>${longest[1]}</b><p>${longest[2]}</p></div></div>`;
      max = Math.max(max, card.scrollHeight);
    });
    card.style.height = max + "px";          // max-height (CSS) lo limita en pantallas bajas; el cuerpo hace scroll
  }

  function render(i) {
    G.i = Math.max(0, Math.min(SLIDES.length - 1, i));
    const s = SLIDES[G.i], card = $(".g-card", root);
    card.dataset.tone = s.tone;
    $(".g-count", root).textContent = t(`paso ${G.i + 1} de ${SLIDES.length}`, `step ${G.i + 1} of ${SLIDES.length}`);
    $(".g-art", root).innerHTML = s.art();
    $(".g-body", root).innerHTML = slideHTML(s);
    $(".g-body", root).scrollTop = 0;
    $$(".g-progress button", root).forEach((b, k) => { b.className = k < G.i ? "g-done" : k === G.i ? "g-cur" : ""; b.firstChild.style.width = k < G.i ? "100%" : "0"; });
    $$("[data-count]", root).forEach((el) => countUp(el));
    const last = G.i === SLIDES.length - 1;
    $('.g-ctrl [data-step="1"]', root).hidden = last;
    $('.g-ctrl [data-step="-1"]', root).disabled = G.i === 0;
    G.t0 = performance.now(); G.elapsed = 0; G.paused = false; setPauseIcon();
    clearInterval(G.timer);
    if (s.dur) G.timer = setInterval(tick, 50);
  }
  function tick() {
    if (G.paused) return;
    const s = SLIDES[G.i], el = performance.now() - G.t0;
    const bar = $$(".g-progress button", root)[G.i]?.firstChild; if (bar) bar.style.width = Math.min(100, el / s.dur * 100) + "%";
    if (el >= s.dur) render(G.i + 1);
  }
  function pause(p) {
    if (!SLIDES[G.i].dur) return;
    if (p && !G.paused) { G.paused = true; G.elapsed = performance.now() - G.t0; }
    else if (!p && G.paused) { G.paused = false; G.t0 = performance.now() - G.elapsed; }
    setPauseIcon();
  }
  function setPauseIcon() {
    const b = $(".g-pause", root); if (!b) return;
    const auto = !!SLIDES[G.i].dur; b.hidden = !auto;
    b.textContent = G.paused ? t("▶ Seguir", "▶ Resume") : t("⏸ Pausa", "⏸ Pause"); b.setAttribute("aria-label", G.paused ? t("Seguir", "Resume") : t("Pausar", "Pause"));
  }
  function countUp(el) {
    const a = +el.dataset.count, b = +el.dataset.to;
    if (REDUCED) { el.textContent = b ? range(a, b) : money(0); return; }
    const t0 = performance.now(), D = 1200, step = (t) => { const p = Math.min(1, (t - t0) / D), e = 1 - (1 - p) ** 3;
      el.textContent = b ? range(Math.round(a * e / 100) * 100, Math.round(b * e / 100) * 100) : money(0); if (p < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }

  function open() {
    if (!root.innerHTML) buildGuide();
    root.hidden = false; document.body.classList.add("guide-open"); fitHeight(); render(0); $(".g-card", root).focus();
  }
  let fitTimer = null;
  addEventListener("resize", () => { if (root.hidden) return; clearTimeout(fitTimer);
    fitTimer = setTimeout(() => { const i = G.i; fitHeight(); render(i); }, 200); });
  function close() { root.hidden = true; document.body.classList.remove("guide-open"); clearInterval(G.timer); }

  root.addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest("[data-guide-close]")) return close();
    if (t.closest("[data-guide-start]")) { close(); setView("install"); window.LAI?.go?.(1); return; }
    if (t.closest("[data-guide-compare]")) { close(); setView("compare"); return; }
    const st = t.closest("[data-step]"); if (st) return render(G.i + +st.dataset.step);
    const go = t.closest("[data-goto]"); if (go) return render(+go.dataset.goto);
    if (t.closest(".g-pause")) return pause(!G.paused);
    const pk = t.closest("[data-pick]");
    if (pk) {
      $$("[data-pick]", root).forEach((b) => b.classList.toggle("on", b === pk));
      const [m, title, text] = RESULTS[pk.dataset.pick];
      $(".g-result", root).innerHTML = `<div class="g-res pop"><div class="g-mascot">${MASCOT[m]}</div><div><b>${title}</b><p>${text}</p></div></div>`;
    }
  });
  // pasar el ratón por encima o leer con calma no debería saltar el paso: pausa al interactuar con el contenido
  root.addEventListener("pointerdown", (e) => { if (e.target.closest(".g-body")) pause(true); });
  let sx = null;                                           // deslizar en pantallas táctiles
  root.addEventListener("touchstart", (e) => { sx = e.touches[0].clientX; }, { passive: true });
  root.addEventListener("touchend", (e) => { if (sx == null) return; const dx = e.changedTouches[0].clientX - sx; if (Math.abs(dx) > 60) render(G.i + (dx < 0 ? 1 : -1)); sx = null; });
  document.addEventListener("keydown", (e) => {
    if (root.hidden) return;
    if (e.key === "Escape") close(); else if (e.key === "ArrowRight") render(G.i + 1); else if (e.key === "ArrowLeft") render(G.i - 1);
    else if (e.key === " " && !e.target.closest("button, input")) { e.preventDefault(); pause(!G.paused); }
  });
  document.addEventListener("click", (e) => { if (e.target.closest("#open-guide, [data-open-guide]")) open(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden && !root.hidden) pause(true); });

  /* ------------------------------------------------------------------ pestañas */
  function setView(v) {
    $$("#tabs button").forEach((b) => { const on = b.dataset.view === v; b.classList.toggle("on", on); if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
    $("#app").hidden = v !== "install"; $("#view-compare").hidden = v !== "compare"; $("#view-components").hidden = v !== "components";
    document.body.dataset.view = v;
    if (v === "compare") renderCompare();
    if (v === "components") renderComponents();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  document.addEventListener("click", (e) => {
    const t = e.target.closest("#tabs [data-view], [data-view-go]"); if (!t) return;
    setView(t.dataset.view || t.dataset.viewGo);
  });

  /* ------------------------------------------------------------------ comparativa */
  function renderCompare() {
    const h = myHardware();
    const rows = [[t("Precio de entrada", "Entry price"), "entry"], [t("Planes de pago", "Paid plans"), "plans"], [t("Herramienta de terminal", "Terminal tool"), "cli"], [t("Puntos fuertes", "Strengths"), "strong"],
      [t("Límites", "Limits"), "limits"], [t("Privacidad", "Privacy"), "privacy"], [t("Sin internet", "Offline"), "offline"], [t("Dónde se ejecuta", "Where it runs"), "runs"]];
    $("#view-compare").innerHTML = `
      <div class="cmp-hero"><div><span class="eyebrow">${t("Comparativa", "Comparison")}</span><h2>${t("IA local o en la nube", "Local or cloud AI")}</h2>
        <p class="lead">${t("Claude, Gemini y Codex frente a una IA que vive en tu ordenador: cuánto cuestan, qué hace bien cada una y qué hace falta para tener algo parecido en casa.", "Claude, Gemini and Codex versus an AI that lives on your computer: what they cost, what each one does well and what it takes to have something similar at home.")}</p>
        <div class="callout warn small">${t(`Precios orientativos a ${DATE}, según fuentes públicas de terceros: cambian a menudo y varían por país. Compruébalos en las páginas oficiales antes de pagar.`, `Rough prices as of ${DATE}, from public third-party sources: they change often and vary by country. Check them on the official pages before paying.`)}</div>
        <button type="button" class="btn primary" data-open-guide>${t("▶ Ver la guía paso a paso", "▶ Watch the step-by-step guide")}</button></div>
        <div class="cmp-squad" aria-hidden="true">${Object.keys(MASCOT).map((k) => `<div class="g-mascot pop">${MASCOT[k]}<b>${WHO[k].name}</b></div>`).join("")}</div></div>

      <div class="card"><h3>${t("Cara a cara", "Face to face")}</h3><div class="cmp-table-wrap"><table class="cmp-table"><thead><tr><th></th>${SERVICES.map((s) =>
        `<th><span class="cmp-m">${MASCOT[s.k]}</span>${WHO[s.k].name}<small>${WHO[s.k].by}</small></th>`).join("")}</tr></thead>
        <tbody>${rows.map(([l, k]) => `<tr><th scope="row">${l}</th>${SERVICES.map((s) => `<td class="${k === "offline" ? (s[k] === "Sí" || s[k] === "Yes" ? "yes" : "no") : ""}">${esc(s[k])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
        <p class="muted small">${t("Ilustraciones propias inspiradas en cada servicio; no son logotipos oficiales.", "Original illustrations inspired by each service; they are not official logos.")}</p></div>

      <div class="card"><h3>${t("Lo esperado a gastar para un equipo equiparable", "What to expect to spend on a comparable machine")}</h3>
        <p class="muted small">${t("La memoria (VRAM de la gráfica o memoria unificada) decide el tamaño del modelo; el ancho de banda, la velocidad. Precios de equipo completo, orientativos.", "Memory (graphics VRAM or unified memory) decides the model size; bandwidth, the speed. Rough prices for a complete machine.")}</p>
        <div class="cmp-tiers">${tiers().map((x, i) => `<div class="cmp-tier ${i === 0 ? "mine" : ""}">${i === 0 ? `<span class="cmp-you">${t("Ya lo tienes", "You have it")}</span>`
            : h && h.tier >= i ? `<span class="cmp-you alt">${t("Tu equipo ya llega aquí", "Your machine already gets here")}</span>` : ""}
          <b class="cmp-price">${x.price[1] ? range(x.price[0], x.price[1]) : money(0)}</b><h4>${x.name}</h4>
          <p><b>${t("Equipo", "Machine")}:</b> ${esc(x.spec)}</p><p><b>${t("Mueve", "Runs")}:</b> ${esc(x.runs)}</p><p><b>${t("Compite con", "Competes with")}:</b> ${esc(x.vs)}</p>
          <p class="muted small">${x.like}</p></div>`).join("")}</div>
        <p class="muted small">${t("Comparaciones aproximadas según pruebas públicas; varían mucho según la tarea. Puedes simular otro equipo en «Instalación → Tu equipo».", "Approximate comparisons based on public benchmarks; they vary a lot by task. You can simulate another machine in «Setup → Your machine».")}</p></div>

      <div class="card"><h3>${t("Calculadora: ¿cuándo se amortiza?", "Calculator: when does it pay off?")}</h3><div class="cmp-calc">
        <label>${t("Suscripción que dejarías de pagar ($/mes)", "Subscription you'd stop paying ($/month)")}<select id="c-sub"><option>8</option><option selected>20</option><option>100</option><option>200</option></select></label>
        <label>${t("Coste del equipo ($)", "Cost of the machine ($)")}<input id="c-hw" type="number" value="3000" min="0" step="100"></label>
        <label class="row"><input id="c-have" type="checkbox"> ${t("Ya tengo el equipo (solo cuenta la luz)", "I already have the machine (only electricity counts)")}</label>
        <label>${t("Horas de uso al día", "Hours of use per day")}<input id="c-h" type="number" value="3" min="0" max="24" step="0.5"></label>
        <label>${t("Consumo con la IA trabajando (W)", "Power draw with the AI working (W)")}<input id="c-w" type="number" value="350" min="10" step="10"></label>
        <label>${t("Precio de la luz ($/kWh)", "Electricity price ($/kWh)")}<input id="c-kwh" type="number" value="0.18" min="0" step="0.01"></label></div>
        <div class="cmp-calc-out" id="c-out"></div></div>

      <div class="two"><div class="card"><h3>${t("✅ Beneficios", "✅ Benefits")}</h3><div class="cmp-list">${PROS.map(([i, txt, d]) => `<div><span>${i}</span><div><b>${txt}</b><p>${d}</p></div></div>`).join("")}</div></div>
        <div class="card"><h3>${t("⚠️ Problemas", "⚠️ Drawbacks")}</h3><div class="cmp-list">${CONS.map(([i, txt, d]) => `<div><span>${i}</span><div><b>${txt}</b><p>${d}</p></div></div>`).join("")}</div></div></div>

      <div class="card"><h3>${t("¿Para qué sirve de verdad una IA local?", "What is a local AI really for?")}</h3><p class="muted small">${t("En cualquier lado, pero no en cualquier hardware: lo que va mejor en tu PC y lo que conviene dejar a la nube.", "Anywhere, but not on any hardware: what works best on your PC and what is better left to the cloud.")}</p>
        <div class="cmp-uses">${USES.map(([i, txt, w]) => `<div class="${w}"><span>${i}</span><p>${txt}</p><b>${w === "local" ? "Local" : t("Mejor en la nube", "Better in the cloud")}</b></div>`).join("")}</div></div>

      <div class="card"><h3>${t("Mínimos realistas", "Realistic minimums")}</h3><table class="cmp-min">${t(`<thead><tr><th>Tu memoria</th><th>Qué funciona bien</th><th>Para qué alcanza</th></tr></thead><tbody>
        <tr><td>8 GB de RAM, sin GPU</td><td>Modelos de 1–3B</td><td>Respuestas cortas y clasificar textos; lento con textos largos</td></tr>
        <tr><td>16 GB de RAM o GPU de 6–8 GB</td><td>4–9B; MoE de 30B con RAM</td><td>Asistente diario, resúmenes, correos, leer documentos</td></tr>
        <tr><td>GPU de 12–16 GB + 32 GB de RAM</td><td>14B rápido; 27–35B con ayuda de la RAM</td><td>Programar tareas acotadas, escribir textos largos</td></tr>
        <tr><td>GPU de 24 GB o 64 GB unificados</td><td>27–35B rápidos</td><td>El punto dulce para uso serio en casa</td></tr>
        <tr><td>128 GB o más</td><td>70–120B y MoE grandes</td><td>Lo más cerca de la nube que se puede tener en un escritorio</td></tr></tbody>`,
        `<thead><tr><th>Your memory</th><th>What works well</th><th>What it's enough for</th></tr></thead><tbody>
        <tr><td>8 GB of RAM, no GPU</td><td>1–3B models</td><td>Short answers and classifying text; slow with long texts</td></tr>
        <tr><td>16 GB of RAM or a 6–8 GB GPU</td><td>4–9B; 30B MoE with RAM</td><td>Daily assistant, summaries, emails, reading documents</td></tr>
        <tr><td>12–16 GB GPU + 32 GB of RAM</td><td>Fast 14B; 27–35B with help from RAM</td><td>Programming bounded tasks, writing long texts</td></tr>
        <tr><td>24 GB GPU or 64 GB unified</td><td>Fast 27–35B</td><td>The sweet spot for serious use at home</td></tr>
        <tr><td>128 GB or more</td><td>70–120B and large MoE</td><td>The closest to the cloud you can have on a desk</td></tr></tbody>`)}</table></div>

      <div class="card"><h3>${t("Páginas oficiales", "Official pages")}</h3><p class="small"><a href="https://claude.com/pricing" target="_blank" rel="noopener">Claude · ${t("precios", "pricing")}</a> ·
        <a href="https://gemini.google/subscriptions/" target="_blank" rel="noopener">Gemini · ${t("suscripciones", "subscriptions")}</a> ·
        <a href="https://openai.com/chatgpt/pricing/" target="_blank" rel="noopener">ChatGPT / Codex · ${t("precios", "pricing")}</a> ·
        <a href="https://github.com/openai/codex" target="_blank" rel="noopener">Codex CLI</a> ·
        <a href="https://github.com/google-gemini/gemini-cli" target="_blank" rel="noopener">Gemini CLI</a></p></div>`;
    const calc = () => {
      const sub = +$("#c-sub").value, have = $("#c-have").checked, hw = have ? 0 : Math.max(0, +$("#c-hw").value || 0);
      const kwhMonth = (+$("#c-h").value || 0) * (+$("#c-w").value || 0) / 1000 * 30, luz = kwhMonth * (+$("#c-kwh").value || 0);
      const save = sub - luz, months = save > 0 ? hw / save : Infinity;
      const y3 = { nube: sub * 36, local: hw + luz * 36 };
      const max = Math.max(y3.nube, y3.local, 1);
      const when = !isFinite(months) ? t("nunca", "never") : months < 1 ? t("menos de 1 mes", "under 1 month") : months > 240 ? t("más de 20 años", "over 20 years")
        : months < 24 ? Math.ceil(months) + t(" meses", " months") : (months / 12).toFixed(1) + t(" años", " years");
      $("#c-out").innerHTML = `<div class="cmp-kpis"><div><small>${t("Luz de la IA local", "Local AI electricity")}</small><b>${t(`${luz.toFixed(2)} $/mes`, `$${luz.toFixed(2)}/month`)}</b><span>${kwhMonth.toFixed(0)} kWh</span></div>
        <div><small>${t("Se amortiza en", "Pays off in")}</small><b>${when}</b>
        <span>${!isFinite(months) ? t("la luz cuesta más que la suscripción", "electricity costs more than the subscription") : t("frente a la suscripción", "versus the subscription")}</span></div></div>
        <div class="cmp-bars"><div><span>${t("Nube, 3 años", "Cloud, 3 years")}</span><i style="width:${y3.nube / max * 100}%"></i><b>${money(y3.nube)}</b></div>
        <div><span>${t("Local, 3 años", "Local, 3 years")}</span><i class="loc" style="width:${y3.local / max * 100}%"></i><b>${money(y3.local)}</b></div></div>
        <p class="muted small">${t("No incluye el valor del equipo para otros usos (juegos, trabajo) ni la diferencia de calidad entre modelos.", "It doesn't include the machine's value for other uses (games, work) or the quality difference between models.")}</p>`;
    };
    const vc = $("#view-compare");
    if (!vc.dataset.bound) { vc.dataset.bound = "1"; vc.addEventListener("input", () => window.LAIGuide.calc?.()); vc.addEventListener("change", () => window.LAIGuide.calc?.()); }
    window.LAIGuide.calc = calc; calc();
  }

  /* ------------------------------------------------------------------ componentes */
  let compData = null;
  async function renderComponents() {
    const box = $("#view-components");
    if (!compData) {
      box.innerHTML = `<p class="muted">${t("Cargando componentes…", "Loading components…")}</p>`;
      try { compData = await window.LAI.api("/api/components"); }
      catch { box.innerHTML = `<div class="card">${t("El asistente ya se cerró. Para ver los componentes usa <code>/componentes demo</code> en Clay-Myrrh.", "The assistant has already closed. To see the components use <code>/components demo</code> in Clay-Myrrh.")}</div>`; return; }
    }
    box.innerHTML = `<div class="cmp-hero"><div><span class="eyebrow">${t("Componentes prediseñados", "Prebuilt components")}</span><h2>${t(`${compData.total} piezas listas para tus webs`, `${compData.total} pieces ready for your websites`)}</h2>
      <p class="lead">${t(`La IA solo escribe etiquetas como <code>&lt;menu&gt;</code> o <code>&lt;wizard&gt;</code> con el contenido, y Clay-Myrrh genera el HTML, el CSS y el JavaScript ya probados.
      Así gasta unas 4 veces menos tokens y el diseño nunca se rompe. Úsalos con <code>/plantilla</code>, con <code>/zero</code> o pidiéndole una página en el chat.`,
      `The AI only writes tags like <code>&lt;menu&gt;</code> or <code>&lt;wizard&gt;</code> with the content, and Clay-Myrrh generates the tested HTML, CSS and JavaScript.
      That uses about 4 times fewer tokens and the design never breaks. Use them with <code>/template</code>, with <code>/zero</code> or by asking for a page in the chat. (Component descriptions are in Spanish.)`)}</p>
      <div class="row" style="flex-wrap:wrap"><a class="btn primary" href="/demo/" target="_blank" rel="noopener">${t("Abrir la demo en otra pestaña ↗", "Open the demo in another tab ↗")}</a>
      <input id="comp-q" type="search" placeholder="${t("Buscar componente…", "Search components…")}" aria-label="${t("Buscar componente", "Search components")}" style="max-width:280px"></div></div></div>
      <div class="comp-layout"><div id="comp-groups">${compData.groups.map((g) => `<section class="comp-group"><h3>${esc(g.group)} <span class="muted small">${g.items.length}</span></h3>
        <div class="comp-grid">${g.items.map((c) => `<details class="comp" data-name="${esc(c.name)} ${esc(c.desc.toLowerCase())}"><summary><code>&lt;${esc(c.name)}&gt;</code><span>${esc(c.desc)}</span></summary>
          ${c.attrs ? `<p class="small"><b>${t("Atributos", "Attributes")}:</b> <code>${esc(c.attrs)}</code></p>` : ""}${c.items ? `<p class="small"><b>${t("Ítems", "Items")}:</b> <code>${esc(c.items)}</code></p>` : ""}</details>`).join("")}</div></section>`).join("")}</div>
      <div class="comp-preview card"><div class="row between"><b>${t("Demo en vivo", "Live demo")}</b><span class="muted small">${t("la misma que abre <code>/componentes demo</code>", "the same one <code>/components demo</code> opens")}</span></div>
        <iframe src="/demo/" title="${t("Demo de componentes", "Components demo")}" loading="lazy"></iframe></div></div>`;
    $("#comp-q").addEventListener("input", (e) => { const q = e.target.value.toLowerCase().trim();
      $$(".comp", box).forEach((d) => { d.hidden = q && !d.dataset.name.toLowerCase().includes(q); });
      $$(".comp-group", box).forEach((g) => { g.hidden = !$$(".comp", g).some((d) => !d.hidden); }); });
  }

  /* ------------------------------------------------------------------ arranque */
  $$(".guide-cta-mascots").forEach((el) => { el.innerHTML = ["claude", "gemini", "codex", "local"].map((k) => `<div class="g-mascot">${MASCOT[k]}</div>`).join(""); });
  const start = () => {          // enlaces directos: #guia, #guia-3, #comparativa, #componentes
    const h = decodeURIComponent(location.hash);
    const m = h.match(/^#(?:gu[ií]a|guide)(?:-(\d+))?$/);
    if (m) { open(); if (m[1]) render(+m[1] - 1); return; }
    if (h === "#comparativa" || h === "#comparison") return setView("compare");
    if (h === "#componentes" || h === "#components") return setView("components");
    if (h === "#instalacion" || h === "#setup") return;
    if (!store.get("lai-guide-never", false)) open();
  };
  if (window.LAI) window.LAI.onReady.push(start); else start();
  window.LAIGuide = { open, close, setView };
})();
