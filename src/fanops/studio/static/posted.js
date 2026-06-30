(function () {
  function body() { return document.getElementById("posted-body"); }
  function boxes() { const b = body(); return b ? b.querySelectorAll('input[name="ids"]') : []; }
  function count() {
    let n = 0; boxes().forEach(b => { if (b.checked) n++; });
    const el = document.getElementById("posted-sel-count");
    if (el) el.textContent = n ? n + " selected" : "Select failed posts to recover";
    const dock = document.getElementById("posted-action-dock");
    if (dock) dock.classList.toggle("has-selection", n > 0);
  }
  document.addEventListener("change", e => { if (e.target && e.target.name === "ids") count(); });
  document.addEventListener("click", e => {
    const t = e.target.closest("[data-posted-action]");
    if (!t || t.dataset.postedAction !== "clear") return;
    boxes().forEach(x => { x.checked = false; }); count();
  });
  document.body.addEventListener("htmx:afterSwap", e => {
    if (e.detail.target && e.detail.target.id === "posted-body") count();
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", count);
  else count();
})();
