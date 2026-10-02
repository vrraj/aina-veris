// Veris Configuration page: runtime tunables table + live memory panel.
const statusLine = document.getElementById("statusLine");
const tunablesBody = document.getElementById("tunablesBody");
const cachesBody = document.getElementById("cachesBody");
const memoryCards = document.getElementById("memoryCards");
const refreshBtn = document.getElementById("refreshBtn");
const applyBtn = document.getElementById("applyBtn");

const pending = new Map(); // key -> edited value awaiting Apply

function fmtMb(mb) {
  if (mb == null) return "—";
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}

function statCard(label, value, sub) {
  const el = document.createElement("div");
  el.className = "stat-card";
  el.innerHTML =
    `<div class="stat-label">${label}</div>` +
    `<div class="stat-value">${value}</div>` +
    (sub ? `<div class="stat-sub">${sub}</div>` : "");
  return el;
}

function renderMemory(memory) {
  memoryCards.innerHTML = "";
  const sys = memory.system;
  memoryCards.appendChild(
    statCard("Available", fmtMb(sys.available_mb), `of ${fmtMb(sys.total_mb)} total`)
  );
  memoryCards.appendChild(
    statCard("System used", `${sys.used_percent}%`, `swap ${fmtMb(memory.swap.used_mb)}`)
  );
  memoryCards.appendChild(
    statCard("App process (RSS)", fmtMb(memory.process_rss_mb), "this service")
  );
  const cg = memory.container;
  memoryCards.appendChild(
    statCard(
      "Container",
      cg ? fmtMb(cg.used_mb) : "—",
      cg ? `of ${fmtMb(cg.limit_mb)} cgroup cap` : "no cgroup limit"
    )
  );
}

function renderTunables(tunables) {
  tunablesBody.innerHTML = "";
  for (const t of tunables) {
    const tr = document.createElement("tr");
    tr.className = "border-t border-gray-100 align-top";

    const nameTd = document.createElement("td");
    nameTd.className = "py-2 pr-4 font-medium";
    nameTd.innerHTML =
      `${t.label}<div class="mono text-xs text-gray-400">${t.key}</div>`;

    const valTd = document.createElement("td");
    valTd.className = "py-2 pr-4";
    const input = document.createElement("input");
    input.className = "cfg-input mono";
    input.type = "number";
    input.min = t.min;
    input.max = t.max;
    input.step = "any";
    input.value = t.value;
    input.dataset.key = t.key;
    input.addEventListener("input", () => {
      const dirty = Number(input.value) !== Number(t.value);
      input.classList.toggle("dirty", dirty);
      if (dirty) pending.set(t.key, Number(input.value));
      else pending.delete(t.key);
      applyBtn.disabled = pending.size === 0;
    });
    valTd.appendChild(input);

    const unitTd = document.createElement("td");
    unitTd.className = "py-2 pr-4 text-gray-500";
    unitTd.textContent = t.unit;

    const rangeTd = document.createElement("td");
    rangeTd.className = "py-2 pr-4 mono text-gray-500";
    rangeTd.textContent = `${t.min}–${t.max}`;

    const descTd = document.createElement("td");
    descTd.className = "py-2 text-gray-600";
    descTd.innerHTML = t.description + (t.note ? ` <div class="note-text">Note: ${t.note}</div>` : "");

    tr.append(nameTd, valTd, unitTd, rangeTd, descTd);
    tunablesBody.appendChild(tr);
  }
}

function renderCaches(caches) {
  cachesBody.innerHTML = "";
  if (!caches.length) {
    cachesBody.innerHTML = `<tr><td class="py-2 text-gray-500" colspan="4">No model caches active.</td></tr>`;
    return;
  }
  for (const c of caches) {
    const models = c.models || [];
    if (!models.length) {
      const tr = document.createElement("tr");
      tr.className = "border-t border-gray-100 text-gray-500";
      tr.innerHTML =
        `<td class="py-2 pr-4"><div>${c.name}</div><div class="text-xs">${c.description}</div></td>` +
        `<td class="py-2 pr-4">${c.idle_timeout_s}s</td>` +
        `<td class="py-2 pr-4 italic">empty</td><td></td>`;
      cachesBody.appendChild(tr);
      continue;
    }
    models.forEach((m, i) => {
      const tr = document.createElement("tr");
      tr.className = "border-t border-gray-100";
      const head =
        i === 0
          ? `<td class="py-2 pr-4"><div>${c.name}</div><div class="text-xs text-gray-500">${c.description}</div></td>` +
            `<td class="py-2 pr-4">${c.idle_timeout_s}s</td>`
          : `<td></td><td></td>`;
      tr.innerHTML =
        head +
        `<td class="py-2 pr-4 mono text-xs" style="word-break:break-all">${m.key}</td>` +
        `<td class="py-2 text-gray-500">${m.idle_secs}s</td>`;
      cachesBody.appendChild(tr);
    });
  }
}

async function load() {
  try {
    const res = await fetch("/config/runtime");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderMemory(data.memory);
    // Don't clobber in-flight edits: only re-render inputs not being edited.
    if (pending.size === 0) renderTunables(data.tunables);
    else {
      for (const t of data.tunables) {
        if (!pending.has(t.key)) {
          const input = tunablesBody.querySelector(`input[data-key="${t.key}"]`);
          if (input) { input.value = t.value; }
        }
      }
    }
    renderCaches(data.memory.model_caches);
    statusLine.textContent = `${data.tunables.length} tunables · ${data.persistence}`;
  } catch (err) {
    statusLine.textContent = `Failed to load: ${err.message}`;
  }
}

applyBtn.addEventListener("click", async () => {
  if (pending.size === 0) return;
  applyBtn.disabled = true;
  try {
    const res = await fetch("/config/runtime", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates: Object.fromEntries(pending) }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    const nCaches = Object.values(data.caches_updated || {}).reduce((a, b) => a + b, 0);
    statusLine.textContent =
      `Applied ${Object.keys(data.applied).length} setting(s)` +
      (nCaches ? ` · ${nCaches} live cache(s) updated` : "");
    pending.clear();
    await load();
  } catch (err) {
    statusLine.textContent = `Apply failed: ${err.message}`;
    applyBtn.disabled = pending.size === 0;
  }
});

refreshBtn.addEventListener("click", load);
load();
setInterval(load, 10000);
