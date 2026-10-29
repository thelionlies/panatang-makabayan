(function () {
  const PLEDGE = `Iniibig ko ang Pilipinas,
aking lupang sinilangan
tahanan ng aking lahi;
kinukupkop ako at tinutulungang
maging malakas, masipag at marangal.
Dahil mahal ko ang Pilipinas,
diringgin ko ang payo
ng aking mga magulang,
susundin ko ang tuntunin ng paaralan,
tutuparin ko ang tungkulin
ng mamamayang makabayan;
naglilingkod, nag-aaral, at nananalangin
nang buong katapatan.
Iaalay ko ang aking buhay,
pangarap, pagsisikap
sa bansang Pilipinas.`;

  // Same list as MODELS in generate.py.
  const MODELS = [
    { id: "openai/gpt-6-luna", label: "GPT-6 Luna", maker: "OpenAI", origin: "USA" },
    { id: "anthropic/claude-haiku-5.5", label: "Claude Haiku 5.5", maker: "Anthropic", origin: "USA" },
    { id: "google/gemini-3.1-flash-lite", label: "Gemini 3.1 Flash Lite", maker: "Google", origin: "USA" },
    { id: "deepseek/deepseek-v4.1-flash", label: "DeepSeek V4.1 Flash", maker: "DeepSeek", origin: "China" },
  ];

  // Written by generate.py as data/autocomplete.js and data/roles.js; absent until it has been run.
  const data = window.AUTOCOMPLETE || { models: [] };
  const voicesData = window.ROLES || { roles: [] };
  const completionsById = Object.fromEntries(data.models.map((m) => [m.id, m.completions]));

  // Two tabs share one page: Inference (machines finish the pledge from a hovered word) and
  // Voices (the whole pledge rewritten as someone else would say it). Each keeps its own choice.
  const MODES = {
    inference: MODELS.map((m) => ({ ...m, kind: "machine", sub: `${m.maker} · ${m.origin}` })),
    voices: voicesData.roles.map((r) => ({ ...r, kind: "voice", sub: r.gloss })),
  };
  const chosen = { inference: MODES.inference[0], voices: MODES.voices[0] };

  const pledge = data.pledge || PLEDGE;
  const words = [...pledge.matchAll(/\S+/g)].map((m) => ({ text: m[0], start: m.index, end: m.index + m[0].length }));
  let mode = "inference";
  let choice = chosen.inference;
  let voiceText = ""; // the rewrite picked when a voice was chosen
  let active = null; // hovered word index
  let leaveTimer = null;
  const random = (list) => list[Math.floor(Math.random() * list.length)];

  // ---- Selector (custom dropdown; the native <select> popup can't be styled) ----
  const pickerBtn = document.getElementById("model-button");
  const pickerLabel = pickerBtn.querySelector(".picker-label");
  const pickerList = document.getElementById("model-list");
  const hintEl = document.querySelector(".hint");
  const machineHint = hintEl.textContent;

  let optionBtns = [];
  function buildPicker(items) {
    pickerList.innerHTML = "";
    optionBtns = items.map((item) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "option");
      b.innerHTML = `<span>${item.label}</span><small>${item.sub}</small>`;
      b.addEventListener("click", () => {
        choose(item);
        closePicker(false);
      });
      li.appendChild(b);
      pickerList.appendChild(li);
      return b;
    });
  }

  function choose(item, rerender = true) {
    choice = chosen[mode] = item;
    pickerLabel.textContent = item.label;
    optionBtns.forEach((b, i) => b.setAttribute("aria-selected", MODES[mode][i] === item));
    hintEl.textContent =
      item.kind === "voice" ? `${item.gloss}, as imagined by ${voicesData.modelLabel}.` : machineHint;
    if (item.kind === "voice") voiceText = random(item.rewrites);
    hulaEl.classList.toggle("is-voice", item.kind === "voice");
    active = null;
    if (rerender) renderHula(null);
  }

  function setMode(next) {
    if (!MODES[next].length) next = "inference";
    mode = next;
    buildPicker(MODES[mode]);
    choose(chosen[mode]);
  }

  function openPicker() {
    pickerList.hidden = false;
    pickerBtn.setAttribute("aria-expanded", "true");
    optionBtns[MODES[mode].indexOf(choice)].focus();
  }
  function closePicker(refocus) {
    pickerList.hidden = true;
    pickerBtn.setAttribute("aria-expanded", "false");
    if (refocus) pickerBtn.focus();
  }

  pickerBtn.addEventListener("click", () => (pickerList.hidden ? openPicker() : closePicker(false)));
  document.addEventListener("click", (e) => {
    if (!pickerList.hidden && !e.target.closest(".picker")) closePicker(false);
  });
  pickerList.addEventListener("keydown", (e) => {
    const i = optionBtns.indexOf(document.activeElement);
    if (e.key === "Escape") closePicker(true);
    else if (e.key === "ArrowDown") { e.preventDefault(); optionBtns[(i + 1) % optionBtns.length].focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); optionBtns[(i - 1 + optionBtns.length) % optionBtns.length].focus(); }
  });

  document.getElementById("model-table").innerHTML = MODELS.map(
    (m) => `<tr><td>${m.label}</td><td>${m.maker}</td><td>${m.origin}</td></tr>`
  ).join("");

  // ---- Playground ----
  // Two stacked layers: an invisible copy of the original pledge catches the mouse,
  // so hovering always maps to original word positions, even over replaced text.
  const hulaEl = document.getElementById("hula-text");
  const ghostEl = document.createElement("div");
  const viewEl = document.createElement("div");
  ghostEl.className = "ghost";
  viewEl.className = "view";
  viewEl.setAttribute("aria-hidden", "true");
  hulaEl.append(ghostEl, viewEl);

  function fillWords(el, upTo, activeIdx) {
    el.innerHTML = "";
    let cursor = 0;
    for (let i = 0; i <= upTo; i++) {
      const w = words[i];
      el.append(pledge.slice(cursor, w.start));
      const span = document.createElement("span");
      span.className = "w" + (i === activeIdx ? " active" : "");
      span.dataset.i = i;
      span.textContent = w.text;
      el.appendChild(span);
      cursor = w.end;
    }
    return cursor;
  }

  const ghostEnd = fillWords(ghostEl, words.length - 1, null);
  ghostEl.append(pledge.slice(ghostEnd));

  function appendAi(text) {
    const ai = document.createElement("span");
    ai.className = "ai";
    ai.textContent = text;
    viewEl.appendChild(ai);
  }

  // upTo: null = original pledge (or a voice's whole rewrite), a number = hovered word.
  function renderHula(upTo, continuation) {
    if (choice.kind === "voice") {
      viewEl.innerHTML = "";
      appendAi(voiceText);
    } else if (upTo == null) {
      viewEl.append(pledge.slice(fillWords(viewEl, words.length - 1, null)));
    } else {
      fillWords(viewEl, upTo, upTo);
      if (continuation) appendAi((/^[\s,.;:!?)]/.test(continuation) ? "" : " ") + continuation);
    }
    // The poem area only ever grows: a shorter text never shrinks the page, so the
    // browser never has to pull the scroll position up. The page moves only when scrolled.
    const h = viewEl.offsetHeight;
    if (h > (parseFloat(hulaEl.style.minHeight) || 0)) hulaEl.style.minHeight = h + "px";
  }

  // Until generate.py has run: every remaining word becomes "mega", line breaks kept.
  function placeholder(i) {
    return pledge.slice(words[i].end).replace(/\S+/g, "mega");
  }

  function pick(i) {
    const variants = (completionsById[choice.id] || [])[i] || [];
    return variants.length ? random(variants) : placeholder(i);
  }

  function activate(i) {
    clearTimeout(leaveTimer);
    if (choice.kind === "voice" || i === active) return;
    active = i;
    renderHula(i, pick(i));
  }

  function scheduleRevert() {
    leaveTimer = setTimeout(() => {
      active = null;
      renderHula(null);
    }, 900);
  }

  ghostEl.addEventListener("mouseover", (e) => {
    const w = e.target.closest(".w");
    if (w) activate(+w.dataset.i);
  });
  ghostEl.addEventListener("click", (e) => {
    const w = e.target.closest(".w");
    if (w) activate(+w.dataset.i);
  });
  hulaEl.addEventListener("mouseleave", scheduleRevert);

  // ---- Pages (hash routing); no hash or an unknown one shows the landing page ----
  const tabs = [...document.querySelectorAll(".tabs a")];
  function showTab() {
    let id = location.hash.slice(1);
    if (id === "playground") id = "inference"; // links shared before the tabs were split
    const page = id === "inference" || id === "voices" ? "play" : ["notes", "about"].includes(id) ? id : "home";
    tabs.forEach((t) => t.setAttribute("aria-current", t.dataset.tab === id ? "page" : "false"));
    document.querySelectorAll(".page").forEach((p) => (p.hidden = p.id !== "page-" + page));
    if (page === "play") setMode(id);
    scrollTo(0, 0);
  }
  addEventListener("hashchange", showTab);
  showTab();

  // ---- Light / dark toggle; with no saved choice the system setting applies ----
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    const current = root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (e) {}
  });
})();
