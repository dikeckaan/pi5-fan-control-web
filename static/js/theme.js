// Loaded in <head> so the saved theme is applied before the first paint.
(function () {
  "use strict";
  const KEY = "pifan.theme";
  const media = window.matchMedia("(prefers-color-scheme: dark)");

  function saved() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    document.dispatchEvent(new CustomEvent("themechange", { detail: theme }));
  }

  apply(saved() || (media.matches ? "dark" : "light"));

  media.addEventListener("change", (e) => {
    if (!saved()) apply(e.matches ? "dark" : "light");
  });

  window.Theme = {
    toggle() {
      const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      try { localStorage.setItem(KEY, next); } catch (e) { /* private mode */ }
      apply(next);
    },
  };
})();
