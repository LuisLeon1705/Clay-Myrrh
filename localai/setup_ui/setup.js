/* Clay-Myrrh · asistente de instalación visual */
(() => {
  "use strict";
  const TOKEN = document.querySelector('meta[name="setup-token"]').content;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const gb = (b) => (b / 1024 ** 3).toFixed(1) + " GB";
  const api = async (path, body) => {
    const r = await fetch(path, { method: body === undefined ? "GET" : "POST", headers: { "X-Setup-Token": TOKEN, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return r.json();
  };

  const STEPS = ["Bienvenida", "Tu equipo", "Usos", "Niveles", "Resumen", "Descarga", "Listo"];
  const GLOSSARY = {
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
  const DEMO_TEXT = "Claro. Para preparar un buen café de filtro usa unos 15 gramos de café molido medio por cada 250 ml de agua a 92 °C. Moja primero el café con un poco de agua y espera 30 segundos para que suelte el gas; después vierte el resto en círculos lentos. El proceso completo debería durar entre dos minutos y medio y tres minutos. Si sale amargo, muele un poco más grueso; si sale ácido o aguado, más fino.";
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
    next.textContent = ["Empezar →", "Siguiente →", "Siguiente →", "Ver resumen →", "", "", "Cerrar pestaña"][step];
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
    if (S.step === 6) { window.close(); document.body.innerHTML = '<div class="done" style="margin-top:20vh"><div class="done-icon">👋</div><h2>Asistente cerrado</h2><p class="lead" style="margin:auto">Ya puedes cerrar esta pestaña.</p></div>'; return; }
    show(S.step + 1);
  });

  /* ------------------------------------------------------------------ hardware */
  const nearest = (arr, v) => arr.reduce((b, x) => (Math.abs(x - v) < Math.abs(b - v) ? x : b), arr[0]);
  function field(label, html, help = "") {
    return `<div class="hw-field"><label>${label}${help ? ` <button class="info" type="button" data-term="${help}">?</button>` : ""}</label>${html}</div>`;
  }
  function renderHardware() {
    const hw = S.hw, det = S.state.hardware;
    const gpuOpts = [`<option value="">Sin GPU dedicada</option>`].concat(S.state.gpus.map(([n, bw]) =>
      `<option value="${esc(n)}" ${hw.gpu_name && hw.gpu_name.toUpperCase().includes(n) ? "selected" : ""}>${esc(n)} · ${bw} GB/s</option>`));
    const gpuKnown = !hw.gpu_name || S.state.gpus.some(([n]) => hw.gpu_name.toUpperCase().includes(n));
    if (hw.gpu_name && !gpuKnown) gpuOpts.push(`<option value="__custom" selected>${esc(hw.gpu_name)}</option>`);
    $("#hw-form").innerHTML = [
      field(`RAM <span id="v-ram">${hw.ram_gb} GB</span>`, `<input type="range" id="f-ram" min="0" max="${RAM_GB.length - 1}" value="${RAM_GB.indexOf(nearest(RAM_GB, hw.ram_gb))}">`, "ram"),
      field("Velocidad RAM", `<select id="f-mts">${MTS.map((m) => `<option ${m === nearest(MTS, hw.ram_mts || 3200) ? "selected" : ""}>${m}</option>`).join("")}</select>`, "mts"),
      field("Canales de RAM", `<select id="f-ch">${[1, 2, 4, 6, 8, 12].map((c) => `<option ${c === hw.ram_channels ? "selected" : ""}>${c}</option>`).join("")}</select>`, "mts"),
      field("Tarjeta gráfica", `<select id="f-gpu">${gpuOpts.join("")}</select>`, "vram"),
      field(`VRAM <span id="v-vram">${hw.vram_gb} GB</span>`, `<input type="range" id="f-vram" min="0" max="${VRAM_GB.length - 1}" value="${VRAM_GB.indexOf(nearest(VRAM_GB, hw.vram_gb))}">`, "vram"),
      field(`Ancho de banda GPU <span id="v-bw">${hw.gpu_bw} GB/s</span>`, `<input type="range" id="f-bw" min="0" max="2000" step="8" value="${hw.gpu_bw}">`, "bandwidth"),
      field("Procesador", `<div class="small muted">${esc(hw.cpu_name)} · ${hw.cpu_cores} núcleos · ${hw.cpu_ghz} GHz ${det.detected.includes("cpu") && hw.cpu_name === det.cpu_name ? '<span class="detected-tag">✓ detectado</span>' : ""}</div>`),
      field("Disco libre", `<div class="small muted">${hw.disk_free_gb} GB en la carpeta de modelos</div>`),
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
    $("#lanes").innerHTML = (hw.vram_gb >= 2 && hw.gpu_bw ? lane("gpu", "VRAM (tarjeta gráfica)", hw.vram_gb, hw.gpu_bw, "carril rápido") :
      `<div class="callout info small">Sin tarjeta gráfica dedicada: todo irá por la RAM. Funcionan bien los modelos pequeños (hasta ~4B) y los MoE.</div>`)
      + lane("ram", "RAM (ordenador)", hw.ram_gb, ramBw, hw.vram_gb >= 2 ? "carril lento" : "único carril");
    $("#hw-class").textContent = S.rec ? "Tu equipo: " + S.rec.class : "";
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
    Object.assign(S.hw, p.hw, { cpu_name: "(equipo simulado)", cpu_cores: 8, cpu_ghz: 4.0 });
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
  $("#demo-buttons").innerHTML = [[2, "2 t/s · muy lento"], [5, "5 t/s · leer en voz alta"], [10, "10 t/s · cómodo"], [25, "25 t/s · fluido"], [60, "60 t/s · instantáneo"]]
    .map(([t, l]) => `<button class="btn ghost small" data-tps="${t}" type="button">${l}</button>`).join("");
  $("#demo-buttons").addEventListener("click", (e) => { const b = e.target.closest("[data-tps]"); if (b) demo(+b.dataset.tps, b.textContent); });

  /* ------------------------------------------------------------------ usos */
  function renderCats() {
    $("#cats").innerHTML = S.state.categories.map((c) => `<button type="button" class="cat ${S.cats.has(c.key) ? "on" : ""}" data-cat="${c.key}" aria-pressed="${S.cats.has(c.key)}">
      <span class="check">${S.cats.has(c.key) ? "✓" : ""}</span><div class="cicon">${c.icon}</div><h3>${esc(c.name)}</h3><p>${esc(c.desc)}</p>
      ${c.needs_vision ? '<span class="vis">👁 usa visión</span>' : ""}</button>`).join("");
    $("#nav-hint").textContent = S.cats.size ? `${S.cats.size} seleccionado(s)` : "Elige al menos uno";
    $("#next").disabled = !S.cats.size;
  }
  $("#cats").addEventListener("click", (e) => {
    const b = e.target.closest("[data-cat]"); if (!b) return;
    const k = b.dataset.cat; S.cats.has(k) ? S.cats.delete(k) : S.cats.add(k); renderCats();
  });

  /* ------------------------------------------------------------------ niveles */
  function speedColor(t) { return t >= 20 ? "var(--ok)" : t >= 8 ? "var(--accent-2)" : t >= 3 ? "var(--warn)" : "var(--bad)"; }
  function tierCard(cat, tkey, m, title) {
    if (!m) return `<button class="tier" disabled type="button"><span class="tname">${title}</span><span class="mname">No cabe</span><span class="note">Ningún modelo de este uso cabe en tu memoria.</span></button>`;
    const on = S.choices[cat.key] === m.key;
    const g = Math.round(m.gpu_frac * 100);
    const pct = Math.min(100, Math.log10(1 + m.tps) / Math.log10(101) * 100);
    const tags = [m.installed ? '<span class="tag ok">✓ ya instalado</span>' : `<span class="tag">descarga ${m.size_gb.toFixed(1)} GB</span>`,
      m.moe ? `<span class="tag moe" title="Mezcla de expertos">MoE · ${m.active_b}B activos</span>` : `<span class="tag">${m.params_b}B</span>`,
      m.mmproj_missing ? '<span class="tag warn">+ descarga su «ojo»</span>' : ""].join("");
    return `<button type="button" class="tier ${on ? "on" : ""}" data-cat="${cat.key}" data-model="${m.key}">
      <span class="tname">${title}</span><span class="mname">${esc(m.name)}</span><span class="note">${esc(m.note)}</span>
      <div class="tags">${tags}</div>
      <div class="membar" title="Dónde se guarda el modelo">${m.where === "CPU" ? `<span class="r" style="width:100%"></span>` : `<span class="g" style="width:${g}%"></span><span class="r" style="width:${100 - g}%"></span>`}</div>
      <div class="memlegend"><span><i style="background:var(--gpu)"></i>VRAM ${m.where === "CPU" ? 0 : g}%</span><span><i style="background:var(--ram)"></i>RAM ${m.where === "CPU" ? 100 : 100 - g}%</span><span>${m.mem_gb} GB en total</span></div>
      <div class="gauge"><div class="meter"><span style="width:${pct}%;background:${speedColor(m.tps)}"></span></div><b>${Math.round(m.tps_low)}–${Math.round(m.tps_high)} t/s</b></div>
      <span class="note">${esc(m.speed_words)}</span>
      <span class="btn ghost small try" data-try="${m.tps}" data-name="${esc(m.name)}">▶ Probar velocidad</span></button>`;
  }
  function renderLevels() {
    if (!S.rec) { $("#levels").innerHTML = '<p class="muted">Calculando…</p>'; recompute(true).then(renderLevels); return; }
    $("#tier-legend").innerHTML = Object.entries(S.state.tiers).map(([k, t]) => `<div><b>${t.name}.</b> <span class="muted">${esc(t.desc)}</span></div>`).join("");
    const html = [];
    for (const c of S.state.categories.filter((x) => S.cats.has(x.key))) {
      const r = S.rec.categories[c.key];
      if (!(c.key in S.choices)) {
        const def = r.tiers.moderado || r.tiers.basico || r.tiers.extensivo;
        S.choices[c.key] = def ? def.key : null;
      }
      const cards = Object.entries(r.tiers).map(([tk, m]) => tierCard(c, tk, m, S.state.tiers[tk].name));
      r.installed.forEach((m) => cards.push(tierCard(c, "inst", m, "Ya lo tienes")));
      html.push(`<div class="level"><div class="level-head"><span class="cicon">${c.icon}</span><div><h3 style="margin:0">${esc(c.name)}</h3>
        <span class="muted small">${esc(c.desc)}</span></div></div><div class="tiers">${cards.join("")}</div>
        <div class="callout cloud small"><span>☁️</span><span><b>¿Local o nube?</b> ${esc(c.cloud)}</span></div>
        <button type="button" class="link small skip" data-skip="${c.key}">${S.choices[c.key] ? "Omitir este uso" : "✓ Omitido · pulsa una tarjeta para elegir"}</button></div>`);
    }
    $("#levels").innerHTML = '<div class="typing" id="tier-demo" style="min-height:80px;margin-bottom:1.5rem">Pulsa «▶ Probar velocidad» en cualquier tarjeta para ver a qué ritmo escribiría ese modelo en tu equipo.</div>' + html.join("");
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
      return `<div class="sumrow"><span>${c.icon} ${esc(c.name)}</span><span><b>${esc(m.name)}</b> <span class="muted small">${m.installed ? "instalado" : m.size_gb.toFixed(1) + " GB"}</span></span></div>`;
    }).join("") || '<p class="muted">No elegiste ningún modelo.</p>';
    $("#sum-primary").innerHTML = usable.map((k) => { const c = cats.find((x) => x.key === k);
      return `<label><input type="radio" name="primary" value="${k}" ${k === S.primary ? "checked" : ""}> ${c.icon} ${esc(c.name)} <span class="muted small">— ${esc(modelInfo(k, ch[k]).name)}</span></label>`; }).join("")
      || '<p class="muted small">(solo elegiste el lector: se mantiene tu modelo actual)</p>';
    $$("#sum-primary input").forEach((r) => r.addEventListener("change", () => { S.primary = r.value; }));
    $("#sum-files").innerHTML = '<p class="muted">Consultando Hugging Face…</p>'; $("#sum-disk").innerHTML = "";
    $("#next").disabled = true;
    try {
      S.summary = await api("/api/summary", { hw: S.hw, choices: ch, primary: S.primary });
      const s = S.summary;
      $("#sum-files").innerHTML = s.error ? `<div class="callout warn">${esc(s.error)}</div>` : s.files.length ? s.files.map((f) =>
        `<div class="file"><div class="row"><span>${esc(f.name)} <span class="muted small">(${esc(f.folder)})</span></span><b>${gb(f.size)}</b></div></div>`).join("")
        : '<div class="callout info">Todo lo elegido ya está instalado: solo aplicaré la configuración.</div>';
      const pct = s.disk_free ? Math.min(100, s.total / s.disk_free * 100) : 0;
      $("#sum-disk").innerHTML = s.total ? `<div style="margin-top:1rem"><div class="row between small"><span>Total: <b>${gb(s.total)}</b></span><span class="muted">libre en disco: ${gb(s.disk_free)}</span></div>
        <div class="bar"><span style="width:${pct.toFixed(1)}%"></span></div><p class="muted small" style="margin-top:.5rem">≈ ${Math.max(1, Math.round(s.total / (50 * 1024 ** 2) / 60))} min a 50 MB/s</p>
        <button class="link small" id="save-only" type="button">Solo guardar el plan (descargar luego con clay-myrrh --download)</button></div>` : "";
      $("#save-only")?.addEventListener("click", async () => { await api("/api/summary", { hw: S.hw, choices: ch, primary: S.primary });
        $("#nav-hint").textContent = "Plan guardado. Ejecuta «clay-myrrh --download» cuando quieras."; });
      $("#next").textContent = s.total ? "Descargar y configurar →" : "Aplicar configuración →";
      $("#next").disabled = !Object.keys(ch).length || !!s.error;
      if (s.total > s.disk_free) { $("#next").disabled = true; $("#nav-hint").textContent = "No hay espacio suficiente en el disco"; }
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
    S.poll = setInterval(async () => {
      const p = await api("/api/progress").catch(() => null); if (!p) return;
      $("#dl-message").textContent = p.message || "";
      $("#dl-bar").style.width = p.total ? (p.done / p.total * 100).toFixed(1) + "%" : "0";
      $("#dl-bytes").textContent = p.total ? `${gb(p.done)} de ${gb(p.total)}` : "";
      $("#dl-speed").textContent = p.speed ? `${(p.speed / 1024 ** 2).toFixed(1)} MB/s` : "";
      $("#dl-eta").textContent = p.eta != null ? `quedan ${p.eta > 90 ? Math.round(p.eta / 60) + " min" : Math.round(p.eta) + " s"}` : "";
      $("#dl-files").innerHTML = p.files.map((f) => `<div class="file"><div class="row"><span>${f.state === "hecho" ? "✅" : f.state === "descargando" ? "⬇️" : "⏳"} ${esc(f.name)}</span>
        <span class="muted small">${gb(f.done)} / ${gb(f.size)}</span></div><div class="bar"><span style="width:${(f.done / f.size * 100).toFixed(1)}%"></span></div></div>`).join("")
        + (p.state === "running" ? '<button class="btn ghost small" id="dl-cancel" type="button" style="justify-self:start">⏸ Pausar</button>' : "")
        + (["error", "cancelled"].includes(p.state) ? '<button class="btn primary small" id="dl-retry" type="button" style="justify-self:start">↻ Reanudar</button>' : "");
      $("#dl-cancel")?.addEventListener("click", () => api("/api/cancel", {}));
      $("#dl-retry")?.addEventListener("click", async () => { await api("/api/download", {}); poll(); });
      if (p.state === "done") { clearInterval(S.poll); finish({}); }
      if (["error", "cancelled"].includes(p.state)) clearInterval(S.poll);
    }, 600);
  }
  function finish() {
    const ch = chosen(), cats = S.state.categories;
    const prim = S.primary ? modelInfo(S.primary, ch[S.primary]) : null;
    const lec = ch.lectura ? modelInfo("lectura", ch.lectura) : null;
    $("#done-text").innerHTML = `Abre <b>Clay-Myrrh</b> con el acceso directo o con <code>clay-myrrh.cmd</code>.` + (prim ? ` Se cargará <b>${esc(prim.name)}</b> (${esc(cats.find((c) => c.key === S.primary).name)}).` : "")
      + (lec ? ` Las imágenes y PDFs escaneados los leerá <b>${esc(lec.name)}</b>.` : "");
    const profiles = Object.keys(ch).filter((k) => k !== "lectura").map((k) => `<code>/modo ${k}</code>`).join(" · ");
    $("#cheats").innerHTML = [
      ["🔁", "Cambiar de uso", profiles || "<code>/model</code>", "Carga el modelo y los ajustes de ese uso."],
      ["📎", "Adjuntar archivos", "<code>@documento.pdf ¿de qué trata?</code>", "Imágenes, PDFs y código; los escaneados los transcribe el lector."],
      ["🧱", "Crear una web", "<code>/plantilla</code> · <code>/zero</code>", "57 componentes prediseñados: menos tokens y diseño probado (<code>/componentes demo</code>)."],
      ["❓", "Ayuda", "<code>/help</code> · <code>?</code>", "Todos los comandos y atajos de teclado."],
      ["⚙️", "Ajustes", "<code>/settings</code>", "Tema, razonamiento, visión, tokens…"],
      ["🧭", "Volver aquí", "<code>instalar-clay-myrrh.bat</code>", "Para añadir usos o cambiar de nivel."],
    ].map(([i, t, c, d]) => `<div class="card cheat"><div class="ficon" style="font-size:1.6rem">${i}</div><h3>${t}</h3><p>${c}</p><p class="muted small">${d}</p></div>`).join("");
    show(6);
    // instalación terminada: se apaga el asistente y se cierra su ventana de consola
    clearInterval(S.ping);
    setTimeout(() => api("/api/quit", {}).catch(() => {}), 800);
    $("#nav-hint").textContent = "La ventana del asistente se ha cerrado; esta página ya no hace falta.";
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
  })().catch((e) => { document.body.innerHTML = `<div class="card" style="margin:2rem">No se pudo iniciar el asistente: ${esc(e.message)}</div>`; });
})();
