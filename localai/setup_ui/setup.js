/* Clay-Myrrh · asistente de instalación visual */
(() => {
  "use strict";
  const TOKEN = document.querySelector('meta[name="setup-token"]').content;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const gb = (b) => (b / 1024 ** 3).toFixed(1) + " GB";
  // idioma: lo pone el servidor (config del usuario o ?lang=); t(español, inglés) como T() en Python
  const LANG = document.querySelector('meta[name="setup-lang"]')?.content === "en" ? "en" : "es";
  const t = (es, en) => (LANG === "en" ? en : es);
  window.LAI_T = t;
  window.LAI_LANG = LANG;
  if (LANG === "en") {
    document.title = "Clay-Myrrh · Setup assistant";
    $$("[data-en]").forEach((el) => { el.innerHTML = el.dataset.en; });
    $$("[data-en-label]").forEach((el) => el.setAttribute("aria-label", el.dataset.enLabel));
  }
  const sw = $("#lang-switch");
  if (sw) { sw.textContent = LANG === "en" ? "ES" : "EN"; sw.href = "?lang=" + (LANG === "en" ? "es" : "en");
    sw.title = LANG === "en" ? "Español" : "English"; }
  const cmd = (es, en) => t(es, en);          // nombres de comando de Clay-Myrrh según el idioma
  const api = async (path, body) => {
    const r = await fetch(path, { method: body === undefined ? "GET" : "POST", headers: { "X-Setup-Token": TOKEN, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return r.json();
  };

  const STEPS = t(["Bienvenida", "Tu equipo", "Usos", "Niveles", "Resumen", "Descarga", "Listo"],
    ["Welcome", "Your machine", "Uses", "Levels", "Summary", "Download", "Done"]);
  const GLOSSARY = LANG === "en" ? {
    token: ["Token", "A small piece of text (≈ ¾ of a word). Speed is measured in tokens per second (t/s): reading aloud is ≈3 t/s; reading quickly in silence, ≈8 t/s. Above 20 t/s it feels instant."],
    bandwidth: ["Bandwidth", "How much data per second the memory can move. To write each token the AI reads the whole model, so bandwidth decides the speed far more than the processor's GHz."],
    vram: ["VRAM", "The graphics card's memory. It's very fast (hundreds of GB/s): whatever fits here runs 5 to 10 times faster than in RAM."],
    ram: ["RAM", "The computer's memory. Slower than VRAM (tens of GB/s). If the model doesn't fit entirely in VRAM, the rest goes here and slows down."],
    mts: ["MT/s and channels", "RAM speed (e.g. DDR4-3600 = 3600 MT/s). With two sticks in dual channel the bandwidth doubles; that's why RAM should go in pairs."],
    moe: ["MoE (mixture of experts)", "Models with many parameters that only use part of them for each token (e.g. 35B in total, 3B active). They think like a big one and write almost as fast as a small one: ideal if you have little VRAM."],
    quant: ["Quantisation (Q4, Q8…)", "Compressing the model by storing each number with fewer bits. Q4 takes ≈¼ of the original and loses very little quality; Q8 is almost identical to the original but takes twice as much as Q4."],
    mmproj: ["mmproj (the «eye»)", "An extra file that lets a model see images. Clay-Myrrh uses a model with an eye as a «reader» to transcribe photos and scanned PDFs, and your main model works with that text."],
    context: ["Context", "The conversation's memory, measured in tokens. More context allows longer documents, but takes more memory."],
    params: ["Parameters (B)", "The size of the model's «brain» in billions. More parameters usually give better answers, but take more space and run slower."],
    cloud: ["Local or cloud", "Local models are private, free and work offline. Cloud AIs (like Claude) are still better at long, complex tasks: large code projects, documents with hundreds of pages or very precise reasoning."],
  } : {
    token: ["Token", "Un trocito de texto (≈ ¾ de palabra). La velocidad se mide en tokens por segundo (t/s): leer en voz alta son ≈3 t/s; leer rápido en silencio, ≈8 t/s. Por encima de 20 t/s se siente instantáneo."],
    bandwidth: ["Ancho de banda", "Cuántos datos por segundo puede mover la memoria. Para escribir cada token la IA lee el modelo entero, así que el ancho de banda decide la velocidad mucho más que los GHz del procesador."],
    vram: ["VRAM", "La memoria de la tarjeta gráfica. Es muy rápida (cientos de GB/s): lo que quepa aquí va de 5 a 10 veces más rápido que en la RAM."],
    ram: ["RAM", "La memoria del ordenador. Más lenta que la VRAM (decenas de GB/s). Si el modelo no cabe entero en la VRAM, el resto va aquí y se ralentiza."],
    mts: ["MT/s y canales", "La velocidad de la RAM (p. ej. DDR4-3600 = 3600 MT/s). Con dos módulos en doble canal el ancho de banda se duplica; por eso conviene poner la RAM por pares."],
    moe: ["MoE (mezcla de expertos)", "Modelos con muchos parámetros que solo usan una parte para cada token (p. ej. 35B en total, 3B activos). Piensan como uno grande y escriben casi tan rápido como uno pequeño: ideales si tienes poca VRAM."],
    quant: ["Cuantización (Q4, Q8…)", "Comprimir el modelo guardando cada número con menos bits. Q4 ocupa ≈¼ del original y pierde muy poca calidad; Q8 es casi idéntico al original pero ocupa el doble que Q4."],
    mmproj: ["mmproj (el «ojo»)", "Un archivo extra que permite a un modelo ver imágenes. Clay-Myrrh usa un modelo con ojo como «lector» para transcribir fotos y PDFs escaneados, y tu modelo principal trabaja con ese texto."],
    context: ["Contexto", "La memoria de la conversación, medida en tokens. Más contexto permite documentos más largos, pero ocupa más memoria."],
    params: ["Parámetros (B)", "El tamaño del «cerebro» del modelo en miles de millones. Más parámetros suelen dar mejores respuestas, pero ocupan más y van más lentos."],
    cloud: ["Local o nube", "Los modelos locales son privados, gratuitos y sin internet. Las IA en la nube (como Claude) siguen siendo mejores en tareas largas y complejas: proyectos grandes de código, documentos de cientos de páginas o razonamiento muy preciso."],
  };
  const DEMO_TEXT = LANG === "en" ? "Sure. To make a good filter coffee use about 15 grams of medium-ground coffee for every 250 ml of water at 92 °C. First wet the coffee with a little water and wait 30 seconds so it releases its gas; then pour the rest in slow circles. The whole process should take between two and a half and three minutes. If it comes out bitter, grind a little coarser; if it's sour or watery, finer." : "Claro. Para preparar un buen café de filtro usa unos 15 gramos de café molido medio por cada 250 ml de agua a 92 °C. Moja primero el café con un poco de agua y espera 30 segundos para que suelte el gas; después vierte el resto en círculos lentos. El proceso completo debería durar entre dos minutos y medio y tres minutos. Si sale amargo, muele un poco más grueso; si sale ácido o aguado, más fino.";
  const RAM_GB = [4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024];
  const VRAM_GB = [0, 2, 4, 6, 8, 10, 11, 12, 16, 20, 24, 32, 40, 48, 64, 80, 96, 128, 160, 192];
  const MTS = [2133, 2400, 2666, 2933, 3200, 3600, 4000, 4800, 5200, 5600, 6000, 6400, 7200, 8000, 8533];

  const S = { step: 0, state: null, hw: null, rec: null, cats: new Set(["utilidad", "programacion", "lectura"]), choices: {}, primary: null,
    summary: null, poll: null, demo: null };
  // la guía y las pestañas (guide.js) leen el equipo detectado o simulado
  window.LAI = { get state() { return S.state; }, get hw() { return S.hw; }, get rec() { return S.rec; }, api,
    onReady: [], go: (step) => show(step) };

  /* ------------------------------------------------------------------ pasos */
  function renderStepper() {
    $("#stepper").innerHTML = STEPS.map((n, i) => `<li class="${i === S.step ? "active" : i < S.step ? "done" : ""}"><span>${n}</span></li>`).join("");
  }
  function show(step) {
    S.step = step;
    $$(".step").forEach((el) => { el.hidden = +el.dataset.step !== step; });
    renderStepper();
    const next = $("#next"), back = $("#back"), hint = $("#nav-hint");
    back.hidden = step === 0 || step >= 5; hint.textContent = ""; next.disabled = false;
    next.textContent = t(["Empezar →", "Siguiente →", "Siguiente →", "Ver resumen →", "", "", "Cerrar pestaña"],
      ["Start →", "Next →", "Next →", "See summary →", "", "", "Close tab"])[step];
    if (step === 1) renderHardware();
    if (step === 2) renderCats();
    if (step === 3) renderLevels();
    if (step === 4) renderSummary();
    if (step === 5) next.hidden = true; else next.hidden = false;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  $("#back").addEventListener("click", () => show(Math.max(0, S.step - 1)));
  $("#next").addEventListener("click", async () => {
    if (S.step === 2 && !S.cats.size) return;
    if (S.step === 4) return startDownloadOrApply();
    if (S.step === 6) { window.close(); document.body.innerHTML = '<div class="done" style="margin-top:20vh"><div class="done-icon">👋</div><h2>' + t("Asistente cerrado", "Assistant closed") + '</h2><p class="lead" style="margin:auto">' + t("Ya puedes cerrar esta pestaña.", "You can close this tab now.") + '</p></div>'; return; }
    show(S.step + 1);
  });

  /* ------------------------------------------------------------------ hardware */
  const nearest = (arr, v) => arr.reduce((b, x) => (Math.abs(x - v) < Math.abs(b - v) ? x : b), arr[0]);
  function field(label, html, help = "") {
    return `<div class="hw-field"><label>${label}${help ? ` <button class="info" type="button" data-term="${help}">?</button>` : ""}</label>${html}</div>`;
  }
  function renderHardware() {
    const hw = S.hw, det = S.state.hardware;
    const gpuOpts = [`<option value="">${t("Sin GPU dedicada", "No dedicated GPU")}</option>`].concat(S.state.gpus.map(([n, bw]) =>
      `<option value="${esc(n)}" ${hw.gpu_name && hw.gpu_name.toUpperCase().includes(n) ? "selected" : ""}>${esc(n)} · ${bw} GB/s</option>`));
    const gpuKnown = !hw.gpu_name || S.state.gpus.some(([n]) => hw.gpu_name.toUpperCase().includes(n));
    if (hw.gpu_name && !gpuKnown) gpuOpts.push(`<option value="__custom" selected>${esc(hw.gpu_name)}</option>`);
    $("#hw-form").innerHTML = [
      field(`RAM <span id="v-ram">${hw.ram_gb} GB</span>`, `<input type="range" id="f-ram" min="0" max="${RAM_GB.length - 1}" value="${RAM_GB.indexOf(nearest(RAM_GB, hw.ram_gb))}">`, "ram"),
      field(t("Velocidad RAM", "RAM speed"), `<select id="f-mts">${MTS.map((m) => `<option ${m === nearest(MTS, hw.ram_mts || 3200) ? "selected" : ""}>${m}</option>`).join("")}</select>`, "mts"),
      field(t("Canales de RAM", "RAM channels"), `<select id="f-ch">${[1, 2, 4, 6, 8, 12].map((c) => `<option ${c === hw.ram_channels ? "selected" : ""}>${c}</option>`).join("")}</select>`, "mts"),
      field(t("Tarjeta gráfica", "Graphics card"), `<select id="f-gpu">${gpuOpts.join("")}</select>`, "vram"),
      field(`VRAM <span id="v-vram">${hw.vram_gb} GB</span>`, `<input type="range" id="f-vram" min="0" max="${VRAM_GB.length - 1}" value="${VRAM_GB.indexOf(nearest(VRAM_GB, hw.vram_gb))}">`, "vram"),
      field(`${t("Ancho de banda GPU", "GPU bandwidth")} <span id="v-bw">${hw.gpu_bw} GB/s</span>`, `<input type="range" id="f-bw" min="0" max="2000" step="8" value="${hw.gpu_bw}">`, "bandwidth"),
      field(t("Procesador", "Processor"), `<div class="small muted">${esc(hw.cpu_name)} · ${hw.cpu_cores} ${t("núcleos", "cores")} · ${hw.cpu_ghz} GHz ${det.detected.includes("cpu") && hw.cpu_name === det.cpu_name ? `<span class="detected-tag">${t("✓ detectado", "✓ detected")}</span>` : ""}</div>`),
      field(t("Disco libre", "Free disk"), `<div class="small muted">${hw.disk_free_gb} ${t("GB en la carpeta de modelos", "GB in the models folder")}</div>`),
    ].join("");
    const upd = () => { recompute(); };
    $("#f-ram").addEventListener("input", (e) => { hw.ram_gb = RAM_GB[+e.target.value]; $("#v-ram").textContent = hw.ram_gb + " GB"; upd(); });
    $("#f-vram").addEventListener("input", (e) => { hw.vram_gb = VRAM_GB[+e.target.value]; $("#v-vram").textContent = hw.vram_gb + " GB"; upd(); });
    $("#f-bw").addEventListener("input", (e) => { hw.gpu_bw = +e.target.value; $("#v-bw").textContent = hw.gpu_bw + " GB/s"; upd(); });
    $("#f-mts").addEventListener("change", (e) => { hw.ram_mts = +e.target.value; upd(); });
    $("#f-ch").addEventListener("change", (e) => { hw.ram_channels = +e.target.value; upd(); });
    $("#f-gpu").addEventListener("change", (e) => {
      const v = e.target.value;
      if (!v) { hw.gpu_name = ""; hw.vram_gb = 0; hw.gpu_bw = 0; }
      else if (v !== "__custom") { const g = S.state.gpus.find(([n]) => n === v); hw.gpu_name = v; hw.gpu_bw = g[1]; if (!hw.vram_gb) hw.vram_gb = 8; }
      renderHardware(); upd();
    });
    drawLanes();
  }
  function drawLanes() {
    const hw = S.hw;
    const ramBw = (hw.ram_mts || 3200) * 8 * Math.max(1, hw.ram_channels) / 1000;
    const maxCap = Math.max(hw.vram_gb, hw.ram_gb, 1), maxBw = Math.max(hw.gpu_bw, ramBw, 1);
    const lane = (cls, name, cap, bw, desc) => {
      const h = 10 + 30 * Math.log10(1 + 9 * bw / maxBw);
      const w = Math.max(14, 100 * cap / maxCap);
      const dur = Math.max(0.25, Math.min(6, 60 / Math.max(bw, 1)));
      return `<div class="lane"><div class="lane-head"><span><b>${name}</b> · ${cap} GB</span><span>${bw.toFixed(0)} GB/s · ${desc}</span></div>
        <div class="pipe ${cls}" style="height:${h.toFixed(0)}px;width:${w.toFixed(0)}%"><div class="flow" style="animation-duration:${dur}s"></div></div></div>`;
    };
    $("#lanes").innerHTML = (hw.vram_gb >= 2 && hw.gpu_bw ? lane("gpu", t("VRAM (tarjeta gráfica)", "VRAM (graphics card)"), hw.vram_gb, hw.gpu_bw, t("carril rápido", "fast lane")) :
      `<div class="callout info small">${t("Sin tarjeta gráfica dedicada: todo irá por la RAM. Funcionan bien los modelos pequeños (hasta ~4B) y los MoE.", "No dedicated graphics card: everything goes through RAM. Small models (up to ~4B) and MoE models work well.")}</div>`)
      + lane("ram", t("RAM (ordenador)", "RAM (computer)"), hw.ram_gb, ramBw, hw.vram_gb >= 2 ? t("carril lento", "slow lane") : t("único carril", "only lane"));
    $("#hw-class").textContent = S.rec ? t("Tu equipo: ", "Your machine: ") + S.rec.class : "";
  }
  let recTimer = null;
  function recompute(now = false) {
    clearTimeout(recTimer);
    const run = async () => { S.rec = await api("/api/recommend", { hw: S.hw }); drawLanes(); };
    if (now) return run();
    recTimer = setTimeout(run, 250);
  }
  $("#preset").addEventListener("change", (e) => {
    const p = S.state.presets[+e.target.value]; if (!p) return;
    Object.assign(S.hw, p.hw, { cpu_name: t("(equipo simulado)", "(simulated machine)"), cpu_cores: 8, cpu_ghz: 4.0 });
    renderHardware(); recompute();
  });
  $("#hw-reset").addEventListener("click", () => { S.hw = structuredClone(S.state.hardware); $("#preset").value = ""; renderHardware(); recompute(); });

  /* ------------------------------------------------------------------ demo de velocidad */
  function demo(tps, label) {
    clearInterval(S.demo);
    const box = $("#demo-box") && S.step === 1 ? $("#demo-box") : $("#tier-demo");
    if (!box) return;
    const pieces = DEMO_TEXT.match(/.{1,4}/gs);
    let i = 0; const t0 = performance.now();
    box.innerHTML = "";
    const out = document.createElement("span"); const cur = document.createElement("span"); cur.className = "cursor";
    const meta = document.createElement("span"); meta.className = "meta";
    box.append(out, cur, meta);
    S.demo = setInterval(() => {
      const el = (performance.now() - t0) / 1000;
      const target = Math.min(pieces.length, Math.floor(el * tps));
      if (target > i) { out.textContent += pieces.slice(i, target).join(""); i = target; }
      meta.textContent = `${label} · ${tps} tokens/s · ${el.toFixed(1)} s`;
      if (i >= pieces.length) { clearInterval(S.demo); cur.remove(); }
    }, 40);
  }
  $("#demo-buttons").innerHTML = [[2, t("2 t/s · muy lento", "2 t/s · very slow")], [5, t("5 t/s · leer en voz alta", "5 t/s · reading aloud")], [10, t("10 t/s · cómodo", "10 t/s · comfortable")], [25, t("25 t/s · fluido", "25 t/s · smooth")], [60, t("60 t/s · instantáneo", "60 t/s · instant")]]
    .map(([t, l]) => `<button class="btn ghost small" data-tps="${t}" type="button">${l}</button>`).join("");
  $("#demo-buttons").addEventListener("click", (e) => { const b = e.target.closest("[data-tps]"); if (b) demo(+b.dataset.tps, b.textContent); });

  /* ------------------------------------------------------------------ usos */
  function renderCats() {
    $("#cats").innerHTML = S.state.categories.map((c) => `<button type="button" class="cat ${S.cats.has(c.key) ? "on" : ""}" data-cat="${c.key}" aria-pressed="${S.cats.has(c.key)}">
      <span class="check">${S.cats.has(c.key) ? "✓" : ""}</span><div class="cicon">${c.icon}</div><h3>${esc(c.name)}</h3><p>${esc(c.desc)}</p>
      ${c.needs_vision ? `<span class="vis">${t("👁 usa visión", "👁 uses vision")}</span>` : ""}</button>`).join("");
    $("#nav-hint").textContent = S.cats.size ? t(`${S.cats.size} seleccionado(s)`, `${S.cats.size} selected`) : t("Elige al menos uno", "Choose at least one");
    $("#next").disabled = !S.cats.size;
  }
  $("#cats").addEventListener("click", (e) => {
    const b = e.target.closest("[data-cat]"); if (!b) return;
    const k = b.dataset.cat; S.cats.has(k) ? S.cats.delete(k) : S.cats.add(k); renderCats();
  });

  /* ------------------------------------------------------------------ niveles */
  function speedColor(t) { return t >= 20 ? "var(--ok)" : t >= 8 ? "var(--accent-2)" : t >= 3 ? "var(--warn)" : "var(--bad)"; }
  function tierCard(cat, tkey, m, title) {
    if (!m) return `<button class="tier" disabled type="button"><span class="tname">${title}</span><span class="mname">${t("No cabe", "Doesn't fit")}</span><span class="note">${t("Ningún modelo de este uso cabe en tu memoria.", "No model for this use fits in your memory.")}</span></button>`;
    const on = S.choices[cat.key] === m.key;
    const g = Math.round(m.gpu_frac * 100);
    const pct = Math.min(100, Math.log10(1 + m.tps) / Math.log10(101) * 100);
    const tags = [m.installed ? `<span class="tag ok">${t("✓ ya instalado", "✓ already installed")}</span>` : `<span class="tag">${t("descarga", "download")} ${m.size_gb.toFixed(1)} GB</span>`,
      m.moe ? `<span class="tag moe" title="${t("Mezcla de expertos", "Mixture of experts")}">MoE · ${m.active_b}B ${t("activos", "active")}</span>` : `<span class="tag">${m.params_b}B</span>`,
      m.mmproj_missing ? `<span class="tag warn">${t("+ descarga su «ojo»", "+ downloads its «eye»")}</span>` : ""].join("");
    return `<button type="button" class="tier ${on ? "on" : ""}" data-cat="${cat.key}" data-model="${m.key}">
      <span class="tname">${title}</span><span class="mname">${esc(m.name)}</span><span class="note">${esc(m.note)}</span>
      <div class="tags">${tags}</div>
      <div class="membar" title="${t("Dónde se guarda el modelo", "Where the model is kept")}">${m.where === "CPU" ? `<span class="r" style="width:100%"></span>` : `<span class="g" style="width:${g}%"></span><span class="r" style="width:${100 - g}%"></span>`}</div>
      <div class="memlegend"><span><i style="background:var(--gpu)"></i>VRAM ${m.where === "CPU" ? 0 : g}%</span><span><i style="background:var(--ram)"></i>RAM ${m.where === "CPU" ? 100 : 100 - g}%</span><span>${m.mem_gb} ${t("GB en total", "GB in total")}</span></div>
      <div class="gauge"><div class="meter"><span style="width:${pct}%;background:${speedColor(m.tps)}"></span></div><b>${Math.round(m.tps_low)}–${Math.round(m.tps_high)} t/s</b></div>
      <span class="note">${esc(m.speed_words)}</span>
      <span class="btn ghost small try" data-try="${m.tps}" data-name="${esc(m.name)}">${t("▶ Probar velocidad", "▶ Try the speed")}</span></button>`;
  }
  function renderLevels() {
    if (!S.rec) { $("#levels").innerHTML = `<p class="muted">${t("Calculando…", "Calculating…")}</p>`; recompute(true).then(renderLevels); return; }
    $("#tier-legend").innerHTML = Object.entries(S.state.tiers).map(([k, t]) => `<div><b>${t.name}.</b> <span class="muted">${esc(t.desc)}</span></div>`).join("");
    const html = [];
    for (const c of S.state.categories.filter((x) => S.cats.has(x.key))) {
      const r = S.rec.categories[c.key];
      if (!(c.key in S.choices)) {
        const def = r.tiers.moderado || r.tiers.basico || r.tiers.extensivo;
        S.choices[c.key] = def ? def.key : null;
      }
      const cards = Object.entries(r.tiers).map(([tk, m]) => tierCard(c, tk, m, S.state.tiers[tk].name));
      r.installed.forEach((m) => cards.push(tierCard(c, "inst", m, t("Ya lo tienes", "You already have it"))));
      html.push(`<div class="level"><div class="level-head"><span class="cicon">${c.icon}</span><div><h3 style="margin:0">${esc(c.name)}</h3>
        <span class="muted small">${esc(c.desc)}</span></div></div><div class="tiers">${cards.join("")}</div>
        <div class="callout cloud small"><span>☁️</span><span><b>${t("¿Local o nube?", "Local or cloud?")}</b> ${esc(c.cloud)}</span></div>
        <button type="button" class="link small skip" data-skip="${c.key}">${S.choices[c.key] ? t("Omitir este uso", "Skip this use") : t("✓ Omitido · pulsa una tarjeta para elegir", "✓ Skipped · press a card to choose")}</button></div>`);
    }
    $("#levels").innerHTML = '<div class="typing" id="tier-demo" style="min-height:80px;margin-bottom:1.5rem">' + t("Pulsa «▶ Probar velocidad» en cualquier tarjeta para ver a qué ritmo escribiría ese modelo en tu equipo.", "Press «▶ Try the speed» on any card to see how fast that model would write on your machine.") + '</div>' + html.join("");
  }
  $("#levels").addEventListener("click", (e) => {
    const t = e.target.closest("[data-try]");
    if (t) { e.stopPropagation(); demo(Math.max(1, Math.round(+t.dataset.try)), t.dataset.name); $("#tier-demo").scrollIntoView({ behavior: "smooth", block: "center" }); return; }
    const sk = e.target.closest("[data-skip]");
    if (sk) { S.choices[sk.dataset.skip] = null; renderLevels(); return; }
    const card = e.target.closest(".tier[data-model]");
    if (card) { S.choices[card.dataset.cat] = card.dataset.model; renderLevels(); }
  });

  /* ------------------------------------------------------------------ resumen */
  const chosen = () => Object.fromEntries(Object.entries(S.choices).filter(([k, v]) => v && S.cats.has(k)));
  function modelInfo(cat, key) {
    const r = S.rec.categories[cat]; return Object.values(r.tiers).concat(r.installed).find((m) => m && m.key === key);
  }
  async function renderSummary() {
    const ch = chosen(), cats = S.state.categories;
    const usable = Object.keys(ch).filter((k) => k !== "lectura");
    if (!usable.includes(S.primary)) S.primary = usable[0] || null;
    $("#sum-choices").innerHTML = Object.entries(ch).map(([k, mk]) => {
      const c = cats.find((x) => x.key === k), m = modelInfo(k, mk);
      return `<div class="sumrow"><span>${c.icon} ${esc(c.name)}</span><span><b>${esc(m.name)}</b> <span class="muted small">${m.installed ? t("instalado", "installed") : m.size_gb.toFixed(1) + " GB"}</span></span></div>`;
    }).join("") || `<p class="muted">${t("No elegiste ningún modelo.", "You didn't choose any model.")}</p>`;
    $("#sum-primary").innerHTML = usable.map((k) => { const c = cats.find((x) => x.key === k);
      return `<label><input type="radio" name="primary" value="${k}" ${k === S.primary ? "checked" : ""}> ${c.icon} ${esc(c.name)} <span class="muted small">— ${esc(modelInfo(k, ch[k]).name)}</span></label>`; }).join("")
      || `<p class="muted small">${t("(solo elegiste el lector: se mantiene tu modelo actual)", "(you only chose the reader: your current model is kept)")}</p>`;
    $$("#sum-primary input").forEach((r) => r.addEventListener("change", () => { S.primary = r.value; }));
    $("#sum-files").innerHTML = `<p class="muted">${t("Consultando Hugging Face…", "Querying Hugging Face…")}</p>`; $("#sum-disk").innerHTML = "";
    $("#next").disabled = true;
    try {
      S.summary = await api("/api/summary", { hw: S.hw, choices: ch, primary: S.primary });
      const s = S.summary;
      $("#sum-files").innerHTML = s.error ? `<div class="callout warn">${esc(s.error)}</div>` : s.files.length ? s.files.map((f) =>
        `<div class="file"><div class="row"><span>${esc(f.name)} <span class="muted small">(${esc(f.folder)})</span></span><b>${gb(f.size)}</b></div></div>`).join("")
        : `<div class="callout info">${t("Todo lo elegido ya está instalado: solo aplicaré la configuración.", "Everything you chose is already installed: I'll only apply the settings.")}</div>`;
      const pct = s.disk_free ? Math.min(100, s.total / s.disk_free * 100) : 0;
      $("#sum-disk").innerHTML = s.total ? `<div style="margin-top:1rem"><div class="row between small"><span>Total: <b>${gb(s.total)}</b></span><span class="muted">${t("libre en disco", "free on disk")}: ${gb(s.disk_free)}</span></div>
        <div class="bar"><span style="width:${pct.toFixed(1)}%"></span></div><p class="muted small" style="margin-top:.5rem">≈ ${Math.max(1, Math.round(s.total / (50 * 1024 ** 2) / 60))} min ${t("a", "at")} 50 MB/s</p>
        <button class="link small" id="save-only" type="button">${t("Solo guardar el plan (descargar luego con clay-myrrh --download)", "Only save the plan (download later with clay-myrrh --download)")}</button></div>` : "";
      $("#save-only")?.addEventListener("click", async () => { await api("/api/summary", { hw: S.hw, choices: ch, primary: S.primary });
        $("#nav-hint").textContent = t("Plan guardado. Ejecuta «clay-myrrh --download» cuando quieras.", "Plan saved. Run «clay-myrrh --download» whenever you like."); });
      $("#next").textContent = s.total ? t("Descargar y configurar →", "Download and set up →") : t("Aplicar configuración →", "Apply settings →");
      $("#next").disabled = !Object.keys(ch).length || !!s.error;
      if (s.total > s.disk_free) { $("#next").disabled = true; $("#nav-hint").textContent = t("No hay espacio suficiente en el disco", "There isn't enough disk space"); }
    } catch (err) { $("#sum-files").innerHTML = `<div class="callout warn">${esc(err.message)}</div>`; }
  }

  /* ------------------------------------------------------------------ descarga */
  async function startDownloadOrApply() {
    await api("/api/summary", { hw: S.hw, choices: chosen(), primary: S.primary });
    if (!S.summary || !S.summary.total) { const r = await api("/api/apply", {}); return finish(r); }
    const r = await api("/api/download", {});
    if (!r.ok) { $("#nav-hint").textContent = r.message; return; }
    show(5); poll();
  }
  function poll() {
    clearInterval(S.poll);
    S.poll = setInterval(pollTick, 600);
    pollTick();
  }
  async function pollTick() {
    const p = await api("/api/progress").catch(() => null); if (!p || S.step !== 5) return;
    $("#dl-message").textContent = p.message || "";
    $("#dl-bar").style.width = p.total ? (p.done / p.total * 100).toFixed(1) + "%" : "0";
    $("#dl-bytes").textContent = p.total ? `${gb(p.done)} ${t("de", "of")} ${gb(p.total)}` : "";
    $("#dl-speed").textContent = p.speed ? `${(p.speed / 1024 ** 2).toFixed(1)} MB/s` : "";
    $("#dl-eta").textContent = p.eta != null ? `${t("quedan", "")} ${p.eta > 90 ? Math.round(p.eta / 60) + " min" : Math.round(p.eta) + " s"}${t("", " left")}` : "";
    $("#dl-files").innerHTML = p.files.map((f) => `<div class="file"><div class="row"><span>${f.state === "hecho" ? "✅" : f.state === "descargando" ? "⬇️" : "⏳"} ${esc(f.name)}</span>
      <span class="muted small">${gb(f.done)} / ${gb(f.size)}</span></div><div class="bar"><span style="width:${(f.done / f.size * 100).toFixed(1)}%"></span></div></div>`).join("")
      + (p.state === "running" ? '<button class="btn ghost small" id="dl-cancel" type="button" style="justify-self:start">' + t("⏸ Pausar", "⏸ Pause") + '</button>' : "")
      + (["error", "cancelled"].includes(p.state) ? '<button class="btn primary small" id="dl-retry" type="button" style="justify-self:start">' + t("↻ Reanudar", "↻ Resume") + '</button>' : "");
    $("#dl-cancel")?.addEventListener("click", () => api("/api/cancel", {}));
    $("#dl-retry")?.addEventListener("click", async () => { await api("/api/download", {}); poll(); });
    if (p.state === "done") { clearInterval(S.poll); S.poll = null; finish({}); }
    if (["error", "cancelled"].includes(p.state)) { clearInterval(S.poll); S.poll = null; }
  }
  function finish() {
    const ch = chosen(), cats = S.state.categories;
    const prim = S.primary ? modelInfo(S.primary, ch[S.primary]) : null;
    const lec = ch.lectura ? modelInfo("lectura", ch.lectura) : null;
    const primName = prim ? cats.find((c) => c.key === S.primary).name : "";
    $("#done-text").innerHTML = t(`Abre <b>Clay-Myrrh</b> con el acceso directo o con <code>clay-myrrh.cmd</code>.`,
        `Open <b>Clay-Myrrh</b> with the shortcut or with <code>clay-myrrh.cmd</code>.`)
      + (prim ? t(` Se cargará <b>${esc(prim.name)}</b> (${esc(primName)}).`, ` It will load <b>${esc(prim.name)}</b> (${esc(primName)}).`) : "")
      + (lec ? t(` Las imágenes y PDFs escaneados los leerá <b>${esc(lec.name)}</b>.`, ` Images and scanned PDFs will be read by <b>${esc(lec.name)}</b>.`) : "");
    const profiles = Object.keys(ch).filter((k) => k !== "lectura").map((k) => `<code>${cmd("/modo", "/mode")} ${k}</code>`).join(" · ");
    $("#cheats").innerHTML = (LANG === "en" ? [
      ["🔁", "Switch use", profiles || "<code>/model</code>", "Loads that use's model and settings."],
      ["📎", "Attach files", "<code>@document.pdf what is it about?</code>", "Images, PDFs and code; scanned ones are transcribed by the reader."],
      ["🧱", "Build a website", "<code>/template</code> · <code>/zero</code>", "57 prebuilt components: fewer tokens and a tested design (<code>/components demo</code>)."],
      ["❓", "Help", "<code>/help</code> · <code>?</code>", "All the commands and keyboard shortcuts."],
      ["⚙️", "Settings", "<code>/settings</code>", "Language, theme, reasoning, vision, tokens…"],
      ["🧭", "Come back here", "<code>instalar-clay-myrrh.bat</code>", "To add uses or change level."],
    ] : [
      ["🔁", "Cambiar de uso", profiles || "<code>/modelo</code>", "Carga el modelo y los ajustes de ese uso."],
      ["📎", "Adjuntar archivos", "<code>@documento.pdf ¿de qué trata?</code>", "Imágenes, PDFs y código; los escaneados los transcribe el lector."],
      ["🧱", "Crear una web", "<code>/plantilla</code> · <code>/zero</code>", "57 componentes prediseñados: menos tokens y diseño probado (<code>/componentes demo</code>)."],
      ["❓", "Ayuda", "<code>/ayuda</code> · <code>?</code>", "Todos los comandos y atajos de teclado."],
      ["⚙️", "Ajustes", "<code>/ajustes</code>", "Idioma, tema, razonamiento, visión, tokens…"],
      ["🧭", "Volver aquí", "<code>instalar-clay-myrrh.bat</code>", "Para añadir usos o cambiar de nivel."],
    ]).map(([i, t, c, d]) => `<div class="card cheat"><div class="ficon" style="font-size:1.6rem">${i}</div><h3>${t}</h3><p>${c}</p><p class="muted small">${d}</p></div>`).join("");
    show(6);
    // instalación terminada: se apaga el asistente y se cierra su ventana de consola
    clearInterval(S.ping);
    setTimeout(() => api("/api/quit", {}).catch(() => {}), 800);
    $("#nav-hint").textContent = t("La ventana del asistente se ha cerrado; esta página ya no hace falta.", "The assistant's window has closed; this page is no longer needed.");
  }

  /* ------------------------------------------------------------------ glosario y ayudas */
  $("#glossary-list").innerHTML = Object.values(GLOSSARY).map(([t, d]) => `<dt>${esc(t)}</dt><dd>${esc(d)}</dd>`).join("");
  $("#open-glossary").addEventListener("click", () => { $("#glossary").hidden = false; });
  $("#close-glossary").addEventListener("click", () => { $("#glossary").hidden = true; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#glossary").hidden = true; });
  const tip = $("#tooltip");
  document.addEventListener("mouseover", (e) => {
    const b = e.target.closest("[data-term]"); if (!b) { tip.hidden = true; return; }
    const g = GLOSSARY[b.dataset.term]; if (!g) return;
    tip.innerHTML = `<b>${esc(g[0])}</b><br>${esc(g[1])}`; tip.hidden = false;
    const r = b.getBoundingClientRect(); tip.style.left = Math.min(innerWidth - 340, r.left) + "px"; tip.style.top = (r.bottom + 8) + "px";
  });
  document.addEventListener("click", (e) => { const b = e.target.closest("[data-term]"); if (b) { $("#glossary").hidden = false;
    const dts = $$("#glossary dt"); const i = Object.keys(GLOSSARY).indexOf(b.dataset.term); dts[i]?.scrollIntoView({ block: "center" }); } });

  /* ------------------------------------------------------------------ arranque */
  (async () => {
    S.state = await api("/api/state");
    S.hw = structuredClone(S.state.hardware);
    $("#engine-warning").hidden = S.state.engine_ok;
    if (S.state.engine_help) {
      $("#engine-summary").textContent = S.state.engine_help.summary;
      $("#engine-steps").innerHTML = S.state.engine_help.steps.map((t) => `<li>${esc(t)}</li>`).join("");
    }
    $("#preset").innerHTML += S.state.presets.map((p, i) => `<option value="${i}">${esc(p.label)}</option>`).join("");
    recompute(true);
    show(0);
    window.LAI.onReady.forEach((f) => { try { f(); } catch (e) { console.error(e); } });
    // latido: si la pestaña se cierra, el asistente se apaga solo
    S.ping = setInterval(() => api("/api/ping").catch(() => {}), 5000); api("/api/ping").catch(() => {});
    // en segundo plano el navegador frena los temporizadores: al volver a la pestaña, ponerse al día ya
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) return;
      if (S.ping) api("/api/ping").catch(() => {});
      if (S.poll) pollTick();
    });
  })().catch((e) => { document.body.innerHTML = `<div class="card" style="margin:2rem">${t("No se pudo iniciar el asistente", "The assistant could not start")}: ${esc(e.message)}</div>`; });
})();
