/* ---------------------------------------------------------------------------
   theme.js — day/night for the three boards.

   The choice lives on <html data-theme>, which every page's stylesheet keys its
   dark palette off. It is applied by a tiny inline script in each <head> so the
   page never paints light and then flips; this file only wires the toggle and
   remembers the answer.

   The dialog's candlestick chart deliberately ignores all of this and stays
   dark — see the note in the quiet board's stylesheet.
--------------------------------------------------------------------------- */
(function () {
  "use strict";
  var KEY = "sector-data:theme";

  function remember(t) { try { localStorage.setItem(KEY, t); } catch (e) { /* private mode */ } }

  function apply(t) {
    document.documentElement.setAttribute("data-theme", t);
    var b = document.getElementById("theme");
    if (b) {
      var dark = t === "dark";
      b.setAttribute("aria-pressed", dark ? "true" : "false");
      var ic = b.querySelector(".ic");
      if (ic) ic.textContent = dark ? "☾" : "☀";
      b.title = dark ? "Night mode — click for day" : "Day mode — click for night";
    }
    // the heatmap paints its cell colours in JS, so it needs telling
    window.dispatchEvent(new CustomEvent("themechange", { detail: t }));
  }

  function init() {
    apply(document.documentElement.getAttribute("data-theme") || "light");
    var b = document.getElementById("theme");
    if (!b) return;
    b.addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "dark"
        ? "light" : "dark";
      apply(next);
      remember(next);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();
