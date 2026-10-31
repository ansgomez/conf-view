// Loaded (blocking) in <head> so the right theme is applied before first paint
// and the page never flashes the wrong colours.
//
// The visitor's choice lives in localStorage "cv-theme": "light", "dark", or
// absent (= follow the system setting). <html> gets two attributes:
//   data-theme       the theme actually shown: "light" | "dark"
//   data-theme-mode  the visitor's choice:     "light" | "dark" | "system"
// app.js reuses window.cvTheme to wire up the toggle button.
(function () {
  var KEY = "cv-theme";
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function stored() {
    try {
      var v = localStorage.getItem(KEY);
      return v === "light" || v === "dark" ? v : "system";
    } catch (e) { return "system"; }
  }
  function resolve(mode) {
    if (mode === "light" || mode === "dark") return mode;
    return mq && mq.matches ? "dark" : "light";
  }
  function apply(mode) {
    var root = document.documentElement;
    var theme = resolve(mode);
    root.setAttribute("data-theme", theme);
    root.setAttribute("data-theme-mode", mode);
    var metas = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < metas.length; i++) metas[i].setAttribute("content", theme === "dark" ? "#14161f" : "#f7f8fa");
    return theme;
  }
  function save(mode) {
    try {
      if (mode === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, mode);
    } catch (e) {}
  }

  window.cvTheme = { stored: stored, resolve: resolve, apply: apply, save: save, mq: mq };
  apply(stored());
})();
