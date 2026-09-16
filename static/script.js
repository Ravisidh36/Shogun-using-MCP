/* =========================================================
   SHOGUN — Multi-Agent Travel Command
   Frontend controller for the FastAPI /api/travel backend.
   ========================================================= */
(() => {
  "use strict";

  const THREAD_KEY = "shogun_thread_id";
  const HISTORY_KEY = "shogun_history";
  const MAX_HISTORY = 8;

  const STAGE_MS = [2200, 2200, 1800, 3200, 2600];

  const el = {
    form: document.getElementById("travel-form"),
    input: document.getElementById("query-input"),
    submitBtn: document.getElementById("submit-btn"),
    newTripBtn: document.getElementById("new-trip-btn"),
    chips: document.getElementById("example-chips"),
    scrollCue: document.getElementById("scroll-cue"),
    destination: document.getElementById("destination-input"),
    dateStart: document.getElementById("date-start"),
    dateEnd: document.getElementById("date-end"),
    travelers: document.getElementById("travelers-select"),
    tripStyle: document.getElementById("style-select"),
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
      weather: document.getElementById("panel-weather"),
      hotels: document.getElementById("panel-hotels"),
      draft: document.getElementById("panel-draft"),
    },
    approvalPanel: document.getElementById("approval-panel"),
    approvalRequest: document.getElementById("approval-request"),
    approvalMeta: document.getElementById("approval-meta"),
    approvalDraft: document.getElementById("approval-draft"),
    approvalFeedback: document.getElementById("approval-feedback-input"),
    approvalStatus: document.getElementById("approval-status"),
    approveBtn: document.getElementById("approve-btn"),
    rejectBtn: document.getElementById("reject-btn"),
    history: document.getElementById("history"),
    historyList: document.getElementById("history-list"),
  };

  let stageTimer = null;
  let lastQuery = "";
  let lastPayload = null;
  let approvalInProgress = false;

  function getThreadId() {
    let id = localStorage.getItem(THREAD_KEY);
    if (!id) {
      id = "web_" + (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()));
      localStorage.setItem(THREAD_KEY, id);
    }
    return id;
  }

  function resetPipelineVisual() {
    el.nodes.forEach((node) => {
      node.classList.remove("active", "complete", "waiting", "skipped");
      const status = node.querySelector(".node-status");
      if (status) status.textContent = "Standing by";
      node.style.display = "";
    });
  }

  function resetThread() {
    localStorage.removeItem(THREAD_KEY);
    el.input.value = "";
    if (el.destination) el.destination.value = "";
    if (el.dateStart) el.dateStart.value = "";
    if (el.dateEnd) el.dateEnd.value = "";
    if (el.travelers) el.travelers.selectedIndex = 2;
    if (el.tripStyle) el.tripStyle.selectedIndex = 2;
    el.results.hidden = true;
    el.errorBanner.hidden = true;
    el.approvalPanel.hidden = true;
    el.pipeline.hidden = true;
    resetPipelineVisual();
    el.input.focus();
  }

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

  function relativeTime(ts) {
    const diffMs = Date.now() - ts;
    const mins = Math.round(diffMs / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min${mins === 1 ? "" : "s"} ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
    const days = Math.round(hours / 24);
    return `${days} day${days === 1 ? "" : "s"} ago`;
  }

  function renderHistory() {
    const items = loadHistory();
    el.history.hidden = false;
    el.historyList.innerHTML = "";
    if (!items.length) {
      const empty = document.createElement("li");
      empty.className = "history-empty";
      empty.innerHTML = '<p class="panel-empty-title">No previous trips yet</p><p class="panel-empty-detail">Your saved travel plans will appear here.</p>';
      el.historyList.appendChild(empty);
      return;
    }
    items.forEach((item) => {
      const li = document.createElement("li");
      const span = document.createElement("span");
      span.className = "history-query";
      span.textContent = item.query;
      span.title = item.query;
      const meta = document.createElement("span");
      meta.className = "history-meta";
      meta.textContent = relativeTime(item.ts);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Reopen";
      btn.addEventListener("click", () => {
        lastPayload = item.payload;
        lastQuery = item.query;
        renderResults(item.payload);
        el.input.value = item.query;
        window.scrollTo({ top: el.results.offsetTop - 24, behavior: "smooth" });
      });
      li.append(span, meta, btn);
      el.historyList.appendChild(li);
    });
  }

  function setStage(index, state) {
    const node = el.nodes[index];
    if (!node) return;
    const status = node.querySelector(".node-status");
    node.classList.toggle("active", state === "active");
    node.classList.toggle("complete", state === "complete");
    if (status) status.textContent = state === "active" ? "Working…" : "Reported in";
  }

  function runStageAnimation() {
    resetPipelineVisual();
    el.pipeline.hidden = false;
    let i = 0;
    setStage(0, "active");
    stageTimer = setInterval(() => {
      setStage(i, "complete");
      i += 1;
      if (i < el.nodes.length) {
        setStage(i, "active");
      } else {
        clearInterval(stageTimer);
        stageTimer = null;
      }
    }, STAGE_MS[Math.min(i, STAGE_MS.length - 1)]);
  }

  function finishStageAnimationInstantly() {
    if (stageTimer) {
      clearInterval(stageTimer);
      stageTimer = null;
    }
  }

  function toText(value) {
    if (typeof value === "string") return value;
    if (Array.isArray(value)) {
      return value.map((item) => item && typeof item === "object" ? (item.text ?? JSON.stringify(item)) : String(item)).join("\n\n");
    }
    if (value && typeof value === "object") return JSON.stringify(value, null, 2);
    return value == null ? "" : String(value);
  }

  function escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str == null ? "" : String(str);
    return d.innerHTML;
  }

  function renderMarkdown(value) {
    const text = toText(value);
    if (!text) return "<p><em>No briefing returned.</em></p>";
    const raw = window.marked ? window.marked.parse(text) : escapeHtml(text);
    return window.DOMPurify ? window.DOMPurify.sanitize(raw) : raw;
  }

  function renderEmptyState(container, title, detail) {
    container.innerHTML = `
      <div class="panel-empty">
        <p class="panel-empty-title">${escapeHtml(title)}</p>
        <p class="panel-empty-detail">${escapeHtml(detail)}</p>
      </div>`;
  }

  function renderCollapsibleMarkdown(container, value) {
    container.innerHTML = renderMarkdown(value);
    container.classList.remove("is-collapsible", "is-collapsed");
    container.style.maxHeight = "";
    const height = container.scrollHeight;
    if (height <= 420) return;
    container.classList.add("is-collapsible", "is-collapsed");
    container.style.maxHeight = "420px";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "panel-toggle";
    toggle.textContent = "Show full text";
    toggle.addEventListener("click", () => {
      const collapsed = container.classList.toggle("is-collapsed");
      container.style.maxHeight = collapsed ? "420px" : "none";
      toggle.textContent = collapsed ? "Show full text" : "Show less";
    });
    container.appendChild(toggle);
  }

  function renderFlightPanel(container, flightData, fallbackText) {
    container.innerHTML = "";
    container.classList.remove("is-collapsible", "is-collapsed");
    container.style.maxHeight = "";
    if (flightData && Array.isArray(flightData.flights) && flightData.flights.length) {
      if (flightData.route_info) {
        const route = document.createElement("p");
        route.className = "panel-lede";
        route.textContent = flightData.route_info;
        container.appendChild(route);
      }
      const grid = document.createElement("div");
      grid.className = "flight-grid";
      flightData.flights.forEach((f) => {
        const card = document.createElement("div");
        card.className = "flight-card";
        const dep = f.departure || {};
        const arr = f.arrival || {};
        const status = String(f.status || "unknown").toLowerCase();
        card.innerHTML = `
          <div class="flight-card-head"><span class="flight-airline">${escapeHtml(f.airline || "Unknown airline")}</span><span class="flight-status flight-status-${escapeHtml(status)}">${escapeHtml(status)}</span></div>
          <div class="flight-card-number">${escapeHtml(f.flight_number || "—")}</div>
          <div class="flight-card-route">
            <div class="flight-endpoint"><span class="flight-iata">${escapeHtml(dep.iata || "—")}</span><span class="flight-airport">${escapeHtml(dep.airport || "")}</span><span class="flight-time">${escapeHtml(dep.scheduled || "")}</span></div>
            <span class="flight-arrow">→</span>
            <div class="flight-endpoint"><span class="flight-iata">${escapeHtml(arr.iata || "—")}</span><span class="flight-airport">${escapeHtml(arr.airport || "")}</span><span class="flight-time">${escapeHtml(arr.scheduled || "")}</span></div>
          </div>`;
        grid.appendChild(card);
      });
      container.appendChild(grid);
      if (flightData.notice) {
        const notice = document.createElement("p");
        notice.className = "panel-notice";
        notice.textContent = flightData.notice;
        container.appendChild(notice);
      }
      return;
    }
    if (flightData && flightData.unavailable) {
      renderEmptyState(container, "Flight status is temporarily unavailable", "We'll rely on general route guidance in your itinerary instead.");
      return;
    }
    if (flightData && flightData.notice) {
      renderEmptyState(container, "No live flights in range right now", flightData.notice);
      return;
    }
    renderCollapsibleMarkdown(container, fallbackText);
  }

  function weatherEmoji(condition) {
    const c = String(condition || "").toLowerCase();
    if (c.includes("thunder") || c.includes("storm")) return "⛈️";
    if (c.includes("snow")) return "❄️";
    if (c.includes("rain") || c.includes("drizzle")) return "🌧️";
    if (c.includes("fog") || c.includes("mist") || c.includes("haze")) return "🌫️";
    if (c.includes("cloud") || c.includes("overcast")) return "☁️";
    if (c.includes("clear") || c.includes("sun")) return "☀️";
    return "🌤️";
  }

  function renderWeatherPanel(container, weatherData, fallbackText) {
    container.innerHTML = "";
    container.classList.remove("is-collapsible", "is-collapsed");
    container.style.maxHeight = "";
    if (!weatherData) {
      renderEmptyState(container, "Weather is temporarily unavailable", "We couldn't reach the weather service for this destination just now.");
      return;
    }
    const wrap = document.createElement("div");
    wrap.className = "weather-panel";
    const hasCurrent = weatherData.temperature_c !== null && weatherData.temperature_c !== undefined;
    if (hasCurrent) {
      const current = document.createElement("div");
      current.className = "weather-current";
      current.innerHTML = `
        <div class="weather-current-icon">${weatherEmoji(weatherData.condition)}</div>
        <div class="weather-current-main">
          <div class="weather-current-temp">${Math.round(weatherData.temperature_c)}°C</div>
          <div class="weather-current-condition">${escapeHtml(weatherData.condition || "")}</div>
          <div class="weather-current-city">${escapeHtml(weatherData.city || "")}</div>
        </div>
        <div class="weather-current-stats">
          <div><span>Feels like</span><strong>${weatherData.feels_like_c != null ? Math.round(weatherData.feels_like_c) + "°C" : "—"}</strong></div>
          <div><span>Humidity</span><strong>${weatherData.humidity != null ? weatherData.humidity + "%" : "—"}</strong></div>
          <div><span>Wind</span><strong>${weatherData.wind_speed != null ? weatherData.wind_speed + " m/s" : "—"}</strong></div>
        </div>`;
      wrap.appendChild(current);
    }
    const forecast = Array.isArray(weatherData.forecast) ? weatherData.forecast : [];
    if (forecast.length) {
      const grid = document.createElement("div");
      grid.className = "weather-forecast";
      forecast.slice(0, 7).forEach((day) => {
        const item = document.createElement("div");
        item.className = "weather-day";
        item.innerHTML = `<div class="weather-day-date">${escapeHtml(day.date || "")}</div><div class="weather-day-icon">${weatherEmoji(day.condition)}</div><div class="weather-day-condition">${escapeHtml(day.condition || "")}</div><div class="weather-day-temps">${day.min_temp_c != null ? Math.round(day.min_temp_c) + "°" : "—"} / ${day.max_temp_c != null ? Math.round(day.max_temp_c) + "°" : "—"}</div>`;
        grid.appendChild(item);
      });
      wrap.appendChild(grid);
    }
    if (!wrap.children.length) renderCollapsibleMarkdown(container, fallbackText);
    else container.appendChild(wrap);
  }

  function activateTab(name) {
    el.tabs.forEach((btn) => {
      const active = btn.dataset.tab === name;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", String(active));
    });
    Object.entries(el.panels).forEach(([key, panel]) => {
      panel.hidden = key !== name;
    });
  }

  el.tabs.forEach((btn) => btn.addEventListener("click", () => activateTab(btn.dataset.tab)));

  function formatDate(value) {
    if (!value) return "";
    const d = new Date(`${value}T00:00:00`);
    if (Number.isNaN(d.getTime())) return value;
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }

  function composeQuery() {
    const destination = el.destination?.value.trim() || "";
    const extra = el.input?.value.trim() || "";

    // A quick example chip is a complete natural-language request. Do not
    // accidentally prepend an unrelated destination already in the form.
    if (extra) return extra;

    if (!destination) return "";

    const parts = [`Plan a trip to ${destination}`];
    const start = formatDate(el.dateStart?.value || "");
    const end = formatDate(el.dateEnd?.value || "");
    if (start && end) parts.push(`from ${start} to ${end}`);
    else if (start) parts.push(`starting ${start}`);
    if (el.travelers?.value) parts.push(`for ${el.travelers.value}`);
    if (el.tripStyle?.value) parts.push(`${el.tripStyle.value} style`);
    return `${parts.join(", ")}.`;
  }

  function updatePipelineFromBackend(payload) {
    const selected = new Set(Array.isArray(payload.selected_agents) ? payload.selected_agents : []);
    const routingKnown = Array.isArray(payload.selected_agents);

    el.nodes.forEach((node) => {
      const agent = node.dataset.agent;
      const status = node.querySelector(".node-status");
      if (!status) return;
      node.classList.remove("active", "complete", "waiting", "skipped");

      if (agent === "supervisor") {
        node.classList.add("complete");
        status.textContent = "Completed";
      } else if (agent === "human_approval") {
        if (payload.requires_approval) {
          node.classList.add("waiting");
          status.textContent = "Awaiting approval";
        } else {
          node.classList.add("complete");
          status.textContent = "Reviewed";
        }
      } else if (agent === "final_agent") {
        if (payload.final_response || payload.answer) {
          node.classList.add("complete");
          status.textContent = "Completed";
        }
      } else if (["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"].includes(agent)) {
        if (!routingKnown) {
          node.classList.add("complete");
          status.textContent = "Completed";
        } else if (selected.has(agent)) {
          node.classList.add("complete");
          status.textContent = "Completed";
        } else {
          node.classList.add("skipped");
          status.textContent = "Not required";
        }
      }
    });
  }

  function hideApprovalPanel() {
    el.approvalPanel.hidden = true;
  }

  function showApprovalPanel(payload) {
    lastPayload = payload;
    el.approvalPanel.hidden = false;
    el.results.hidden = true;
    el.errorBanner.hidden = true;
    el.approvalRequest.textContent = payload.approval_request || "Shogun has prepared a draft itinerary for your review.";
    el.approvalDraft.innerHTML = renderMarkdown(payload.itinerary || payload.draft_itinerary || payload.answer || "No draft itinerary was returned.");
    const selected = Array.isArray(payload.selected_agents) ? payload.selected_agents : [];
    const names = {
      flight_agent: "Flight Scout",
      hotel_agent: "Hotel Scout",
      weather_agent: "Weather Scout",
      budget_agent: "Budget Analyst",
      itinerary_agent: "Itinerary Strategist",
    };
    el.approvalMeta.textContent = selected.length ? `Supervisor selected: ${selected.map((x) => names[x] || x).join(" · ")}` : "The Supervisor has prepared this plan for human review.";
    el.approvalFeedback.value = "";
    el.approvalStatus.hidden = true;
    el.approvalStatus.textContent = "";
    updatePipelineFromBackend(payload);
    el.approvalPanel.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function renderGuardrailBlock(payload) {
    hideApprovalPanel();
    el.results.hidden = true;
    el.errorBanner.hidden = false;
    el.errorDetail.textContent = payload.guardrail_reason || payload.answer || "This request was blocked by Shogun's travel guardrail.";
    const title = el.errorBanner.querySelector(".error-title");
    if (title) title.textContent = "Mission blocked by travel guardrail.";
    window.scrollTo({ top: el.errorBanner.offsetTop - 24, behavior: "smooth" });
  }

  function renderResults(payload) {
    lastPayload = payload;
    el.panels.answer.innerHTML = renderMarkdown(payload.answer);
    renderFlightPanel(el.panels.flights, payload.flight_data, payload.flight_results);
    renderWeatherPanel(el.panels.weather, payload.weather_data, payload.weather_results);
    renderCollapsibleMarkdown(el.panels.hotels, payload.hotel_results);
    renderCollapsibleMarkdown(el.panels.draft, payload.itinerary);
    const calls = payload.llm_calls ?? 0;
    el.resultsMeta.textContent = `Trip research complete · ${calls} ${calls === 1 ? "call" : "calls"}`;
    el.resultsMeta.title = payload.thread_id ? `Thread ${payload.thread_id}` : "";
    activateTab("answer");
    el.results.hidden = false;
    el.errorBanner.hidden = true;
  }

  function renderError(error) {
    const message = typeof error === "string" ? error : (error && error.message) || "Something went wrong. Please try again.";
    el.errorDetail.textContent = message;
    el.errorBanner.hidden = false;
    el.results.hidden = true;
    hideApprovalPanel();
  }

  async function dispatch(query) {
    const cleanQuery = String(query || "").trim();
    if (!cleanQuery) return;

    lastQuery = cleanQuery;
    el.submitBtn.disabled = true;
    el.submitBtn.classList.add("loading");
    el.errorBanner.hidden = true;
    el.results.hidden = true;
    hideApprovalPanel();
    runStageAnimation();

    try {
      const res = await fetch("/api/travel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: cleanQuery, thread_id: getThreadId() }),
      });

      const raw = await res.text();
      let data;
      try {
        data = raw ? JSON.parse(raw) : {};
      } catch {
        throw new Error(`Backend returned invalid JSON (HTTP ${res.status}).`);
      }

      finishStageAnimationInstantly();

      if (!res.ok || !data.success) {
        throw new Error(data.error || `Request failed (HTTP ${res.status}).`);
      }

      lastPayload = data;
      updatePipelineFromBackend(data);

      if (data.guardrail_allowed === false) {
        renderGuardrailBlock(data);
        return;
      }

      if (data.requires_approval) {
        showApprovalPanel(data);
        return;
      }

      renderResults(data);
      saveToHistory(cleanQuery, data);
      window.scrollTo({ top: el.results.offsetTop - 24, behavior: "smooth" });
    } catch (err) {
      finishStageAnimationInstantly();
      renderError(err?.message ? `Could not complete the travel mission — ${err.message}` : "Could not complete the travel mission.");
    } finally {
      el.submitBtn.disabled = false;
      el.submitBtn.classList.remove("loading");
    }
  }

  async function resumeMission(approved) {
    if (approvalInProgress) return;
    const threadId = lastPayload?.thread_id || getThreadId();
    if (!threadId) return renderError("No active travel mission was found.");
    const feedback = el.approvalFeedback.value.trim();
    if (!approved && !feedback) {
      el.approvalStatus.hidden = false;
      el.approvalStatus.textContent = "Please describe what you want changed.";
      el.approvalFeedback.focus();
      return;
    }

    approvalInProgress = true;
    el.approveBtn.disabled = true;
    el.rejectBtn.disabled = true;
    el.approvalFeedback.disabled = true;
    el.approvalStatus.hidden = false;
    el.approvalStatus.textContent = approved ? "Approval received. Finalizing your mission…" : "Sending your requested changes…";

    try {
      const res = await fetch("/api/travel/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: threadId, approved, feedback }),
      });
      const raw = await res.text();
      let data;
      try { data = raw ? JSON.parse(raw) : {}; } catch { throw new Error(`Backend returned invalid JSON (HTTP ${res.status}).`); }
      if (!res.ok || !data.success) throw new Error(data.error || `Approval request failed (HTTP ${res.status}).`);
      lastPayload = data;
      updatePipelineFromBackend(data);
      if (data.requires_approval) {
        showApprovalPanel(data);
        return;
      }
      hideApprovalPanel();
      renderResults(data);
      saveToHistory(lastQuery, data);
      window.scrollTo({ top: el.results.offsetTop - 24, behavior: "smooth" });
    } catch (err) {
      renderError(err?.message || "Could not resume the travel mission.");
    } finally {
      approvalInProgress = false;
      el.approveBtn.disabled = false;
      el.rejectBtn.disabled = false;
      el.approvalFeedback.disabled = false;
    }
  }

  el.form.addEventListener("submit", (event) => {
    event.preventDefault();
    dispatch(composeQuery());
  });

  el.input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    if (event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    el.form.requestSubmit();
  });

  el.chips.addEventListener("click", (event) => {
    const btn = event.target.closest(".chip");
    if (!btn) return;
    el.input.value = btn.dataset.example || "";
    el.form.requestSubmit();
  });

  el.newTripBtn.addEventListener("click", resetThread);
  el.approveBtn.addEventListener("click", () => resumeMission(true));
  el.rejectBtn.addEventListener("click", () => resumeMission(false));

  el.scrollCue.addEventListener("click", () => {
    const next = [el.pipeline, el.results, el.errorBanner, el.approvalPanel].find((section) => section && !section.hidden);
    const targetTop = next ? next.offsetTop - 24 : ((document.querySelector(".hero")?.offsetHeight || window.innerHeight));
    window.scrollTo({ top: targetTop, behavior: "smooth" });
  });

  el.errorRetry.addEventListener("click", () => {
    if (lastQuery) dispatch(lastQuery);
  });

  async function downloadPdf() {
    if (!lastPayload?.answer) return;
    const originalLabel = el.downloadPdfLabel.textContent;
    el.downloadPdfBtn.disabled = true;
    el.downloadPdfLabel.textContent = "Preparing…";
    try {
      const res = await fetch("/api/travel/pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer: lastPayload.answer, thread_id: lastPayload.thread_id }),
      });
      if (!res.ok) {
        let message = `PDF export failed (HTTP ${res.status}).`;
        try {
          const body = await res.json();
          if (body?.error) message = typeof body.error === "string" ? body.error : (body.error.message || message);
        } catch {}
        throw new Error(message);
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
      renderError(err?.message || "Could not generate the PDF.");
    } finally {
      el.downloadPdfBtn.disabled = false;
      el.downloadPdfLabel.textContent = originalLabel;
    }
  }

  el.downloadPdfBtn.addEventListener("click", downloadPdf);

  renderHistory();
  getThreadId();
})();
