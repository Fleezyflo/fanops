/* U7 Schedule — dialog scheduling + calendar drag (same /schedule/move/<post_id> route).
   Listeners stay on document: htmx replaces #schedule-body (outerHTML) and drops
   listeners that were bound to that node. */
(function () {
  function panel() { return document.getElementById('schedule-body'); }
  function byId(id) { return document.getElementById(id); }

  function movePost(postId, localDt) {
    if (!postId || !localDt) return;
    const url = '/schedule/move/' + encodeURIComponent(postId) + window.location.search;
    if (typeof htmx !== 'undefined') {
      htmx.ajax('POST', url, {target: '#schedule-body', swap: 'outerHTML', values: {new_time: localDt}});
    } else {
      const f = document.createElement('form');
      f.method = 'POST'; f.action = url;
      const inp = document.createElement('input');
      inp.type = 'hidden'; inp.name = 'new_time'; inp.value = localDt;
      f.appendChild(inp); document.body.appendChild(f); f.submit();
    }
  }

  function openDialog(opts) {
    const dialog = byId('schedule-dialog');
    if (!dialog) return;
    const postInput = byId('schedule-dialog-post');
    const dateInput = byId('schedule-dialog-date');
    const timeInput = byId('schedule-dialog-time');
    const clipLabel = byId('schedule-dialog-clip');
    postInput.value = opts.postId || '';
    clipLabel.textContent = opts.caption || '';
    if (opts.date) dateInput.value = opts.date;
    if (opts.time) timeInput.value = opts.time;
    dialog.showModal();
  }

  document.addEventListener('click', function (e) {
    const body = panel();
    if (!body || !e.target || typeof e.target.closest !== 'function' || !body.contains(e.target)) return;
    if (e.target.closest('#schedule-dialog-cancel')) {
      const dialog = byId('schedule-dialog');
      if (dialog) dialog.close();
      return;
    }
    const btn = e.target.closest('.schedule-dialog-open');
    if (btn) {
      openDialog({postId: btn.dataset.postId, caption: btn.dataset.caption, time: '12:00'});
      return;
    }
    const day = e.target.closest('.schedule-cal-day[data-date]:not([data-past])');
    if (day && !e.target.closest('.schedule-cal-chip')) {
      const row = body.querySelector('.schedule-bucket-list .schedule-bucket-row[data-post-id]');
      if (row) {
        openDialog({postId: row.dataset.postId, caption: row.querySelector('.sched-caption')?.textContent?.trim() || '',
                    date: day.dataset.date, time: '12:00'});
      }
    }
  });

  document.addEventListener('submit', function (e) {
    const form = byId('schedule-dialog-form');
    if (!form || e.target !== form) return;
    e.preventDefault();
    const dt = byId('schedule-dialog-date').value + 'T' + byId('schedule-dialog-time').value;
    movePost(byId('schedule-dialog-post').value, dt);
    const dialog = byId('schedule-dialog');
    if (dialog) dialog.close();
  });

  let dragChip = null;
  document.addEventListener('dragstart', function (e) {
    const body = panel();
    if (!body || !e.target || typeof e.target.closest !== 'function' || !body.contains(e.target)) return;
    const chip = e.target.closest('.schedule-cal-chip--drag');
    if (!chip) return;
    dragChip = chip;
    e.dataTransfer.setData('text/plain', chip.dataset.postId || '');
    e.dataTransfer.effectAllowed = 'move';
  });
  document.addEventListener('dragover', function (e) {
    const body = panel();
    if (!body || !dragChip || !e.target || typeof e.target.closest !== 'function') return;
    const day = e.target.closest('.schedule-cal-day[data-date]:not([data-past])');
    if (!day || !body.contains(day)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  });
  document.addEventListener('drop', function (e) {
    const body = panel();
    if (!body || !dragChip || !e.target || typeof e.target.closest !== 'function') return;
    const day = e.target.closest('.schedule-cal-day[data-date]:not([data-past])');
    if (!day || !body.contains(day)) return;
    e.preventDefault();
    const hm = dragChip.dataset.timeHm || '12:00';
    movePost(dragChip.dataset.postId, day.dataset.date + 'T' + hm);
    dragChip = null;
  });
  document.addEventListener('dragend', function () { dragChip = null; });
})();
