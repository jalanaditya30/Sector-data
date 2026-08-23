/* ---------------------------------------------------------------------------
   bounce.js — makes every button on the page bounce around, and remembers
   whether you want it to.

   Each button gets its own amplitude, period, phase and rotation, so the row
   scatters instead of pulsing in unison — "here and there", not a chorus line.
   The values are derived from the button's index rather than Math.random(), so
   a given button bounces the same way on every reload; only the page's own
   button order can change it.

   Everything is CSS custom properties plus one body class: the animation lives
   in bounce.css and the class is the whole on/off switch.

   Contract with the page:
     .bbtn or <button>   becomes bouncy
     [data-bounce-toggle] becomes the on/off control (label written here)
     [data-no-bounce]     opts a button out
--------------------------------------------------------------------------- */
(function () {
  "use strict";

  var KEY = "sector-data:bounce";
  var reduceMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // localStorage throws outright in some embedded/private contexts — a
  // preference that cannot be read is not a reason to break the page.
  function stored() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }
  function remember(on) {
    try { localStorage.setItem(KEY, on ? "on" : "off"); } catch (e) { /* fine */ }
  }

  // Deterministic 0..1 from an integer: the usual sine hash. Good enough to
  // scatter a dozen buttons, and stable across reloads unlike Math.random().
  function noise(i, salt) {
    var x = Math.sin((i + 1) * 12.9898 + (salt || 0) * 78.233) * 43758.5453;
    return x - Math.floor(x);
  }
  function between(lo, hi, i, salt) { return lo + (hi - lo) * noise(i, salt); }

  function bouncify(el, i) {
    el.classList.add("bouncy");
    el.style.setProperty("--bx", between(6, 13, i, 1).toFixed(1) + "px");
    el.style.setProperty("--by", between(4, 9, i, 2).toFixed(1) + "px");
    el.style.setProperty("--rot", between(1.2, 3.4, i, 3).toFixed(2) + "deg");
    el.style.setProperty("--dur", between(1.7, 3.1, i, 4).toFixed(2) + "s");
    // negative delay: every button is already mid-flight on load, at its own
    // point in the cycle, so they never start out synchronised
    el.style.setProperty("--delay", "-" + between(0, 3, i, 5).toFixed(2) + "s");
  }

  function apply(on) {
    document.body.classList.toggle("bounce-on", on);
    var toggles = document.querySelectorAll("[data-bounce-toggle]");
    for (var i = 0; i < toggles.length; i++) {
      toggles[i].textContent = on ? "bounce: on" : "bounce: off";
      toggles[i].setAttribute("aria-pressed", on ? "true" : "false");
      toggles[i].title = on
        ? "Buttons are bouncing. Click to hold them still."
        : "Buttons are still. Click to set them bouncing.";
    }
  }

  function init() {
    var buttons = document.querySelectorAll("button, .bbtn");
    var n = 0;
    for (var i = 0; i < buttons.length; i++) {
      if (!buttons[i].hasAttribute("data-no-bounce")) bouncify(buttons[i], n++);
    }

    // The motion setting decides the default; an explicit choice overrides it.
    var saved = stored();
    apply(saved === null ? !reduceMotion : saved === "on");

    document.addEventListener("click", function (ev) {
      var t = ev.target.closest && ev.target.closest("[data-bounce-toggle]");
      if (!t) return;
      var on = !document.body.classList.contains("bounce-on");
      apply(on);
      remember(on);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
