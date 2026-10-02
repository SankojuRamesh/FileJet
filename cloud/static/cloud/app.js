"use strict";
// Live transfer tables for the dashboard. Data comes from the REST API (session auth);
// the page polls every 3 s so status matches what the sender and receiver apps show.

const P2P = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const bytes = (n) => {
    if (n == null) return "-";
    const u = ["B", "KB", "MB", "GB", "TB", "PB"];
    let i = 0; n = Number(n);
    while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
    return i === 0 ? `${n} B` : `${n.toFixed(1)} ${u[i]}`;
  };
  const rate = (b) => (b ? `${(b / 1e6).toFixed(1)} MB/s` : "-");
  const dur = (s) => {
    if (s == null) return "-";
    s = Math.round(s);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return h ? `${h}h ${m}m` : m ? `${m}m ${x}s` : `${x}s`;
  };
  const when = (iso) => {
    if (!iso) return "-";
    const d = new Date(iso);
    return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
  };
  const user = (u) => {
    if (!u) return '<span class="muted">-</span>';
    const name = u.display_name || u.username;
    return `<span class="user"><span class="avatar">${esc(name.slice(0, 1).toUpperCase())}</span>${esc(name)}</span>`;
  };
  const conn = (c) => !c ? '<span class="muted">-</span>'
    : `<span class="conn-direct">${esc(c)}</span>`;
  const dirLabel = { send: "Direct send", download: "Download", upload: "Upload", code: "Code transfer" };
  const fileIcon = '<svg class="file-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7z"/><path d="M14 3v4h4"/></svg>';

  const times = (n) => `${n} time${n === 1 ? "" : "s"}`;
  function statusCell(t) {
    const f = t.failures || 0, tries = t.attempts || 1;
    if (t.status === "failed") return `<span class="pill failed">failed ${times(f || 1)}</span>`;
    if (t.status === "cancelled") return `<span class="pill cancelled">cancelled</span>${f > 1 ? `<div class="muted small">failed ${times(f - 1)} before</div>` : ""}`;
    const pill = `<span class="pill ${esc(t.status)}">${esc(t.status)}</span>`;
    if (!f) return pill;
    return t.status === "completed"
      ? `${pill}<div class="muted small">after ${f} failed tr${f === 1 ? "y" : "ies"}</div>`
      : `${pill}<div class="muted small">try ${tries} · failed ${times(f)}</div>`;
  }

  function row(t) {
    const cls = t.status === "completed" ? "done" : t.status === "failed" ? "bad" : "";
    const dir = t.my_role === "sender" ? "↑" : "↓";
    const full = t.relative_path || t.file_name;           // full path only on mouse-over
    return `<tr class="clickable" data-tid="${esc(t.transfer_id)}" title="Click to see every try">
      <td class="name" title="${esc(full)}">${fileIcon}${esc(t.file_name)}</td>
      <td class="num">${bytes(t.file_size)}</td>
      <td>${user(t.sender)}</td>
      <td>${user(t.receiver)}</td>
      <td><span class="dir">${dir} ${esc(dirLabel[t.direction] || t.direction)}${t.share_name ? " · " + esc(t.share_name) : ""}</span></td>
      <td>${statusCell(t)}</td>
      <td><span class="progress ${cls}"><i style="width:${t.progress.toFixed(1)}%"></i></span> <span class="muted">${t.progress.toFixed(0)}%</span></td>
      <td class="num">${t.status === "active" ? rate(t.speed) : rate(t.avg_speed)}</td>
      <td>${conn(t.connection_type)}</td>
      <td>${when(t.first_started_at || t.started_at)}</td>
      <td class="num">${t.duration != null ? dur(t.duration) + (["active", "verifying"].includes(t.status) ? " so far" : "") : ["queued", "pending", "reconnecting", "paused"].includes(t.status) ? '<span class="muted">waiting</span>' : "-"}</td>
    </tr>`;
  }

  const HEAD = `<tr><th>File name</th><th class="num">Size</th><th>From</th><th>To</th><th>Type</th>
    <th>Status</th><th>Progress</th><th class="num">Speed</th><th>Connection</th><th>Date &amp; time</th><th class="num">Time taken</th></tr>`;

  async function fetchJSON(url) {
    const r = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (r.status === 401 || r.status === 403) { location.href = "/login/"; throw new Error("auth"); }
    if (!r.ok) throw new Error(r.statusText);
    return r.json();
  }

  // ---- attempt log: click a row to see when each try started, how it ended and why
  const opened = new Map();                 // transfer_id -> rendered log (kept across the 3 s refresh)
  function logHtml(d) {
    const list = d.attempts, last = list[list.length - 1];
    const head = `Tried ${times(d.total)} · failed ${times(d.failures)}` +
      (last.status === "completed" ? ` · <span class="ok">completed ${esc(when(last.ended_at))}</span>` : "") +
      (d.shown_from > 1 ? ` <span class="muted">(showing tries ${d.shown_from}–${d.total})</span>` : "");
    const rows = list.map((a) => `<tr><td class="num">#${a.n}</td><td>${when(a.started_at)}</td><td>${when(a.ended_at)}</td>
      <td><span class="pill ${esc(a.status)}">${esc(a.status)}</span></td>
      <td class="num">${bytes(a.bytes_transferred)} / ${bytes(a.file_size)}</td><td class="num">${a.duration != null ? dur(a.duration) : "-"}</td>
      <td class="num">${rate(a.avg_speed)}</td><td>${esc(a.connection_type || "-")}</td><td class="muted">${esc(a.error || "")}</td></tr>`).join("");
    return `<div class="attempt-head">${head}</div><table class="grid attempts"><thead><tr><th class="num">Try</th><th>Started</th>
      <th>Ended</th><th>Result</th><th class="num">Transferred</th><th class="num">Time</th><th class="num">Speed</th>
      <th>Connection</th><th>Reason</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  async function loadLog(el, tid) {
    try {
      const d = await fetchJSON(`/api/transfers/${encodeURIComponent(tid)}/attempts/`);
      opened.set(tid, logHtml(d));
    } catch (e) { opened.set(tid, '<span class="muted">Could not load the attempts.</span>'); }
    const cell = el.querySelector(`tr.attempt-row[data-for="${CSS.escape(tid)}"] td`);
    if (cell) cell.innerHTML = opened.get(tid);
  }
  const logRow = (tid, cols = 11) => `<tr class="attempt-row" data-for="${esc(tid)}"><td colspan="${cols}">${opened.get(tid) || '<span class="muted">Loading…</span>'}</td></tr>`;

  // any table whose rows carry data-tid: click a row to open / close its attempt log
  function attemptClicks(el) {
    if (el.dataset.clicks) return;
    el.dataset.clicks = "1";
    el.addEventListener("click", (e) => {
      const tr = e.target.closest("tr.clickable");
      if (!tr) return;
      const tid = tr.dataset.tid;
      if (opened.has(tid)) {
        opened.delete(tid);
        if (tr.nextElementSibling?.classList.contains("attempt-row")) tr.nextElementSibling.remove();
      } else {
        opened.set(tid, "");
        tr.insertAdjacentHTML("afterend", logRow(tid, tr.children.length));
        loadLog(el, tid);
      }
    });
  }

  function table(el, rows, emptyText) {
    el.innerHTML = `<table class="grid"><thead>${HEAD}</thead><tbody>${
      rows.length ? rows.map((t) => row(t) + (opened.has(t.transfer_id) ? logRow(t.transfer_id) : "")).join("")
        : `<tr><td colspan="11" class="empty">${esc(emptyText)}</td></tr>`}</tbody></table>`;
    rows.forEach((t) => { if (opened.has(t.transfer_id)) loadLog(el, t.transfer_id); });
    attemptClicks(el);
  }

  function live(fn, ms = 3000) {
    const ind = document.getElementById("live-indicator");
    const tick = async () => {
      try { await fn(); if (ind) ind.textContent = `live · ${new Date().toLocaleTimeString()}`; }
      catch (e) { if (ind) ind.textContent = "offline - retrying"; }
    };
    tick();
    return setInterval(() => { if (!document.hidden) tick(); }, ms);
  }

  return { esc, bytes, rate, dur, when, table, fetchJSON, live, attemptClicks };
})();
