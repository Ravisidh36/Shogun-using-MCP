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

  const STAGE_MS = [2200, 2200, 1800, 3200, 2600];
  const AGENT_ORDER = ["flight", "hotel", "weather", "itinerary", "final"];

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
      id =
        "web_" +
        (crypto.randomUUID
          ? crypto.randomUUID()
          : String(Date.now()));

      localStorage.setItem(THREAD_KEY, id);
    }

    return id;
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
    el.pipeline.hidden = true;

    resetPipelineVisual();

    el.input.focus();
  }

  // ---------------------------------------------------------
  // History
  // ---------------------------------------------------------

  function loadHistory() {
    try {
      return JSON.parse(
        sessionStorage.getItem(HISTORY_KEY) || "[]"
      );
    } catch {
      return [];
    }
  }

  function saveToHistory(query, payload) {
    const items = loadHistory();

    items.unshift({
      query,
      payload,
      ts: Date.now(),
    });

    sessionStorage.setItem(
      HISTORY_KEY,
      JSON.stringify(items.slice(0, MAX_HISTORY))
    );

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
      empty.innerHTML =
        '<p class="panel-empty-title">No previous trips yet</p>' +
        '<p class="panel-empty-detail">Your saved travel plans will appear here.</p>';
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
        renderResults(item.payload);

        el.input.value = item.query;

        window.scrollTo({
          top: el.results.offsetTop - 24,
          behavior: "smooth",
        });
      });

      li.appendChild(span);
      li.appendChild(meta);
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

      const status = node.querySelector(".node-status");

      if (status) {
        status.textContent = "Standing by";
      }
    });
  }

  function setStage(index, state) {
    const node = el.nodes[index];

    if (!node) return;

    const statusEl = node.querySelector(".node-status");

    if (state === "active") {
      node.classList.add("active");
      node.classList.remove("complete");

      if (statusEl) {
        statusEl.textContent = "Working…";
      }
    }

    if (state === "complete") {
      node.classList.remove("active");
      node.classList.add("complete");

      if (statusEl) {
        statusEl.textContent = "Reported in";
      }
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

      if (i < AGENT_ORDER.length) {
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

    el.nodes.forEach((node) => {
      node.classList.remove("active");
      node.classList.add("complete");

      const status = node.querySelector(".node-status");

      if (status) {
        status.textContent = "Reported in";
      }
    });
  }

  // ---------------------------------------------------------
  // Markdown rendering
  // ---------------------------------------------------------

  function toText(value) {
    if (typeof value === "string") {
      return value;
    }

    if (Array.isArray(value)) {
      return value
        .map((item) => {
          if (item && typeof item === "object") {
            return item.text ?? JSON.stringify(item);
          }

          return String(item);
        })
        .join("\n\n");
    }

    if (value && typeof value === "object") {
      return JSON.stringify(value, null, 2);
    }

    return value == null ? "" : String(value);
  }

  function renderMarkdown(value) {
    const text = toText(value);

    if (!text) {
      return "<p><em>No briefing returned.</em></p>";
    }

    const raw = window.marked
      ? window.marked.parse(text)
      : escapeHtml(text);

    return window.DOMPurify
      ? window.DOMPurify.sanitize(raw)
      : raw;
  }

  function escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  // ---------------------------------------------------------
  // Structured Flight Intel (falls back to the plain-text summary
  // if the backend couldn't build structured flight_data for this
  // request — e.g. the flight API itself is down).
  // ---------------------------------------------------------

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
        const status = (f.status || "unknown").toLowerCase();

        card.innerHTML = `
          <div class="flight-card-head">
            <span class="flight-airline">${escapeHtml(f.airline || "Unknown airline")}</span>
            <span class="flight-status flight-status-${escapeHtml(status)}">${escapeHtml(status)}</span>
          </div>
          <div class="flight-card-number">${escapeHtml(f.flight_number || "—")}</div>
          <div class="flight-card-route">
            <div class="flight-endpoint">
              <span class="flight-iata">${escapeHtml(dep.iata || "—")}</span>
              <span class="flight-airport">${escapeHtml(dep.airport || "")}</span>
              <span class="flight-time">${escapeHtml(dep.scheduled || "")}</span>
            </div>
            <span class="flight-arrow">→</span>
            <div class="flight-endpoint">
              <span class="flight-iata">${escapeHtml(arr.iata || "—")}</span>
              <span class="flight-airport">${escapeHtml(arr.airport || "")}</span>
              <span class="flight-time">${escapeHtml(arr.scheduled || "")}</span>
            </div>
          </div>
        `;
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
      renderEmptyState(
        container,
        "Flight status is temporarily unavailable",
        "We'll rely on general route guidance in your itinerary instead."
      );
      return;
    }

    if (flightData && flightData.notice) {
      renderEmptyState(container, "No live flights in range right now", flightData.notice);
      return;
    }

    renderCollapsibleMarkdown(container, fallbackText);
  }

  // ---------------------------------------------------------
  // Structured Weather (falls back to plain text similarly).
  // ---------------------------------------------------------

  function weatherEmoji(condition) {
    const c = (condition || "").toLowerCase();
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
      renderEmptyState(
        container,
        "Weather is temporarily unavailable",
        "We couldn't reach the weather service for this destination just now."
      );
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
        </div>
      `;
      wrap.appendChild(current);
    } else {
      const notice = document.createElement("p");
      notice.className = "panel-notice";
      notice.textContent = `Current conditions are temporarily unavailable for ${weatherData.city || "this destination"}.`;
      wrap.appendChild(notice);
    }

    const forecast = Array.isArray(weatherData.forecast) ? weatherData.forecast : [];

    if (forecast.length) {
      const forecastRow = document.createElement("div");
      forecastRow.className = "weather-forecast";
      forecast.forEach((entry) => {
        const card = document.createElement("div");
        card.className = "weather-forecast-card";
        card.innerHTML = `
          <div class="weather-forecast-time">${escapeHtml(entry.datetime_label || entry.datetime || "")}</div>
          <div class="weather-forecast-icon">${weatherEmoji(entry.weather)}</div>
          <div class="weather-forecast-temp">${entry.temperature != null ? Math.round(entry.temperature) + "°C" : "—"}</div>
          <div class="weather-forecast-cond">${escapeHtml(entry.weather || "")}</div>
        `;
        forecastRow.appendChild(card);
      });
      wrap.appendChild(forecastRow);
    }

    container.appendChild(wrap);
  }

  function renderEmptyState(container, title, detail) {
    container.innerHTML = "";
    const wrap = document.createElement("div");
    wrap.className = "panel-empty";
    const h = document.createElement("p");
    h.className = "panel-empty-title";
    h.textContent = title;
    const p = document.createElement("p");
    p.className = "panel-empty-detail";
    p.textContent = detail || "";
    wrap.appendChild(h);
    wrap.appendChild(p);
    container.appendChild(wrap);
  }

  // ---------------------------------------------------------
  // Collapsible panels
  // ---------------------------------------------------------

  function renderCollapsibleMarkdown(container, text) {
    container.innerHTML = renderMarkdown(text);

    container.classList.remove(
      "is-collapsible",
      "is-collapsed"
    );

    container.style.maxHeight = "";

    const oldFade = container.querySelector(".panel-fade");

    if (oldFade) {
      oldFade.remove();
    }

    const oldToggle = container.querySelector(".panel-toggle");

    if (oldToggle) {
      oldToggle.remove();
    }
  }

  function setupCollapsible(container, collapsedHeight) {
    const height = collapsedHeight || 260;

    if (container.dataset.collapseChecked === "1") {
      return;
    }

    if (container.hidden) {
      return;
    }

    container.dataset.collapseChecked = "1";

    if (container.scrollHeight <= height + 40) {
      return;
    }

    container.classList.add(
      "is-collapsible",
      "is-collapsed"
    );

    container.style.maxHeight = height + "px";

    const fade = document.createElement("div");
    fade.className = "panel-fade";

    container.appendChild(fade);

    const toggle = document.createElement("button");

    toggle.type = "button";
    toggle.className = "panel-toggle";
    toggle.textContent = "Show full text";

    toggle.addEventListener("click", () => {
      const collapsed =
        container.classList.toggle("is-collapsed");

      container.style.maxHeight = collapsed
        ? height + "px"
        : "none";

      toggle.textContent = collapsed
        ? "Show full text"
        : "Show less";
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

      btn.setAttribute(
        "aria-selected",
        String(isActive)
      );
    });

    Object.entries(el.panels).forEach(
      ([key, panel]) => {
        panel.hidden = key !== name;
      }
    );
  }

  el.tabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      activateTab(btn.dataset.tab);
    });
  });

  // ---------------------------------------------------------
  // Results rendering
  // ---------------------------------------------------------

  function renderResults(payload) {
    lastPayload = payload;

    el.panels.answer.innerHTML =
      renderMarkdown(payload.answer);

    delete el.panels.hotels.dataset.collapseChecked;
    delete el.panels.draft.dataset.collapseChecked;

    renderFlightPanel(
      el.panels.flights,
      payload.flight_data,
      payload.flight_results
    );

    renderWeatherPanel(
      el.panels.weather,
      payload.weather_data,
      payload.weather_results
    );

    renderCollapsibleMarkdown(
      el.panels.hotels,
      payload.hotel_results
    );

    renderCollapsibleMarkdown(
      el.panels.draft,
      payload.itinerary
    );

    const sourceCount = payload.llm_calls ?? 0;
    el.resultsMeta.textContent =
      `Trip research complete · ${sourceCount} ${sourceCount === 1 ? "source" : "sources"}`;
    // Thread id stays available on hover for debugging without
    // being shown as primary UI copy.
    el.resultsMeta.title = payload.thread_id
      ? `Thread ${payload.thread_id}`
      : "";

    activateTab("answer");

    el.results.hidden = false;
    el.errorBanner.hidden = true;
  }

  function renderError(error) {
    const message =
      typeof error === "string"
        ? error
        : (error && error.message) ||
          "Something went wrong. Please try again.";

    el.errorDetail.textContent = message;

    el.errorBanner.hidden = false;
    el.results.hidden = true;
  }

  // ---------------------------------------------------------
  // Structured trip form
  // ---------------------------------------------------------

  function formatDate(value) {
    if (!value) return "";

    const d = new Date(`${value}T00:00:00`);

    if (Number.isNaN(d.getTime())) {
      return value;
    }

    return d.toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
  }

  function composeQuery() {
    const destination =
      el.destination?.value.trim() || "";

    if (!destination) {
      return el.input.value.trim();
    }

    const parts = [
      `Plan a trip to ${destination}`,
    ];

    const start = formatDate(
      el.dateStart?.value || ""
    );

    const end = formatDate(
      el.dateEnd?.value || ""
    );

    if (start && end) {
      parts.push(`from ${start} to ${end}`);
    } else if (start) {
      parts.push(`starting ${start}`);
    }

    if (el.travelers?.value) {
      parts.push(`for ${el.travelers.value}`);
    }

    if (el.tripStyle?.value) {
      parts.push(`${el.tripStyle.value} style`);
    }

    let message = `${parts.join(", ")}.`;

    const extra = el.input.value.trim();

    if (extra) {
      message += ` Additional notes: ${extra}`;
    }

    return message;
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

        headers: {
          "Content-Type": "application/json",
        },

        body: JSON.stringify({
          message: query,
          thread_id: getThreadId(),
        }),
      });

      const data = await res.json();

      finishStageAnimationInstantly();

      if (!res.ok || !data.success) {
        renderError(
          data.error ||
            `Request failed (HTTP ${res.status}).`
        );

        return;
      }

      renderResults(data);

      saveToHistory(query, data);

      window.scrollTo({
        top: el.results.offsetTop - 24,
        behavior: "smooth",
      });
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

    const query = composeQuery();

    if (!query) return;

    dispatch(query);
  });

  // ---------------------------------------------------------
  // Keyboard shortcut
  // ---------------------------------------------------------

  el.input.addEventListener("keydown", (e) => {
    if (
      (e.metaKey || e.ctrlKey) &&
      e.key === "Enter"
    ) {
      e.preventDefault();
      el.form.requestSubmit();
    }
  });

  // ---------------------------------------------------------
  // Example chips
  // ---------------------------------------------------------

  el.chips.addEventListener("click", (e) => {
    const btn = e.target.closest(".chip");

    if (!btn) return;

    el.input.value = btn.dataset.example;
    el.input.focus();
  });

  // ---------------------------------------------------------
  // New campaign
  // ---------------------------------------------------------

  el.newTripBtn.addEventListener(
    "click",
    resetThread
  );

  // ---------------------------------------------------------
  // Scroll cue
  // ---------------------------------------------------------

  el.scrollCue.addEventListener("click", () => {
    const next = [
      el.pipeline,
      el.results,
      el.errorBanner,
    ].find(
      (section) =>
        section && !section.hidden
    );

    const targetTop = next
      ? next.offsetTop - 24
      : (
          document.querySelector(".hero")
            ?.offsetHeight ||
          window.innerHeight
        );

    window.scrollTo({
      top: targetTop,
      behavior: "smooth",
    });
  });

  // ---------------------------------------------------------
  // Retry
  // ---------------------------------------------------------

  el.errorRetry.addEventListener("click", () => {
    if (lastQuery) {
      dispatch(lastQuery);
    }
  });

  // ---------------------------------------------------------
  // PDF download
  // ---------------------------------------------------------

  async function downloadPdf() {
    if (!lastPayload || !lastPayload.answer) {
      return;
    }

    const originalLabel =
      el.downloadPdfLabel.textContent;

    el.downloadPdfBtn.disabled = true;
    el.downloadPdfLabel.textContent = "Preparing…";

    try {
      const res = await fetch(
        "/api/travel/pdf",
        {
          method: "POST",

          headers: {
            "Content-Type": "application/json",
          },

          body: JSON.stringify({
            answer: lastPayload.answer,
            thread_id: lastPayload.thread_id,
          }),
        }
      );

      if (!res.ok) {
        let message = `PDF export failed (HTTP ${res.status}).`;

        try {
          const errBody = await res.json();
          if (errBody?.error) {
            message =
              typeof errBody.error === "string"
                ? errBody.error
                : errBody.error.message || message;
          }
        } catch {
          // Body wasn't JSON — keep the generic message above.
        }

        throw new Error(message);
      }

      const blob = await res.blob();

      const url = URL.createObjectURL(blob);

      const a = document.createElement("a");

      a.href = url;
      a.download =
        "shogun-travel-briefing.pdf";

      document.body.appendChild(a);

      a.click();

      a.remove();

      URL.revokeObjectURL(url);
    } catch (err) {
      renderError(
        err && err.message
          ? err.message
          : "Could not generate the PDF."
      );
    } finally {
      el.downloadPdfBtn.disabled = false;

      el.downloadPdfLabel.textContent =
        originalLabel;
    }
  }

  el.downloadPdfBtn.addEventListener(
    "click",
    downloadPdf
  );

  // ---------------------------------------------------------
  // Init
  // ---------------------------------------------------------

  renderHistory();
  getThreadId();

})();