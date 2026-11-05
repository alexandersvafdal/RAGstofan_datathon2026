// The static demo (GitHub Pages): the same app, answering from answers the real server gave and saved
// (scripts/record_demo.py -> demo/*.json). Each answer's progress is replayed at the speed it really ran.
// Loaded before app.js only in the Pages build; it stands in for the server by answering the app's /api calls.
(() => {
  // The demo's questions: the app's floating examples, and what it suggests after an unrecorded question.
  // Chosen to show the maps (the whole country, and regions the map zooms to), long line charts and time spans. scripts/record_demo.py asks exactly these.
  window.RAGSTOFAN_POOL = [
    ["population", "Show a map of the population by municipality", "Sýndu kort af íbúafjölda eftir sveitarfélögum"],
    ["population", "Show a map of the population of Kópavogur, Hafnarfjörður and Garðabær", "Sýndu á korti íbúafjölda Kópavogs, Hafnarfjarðar og Garðabæjar"],
    ["population", "Show on a map how many people live in Reykjanesbær, Suðurnesjabær, Grindavík and Vogar", "Sýndu á korti hve margir búa í Reykjanesbæ, Suðurnesjabæ, Grindavík og Vogum"],
    ["population", "Show a map of the population of Árborg, Hveragerði and Ölfus", "Sýndu á korti hve margir búa í Árborg, Hveragerði og Ölfusi"],
    ["population", "Show a map of the population of Akureyri, Dalvíkurbyggð and Fjallabyggð in 2026", "Sýndu kort af íbúafjölda Akureyrar, Dalvíkurbyggðar og Fjallabyggðar"],
    ["population", "Which ten municipalities have the most inhabitants?", "Hvaða tíu sveitarfélög eru fjölmennust?"],
    ["population", "How has Iceland's population grown since 1950?", "Hvernig hefur íbúafjöldi Íslands vaxið frá 1950?"],
    ["population", "How many children were born each year since 1950?", "Hve mörg börn fæddust á hverju ári frá 1950?"],
    ["population", "How has the number of immigrants changed since 1996?", "Hvernig hefur fjöldi innflytjenda breyst frá 1996?"],
    ["population", "How many people live in Kópavogur and Hafnarfjörður together?", "Hve margir búa í Kópavogi og Hafnarfirði samtals?"],
    ["prices", "How has inflation developed since 1990?", "Hvernig hefur verðbólgan þróast frá 1990?"],
    ["prices", "How have house prices developed since 2000?", "Hvernig hefur íbúðaverð þróast frá 2000?"],
    ["wages", "Compare wage growth and inflation since 2010", "Berðu saman launaþróun og verðbólgu frá 2010"],
    ["labour", "How has unemployment developed since 2005?", "Hvernig hefur atvinnuleysi þróast frá 2005?"],
    ["tourism", "How did overnight stays develop from 2010 to 2025?", "Hvernig þróuðust gistinætur frá 2010 til 2025?"],
    ["prices", "Show inflation as a heatmap by month and year since 2015", "Sýndu verðbólguna sem hitakort eftir mánuðum og árum frá 2015"],
    ["tourism", "How did foreign passengers through Keflavík airport develop since 2015?", "Hvernig hefur fjöldi erlendra farþega um Keflavíkurflugvöll þróast frá 2015?"],
    ["economy", "How has Iceland's economic growth developed since 1980?", "Hvernig hefur hagvöxtur þróast frá 1980?"],
    ["prices", "What is the annual inflation rate?", "Hver er verðbólgan?"],
  ];

  const realFetch = window.fetch.bind(window);
  const load = name => realFetch(`demo/${name}.json`).then(r => (r.ok ? r.json() : {})).catch(() => ({}));
  const data = Promise.all(["answers", "suggest", "indicators"].map(load))
    .then(([answers, suggest, indicators]) => ({ answers, suggest, indicators }));
  const key = (q, table, display) =>
    [String(q || "").trim().toLowerCase().replace(/\s+/g, " ").replace(/[?.!\s]+$/, ""), table || "", display || ""].join("|");
  const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
  const sleep = (ms, signal) => new Promise((res, rej) => {
    const id = setTimeout(res, ms);
    signal?.addEventListener("abort", () => { clearTimeout(id); rej(new DOMException("aborted", "AbortError")); });
  });

  // A question that was not recorded: say so, and offer the recorded ones (the app shows example questions under it)
  function notRecorded(q, language) {
    const is = language === "is" || (!language && /[þðæöáéíóúý]/i.test(q));
    return { question: q, language: is ? "is" : "en", status: "error", parts: [], sources: [], steps: [],
      text: is ? "Þetta er kyrrstæð kynningarútgáfa: hún sýnir aðeins vistuð svör við tilbúnum spurningum. Prófaðu eina af spurningunum hér fyrir neðan, eða keyrðu RAGstofu sjálf(ur) til að spyrja hvers sem er."
               : "This is a static demo: it replays saved answers to the premade questions only. Try one of the questions below, or run RAGstofan yourself to ask anything.",
      text_source: "none", verdict: "unchecked", verdict_reason: "", llm_calls: 0, total_ms: 0, model: "" };
  }

  function stream(lines, signal) {
    const enc = new TextEncoder();
    return new ReadableStream({
      async start(ctrl) {
        let last = 0;
        try {
          for (const { at, ...msg } of lines) {
            await sleep(Math.max(0, (at ?? last) - last), signal);
            last = at ?? last;
            ctrl.enqueue(enc.encode(JSON.stringify(msg) + "\n"));
          }
          ctrl.close();
        } catch (e) { ctrl.error(e); }
      },
    });
  }

  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    const path = url.pathname.replace(/^.*?(\/api\/)/, "/api/");
    if (!path.startsWith("/api/")) return realFetch(input, init);
    const d = await data;
    const body = init.body ? JSON.parse(init.body) : {};
    switch (path) {
      case "/api/health":
        return json({ model: "qwen3.8:27b", data_source: "saved demo", tables_in_catalog: 1857 });
      case "/api/models":
        return json({ default: "qwen3.8:27b", models: ["qwen3.8:27b"], fast: "", sizes: { "qwen3.8:27b": "large" } });
      case "/api/indicators":
        return json(d.indicators);
      case "/api/suggest":
        return json(d.suggest[key(body.question, null, null)] || d.suggest[key(body.question, (body.sources || [])[0]?.id, null)] || { questions: [] });
      case "/api/voices":
        return json({ voices: [] });
      case "/api/ask/stream": {
        const rec = d.answers[key(body.question, body.table_id, body.display)];
        const lines = rec ? rec.lines : [{ at: 600, type: "answer", answer: notRecorded(body.question, body.language) }];
        return new Response(stream(lines, init.signal), { headers: { "Content-Type": "application/x-ndjson" } });
      }
      default:
        return json({ detail: "Not available in the static demo" }, 503);
    }
  };
  window.RAGSTOFAN_DEMO = true;

  // Light mode on a first visit; a mode the visitor picks later is kept (app.js stores it)
  try {
    if (!localStorage.getItem("ragstofan-demo-light")) {
      const t = JSON.parse(localStorage.getItem("ragstofan-theme") || "{}");
      localStorage.setItem("ragstofan-theme", JSON.stringify({ palette: t.palette || "sterling", mode: "light" }));
      localStorage.setItem("ragstofan-demo-light", "1");
    }
    const mode = JSON.parse(localStorage.getItem("ragstofan-theme") || "{}").mode;
    if (mode && mode !== "auto") document.documentElement.dataset.theme = mode;
  } catch { document.documentElement.dataset.theme = "light"; }

  // What this page is, and that an AI answers: under the greeting, and under the question box while chatting.
  // In the page's language, which app.js sets on <html lang>.
  const REPO = "https://github.com/alexandersvafdal/RAGstofan_datathon2026";
  const WORDS = {
    en: { demo: `Static demo: real answers saved from the server, for the example questions. <a href="${REPO}">Run it yourself</a> to ask anything.`,
          ai: "Answers are chosen by an AI and can be wrong. Check the figure against the linked Statistics Iceland table.",
          tag: "Static demo", banner: "No live AI here: this page replays answers saved from the real server, for the example questions only.",
          run: "Run it yourself", close: "Hide this notice" },
    is: { demo: `Kynningarútgáfa: raunveruleg svör vistuð af þjóninum, við tilbúnu spurningunum. <a href="${REPO}">Keyrðu hana sjálf(ur)</a> til að spyrja hvers sem er.`,
          ai: "Gervigreind velur svörin og þau geta verið röng. Berðu töluna saman við töflu Hagstofunnar sem vísað er í.",
          tag: "Kynningarútgáfa", banner: "Engin gervigreind í gangi hér: síðan spilar svör sem voru vistuð af raunverulega þjóninum, aðeins við tilbúnu spurningunum.",
          run: "Keyra sjálf(ur)", close: "Fela þessa tilkynningu" },
  };
  document.addEventListener("DOMContentLoaded", () => {
    const style = document.createElement("style");
    style.textContent = `.demo-note { font-size: 14px; color: var(--text-2); margin-top: 10px; line-height: 1.45; }
      .demo-note a { color: var(--accent); }
      .demo-note .ai { display: block; margin-top: 4px; font-weight: 600; }
      .ai-disclaimer { text-align: center; font-size: 12px; color: var(--text-3); margin: 6px 12px 0; line-height: 1.35; }
      .composer.inline .ai-disclaimer { display: none; }
      .demo-top { position: sticky; top: 0; z-index: 10; }
      .demo-top .nav { position: static; }
      .demo-banner { display: flex; align-items: center; justify-content: center; gap: 10px 14px; flex-wrap: wrap;
        padding: 9px 16px; background: #b89660; color: #14110b; font-size: 14px; line-height: 1.35; text-align: center; }
      .demo-banner .tag { font-weight: 800; font-size: 12px; letter-spacing: .1em; text-transform: uppercase;
        border: 1.5px solid currentColor; border-radius: 999px; padding: 3px 10px; white-space: nowrap; }
      .demo-banner a { color: inherit; font-weight: 700; white-space: nowrap; text-underline-offset: 3px; }
      .demo-banner { position: relative; padding-right: 44px; padding-left: 44px; }
      .demo-banner .close { position: absolute; right: 8px; top: 50%; transform: translateY(-50%); width: 30px; height: 30px;
        border: 0; border-radius: 999px; background: transparent; color: inherit; font-size: 16px; line-height: 1; cursor: pointer; }
      .demo-banner .close:hover { background: rgba(20,17,11,.12); }
      @media (max-width: 600px) { .demo-banner { font-size: 13px; padding: 8px 40px; } }`;
    document.head.append(style);
    const note = document.createElement("p");
    note.className = "demo-note";
    const foot = document.createElement("p");
    foot.className = "ai-disclaimer";
    // a bar above the navigation, staying on screen while scrolling
    const banner = document.createElement("div");
    banner.className = "demo-banner";
    banner.setAttribute("role", "note");
    const nav = document.querySelector("header.nav");
    if (nav) {
      const top = document.createElement("div");
      top.className = "demo-top";
      nav.before(top);
      let closed = false;
      try { closed = sessionStorage.getItem("ragstofan-demo-banner") === "closed"; } catch {}
      top.append(...(closed ? [] : [banner]), nav);
    }
    const render = () => {
      const w = WORDS[document.documentElement.lang === "is" ? "is" : "en"];
      banner.innerHTML = `<span class="tag">${w.tag}</span><span>${w.banner}</span><a href="${REPO}">${w.run} ↗</a>` +
        `<button type="button" class="close" aria-label="${w.close}">✕</button>`;
      banner.querySelector(".close").onclick = () => {   // closed for this visit; it is back on the next one
        banner.remove();
        try { sessionStorage.setItem("ragstofan-demo-banner", "closed"); } catch {}
      };
      note.innerHTML = `<span class="ai">⚠ ${w.ai}</span>`;   // the banner says it is a demo
      foot.textContent = w.ai;
    };
    render();
    new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
    document.querySelector(".hero .lede")?.after(note);
    document.querySelector("#ask .composer-inner")?.after(foot);
    document.getElementById("mic")?.style.setProperty("display", "none");   // voice needs the server
  });
})();
