// drawer.js — Slice 3: the persona editor slide-out as an ACCESSIBLE modal dialog. CSS owns the slide; this
// owns the a11y CSS can't express: focus into the dialog, trap Tab inside it, `inert` the background (rail +
// workspace), ESC + backdrop + Close button dismiss, and focus returns to the trigger. The drawer body is an
// htmx fragment swapped into #persona-drawer; we open on its settle and close when the action that re-renders
// #personas-panel completes (Save/Delete) or any dismiss affordance fires. Justified deviation from the
// near-pure-CSS rule: an accessible modal's focus-trap/ESC/inert genuinely cannot be done in CSS alone.
(function () {
  var drawer = document.getElementById("persona-drawer");
  if (!drawer) return;
  var backdrop = document.querySelector(".drawer-backdrop");
  // Background to inert while the modal is open — siblings of the body-level drawer, never its ancestors.
  var bg = [document.getElementById("rail-nav"), document.querySelector(".workspace")].filter(Boolean);
  var trigger = null;

  function focusable() {
    // :not([type=hidden]) keeps the form's hidden id input out of the tab cycle explicitly (offsetParent also
    // excludes it, but the selector is the clearer guard); offsetParent!==null drops anything display:none/detached.
    var sel = 'a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
    return Array.prototype.slice.call(drawer.querySelectorAll(sel)).filter(function (el) { return el.offsetParent !== null; });
  }
  function isOpen() { return drawer.classList.contains("open"); }

  function focusHeading() {
    var head = drawer.querySelector("#persona-drawer-heading");
    (head || drawer).focus();
  }
  function open() {
    drawer.classList.add("open");
    if (backdrop) backdrop.hidden = false;
    // Focus the heading BEFORE inerting the background. The trigger lives inside .workspace; if we inert while it
    // still holds focus the browser SILENTLY blurs it to <body> (no focusout fires, so the net can't catch it).
    focusHeading();
    bg.forEach(function (el) { el.inert = true; });
    // htmx runs its OWN focus handling on a later tick after settle, which knocks focus to <body> (it fires no
    // catchable event, so the focusin/focusout nets miss it). Re-assert on a couple of deferred ticks so the
    // authoritative focus lands AFTER htmx is done; the guard stops if focus is already inside the drawer.
    var reassert = function () { if (isOpen() && !drawer.contains(document.activeElement)) focusHeading(); };
    setTimeout(reassert, 0);
    setTimeout(reassert, 120);
  }
  function close() {
    if (!isOpen()) return;
    drawer.classList.remove("open");
    if (backdrop) backdrop.hidden = true;
    bg.forEach(function (el) { el.inert = false; });
    drawer.innerHTML = "";
    // Return focus to the trigger (WCAG 2.4.3). Defer + re-assert: emptying the drawer and un-inerting blur the
    // heading to <body>, and that loss can override an immediately-synchronous focus — same race as open().
    var back = (trigger && document.contains(trigger)) ? trigger : document.getElementById("main-content");
    trigger = null;
    if (!back) return;
    back.focus();
    var reassert = function () { if (!isOpen() && document.activeElement !== back) back.focus(); };
    setTimeout(reassert, 0);
    setTimeout(reassert, 120);
  }

  // Capture the trigger at click time (capture phase, before htmx) — the deterministic source for focus-return,
  // independent of what htmx puts on the settle event or whether the click left the button focused.
  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest(".persona-edit-open");
    if (t) trigger = t;
  }, true);
  // htmx settled the drawer body in -> open the modal.
  document.body.addEventListener("htmx:afterSettle", function (e) {
    if (e.detail && e.detail.target === drawer && drawer.children.length) open();
  });
  // Save/Delete from inside the drawer re-render #personas-panel -> that swap means the action finished -> close.
  // This fires for ANY #personas-panel swap (corpus add/remove/research, account-connect too), which is SAFE only
  // because while the drawer is open .workspace (which contains #personas-panel and every card form) is inert, so
  // no card action can run — the drawer is the only thing that can swap the panel. If the inert scope ever
  // narrows, gate this on a drawer-origin signal (e.g. an HX-Trigger header from /personas/edit + /delete).
  // (The live compose preview posts to #persona-compose-<id> INSIDE the drawer, so it never trips this.)
  document.body.addEventListener("htmx:afterSwap", function (e) {
    if (e.detail && e.detail.target && e.detail.target.id === "personas-panel") close();
  });
  // ESC dismisses; Tab is trapped within the dialog.
  document.addEventListener("keydown", function (e) {
    if (!isOpen()) return;
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key !== "Tab") return;
    var f = focusable();
    if (!f.length) { e.preventDefault(); return; }
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  // Focus-trap safety net: while open, any focus that escapes the drawer is pulled straight back to the heading.
  // Two events, because they cover different escapes: focusin catches focus LANDING on a real control outside the
  // drawer; focusout catches focus LEAVING toward nothing/<body> (htmx's post-settle focus handling throws it
  // there once the trigger is inerted — and a move to <body> fires no focusin). The focusout re-check is deferred
  // a tick so the escape settles first. Focus moves WITHIN the drawer (Tab between fields) pass through untouched.
  document.addEventListener("focusin", function (e) {
    if (isOpen() && !drawer.contains(e.target)) focusHeading();
  });
  document.addEventListener("focusout", function (e) {
    if (!isOpen() || (e.relatedTarget && drawer.contains(e.relatedTarget))) return;
    setTimeout(function () { if (isOpen() && !drawer.contains(document.activeElement)) focusHeading(); }, 0);
  });
  // Dismiss affordances: backdrop click + any [data-drawer-close] (the × and Close buttons in the body).
  if (backdrop) backdrop.addEventListener("click", close);
  drawer.addEventListener("click", function (e) {
    if (e.target.closest("[data-drawer-close]")) { e.preventDefault(); close(); }
  });
})();
