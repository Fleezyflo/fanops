// Progressive enhancement (Track C): activate a transcript line (CLICK or, for keyboard users, Enter/
  // Space on the focused .seg) to fill the first empty pick row in the SAME card with that segment's
  // start/end, then focus its reason. No-JS still works by typing. P6: the .seg is tabindex=0 role=button,
  // so this is reachable without a mouse.
  function fillFromSeg(seg) {
    var card = seg.closest('.card'); if (!card) return;
    var rows = card.querySelectorAll('.pick-row');
    for (var i = 0; i < rows.length; i++) {
      var s = rows[i].querySelector('[name=pick_start]');
      if (s && !s.value) {
        s.value = seg.dataset.start;
        var en = rows[i].querySelector('[name=pick_end]'); if (en) en.value = seg.dataset.end;
        var rsn = rows[i].querySelector('[name=pick_reason]'); if (rsn) rsn.focus();
        break;
      }
    }
  }
  document.addEventListener('click', function (e) {
    var seg = e.target.closest('.seg'); if (seg) fillFromSeg(seg);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var seg = e.target.closest('.seg'); if (!seg) return;
    e.preventDefault();            // Space would otherwise scroll the page
    fillFromSeg(seg);
  });