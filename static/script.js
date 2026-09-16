/* Shogun UI compatibility layer. Loads the verified core script same-origin,
   then applies the dynamic Mission Control and Enter-to-dispatch behavior. */
(() => {
  "use strict";

  const core = document.createElement("script");
  core.src = "/static/script-core.js?v=1";
  core.async = false;

  core.onload = () => {
    const style = document.createElement("style");
    style.textContent = `
      .pipeline-node.skipped { display: none !important; }
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

    // Enter dispatches. Shift+Enter keeps multiline input available.
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.shiftKey) return;
      if (event.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      form.requestSubmit();
    });

    // Only show agents actually selected by the backend. Before routing
    // arrives, show Supervisor alone rather than fake specialist activity.
    let routingArrived = false;
    const syncPipeline = () => {
      const nodes = pipeline.querySelectorAll(".pipeline-node");
      if ([...nodes].some(n => n.classList.contains("skipped"))) {
        routingArrived = true;
      }

      nodes.forEach((node) => {
        if (node.classList.contains("skipped")) {
          node.style.display = "none";
        } else if (!routingArrived && node.dataset.agent !== "supervisor") {
          node.style.display = "none";
        } else {
          node.style.display = "";
        }
      });
    };

    syncPipeline();
    new MutationObserver(syncPipeline).observe(pipeline, {
      subtree: true,
      attributes: true,
      attributeFilter: ["class"]
    });
  };

  core.onerror = () => {
    console.error("Shogun core UI script failed to load.");
  };

  document.head.appendChild(core);
})();
