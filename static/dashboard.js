/* dashboard.js — DB Health Monitor */

let REFRESH_MS = 10000;
let countdown = 10;
let currentDatasourceId = null;
let datasources = [];
let fileTypeDefs = [];
let selectedFileTypes = [];
let countdownTimer = null;
let pollTimer = null;
let activeTab = 'overview';
let currentUserRole = 'viewer';
let currentUsername = '';

const history = {
  labels: [],
  conn: [],
  cache: [],
  cpu: [],
  mem: [],
};

const MAX_POINTS = 30;
const CHART_COLOR = {
  conn: '#4a9eff',
  cache: '#a855f7',
  cpu: '#f59e0b',
  mem: '#10b981',
};

function $(id) { return document.getElementById(id); }

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function fmtNum(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '–';
  return Number(value).toLocaleString('es-MX');
}

function fmtPct(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '–';
  return `${Number(value).toFixed(1)}%`;
}

function fmtSizeMB(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '–';
  const mb = Number(value);
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`;
  return `${mb.toFixed(2)} MB`;
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '–';
  const s = Math.max(0, Number(seconds));
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const secs = Math.floor(s % 60);
  return `${days}d ${String(hours).padStart(2, '0')}h ${String(minutes).padStart(2, '0')}m ${String(secs).padStart(2, '0')}s`;
}

function statusClass(status) {
  if (status === 'OK') return 'pill-ok';
  if (status === 'WARNING') return 'pill-warn';
  if (status === 'CRITICAL') return 'pill-crit';
  return 'pill-unk';
}

function setBar(id, value) {
  const el = $(id);
  if (!el) return;
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  el.style.width = `${pct}%`;
}

function getSelectedFileTypesKey() {
  const ds = datasources.find(d => String(d.id) === String(currentDatasourceId));
  return `selected-file-types:${ds?.tipo_db || 'global'}`;
}

function loadStoredFileTypes() {
  try {
    const raw = localStorage.getItem(getSelectedFileTypesKey());
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveStoredFileTypes(types) {
  try {
    localStorage.setItem(getSelectedFileTypesKey(), JSON.stringify(types));
  } catch {
    // ignore
  }
}

function initCharts() {
  const common = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } },
    scales: {
      x: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.04)' } },
      y: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.04)' } },
    },
  };

  const connCtx = $('chart-conn');
  const cacheCtx = $('chart-cache');
  const sysCtx = $('chart-sys');

  window.connChart = connCtx ? new Chart(connCtx, {
    type: 'line',
    data: { labels: [], datasets: [{ label: 'Conexiones', data: [], borderColor: CHART_COLOR.conn, backgroundColor: 'rgba(74,158,255,0.12)', fill: true, tension: 0.35, pointRadius: 1 }] },
    options: common,
  }) : null;

  window.cacheChart = cacheCtx ? new Chart(cacheCtx, {
    type: 'line',
    data: { labels: [], datasets: [{ label: 'Cache Hit %', data: [], borderColor: CHART_COLOR.cache, backgroundColor: 'rgba(168,85,247,0.12)', fill: true, tension: 0.35, pointRadius: 1 }] },
    options: { ...common, scales: { ...common.scales, y: { ...common.scales.y, max: 100 } } },
  }) : null;

  window.sysChart = sysCtx ? new Chart(sysCtx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        { label: 'CPU %', data: [], borderColor: CHART_COLOR.cpu, backgroundColor: 'rgba(245,158,11,0.12)', fill: false, tension: 0.35, pointRadius: 1 },
        { label: 'RAM %', data: [], borderColor: CHART_COLOR.mem, backgroundColor: 'rgba(16,185,129,0.12)', fill: false, tension: 0.35, pointRadius: 1 },
      ],
    },
    options: { ...common, scales: { ...common.scales, y: { ...common.scales.y, max: 100 } } },
  }) : null;
}

function pushPoint(label, conn, cache, cpu, mem) {
  history.labels.push(label);
  history.conn.push(conn);
  history.cache.push(cache);
  history.cpu.push(cpu);
  history.mem.push(mem);
  if (history.labels.length > MAX_POINTS) {
    history.labels.shift();
    history.conn.shift();
    history.cache.shift();
    history.cpu.shift();
    history.mem.shift();
  }
  if (window.connChart) {
    window.connChart.data.labels = history.labels;
    window.connChart.data.datasets[0].data = history.conn;
    window.connChart.update('none');
  }
  if (window.cacheChart) {
    window.cacheChart.data.labels = history.labels;
    window.cacheChart.data.datasets[0].data = history.cache;
    window.cacheChart.update('none');
  }
  if (window.sysChart) {
    window.sysChart.data.labels = history.labels;
    window.sysChart.data.datasets[0].data = history.cpu;
    window.sysChart.data.datasets[1].data = history.mem;
    window.sysChart.update('none');
  }
}

async function apiFetch(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 401) {
    showLogin(true);
    throw new Error('unauthorized');
  }
  return response;
}

function showLogin(show) {
  $('login-screen')?.classList.toggle('hidden', !show);
  $('app-shell')?.classList.toggle('hidden', show);
}

function showAuthMode(mode) {
  const loginForm = $('login-form');
  const registerForm = $('register-form');
  const loginBtn = $('mode-login');
  const registerBtn = $('mode-register');
  const isRegister = mode === 'register';
  loginForm?.classList.toggle('hidden', isRegister);
  registerForm?.classList.toggle('hidden', !isRegister);
  loginBtn?.classList.toggle('active', !isRegister);
  registerBtn?.classList.toggle('active', isRegister);
}

function applyRoleVisibility(role) {
  currentUserRole = role || 'viewer';
  const adminControls = document.querySelectorAll('.admin-only');
  adminControls.forEach(el => {
    el.classList.toggle('hidden', currentUserRole !== 'admin');
  });
  if (currentUserRole !== 'admin' && activeTab === 'admin') {
    setActiveTab('overview');
  }
}

function applySessionInfo(username, role) {
  currentUsername = username || '';
  applyRoleVisibility(role || 'viewer');
  const chip = $('user-chip');
  if (chip) chip.textContent = currentUsername ? `Usuario: ${currentUsername}` : 'Usuario: –';
  document.title = currentUsername ? `DB Health Monitor · ${currentUsername}` : 'DB Health Monitor';
}

function loadStoredTab() {
  try {
    return localStorage.getItem('dashboard-active-tab') || 'overview';
  } catch {
    return 'overview';
  }
}

function saveStoredTab(tab) {
  try {
    localStorage.setItem('dashboard-active-tab', tab);
  } catch {
    // ignore
  }
}

function setActiveTab(tab) {
  if (tab === 'admin' && currentUserRole !== 'admin') {
    tab = 'overview';
  }
  activeTab = tab;
  const buttons = document.querySelectorAll('[data-tab-target]');
  const panels = document.querySelectorAll('[data-tab-panel]');

  buttons.forEach(button => {
    button.classList.toggle('active', button.getAttribute('data-tab-target') === tab);
  });
  panels.forEach(panel => {
    panel.classList.toggle('hidden', panel.getAttribute('data-tab-panel') !== tab);
  });
  saveStoredTab(tab);
}

function setGlobalStatus(status) {
  const el = $('global-status');
  if (!el) return;
  el.textContent = status || '–';
  el.className = `status-badge ${statusClass(status)}`;
}

function renderDatasourceSelect() {
  const select = $('db-select');
  if (!select) return;
  const current = currentDatasourceId;
  select.innerHTML = '';

  if (!datasources.length) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = 'Sin fuentes de datos';
    select.appendChild(opt);
    select.disabled = true;
    currentDatasourceId = null;
    return;
  }
  select.disabled = false;

  datasources.forEach(ds => {
    const opt = document.createElement('option');
    opt.value = ds.id;
    const ownerLabel = currentUserRole === 'admin' && ds.owner_username ? ` - ${ds.owner_username}` : '';
    opt.textContent = `${ds.nombre} (${ds.tipo_db})${ownerLabel}`;
    if (!ds.activa) opt.textContent += ' ⏸';
    select.appendChild(opt);
  });

  if (!current && datasources.length) {
    currentDatasourceId = datasources[0].id;
  } else if (current) {
    currentDatasourceId = current;
  }

  select.value = String(currentDatasourceId || '');
}

function renderDatasourceTable() {
  const tbody = $('datasource-body');
  if (!tbody) return;
  if (!datasources.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-row">Todavía no has creado fuentes de datos</td></tr>';
    return;
  }

  tbody.innerHTML = datasources.map(ds => `
    <tr>
      <td><strong>${ds.nombre}</strong></td>
      <td>${ds.tipo_db}</td>
      <td style="font-family:'JetBrains Mono', monospace">${ds.host}:${ds.puerto}</td>
      <td>${ds.database}</td>
      <td><span class="pill ${ds.activa ? 'pill-ok' : 'pill-unk'}">${ds.activa ? 'Activa' : 'Inactiva'}</span></td>
      <td class="row-actions">
        <button type="button" class="btn-sm" data-test-ds="${ds.id}">Probar</button>
      </td>
    </tr>
  `).join('');

  tbody.querySelectorAll('[data-test-ds]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const dsId = btn.getAttribute('data-test-ds');
      const response = await apiFetch(`/api/datasources/${dsId}/test`, { method: 'POST' });
      const data = await response.json();
      btn.textContent = data.ok ? `${data.latency_ms} ms` : 'Error';
      btn.classList.toggle('btn-error', !data.ok);
      btn.classList.toggle('btn-success', data.ok);
    });
  });
}

function renderFileTypeChips() {
  const wrap = $('file-type-chips');
  const summary = $('file-type-summary');
  if (!wrap) return;
  wrap.innerHTML = '';

  const defaultTypes = fileTypeDefs.map(item => item.key);
  if (!selectedFileTypes.length) selectedFileTypes = loadStoredFileTypes();
  if (!selectedFileTypes.length) selectedFileTypes = defaultTypes;

  if (summary) {
    summary.textContent = selectedFileTypes.length
      ? `Filtros activos (${selectedFileTypes.length}/${defaultTypes.length || selectedFileTypes.length}): ${selectedFileTypes.join(', ')}`
      : 'Filtros activos: todos';
  }

  fileTypeDefs.forEach(def => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `chip ${selectedFileTypes.includes(def.key) ? 'chip-active' : ''}`;
    btn.textContent = def.label;
    btn.title = def.description;
    btn.setAttribute('aria-pressed', selectedFileTypes.includes(def.key) ? 'true' : 'false');
    btn.addEventListener('click', () => {
      if (selectedFileTypes.includes(def.key)) {
        selectedFileTypes = selectedFileTypes.filter(v => v !== def.key);
      } else {
        selectedFileTypes = [...selectedFileTypes, def.key];
      }
      if (!selectedFileTypes.length) selectedFileTypes = defaultTypes;
      saveStoredFileTypes(selectedFileTypes);
      renderFileTypeChips();
      loadFiles();
    });
    wrap.appendChild(btn);
  });
}

function renderSummaryTable(summaryMap) {
  const tbody = $('summary-body');
  if (!tbody) return;
  const rows = Object.entries(summaryMap || {});
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-row">Sin datos</td></tr>';
    return;
  }

  tbody.innerHTML = rows.map(([id, entry]) => {
    const ds = datasources.find(item => String(item.id) === String(id)) || {};
    const status = entry?.metrics?.status || 'unknown';
    return `
      <tr>
        <td>${ds.nombre || `BD #${id}`}</td>
        <td><span class="pill ${entry?.metrics ? 'pill-ok' : 'pill-unk'}">${entry?.metrics ? 'Sí' : 'No'}</span></td>
        <td><span class="pill ${statusClass(status)}">${status}</span></td>
        <td>${entry?.ts ? new Date(entry.ts).toLocaleString('es-MX') : '–'}</td>
        <td title="${(entry?.error || '').replace(/"/g, '&quot;')}">${entry?.error || '–'}</td>
      </tr>`;
  }).join('');
}

function renderDbStatusTable(metricsMap) {
  const tbody = $('db-status-body');
  if (!tbody) return;
  const rows = datasources.map(ds => {
    const entry = metricsMap?.[ds.id] || {};
    const m = entry.metrics || {};
    return `
      <tr>
        <td><strong>${ds.nombre}</strong></td>
        <td>${ds.tipo_db}</td>
        <td style="font-family:'JetBrains Mono', monospace">${ds.host}:${ds.puerto}</td>
        <td>${m.threads_connected !== undefined ? `${fmtNum(m.threads_connected)}/${fmtNum(m.max_connections)}` : '–'}</td>
        <td>${m.cache_hit_ratio !== undefined ? fmtPct(m.cache_hit_ratio) : '–'}</td>
        <td><span class="pill ${statusClass(m.status || (entry.error ? 'CRITICAL' : 'unknown'))}">${m.status || (entry.error ? 'ERROR' : 'unknown')}</span></td>
      </tr>`;
  });

  tbody.innerHTML = rows.length ? rows.join('') : '<tr><td colspan="6" class="empty-row">Sin bases de datos</td></tr>';
}

function renderAlerts(alerts) {
  const tbody = $('alerts-body');
  if (!tbody) return;
  if (!alerts || !alerts.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-row">Sin alertas recientes</td></tr>';
    return;
  }
  tbody.innerHTML = alerts.map(alert => `
    <tr>
      <td>${alert.alerted_at ? new Date(alert.alerted_at).toLocaleString('es-MX') : '–'}</td>
      <td><span class="pill ${statusClass(alert.severity)}">${alert.severity}</span></td>
      <td>${alert.metric_name || alert.metric || '–'}</td>
      <td>${alert.metric_value || alert.value || '–'}</td>
      <td title="${(alert.message || '').replace(/"/g, '&quot;')}">${alert.message || '–'}</td>
    </tr>
  `).join('');
}

function renderAdminOverview(data) {
  const usersBody = $('admin-users-body');
  const datasourcesBody = $('admin-datasources-body');
  if (!usersBody || !datasourcesBody) return;

  const users = data?.users || [];
  const sources = data?.datasources || [];

  setText('admin-users-count', fmtNum(data?.counts?.users ?? users.length));
  setText('admin-datasources-count', fmtNum(data?.counts?.datasources ?? sources.length));

  usersBody.innerHTML = users.length ? users.map(user => `
    <tr>
      <td>${user.username}</td>
      <td><span class="pill ${user.role === 'admin' ? 'pill-warn' : 'pill-unk'}">${user.role || 'user'}</span></td>
      <td><span class="pill ${user.active ? 'pill-ok' : 'pill-crit'}">${user.active ? 'Sí' : 'No'}</span></td>
      <td>${user.created_at ? new Date(user.created_at).toLocaleString('es-MX') : '–'}</td>
      <td>${user.last_login ? new Date(user.last_login).toLocaleString('es-MX') : '–'}</td>
    </tr>
  `).join('') : '<tr><td colspan="5" class="empty-row">Sin usuarios</td></tr>';

  datasourcesBody.innerHTML = sources.length ? sources.map(source => `
    <tr>
      <td><strong>${source.nombre}</strong></td>
      <td>${source.owner_username || '–'}</td>
      <td>${source.tipo_db}</td>
      <td style="font-family:'JetBrains Mono', monospace">${source.host}:${source.puerto}</td>
      <td>${source.database}</td>
      <td><span class="pill ${source.activa ? 'pill-ok' : 'pill-unk'}">${source.activa ? 'Activa' : 'Inactiva'}</span></td>
    </tr>
  `).join('') : '<tr><td colspan="6" class="empty-row">Sin fuentes</td></tr>';
}

function renderFiles(data) {
  const tbody = $('file-table-body');
  if (!tbody) return;
  const files = data?.files || [];
  if (!currentDatasourceId) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-row">Selecciona una base de datos</td></tr>';
    return;
  }
  if (!files.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-row">No hay archivos configurados para los filtros elegidos</td></tr>';
    return;
  }
  tbody.innerHTML = files.map(file => `
    <tr>
      <td>${file.label}</td>
      <td style="font-family:'JetBrains Mono', monospace; white-space:normal; max-width: 340px;">${file.path}</td>
      <td>${file.kind}</td>
      <td><span class="pill ${file.exists ? 'pill-ok' : 'pill-crit'}">${file.exists ? 'Sí' : 'No'}</span></td>
      <td>${file.size_mb ? fmtSizeMB(file.size_mb) : '–'}</td>
      <td>${file.modified_at ? new Date(file.modified_at).toLocaleString('es-MX') : '–'}</td>
      <td>${file.entries ?? '–'}</td>
    </tr>
  `).join('');
}

function setKpis(metrics) {
  if (!metrics) return;
  setText('kpi-conn-val', `${fmtNum(metrics.threads_connected)}/${fmtNum(metrics.max_connections)}`);
  setText('kpi-conn-pct', `${fmtPct(metrics.connection_pct)} uso`);
  setText('kpi-cache-val', fmtPct(metrics.cache_hit_ratio));
  setText('kpi-cpu-val', fmtPct(metrics.cpu_pct));
  setText('kpi-mem-val', fmtPct(metrics.mem_pct));
  setText('kpi-disk-val', fmtPct(metrics.disk_used_pct));
  setText('kpi-disk-sub', metrics.disk_free_gb !== undefined ? `Libre ${Number(metrics.disk_free_gb).toFixed(2)} GB` : 'Libre –');
  setText('kpi-status-val', metrics.status || '–');
  setText('kpi-threads-sub', `${fmtNum(metrics.threads_running)} activos / ${fmtNum(metrics.threads_waiting)} esperando`);

  setBar('kpi-conn-bar', metrics.connection_pct || 0);
  setBar('kpi-cache-bar', metrics.cache_hit_ratio || 0);
  setBar('kpi-cpu-bar', metrics.cpu_pct || 0);
  setBar('kpi-mem-bar', metrics.mem_pct || 0);
  setBar('kpi-disk-bar', metrics.disk_used_pct || 0);

  const statusEl = $('kpi-status-val');
  if (statusEl) statusEl.className = `kpi-value ${metrics.status === 'OK' ? 'status-ok' : metrics.status === 'WARNING' ? 'status-warn' : 'status-crit'}`;

  const label = new Date().toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  pushPoint(label, metrics.threads_connected || 0, metrics.cache_hit_ratio || 0, metrics.cpu_pct || 0, metrics.mem_pct || 0);
}

async function loadFileTypes() {
  if (!currentDatasourceId) return;
  const response = await apiFetch(`/api/file-types?datasource_id=${currentDatasourceId}`);
  if (!response.ok) return;
  fileTypeDefs = await response.json();
  renderFileTypeChips();
}

async function loadFiles() {
  if (!currentDatasourceId) {
    renderFiles({ files: [] });
    return;
  }
  const types = selectedFileTypes.length ? selectedFileTypes.join(',') : '';
  const response = await apiFetch(`/api/files?datasource_id=${currentDatasourceId}${types ? `&types=${encodeURIComponent(types)}` : ''}`);
  if (!response.ok) return;
  const data = await response.json();
  renderFiles(data);
  setText('sum-files', fmtNum(data.total || 0));
  const summary = $('file-type-summary');
  if (summary) {
    summary.textContent = data.selected_types && data.selected_types.length
      ? `Filtros activos (${data.selected_types.length}): ${data.selected_types.join(', ')}`
      : 'Filtros activos: todos';
  }
}

async function loadGlobalSummary() {
  const response = await apiFetch('/api/summary/global');
  if (!response.ok) return;
  const data = await response.json();
  setGlobalStatus(data.global_status || '–');
  setText('sum-total', fmtNum(data.total_datasources || 0));
  setText('sum-online', fmtNum(data.online || 0));
  setText('sum-offline', fmtNum(data.offline || 0));
}

async function loadAdminOverview() {
  if (currentUserRole !== 'admin') return;
  const response = await apiFetch('/api/admin/overview');
  if (!response.ok) return;
  const data = await response.json();
  renderAdminOverview(data);
}

async function loadMetricsAndTables() {
  const [metricsRes, summaryRes, alertsRes] = await Promise.all([
    apiFetch('/api/metrics'),
    apiFetch('/api/summary/global'),
    currentDatasourceId ? apiFetch(`/api/alerts/history?datasource_id=${currentDatasourceId}`) : Promise.resolve(null),
  ]);

  if (metricsRes && metricsRes.ok) {
    const metricsMap = await metricsRes.json();
    renderDbStatusTable(metricsMap);
    if (currentDatasourceId && metricsMap[currentDatasourceId]?.metrics) {
      const m = metricsMap[currentDatasourceId].metrics;
      setKpis(m);
      setText('last-update', `Actualizado: ${new Date().toLocaleString('es-MX')}`);
      setText('refresh-value', `${Math.round(REFRESH_MS / 1000)} s`);
    }
  }

  if (summaryRes && summaryRes.ok) {
    const summary = await summaryRes.json();
    renderSummaryTable(summary.datasources || {});
  }

  if (alertsRes && alertsRes.ok) {
    const alerts = await alertsRes.json();
    renderAlerts(alerts);
  }
}

async function loadDatasources() {
  const response = await apiFetch('/api/datasources');
  if (!response.ok) return;
  datasources = await response.json();
  renderDatasourceSelect();
  renderDatasourceTable();
  if (datasources.length) {
    const stillExists = datasources.some(ds => String(ds.id) === String(currentDatasourceId));
    if (!stillExists) currentDatasourceId = datasources[0].id;
  }
  $('db-select').value = String(currentDatasourceId || '');
}

async function refreshAll() {
  const tasks = [loadAdminOverview()];
  if (currentDatasourceId) {
    tasks.unshift(loadGlobalSummary(), loadMetricsAndTables(), loadFiles());
  }
  await Promise.all(tasks);
}

function startTimers() {
  clearInterval(countdownTimer);
  clearInterval(pollTimer);
  countdown = Math.max(1, Math.round(REFRESH_MS / 1000));
  setText('refresh-value', `${countdown} s`);
  countdownTimer = setInterval(() => {
    countdown -= 1;
    if (countdown <= 0) countdown = Math.max(1, Math.round(REFRESH_MS / 1000));
    setText('refresh-value', `${countdown} s`);
  }, 1000);
  pollTimer = setInterval(() => {
    refreshAll().catch(console.error);
  }, REFRESH_MS);
}

async function ensureSession() {
  try {
    const response = await fetch('/api/me');
    if (!response.ok) {
      applySessionInfo('', 'viewer');
      showLogin(true);
      return false;
    }
    const data = await response.json();
    applySessionInfo(data.user || '', data.role || 'viewer');
    showLogin(false);
    return true;
  } catch {
    applySessionInfo('', 'viewer');
    showLogin(true);
    return false;
  }
}

async function handleLogin(event) {
  event.preventDefault();
  const payload = {
    username: $('login-user').value.trim(),
    password: $('login-pass').value,
  };
  const errorBox = $('login-error');
  errorBox.classList.add('hidden');
  try {
    const response = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      errorBox.textContent = data.error || 'No se pudo iniciar sesión';
      errorBox.classList.remove('hidden');
      return;
    }
    applySessionInfo(data.user || payload.username, data.role || 'viewer');
    await boot();
  } catch (error) {
    errorBox.textContent = 'Error de red al iniciar sesión';
    errorBox.classList.remove('hidden');
  }
}

async function handleRegister(event) {
  event.preventDefault();
  const payload = {
    username: $('register-user').value.trim(),
    password: $('register-pass').value,
    confirm_password: $('register-pass2').value,
  };
  const errorBox = $('register-error');
  errorBox.classList.add('hidden');
  try {
    const response = await fetch('/api/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      errorBox.textContent = data.error || 'No se pudo crear la cuenta';
      errorBox.classList.remove('hidden');
      return;
    }
    $('login-user').value = payload.username;
    $('login-pass').value = payload.password;
    showAuthMode('login');
    $('login-error').classList.add('hidden');
    $('login-error').textContent = '';
    setText('login-error', '');
    await handleLogin({ preventDefault() {}, });
  } catch (error) {
    errorBox.textContent = 'Error de red al crear la cuenta';
    errorBox.classList.remove('hidden');
  }
}

async function handleDatasourceCreate(event) {
  event.preventDefault();
  const payload = {
    nombre: $('ds-name').value.trim(),
    tipo_db: $('ds-type').value,
    host: $('ds-host').value.trim(),
    puerto: Number($('ds-port').value || 0),
    usuario: $('ds-user').value.trim(),
    password: $('ds-pass').value,
    database: $('ds-db').value.trim(),
    activa: $('ds-active').checked,
  };
  if (!payload.nombre || !payload.host || !payload.usuario || !payload.database || !payload.puerto) return;
  const response = await apiFetch('/api/datasources', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  if (!response.ok) return;
  const data = await response.json();
  $('datasource-form').reset();
  $('ds-type').value = payload.tipo_db;
  $('ds-port').value = payload.tipo_db === 'sqlserver' ? 1433 : payload.tipo_db === 'mongodb' ? 27017 : 5432;
  $('ds-active').checked = true;
  await loadDatasources();
  currentDatasourceId = data.id;
  $('db-select').value = String(currentDatasourceId);
  await loadFileTypes();
  await refreshAll();
    setActiveTab('files');
}

async function handleLogout() {
  await fetch('/api/logout', { method: 'POST' });
  datasources = [];
  fileTypeDefs = [];
  selectedFileTypes = [];
  currentDatasourceId = null;
  currentUserRole = 'viewer';
  applySessionInfo('', 'viewer');
  showLogin(true);
}

async function boot() {
  const ok = await ensureSession();
  if (!ok) return;
  setActiveTab(loadStoredTab());
  await loadDatasources();
  if (datasources.length) {
    currentDatasourceId = currentDatasourceId || datasources[0].id;
    $('db-select').value = String(currentDatasourceId);
    await loadFileTypes();
    await refreshAll();
    startTimers();
  } else {
    renderFileTypeChips();
    renderFiles({ files: [] });
  }
  await loadAdminOverview();
}

function bindEvents() {
  $('login-form')?.addEventListener('submit', handleLogin);
  $('register-form')?.addEventListener('submit', handleRegister);
  $('datasource-form')?.addEventListener('submit', handleDatasourceCreate);
  $('logout-btn')?.addEventListener('click', handleLogout);
  $('datasource-refresh')?.addEventListener('click', async () => {
    await loadDatasources();
    if (currentDatasourceId) {
      await loadFileTypes();
      await refreshAll();
    }
  });
  $('mode-login')?.addEventListener('click', () => showAuthMode('login'));
  $('mode-register')?.addEventListener('click', () => showAuthMode('register'));
  document.querySelectorAll('[data-tab-target]').forEach(button => {
    button.addEventListener('click', () => setActiveTab(button.getAttribute('data-tab-target')));
  });
  $('db-select')?.addEventListener('change', async (event) => {
    currentDatasourceId = event.target.value;
    selectedFileTypes = loadStoredFileTypes();
    await loadFileTypes();
    await refreshAll();
    setActiveTab('files');
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  initCharts();
  bindEvents();
  showAuthMode('login');
  const sessionOk = await ensureSession();
  if (!sessionOk) {
    setActiveTab('overview');
    return;
  }
  await boot();
});
