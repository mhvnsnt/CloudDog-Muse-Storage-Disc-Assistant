/* CloudDog dashboard frontend — vanilla JS, zero dependencies.
   Every value shown comes from the live backend endpoints. */
"use strict";

async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  return { status: res.status, body };
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function fmtBytes(n) {
  if (n == null) return "—";
  const units = ["B","KB","MB","GB","TB","PB"];
  let v = Number(n), i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return v.toFixed(v >= 100 ? 0 : 1) + " " + units[i];
}

function errHtml(e) {
  return `<div class="err">error: ${esc(e && e.error ? e.error : JSON.stringify(e))}</div>`;
}

function setOut(id, html) { document.getElementById(id).innerHTML = html; }

/* ---------- Header disk meter ---------- */
async function loadDiskMeter() {
  const { body } = await api("/api/disk");
  if (body.use_pct == null) { setOut("disk-meter-label", "disk: unavailable"); return; }
  const meter = document.getElementById("disk-meter");
  meter.querySelector("div").style.width = body.use_pct + "%";
  meter.className = "meter" + (body.use_pct >= 90 ? " crit" : body.use_pct >= 75 ? " warn" : "");
  setOut("disk-meter-label",
    `${esc(body.path)} — ${fmtBytes(body.used_bytes)} / ${fmtBytes(body.total_bytes)} used ` +
    `(${body.use_pct}%), ${fmtBytes(body.free_bytes)} free`);
}

/* ---------- Storage overview + du ---------- */
async function loadOverview() {
  const { body } = await api("/api/disk");
  if (body.use_pct == null) { setOut("overview-kv", errHtml(body)); return; }
  setOut("overview-kv",
    `<b>mount</b> ${esc(body.path)} &nbsp; <b>total</b> ${fmtBytes(body.total_bytes)} ` +
    `&nbsp; <b>used</b> ${fmtBytes(body.used_bytes)} (${body.use_pct}%) ` +
    `&nbsp; <b>free</b> ${fmtBytes(body.free_bytes)}`);
  loadDiskMeter();
}

async function loadDU() {
  const p = document.getElementById("du-path").value || "/home/hatch/workspace";
  const { status, body } = await api("/api/du?path=" + encodeURIComponent(p) + "&depth=1");
  const tb = document.querySelector("#du-table tbody");
  if (status >= 400 || !Array.isArray(body.entries)) { tb.innerHTML = `<tr><td colspan="3">${esc(body.error || "failed")}</td></tr>`; return; }
  tb.innerHTML = body.entries.map(e =>
    `<tr><td><code>${esc(e.path)}</code></td><td class="num">${fmtBytes(e.bytes)}</td>` +
    `<td>${e.mtime ? new Date(e.mtime * 1000).toLocaleString() : "—"}</td></tr>`).join("")
    || `<tr><td colspan="3">empty</td></tr>`;
}

/* ---------- Clutter table ---------- */
async function loadPolicyScan() {
  const p = document.getElementById("scan-path").value || "/home/hatch/workspace";
  const { status, body } = await api("/api/policy/scan?path=" + encodeURIComponent(p));
  if (status >= 400) { setOut("clutter-out", errHtml(body)); return; }
  if (!body.length) { setOut("clutter-out", `<div class="ok">no policy actions for ${esc(p)}</div>`); return; }
  setOut("clutter-out", `<table><thead><tr><th>path</th><th style="text-align:right">bytes</th><th>action</th><th>tier</th><th>reason</th></tr></thead><tbody>` +
    body.map(r => `<tr><td><code>${esc(r.path)}</code></td><td class="num">${fmtBytes(r.bytes)}</td>` +
      `<td>${esc(r.action ?? "—")}</td><td>${esc(r.tier ?? "—")}</td><td>${esc(r.reason ?? "")}</td></tr>`).join("") +
    `</tbody></table>`);
}

/* ---------- Terminal ---------- */
function splitArgv(s) {
  // shell-ish splitting for quotes only; never passed to a shell
  const out = []; let cur = "", q = null;
  for (const ch of s) {
    if (q) { if (ch === q) q = null; else cur += ch; }
    else if (ch === '"' || ch === "'") q = ch;
    else if (/\s/.test(ch)) { if (cur) { out.push(cur); cur = ""; } }
    else cur += ch;
  }
  if (cur) out.push(cur);
  return out;
}

async function runCLI() {
  const argv = splitArgv(document.getElementById("cli-argv").value.trim());
  const out = document.getElementById("cli-out");
  out.textContent = "running…";
  if (!argv.length) { out.textContent = "enter a command"; return; }
  const { status, body } = await api("/api/cli", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ argv })
  });
  if (status >= 400 || !body.ok) { out.textContent = "ERROR " + status + ": " + (body.error || "?"); return; }
  out.textContent = `$ clouddog ${argv.join(" ")}\nreturncode: ${body.returncode}\n\n--- stdout ---\n${body.stdout}\n--- stderr ---\n${body.stderr}`;
}

/* ---------- Daemon scheduler ---------- */
async function genDaemon() {
  const osName = document.getElementById("daemon-os").value;
  const interval = parseInt(document.getElementById("daemon-interval").value, 10);
  const { status, body } = await api("/api/daemon/install", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ os: osName, interval_minutes: interval })
  });
  if (status >= 400 || !body.ok) { setOut("daemon-out", errHtml(body)); return; }
  setOut("daemon-out", body.files.map(f =>
    `<h3 style="font-size:14px"><code>${esc(f.filename)}</code></h3><pre class="out">${esc(f.content)}</pre>`).join(""));
}

/* ---------- Repo explorer ---------- */
async function loadRepos() {
  const owner = document.getElementById("repo-owner").value || "mhvnsnt";
  const { status, body } = await api("/api/repos?owner=" + encodeURIComponent(owner));
  if (status >= 400) { setOut("repos-out", errHtml(body)); return; }
  setOut("repos-out", `<table><thead><tr><th>name</th><th>description</th></tr></thead><tbody>` +
    body.map(r => `<tr><td><code>${esc(r.name)}</code></td><td>${esc(r.description ?? "")}</td></tr>`).join("") +
    `</tbody></table>`);
}

async function loadRepoContents() {
  const owner = document.getElementById("repo-owner").value || "mhvnsnt";
  const repo = document.getElementById("repo-name").value.trim();
  const sub = document.getElementById("repo-subpath").value.trim();
  if (!repo) { setOut("repos-out", `<div class="err">enter a repo name</div>`); return; }
  const { status, body } = await api(
    `/api/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/contents?path=` + encodeURIComponent(sub));
  if (status >= 400) { setOut("repos-out", errHtml(body)); return; }
  setOut("repos-out", `<table><thead><tr><th>name</th><th>type</th><th style="text-align:right">size</th><th>path</th></tr></thead><tbody>` +
    body.map(i => `<tr><td><code>${esc(i.name)}</code></td><td>${esc(i.type)}</td>` +
      `<td class="num">${i.size == null ? "—" : fmtBytes(i.size)}</td><td><code>${esc(i.path)}</code></td></tr>`).join("") +
    `</tbody></table>`);
}

/* ---------- Torrent mirror ---------- */
async function loadTorrent() {
  const { body } = await api("/api/torrent/status");
  let html = `<div class="kv"><b>available</b> ${body.available ? "yes" : "no"}`;
  if (body.version) html += ` &nbsp; <b>version</b> ${esc(body.version)}`;
  if (body.reason) html += `<br><b>reason</b> ${esc(body.reason)}`;
  html += `</div>`;
  const ts = body.torrents || [];
  html += ts.length
    ? `<table><thead><tr><th>id</th><th>name</th><th style="text-align:right">progress</th><th style="text-align:right">down rate</th><th>state</th></tr></thead><tbody>` +
      ts.map(t => `<tr><td><code>${esc(t.id)}</code></td><td>${esc(t.name)}</td>` +
        `<td class="num">${t.progress_pct == null ? "—" : t.progress_pct + "%"}</td>` +
        `<td class="num">${t.down_rate == null ? "—" : fmtBytes(t.down_rate) + "/s"}</td>` +
        `<td>${esc(t.state ?? "—")}</td></tr>`).join("") + `</tbody></table>`
    : `<div class="kv">no torrents</div>`;
  setOut("torrent-out", html);
}

async function addTorrent() {
  const magnet = document.getElementById("magnet").value.trim();
  const tier = document.getElementById("torrent-tier").value;
  const remote_ref = document.getElementById("torrent-ref").value.trim();
  const { status, body } = await api("/api/torrent/add", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ magnet, tier, remote_ref })
  });
  setOut("torrent-add-out", status >= 400 || !body.ok
    ? errHtml(body)
    : `<div class="ok">added: <code>${esc(body.id)}</code></div>`);
}

/* ---------- Compute ---------- */
async function loadSysinfo() {
  const { status, body } = await api("/api/sysinfo");
  if (status >= 400) { setOut("compute-out", errHtml(body)); return; }
  const c = body.cpu, m = body.mem, d = body.disk, g = body.gpu;
  let html = `<b>load average (1/5/15 min)</b> ${c.load_avg_1min.toFixed(2)} / ${c.load_avg_5min.toFixed(2)} / ${c.load_avg_15min.toFixed(2)} ` +
    `&nbsp; <b>cpus</b> ${c.cpu_count}<br>` +
    `<b>memory</b> ${fmtBytes(m.used_bytes)} / ${fmtBytes(m.total_bytes)} (${m.pct}%)<br>` +
    `<b>disk</b> ${fmtBytes(d.used_bytes)} / ${fmtBytes(d.total_bytes)} (${d.use_pct}%) free ${fmtBytes(d.free_bytes)}<br>`;
  html += g
    ? `<b>gpu</b> ${esc(g.name)} — ${fmtBytes(g.memory_used_bytes)} / ${fmtBytes(g.memory_total_bytes)} used`
    : `<b>gpu</b> none detected (nvidia-smi absent)`;
  setOut("compute-out", html);
}

/* ---------- Emergency reaper ---------- */
async function loadLanes() {
  const { status, body } = await api("/api/lanes");
  if (status >= 400) { setOut("lanes-out", errHtml(body)); return; }
  if (!body.length) { setOut("lanes-out", `<div class="kv">no blob_checkpoint.json lanes found under ~/workspace</div>`); return; }
  setOut("lanes-out", `<table><thead><tr><th>lane</th><th style="text-align:right">uploaded</th><th style="text-align:right">total</th><th style="text-align:right">pct</th><th>alive</th><th>note</th><th>log tail</th></tr></thead><tbody>` +
    body.map(l => `<tr><td><code>${esc(l.lane)}</code></td>` +
      `<td class="num">${l.uploaded == null ? "—" : l.uploaded}</td>` +
      `<td class="num">${l.total == null ? "—" : l.total}</td>` +
      `<td class="num">${l.pct == null ? "—" : l.pct + "%"}</td>` +
      `<td>${l.alive ? '<span class="pill live">alive</span>' : '<span class="pill down">not seen</span>'}</td>` +
      `<td>${esc(l.note ?? "")}</td>` +
      `<td><details class="log"><summary>${l.log_tail.length} lines</summary><pre class="out">${esc(l.log_tail.join("\n"))}</pre></details></td></tr>`).join("") +
    `</tbody></table>`);
}

async function runReap() {
  const { status, body } = await api("/api/reap", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      manifest: document.getElementById("reap-manifest").value.trim(),
      src_dir: document.getElementById("reap-src").value.trim(),
      dry_run: document.getElementById("reap-dry").checked
    })
  });
  setOut("reaper-out", status >= 400 || !body.ok
    ? errHtml(body)
    : `<div class="ok">reap ok</div><pre class="out">${esc(JSON.stringify(body, null, 2))}</pre>`);
}

async function runSweep() {
  const { status, body } = await api("/api/sweep", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ dry_run: document.getElementById("sweep-dry").checked })
  });
  setOut("reaper-out", `<div class="sub">engine sweep (not the live disk-space-guardian)</div>` +
    (status >= 400 || !body.ok ? errHtml(body)
      : `<div class="ok">sweep ok</div><pre class="out">${esc(JSON.stringify(body.report, null, 2))}</pre>`));
}

/* ---------- Broadcast ---------- */
async function draftBroadcast() {
  const title = document.getElementById("bc-title").value.trim();
  let laws;
  try { laws = JSON.parse(document.getElementById("bc-laws").value); }
  catch (e) { document.getElementById("bc-out").textContent = "laws is not valid JSON: " + e; return; }
  const { status, body } = await api("/api/broadcast/draft", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ title, laws })
  });
  document.getElementById("bc-out").textContent =
    (status >= 400 || !body.ok) ? "ERROR: " + (body.error || status) : body.markdown;
}

/* ---------- Relocation wizard ---------- */
async function runRelocate() {
  const { status, body } = await api("/api/relocate", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      path: document.getElementById("rel-path").value.trim(),
      tier: document.getElementById("rel-tier").value,
      remote_ref: document.getElementById("rel-ref").value.trim(),
      dry_run: document.getElementById("rel-dry").checked
    })
  });
  let html;
  if (status >= 400 || !body.ok) { html = errHtml(body); }
  else {
    html = body.dry_run ? `<div class="ok">dry run — nothing stored, nothing deleted</div>` : `<div class="ok">relocated</div>`;
    if (body.shim && !body.dry_run) html += `<div class="kv"><b>retrieval shim</b> <code>${esc(body.shim)}</code></div>`;
    if (body.limits) html += `<div class="kv"><b>limits</b><ul>${body.limits.map(l => `<li>${esc(l)}</li>`).join("")}</ul></div>`;
    html += `<pre class="out">${esc(JSON.stringify(body.dry_run ? {manifest: body.manifest, shim_preview: body.shim} : {stored: body.stored}, null, 2))}</pre>`;
  }
  setOut("relocate-out", html);
}

/* ---------- Audit ---------- */
async function loadAudit() {
  const limit = document.getElementById("audit-limit").value || 100;
  const { status, body } = await api("/api/audit?limit=" + encodeURIComponent(limit));
  if (status >= 400) { setOut("audit-out", errHtml(body)); return; }
  if (!body.length) { setOut("audit-out", `<div class="kv">no audit entries</div>`); return; }
  setOut("audit-out", `<table><thead><tr><th>ts</th><th>kind</th><th>path</th><th style="text-align:right">size</th><th>locator</th><th>note</th></tr></thead><tbody>` +
    body.map(e => `<tr><td><code>${esc(e.ts ?? "")}</code></td><td>${esc(e.kind ?? "")}</td>` +
      `<td><code>${esc(e.path ?? "")}</code></td><td class="num">${e.size == null ? "—" : fmtBytes(e.size)}</td>` +
      `<td><code>${esc(e.locator ?? "")}</code></td><td>${esc(e.note ?? "")}</td></tr>`).join("") +
    `</tbody></table>`);
}

/* ---------- Backends ---------- */
async function loadBackends() {
  const { body } = await api("/api/backends");
  if (!Array.isArray(body)) { setOut("backends-out", errHtml(body)); return; }
  setOut("backends-out", `<table><thead><tr><th>backend</th><th>health</th><th>reason</th></tr></thead><tbody>` +
    body.map(b => `<tr><td><code>${esc(b.name)}</code></td>` +
      `<td><span class="pill ${esc(b.health)}">${esc(b.health)}</span></td>` +
      `<td>${esc(b.reason ?? "")}</td></tr>`).join("") + `</tbody></table>`);
}

/* ---------- init ---------- */
window.addEventListener("DOMContentLoaded", () => {
  loadDiskMeter(); loadOverview(); loadDU(); loadSysinfo();
  loadBackends(); loadLanes(); loadTorrent(); loadAudit();
});
