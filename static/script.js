/* =========================================================
   SHOGUN — Multi-Agent Travel Command
   Talks to POST /api/travel on the FastAPI backend (app.py),
   which wraps backend.run_travel_agent() / the LangGraph
   travel_graph pipeline.
   ========================================================= */

(() => {
  "use strict";

  const THREAD_KEY = "shogun_thread_id";
  const HISTORY_KEY = "shogun_history";
  const MAX_HISTORY = 8;

  // Simulated per-agent duration while we wait on the single
  // blocking POST response (the backend has no streaming/progress
  // endpoint, so this just reflects the real, fixed node order:
  // flight_agent -> hotel_agent -> itinerary_agent -> final_agent).
  const STAGE_MS = [2200, 2200, 3200, 2600];
  const AGENT_ORDER = ["flight", "hotel", "itinerary", "final"];

  const el = {
    form: document.getElementById("travel-form"),
    input: document.getElementById("query-input"),
    submitBtn: document.getElementById("submit-btn"),
    newTripBtn: document.getElementById("new-trip-btn"),
    chips: document.getElementById("example-chips"),
    scrollCue: document.getElementById("scroll-cue"),

    pipeline: document.getElementById("pipeline"),
    nodes: Array.from(document.querySelectorAll(".pipeline-node")),

    errorBanner: document.getElementById("error-banner"),
    errorDetail: document.getElementById("error-detail"),
    errorRetry: document.getElementById("error-retry"),

    results: document.getElementById("results"),
    resultsMeta: document.getElementById("results-meta"),
    downloadPdfBtn: document.getElementById("download-pdf-btn"),
    downloadPdfLabel: document.getElementById("download-pdf-label"),
    tabs: Array.from(document.querySelectorAll(".tab")),
    panels: {
      answer: document.getElementById("panel-answer"),
      flights: document.getElementById("panel-flights"),
      hotels: document.getElementById("panel-hotels"),
      draft: document.getElementById("panel-draft"),
    },

    history: document.getElementById("history"),
    historyList: document.getElementById("history-list"),
  };

  let stageTimer = null;
  let lastQuery = "";
  let lastPayload = null;

  // ---------------------------------------------------------
  // Thread persistence
  // ---------------------------------------------------------

  function getThreadId() {
    let id = localStorage.getItem(THREAD_KEY);
    if (!id) {
      id = "web_" + (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()));
      localStorage.setItem(THREAD_KEY, id);
    }
    return id;
  }

  function resetThread() {
    localStorage.removeItem(THREAD_KEY);
    el.input.value = "";
    el.results.hidden = true;
    el.errorBanner.hidden = true;
    el.pipeline.hidden = true;
    resetPipelineVisual();
    el.input.focus();
  }

  // ---------------------------------------------------------
  // History (session-local, cosmetic — not authoritative state)
  // ---------------------------------------------------------

  function loadHistory() {
    try {
      return JSON.parse(sessionStorage.getItem(HISTORY_KEY) || "[]");
    } catch {
      return [];
    }
  }

  function saveToHistory(query, payload) {
    const items = loadHistory();
    items.unshift({ query, payload, ts: Date.now() });
    sessionStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, MAX_HISTORY)));
    renderHistory();
  }

  function renderHistory() {
    const items = loadHistory();
    if (!items.length) {
      el.history.hidden = true;
      return;
    }
    el.history.hidden = false;
    el.historyList.innerHTML = "";
    items.forEach((item, idx) => {
      const li = document.createElement("li");

      const span = document.createElement("span");
      span.className = "history-query";
      span.textContent = item.query;
      span.title = item.query;

      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Reopen";
      btn.addEventListener("click", () => {
        renderResults(item.payload);
        el.input.value = item.query;
        window.scrollTo({ top: el.results.offsetTop - 24, behavior: "smooth" });
      });

      li.appendChild(span);
      li.appendChild(btn);
      el.historyList.appendChild(li);
    });
  }

  // ---------------------------------------------------------
  // Pipeline visual
  // ---------------------------------------------------------

  function resetPipelineVisual() {
    el.nodes.forEach((node) => {
      node.classList.remove("active", "complete");
      node.querySelector(".node-status").textContent = "Standing by";
    });
  }

  function setStage(index, state) {
    const node = el.nodes[index];
    if (!node) return;
    const statusEl = node.querySelector(".node-status");
    if (state === "active") {
      node.classList.add("active");
      node.classList.remove("complete");
      statusEl.textContent = "Working…";
    } else if (state === "complete") {
      node.classList.remove("active");
      node.classList.add("complete");
      statusEl.textContent = "Reported in";
    }
  }

  function runStageAnimation() {
    resetPipelineVisual();
    el.pipeline.hidden = false;

    let i = 0;
    setStage(0, "active");

    stageTimer = setInterval(() => {
      setStage(i, "complete");
      i += 1;
      // Hold on the final node until the real response lands —
      // there is no 5th stage, so we just stop advancing here.
      if (i < AGENT_ORDER.length) {
        setStage(i, "active");
      } else {
        clearInterval(stageTimer);
      }
    }, STAGE_MS[Math.min(i, STAGE_MS.length - 1)]);
  }

  function finishStageAnimationInstantly() {
    if (stageTimer) clearInterval(stageTimer);
    el.nodes.forEach((node) => {
      node.classList.remove("active");
      node.classList.add("complete");
      node.querySelector(".node-status").textContent = "Reported in";
    });
  }

  // ---------------------------------------------------------
  // Markdown rendering (final "answer" is LLM-authored markdown)
  // ---------------------------------------------------------

  function renderMarkdown(text) {
    if (!text) return "<p><em>No briefing returned.</em></p>";
    const raw = window.marked ? window.marked.parse(text) : escapeHtml(text);
    return window.DOMPurify ? window.DOMPurify.sanitize(raw) : raw;
  }

  function escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  function renderPlain(container, text) {
    container.innerHTML = "";
    const pre = document.createElement("pre");
    pre.textContent = text && text.trim() ? text : "No data returned for this leg.";
    container.appendChild(pre);
  }

  // ---------------------------------------------------------
  // Flight cards (structured, not a raw text dump)
  // ---------------------------------------------------------

  const STATUS_LABELS = {
    scheduled: "Scheduled",
    active: "In the air",
    landed: "Landed",
    cancelled: "Cancelled",
    incident: "Incident",
    diverted: "Diverted",
    unknown: "Status unknown",
  };

  function statusClass(status) {
    const key = (status || "unknown").toLowerCase();
    if (["cancelled", "incident"].includes(key)) return "status-bad";
    if (key === "diverted") return "status-warn";
    if (key === "landed") return "status-good";
    if (key === "active") return "status-live";
    return "status-neutral";
  }

  function fmtTime(iso) {
    if (!iso) return "Time TBD";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  }

  function flightCardHtml(f) {
    const status = (f.status || "unknown").toLowerCase();
    const statusLabel = STATUS_LABELS[status] || f.status || "Unknown";
    const dep = f.departure || {};
    const arr = f.arrival || {};
    const depDelay = dep.delay_minutes != null ? `<span class="delay-tag">+${dep.delay_minutes}m</span>` : "";
    const arrDelay = arr.delay_minutes != null ? `<span class="delay-tag">+${arr.delay_minutes}m</span>` : "";

    return `
      <article class="flight-card">
        <header class="flight-card-head">
          <div class="flight-card-id">
            <span class="flight-airline">${escapeHtml(f.airline || "Unknown airline")}</span>
            <span class="flight-number">${escapeHtml(f.flight_number || "—")}</span>
          </div>
          <span class="status-badge ${statusClass(status)}">${escapeHtml(statusLabel)}</span>
        </header>
        <div class="flight-card-route">
          <div class="flight-leg">
            <span class="leg-iata">${escapeHtml(dep.iata || "—")}</span>
            <span class="leg-airport">${escapeHtml(dep.airport || "")}</span>
            <span class="leg-time">${fmtTime(dep.scheduled)} ${depDelay}</span>
          </div>
          <div class="flight-leg-arrow" aria-hidden="true">→</div>
          <div class="flight-leg">
            <span class="leg-iata">${escapeHtml(arr.iata || "—")}</span>
            <span class="leg-airport">${escapeHtml(arr.airport || "")}</span>
            <span class="leg-time">${fmtTime(arr.scheduled)} ${arrDelay}</span>
          </div>
        </div>
      </article>
    `;
  }

  function renderFlights(container, flightData, notice, fallbackText) {
    container.innerHTML = "";

    const noticeEl = document.createElement("p");
    noticeEl.className = "flight-notice";
    noticeEl.textContent = notice || "AviationStack's free tier doesn't include ticket prices — status only.";
    container.appendChild(noticeEl);

    if (Array.isArray(flightData) && flightData.length) {
      const grid = document.createElement("div");
      grid.className = "flight-card-grid";
      grid.innerHTML = flightData.map(flightCardHtml).join("");
      container.appendChild(grid);
      return;
    }

    const empty = document.createElement("div");
    empty.className = "flight-empty";
    empty.innerHTML = `<p>${escapeHtml(
      (fallbackText && fallbackText.trim())
        ? fallbackText.split("\n")[0]
        : "No live flights currently in range for this route."
    )}</p>`;
    container.appendChild(empty);
  }

  // ---------------------------------------------------------
  // Collapsible markdown panels (Lodging Intel / Draft Itinerary
  // used to dump the raw AI text in full — this caps the initial
  // height and lets people expand if they actually want to read
  // the whole thing).
  // ---------------------------------------------------------

  function renderCollapsibleMarkdown(container, text) {
    container.innerHTML = renderMarkdown(text);
    // Reset any leftover collapse state from a previous query.
    container.classList.remove("is-collapsible", "is-collapsed");
    container.style.maxHeight = "";
    const oldFade = container.querySelector(".panel-fade");
    if (oldFade) oldFade.remove();
    const oldToggle = container.querySelector(".panel-toggle");
    if (oldToggle) oldToggle.remove();
  }

  function setupCollapsible(container, collapsedHeight) {
    const height = collapsedHeight || 260;

    // Already set up (or already known to be short) for this content.
    if (container.dataset.collapseChecked === "1") return;

    // Panel must actually be visible for scrollHeight to mean anything.
    if (container.hidden) return;

    container.dataset.collapseChecked = "1";

    if (container.scrollHeight <= height + 40) return;

    container.classList.add("is-collapsible", "is-collapsed");
    container.style.maxHeight = height + "px";

    const fade = document.createElement("div");
    fade.className = "panel-fade";
    container.appendChild(fade);

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "panel-toggle";
    toggle.textContent = "Show full text";
    toggle.addEventListener("click", () => {
      const collapsed = container.classList.toggle("is-collapsed");
      container.style.maxHeight = collapsed ? height + "px" : "none";
      toggle.textContent = collapsed ? "Show full text" : "Show less";
    });
    container.appendChild(toggle);
  }

  // ---------------------------------------------------------
  // Tabs
  // ---------------------------------------------------------

  function activateTab(name) {
    el.tabs.forEach((btn) => {
      const isActive = btn.dataset.tab === name;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-selected", String(isActive));
    });
    Object.entries(el.panels).forEach(([key, panel]) => {
      panel.hidden = key !== name;
    });

    if (name === "hotels") setupCollapsible(el.panels.hotels, 260);
    if (name === "draft") setupCollapsible(el.panels.draft, 320);
  }

  el.tabs.forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab));
  });

  // ---------------------------------------------------------
  // Results rendering
  // ---------------------------------------------------------

  function renderResults(payload) {
    lastPayload = payload;

    el.panels.answer.innerHTML = renderMarkdown(payload.answer);

    renderFlights(el.panels.flights, payload.flight_data, payload.flight_notice, payload.flight_results);

    delete el.panels.hotels.dataset.collapseChecked;
    delete el.panels.draft.dataset.collapseChecked;
    renderCollapsibleMarkdown(el.panels.hotels, payload.hotel_results);
    renderCollapsibleMarkdown(el.panels.draft, payload.itinerary);

    el.resultsMeta.textContent =
      `Thread ${payload.thread_id || "—"} · ${payload.llm_calls ?? "?"} agent calls`;

    activateTab("answer");
    el.results.hidden = false;
    el.errorBanner.hidden = true;
  }

  function renderError(message) {
    el.errorDetail.textContent = message || "Unknown error.";
    el.errorBanner.hidden = false;
    el.results.hidden = true;
  }

  // ---------------------------------------------------------
  // Submit
  // ---------------------------------------------------------

  async function dispatch(query) {
    lastQuery = query;
    el.submitBtn.disabled = true;
    el.submitBtn.classList.add("loading");
    el.errorBanner.hidden = true;
    el.results.hidden = true;

    runStageAnimation();

    try {
      const res = await fetch("/api/travel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: query,
          thread_id: getThreadId(),
        }),
      });

      const data = await res.json();

      finishStageAnimationInstantly();

      if (!res.ok || !data.success) {
        renderError(data.error || `Request failed (HTTP ${res.status}).`);
        return;
      }

      renderResults(data);
      saveToHistory(query, data);
      window.scrollTo({ top: el.results.offsetTop - 24, behavior: "smooth" });
    } catch (err) {
      finishStageAnimationInstantly();
      renderError(
        err && err.message
          ? `Could not reach the backend — ${err.message}`
          : "Could not reach the backend."
      );
    } finally {
      el.submitBtn.disabled = false;
      el.submitBtn.classList.remove("loading");
    }
  }

  el.form.addEventListener("submit", (e) => {
    e.preventDefault();
    const query = el.input.value.trim();
    if (!query) return;
    dispatch(query);
  });

  el.input.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      el.form.requestSubmit();
    }
  });

  el.chips.addEventListener("click", (e) => {
    const btn = e.target.closest(".chip");
    if (!btn) return;
    el.input.value = btn.dataset.example;
    el.input.focus();
  });

  el.newTripBtn.addEventListener("click", resetThread);

  // Scroll cue: nudge past the hero to whatever comes next — the
  // pipeline/results once a campaign is running, or the footer
  // before that.
  el.scrollCue.addEventListener("click", () => {
    const next = [el.pipeline, el.results, el.errorBanner].find(
      (section) => section && !section.hidden
    );
    const targetTop = next
      ? next.offsetTop - 24
      : (document.querySelector(".hero")?.offsetHeight || window.innerHeight);
    window.scrollTo({ top: targetTop, behavior: "smooth" });
  });

  el.errorRetry.addEventListener("click", () => {
    if (lastQuery) dispatch(lastQuery);
  });

  // ---------------------------------------------------------
  // PDF download
  // ---------------------------------------------------------

  async function downloadPdf() {
    if (!lastPayload || !lastPayload.answer) return;

    const originalLabel = el.downloadPdfLabel.textContent;
    el.downloadPdfBtn.disabled = true;
    el.downloadPdfLabel.textContent = "Preparing…";

    try {
      const res = await fetch("/api/travel/pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          answer: lastPayload.answer,
          thread_id: lastPayload.thread_id,
        }),
      });

      if (!res.ok) {
        throw new Error(`PDF export failed (HTTP ${res.status}).`);
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "shogun-travel-briefing.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      renderError(
        err && err.message ? err.message : "Could not generate the PDF."
      );
    } finally {
      el.downloadPdfBtn.disabled = false;
      el.downloadPdfLabel.textContent = originalLabel;
    }
  }

  el.downloadPdfBtn.addEventListener("click", downloadPdf);

  // ---------------------------------------------------------
  // Init
  // ---------------------------------------------------------

  renderHistory();
  getThreadId(); // ensure one exists from the first load
})();
