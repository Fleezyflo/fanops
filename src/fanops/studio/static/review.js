/* Review selection dock + focus-mode keyboard shortcuts. */
(function () {
  function body() { return document.getElementById("review-body"); }
  function focusRoot() { return document.getElementById("review-focus"); }
  function boxes() { const b = body(); return b ? b.querySelectorAll('input[name="ids"]') : []; }
  function count() {
    let n = 0; boxes().forEach(b => { if (b.checked) n++; });
    const el = document.getElementById("review-sel-count");
    if (el) el.textContent = n ? n + " selected" : "Tick posts below, then approve or reject";
    const bar = document.getElementById("review-action-dock");
    if (bar) bar.classList.toggle("has-selection", n > 0);
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
    }
    count();
  });
  document.addEventListener("keydown", e => {
    if (!focusRoot() || e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (e.target && e.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (e.target && e.target.isContentEditable)) return;
    const root = focusRoot();
    if (e.key === "a" || e.key === "A") {
      const btn = root.querySelector(".focus-actions .primary");
      if (btn) { e.preventDefault(); btn.click(); }
    } else if (e.key === "r" || e.key === "R") {
      const btn = root.querySelector(".focus-actions .surface-reject");
      if (btn) { e.preventDefault(); btn.click(); }
    } else if (e.key === "n" || e.key === "N" || e.key === "ArrowRight") {
      const next = root.querySelector('.focus-actions a[href*="fi="]');
      const links = root.querySelectorAll(".focus-actions a.button");
      const nxt = links.length ? links[links.length - 1] : null;
      if (nxt && nxt.textContent.indexOf("Next") >= 0) { e.preventDefault(); nxt.click(); }
    } else if (e.key === "p" || e.key === "P" || e.key === "ArrowLeft") {
      const links = root.querySelectorAll(".focus-actions a.button");
      if (links.length && links[0].textContent.indexOf("Previous") >= 0) { e.preventDefault(); links[0].click(); }
    }
  });
  document.body.addEventListener("htmx:afterSwap", e => {
    if (e.detail.target && e.detail.target.id === "review-body") count();
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", count);
  else count();
})();
