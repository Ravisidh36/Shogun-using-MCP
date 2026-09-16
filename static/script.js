/*
 * SHOGUN UI compatibility layer.
 * Loads the verified application core from the known-good commit, then
 * applies the Mission Control and Enter-to-dispatch UI behavior.
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
      .pipeline-node.skipped {
        display: none !important;
      }

      #pipeline .pipeline-track {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
      }
    `;
    document.head.appendChild(style);

    const form = document.getElementById("travel-form");
    const input = document.getElementById("query-input");
    const pipeline = document.getElementById("pipeline");

    if (!form || !input || !pipeline) return;

    // Enter submits. Shift+Enter keeps multiline input available.
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.shiftKey) return;
      if (event.isComposing || event.keyCode === 229) return;

      event.preventDefault();
      form.requestSubmit();
    });

    // Until the backend returns routing state, show Supervisor only.
    let backendRoutingArrived = false;

    const syncVisibility = () => {
      const nodes = pipeline.querySelectorAll(".pipeline-node");
      const hasRoutingState = Array.from(nodes).some((node) =>
        node.classList.contains("skipped")
      );

      if (hasRoutingState) {
        backendRoutingArrived = true;
      }

      nodes.forEach((node) => {
        const isSkipped = node.classList.contains("skipped");
        const agent = node.dataset.agent;

        if (isSkipped) {
          node.style.display = "none";
        } else if (!backendRoutingArrived && agent !== "supervisor") {
          node.style.display = "none";
        } else {
          node.style.display = "";
        }
      });
    };

    syncVisibility();

    new MutationObserver(syncVisibility).observe(pipeline, {
      subtree: true,
      attributes: true,
      attributeFilter: ["class"],
    });

    // Replace raw provider exceptions with a clean user-facing message.
    const flightPanel = document.getElementById("panel-flights");

    if (flightPanel) {
      new MutationObserver(() => {
        const text = flightPanel.textContent || "";
        if (!/flight information unavailable:/i.test(text)) return;

        const message = document.createElement("div");
        message.className = "panel-empty";
        message.innerHTML = `
          <p class="panel-empty-title">Live flight data is temporarily unavailable</p>
          <p class="panel-empty-detail">
            The requested route was understood correctly, but the aviation
            data provider did not return flight records. No flight numbers
            or prices have been invented.
          </p>
        `;

        flightPanel.replaceChildren(message);
      }).observe(flightPanel, {
        childList: true,
        subtree: true,
        characterData: true,
      });
    }
  }

  loadCore()
    .then(installUiFixes)
    .catch((error) => {
      console.error("Shogun UI initialization failed:", error);
    });
})();
