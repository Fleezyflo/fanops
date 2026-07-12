/* U7 Schedule — dialog scheduling + calendar drag (same /schedule/move/<post_id> route). */
(function () {
  const body = document.getElementById('schedule-body');
  if (!body) return;
  const dialog = document.getElementById('schedule-dialog');
  const form = document.getElementById('schedule-dialog-form');
  const postInput = document.getElementById('schedule-dialog-post');
  const dateInput = document.getElementById('schedule-dialog-date');
  const timeInput = document.getElementById('schedule-dialog-time');
  const clipLabel = document.getElementById('schedule-dialog-clip');
  const cancelBtn = document.getElementById('schedule-dialog-cancel');
  let dragChip = null;

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
    if (!dialog) return;
    postInput.value = opts.postId || '';
    clipLabel.textContent = opts.caption || '';
    if (opts.date) dateInput.value = opts.date;
    if (opts.time) timeInput.value = opts.time;
    dialog.showModal();
  }

  body.addEventListener('click', function (e) {
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

  if (cancelBtn) cancelBtn.addEventListener('click', function () { dialog.close(); });
  if (form) form.addEventListener('submit', function (e) {
    e.preventDefault();
    const dt = dateInput.value + 'T' + timeInput.value;
    movePost(postInput.value, dt);
    dialog.close();
  });

  body.addEventListener('dragstart', function (e) {
    const chip = e.target.closest('.schedule-cal-chip--drag');
    if (!chip) return;
    dragChip = chip;
    e.dataTransfer.setData('text/plain', chip.dataset.postId || '');
    e.dataTransfer.effectAllowed = 'move';
  });
  body.addEventListener('dragover', function (e) {
    const day = e.target.closest('.schedule-cal-day[data-date]:not([data-past])');
    if (!day || !dragChip) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  });
  body.addEventListener('drop', function (e) {
    const day = e.target.closest('.schedule-cal-day[data-date]:not([data-past])');
    if (!day || !dragChip) return;
    e.preventDefault();
    const hm = dragChip.dataset.timeHm || '12:00';
    const localDt = day.dataset.date + 'T' + hm;
    movePost(dragChip.dataset.postId, localDt);
    dragChip = null;
  });
  body.addEventListener('dragend', function () { dragChip = null; });
})();
