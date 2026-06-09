// Auto-fit the architecture diagram iframes to their content so there are
// never scrollbars. The diagrams are same-origin self-contained HTML, so we
// can read their rendered height and size the iframe to match. Re-runs on
// each iframe load and on window resize (the diagrams reflow with width).
(function () {
  function fit(frame) {
    try {
      var doc = frame.contentDocument || frame.contentWindow.document;
      if (doc && doc.documentElement) {
        // +2px guards against sub-pixel rounding that can reintroduce a
        // 1px scrollbar.
        frame.style.height = doc.documentElement.scrollHeight + 2 + "px";
      }
    } catch (e) {
      /* cross-origin or not yet loaded — leave the CSS fallback height */
    }
  }

  function frames() {
    return document.querySelectorAll("iframe.diagram");
  }

  function fitAll() {
    frames().forEach(fit);
  }

  function init() {
    frames().forEach(function (frame) {
      frame.setAttribute("scrolling", "no");
      frame.addEventListener("load", function () {
        fit(frame);
      });
      fit(frame);
    });
    window.addEventListener("resize", fitAll);
  }

  if (document.readyState !== "loading") {
    init();
  } else {
    document.addEventListener("DOMContentLoaded", init);
  }
})();
