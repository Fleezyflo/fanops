/* Review selection dock + source-select helper. */
(function () {
  function body() { return document.getElementById("review-body"); }
  function boxes() { const b = body(); return b ? b.querySelectorAll('input[name="ids"]') : []; }
  function count() {
    let n = 0; boxes().forEach(b => { if (b.checked) n++; });
    const el = document.getElementById("review-sel-count");
    if (el) el.textContent = n ? n + " selected" : "Tick posts below, then approve or reject";
    const bar = document.getElementById("review-action-dock");
    if (bar) bar.classList.toggle("has-selection", n > 0);
    document.querySelectorAll("[data-bulk-action]").forEach(b => { b.disabled = n === 0; });
  }
  document.addEventListener("change", e => { if (e.target && e.target.name === "ids") count(); });
  document.addEventListener("click", e => {
    const t = e.target.closest("[data-review-action]");
    if (!t) return;
    const b = body(); if (!b) return;
    const act = t.dataset.reviewAction;
    if (act === "select-all") boxes().forEach(x => { x.checked = true; });
    else if (act === "clear") boxes().forEach(x => { x.checked = false; });
    else if (act === "select-batch") {
      const bid = t.dataset.batchId || "";
      b.querySelectorAll('input[name="ids"][data-batch="' + bid + '"]').forEach(x => { x.checked = true; });
    } else if (act === "select-source") {
      const sk = t.dataset.sourceKey || "";
      b.querySelectorAll('input[name="ids"][data-source="' + sk + '"]').forEach(x => { x.checked = true; });
    }
    count();
  });
  document.body.addEventListener("htmx:afterSwap", e => {
    if (e.detail.target && e.detail.target.id === "review-body") count();
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", count);
  else count();
})();
