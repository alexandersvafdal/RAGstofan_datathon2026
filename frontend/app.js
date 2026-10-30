// Renders the backend's Answer object (hagbot/schemas.py) and the dashboard (/api/indicators). No build step.

const I18N = {
  en: {
    eyebrow: "Statistics Iceland · Gagnaþon 2026", hello: "Iceland, in numbers.",
    sub: "Ask anything about Iceland's official statistics. Every answer comes with the figure, its unit and the table it came from.",
    today: "The latest numbers", asof: "Data as of", placeholder: "Ask about Icelandic statistics",
    steps: ["Understanding the question", "Finding the right table", "Choosing the categories", "Reading the numbers", "Writing the answer"],
    working: "Working on it", how: "How I got this", data: "Data", source: "Source", checked: "Numbers checked against the table",
    calc: "Calculated", failed: "Something went wrong", retry: "Try again", seconds: "s", calls: "model calls",
    pts: "pts", more: "Ask next", palette: "Colour", appearance: "Appearance",
    live: { plan: q => `Read as: ${q}`, split: n => `Split into ${n} questions`, searched: n => `Searched ${n} tables`,
            picked: "picked", numbers: n => `${n} ${n === 1 ? "number" : "numbers"} read`, checked: "Numbers checked against the table",
            template: "Written from the numbers directly", error: "Hit a problem" },
  },
  is: {
    eyebrow: "Hagstofa Íslands · Gagnaþon 2026", hello: "Ísland í tölum.",
    sub: "Spurðu um opinbera tölfræði Íslands. Hverju svari fylgir talan, eining hennar og taflan sem hún kemur úr.",
    today: "Nýjustu tölur", asof: "Gögn frá", placeholder: "Spyrðu um íslenska tölfræði",
    steps: ["Skil spurninguna", "Finn réttu töfluna", "Vel flokkana", "Les tölurnar", "Skrifa svarið"],
    working: "Augnablik", how: "Hvernig svarið varð til", data: "Gögn", source: "Heimild", checked: "Tölur bornar saman við töfluna",
    calc: "Reiknað", failed: "Eitthvað fór úrskeiðis", retry: "Reyna aftur", seconds: "s", calls: "köll í líkan",
    pts: "pst.", more: "Spyrðu næst", palette: "Litir", appearance: "Útlit",
    live: { plan: q => `Skilið sem: ${q}`, split: n => `Skipt í ${n} spurningar`, searched: n => `Leitaði í ${n} töflum`,
            picked: "valdi", numbers: n => `${n} ${n === 1 ? "tala lesin" : "tölur lesnar"}`, checked: "Tölur bornar saman við töfluna",
            template: "Skrifað beint upp úr tölunum", error: "Vandamál kom upp" },
  },
};

// Example questions. topic ties them to tables for follow-up suggestions.
const POOL = [
  ["population", "How many people live in Iceland?", "Hve margir búa á Íslandi?"],
  ["population", "How many people live in Reykjavík?", "Hvað búa margir í Reykjavík?"],
  ["population", "How much did Reykjavík grow from 2025 to 2026?", "Hve mikið fjölgaði í Reykjavík frá 2025 til 2026?"],
  ["population", "What share of Iceland's people live in Reykjavík?", "Hve stórt hlutfall landsmanna býr í Reykjavík?"],
  ["population", "How many people live in Kópavogur and Hafnarfjörður together?", "Hve margir búa í Kópavogi og Hafnarfirði samtals?"],
  ["population", "How many more men than women live in Iceland?", "Hve mörgum fleiri karlar en konur búa á Íslandi?"],
  ["population", "How many children were born in Iceland in 2025?", "Hve mörg börn fæddust á Íslandi árið 2025?"],
  ["population", "How many immigrants live in Iceland?", "Hve margir innflytjendur búa á Íslandi?"],
  ["prices", "What is the annual inflation rate?", "Hver er verðbólgan?"],
  ["prices", "How has the consumer price index changed since 2020?", "Hvernig hefur vísitala neysluverðs þróast frá 2020?"],
  ["prices", "How much have house prices risen in the last year?", "Hve mikið hefur íbúðaverð hækkað síðasta árið?"],
  ["wages", "How much have wages risen in the last 12 months?", "Hve mikið hafa laun hækkað síðustu 12 mánuði?"],
  ["labour", "What is the unemployment rate?", "Hvert er atvinnuleysið?"],
  ["labour", "How many people live in Iceland, and what is the unemployment rate?", "Hve margir búa á Íslandi og hvert er atvinnuleysið?"],
  ["tourism", "How many overnight stays were there in registered accommodation?", "Hve margar gistinætur voru á skráðum gististöðum?"],
  ["tourism", "How many foreign passengers came through Keflavík airport in August?", "Hve margir erlendir farþegar fóru um Keflavíkurflugvöll í ágúst?"],
  ["tourism", "What share of overnight stays were by foreigners in 2025?", "Hve stór hluti gistinátta var vegna útlendinga árið 2025?"],
  ["economy", "What was Iceland's economic growth last year?", "Hver var hagvöxturinn á síðasta ári?"],
  ["economy", "What was Iceland's GDP in 2025?", "Hver var landsframleiðslan árið 2025?"],
  ["economy", "How much fish did Iceland export last year?", "Hve mikið var flutt út af sjávarafurðum á síðasta ári?"],
  ["transport", "How many passenger cars were registered in Iceland in 2025?", "Hve margir fólksbílar voru skráðir á Íslandi árið 2025?"],
];
const TOPIC_OF = [[/^MAN/, "population"], [/^VIS/, "prices"], [/^LAU/, "wages"], [/^VIN/, "labour"],
  [/^SAM0[12]/, "tourism"], [/^(THJ|UTA)/, "economy"], [/^SAM03/, "transport"]];

let lang = "auto";
const $ = (s, el = document) => el.querySelector(s);
const ui = () => (lang === "is" ? "is" : "en");
const t = (k, l = ui()) => (I18N[l] || I18N.en)[k];
const locale = l => (l === "is" ? "is-IS" : "en-US");
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

function el(tag, attrs = {}, ...children) {
  const e = document.createElementNS(attrs.svg ? "http://www.w3.org/2000/svg" : "http://www.w3.org/1999/xhtml", tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "svg") continue;
    if (k === "class") e.setAttribute("class", v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) e.append(c);
  return e;
}

/* ---------- formatting ---------- */
// Written out rather than taken from Intl: some browsers ship without Icelandic locale data.
const MONTHS = {
  en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
  is: ["jan.", "feb.", "mar.", "apr.", "maí", "jún.", "júl.", "ágú.", "sep.", "okt.", "nóv.", "des."],
};
// Icelandic numbers: 394.324 and 5,9. Built from the English format by swapping the separators.
const localize = (text, L) => (L === "is" ? text.replace(/[.,]/g, c => (c === "," ? "." : ",")) : text);
function fmtPeriod(p, L = ui()) {
  let m = /^(\d{4})M(\d{2})$/.exec(p);
  if (m) return `${MONTHS[L === "is" ? "is" : "en"][+m[2] - 1]} ${m[1]}`;
  m = /^(\d{4})Q(\d)$/.exec(p);
  if (m) return L === "is" ? `${m[2]}. ársfj. ${m[1]}` : `Q${m[2]} ${m[1]}`;
  return p;
}
const prettyPeriods = (text, L = ui()) => text.replace(/\b\d{4}(M\d{2}|Q\d)\b/g, m => fmtPeriod(m, L));
function fmtNum(v, unit, L = ui()) {
  const pct = unit === "%";
  return localize(new Intl.NumberFormat("en-US", pct ? { minimumFractionDigits: 1, maximumFractionDigits: 1 }
    : { maximumFractionDigits: Math.abs(v) < 100 && !Number.isInteger(v) ? 1 : 0 }).format(v), L);
}
function fmtDelta(ind, L) {
  if (ind.previous == null) return "";
  const d = ind.value - ind.previous;
  const arrow = d > 0 ? "↑" : d < 0 ? "↓" : "→";
  if (ind.unit.en === "%") return `${arrow} ${fmtNum(Math.abs(d), "%", L)} ${t("pts", L)}`;
  if (!ind.previous) return arrow;
  return `${arrow} ${localize(new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(Math.abs(d / ind.previous * 100)), L)}%`;
}

/* ---------- static text ---------- */
function applyLanguage() {
  document.documentElement.lang = ui();
  document.querySelectorAll("[data-i18n]").forEach(e => { e.textContent = t(e.dataset.i18n); });
  $("#q").placeholder = t("placeholder");
  renderTiles();
  pills.restart();
}

/* ---------- floating question pills ---------- */
const pills = (() => {
  const field = $("#pills");
  let timers = [], queue = [], live = new Set(), running = false;

  function questions() {
    const pick = lang === "auto" ? POOL.flatMap(([, en, is], i) => (i % 3 === 2 ? [[is, "is"]] : [[en, "en"]]))
      : POOL.map(([, en, is]) => (lang === "is" ? [is, "is"] : [en, "en"]));
    return pick.sort(() => Math.random() - 0.5);
  }
  const later = (fn, ms) => { const id = setTimeout(fn, ms); timers.push(id); return id; };

  function overlaps(box) {
    for (const p of live) {
      const b = p._box;
      if (box.x < b.x + b.w + 14 && box.x + box.w + 14 > b.x && box.y < b.y + b.h + 12 && box.y + box.h + 12 > b.y) return true;
    }
    return false;
  }

  function spawn() {
    if (!running) return;
    if (!queue.length) queue = questions();
    const [text, l] = queue.shift();
    const p = el("button", { class: "pill", type: "button" }, text, lang === "auto" && l === "is" ? el("span", { class: "lang-tag" }, "IS") : null);
    p.addEventListener("click", () => ask(text));
    field.append(p);
    const W = field.clientWidth, H = field.clientHeight, w = p.offsetWidth, h = p.offsetHeight;
    let box = null;
    for (let i = 0; i < 40 && !box; i++) {
      const x = Math.random() * Math.max(1, W - w), y = Math.random() * Math.max(1, H - h);
      const cand = { x, y, w, h };
      if (!overlaps(cand)) box = cand;
    }
    if (!box) { p.remove(); later(spawn, 1200); return; }
    const life = 9000 + Math.random() * 6000;
    const dx = (Math.random() - 0.5) * 60, dy = (Math.random() - 0.5) * 24;
    const x2 = Math.min(Math.max(0, box.x + dx), W - w), y2 = Math.min(Math.max(0, box.y + dy), H - h);
    p._box = { x: Math.min(box.x, x2), y: Math.min(box.y, y2), w: w + Math.abs(x2 - box.x), h: h + Math.abs(y2 - box.y) };
    Object.entries({ "--x": `${box.x}px`, "--y": `${box.y}px`, "--x2": `${x2}px`, "--y2": `${y2}px`, "--life": `${life}ms` })
      .forEach(([k, v]) => p.style.setProperty(k, v));
    live.add(p);
    requestAnimationFrame(() => requestAnimationFrame(() => p.classList.add("in")));
    later(() => p.classList.add("drift"), 900);
    p._out = later(() => leave(p), life);
    p.addEventListener("mouseenter", () => {          // hold still while the pointer is on it
      clearTimeout(p._out);
      p.style.transition = "box-shadow .25s, border-color .25s";
      p.style.transform = getComputedStyle(p).transform;
    });
    p.addEventListener("mouseleave", () => {
      p.style.transition = ""; p.style.transform = "";
      p._out = later(() => leave(p), 2500);
    });
  }

  function leave(p) {
    p.classList.add("out");
    later(() => { p.remove(); live.delete(p); }, 900);
    later(spawn, 500 + Math.random() * 900);
  }

  function start() {
    stop();
    running = true;
    queue = questions();
    if (reduceMotion) {
      field.classList.add("static");
      queue.slice(0, 6).forEach(([text]) => field.append(el("button", { class: "pill static", type: "button", onclick: () => ask(text) }, text)));
      return;
    }
    const n = field.clientWidth < 600 ? 5 : 7;
    for (let i = 0; i < n; i++) later(spawn, 250 + i * 650);
  }
  function stop() {
    running = false;
    timers.forEach(clearTimeout); timers = [];
    live.clear(); field.replaceChildren(); field.classList.remove("static");
  }
  return { start, stop, restart: () => running && start() };
})();

/* ---------- dashboard ---------- */
let indicatorData = null;

async function loadIndicators() {
  $("#tiles").replaceChildren(...Array.from({ length: 6 }, () => el("div", { class: "tile skeleton" })));
  try {
    indicatorData = await (await fetch("/api/indicators")).json();
  } catch { indicatorData = { indicators: [], data_as_of: "" }; }
  renderTiles();
}

// Each tile shows its history in a different small chart, chosen for what the numbers are:
// a count that grows (slope), a monthly rate (heatmap), a rate over time (line), a monthly change (lollipop),
// a yearly total (bars), a growth rate that can go negative (diverging bars).
const MINI_KIND = { population: "slope", inflation: "heatmap", unemployment: "line", wages: "lollipop",
                    overnight_stays: "bars", gdp_growth: "diverging" };
const svgEl = (tag, attrs = {}, ...kids) => el(tag, { svg: 1, ...attrs }, ...kids);
const tip = (p, unit, L) => svgEl("title", {}, `${fmtPeriod(p.x, L)}: ${fmtNum(p.y, unit, L)}${unit === "%" ? "%" : ""}`);

function drawMini(holder) {
  const { ind, L } = holder._mini;
  const unit = ind.unit.en, kind = MINI_KIND[ind.id] || "line";
  const W = Math.max(120, Math.round(holder.clientWidth || 200)), H = 56, pad = 4;
  const s = kind === "lollipop" ? ind.series.slice(-12) : ind.series;
  const ys = s.map(p => p.y);
  const lo = Math.min(...ys, kind === "bars" || kind === "diverging" ? 0 : Infinity), hi = Math.max(...ys, kind === "diverging" ? 0 : -Infinity);
  const span = hi - lo || 1;
  const X = i => pad + (i / Math.max(1, s.length - 1)) * (W - 2 * pad);
  const bottom = kind === "slope" ? 16 : pad;   // the slope keeps room under it for its year labels
  const Y = v => H - bottom - ((v - lo) / span) * (H - bottom - pad);
  const svg = svgEl("svg", { class: `mini-svg ${kind}`, viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
    "aria-label": `${ind.label[L]}: ${s.length} ${L === "is" ? "tímabil" : "periods"}` });
  const last = s.length - 1;

  if (kind === "line" || kind === "slope") {
    const pts = s.map((p, i) => [X(i), Y(p.y)]);
    const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
    if (kind === "line") svg.append(svgEl("path", { class: "area", d: `${d}L${pts[last][0]},${H}L${pts[0][0]},${H}Z` }));
    svg.append(svgEl("path", { class: "line draw", d, pathLength: 1 }));
    if (kind === "slope") {        // first and last point, labelled with their years
      for (const i of [0, last]) svg.append(svgEl("circle", { class: i ? "dot lead" : "dot", cx: pts[i][0], cy: pts[i][1], r: i ? 4.5 : 3.5 }, tip(s[i], unit, L)));
      svg.append(svgEl("text", { class: "lbl", x: pts[0][0], y: H - 1, "text-anchor": "start" }, fmtPeriod(s[0].x, L)));
      svg.append(svgEl("text", { class: "lbl", x: pts[last][0], y: H - 1, "text-anchor": "end" }, fmtPeriod(s[last].x, L)));
    } else {
      svg.append(svgEl("circle", { class: "dot lead", cx: pts[last][0], cy: pts[last][1], r: 3.5 }, tip(s[last], unit, L)));
    }

  } else if (kind === "bars" || kind === "diverging" || kind === "lollipop") {
    const n = s.length, gap = kind === "lollipop" ? 0 : Math.max(2, W / n * 0.22), bw = (W - 2 * pad - gap * (n - 1)) / n;
    const zero = Y(Math.max(lo, Math.min(0, hi)));
    s.forEach((p, i) => {
      const x = pad + i * (bw + gap), yv = Y(p.y), top = Math.min(yv, zero), h = Math.max(1.5, Math.abs(zero - yv));
      const cls = (p.y < 0 ? "neg " : "") + (i === last ? "lead" : "");
      if (kind === "lollipop") {
        const cx = x + bw / 2;
        svg.append(svgEl("g", { class: `pop ${cls}`, style: `--i:${i}` }, svgEl("line", { x1: cx, x2: cx, y1: zero, y2: yv }),
          svgEl("circle", { cx, cy: yv, r: i === last ? 4 : 3 }), tip(p, unit, L)));
      } else {
        svg.append(svgEl("rect", { class: `bar ${cls}`, x, y: top, width: bw, height: h, rx: Math.min(3, bw / 2), style: `--i:${i}` }, tip(p, unit, L)));
      }
    });
    if (kind === "diverging") svg.append(svgEl("line", { class: "zero", x1: pad, x2: W - pad, y1: zero, y2: zero }));

  } else if (kind === "heatmap") {   // a row per year, a column per month; darker means higher
    const rows = [...new Set(s.map(p => p.x.slice(0, 4)))];
    const cw = (W - 2 * pad - 28) / 12, ch = (H - 2 * pad - (rows.length - 1) * 3) / rows.length;
    rows.forEach((yr, r) => svg.append(svgEl("text", { class: "lbl", x: pad, y: pad + r * (ch + 3) + ch / 2 + 3.5 }, yr)));
    s.forEach((p, i) => {
      const r = rows.indexOf(p.x.slice(0, 4)), m = +p.x.slice(5) - 1;
      const t = (p.y - lo) / span;
      svg.append(svgEl("rect", { class: "cell" + (i === last ? " lead" : ""), x: pad + 28 + m * cw + 1, y: pad + r * (ch + 3), width: cw - 2.5, height: ch,
        rx: 3, style: `--i:${i};fill-opacity:${(0.18 + 0.82 * t).toFixed(2)}` }, tip(p, unit, L)));
    });
  }
  holder.replaceChildren(svg);
}

function miniChart(ind, L) {
  const holder = el("div", { class: "mini" });
  holder._mini = { ind, L };
  return holder;
}

function renderTiles() {
  if (!indicatorData) return;
  const L = ui();
  $("#asof").textContent = indicatorData.data_as_of ? `${t("asof", L)} ${indicatorData.data_as_of.replace(" snapshot", "")}` : "";
  $("#tiles").replaceChildren(...indicatorData.indicators.map((ind, i) => {
    const unit = ind.unit[L];
    const tile = el("button", { class: "tile", type: "button", style: `animation-delay:${i * 70}ms`,
        title: ind.question[L], onclick: () => ask(ind.question[L]) },
      el("span", { class: "t-label" }, ind.label[L]),
      el("span", { class: "t-value" }, fmtNum(ind.value, unit, L), el("small", {}, unit === "%" ? "%" : "")),
      el("span", { class: "t-meta" }, fmtPeriod(ind.period, L), el("span", { class: "delta" }, fmtDelta(ind, L))),
      miniChart(ind, L),
      el("span", { class: "t-src" }, `${ind.table.id} · ${unit === "%" ? "" : unit}`.replace(/ · $/, "")));
    return tile;
  }));
  requestAnimationFrame(() => document.querySelectorAll(".tile .mini").forEach(drawMini));  // sized to the tile, so dots stay round
}
let miniResize;
addEventListener("resize", () => { clearTimeout(miniResize); miniResize = setTimeout(() => document.querySelectorAll(".tile .mini").forEach(drawMini), 150); });

/* ---------- conversation ---------- */
function showChat() {
  const home = $("#home");
  if (home.hidden) return;
  pills.stop();
  home.classList.add("leaving");
  setTimeout(() => { home.hidden = true; $("#chat").classList.add("on"); }, reduceMotion ? 0 : 350);
}

// Live progress: each step lights up when the server reports it done, with what it actually found.
const STEP_ORDER = ["plan", "retrieve", "select", "fetch", "compose"];

function readSelection(detail, L) {
  // "value {'Month': ['2026M09'], 'Item': ['Annual change, %']} notes: [...]" -> "Month: Sep 2026 · Item: Annual change, %"
  const pairs = [...detail.matchAll(/'([^']+)': \[([^\]]*)\]/g)].map(([, k, v]) => {
    const vals = [...v.matchAll(/'([^']*)'/g)].map(m => prettyPeriods(m[1], L));
    return `${k}: ${vals.length > 3 ? `${vals[0]} … ${vals[vals.length - 1]} (${vals.length})` : vals.join(", ")}`;
  });
  return pairs.slice(0, 4).join(" · ");
}

function liveView(L) {
  const words = t("live", L);
  const rows = {};
  const list = el("ol", { class: "live-steps" }, STEP_ORDER.map((name, i) => {
    const row = el("li", { class: "ls" },
      el("span", { class: "ls-icon", "aria-hidden": "true" },
        el("svg", { svg: 1, viewBox: "0 0 24 24" }, el("path", { svg: 1, class: "tick", d: "M6 12.5l4 4 8-9" }))),
      el("div", { class: "ls-body" },
        el("div", { class: "ls-title" }, el("span", {}, t("steps", L)[i]), el("span", { class: "ls-ms" })),
        el("div", { class: "ls-detail" })));
    rows[name] = row;
    return row;
  }));
  const time = el("span", { class: "live-time" }, "0.0 s");
  const view = el("div", { class: "live" },
    el("div", { class: "live-head" }, el("span", { class: "live-orb", "aria-hidden": "true" }), el("span", {}, `${t("working", L)}…`), time),
    list);
  const t0 = performance.now();
  const done = new Set();
  let parts = 1;
  const refresh = () => {
    const current = STEP_ORDER.find(n => !done.has(n));
    STEP_ORDER.forEach(n => rows[n].classList.toggle("now", n === current));
  };
  view._timer = setInterval(() => { time.textContent = `${((performance.now() - t0) / 1000).toFixed(1)} s`; }, 100);
  refresh();

  view.step = st => {
    const row = rows[st.name];
    if (!row) {                                     // an error step: show it on the step in progress
      const cur = STEP_ORDER.find(n => !done.has(n));
      if (cur) rows[cur].classList.add("bad"), (rows[cur].querySelector(".ls-detail").textContent = words.error);
      return;
    }
    const detail = row.querySelector(".ls-detail");
    const add = node => { detail.append(node); row.classList.add("has-detail"); };
    if (st.name === "plan") {
      const qs = st.detail.split(" | ");
      parts = qs.length;
      add(el("span", { class: "ls-text" }, qs.length > 1 ? words.split(qs.length) : words.plan(qs[0])));
    } else if (st.name === "retrieve") {
      const m = /^(\S+) from \[(.*)\]/.exec(st.detail);
      const cands = m ? m[2].split(", ") : [];
      add(el("span", { class: "ls-text" }, `${words.searched(document.querySelector("#status").dataset.tables || "1,857")} → ${words.picked} `, el("b", {}, m ? m[1] : st.detail)));
      add(el("span", { class: "cands" }, cands.slice(0, 8).map((c, i) =>
        el("span", { class: "cand" + (m && c === m[1] ? " hit" : ""), style: `animation-delay:${i * 60}ms` }, c))));
    } else if (st.name === "select") {
      add(el("span", { class: "ls-text" }, readSelection(st.detail, L)));
    } else if (st.name === "fetch") {
      const n = +(/^(\d+) rows/.exec(st.detail) || [0, 0])[1];
      const note = st.detail.split("; ")[1];
      add(el("span", { class: "ls-text" }, words.numbers(n) + (note ? ` · ${prettyPeriods(note, L)}` : "")));
    } else if (st.name === "compose") {
      add(el("span", { class: "ls-text" }, st.detail === "model" ? words.checked : words.template));
    }
    const ms = row.querySelector(".ls-ms");
    ms.textContent = st.ms >= 1000 ? `${(st.ms / 1000).toFixed(1)} s` : `${st.ms} ms`;
    row._count = (row._count || 0) + 1;
    // retrieve, select and fetch run once per sub-question
    if (st.name === "plan" || st.name === "compose" || row._count >= parts) { done.add(st.name); row.classList.add("done"); }
    refresh();
  };
  return view;
}

async function askStream(body, onStep) {
  const r = await fetch("/api/ask/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body });
  if (!r.ok || !r.body) throw new Error(`${r.status} ${r.ok ? "" : await r.text()}`);
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      const msg = JSON.parse(line);
      if (msg.type === "step") onStep(msg);
      else if (msg.type === "answer") return msg.answer;
    }
  }
  throw new Error("the answer stream ended early");
}

async function ask(question, tableId = null) {
  showChat();
  const chat = $("#chat");
  const guess = lang === "auto" ? (/[þðæöáéíóúý]/i.test(question) ? "is" : "en") : lang;
  if (!tableId) chat.append(el("div", { class: "msg user" }, el("div", { class: "bubble" }, question)));
  const card = el("div", { class: "card" });
  const progress = liveView(guess);
  card.append(progress);
  const bot = el("div", { class: "msg bot" }, card);
  chat.append(bot);
  setTimeout(() => bot.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "end" }), 380);
  $("#send").disabled = true;
  try {
    const body = JSON.stringify({ question, language: lang === "auto" ? null : lang, table_id: tableId });
    const answer = await askStream(body, st => progress.step(st));
    clearInterval(progress._timer);
    progress.classList.add("finishing");
    await new Promise(r => setTimeout(r, reduceMotion ? 0 : 450));   // let the last tick land before the answer replaces it
    renderAnswer(card, answer);
    card.classList.add("arrive");
  } catch (e) {
    card.replaceChildren(
      el("p", { class: "answer-text error" }, `${t("failed", guess)}. ${e.message}`),
      el("div", { class: "followups" }, el("button", { class: "pill", type: "button", onclick: () => { bot.remove(); ask(question, tableId); } }, t("retry", guess))));
  } finally {
    clearInterval(progress._timer);
    $("#send").disabled = false;
    bot.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" });
  }
}

function renderAnswer(card, a) {
  const L = a.language;
  card.replaceChildren(el("p", { class: "answer-text" + (a.status === "error" ? " error" : "") }, prettyPeriods(a.text, L)));

  if (a.status === "clarify" && a.clarification) {
    card.append(el("div", { class: "clarify" },
      el("div", {}, a.clarification.message),
      el("div", { class: "opts" }, a.clarification.options.map(o =>
        el("button", { type: "button", onclick: () => ask(a.question, o.table_id) }, `${o.label} `, el("span", { class: "muted" }, o.table_id))))));
  }

  const figures = el("div", { class: "figures" });
  for (const p of a.parts) {
    // a calculation: its result; a series: where it started and where it is now; otherwise up to three numbers
    const lines = new Map();   // several series: the latest value of each line
    for (const f of p.facts) lines.set(f.label.replace(/\b\d{4}(M\d{2}|Q\d)?\b,?\s*/, ""), f);
    const facts = p.computation ? [p.facts[p.facts.length - 1]]
      : p.facts.length > 3 && lines.size > 1 ? [...lines.values()].slice(0, 4)
      : p.facts.length > 3 ? [p.facts[0], p.facts[p.facts.length - 1]] : p.facts.slice(0, 3);
    for (const f of facts) {
      const unit = f.unit && f.unit !== "%" ? f.unit : "";
      figures.append(el("div", { class: "figure" },
        el("div", { class: "f-value" }, f.formatted.replace(/\s*%$/, ""), el("small", {}, f.unit === "%" || /%$/.test(f.formatted) ? "%" : unit)),
        el("div", { class: "f-label" }, prettyPeriods(f.label, L)),
        p.computation ? el("div", { class: "f-calc" }, `${t("calc", L)}: ${p.computation.expression}`) : null));
    }
  }
  if (figures.childElementCount) card.append(figures);

  for (const p of a.parts) {
    if (p.chart && p.chart.type !== "kpi" && p.chart.data.length > 1) card.append(chartBlock(p, L));
  }

  const meta = el("div", { class: "meta-row" });
  for (const s of a.sources) {
    meta.append(el("a", { class: "chip", href: s.url, target: "_blank", rel: "noopener", title: s.title },
      `${t("source", L)} `, el("b", {}, s.id)));
  }
  if (a.text_source === "model") meta.append(el("span", { class: "chip ok" }, t("checked", L)));
  if (a.total_ms) meta.append(el("span", { class: "chip" }, `${(a.total_ms / 1000).toFixed(1)} ${t("seconds", L)}`));
  if (meta.childElementCount) card.append(meta);

  if (a.parts.length) card.append(dataTable(a.parts, L));
  if (a.steps.length) card.append(stepsView(a, L));
  card.append(followups(a, L));
}

function followups(a, L) {
  const asked = new Set([a.question]);
  const topics = new Set(a.parts.map(p => (TOPIC_OF.find(([rx]) => rx.test(p.table.id)) || [])[1]).filter(Boolean));
  const idx = L === "is" ? 2 : 1;
  const same = POOL.filter(q => topics.has(q[0]) && !asked.has(q[idx]));
  const other = POOL.filter(q => !topics.has(q[0]));
  const picks = [...same.sort(() => Math.random() - 0.5).slice(0, 2), ...other.sort(() => Math.random() - 0.5).slice(0, 1)];
  return el("div", { class: "followups", "aria-label": t("more", L) },
    picks.map((q, i) => el("button", { class: "pill", type: "button", style: `animation-delay:${200 + i * 120}ms`, onclick: () => ask(q[idx]) }, q[idx])));
}

function dataTable(parts, L) {
  const d = el("details", { class: "disclose" }, el("summary", {}, t("data", L)));
  for (const p of parts) {
    const dims = Object.keys(p.rows[0]?.labels || {});
    d.append(el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, dims.map(x => el("th", {}, x)), el("th", {}, p.unit || "Value"))),
      el("tbody", {}, p.rows.map(r => el("tr", {},
        dims.map(x => el("td", {}, /^\d{4}([MQ]\d+)?$/.test(r.labels[x]) ? fmtPeriod(r.labels[x], L) : r.labels[x])),
        el("td", { class: "num" }, r.value == null ? "–" : localize(r.value.toLocaleString("en-US"), L))))))));
  }
  return d;
}

function stepsView(a, L) {
  const max = Math.max(...a.steps.map(s => s.ms), 1);
  return el("details", { class: "disclose" },
    el("summary", {}, `${t("how", L)} · ${(a.total_ms / 1000).toFixed(1)} ${t("seconds", L)} · ${a.llm_calls} ${t("calls", L)}`),
    el("ol", { class: "timeline" }, a.steps.map(s => el("li", {},
      el("span", { class: "s-name" }, s.name),
      el("span", { class: "s-bar" }, el("i", { style: `width:${Math.max(2, s.ms / max * 100)}%` })),
      el("span", { class: "s-ms" }, s.ms >= 1000 ? `${(s.ms / 1000).toFixed(1)} s` : `${s.ms} ms`),
      el("span", { class: "s-detail" }, prettyPeriods(s.detail, L))))),
    el("p", { class: "muted", style: "font-size:12px;margin:12px 0 0" }, `${a.model}`));
}

/* ---------- charts ---------- */
const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const IS_PERIOD = x => /^\d{4}([MQ]\d+)?$/.test(x);
const IS_TOTAL = x => /^(total|alls|samtals|all)\b/i.test(x.trim());
const VIEW_NAMES = {
  en: { line: "Line", stacked: "Stacked", heatmap: "Heatmap", bars: "Bars", lollipop: "Lollipop", donut: "Donut", waffle: "Waffle", slope: "Slope" },
  is: { line: "Lína", stacked: "Staflað", heatmap: "Hitakort", bars: "Súlur", lollipop: "Sleikjó", donut: "Kleinuhringur", waffle: "Vaffla", slope: "Halli" },
};

// Which views suit this answer, best first. The backend says line or bar; the shape of the data and the calculation decide the rest.
function viewsFor(c, comp) {
  const series = new Set(c.data.map(d => d.series).filter(Boolean));
  const multi = series.size > 1;
  const isTime = c.data.every(d => IS_PERIOD(d.x));
  const monthly = isTime && c.data.every(d => /M\d\d$/.test(d.x));
  const quarterly = isTime && c.data.every(d => /Q\d$/.test(d.x));
  if (comp?.op === "share" && c.data.length === 2) return ["donut", "waffle", "bars"];
  if (["difference", "pct_change"].includes(comp?.op) && c.data.length === 2) return ["slope", "bars"];
  if (c.type === "line") {
    if (multi) return ["line", "stacked", "bars"];
    if ((monthly && c.data.length >= 18) || (quarterly && c.data.length >= 8)) return ["line", "heatmap", "bars"];
    return ["line", "bars"];
  }
  if (isTime) return c.data.length === 2 ? ["slope", "bars"] : ["bars", "line"];
  const parts = c.data.filter(d => !IS_TOTAL(d.x));
  if (!multi && parts.length >= 2 && parts.length <= 7 && parts.every(d => d.y >= 0)) return ["bars", "donut", "lollipop"];
  return multi ? ["bars"] : ["bars", "lollipop"];
}

function chartBlock(p, L) {
  const views = viewsFor(p.chart, p.computation);
  const holder = el("div", { class: "chart" });
  const switcher = views.length > 1 ? el("div", { class: "segmented mini", role: "group", "aria-label": "Chart type" },
    views.map((v, i) => el("button", { type: "button", class: i ? "" : "on", onclick: e => {
      switcher.querySelectorAll("button").forEach(b => b.classList.toggle("on", b === e.currentTarget));
      drawChart(holder, p.chart, L, v, p.computation);
    } }, VIEW_NAMES[L === "is" ? "is" : "en"][v]))) : null;
  requestAnimationFrame(() => drawChart(holder, p.chart, L, views[0], p.computation));
  return el("div", { class: "chart-block" }, el("div", { class: "chart-head" }, el("p", { class: "chart-title" }, p.chart.title), switcher), holder);
}

function drawChart(holder, c, L, view = c.type === "line" ? "line" : "bars", comp = null) {
  holder._chart = [c, L, view, comp];
  holder.replaceChildren();
  delete holder.dataset.h;
  holder.classList.toggle("square", ["donut", "waffle"].includes(view));
  const data = c.data.map(d => ({ ...d, label: fmtPeriod(d.x, L) }));
  if (data.every(d => IS_PERIOD(d.x))) data.sort((a, b) => a.x.localeCompare(b.x));
  const series = [...new Set(data.map(d => d.series).filter(Boolean))];
  const multi = series.length > 1;
  const accent = cssVar("--series-1"), card = cssVar("--card"), ink = cssVar("--text"), muted = cssVar("--text-3");
  const palette = [cssVar("--series-1"), cssVar("--series-2"), cssVar("--series-3"), cssVar("--accent-2"), cssVar("--text-3"), cssVar("--text-2"), cssVar("--hairline")];
  const fmt = c.unit === "%" || data.some(d => !Number.isInteger(d.y)) ? ",.1f" : ",.0f";
  const unitSuffix = c.unit && c.unit !== "%" ? ` ${c.unit}` : c.unit === "%" ? "%" : "";
  const y = { field: "y", type: "quantitative", title: null, axis: { format: "~s", tickCount: 4 }, scale: { zero: view !== "line" && view !== "slope" } };
  const color = multi ? { field: "series", type: "nominal", title: null, scale: { domain: series, range: palette },
    legend: { orient: "top", direction: "horizontal", symbolType: "circle" } } : { value: accent };
  const tooltip = [{ field: "label", title: c.x_label || " " }, ...(multi ? [{ field: "series", title: " " }] : []),
    { field: "y", title: c.unit || " ", format: fmt }];
  const hover = { name: "hover", select: { type: "point", on: "pointerover", clear: "pointerout" } };
  let spec, width = "container", height = 260;

  if (view === "line" || view === "stacked") {
    const x = { field: "label", type: "ordinal", title: null, sort: null, axis: { labelAngle: 0, labelOverlap: "greedy", labelPadding: 8 } };
    const layers = [];
    if (view === "stacked") {
      layers.push({ mark: { type: "area", interpolate: "monotone", opacity: 0.85, line: { strokeWidth: 1.5, color: card } },
        encoding: { x, y: { ...y, stack: "zero", scale: { zero: true } }, color, tooltip } });
    } else {
      if (!multi) layers.push({ mark: { type: "area", interpolate: "monotone", line: false,
        color: { gradient: "linear", x1: 0, y1: 1, x2: 0, y2: 0, stops: [{ offset: 0, color: "rgba(0,0,0,0)" }, { offset: 1, color: accent + "55" }] } },
        encoding: { x, y } });
      layers.push({ mark: { type: "line", interpolate: "monotone", strokeWidth: 2.5, strokeCap: "round" }, encoding: { x, y, color } });
      layers.push({ params: [{ name: "near", select: { type: "point", fields: ["label"], nearest: true, on: "pointerover", clear: "pointerout" } }],
        mark: { type: "point", filled: true, size: 70, stroke: card, strokeWidth: 2 },
        encoding: { x, y, color, opacity: { condition: { param: "near", empty: false, value: 1 }, value: 0 }, tooltip } });
      layers.push({ transform: [{ filter: { param: "near", empty: false } }], mark: { type: "rule", color: muted, strokeDash: [3, 3] }, encoding: { x } });
      if (!multi) layers.push({ transform: [{ window: [{ op: "row_number", as: "i" }] }, { joinaggregate: [{ op: "max", field: "i", as: "n" }] },
        { filter: "datum.i === datum.n" }],
        mark: { type: "text", align: "right", dx: -4, dy: -12, fontWeight: 600, fontSize: 13, color: ink },
        encoding: { x, y, text: { field: "y", type: "quantitative", format: fmt } } });
    }
    spec = { layer: layers };

  } else if (view === "heatmap") {
    // one row per year, one column per month or quarter; the colour is the value
    const cells = data.map(d => {
      const m = /^(\d{4})(M(\d\d)|Q(\d))$/.exec(d.x);
      const col = m[3] ? MONTHS[L === "is" ? "is" : "en"][+m[3] - 1] : `Q${m[4]}`;
      return { ...d, year: m[1], col, ci: m[3] ? +m[3] : +m[4] };
    });
    const cols = [...new Map(cells.sort((a, b) => a.ci - b.ci).map(d => [d.col, d.ci])).keys()];
    const years = [...new Set(cells.map(d => d.year))].sort();
    height = Math.max(120, years.length * 34);
    const lo = Math.min(...cells.map(d => d.y)), hi = Math.max(...cells.map(d => d.y));
    spec = { data: { values: cells }, layer: [
      { params: [hover], mark: { type: "rect", cornerRadius: 5, stroke: card, strokeWidth: 3 },
        encoding: { x: { field: "col", type: "ordinal", sort: cols, title: null, axis: { orient: "top", labelAngle: 0, labelPadding: 6 } },
          y: { field: "year", type: "ordinal", sort: years, title: null, axis: { labelPadding: 8 } },
          color: { field: "y", type: "quantitative", title: null, scale: { range: [cssVar("--grid") === "" ? card : card, accent] },
            legend: { orient: "bottom", direction: "horizontal", gradientLength: 180, format: fmt } },
          tooltip: [{ field: "label", title: c.x_label || " " }, { field: "y", title: c.unit || " ", format: fmt }] } },
      ...(cells.length <= 72 ? [{ mark: { type: "text", fontSize: 11, fontWeight: 600 },
        encoding: { x: { field: "col", type: "ordinal", sort: cols }, y: { field: "year", type: "ordinal", sort: years },
          text: { field: "y", type: "quantitative", format: fmt },
          color: { condition: { test: `datum.y > ${lo + (hi - lo) * 0.55}`, value: cssVar("--accent-ink") }, value: ink } } }] : []),
    ] };

  } else if (view === "slope") {
    // before → after, with the change written on the line
    const [a, b] = data;
    const delta = b.y - a.y, pct = a.y ? delta / a.y * 100 : null;
    const sign = delta > 0 ? "+" : delta < 0 ? "−" : "";
    const deltaText = `${sign}${fmtNum(Math.abs(delta), c.unit, L)}${unitSuffix}` + (pct != null && c.unit !== "%" ? `  (${sign}${fmtNum(Math.abs(pct), "%", L)}%)` : "");
    height = 220;
    const x = { field: "label", type: "ordinal", sort: null, title: null, axis: { labelAngle: 0, labelFontSize: 13, labelFontWeight: 600, labelPadding: 10 }, scale: { padding: 0.6 } };
    const yy = { ...y, axis: null, scale: { zero: false, padding: 30 } };
    spec = { layer: [
      { mark: { type: "line", strokeWidth: 3, color: accent, strokeCap: "round" }, encoding: { x, y: yy } },
      { mark: { type: "point", filled: true, size: 160, color: accent, stroke: card, strokeWidth: 3 }, encoding: { x, y: yy, tooltip } },
      { mark: { type: "text", dy: -18, fontSize: 15, fontWeight: 700, color: ink }, encoding: { x, y: yy, text: { field: "y", type: "quantitative", format: fmt } } },
      { data: { values: [{ t: deltaText }] }, mark: { type: "text", fontSize: 13, fontWeight: 600, color: accent, baseline: "top" },
        encoding: { text: { field: "t" }, x: { value: "width" }, y: { value: 0 } } , transform: [] },
    ] };
    spec.layer[3].mark.align = "right";

  } else if (view === "donut" || view === "waffle") {
    // a share: the part against the rest; otherwise the categories against each other
    let slices;
    if (comp?.op === "share" && data.length === 2) {
      const whole = data.find(d => IS_TOTAL(d.x)) || data.reduce((m, d) => (d.y > m.y ? d : m));
      const part = data.find(d => d !== whole);
      slices = [{ label: part.label, y: part.y }, { label: L === "is" ? "Annað" : "Everything else", y: Math.max(0, whole.y - part.y) }];
    } else {
      slices = data.filter(d => !IS_TOTAL(d.x)).sort((a, b) => b.y - a.y).map(d => ({ label: d.label, y: d.y }));
    }
    const total = slices.reduce((t, d) => t + d.y, 0) || 1;
    slices.forEach(d => { d.pct = d.y / total * 100; });
    const colors = slices.length === 2 && comp?.op === "share" ? [accent, cssVar("--grid") || muted] : palette.slice(0, slices.length);
    const head = slices[0];
    if (view === "donut") {
      width = 300; height = 260;
      spec = { data: { values: slices }, layer: [
        { params: [hover], mark: { type: "arc", innerRadius: 78, outerRadius: 118, padAngle: 0.012, cornerRadius: 5 },
          encoding: { theta: { field: "y", type: "quantitative", stack: true }, order: { field: "y", sort: "descending" },
            color: { field: "label", type: "nominal", sort: null, title: null, scale: { domain: slices.map(d => d.label), range: colors },
              legend: { orient: "right", labelLimit: 160, symbolType: "circle" } },
            opacity: { condition: { param: "hover", value: 1 }, value: 0.88 },
            tooltip: [{ field: "label", title: " " }, { field: "y", title: c.unit || " ", format: fmt }, { field: "pct", title: "%", format: ".1f" }] } },
        { data: { values: [{ t: `${fmtNum(head.pct, "%", L)}%` }] }, mark: { type: "text", fontSize: 28, fontWeight: 700, color: ink, dy: -6 },
          encoding: { text: { field: "t" } } },
        { data: { values: [{ t: head.label.length > 22 ? head.label.slice(0, 21) + "…" : head.label }] },
          mark: { type: "text", fontSize: 12, color: muted, dy: 18 }, encoding: { text: { field: "t" } } },
      ] };
    } else {
      // 100 squares, filled in proportion
      const cells = [];
      let k = 0;
      slices.forEach((d, si) => { const n = si === slices.length - 1 ? 100 - k : Math.round(d.pct); for (let i = 0; i < n && k < 100; i++, k++) cells.push({ i: k, label: d.label }); });
      width = 260; height = 260;
      spec = { data: { values: cells }, transform: [{ calculate: "datum.i % 10", as: "col" }, { calculate: "floor(datum.i / 10)", as: "row" }],
        mark: { type: "rect", cornerRadius: 4, stroke: card, strokeWidth: 3 },
        encoding: { x: { field: "col", type: "ordinal", axis: null }, y: { field: "row", type: "ordinal", axis: null },
          color: { field: "label", type: "nominal", title: null, scale: { domain: slices.map(d => d.label), range: colors },
            legend: { orient: "right", labelLimit: 160, symbolType: "square" } },
          tooltip: [{ field: "label", title: " " }] } };
      width = 380;
    }

  } else {  // bars and lollipop
    const isTime = data.every(d => IS_PERIOD(d.x));
    const long = !isTime && (data.some(d => d.label.length > 12) || data.length > 8);
    const horizontal = long || view === "lollipop";
    const cat = { field: "label", type: "nominal", title: null, sort: multi || isTime ? null : { field: "y", order: "descending" }, axis: { labelLimit: 180, labelPadding: 8 } };
    const val = { ...y, axis: { format: "~s", tickCount: 4 } };
    const enc = horizontal ? { y: cat, x: val } : { x: { ...cat, axis: { labelAngle: 0 } }, y };
    if (horizontal) height = Math.max(140, data.length * (multi ? 44 : 32));
    if (view === "lollipop") {
      spec = { layer: [
        { transform: [{ calculate: "0", as: "zero" }], mark: { type: "rule", strokeWidth: 2.5, color: accent, opacity: 0.45 },
          encoding: { y: cat, x: { field: "zero", type: "quantitative" }, x2: { field: "y" } } },
        { mark: { type: "point", filled: true, size: 150, color: accent, stroke: card, strokeWidth: 2 }, encoding: { ...enc, tooltip } },
        { mark: { type: "text", align: "left", dx: 12, fontSize: 12, fontWeight: 600, color: ink }, encoding: { ...enc, text: { field: "y", type: "quantitative", format: fmt } } },
      ] };
    } else {
      const few = data.length <= 4 && !multi;
      const bar = { type: "bar", cornerRadiusEnd: 6, ...(horizontal ? { height: { band: 0.66 } } : few ? { width: 56 } : { width: { band: 0.62 } }) };
      if (few && !horizontal) height = 200;
      spec = { params: [hover], mark: bar,
        encoding: { ...enc, color, tooltip, opacity: { condition: { param: "hover", value: 1 }, value: 0.82 },
          ...(multi ? (horizontal ? { yOffset: { field: "series" } } : { xOffset: { field: "series" } }) : {}) } };
    }
  }

  vegaEmbed(holder, {
    $schema: "https://vega.github.io/schema/vega-lite/v5.json",
    width, height, autosize: { type: width === "container" ? "fit-x" : "pad", contains: "padding" },
    data: { values: data }, ...spec,
    config: {
      background: null, view: { stroke: null }, padding: { top: 12, right: 6, bottom: 4, left: 4 },
      font: "-apple-system, BlinkMacSystemFont, Inter, system-ui, sans-serif",
      axis: { labelColor: muted, titleColor: muted, gridColor: cssVar("--grid"), domain: false, ticks: false, labelFontSize: 12 },
      axisX: { grid: false }, axisY: { gridDash: [2, 3] },
      legend: { labelColor: cssVar("--text-2"), labelFontSize: 12, gradientThickness: 8 },
    },
  }, { actions: false, renderer: "svg", tooltip: { theme: "custom" } })
    .catch(e => holder.append(el("div", { class: "muted" }, `chart error: ${e.message}`)));
}

/* ---------- wiring ---------- */
async function health() {
  try {
    const h = await (await fetch("/api/health")).json();
    $("#status").textContent = `${h.model} · ${h.data_source} data · ${h.tables_in_catalog} tables`;
    $("#status").dataset.tables = h.tables_in_catalog.toLocaleString("en-US");
  } catch { $("#status").textContent = "backend offline"; }
}

$("#ask").addEventListener("submit", e => {
  e.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  $("#q").value = "";
  ask(q);
});
/* theme picker: palette and light/dark, remembered in this browser */
const themeBox = $("#theme");
function applyTheme(palette, mode) {
  const root = document.documentElement;
  if (palette === "warm") delete root.dataset.palette; else root.dataset.palette = palette;
  if (mode === "auto") delete root.dataset.theme; else root.dataset.theme = mode;
  themeBox.querySelectorAll(".swatch").forEach(b => b.classList.toggle("on", b.dataset.palette === palette));
  themeBox.querySelectorAll("[data-mode]").forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
  try { localStorage.setItem("ragstofan-theme", JSON.stringify({ palette, mode })); } catch {}
  requestAnimationFrame(() => document.querySelectorAll(".chart").forEach(h => h._chart && drawChart(h, ...h._chart)));
}
const currentTheme = () => [document.documentElement.dataset.palette || "warm", document.documentElement.dataset.theme || "auto"];
themeBox.querySelector(".theme-btn").addEventListener("click", e => {
  e.stopPropagation();
  const open = themeBox.classList.toggle("open");
  e.currentTarget.setAttribute("aria-expanded", open);
});
document.addEventListener("click", e => { if (!themeBox.contains(e.target)) themeBox.classList.remove("open"); });
document.addEventListener("keydown", e => { if (e.key === "Escape") themeBox.classList.remove("open"); });
themeBox.querySelectorAll(".swatch").forEach(b => b.addEventListener("click", () => applyTheme(b.dataset.palette, currentTheme()[1])));
themeBox.querySelectorAll("[data-mode]").forEach(b => b.addEventListener("click", () => applyTheme(currentTheme()[0], b.dataset.mode)));
applyTheme(...currentTheme());

document.querySelectorAll(".segmented [data-lang]").forEach(b => b.addEventListener("click", () => {
  lang = b.dataset.lang;
  document.querySelectorAll(".segmented [data-lang]").forEach(x => x.classList.toggle("on", x === b));
  applyLanguage();
}));
document.addEventListener("visibilitychange", () => { if (!$("#home").hidden) document.hidden ? pills.stop() : pills.start(); });

document.body.prepend(el("svg", { svg: 1, width: 0, height: 0, style: "position:absolute", "aria-hidden": "true" },
  el("defs", { svg: 1 }, el("linearGradient", { svg: 1, id: "spark-fill", x1: 0, y1: 0, x2: 0, y2: 1 },
    el("stop", { svg: 1, offset: "0%", style: "stop-color:var(--accent);stop-opacity:.22" }),
    el("stop", { svg: 1, offset: "100%", style: "stop-color:var(--accent);stop-opacity:0" })))));

health();
loadIndicators();
pills.start();
