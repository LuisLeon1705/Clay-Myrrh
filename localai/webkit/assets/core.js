/* Clay-Myrrh · componentes — comportamiento de los componentes (generado; edita pagina.lai, no este archivo) */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* sin almacenamiento */ } },
  };
  function toast(msg) {
    let t = $(".toast");
    if (!t) { t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "status"); document.body.appendChild(t); }
    t.textContent = msg; t.classList.add("show");
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2600);
  }

  /* --- navegación ------------------------------------------------------------ */
  $$(".nav-toggle").forEach((b) => b.addEventListener("click", () => {
    const nav = b.closest(".navbar"); const open = nav.classList.toggle("open");
    b.setAttribute("aria-expanded", open);
  }));
  $$(".navbar .nav-links a").forEach((a) => a.addEventListener("click", () => a.closest(".navbar").classList.remove("open")));
  const navbar = $(".navbar");
  if (navbar) { const f = () => navbar.classList.toggle("scrolled", scrollY > 10); f(); addEventListener("scroll", f, { passive: true }); }

  /* --- aparición al hacer scroll ------------------------------------------------ */
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("is-visible"); io.unobserve(e.target); } }), { threshold: 0.12 });
    $$(".reveal").forEach((el) => io.observe(el));
  } else { $$(".reveal").forEach((el) => el.classList.add("is-visible")); }

  /* --- contadores (stats) ------------------------------------------------------ */
  $$("[data-count]").forEach((el) => {
    const end = parseFloat(el.dataset.count); if (isNaN(end)) return;
    const dec = (el.dataset.count.split(".")[1] || "").length;
    const run = () => { const t0 = performance.now(); const step = (t) => { const p = Math.min(1, (t - t0) / 1400);
      el.textContent = (end * (1 - Math.pow(1 - p, 3))).toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
      if (p < 1) requestAnimationFrame(step); }; requestAnimationFrame(step); };
    if ("IntersectionObserver" in window) { const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) { run(); io.disconnect(); } }); io.observe(el); } else run();
  });

  /* --- filtros por categoría (galería, posts, productos) ------------------------ */
  $$("[data-filter-group]").forEach((group) => {
    const scope = group.closest("section"); const items = $$("[data-category]", scope);
    let cat = "*", q = "";
    const apply = () => items.forEach((it) => {
      const okCat = cat === "*" || it.dataset.category === cat;
      const okQ = !q || it.textContent.toLowerCase().includes(q);
      it.hidden = !(okCat && okQ);
    });
    $$("button[data-filter]", group).forEach((b) => b.addEventListener("click", () => {
      $$("button[data-filter]", group).forEach((x) => x.classList.toggle("active", x === b));
      cat = b.dataset.filter; apply();
    }));
    const search = $("input[data-search]", scope);
    if (search) search.addEventListener("input", () => { q = search.value.trim().toLowerCase(); apply(); });
  });

  /* --- tabla: búsqueda y orden --------------------------------------------------- */
  $$(".data-table").forEach((wrap) => {
    const tbody = $("tbody", wrap); const rows = $$("tr", tbody); const search = $("input[data-table-search]", wrap);
    if (search) search.addEventListener("input", () => { const q = search.value.toLowerCase(); rows.forEach((r) => { r.hidden = !r.textContent.toLowerCase().includes(q); }); });
    $$("th[data-sort]", wrap).forEach((th, i) => th.addEventListener("click", () => {
      const dir = th.dataset.dir === "asc" ? "desc" : "asc"; $$("th", wrap).forEach((x) => delete x.dataset.dir); th.dataset.dir = dir;
      const val = (r) => { const t = r.children[i].textContent.trim(); const n = parseFloat(t.replace(/[^\d.,-]/g, "").replace(",", ".")); return isNaN(n) ? t.toLowerCase() : n; };
      rows.sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : 0) * (dir === "asc" ? 1 : -1)).forEach((r) => tbody.appendChild(r));
    }));
  });

  /* --- formularios con validación (contacto, boletín, formularios a medida) ------------ */
  function validate(form) {
    let ok = true;
    $$(".field", form).forEach((f) => {
      const inp = $("input, textarea, select", f); if (!inp) return; let msg = "";
      if (inp.type === "checkbox") { if (inp.required && !inp.checked) msg = "Debes marcar esta casilla"; }
      else if (inp.required && !inp.value.trim()) msg = "Este campo es obligatorio";
      else if (inp.type === "email" && inp.value && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(inp.value)) msg = "Correo no válido";
      else if (inp.minLength > 0 && inp.value && inp.value.length < inp.minLength) msg = `Mínimo ${inp.minLength} caracteres`;
      f.classList.toggle("invalid", !!msg); const e = $(".error", f); if (e) e.textContent = msg; ok = ok && !msg;
    });
    return ok;
  }
  $$("form[data-validate]").forEach((form) => form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (!validate(form)) return;
    const data = Object.fromEntries(new FormData(form));
    const sk = form.dataset.store || "lai-mensajes";
    const saved = store.get(sk, []); saved.push({ ...data, fecha: new Date().toISOString() }); store.set(sk, saved);
    if (form.dataset.mailto) {
      const body = Object.entries(data).map(([k, v]) => `${k}: ${v}`).join("\n");
      location.href = `mailto:${form.dataset.mailto}?subject=${encodeURIComponent(form.dataset.subject || "Contacto")}&body=${encodeURIComponent(body)}`;
    }
    form.reset(); toast(form.dataset.ok || "¡Gracias! Te responderemos pronto.");
  }));

  /* --- carrito ------------------------------------------------------------------- */
  const cartEl = $(".cart");
  if (cartEl) {
    const cur = cartEl.dataset.currency || "$"; let cart = store.get("lai-carrito", []);
    const fmt = (n) => `${cur}${Number(n).toFixed(2)}`;
    const render = () => {
      store.set("lai-carrito", cart);
      const count = cart.reduce((s, x) => s + x.qty, 0);
      $$(".cart-count").forEach((c) => { c.textContent = count; c.hidden = !count; });
      $(".cart-items", cartEl).innerHTML = cart.length ? cart.map((x, i) => `<li><span>${esc(x.name)}</span>
        <span class="qty"><button data-i="${i}" data-d="-1" aria-label="Quitar uno">−</button>${x.qty}<button data-i="${i}" data-d="1" aria-label="Añadir uno">+</button></span>
        <strong>${fmt(x.price * x.qty)}</strong></li>`).join("") : `<li class="muted">Tu carrito está vacío</li>`;
      $(".cart-total", cartEl).textContent = fmt(cart.reduce((s, x) => s + x.price * x.qty, 0));
    };
    $$("[data-add-cart]").forEach((b) => b.addEventListener("click", () => {
      const p = JSON.parse(b.dataset.addCart); const found = cart.find((x) => x.name === p.name);
      found ? found.qty++ : cart.push({ ...p, qty: 1 }); render(); toast(`Añadido: ${p.name}`);
    }));
    cartEl.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-i]"); if (!b) return;
      const it = cart[+b.dataset.i]; it.qty += +b.dataset.d; if (it.qty <= 0) cart.splice(+b.dataset.i, 1); render();
    });
    $$(".cart-open").forEach((b) => b.addEventListener("click", () => cartEl.classList.add("open")));
    $$(".cart-close", cartEl).forEach((b) => b.addEventListener("click", () => cartEl.classList.remove("open")));
    const co = $("form.checkout", cartEl);
    if (co) co.addEventListener("submit", (e) => { e.preventDefault(); if (!cart.length) return toast("El carrito está vacío");
      cart = []; render(); co.reset(); cartEl.classList.remove("open"); toast("Pedido realizado (simulación)"); });
    render();
  }

  /* --- CRUD con localStorage ------------------------------------------------------ */
  $$(".crud").forEach((root) => {
    const cfg = JSON.parse(root.dataset.config); const key = `lai-crud-${cfg.key}`;
    let rows = store.get(key, null) ?? cfg.seed ?? []; let editing = null;
    const tbody = $("tbody", root), form = $("form", root), dlg = $("dialog", root), search = $("input[data-crud-search]", root);
    const save = () => store.set(key, rows);
    const draw = () => {
      const q = (search?.value || "").toLowerCase();
      const vis = rows.map((r, i) => [r, i]).filter(([r]) => !q || Object.values(r).join(" ").toLowerCase().includes(q));
      tbody.innerHTML = vis.length ? vis.map(([r, i]) => `<tr>${cfg.fields.map((f) => `<td>${esc(r[f.name])}</td>`).join("")}
        <td class="actions"><button class="btn btn-ghost btn-sm" data-edit="${i}">Editar</button>
        <button class="btn btn-ghost btn-sm danger" data-del="${i}">Borrar</button></td></tr>`).join("")
        : `<tr><td colspan="${cfg.fields.length + 1}" class="muted empty">Sin registros</td></tr>`;
      $(".crud-count", root).textContent = `${rows.length} ${rows.length === 1 ? cfg.singular : cfg.plural}`;
    };
    const open = (i) => { editing = i; form.reset();
      if (i != null) cfg.fields.forEach((f) => { form.elements[f.name].value = rows[i][f.name] ?? ""; });
      $(".dlg-title", root).textContent = i == null ? `Nuevo ${cfg.singular}` : `Editar ${cfg.singular}`; dlg.showModal(); };
    $(".crud-new", root).addEventListener("click", () => open(null));
    $(".crud-cancel", root).addEventListener("click", () => dlg.close());
    tbody.addEventListener("click", (e) => {
      const ed = e.target.closest("[data-edit]"), del = e.target.closest("[data-del]");
      if (ed) open(+ed.dataset.edit);
      if (del && confirm(`¿Borrar este ${cfg.singular}?`)) { rows.splice(+del.dataset.del, 1); save(); draw(); toast("Borrado"); }
    });
    form.addEventListener("submit", (e) => { e.preventDefault();
      const rec = Object.fromEntries(cfg.fields.map((f) => [f.name, form.elements[f.name].value.trim()]));
      if (cfg.fields.some((f) => f.required && !rec[f.name])) return toast("Completa los campos obligatorios");
      editing == null ? rows.unshift(rec) : (rows[editing] = rec); save(); draw(); dlg.close(); toast("Guardado"); });
    search?.addEventListener("input", draw);
    $(".crud-csv", root)?.addEventListener("click", () => {
      const head = cfg.fields.map((f) => f.label); const lines = [head, ...rows.map((r) => cfg.fields.map((f) => r[f.name] ?? ""))]
        .map((l) => l.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(";")).join("\n");
      const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(["﻿" + lines], { type: "text/csv" }));
      a.download = `${cfg.key}.csv`; a.click(); URL.revokeObjectURL(a.href);
    });
    draw();
  });

  /* --- pestañas ----------------------------------------------------------------------- */
  $$("[data-tabs]").forEach((box) => {
    const tabs = $$('[role="tab"]', box), panels = $$('[role="tabpanel"]', box);
    const sel = (i) => {
      tabs.forEach((t, k) => { t.setAttribute("aria-selected", k === i); t.tabIndex = k === i ? 0 : -1; });
      panels.forEach((p, k) => { p.hidden = k !== i; });
    };
    tabs.forEach((t, i) => {
      t.addEventListener("click", () => sel(i));
      t.addEventListener("keydown", (e) => {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        const j = (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length; sel(j); tabs[j].focus();
      });
    });
  });

  /* --- carrusel ----------------------------------------------------------------------- */
  $$("[data-carousel]").forEach((c) => {
    const track = $(".car-track", c), slides = $$(".slide", track), dots = $(".car-dots", c);
    if (!slides.length) return;
    let cur = 0, timer = null;
    dots.innerHTML = slides.length > 1 ? slides.map((_, i) => `<button type="button" aria-label="Ir a la diapositiva ${i + 1}"></button>`).join("") : "";
    const db = $$("button", dots);
    const mark = () => db.forEach((b, i) => b.setAttribute("aria-current", i === cur));
    const go = (i) => { cur = (i + slides.length) % slides.length; track.scrollTo({ left: cur * track.clientWidth }); mark(); };
    const secs = parseFloat(c.dataset.autoplay) || 0;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const restart = () => { clearInterval(timer); if (secs > 0 && !reduce && slides.length > 1) timer = setInterval(() => go(cur + 1), secs * 1000); };
    track.addEventListener("scroll", () => { const i = Math.round(track.scrollLeft / track.clientWidth); if (i !== cur) { cur = i; mark(); } }, { passive: true });
    db.forEach((b, i) => b.addEventListener("click", () => { go(i); restart(); }));
    $(".car-prev", c)?.addEventListener("click", () => { go(cur - 1); restart(); });
    $(".car-next", c)?.addEventListener("click", () => { go(cur + 1); restart(); });
    c.addEventListener("mouseenter", () => clearInterval(timer));
    c.addEventListener("mouseleave", restart);
    mark(); restart();
  });

  /* --- acceso simulado (login / registro) -------------------------------------------- */
  async function hash(s) {
    try {
      const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
      return Array.from(new Uint8Array(b)).map((x) => x.toString(16).padStart(2, "0")).join("");
    } catch { let h = 5381; for (const ch of s) h = ((h << 5) + h + ch.charCodeAt(0)) | 0; return "d" + (h >>> 0).toString(16); }
  }
  $$("[data-auth-box]").forEach((box) => {
    const forms = $(".auth-forms", box), welcome = $(".auth-welcome", box), tabs = $$("[data-auth-tab]", box);
    const show = () => {
      const s = store.get("lai-sesion", null); forms.hidden = !!s; welcome.hidden = !s;
      if (s) $("[data-auth-name]", box).textContent = s.name;
      document.body.classList.toggle("is-auth", !!s);
    };
    const pick = (name) => {
      tabs.forEach((t) => t.setAttribute("aria-selected", t.dataset.authTab === name));
      $$("form[data-auth]", box).forEach((f) => { f.hidden = f.dataset.auth !== name; });
    };
    const fail = (form, name, msg) => { const f = form.elements[name].closest(".field"); f.classList.add("invalid"); $(".error", f).textContent = msg; };
    tabs.forEach((t) => t.addEventListener("click", () => pick(t.dataset.authTab)));
    $$("form[data-auth]", box).forEach((form) => form.addEventListener("submit", async (e) => {
      e.preventDefault(); if (!validate(form)) return;
      const d = Object.fromEntries(new FormData(form)); const email = d.email.trim().toLowerCase(); const users = store.get("lai-usuarios", {});
      if (form.dataset.auth === "register") {
        if (users[email]) return fail(form, "email", "Ya existe una cuenta con este correo");
        if (d.password !== d.password2) return fail(form, "password2", "Las contraseñas no coinciden");
        users[email] = { name: d.name.trim(), pass: await hash(email + ":" + d.password) }; store.set("lai-usuarios", users);
      } else if (!users[email] || users[email].pass !== await hash(email + ":" + d.password)) {
        return fail(form, "password", "Correo o contraseña incorrectos");
      }
      store.set("lai-sesion", { email, name: users[email].name }); form.reset(); show(); toast(`Hola, ${users[email].name}`);
      if (box.dataset.redirect) location.hash = box.dataset.redirect;
    }));
    $("[data-auth-logout]", box).addEventListener("click", () => { store.set("lai-sesion", null); show(); pick("login"); toast("Sesión cerrada"); });
    pick("login"); show();
  });

  /* --- kanban ------------------------------------------------------------------------- */
  $$("[data-kanban]").forEach((root) => {
    const cfg = JSON.parse(root.dataset.kanban); const key = `lai-kanban-${cfg.key}`;
    let cards = store.get(key, null) ?? cfg.seed.map((c, i) => ({ id: Date.now() + i, ...c }));
    let dragId = null;
    const save = () => store.set(key, cards);
    const draw = () => {
      root.innerHTML = cfg.cols.map((col, ci) => {
        const list = cards.filter((c) => c.col === col);
        return `<div class="kb-col" data-col="${esc(col)}"><div class="kb-head"><span>${esc(col)}</span><span class="kb-count">${list.length}</span></div>
          <div class="kb-list">${list.map((c) => `<div class="kb-card" draggable="true" data-id="${c.id}"><div>${c.tag ? `<span class="badge">${esc(c.tag)}</span><br>` : ""}${esc(c.text)}</div>
          <div class="kb-tools">${ci < cfg.cols.length - 1 ? `<button type="button" data-move="${c.id}" aria-label="Mover a ${esc(cfg.cols[ci + 1])}">→</button>` : ""}
          <button type="button" data-del="${c.id}" aria-label="Borrar tarjeta">×</button></div></div>`).join("")}</div>
          <button type="button" class="kb-add" data-add="${ci}">+ Añadir tarjeta</button></div>`;
      }).join("");
    };
    root.addEventListener("dragstart", (e) => {
      const c = e.target.closest?.(".kb-card"); if (!c) return;
      dragId = +c.dataset.id; c.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", c.dataset.id);
    });
    root.addEventListener("dragend", () => { $$(".kb-card", root).forEach((x) => x.classList.remove("dragging")); $$(".kb-col", root).forEach((x) => x.classList.remove("over")); });
    root.addEventListener("dragover", (e) => {
      const col = e.target.closest?.(".kb-col"); if (!col || dragId == null) return;
      e.preventDefault(); $$(".kb-col", root).forEach((x) => x.classList.toggle("over", x === col));
    });
    root.addEventListener("drop", (e) => {
      const col = e.target.closest?.(".kb-col"); if (!col || dragId == null) return;
      e.preventDefault();
      const card = cards.find((c) => c.id === dragId);
      if (card) {
        const over = e.target.closest(".kb-card"); cards = cards.filter((c) => c !== card); card.col = col.dataset.col;
        const idx = over ? cards.findIndex((c) => c.id === +over.dataset.id) : -1;
        idx >= 0 ? cards.splice(idx, 0, card) : cards.push(card); save(); draw();
      }
      dragId = null;
    });
    root.addEventListener("click", (e) => {
      const del = e.target.closest("[data-del]"), add = e.target.closest("[data-add]"), mv = e.target.closest("[data-move]");
      if (del) { cards = cards.filter((c) => c.id !== +del.dataset.del); save(); draw(); }
      if (mv) { const c = cards.find((x) => x.id === +mv.dataset.move); const i = cfg.cols.indexOf(c.col); if (i < cfg.cols.length - 1) { c.col = cfg.cols[i + 1]; save(); draw(); } }
      if (add) { const t = prompt("Texto de la tarjeta:"); if (t && t.trim()) { cards.push({ id: Date.now(), col: cfg.cols[+add.dataset.add], text: t.trim(), tag: "" }); save(); draw(); } }
    });
    draw();
  });

  /* --- calendario --------------------------------------------------------------------- */
  $$("[data-calendar]").forEach((root) => {
    const cfg = JSON.parse(root.dataset.calendar); const evs = cfg.events || [];
    const MONTHS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];
    const DOW = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];
    const pad = (n) => String(n).padStart(2, "0");
    const today = new Date(); const todayKey = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
    let [y, m] = (cfg.month || "").split("-").map(Number);
    if (!y) { y = today.getFullYear(); m = today.getMonth() + 1; }
    let selected = null;
    const nice = (k) => k.split("-").reverse().join("/");
    const draw = () => {
      const start = (new Date(y, m - 1, 1).getDay() + 6) % 7, days = new Date(y, m, 0).getDate();
      let cells = "<div class=\"cal-cell empty\"></div>".repeat(start);
      for (let d = 1; d <= days; d++) {
        const k = `${y}-${pad(m)}-${pad(d)}`; const list = evs.filter((e) => e.date === k);
        cells += `<button type="button" class="cal-cell${k === todayKey ? " today" : ""}${k === selected ? " sel" : ""}${list.length ? " has" : ""}" data-day="${k}" aria-label="${nice(k)}${list.length ? `, ${list.length} evento(s)` : ""}">
          <span class="cal-n">${d}</span>${list.slice(0, 2).map((e) => `<span class="cal-ev"${e.color ? ` style="--ev:${esc(e.color)}"` : ""}>${esc(e.time ? e.time + " " : "")}${esc(e.text)}</span>`).join("")}
          ${list.length > 2 ? `<span class="cal-more">+${list.length - 2} más</span>` : ""}</button>`;
      }
      const sel = selected ? evs.filter((e) => e.date === selected).sort((a, b) => (a.time || "").localeCompare(b.time || "")) : [];
      const detail = !selected ? "<p class=\"muted\">Pulsa un día para ver sus eventos.</p>"
        : sel.length ? `<h4>${nice(selected)}</h4><ul>${sel.map((e) => `<li><b>${esc(e.time || "Todo el día")}</b> · ${esc(e.text)}</li>`).join("")}</ul>`
        : `<p class="muted">Sin eventos el ${nice(selected)}.</p>`;
      root.innerHTML = `<div class="cal-head"><button type="button" class="cal-nav" data-nav="-1" aria-label="Mes anterior">‹</button>
        <strong>${MONTHS[m - 1]} ${y}</strong><button type="button" class="cal-nav" data-nav="1" aria-label="Mes siguiente">›</button></div>
        <div class="cal-grid">${DOW.map((d) => `<div class="cal-dow">${d}</div>`).join("")}${cells}</div><div class="cal-detail">${detail}</div>`;
    };
    root.addEventListener("click", (e) => {
      const nav = e.target.closest("[data-nav]"), day = e.target.closest("[data-day]");
      if (nav) { m += +nav.dataset.nav; if (m < 1) { m = 12; y--; } if (m > 12) { m = 1; y++; } draw(); }
      if (day) { selected = day.dataset.day; draw(); }
    });
    draw();
  });

  /* --- cuenta regresiva ----------------------------------------------------------------- */
  $$("[data-countdown]").forEach((root) => {
    const end = new Date(root.dataset.countdown).getTime(); if (isNaN(end)) return;
    let timer = null;
    const tick = () => {
      const s = Math.max(0, Math.floor((end - Date.now()) / 1000));
      const v = { d: Math.floor(s / 86400), h: Math.floor((s % 86400) / 3600), m: Math.floor((s % 3600) / 60), s: s % 60 };
      Object.entries(v).forEach(([k, n]) => { const el = $(`[data-cd="${k}"]`, root); if (el) el.textContent = pad2(n); });
      if (s <= 0) { clearInterval(timer); const d = $(".cd-done", root); d.textContent = root.dataset.done; d.hidden = false; }
    };
    const pad2 = (n) => String(n).padStart(2, "0");
    timer = setInterval(tick, 1000); tick();
  });

  /* --- aviso superior que se puede cerrar ------------------------------------------------- */
  $$(".banner[data-dismiss]").forEach((b) => {
    const k = `lai-banner-${b.dataset.dismiss}`;
    if (store.get(k, false)) { b.remove(); return; }
    $(".banner-close", b)?.addEventListener("click", () => { b.remove(); store.set(k, true); });
  });

  /* --- lista de tareas ------------------------------------------------------------------ */
  $$("[data-todo]").forEach((root) => {
    const cfg = JSON.parse(root.dataset.todo); const key = `lai-todo-${cfg.key}`;
    let items = store.get(key, null) ?? cfg.seed.map((t, i) => ({ id: Date.now() + i, ...t }));
    let filter = "all";
    const list = $(".todo-list", root), form = $(".todo-form", root), input = $("input", form);
    const draw = () => {
      store.set(key, items);
      const vis = items.filter((t) => filter === "all" || (filter === "done") === !!t.done);
      list.innerHTML = vis.length ? vis.map((t) => `<li class="${t.done ? "done" : ""}"><input type="checkbox" data-tg="${t.id}" ${t.done ? "checked" : ""} aria-label="Completada">
        <span>${esc(t.text)}</span><button type="button" class="todo-del" data-del="${t.id}" aria-label="Borrar tarea">×</button></li>`).join("")
        : "<li class=\"muted\">Nada por aquí</li>";
      $(".todo-left", root).textContent = `${items.filter((t) => !t.done).length} pendiente(s)`;
    };
    form.addEventListener("submit", (e) => { e.preventDefault(); const v = input.value.trim(); if (!v) return; items.unshift({ id: Date.now(), text: v, done: false }); input.value = ""; draw(); });
    list.addEventListener("click", (e) => {
      const tg = e.target.closest("[data-tg]"), del = e.target.closest("[data-del]");
      if (tg) { const t = items.find((x) => x.id === +tg.dataset.tg); if (t) t.done = tg.checked; draw(); }
      if (del) { items = items.filter((x) => x.id !== +del.dataset.del); draw(); }
    });
    $$("[data-tf]", root).forEach((b) => b.addEventListener("click", () => { filter = b.dataset.tf; $$("[data-tf]", root).forEach((x) => x.classList.toggle("active", x === b)); draw(); }));
    $(".todo-clear", root)?.addEventListener("click", () => { items = items.filter((t) => !t.done); draw(); });
    draw();
  });

  /* --- horario: hoy y «abierto ahora» ------------------------------------------------------ */
  $$("[data-hours]").forEach((box) => {
    const now = new Date(), dow = ((now.getDay() + 6) % 7) + 1, mins = now.getHours() * 60 + now.getMinutes();
    const toMin = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + (m || 0); };
    let status = "closed", text = "Cerrado ahora";
    $$(".hours-row", box).forEach((row) => {
      const days = row.dataset.days.split(",").filter(Boolean).map(Number);
      if (!days.includes(dow)) return;
      row.classList.add("today");
      for (const s of row.dataset.slots.split(",").filter(Boolean)) {
        const [a, b] = s.split("-").map(toMin);
        if (mins >= a && mins < b) { status = "open"; text = `Abierto ahora · cierra a las ${s.split("-")[1]}`; }
        else if (status !== "open" && mins < a) text = `Cerrado ahora · abre a las ${s.split("-")[0]}`;
      }
    });
    const st = $(".hours-status", box); st.classList.add(status); st.textContent = text;
  });

  /* --- antes / después ---------------------------------------------------------------------- */
  $$("[data-ba]").forEach((ba) => { const r = $("input", ba); r.addEventListener("input", () => ba.style.setProperty("--pos", r.value + "%")); });

  /* --- ventanas emergentes -------------------------------------------------------------------- */
  $$("[data-modal-open]").forEach((b) => b.addEventListener("click", () => document.getElementById(b.dataset.modalOpen)?.showModal()));
  $$(".lai-dialog").forEach((d) => {
    d.addEventListener("click", (e) => { if (e.target === d || e.target.closest("[data-modal-close]")) d.close(); });
  });

  /* --- formulario por pasos ----------------------------------------------------------------- */
  $$("[data-wizard]").forEach((form) => {
    const steps = $$(".wz-step", form), marks = $$(".wz-progress span", form);
    const back = $("[data-wz-back]", form), next = $("[data-wz-next]", form), send = $("[data-wz-send]", form);
    let cur = 0;
    const go = (i) => {
      cur = i; steps.forEach((s, k) => { s.hidden = k !== i; });
      marks.forEach((m, k) => { m.classList.toggle("done", k < i); m.classList.toggle("cur", k === i); });
      back.style.visibility = i ? "visible" : "hidden"; next.hidden = i === steps.length - 1; send.hidden = i !== steps.length - 1;
    };
    next.addEventListener("click", () => { if (validate(steps[cur])) go(cur + 1); });
    back.addEventListener("click", () => go(Math.max(0, cur - 1)));
    form.addEventListener("submit", (e) => {
      e.preventDefault(); if (!validate(steps[cur])) return;
      const data = Object.fromEntries(new FormData(form)); const sk = form.dataset.store || "lai-pasos";
      const saved = store.get(sk, []); saved.push({ ...data, fecha_envio: new Date().toISOString() }); store.set(sk, saved);
      if (form.dataset.mailto) location.href = `mailto:${form.dataset.mailto}?subject=${encodeURIComponent(form.dataset.subject || "Formulario")}&body=${encodeURIComponent(Object.entries(data).map(([k, v]) => `${k}: ${v}`).join("\n"))}`;
      form.reset(); go(0); toast(form.dataset.ok || "¡Enviado!");
    });
    go(0);
  });

  /* --- cuestionario ------------------------------------------------------------------------------ */
  $$("[data-quiz]").forEach((qz) => {
    $("[data-quiz-check]", qz).addEventListener("click", () => {
      let ok = 0; const qs = $$(".quiz-q", qz);
      qs.forEach((q) => {
        const ans = +q.dataset.answer, sel = $("input:checked", q);
        $$(".quiz-opt", q).forEach((o, k) => { o.classList.toggle("right", k === ans); o.classList.toggle("wrong", !!sel && +sel.value === k && k !== ans); });
        if (sel && +sel.value === ans) ok++;
        const ex = $(".quiz-explain", q); if (ex && ex.textContent.trim()) ex.hidden = false;
      });
      const pct = qs.length ? Math.round(ok / qs.length * 100) : 0;
      $(".quiz-result", qz).textContent = `${ok} de ${qs.length} correctas (${pct}%) ${pct === 100 ? "🏆" : pct >= 60 ? "👏" : "📚"}`;
    });
  });

  /* --- encuesta ------------------------------------------------------------------------------------ */
  $$("[data-poll]").forEach((root) => {
    const cfg = JSON.parse(root.dataset.poll); const key = `lai-poll-${cfg.key}`;
    const votes = store.get(key + "-votos", cfg.opts.map((o) => o.votes || 0)); let mine = store.get(key, null);
    const draw = () => {
      const total = votes.reduce((a, b) => a + b, 0);
      $$(".poll-opt", root).forEach((b, i) => {
        const p = total ? Math.round(votes[i] / total * 100) : 0;
        b.classList.toggle("mine", mine === i);
        $(".fill", b).style.width = mine !== null ? p + "%" : "0"; $(".pct", b).textContent = mine !== null ? `${p}%` : "";
      });
      root.classList.toggle("voted", mine !== null);
      $(".poll-total", root).textContent = mine !== null ? `${total} voto(s)` : "Elige una opción para ver los resultados";
    };
    root.addEventListener("click", (e) => {
      const b = e.target.closest(".poll-opt"); if (!b || mine !== null) return;
      mine = +b.dataset.i; votes[mine]++; store.set(key, mine); store.set(key + "-votos", votes); draw();
    });
    draw();
  });

  /* --- calculadora (fórmula segura: números, variables, + - * / ^ ( ) y funciones de Math) ---------- */
  const FN = new Set(["round", "min", "max", "pow", "sqrt", "abs", "floor", "ceil"]);
  function evalFormula(f, vars) {
    let js = "";
    for (const m of f.matchAll(/\s*([A-Za-z_]\w*|\d+(?:[.,]\d+)?|[-+*/%^(),])/g)) {
      const t = m[1];
      if (/^[A-Za-z_]/.test(t)) { if (t in vars) js += `(${+vars[t] || 0})`; else if (FN.has(t)) js += `Math.${t}`; else throw new Error(t); }
      else if (/^\d/.test(t)) js += t.replace(",", ".");
      else js += t === "^" ? "**" : t;
    }
    return Function(`"use strict"; return (${js || 0});`)();
  }
  $$("[data-calc]").forEach((root) => {
    const out = $(".calc-out strong", root), dec = +root.dataset.decimals || 0, unit = root.dataset.unit || "";
    const run = () => {
      const vars = Object.fromEntries($$("[data-var]", root).map((i) => [i.dataset.var, parseFloat(String(i.value).replace(",", ".")) || 0]));
      try { const v = evalFormula(root.dataset.formula, vars); out.textContent = isFinite(v) ? v.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec }) + (unit ? " " + unit : "") : "—"; }
      catch (err) { out.textContent = "Fórmula no válida"; }
    };
    root.addEventListener("input", run); root.addEventListener("change", run); run();
  });

  /* --- control de gastos ---------------------------------------------------------------------------- */
  $$("[data-expenses]").forEach((root) => {
    const cfg = JSON.parse(root.dataset.expenses); const key = `lai-gastos-${cfg.key}`;
    let items = store.get(key, null) ?? cfg.seed.map((x, i) => ({ id: Date.now() + i, ...x }));
    const money = (n) => `${n < 0 ? "-" : ""}${cfg.currency}${Math.abs(n).toFixed(2)}`;
    const draw = () => {
      store.set(key, items);
      const inc = items.filter((x) => x.type === "ingreso").reduce((s, x) => s + x.amount, 0);
      const out = items.filter((x) => x.type !== "ingreso").reduce((s, x) => s + x.amount, 0);
      $('[data-k="in"]', root).textContent = money(inc); $('[data-k="out"]', root).textContent = money(out);
      const bal = $('[data-k="bal"]', root); bal.textContent = money(inc - out); bal.className = inc - out >= 0 ? "exp-in" : "exp-out";
      $(".exp-list", root).innerHTML = items.length ? items.map((x) => `<li><span>${esc(x.text)}<br><small>${esc(x.category)}${x.date ? " · " + esc(x.date) : ""}</small></span>
        <b class="${x.type === "ingreso" ? "exp-in" : "exp-out"}">${x.type === "ingreso" ? "+" : "−"}${money(x.amount)}</b>
        <button type="button" class="todo-del" data-del="${x.id}" aria-label="Borrar movimiento" style="background:none;border:0;cursor:pointer;color:var(--muted);font-size:1.1rem">×</button></li>`).join("")
        : '<li class="muted">Sin movimientos</li>';
      const by = {}; items.filter((x) => x.type !== "ingreso").forEach((x) => { by[x.category] = (by[x.category] || 0) + x.amount; });
      const max = Math.max(1, ...Object.values(by));
      $(".exp-chart", root).innerHTML = Object.keys(by).length ? Object.entries(by).sort((a, b) => b[1] - a[1]).map(([c, v]) =>
        `<div class="exp-bar"><div><span>${esc(c)}</span><b>${money(v)}</b></div><i style="width:${(v / max * 100).toFixed(0)}%"></i></div>`).join("")
        : '<p class="muted">Aún no hay gastos.</p>';
    };
    $(".exp-form", root).addEventListener("submit", (e) => {
      e.preventDefault(); const d = Object.fromEntries(new FormData(e.target)); const amount = Math.abs(parseFloat(d.amount));
      if (!d.text.trim() || !amount) return toast("Escribe el concepto y el importe");
      items.unshift({ id: Date.now(), text: d.text.trim(), amount, type: d.type, category: d.category, date: new Date().toISOString().slice(0, 10) });
      e.target.reset(); draw();
    });
    $(".exp-list", root).addEventListener("click", (e) => { const b = e.target.closest("[data-del]"); if (b) { items = items.filter((x) => x.id !== +b.dataset.del); draw(); } });
    draw();
  });

  /* --- botones flotantes: modo oscuro y volver arriba ------------------------------------------------ */
  const modeBtn = $("[data-theme-toggle]");
  if (modeBtn) {
    const apply = (m) => { if (m === "dark") document.documentElement.dataset.mode = "dark"; else delete document.documentElement.dataset.mode;
      modeBtn.textContent = m === "dark" ? "☀️" : "🌙"; modeBtn.setAttribute("aria-label", m === "dark" ? "Cambiar a modo claro" : "Cambiar a modo oscuro"); };
    apply(store.get("lai-modo", "light"));
    modeBtn.addEventListener("click", () => { const m = document.documentElement.dataset.mode === "dark" ? "light" : "dark"; store.set("lai-modo", m); apply(m); });
  }
  const topBtn = $("[data-totop]");
  if (topBtn) {
    const f = () => topBtn.classList.toggle("show", scrollY > 600); f(); addEventListener("scroll", f, { passive: true });
    topBtn.addEventListener("click", () => scrollTo({ top: 0, behavior: "smooth" }));
  }

  /* --- aviso de cookies --------------------------------------------------------------------------------- */
  $$(".cookie").forEach((c) => {
    if (store.get("lai-cookies", null) === null) c.hidden = false;
    $$("[data-cookie]", c).forEach((b) => b.addEventListener("click", () => { store.set("lai-cookies", b.dataset.cookie); c.hidden = true; }));
  });

  /* --- chatbot de preguntas frecuentes ------------------------------------------------------------------- */
  $$("[data-bot]").forEach((bot) => {
    const cfg = JSON.parse(bot.dataset.bot), log = $(".bot-log", bot), input = $("input", bot);
    const norm = (s) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
    const say = (text, me = false) => { const m = document.createElement("div"); m.className = "bot-msg" + (me ? " me" : ""); m.textContent = text; log.appendChild(m); log.scrollTop = log.scrollHeight; };
    const answer = (q) => {
      const w = norm(q); let best = null, score = 0;
      cfg.faq.forEach((f) => { const s = f.q.filter((k) => w.includes(norm(k))).length; if (s > score) { score = s; best = f; } });
      setTimeout(() => say(best ? best.a : cfg.fallback), 450);
    };
    $(".bot-chips", bot).innerHTML = cfg.faq.filter((f) => f.label).map((f, i) => `<button type="button" data-chip="${i}">${esc(f.label)}</button>`).join("");
    $(".bot-chips", bot).addEventListener("click", (e) => { const c = e.target.closest("[data-chip]"); if (!c) return; const f = cfg.faq[+c.dataset.chip]; say(f.label, true); setTimeout(() => say(f.a), 450); });
    $("form", bot).addEventListener("submit", (e) => { e.preventDefault(); const q = input.value.trim(); if (!q) return; say(q, true); input.value = ""; answer(q); });
    let greeted = false;
    $$("[data-bot-open]").forEach((b) => b.addEventListener("click", () => { bot.hidden = !bot.hidden; if (!bot.hidden) { if (!greeted) { say(cfg.greeting); greeted = true; } input.focus(); } }));
    $("[data-bot-close]", bot).addEventListener("click", () => { bot.hidden = true; });
  });

  /* --- presupuesto / factura ------------------------------------------------------------------------------- */
  $$("[data-invoice]").forEach((inv) => {
    const tax = +inv.dataset.tax || 0, cur = inv.dataset.currency || "";
    const money = (n) => `${cur}${n.toFixed(2)}`;
    const run = () => {
      let sub = 0;
      $$("tbody tr", inv).forEach((tr) => { const q = parseFloat($("[data-qty]", tr)?.value ?? 1) || 0; const v = q * (+tr.dataset.price || 0); sub += v; $("[data-line]", tr).textContent = money(v); });
      $("[data-sub]", inv).textContent = money(sub); $("[data-tax-v]", inv).textContent = money(sub * tax / 100); $("[data-total]", inv).textContent = money(sub * (1 + tax / 100));
    };
    inv.addEventListener("input", run);
  });

  /* --- precios: mensual / anual ------------------------------------------------------------------------------ */
  $$("[data-billing]").forEach((t) => {
    const sec = t.closest("section");
    $$("[data-bill]", t).forEach((b) => b.addEventListener("click", () => {
      const year = b.dataset.bill === "year";
      $$("[data-bill]", t).forEach((x) => x.setAttribute("aria-pressed", x === b));
      $$(".price[data-vm]", sec).forEach((p) => {
        const v = year && p.dataset.vy ? p.dataset.vy : p.dataset.vm;
        $("span", p).textContent = v; $("small", p).textContent = year && p.dataset.vy ? p.dataset.py : p.dataset.pm;
      });
    }));
  });

  /* --- YouTube abierto como archivo local: sin origen el reproductor falla (error 153) -------- */
  if (location.protocol === "file:") {
    $$('iframe[src*="youtube-nocookie.com/embed/"]').forEach((f) => {
      const id = f.src.split("/embed/")[1].split(/[?&]/)[0];
      const a = document.createElement("a");
      a.href = `https://www.youtube.com/watch?v=${id}`; a.target = "_blank"; a.rel = "noopener"; a.className = "yt-poster";
      a.setAttribute("aria-label", `Ver «${f.title}» en YouTube`);
      a.innerHTML = `<img src="https://i.ytimg.com/vi/${id}/hqdefault.jpg" alt=""><span>▶</span>`;
      f.replaceWith(a);
    });
  }

  /* --- año actual en el pie --------------------------------------------------------- */
  $$("[data-year]").forEach((el) => { el.textContent = new Date().getFullYear(); });
})();
