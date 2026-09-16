/*
 * SHOGUN UI loader + interaction fixes.
 *
 * The full application script is pinned to the last verified application
 * commit so these UI-only fixes can be layered on without changing the
 * existing travel logic.
 */
(() => {
  "use strict";

  const CORE_URL =
    "https://raw.githubusercontent.com/Ravisidh36/Shogun-using-MCP/29d7a3a3fa24b5a0f1520d45f29283d785617c43/static/script.js";

  function loadCore() {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = CORE_URL;
      script.async = false;
      script.onload = resolve;
      script.onerror = () => reject(new Error("Unable to load Shogun core UI script."));
      document.head.appendChild(script);
    });
  }

  function installUiFixes() {
    const style = document.createElement("style");
    style.id = "shogun-ui-fixes";
    style.textContent = `
      /* Only agents selected by the backend remain visible. */
      .pipeline-node.skipped {
        display: none !important;
      }

      /* Keep the active pipeline compact when only one specialist is used. */
      #pipeline {
        grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
      }
    `;
    document.head.appendChild(style);

    const form = document.getElementById("travel-form");
    const input = document.getElementById("query-input");
    const pipeline = document.getElementById("pipeline");

    if (!form || !input) return;

    // Enter submits; Shift+Enter keeps the textarea multiline.
    // IME composition is respected so Enter can still confirm candidates.
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.shiftKey) return;
      if (event.isComposing || event.keyCode === 229) return;

      event.preventDefault();
      form.requestSubmit();
    });

    // The core script already marks backend-unselected agents as `skipped`.
    // Convert that state into actual visibility instead of merely showing
    // every agent with a "Not required" status.
    if (pipeline) {
      const syncVisibility = () => {
        pipeline.querySelectorAll(".pipeline-node").forEach((node) => {
          node.style.display = node.classList.contains("skipped") ? "none" : "";
        });
      };

      syncVisibility();

      const observer = new MutationObserver(syncVisibility);
      observer.observe(pipeline, {
        subtree: true,
        attributes: true,
        attributeFilter: ["class"],
      });
    }

    // Make the flight panel graceful when the aviation MCP/provider is down.
    // We do not fabricate flight data; we replace the raw backend error with
    // a clear service-status message while preserving successful results.
    const results = document.getElementById("results");
    if (results) {
      const flightPanel = document.getElementById("panel-flights");
      if (flightPanel) {
        const flightObserver = new MutationObserver(() => {
          const text = flightPanel.textContent || "";
          if (!/flight information unavailable:/i.test(text)) return;

          const message = document.createElement("div");
          message.className = "panel-empty";
          message.innerHTML = `
            <p class="panel-empty-title">Live flight data is temporarily unavailable</p>
            <p class="panel-empty-detail">
              The route was understood correctly, but the aviation data provider did not return flight records.
              No flight numbers or prices have been invented.
            </p>
          `;

          flightPanel.replaceChildren(message);
        });

        flightObserver.observe(flightPanel, {
          childList: true,
          subtree: true,
          characterData: true,
        });
      }
    }
  }

  loadCore()
    .then(installUiFixes)
    .catch((error) => {
      console.error("Shogun UI initialization failed:", error);
    });
})();
