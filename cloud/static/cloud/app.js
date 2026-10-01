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

  function row(t) {
    const cls = t.status === "completed" ? "done" : t.status === "failed" ? "bad" : "";
    const dir = t.my_role === "sender" ? "↑" : "↓";
    const path = t.relative_path && t.relative_path !== t.file_name ? `<div class="muted mono">${esc(t.relative_path)}</div>` : "";
    return `<tr>
      <td class="name" title="${esc(t.file_name)}">${fileIcon}${esc(t.file_name)}${path}</td>
      <td class="num">${bytes(t.file_size)}</td>
      <td>${user(t.sender)}</td>
      <td>${user(t.receiver)}</td>
      <td><span class="dir">${dir} ${esc(dirLabel[t.direction] || t.direction)}${t.share_name ? " · " + esc(t.share_name) : ""}</span></td>
      <td><span class="pill ${esc(t.status)}">${esc(t.status)}</span></td>
      <td><span class="progress ${cls}"><i style="width:${t.progress.toFixed(1)}%"></i></span> <span class="muted">${t.progress.toFixed(0)}%</span></td>
      <td class="num">${t.status === "active" ? rate(t.speed) : rate(t.avg_speed)}</td>
      <td>${conn(t.connection_type)}</td>
      <td>${when(t.started_at)}</td>
      <td class="num">${dur(t.duration)}</td>
    </tr>`;
  }

  const HEAD = `<tr><th>File name</th><th class="num">Size</th><th>From</th><th>To</th><th>Type</th>
    <th>Status</th><th>Progress</th><th class="num">Speed</th><th>Connection</th><th>Date &amp; time</th><th class="num">Duration</th></tr>`;

  async function fetchJSON(url) {
    const r = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (r.status === 401 || r.status === 403) { location.href = "/login/"; throw new Error("auth"); }
    if (!r.ok) throw new Error(r.statusText);
    return r.json();
  }

  function table(el, rows, emptyText) {
    el.innerHTML = `<table class="grid"><thead>${HEAD}</thead><tbody>${
      rows.length ? rows.map(row).join("") : `<tr><td colspan="11" class="empty">${esc(emptyText)}</td></tr>`}</tbody></table>`;
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

  return { esc, bytes, rate, dur, when, table, fetchJSON, live };
})();
