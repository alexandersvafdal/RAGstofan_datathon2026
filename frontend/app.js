// Renders the backend's Answer object (hagbot/schemas.py) and the dashboard (/api/indicators). No build step.

const I18N = {
  en: {
    eyebrow: "Statistics Iceland · Gagnaþon 2026", hello: "Iceland, in numbers.",
    sub: "Ask anything about Iceland's official statistics. Every answer comes with the figure, its unit and the table it came from.",
    today: "The latest numbers", asof: "Data as of", placeholder: "Ask about Icelandic statistics",
    steps: ["Understanding the question", "Finding the right table", "Choosing the categories", "Reading the numbers", "Writing the answer"],
    working: "Working on it", how: "How I got this", data: "Data", source: "Source", checked: "Numbers checked against the table",
    calc: "Calculated", failed: "Something went wrong", retry: "Try again", seconds: "s", calls: "model calls",
    pts: "pts", more: "Ask next",
  },
  is: {
    eyebrow: "Hagstofa Íslands · Gagnaþon 2026", hello: "Ísland í tölum.",
    sub: "Spurðu um opinbera tölfræði Íslands. Hverju svari fylgir talan, eining hennar og taflan sem hún kemur úr.",
    today: "Nýjustu tölur", asof: "Gögn frá", placeholder: "Spyrðu um íslenska tölfræði",
    steps: ["Skil spurninguna", "Finn réttu töfluna", "Vel flokkana", "Les tölurnar", "Skrifa svarið"],
    working: "Augnablik", how: "Hvernig svarið varð til", data: "Gögn", source: "Heimild", checked: "Tölur bornar saman við töfluna",
    calc: "Reiknað", failed: "Eitthvað fór úrskeiðis", retry: "Reyna aftur", seconds: "s", calls: "köll í líkan",
    pts: "pst.", more: "Spyrðu næst",
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

function sparkline(series) {
  const W = 200, H = 54, pad = 4;
  const ys = series.map(p => p.y), lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const pts = series.map((p, i) => [pad + (i / Math.max(1, series.length - 1)) * (W - 2 * pad), H - pad - ((p.y - lo) / span) * (H - 2 * pad)]);
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
  const [ex, ey] = pts[pts.length - 1];
  return el("svg", { svg: 1, class: "spark", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", "aria-hidden": "true" },
    el("path", { svg: 1, class: "area", d: `${d}L${ex},${H}L${pts[0][0]},${H}Z` }),
    el("path", { svg: 1, class: "line", d, "vector-effect": "non-scaling-stroke" }),
    el("circle", { svg: 1, class: "end", cx: ex, cy: ey, r: 3.5, "vector-effect": "non-scaling-stroke" }));
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
      sparkline(ind.series),
      el("span", { class: "t-src" }, `${ind.table.id} · ${unit === "%" ? "" : unit}`.replace(/ · $/, "")));
    return tile;
  }));
}

/* ---------- conversation ---------- */
function showChat() {
  const home = $("#home");
  if (home.hidden) return;
  pills.stop();
  home.classList.add("leaving");
  setTimeout(() => { home.hidden = true; $("#chat").classList.add("on"); }, reduceMotion ? 0 : 350);
}

function progressView(L) {
  const steps = t("steps", L);
  const items = steps.map(s => el("li", {}, s));
  const time = el("span", { class: "p-time" }, "0 s");
  const view = el("div", { class: "progress" }, el("div", { class: "p-head" }, el("span", {}, `${t("working", L)}…`), time), el("ol", {}, items));
  const marks = [0, 7, 17, 34, 36];  // rough seconds at which each step starts on a laptop
  const t0 = performance.now();
  const tick = () => {
    const s = (performance.now() - t0) / 1000;
    time.textContent = `${Math.floor(s)} s`;
    const now = marks.filter(m => s >= m).length - 1;
    items.forEach((li, i) => { li.className = i < now ? "done" : i === now ? "now" : ""; });
  };
  tick();
  view._timer = setInterval(tick, 250);
  return view;
}

async function ask(question, tableId = null) {
  showChat();
  const chat = $("#chat");
  const guess = lang === "auto" ? (/[þðæöáéíóúý]/i.test(question) ? "is" : "en") : lang;
  if (!tableId) chat.append(el("div", { class: "msg user" }, el("div", { class: "bubble" }, question)));
  const card = el("div", { class: "card" });
  const progress = progressView(guess);
  card.append(progress);
  const bot = el("div", { class: "msg bot" }, card);
  chat.append(bot);
  setTimeout(() => bot.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "end" }), 380);
  $("#send").disabled = true;
  try {
    const r = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, language: lang === "auto" ? null : lang, table_id: tableId }),
    });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    renderAnswer(card, await r.json());
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
    const facts = p.computation ? [p.facts[p.facts.length - 1]]
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
    if (p.chart && p.chart.type !== "kpi" && p.chart.data.length > 1) {
      const holder = el("div", { class: "chart" });
      card.append(el("p", { class: "chart-title" }, p.chart.title), holder);
      drawChart(holder, p.chart, L);
    }
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

function drawChart(holder, c, L) {
  const data = c.data.map(d => ({ ...d, label: fmtPeriod(d.x, L) }));
  const series = [...new Set(data.map(d => d.series).filter(Boolean))];
  const multi = series.length > 1;
  const accent = cssVar("--series-1");
  const palette = [cssVar("--series-1"), cssVar("--series-2"), cssVar("--series-3")];
  const fmt = c.unit === "%" || data.some(d => !Number.isInteger(d.y)) ? ",.1f" : ",.0f";
  const y = { field: "y", type: "quantitative", title: null, axis: { format: "~s", tickCount: 4 }, scale: { zero: c.type === "bar" } };
  const color = multi ? { field: "series", type: "nominal", title: null, scale: { domain: series, range: palette },
    legend: { orient: "top", direction: "horizontal", symbolType: "circle" } } : { value: accent };
  const tooltip = [{ field: "label", title: c.x_label || " " }, ...(multi ? [{ field: "series", title: " " }] : []),
    { field: "y", title: c.unit || " ", format: fmt }];
  let spec;

  if (c.type === "line") {
    const x = { field: "label", type: "ordinal", title: null, sort: null, axis: { labelAngle: 0, labelOverlap: "greedy", labelPadding: 8 } };
    const layers = [];
    if (!multi) layers.push({ mark: { type: "area", interpolate: "monotone", line: false,
      color: { gradient: "linear", x1: 0, y1: 1, x2: 0, y2: 0, stops: [{ offset: 0, color: "rgba(0,0,0,0)" }, { offset: 1, color: accent + "55" }] } },
      encoding: { x, y } });
    layers.push({ mark: { type: "line", interpolate: "monotone", strokeWidth: 2.5, strokeCap: "round" }, encoding: { x, y, color } });
    layers.push({ params: [{ name: "hover", select: { type: "point", fields: ["label"], nearest: true, on: "pointerover", clear: "pointerout" } }],
      mark: { type: "point", filled: true, size: 70, stroke: cssVar("--card"), strokeWidth: 2 },
      encoding: { x, y, color, opacity: { condition: { param: "hover", empty: false, value: 1 }, value: 0 }, tooltip } });
    layers.push({ transform: [{ filter: { param: "hover", empty: false } }], mark: { type: "rule", color: cssVar("--text-3"), strokeDash: [3, 3] },
      encoding: { x } });
    if (!multi) layers.push({ transform: [{ window: [{ op: "row_number", as: "i" }] }, { joinaggregate: [{ op: "max", field: "i", as: "n" }] },
      { filter: "datum.i === datum.n" }],
      mark: { type: "text", align: "right", dx: -4, dy: -12, fontWeight: 600, fontSize: 13, color: cssVar("--text") },
      encoding: { x, y, text: { field: "y", type: "quantitative", format: fmt } } });
    spec = { layer: layers };
  } else {
    const isTime = data.every(d => /^\d{4}([MQ]\d+)?$/.test(d.x));
    if (isTime) data.sort((a, b) => a.x.localeCompare(b.x));
    const long = !isTime && (data.some(d => d.label.length > 12) || data.length > 8);
    const cat = { field: "label", type: "nominal", title: null, sort: multi || isTime ? null : long ? "-x" : "-y", axis: { labelLimit: 180, labelPadding: 8 } };
    const few = data.length <= 4 && !multi;
    const bar = { type: "bar", cornerRadiusEnd: 6, ...(long ? { height: { band: 0.66 } } : few ? { width: 56 } : { width: { band: 0.62 } }) };
    if (few && !long) holder.dataset.h = 200;
    const enc = long ? { y: cat, x: { ...y, axis: { format: "~s", tickCount: 4 } } } : { x: { ...cat, axis: { labelAngle: 0 } }, y };
    spec = { params: [{ name: "hover", select: { type: "point", on: "pointerover", clear: "pointerout" } }], mark: bar,
      encoding: { ...enc, color, tooltip, opacity: { condition: { param: "hover", value: 1 }, value: 0.82 },
        ...(multi ? (long ? { yOffset: { field: "series" } } : { xOffset: { field: "series" } }) : {}) } };
    if (long) holder.dataset.h = Math.max(140, data.length * (multi ? 44 : 30));
  }

  const ink = cssVar("--text-3");
  vegaEmbed(holder, {
    $schema: "https://vega.github.io/schema/vega-lite/v5.json",
    width: "container", height: +(holder.dataset.h || 260), autosize: { type: "fit-x", contains: "padding" },
    data: { values: data }, ...spec,
    config: {
      background: null, view: { stroke: null }, padding: { top: 12, right: 6, bottom: 4, left: 4 },
      font: "-apple-system, BlinkMacSystemFont, Inter, system-ui, sans-serif",
      axis: { labelColor: ink, titleColor: ink, gridColor: cssVar("--grid"), domain: false, ticks: false, labelFontSize: 12 },
      axisX: { grid: false }, axisY: { gridDash: [2, 3] }, legend: { labelColor: cssVar("--text-2"), labelFontSize: 12 },
    },
  }, { actions: false, renderer: "svg", tooltip: { theme: "custom" } })
    .catch(e => holder.append(el("div", { class: "muted" }, `chart error: ${e.message}`)));
}

/* ---------- wiring ---------- */
async function health() {
  try {
    const h = await (await fetch("/api/health")).json();
    $("#status").textContent = `${h.model} · ${h.data_source} data · ${h.tables_in_catalog} tables`;
  } catch { $("#status").textContent = "backend offline"; }
}

$("#ask").addEventListener("submit", e => {
  e.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  $("#q").value = "";
  ask(q);
});
document.querySelectorAll(".segmented button").forEach(b => b.addEventListener("click", () => {
  lang = b.dataset.lang;
  document.querySelectorAll(".segmented button").forEach(x => x.classList.toggle("on", x === b));
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
