const listEl = document.querySelector("#monitor-list");
const detailEl = document.querySelector("#detail");
const summaryEl = document.querySelector("#summary");
const alertsEl = document.querySelector("#alerts");
const editor = document.querySelector("#editor");
const form = document.querySelector("#editor-form");
const formError = document.querySelector("#form-error");

const folderMenu = document.querySelector("#folder-menu");

const state = {
  monitors: [],
  folders: [],
  alerts: [],
  selectedId: null,
  rangeKey: "24h",
  history: null,
  editingId: null,
  listStamp: "",
  detailStamp: "",
  alertStamp: "",
  renaming: null,
  menuFolderId: null,
  drag: null,
  drop: null,
};

document.querySelector("#add-open").addEventListener("click", () => openEditor(null));
document.querySelector("#new-folder").addEventListener("click", createFolder);
folderMenu.addEventListener("click", onFolderMenu);
document.addEventListener("click", () => { folderMenu.hidden = true; });
document.querySelector("#editor-cancel").addEventListener("click", () => editor.close());
form.addEventListener("submit", saveMonitor);

async function api(path, options) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = body && body.detail ? body.detail : "Request failed";
    throw new Error(typeof detail === "string" ? detail : "Request failed");
  }
  return body;
}

function openEditor(monitor) {
  state.editingId = monitor ? monitor.id : null;
  document.querySelector("#editor-title").textContent = monitor ? "Edit site" : "Add a site";
  document.querySelector("#editor-save").textContent = monitor ? "Save" : "Start checking";
  document.querySelector("#field-name").value = monitor ? monitor.name : "";
  document.querySelector("#field-url").value = monitor ? monitor.url : "";
  document.querySelector("#field-interval").value = monitor ? monitor.interval_seconds : 60;
  document.querySelector("#field-timeout").value = monitor ? monitor.timeout_seconds : 30;
  document.querySelector("#field-alert").checked = monitor ? monitor.alert_screen : true;
  formError.hidden = true;
  editor.showModal();
}

async function saveMonitor(event) {
  event.preventDefault();
  const payload = {
    name: document.querySelector("#field-name").value,
    url: document.querySelector("#field-url").value,
    interval_seconds: Number(document.querySelector("#field-interval").value),
    timeout_seconds: Number(document.querySelector("#field-timeout").value),
    alert_screen: document.querySelector("#field-alert").checked,
  };
  try {
    const saved = state.editingId
      ? await api(`/api/monitors/${state.editingId}`, { method: "PATCH", body: JSON.stringify(payload) })
      : await api("/api/monitors", { method: "POST", body: JSON.stringify(payload) });
    state.selectedId = saved.id;
    editor.close();
    await refresh();
  } catch (error) {
    formError.hidden = false;
    formError.textContent = error.message;
  }
}

async function refresh() {
  const data = await api("/api/monitors");
  state.monitors = data.monitors;
  state.folders = data.folders || [];
  state.alerts = data.alerts;
  if (!state.monitors.some((monitor) => monitor.id === state.selectedId)) {
    state.selectedId = state.monitors.length ? state.monitors[0].id : null;
  }
  renderList();
  renderAlerts();
  if (state.selectedId) {
    state.history = await api(`/api/monitors/${state.selectedId}/history?range_key=${state.rangeKey}`);
  } else {
    state.history = null;
  }
  renderDetail();
}

function renderTally() {
  const total = state.monitors.length;
  const down = state.monitors.filter((monitor) => monitor.last_status === "down").length;
  const count = document.createElement("span");
  count.className = down === 0 ? "tally-x is-zero" : "tally-x";
  count.textContent = String(down);
  summaryEl.replaceChildren(count, ` of ${total}`);
  summaryEl.title = `${down} not responding, out of ${total} sites`;
}

function renderList() {
  renderTally();
  const stamp = JSON.stringify({
    monitors: state.monitors,
    folders: state.folders,
    selectedId: state.selectedId,
    renaming: state.renaming,
  });
  if (document.activeElement && document.activeElement.classList.contains("folder-name-input")) return;
  if (stamp === state.listStamp) return;
  state.listStamp = stamp;

  if (!state.monitors.length && !state.folders.length) {
    listEl.innerHTML = `<p class="empty">Add a URL to start a once-a-minute check.</p>`;
    return;
  }
  listEl.innerHTML = rootItems().map((item) => {
    if (item.type === "folder") return folderHtml(item);
    const monitor = state.monitors.find((entry) => entry.id === item.id);
    return monitorHtml(monitor, null);
  }).join("");
  bindListInteractions();
}

function renderAlerts() {
  const stamp = JSON.stringify(state.alerts);
  if (stamp === state.alertStamp) return;
  state.alertStamp = stamp;
  if (!state.alerts.length) {
    alertsEl.innerHTML = "";
    return;
  }
  alertsEl.innerHTML = state.alerts.map((alert) => `
    <article class="alert ${alert.kind}">
      <div>
        <strong>${escapeHtml(alert.name)} ${alert.kind === "down" ? "is down" : "recovered"}</strong>
        <p>${escapeHtml(alert.message)} · ${escapeHtml(alert.url)}</p>
      </div>
      <button class="ghost" type="button" data-dismiss="${alert.id}">Dismiss</button>
    </article>
  `).join("");
  alertsEl.querySelectorAll("[data-dismiss]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/alerts/${button.dataset.dismiss}/dismiss`, { method: "POST" });
      await refresh();
    });
  });
}

function renderDetail() {
  const monitor = state.monitors.find((item) => item.id === state.selectedId);
  const stamp = JSON.stringify({ id: state.selectedId, range: state.rangeKey, monitor, history: state.history });
  if (stamp === state.detailStamp) return;
  state.detailStamp = stamp;
  if (!monitor || !state.history) {
    detailEl.innerHTML = `<p class="empty">Watch a website from this machine. Each check is stored so you can graph response time and review failures.</p>`;
    return;
  }
  const stats = state.history.stats;
  const status = !monitor.enabled ? "paused" : (monitor.last_status || "unknown");
  const statusText = {
    up: "Up",
    down: monitor.last_error || "Down",
    paused: "Paused",
    unknown: "Waiting for the first check",
  }[status];
  detailEl.innerHTML = `
    <div class="detail-head">
      <div>
        <h1>${escapeHtml(monitor.name)}</h1>
        <p class="url">${escapeHtml(monitor.url)}</p>
        <p class="status-line ${status}">${escapeHtml(statusText)}</p>
      </div>
      <div class="actions">
        <button class="ghost" type="button" id="check-now">Check now</button>
        <button class="ghost" type="button" id="toggle">${monitor.enabled ? "Pause" : "Resume"}</button>
        <button class="ghost" type="button" id="edit">Edit</button>
        <button class="danger" type="button" id="remove">Remove</button>
      </div>
    </div>
    <div class="ranges">
      ${["1h", "24h", "7d", "30d", "all"].map((key) =>
        `<button type="button" data-range="${key}" class="${key === state.rangeKey ? "on" : ""}">${key}</button>`
      ).join("")}
    </div>
    <div class="stats">
      ${stat("Uptime", formatPercent(stats.uptime_percent))}
      ${stat("Avg response", formatMs(stats.avg_ms))}
      ${stat("p95 response", formatMs(stats.p95_ms))}
      ${stat("Incidents", String(stats.incidents))}
    </div>
    <div class="chart-wrap">${drawChart(state.history.series)}</div>
    <table class="log">
      <thead><tr><th>When</th><th>Result</th><th>HTTP</th><th>Time</th><th>Detail</th></tr></thead>
      <tbody>
        ${state.history.checks.filter((check) => check.status !== "up").map((check) => `
          <tr>
            <td>${escapeHtml(formatTime(check.checked_at))}</td>
            <td class="bad">${check.status}</td>
            <td>${check.http_status ?? "—"}</td>
            <td>${formatMs(check.response_ms)}</td>
            <td>${escapeHtml(check.error || "")}</td>
          </tr>`).join("") || `<tr><td colspan="5">No failed checks in this range.</td></tr>`}
      </tbody>
    </table>`;

  detailEl.querySelector("#check-now").addEventListener("click", async () => {
    await api(`/api/monitors/${monitor.id}/check`, { method: "POST" });
    await refresh();
  });
  detailEl.querySelector("#toggle").addEventListener("click", async () => {
    await api(`/api/monitors/${monitor.id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !monitor.enabled }),
    });
    await refresh();
  });
  detailEl.querySelector("#edit").addEventListener("click", () => openEditor(monitor));
  detailEl.querySelector("#remove").addEventListener("click", async () => {
    if (!confirm(`Stop watching ${monitor.name}?`)) return;
    await api(`/api/monitors/${monitor.id}`, { method: "DELETE" });
    await refresh();
  });
  detailEl.querySelectorAll("[data-range]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.rangeKey = button.dataset.range;
      await refresh();
    });
  });
}

function stat(label, value) {
  return `<div class="stat"><span>${label}</span><strong>${value}</strong></div>`;
}

function formatPercent(value) {
  return value == null ? "—" : `${value.toFixed(2)}%`;
}

function formatMs(value) {
  return value == null ? "—" : `${Math.round(value)} ms`;
}

function formatTime(value) {
  return new Date(value).toLocaleString();
}

function drawChart(series) {
  const width = 760;
  const height = 220;
  const pad = { l: 48, r: 12, t: 16, b: 28 };
  const values = series.map((point) => point.response_ms).filter((value) => value != null);
  const max = Math.max(100, ...(values.length ? values : [100]));
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const x = (index) => pad.l + (series.length <= 1 ? innerW / 2 : (index / (series.length - 1)) * innerW);
  const y = (value) => pad.t + innerH - (value / max) * innerH;
  const known = series.map((point, index) => ({ index, ...point })).filter((point) => point.response_ms != null);
  const line = known.map((point, index) => `${index === 0 ? "M" : "L"} ${x(point.index)} ${y(point.response_ms)}`).join(" ");
  const area = known.length
    ? `${line} L ${x(known[known.length - 1].index)} ${pad.t + innerH} L ${x(known[0].index)} ${pad.t + innerH} Z`
    : "";
  const downs = series.map((point, index) => point.status === "down"
    ? `<line class="down-mark" x1="${x(index)}" x2="${x(index)}" y1="${pad.t}" y2="${pad.t + innerH}" />`
    : "").join("");
  return `
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Response time graph">
      <line class="axis" x1="${pad.l}" y1="${pad.t + innerH}" x2="${width - pad.r}" y2="${pad.t + innerH}" />
      <text x="4" y="${pad.t + 8}">${Math.round(max)} ms</text>
      <text x="4" y="${pad.t + innerH}">0</text>
      ${downs}
      <path class="area" d="${area}"></path>
      <path class="line" d="${line}"></path>
    </svg>`;
}

function monitorHtml(monitor, parentId) {
  const status = !monitor.enabled ? "paused" : (monitor.last_status || "unknown");
  const parent = parentId == null ? "" : ` data-parent="${parentId}"`;
  return `
    <div class="monitor ${monitor.id === state.selectedId ? "active" : ""}" draggable="true" data-kind="monitor" data-id="${monitor.id}"${parent} title="${escapeHtml(monitor.name)}">
      <span class="dot ${status}"></span>
      <strong>${escapeHtml(monitor.name)}</strong>
    </div>`;
}

function folderHtml(item) {
  const folder = state.folders.find((entry) => entry.id === item.id);
  const problems = state.monitors.filter((monitor) => (
    monitor.folder_id === folder.id && monitor.enabled && monitor.last_status === "down"
  )).length;
  const mark = problems
    ? `<span class="down-count" aria-label="${problems} not working">${problems}</span>`
    : `<span class="dot up" aria-label="All working"></span>`;
  const name = state.renaming === folder.id
    ? `<input class="folder-name-input" value="${escapeHtml(folder.name)}" aria-label="Folder name">`
    : `<strong>${escapeHtml(folder.name)}</strong>`;
  const children = folder.collapsed
    ? ""
    : `<div class="folder-children">${item.children.map((id) => monitorHtml(state.monitors.find((entry) => entry.id === id), folder.id)).join("")}</div>`;
  const tone = problems ? "down" : "good";
  return `
    <div class="folder">
      <div class="folder-head ${tone}" draggable="true" data-kind="folder" data-id="${folder.id}">
        <button class="chevron" type="button" aria-label="${folder.collapsed ? "Expand" : "Collapse"} folder">${folder.collapsed ? "▸" : "▾"}</button>
        ${mark}
        ${name}
      </div>
      ${children}
    </div>`;
}

function rootItems() {
  const items = state.folders.map((folder) => ({
    type: "folder",
    id: folder.id,
    sort: folder.sort_order,
    children: state.monitors
      .filter((monitor) => monitor.folder_id === folder.id)
      .sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
      .map((monitor) => monitor.id),
  }));
  state.monitors.filter((monitor) => monitor.folder_id == null).forEach((monitor) => {
    items.push({ type: "monitor", id: monitor.id, sort: monitor.sort_order, children: [] });
  });
  return items.sort((a, b) => a.sort - b.sort || a.id - b.id);
}

function bindListInteractions() {
  listEl.querySelectorAll(".monitor").forEach((row) => {
    row.addEventListener("click", () => {
      if (row.dataset.suppress === "1") {
        row.dataset.suppress = "";
        return;
      }
      state.selectedId = Number(row.dataset.id);
      refresh();
    });
    bindDrag(row);
  });
  listEl.querySelectorAll(".folder-head").forEach((row) => {
    row.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      event.stopPropagation();
      state.menuFolderId = Number(row.dataset.id);
      folderMenu.hidden = false;
      folderMenu.style.left = `${event.clientX}px`;
      folderMenu.style.top = `${event.clientY}px`;
    });
    row.querySelector(".chevron").addEventListener("click", async (event) => {
      event.stopPropagation();
      const folder = state.folders.find((entry) => entry.id === Number(row.dataset.id));
      await api(`/api/folders/${folder.id}`, {
        method: "PATCH",
        body: JSON.stringify({ collapsed: !folder.collapsed }),
      });
      state.listStamp = "";
      await refresh();
    });
    const input = row.querySelector(".folder-name-input");
    if (input) {
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") input.blur();
        if (event.key === "Escape") {
          state.renaming = null;
          state.listStamp = "";
          input.blur();
          refresh();
        }
      });
      input.addEventListener("blur", () => saveFolderName(Number(row.dataset.id), input.value));
      input.focus();
      input.select();
    }
    bindDrag(row);
  });
}

function bindDrag(row) {
  row.addEventListener("dragstart", (event) => {
    state.drag = { kind: row.dataset.kind, id: Number(row.dataset.id) };
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", `${row.dataset.kind}:${row.dataset.id}`);
    row.classList.add("row-dragging");
  });
  row.addEventListener("dragend", () => {
    row.dataset.suppress = "1";
    row.classList.remove("row-dragging");
    state.drag = null;
    clearDropClasses();
  });
  row.addEventListener("dragover", (event) => {
    if (!state.drag) return;
    const target = dropTarget(row, event.clientY);
    if (!target) return;
    event.preventDefault();
    clearDropClasses();
    row.classList.add(target.into ? "drop-into" : (target.after ? "insert-after" : "insert-before"));
    state.drop = target;
  });
  row.addEventListener("drop", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const drag = state.drag;
    const target = state.drop;
    state.drag = null;
    clearDropClasses();
    if (drag && target) commitMove(drag, target);
  });
}

function dropTarget(row, clientY) {
  const drag = state.drag;
  const kind = row.dataset.kind;
  const id = Number(row.dataset.id);
  if (drag.kind === kind && drag.id === id) return null;
  const rect = row.getBoundingClientRect();
  const ratio = rect.height ? (clientY - rect.top) / rect.height : 0.5;
  const after = ratio >= 0.5;
  const items = rootItems();
  if (kind === "folder") {
    const rootIndex = items.findIndex((item) => item.type === "folder" && item.id === id);
    if (drag.kind === "monitor" && ratio > 0.28 && ratio < 0.72) {
      const folder = items.find((item) => item.type === "folder" && item.id === id);
      return { parentId: id, index: folder.children.length, into: true, after: false };
    }
    return { parentId: null, index: after ? rootIndex + 1 : rootIndex, into: false, after };
  }
  const parentId = row.dataset.parent ? Number(row.dataset.parent) : null;
  if (drag.kind === "folder") {
    const rootIndex = parentId == null
      ? items.findIndex((item) => item.type === "monitor" && item.id === id)
      : items.findIndex((item) => item.type === "folder" && item.id === parentId);
    return { parentId: null, index: after ? rootIndex + 1 : rootIndex, into: false, after };
  }
  const index = parentId == null
    ? items.findIndex((item) => item.type === "monitor" && item.id === id)
    : items.find((item) => item.type === "folder" && item.id === parentId).children.indexOf(id);
  return { parentId, index: after ? index + 1 : index, into: false, after };
}

function clearDropClasses() {
  listEl.querySelectorAll(".insert-before, .insert-after, .drop-into").forEach((row) => {
    row.classList.remove("insert-before", "insert-after", "drop-into");
  });
}

async function commitMove(drag, target) {
  const items = rootItems().map((item) => ({ ...item, children: [...item.children] }));
  const origin = findPosition(items, drag);
  const node = removeNode(items, drag);
  let index = target.index;
  if (origin && origin.parentId === (drag.kind === "folder" ? null : target.parentId) && origin.index < index) {
    index -= 1;
  }
  if (drag.kind === "folder") insertNode(items, node, null, Math.max(0, index));
  else insertNode(items, node, target.parentId, Math.max(0, index));
  await api("/api/layout", {
    method: "POST",
    body: JSON.stringify({
      items: items.map((item) => ({ type: item.type, id: item.id, children: item.children })),
    }),
  });
  state.listStamp = "";
  await refresh();
}

function findPosition(items, drag) {
  if (drag.kind === "folder") {
    const index = items.findIndex((item) => item.type === "folder" && item.id === drag.id);
    return { parentId: null, index };
  }
  for (const item of items) {
    if (item.type === "monitor" && item.id === drag.id) {
      return { parentId: null, index: items.indexOf(item) };
    }
    if (item.type === "folder") {
      const index = item.children.indexOf(drag.id);
      if (index >= 0) return { parentId: item.id, index };
    }
  }
  return null;
}

function removeNode(items, drag) {
  if (drag.kind === "folder") {
    const index = items.findIndex((item) => item.type === "folder" && item.id === drag.id);
    return items.splice(index, 1)[0];
  }
  const rootIndex = items.findIndex((item) => item.type === "monitor" && item.id === drag.id);
  if (rootIndex >= 0) return items.splice(rootIndex, 1)[0];
  for (const item of items) {
    if (item.type !== "folder") continue;
    const index = item.children.indexOf(drag.id);
    if (index >= 0) {
      item.children.splice(index, 1);
      return { type: "monitor", id: drag.id, children: [] };
    }
  }
  return null;
}

function insertNode(items, node, parentId, index) {
  if (parentId == null) {
    items.splice(Math.min(index, items.length), 0, node);
    return;
  }
  const folder = items.find((item) => item.type === "folder" && item.id === parentId);
  folder.children.splice(Math.min(index, folder.children.length), 0, node.id);
}

async function createFolder() {
  const folder = await api("/api/folders", { method: "POST", body: JSON.stringify({ name: "Folder" }) });
  state.renaming = folder.id;
  state.listStamp = "";
  await refresh();
}

function onFolderMenu(event) {
  const action = event.target.dataset.action;
  const folderId = state.menuFolderId;
  folderMenu.hidden = true;
  if (!action || folderId == null) return;
  if (action === "rename") {
    state.renaming = folderId;
    state.listStamp = "";
    renderList();
  }
  if (action === "delete") {
    const folder = state.folders.find((entry) => entry.id === folderId);
    if (!folder || !confirm(`Delete folder “${folder.name}”? Sites inside it stay in the list.`)) return;
    api(`/api/folders/${folderId}`, { method: "DELETE" }).then(() => {
      state.listStamp = "";
      return refresh();
    });
  }
}

async function saveFolderName(folderId, name) {
  if (state.renaming !== folderId) return;
  state.renaming = null;
  const trimmed = name.trim();
  if (trimmed) {
    await api(`/api/folders/${folderId}`, { method: "PATCH", body: JSON.stringify({ name: trimmed }) });
  }
  state.listStamp = "";
  await refresh();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

refresh().catch((error) => {
  summaryEl.textContent = error.message;
});
setInterval(() => {
  if (!editor.open && !state.drag && !state.renaming) refresh().catch(() => {});
}, 3000);
