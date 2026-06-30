// Gates: transcript click-to-fill + dynamic moment pick rows.
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
    var add = e.target.closest('[data-add-pick]');
    if (add) {
      var box = add.closest('form').querySelector('.pick-rows');
      if (!box) return;
      var row = box.querySelector('.pick-row');
      if (row) box.appendChild(row.cloneNode(true));
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var seg = e.target.closest('.seg'); if (!seg) return;
    e.preventDefault();
    fillFromSeg(seg);
  });
