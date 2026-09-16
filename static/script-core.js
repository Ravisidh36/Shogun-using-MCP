/* Shogun core compatibility bridge. Loads the known-good full UI implementation. */
(() => {
  const script = document.createElement("script");
  script.src = "https://raw.githubusercontent.com/Ravisidh36/Shogun-using-MCP/29d7a3a3fa24b5a0f1520d45f29283d785617c43/static/script.js";
  script.async = false;
  script.onerror = () => console.error("Shogun core UI failed to load.");
  document.head.appendChild(script);
})();
