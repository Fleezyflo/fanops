/* S02: chunked resumable upload for files larger than data-upload-max-bytes on #run-upload-form. */
(function () {
  var CHUNK = 64 * 1024 * 1024;   // 64 MB — well under typical FANOPS_UPLOAD_MAX_MB per-chunk ceiling
  var LS_KEY = "fanops.upload.resume";

  function form() { return document.getElementById("run-upload-form"); }
  function progress() { return document.getElementById("upload-progress"); }
  function bar() { return document.getElementById("upload-progress-bar"); }
  function label() { return document.getElementById("upload-progress-label"); }

  function maxBytes(f) {
    var el = form();
    if (!el) return Infinity;
    var n = parseInt(el.getAttribute("data-upload-max-bytes") || "0", 10);
    return n > 0 ? n : Infinity;
  }

  function needsChunked(files) {
    var cap = maxBytes();
    for (var i = 0; i < files.length; i++) { if (files[i].size > cap) return true; }
    return false;
  }

  function bufSha256(buf) {
    return crypto.subtle.digest("SHA-256", buf).then(function (d) {
      return Array.from(new Uint8Array(d)).map(function (b) { return b.toString(16).padStart(2, "0"); }).join("");
    });
  }

  function readResume() {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || "null"); } catch (e) { return null; }
  }
  function writeResume(v) {
    try { if (v) localStorage.setItem(LS_KEY, JSON.stringify(v)); else localStorage.removeItem(LS_KEY); } catch (e) {}
  }

  function setProgress(pct, text) {
    var pr = progress(), b = bar(), l = label();
    if (!pr) return;
    pr.hidden = false;
    if (b) b.value = pct;
    if (l) l.textContent = text || "";
  }
  function clearProgress() {
    var pr = progress(); if (pr) pr.hidden = true;
    if (bar()) bar().value = 0;
    if (label()) label().textContent = "";
  }

  function postFinalize(fd) {
    return fetch("/run/upload/finalize", { method: "POST", body: fd })
      .then(function (r) { return r.text(); });
  }

  function uploadOne(file, extra) {
    return file.arrayBuffer().then(function (ab) {
      var data = new Uint8Array(ab);
      return bufSha256(ab).then(function (sha) {
        return fetch("/run/upload/init", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: file.name, size: file.size, sha256: sha })
        }).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "init failed"); return j; }); })
          .then(function (init) {
            var uid = init.upload_id, off = init.offset || 0;
            writeResume({ upload_id: uid, filename: file.name, size: file.size, sha256: sha, offset: off });
            function sendChunk() {
              if (off >= data.length) return Promise.resolve();
              var end = Math.min(off + CHUNK, data.length);
              var slice = data.subarray(off, end);
              return fetch("/run/upload/chunk?upload_id=" + encodeURIComponent(uid) + "&offset=" + off, {
                method: "PUT",
                headers: { "Content-Type": "application/octet-stream" },
                body: slice
              }).then(function (r) {
                return r.json().then(function (j) {
                  if (r.status === 409) { off = j.received || 0; writeResume({ upload_id: uid, filename: file.name, size: file.size, sha256: sha, offset: off }); return sendChunk(); }
                  if (!r.ok) throw new Error(j.error || "chunk failed");
                  off = j.received != null ? j.received : end;
                  writeResume({ upload_id: uid, filename: file.name, size: file.size, sha256: sha, offset: off });
                  setProgress(Math.round((off / data.length) * 100), "Uploading " + file.name + "… " + off + " / " + data.length);
                  return sendChunk();
                });
              });
            }
            return sendChunk().then(function () {
              var fd = new FormData();
              fd.append("upload_id", uid);
              if (extra) {
                if (extra.batch_name) fd.append("batch_name", extra.batch_name);
                (extra.target_accounts || []).forEach(function (h) { fd.append("target_accounts", h); });
                if (extra.no_subs) fd.append("no_subs", "1");
              }
              return postFinalize(fd);
            });
          });
      });
    });
  }

  function runChunkedUpload(f, files) {
    f.preventDefault();
    var el = form(); if (!el) return;
    var extra = {
      batch_name: (el.querySelector('[name="batch_name"]') || {}).value || "",
      target_accounts: Array.prototype.map.call(el.querySelectorAll('[name="target_accounts"]:checked'), function (c) { return c.value; }),
      no_subs: !!(el.querySelector('[name="no_subs"]') && el.querySelector('[name="no_subs"]').checked)
    };
    setProgress(0, "Preparing…");
    var chain = Promise.resolve();
    Array.prototype.forEach.call(files, function (file) {
      chain = chain.then(function () { return uploadOne(file, extra); });
    });
    chain.then(function (html) {
      writeResume(null);
      clearProgress();
      var panel = document.getElementById("run-panel");
      if (panel && html) { panel.outerHTML = html; document.body.dispatchEvent(new CustomEvent("htmx:afterSwap", { detail: { target: panel } })); }
    }).catch(function (err) {
      clearProgress();
      alert(err && err.message ? err.message : "Upload failed");
    });
  }

  document.addEventListener("submit", function (e) {
    var f = form();
    if (!f || e.target !== f) return;
    var inp = f.querySelector('input[type="file"]');
    if (!inp || !inp.files || !inp.files.length) return;
    if (!needsChunked(inp.files)) return;   // small files — legacy htmx multipart path
    runChunkedUpload(e, inp.files);
  });
})();
