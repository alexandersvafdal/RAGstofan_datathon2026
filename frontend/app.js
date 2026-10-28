// Renders the backend's Answer object (hagbot/schemas.py). No build step.

const PALETTE = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
};
const I18N = {
  en: { thinking: "Finding the right table…", table: "Data table", how: "How I got this", source: "Source",
        placeholder: "Ask about Icelandic statistics…", calc: "Calculated", failed: "Request failed" },
  is: { thinking: "Leita að réttu töflunni…", table: "Gagnatafla", how: "Hvernig svarið varð til", source: "Heimild",
        placeholder: "Spyrðu um íslenska tölfræði…", calc: "Reiknað", failed: "Beiðni mistókst" },
};

let lang = "auto";
const $ = (s, el = document) => el.querySelector(s);
const chat = $("#chat");
const t = (k, l) => (I18N[l] || I18N.en)[k];

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) e.append(c);
  return e;
}

async function health() {
  try {
    const h = await (await fetch("/api/health")).json();
    $("#status").textContent = `${h.model} · ${h.data_source} · ${h.tables_in_catalog} tables`;
  } catch { $("#status").textContent = "backend offline"; }
}

async function ask(question, tableId = null) {
  $("#empty")?.remove();
  if (!tableId) chat.append(el("div", { class: "msg user" }, el("div", { class: "bubble" }, question)));
  const bot = $("#tpl-bot").content.firstElementChild.cloneNode(true);
  const guess = lang === "auto" ? (/(^|\s)[a-zþðæö]*[þðæöáéíóúý]/.test(question) ? "is" : "en") : lang;
  $(".loading-text", bot).textContent = t("thinking", guess);
  chat.append(bot);
  bot.scrollIntoView({ behavior: "smooth", block: "end" });
  $("#send").disabled = true;
  try {
    const r = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, language: lang === "auto" ? null : lang, table_id: tableId }),
    });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    renderAnswer($(".card", bot), await r.json());
  } catch (e) {
    $(".card", bot).replaceChildren(el("p", { class: "answer-text error" }, `${t("failed", guess)}: ${e.message}`));
  } finally {
    $("#send").disabled = false;
    bot.scrollIntoView({ behavior: "smooth", block: "end" });
  }
}

function renderAnswer(card, a) {
  const L = a.language;
  card.replaceChildren();
  card.append(el("p", { class: "answer-text" + (a.status === "error" ? " error" : "") }, a.text));

  if (a.status === "clarify" && a.clarification) {
    card.append(el("div", { class: "clarify" },
      el("div", {}, a.clarification.message),
      el("div", { class: "opts" }, a.clarification.options.map(o =>
        el("button", { onclick: () => ask(a.question, o.table_id) }, `${o.label} `, el("span", { class: "muted" }, o.table_id))))));
  }

  for (const p of a.parts) {
    card.append(kpis(p, L));
    if (p.chart && p.chart.type !== "kpi") {
      const holder = el("div", { class: "chart" });
      card.append(holder);
      drawChart(holder, p.chart);
    }
  }

  if (a.sources.length) {
    card.append(el("div", { class: "sources" }, a.sources.map(s =>
      el("a", { class: "source", href: s.url, target: "_blank", rel: "noopener" },
        `${t("source", L)}: `, el("b", {}, s.id), ` · ${s.title}`))));
  }
  if (a.parts.length) card.append(dataTable(a.parts, L));
  card.append(stepsView(a, L));
}

function kpis(p, L) {
  const box = el("div", { class: "kpis" });
  if (p.computation) {
    const f = p.facts[p.facts.length - 1];
    box.append(el("div", { class: "kpi" },
      el("div", { class: "v" }, f.formatted + (f.unit && f.unit !== "%" ? ` ${f.unit}` : "")),
      el("div", { class: "l" }, f.label),
      el("div", { class: "x" }, `${t("calc", L)}: ${p.computation.expression}`)));
  } else if (p.facts.length <= 3) {
    for (const f of p.facts) {
      box.append(el("div", { class: "kpi" },
        el("div", { class: "v" }, f.formatted + (f.unit && f.unit !== "%" ? ` ${f.unit}` : "")),
        el("div", { class: "l" }, f.label)));
    }
  }
  return box;
}

function dataTable(parts, L) {
  const d = el("details", {}, el("summary", {}, t("table", L)));
  for (const p of parts) {
    const dims = Object.keys(p.rows[0]?.labels || {});
    d.append(el("div", { class: "muted" }, `${p.table.id} · ${p.table.title}`),
      el("table", {},
        el("thead", {}, el("tr", {}, dims.map(x => el("th", {}, x)), el("th", {}, p.unit || "value"))),
        el("tbody", {}, p.rows.map(r => el("tr", {},
          dims.map(x => el("td", {}, r.labels[x])),
          el("td", { class: "num" }, r.value == null ? "…" : r.value.toLocaleString(L === "is" ? "is-IS" : "en-GB")))))));
  }
  return d;
}

function stepsView(a, L) {
  return el("details", {}, el("summary", {},
      `${t("how", L)} · ${(a.total_ms / 1000).toFixed(1)} s · ${a.llm_calls} model calls · ${a.model}`),
    el("ul", { class: "steps" }, a.steps.map(s =>
      el("li", {}, el("b", {}, s.name), ` ${s.ms} ms `, el("code", {}, s.detail)))),
    el("div", { class: "muted" }, `text: ${a.text_source}`));
}

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function drawChart(holder, c) {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const series = [...new Set(c.data.map(d => d.series).filter(Boolean))];
  const hasSeries = series.length > 1;
  const color = hasSeries
    ? { field: "series", type: "nominal", title: null,
        scale: { domain: series, range: PALETTE[dark ? "dark" : "light"].slice(0, series.length) },
        legend: { orient: "top", direction: "horizontal" } }
    : { value: cssVar("--series-1") };
  const y = { field: "y", type: "quantitative", title: c.unit || null, axis: { format: "~s", tickCount: 5 } };
  const tooltip = [
    { field: "x", title: c.x_label || " " },
    ...(hasSeries ? [{ field: "series", title: " " }] : []),
    { field: "y", title: c.unit || "value", format: "," },
  ];
  const longLabels = c.type === "bar" && c.data.some(d => d.x.length > 12);

  let spec;
  if (c.type === "line") {
    spec = {
      mark: { type: "line", strokeWidth: 2, point: { filled: true, size: 64 } },
      encoding: { x: { field: "x", type: "ordinal", title: null, sort: null, axis: { labelAngle: 0, labelOverlap: true } },
                  y, color, tooltip },
    };
  } else if (longLabels) { // horizontal bars keep category names readable
    spec = {
      mark: { type: "bar", cornerRadiusEnd: 4, height: { band: 0.7 } },
      encoding: { y: { field: "x", type: "nominal", title: null, sort: null },
                  x: { ...y, axis: { format: "~s", tickCount: 5 } }, color, tooltip,
                  ...(hasSeries ? { yOffset: { field: "series" } } : {}) },
    };
  } else {
    spec = {
      mark: { type: "bar", cornerRadiusEnd: 4, width: { band: 0.7 } },
      encoding: { x: { field: "x", type: "nominal", title: null, sort: null, axis: { labelAngle: 0 } },
                  y, color, tooltip, ...(hasSeries ? { xOffset: { field: "series" } } : {}) },
    };
  }
  const ink = cssVar("--text-secondary");
  vegaEmbed(holder, {
    $schema: "https://vega.github.io/schema/vega-lite/v5.json",
    width: "container", height: longLabels ? Math.max(120, c.data.length * 28) : 240,
    data: { values: c.data }, ...spec,
    config: {
      background: null, view: { stroke: null },
      font: "system-ui, -apple-system, Segoe UI, sans-serif",
      axis: { labelColor: ink, titleColor: ink, gridColor: cssVar("--grid"), domain: false, tickColor: cssVar("--grid"), labelFontSize: 12 },
      axisX: { grid: false }, legend: { labelColor: ink, labelFontSize: 12 },
      bar: { stroke: cssVar("--surface-1"), strokeWidth: hasSeries ? 2 : 0 },
    },
  }, { actions: false, renderer: "svg" }).catch(e => holder.append(el("div", { class: "muted" }, `chart error: ${e.message}`)));
}

$("#ask").addEventListener("submit", e => {
  e.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  $("#q").value = "";
  ask(q);
});
document.querySelectorAll(".example").forEach(b => b.addEventListener("click", () => ask(b.textContent)));
document.querySelectorAll(".lang button").forEach(b => b.addEventListener("click", () => {
  lang = b.dataset.lang;
  document.querySelectorAll(".lang button").forEach(x => x.classList.toggle("on", x === b));
  $("#q").placeholder = t("placeholder", lang === "is" ? "is" : "en");
  document.documentElement.lang = lang === "is" ? "is" : "en";
}));
health();
