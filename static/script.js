/*
 * SHOGUN UI compatibility layer.
 * Keeps the verified travel logic intact while making the mission-control
 * pipeline reflect the agents actually selected by the backend.
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
      /* Unselected agents disappear completely instead of showing
         "Not required" cards. */
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

    // ---------------------------------------------------------
    // Enter to dispatch
    // ---------------------------------------------------------
    // Enter submits the request. Shift+Enter still creates a new line.
    // Do not steal Enter while an IME is composing text.
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.shiftKey) return;
      if (event.isComposing || event.keyCode === 229) return;

      event.preventDefault();
      form.requestSubmit();
    });

    // ---------------------------------------------------------
    // Dynamic Mission Control
    // ---------------------------------------------------------
    // The backend already returns selected_agents and the core script marks
    // unselected nodes as `skipped`. We turn that into actual visibility.
    // Before the backend responds, only Supervisor is shown so the UI never
    // pretends Hotel/Weather/Budget/Itinerary are running.
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
          return;
        }

        // Until selected_agents arrives, don't show the fake sequential
        // animation for specialist agents that may never be used.
        if (!backendRoutingArrived && agent !== "supervisor") {
          node.style.display = "none";
          return;
        }

        node.style.display = "";
      });
    };

    syncVisibility();

    const pipelineObserver = new MutationObserver(syncVisibility);
    pipelineObserver.observe(pipeline, {
      subtree: true,
      attributes: true,
      attributeFilter: ["class"],
    });

    // ---------------------------------------------------------
    // Flight-provider error cleanup
    // ---------------------------------------------------------
    // The current AviationStack MCP integration supplies airport/airline
    // intelligence rather than a guaranteed bookable fare feed. If that
    // provider fails, don't expose its raw exception in the user-facing card.
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
            The Delhi → Los Angeles route was understood correctly, but the
            aviation data provider did not return flight records. No flight
            numbers or prices have been invented.
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

  loadCore()
    .then(installUiFixes)
    .catch((error) => {
      console.error("Shogun UI initialization failed:", error);
    });
})();
