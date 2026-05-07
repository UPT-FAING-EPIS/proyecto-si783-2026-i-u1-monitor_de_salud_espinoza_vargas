/* dashboard.js — DB Health Monitor */

// ── State ──────────────────────────────────────────────────────────────────────
let currentTab   = 'dashboard';
let currentDsId  = 'global';
let refreshTimer = null;
let REFRESH_MS   = 30000;
let datasources  = [];

// ── Charts ────────────────────────────────────────────────────────────────────
const MAX_POINTS = 40;
const chartDefaults = {
  responsive: true, maintainAspectRatio: true,
  animation: { duration: 300 },
  plugins: { legend: { display: true, labels: { color: '#94a3b8', font: { size: 11 } } } },
  scales: {
    x: { ticks: { color: '#64748b', maxTicksLimit: 6, font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.04)' } },
    y: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.04)' } }
  }
};

function mkChart(id, label, color, yMax) {
  const ctx = document.getElementById(id);
  if (!ctx) return null;
  return new Chart(ctx, {
    type: 'line',
    data: { labels: [], datasets: [{ label, data: [], borderColor: color, backgroundColor: color + '20', fill: true, tension: 0.4, pointRadius: 2 }] },
    options: { ...chartDefaults, scales: { ...chartDefaults.scales, y: { ...chartDefaults.scales.y, max: yMax } } }
  });
}

function mkDualChart(id, l1, c1, l2, c2) {
  const ctx = document.getElementById(id);
  if (!ctx) return null;
  return new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        { label: l1, data: [], borderColor: c1, backgroundColor: c1 + '20', fill: false, tension: 0.4, pointRadius: 2 },
        { label: l2, data: [], borderColor: c2, backgroundColor: c2 + '20', fill: false, tension: 0.4, pointRadius: 2 }
      ]
    },
    options: { ...chartDefaults, scales: { ...chartDefaults.scales, y: { ...chartDefaults.scales.y, max: 100 } } }
  });
}

const charts = {};

function initCharts() {
  charts.conn  = mkChart('chart-conn',  'Conexiones',     '#4a9eff', undefined);
  charts.cache = mkChart('chart-cache', 'Cache Hit %',    '#a855f7', 100);
  charts.sys   = mkDualChart('chart-sys', 'CPU %', '#f59e0b', 'RAM %', '#10b981');
}

function pushChart(chart, label, ...values) {
  if (!chart) return;
  chart.data.labels.push(label);
  values.forEach((v, i) => chart.data.datasets[i].data.push(v));
  if (chart.data.labels.length > MAX_POINTS) {
    chart.data.labels.shift();
    chart.data.datasets.forEach(d => d.data.shift());
  }
  chart.update('none');
}

// ── Tab navigation ─────────────────────────────────────────────────────────────
function showTab(tab) {
  currentTab = tab;
  ['dashboard', 'datasources', 'import'].forEach(t => {
    document.getElementById('section-' + t).classList.toggle('hidden', t !== tab);
    document.getElementById('tab-' + t).classList.toggle('active', t === tab);
  });
  if (tab === 'datasources') loadDatasourcesTable();
  if (tab === 'import')      { loadImportHistory(); loadDsSelects(); }
}

// ── Datasource selector (dashboard) ───────────────────────────────────────────
function onDsChange() {
  currentDsId = document.getElementById('ds-select').value;
}

function populateDsSelect(ds_list) {
  datasources = ds_list;
  const sel = document.getElementById('ds-select');
  const cur = sel.value;
  sel.innerHTML = '<option value="global">🌐 Vista Global</option>';
  ds_list.forEach(ds => {
    const opt = document.createElement('option');
    opt.value = ds.id;
    opt.text  = `${ds.nombre} (${ds.tipo_db})`;
    if (!ds.activa) opt.text += ' ⏸';
    sel.appendChild(opt);
  });
  if (cur) sel.value = cur;
  currentDsId = sel.value;
}

function loadDsSelects() {
  const sel = document.getElementById('import-ds-select');
  sel.innerHTML = '<option value="">— Selecciona fuente —</option>';
  datasources.filter(d => d.activa).forEach(ds => {
    const opt = document.createElement('option');
    opt.value = ds.id;
    opt.text  = `${ds.nombre} (${ds.host})`;
    sel.appendChild(opt);
  });
}

// ── KPI helpers ────────────────────────────────────────────────────────────────
function pct(v) { return typeof v === 'number' ? v.toFixed(1) + '%' : '–'; }
function num(v) { return typeof v === 'number' ? v : '–'; }

function statusClass(s) {
  if (!s) return 'pill-unk';
  if (s === 'OK')       return 'pill-ok';
  if (s === 'WARNING')  return 'pill-warn';
  if (s === 'CRITICAL') return 'pill-crit';
  return 'pill-unk';
}

function setBar(id, value, max = 100) {
  const el = document.getElementById(id);
  if (el) el.style.width = Math.min(100, (value / max) * 100).toFixed(1) + '%';
}

function setEl(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function setKpi(m) {
  if (!m) return;
  setEl('kpi-conn-val',    `${num(m.threads_connected)}/${num(m.max_connections)}`);
  setEl('kpi-conn-pct',    pct(m.connection_pct) + ' uso');
  setEl('kpi-cache-val',   pct(m.cache_hit_ratio));
  setEl('kpi-cpu-val',     pct(m.cpu_pct));
  setEl('kpi-mem-val',     pct(m.mem_pct));
  setEl('kpi-size-val',    typeof m.db_size_mb === 'number' ? m.db_size_mb.toFixed(1) + ' MB' : '–');
  setEl('kpi-status-val',  m.status || '–');
  setEl('kpi-threads-sub', `${num(m.threads_running)} activos / ${num(m.threads_waiting)} esperando`);

  setBar('kpi-conn-bar',  m.connection_pct);
  setBar('kpi-cache-bar', m.cache_hit_ratio);
  setBar('kpi-cpu-bar',   m.cpu_pct);
  setBar('kpi-mem-bar',   m.mem_pct);

  const statusEl = document.getElementById('kpi-status-val');
  if (statusEl) {
    statusEl.className = 'kpi-value ' + (m.status === 'OK' ? 'status-ok' : m.status === 'WARNING' ? 'status-warn' : 'status-crit');
  }

  const ts = new Date().toLocaleTimeString();
  pushChart(charts.conn,  ts, m.threads_connected);
  pushChart(charts.cache, ts, m.cache_hit_ratio);
  pushChart(charts.sys,   ts, m.cpu_pct, m.mem_pct);

  setEl('last-update', 'Actualizado: ' + new Date().toLocaleTimeString());
}

// ── Global summary table ───────────────────────────────────────────────────────
function renderGlobalTable(snap) {
  const tbody = document.getElementById('global-table-body');
  if (!tbody) return;
  const entries = Object.entries(snap);
  if (!entries.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-row">Sin fuentes activas</td></tr>';
    return;
  }
  tbody.innerHTML = entries.map(([id, v]) => {
    const m  = v.metrics || {};
    const ds = datasources.find(d => d.id == id) || {};
    const st = m.status || 'unknown';
    return `<tr>
      <td>${ds.nombre || 'DS #'+id}</td>
      <td>${ds.tipo_db || '–'}</td>
      <td style="font-family:monospace">${ds.host || '–'}</td>
      <td>${num(m.threads_connected)}/${num(m.max_connections)}</td>
      <td>${pct(m.cache_hit_ratio)}</td>
      <td><span class="pill ${statusClass(st)}">${st}</span></td>
    </tr>`;
  }).join('');
}

// ── Main fetch loop ────────────────────────────────────────────────────────────
async function fetchAndUpdate() {
  try {
    // Always load datasources list
    const dsRes = await fetch('/api/datasources');
    if (dsRes.ok) {
      const ds_list = await dsRes.json();
      populateDsSelect(ds_list);
    }

    if (currentDsId === 'global') {
      // Global summary
      const res = await fetch('/api/summary/global');
      if (res.ok) {
        const data = await res.json();
        const st   = data.global_status || 'OK';
        setGlobalStatus(st);
        // Show first online source in KPIs
        const metricsRes = await fetch('/api/metrics');
        if (metricsRes.ok) {
          const snap = await metricsRes.json();
          const firstId = Object.keys(snap).find(k => snap[k].metrics);
          if (firstId) setKpi(snap[firstId].metrics);
          renderGlobalTable(snap);
        }
      }
    } else {
      const res = await fetch(`/api/summary/${currentDsId}`);
      if (res.status === 202) { setEl('last-update', 'Cargando…'); return; }
      if (res.ok) {
        const data = await res.json();
        if (data.metrics) setKpi(data.metrics);
        if (data.error)   setEl('last-update', '⚠ ' + data.error);
        const gs = data.metrics ? data.metrics.status : 'unknown';
        setGlobalStatus(gs);
      }
    }
    document.getElementById('conn-dot').className = 'conn-dot dot-ok';
  } catch (e) {
    console.error('fetchAndUpdate:', e);
    document.getElementById('conn-dot').className = 'conn-dot dot-err';
  }
}

function setGlobalStatus(st) {
  const el = document.getElementById('global-status');
  if (!el) return;
  el.textContent  = st;
  el.className    = 'status-badge ' + (st === 'OK' ? 'badge-ok' : st === 'WARNING' ? 'badge-warn' : 'badge-crit');
}

function startLoop() {
  fetchAndUpdate();
  refreshTimer = setInterval(fetchAndUpdate, REFRESH_MS);
}

// ── Datasources CRUD ───────────────────────────────────────────────────────────
async function loadDatasourcesTable() {
  const tbody = document.getElementById('ds-table-body');
  try {
    const res  = await fetch('/api/datasources');
    const list = await res.json();
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-row">Sin fuentes configuradas</td></tr>';
      return;
    }
    tbody.innerHTML = list.map(ds => {
      const st = ds.status || 'unknown';
      return `<tr>
        <td>${ds.id}</td>
        <td><strong>${ds.nombre}</strong></td>
        <td>${ds.tipo_db}</td>
        <td style="font-family:monospace">${ds.host}:${ds.puerto}</td>
        <td>${ds.database}</td>
        <td><span class="pill ${statusClass(st)}">${st}</span></td>
        <td style="display:flex;gap:0.4rem;flex-wrap:wrap">
          <button class="btn-sm success" onclick="testDs(${ds.id})">🔌 Test</button>
          <button class="btn-sm" onclick="editDs(${ds.id})">✏️ Editar</button>
          <button class="btn-sm danger" onclick="deleteDs(${ds.id}, '${ds.nombre}')">🗑</button>
        </td>
      </tr>`;
    }).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-row">Error: ${e.message}</td></tr>`;
  }
}

async function testDs(id) {
  const res  = await fetch(`/api/datasources/${id}/test`, { method: 'POST' });
  const data = await res.json();
  alert(data.ok ? `✅ Conectado en ${data.latency_ms} ms` : `❌ Error: ${data.error}`);
  loadDatasourcesTable();
}

async function deleteDs(id, nombre) {
  if (!confirm(`¿Eliminar "${nombre}"?`)) return;
  await fetch(`/api/datasources/${id}`, { method: 'DELETE' });
  loadDatasourcesTable();
}

function openDsModal(ds = null) {
  document.getElementById('modal-title').textContent = ds ? 'Editar Fuente' : 'Nueva Fuente de Datos';
  document.getElementById('modal-ds-id').value   = ds ? ds.id : '';
  document.getElementById('ds-nombre').value     = ds ? ds.nombre : '';
  document.getElementById('ds-tipo').value       = ds ? ds.tipo_db : 'postgresql';
  document.getElementById('ds-host').value       = ds ? ds.host : '';
  document.getElementById('ds-puerto').value     = ds ? ds.puerto : '5432';
  document.getElementById('ds-usuario').value   = ds ? ds.usuario : '';
  document.getElementById('ds-password').value  = '';
  document.getElementById('ds-database').value  = ds ? ds.database : '';
  document.getElementById('ds-activa').checked  = ds ? ds.activa : true;
  document.getElementById('test-result').textContent = '';
  document.getElementById('test-result').className   = 'test-result';
  document.getElementById('ds-modal').classList.remove('hidden');
}

function closeDsModal() {
  document.getElementById('ds-modal').classList.add('hidden');
}

async function editDs(id) {
  const res = await fetch('/api/datasources');
  const list = await res.json();
  const ds = list.find(d => d.id === id);
  if (ds) openDsModal(ds);
}

async function testDsModal() {
  const body = getDsFormData();
  const dsId = document.getElementById('modal-ds-id').value;
  const resultEl = document.getElementById('test-result');
  resultEl.textContent = '⏳ Probando…';
  resultEl.className   = 'test-result';

  let res, data;
  if (dsId) {
    res  = await fetch(`/api/datasources/${dsId}/test`, { method: 'POST' });
    data = await res.json();
  } else {
    // Save temp then test - just test with a POST create + test + delete
    res  = await fetch('/api/datasources', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const created = await res.json();
    if (created.id) {
      const tres = await fetch(`/api/datasources/${created.id}/test`, { method: 'POST' });
      data = await tres.json();
      await fetch(`/api/datasources/${created.id}`, { method: 'DELETE' });
    } else { data = { ok: false, error: created.error }; }
  }

  resultEl.textContent = data.ok ? `✅ OK (${data.latency_ms} ms)` : `❌ ${data.error}`;
  resultEl.className   = 'test-result ' + (data.ok ? 'ok' : 'fail');
}

function getDsFormData() {
  return {
    nombre:   document.getElementById('ds-nombre').value.trim(),
    tipo_db:  document.getElementById('ds-tipo').value,
    host:     document.getElementById('ds-host').value.trim(),
    puerto:   parseInt(document.getElementById('ds-puerto').value),
    usuario:  document.getElementById('ds-usuario').value.trim(),
    password: document.getElementById('ds-password').value,
    database: document.getElementById('ds-database').value.trim(),
    activa:   document.getElementById('ds-activa').checked,
  };
}

async function saveDsModal() {
  const data  = getDsFormData();
  const dsId  = document.getElementById('modal-ds-id').value;
  const method = dsId ? 'PUT' : 'POST';
  const url    = dsId ? `/api/datasources/${dsId}` : '/api/datasources';
  const res    = await fetch(url, {
    method, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  const result = await res.json();
  if (res.ok) {
    closeDsModal();
    loadDatasourcesTable();
  } else {
    alert('Error: ' + (result.error || 'Desconocido'));
  }
}

// ── SQL Import ─────────────────────────────────────────────────────────────────
let selectedFile = null;

function onFileSelect(input) {
  selectedFile = input.files[0] || null;
  const dropText = document.getElementById('drop-text');
  if (selectedFile) {
    dropText.textContent = `📄 ${selectedFile.name} (${(selectedFile.size/1024).toFixed(1)} KB)`;
    document.getElementById('drop-zone').classList.add('drag-over');
  } else {
    dropText.textContent = 'Haz clic o arrastra un archivo .sql aquí';
    document.getElementById('drop-zone').classList.remove('drag-over');
  }
  updateImportBtn();
}

function updateImportBtn() {
  const dsId = document.getElementById('import-ds-select').value;
  document.getElementById('btn-import').disabled = !(selectedFile && dsId);
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('import-ds-select').addEventListener('change', updateImportBtn);

  // Drag & drop
  const dz = document.getElementById('drop-zone');
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag-over'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag-over'));
  dz.addEventListener('drop', e => {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (f) { selectedFile = f; document.getElementById('drop-text').textContent = `📄 ${f.name}`; updateImportBtn(); }
  });
});

async function runImport() {
  const dsId = document.getElementById('import-ds-select').value;
  if (!selectedFile || !dsId) return;

  const btn = document.getElementById('btn-import');
  btn.disabled   = true;
  btn.textContent = '⏳ Importando…';

  const form = new FormData();
  form.append('datasource_id', dsId);
  form.append('file', selectedFile);

  const resultCard = document.getElementById('import-result');
  const resultBody = document.getElementById('import-result-body');
  resultCard.style.display = 'block';
  resultBody.innerHTML = '<div style="color:var(--text-dim)">Procesando…</div>';

  try {
    const res  = await fetch('/api/import-sql', { method: 'POST', body: form });
    const data = await res.json();

    if (!res.ok) {
      resultBody.innerHTML = `<div style="color:var(--red)">❌ ${data.error}</div>`;
    } else {
      const ok  = data.status === 'success';
      resultBody.innerHTML = `
        <div class="result-stat"><span>Estado</span><strong style="color:${ok?'var(--green)':'var(--red)'}">${ok ? '✅ Éxito' : '❌ Fallido'}</strong></div>
        <div class="result-stat"><span>Sentencias OK</span><strong>${data.statements_ok}</strong></div>
        <div class="result-stat"><span>Sentencias fallidas</span><strong>${data.statements_failed}</strong></div>
        <div class="result-stat"><span>Total</span><strong>${data.total_statements}</strong></div>
        ${data.errors && data.errors.length ? `<div class="result-errors">${data.errors.join('\n')}</div>` : ''}
      `;
    }
    loadImportHistory();
  } catch (e) {
    resultBody.innerHTML = `<div style="color:var(--red)">Error de red: ${e.message}</div>`;
  }

  btn.disabled   = false;
  btn.textContent = '▶ Ejecutar importación';
}

async function loadImportHistory() {
  const tbody = document.getElementById('import-history-body');
  try {
    const res  = await fetch('/api/import-history');
    const rows = await res.json();
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-row">Sin importaciones</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(r => {
      const st = r.status;
      const cls = st === 'success' ? 'pill-ok' : st === 'blocked' ? 'pill-warn' : 'pill-crit';
      const ts  = r.uploaded_at ? new Date(r.uploaded_at).toLocaleString() : '–';
      return `<tr>
        <td style="font-size:0.75rem">${ts}</td>
        <td>${r.ds_nombre || 'DS #'+r.datasource_id}</td>
        <td style="font-family:monospace;font-size:0.75rem">${r.filename}</td>
        <td><span class="pill ${cls}">${st}</span></td>
        <td>${r.statements_ok}</td>
        <td>${r.statements_failed}</td>
        <td style="font-size:0.72rem;color:var(--red);max-width:200px;overflow:hidden;text-overflow:ellipsis">${r.error_message || ''}</td>
      </tr>`;
    }).join('');
  } catch(e) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-row">Error: ${e.message}</td></tr>`;
  }
}

// ── Init ───────────────────────────────────────────────────────────────────────
(async () => {
  // Get config first
  try {
    const cfg = await (await fetch('/api/config')).json();
    REFRESH_MS = (cfg.refresh_interval || 30) * 1000;
  } catch(e) {}

  initCharts();
  startLoop();
})();
