/* ═══════════════════════════════════════════════════════
   MySQL Health Monitor — Dashboard JS
═══════════════════════════════════════════════════════ */

// ─────────────────────────────────────────────────────
// CONFIG & STATE
// ─────────────────────────────────────────────────────
let REFRESH = 5;         // segundos (se actualiza desde /api/config)
let THRESHOLDS = {};
let countdown = REFRESH;
let historyLen = 30;     // puntos en los gráficos

const history = {
  labels: [],
  qps:    [],
  conn:   [],
  cache:  [],
};

// ─────────────────────────────────────────────────────
// CHART.JS — DEFAULTS
// ─────────────────────────────────────────────────────
Chart.defaults.color = '#64748b';
Chart.defaults.borderColor = 'rgba(255,255,255,0.05)';
Chart.defaults.font.family = "'Inter', sans-serif";

function lineChartConfig(label, color, data, yMax) {
  return {
    type: 'line',
    data: {
      labels: history.labels,
      datasets: [{
        label,
        data,
        borderColor: color,
        backgroundColor: color.replace(')', ',0.1)').replace('rgb', 'rgba'),
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.4,
        fill: true,
      }]
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: false }, tooltip: { mode: 'index' } },
      scales: {
        x: { display: false },
        y: {
          min: 0,
          max: yMax || undefined,
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: { font: { size: 10 } },
        }
      }
    }
  };
}

const qpsChart  = new Chart(document.getElementById('chart-qps'),
  lineChartConfig('QPS', 'rgb(74,158,255)', history.qps));

const connChart = new Chart(document.getElementById('chart-conn'),
  lineChartConfig('Conexiones', 'rgb(168,85,247)', history.conn));

const cacheChart = new Chart(document.getElementById('chart-cache'),
  lineChartConfig('Hit Ratio %', 'rgb(16,214,126)', history.cache, 100));

// DML doughnut
const dmlChart = new Chart(document.getElementById('chart-dml'), {
  type: 'doughnut',
  data: {
    labels: ['SELECT', 'INSERT', 'UPDATE', 'DELETE'],
    datasets: [{
      data: [0, 0, 0, 0],
      backgroundColor: ['#4a9eff','#10d67e','#f59e0b','#ef4444'],
      borderWidth: 0,
      hoverOffset: 8,
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    cutout: '65%',
    plugins: {
      legend: {
        position: 'bottom',
        labels: { font: { size: 11 }, padding: 14, boxWidth: 10 }
      }
    }
  }
});

// ─────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────
function fmtSize(mb) {
  if (mb === null || mb === undefined) return '–';
  mb = parseFloat(mb);
  if (mb >= 1024) return (mb / 1024).toFixed(2) + ' GB';
  return mb.toFixed(2) + ' MB';
}

function fmtNum(n) {
  if (n === null || n === undefined) return '–';
  return Number(n).toLocaleString('es-MX');
}

function statusClass(value, warn, crit, invert = false) {
  if (invert) {
    if (value < crit) return 'status-crit';
    if (value < warn) return 'status-warn';
    return 'status-ok';
  }
  if (value >= crit) return 'status-crit';
  if (value >= warn) return 'status-warn';
  return 'status-ok';
}

function barColor(cls) {
  if (cls === 'status-crit') return '#ef4444';
  if (cls === 'status-warn') return '#f59e0b';
  return '#10d67e';
}

function setKpiStatus(cardId, cls) {
  const el = document.getElementById(cardId);
  el.classList.remove('status-ok', 'status-warn', 'status-crit', 'status-blue');
  el.classList.add(cls);
}

function now() {
  return new Date().toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// ─────────────────────────────────────────────────────
// RENDER METRICS
// ─────────────────────────────────────────────────────
function renderMetrics(data) {
  const m = data.metrics;
  const alerts = data.alerts || [];

  // ── Header ────────────────────────────────────────
  document.getElementById('hdr-version').textContent = `MySQL ${m.version}`;
  document.getElementById('hdr-uptime').textContent  = `⏱ ${m.uptime_str}`;
  document.getElementById('last-update').textContent = new Date().toLocaleString('es-MX');
  document.getElementById('conn-status').className   = 'conn-dot dot-ok';

  // ── Alerts ───────────────────────────────────────
  renderAlerts(alerts);

  // ── KPI: Conexiones ───────────────────────────────
  const connPct = m.connection_pct;
  const connCls = statusClass(connPct,
    THRESHOLDS.connections_warning || 70,
    THRESHOLDS.connections_critical || 90);
  document.getElementById('kpi-conn-val').textContent = `${m.threads_connected}/${m.max_connections}`;
  document.getElementById('kpi-conn-pct').textContent = `${connPct.toFixed(1)}% usado`;
  setBarWidth('kpi-conn-bar', connPct, barColor(connCls));
  setKpiStatus('kpi-connections', connCls);

  // ── KPI: QPS ─────────────────────────────────────
  document.getElementById('kpi-qps-val').textContent = m.qps.toFixed(1);
  setKpiStatus('kpi-qps', 'status-blue');

  // ── KPI: Cache Hit ────────────────────────────────
  const cacheCls = statusClass(m.innodb_hit_ratio,
    THRESHOLDS.cache_hit_warning || 95,
    THRESHOLDS.cache_hit_critical || 85, true);
  document.getElementById('kpi-cache-val').textContent = `${m.innodb_hit_ratio.toFixed(2)}%`;
  setBarWidth('kpi-cache-bar', m.innodb_hit_ratio, barColor(cacheCls));
  setKpiStatus('kpi-cache', cacheCls);

  // ── KPI: Threads ─────────────────────────────────
  const thrCls = statusClass(m.threads_running,
    THRESHOLDS.threads_running_warning || 20,
    THRESHOLDS.threads_running_critical || 50);
  document.getElementById('kpi-threads-val').textContent = `${m.threads_running} / ${m.threads_cached}`;
  setKpiStatus('kpi-threads', thrCls);

  // ── KPI: Slow Queries ─────────────────────────────
  const slowCls = statusClass(m.slow_queries,
    THRESHOLDS.slow_queries_warning || 100,
    THRESHOLDS.slow_queries_critical || 500);
  document.getElementById('kpi-slow-val').textContent = fmtNum(m.slow_queries);
  setKpiStatus('kpi-slow', slowCls);

  // ── KPI: Buffer Pool ─────────────────────────────
  document.getElementById('kpi-bp-val').textContent  = `${m.bp_size_mb} MB`;
  document.getElementById('kpi-bp-sub').textContent  = `${m.bp_used_pct.toFixed(1)}% utilizado`;
  setBarWidth('kpi-bp-bar', m.bp_used_pct, '#a855f7');
  setKpiStatus('kpi-bp', 'status-blue');

  // ── Charts history ────────────────────────────────
  const ts = now();
  pushHistory(ts, m.qps, m.threads_connected, m.innodb_hit_ratio);

  // ── DML chart ─────────────────────────────────────
  dmlChart.data.datasets[0].data = [m.com_select, m.com_insert, m.com_update, m.com_delete];
  dmlChart.update('none');

  // ── Processlist ───────────────────────────────────
  renderProcesses(m.processes);

  // ── DB sizes ─────────────────────────────────────
  renderDbSizes(m.db_sizes);

  // ── Top tables ───────────────────────────────────
  renderTopTables(m.top_tables);

  // ── Replication ───────────────────────────────────
  renderReplication(m.replication);
}

function setBarWidth(id, pct, color) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.width  = Math.min(pct, 100) + '%';
  el.style.background = color;
}

function pushHistory(label, qps, conn, cache) {
  history.labels.push(label);
  history.qps.push(qps);
  history.conn.push(conn);
  history.cache.push(cache);

  if (history.labels.length > historyLen) {
    history.labels.shift();
    history.qps.shift();
    history.conn.shift();
    history.cache.shift();
  }

  qpsChart.update('none');
  connChart.update('none');
  cacheChart.update('none');
}

// ─────────────────────────────────────────────────────
// RENDER HELPERS
// ─────────────────────────────────────────────────────
function renderAlerts(alerts) {
  const bar   = document.getElementById('alert-bar');
  const badge = document.getElementById('alert-badge');
  const count = document.getElementById('alert-count');

  if (!alerts.length) {
    bar.classList.add('hidden');
    badge.classList.add('hidden');
    return;
  }

  badge.classList.remove('hidden');
  count.textContent = alerts.length;

  const hasCrit = alerts.some(a => a.severity === 'CRITICAL');
  bar.classList.remove('hidden', 'warn-only');
  if (!hasCrit) bar.classList.add('warn-only');

  bar.innerHTML = alerts.map(a =>
    `<span class="alert-pill ${a.severity}">
      ${a.severity === 'CRITICAL' ? '🔴' : '🟡'} <strong>${a.metric}</strong>: ${a.value}
      <span style="opacity:0.6;font-size:10px">(${a.threshold})</span>
    </span>`
  ).join('');
}

function renderProcesses(procs) {
  const tbody = document.getElementById('proc-body');
  document.getElementById('proc-count').textContent = procs.length;

  if (!procs.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-row">Sin procesos activos</td></tr>';
    return;
  }

  tbody.innerHTML = procs.map(p => {
    const t = parseInt(p.TIME) || 0;
    const tcls = t > 30 ? 'time-crit' : t > 10 ? 'time-warn' : 'time-ok';
    return `<tr>
      <td>${p.ID}</td>
      <td>${p.USER || ''}</td>
      <td>${p.DB || '–'}</td>
      <td>${p.COMMAND || ''}</td>
      <td class="${tcls}">${t}s</td>
      <td>${p.STATE || ''}</td>
      <td title="${(p.INFO||'').replace(/"/g,"'")}">
        ${(p.INFO || '').substring(0, 60)}${(p.INFO||'').length > 60 ? '…' : ''}
      </td>
    </tr>`;
  }).join('');
}

function renderDbSizes(sizes) {
  const tbody = document.getElementById('db-body');
  if (!sizes || !sizes.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-row">Sin bases de datos</td></tr>';
    return;
  }

  const maxMb = Math.max(...sizes.map(d => parseFloat(d.size_mb) || 0), 1);

  tbody.innerHTML = sizes.map(db => {
    const mb   = parseFloat(db.size_mb) || 0;
    const pct  = ((mb / maxMb) * 100).toFixed(1);
    const col  = mb > 500 ? '#ef4444' : mb > 100 ? '#f59e0b' : '#4a9eff';
    return `<tr>
      <td style="color:var(--text)">${db.db_name}</td>
      <td>${fmtSize(mb)}</td>
      <td>${fmtNum(db.tables)}</td>
      <td>${fmtNum(db.rows_est)}</td>
      <td>
        <span class="mini-bar-wrap">
          <span class="mini-bar-fill" style="width:${pct}%;background:${col}"></span>
        </span>
        <span style="font-size:10px;margin-left:4px;color:var(--text-dim)">${pct}%</span>
      </td>
    </tr>`;
  }).join('');
}

function renderTopTables(tables) {
  const tbody = document.getElementById('tbl-body');
  if (!tables || !tables.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-row">Sin tablas</td></tr>';
    return;
  }

  tbody.innerHTML = tables.map(t => `<tr>
    <td>${t.db_name}</td>
    <td style="color:var(--text)">${t.table_name}</td>
    <td><span style="color:var(--accent-blue)">${t.engine || '?'}</span></td>
    <td>${fmtSize(t.size_mb)}</td>
    <td>${fmtNum(t.rows_est)}</td>
  </tr>`).join('');
}

function renderReplication(rep) {
  const section = document.getElementById('replication-section');
  if (!rep) { section.classList.add('hidden'); return; }
  section.classList.remove('hidden');

  const ioOk  = rep.Slave_IO_Running  === 'Yes';
  const sqlOk = rep.Slave_SQL_Running === 'Yes';
  const lag   = rep.Seconds_Behind_Master;

  document.getElementById('repl-io').innerHTML =
    `<span style="color:${ioOk?'var(--accent-green)':'var(--accent-red)'}">
      ${ioOk ? '✔ Running' : '✘ Stopped'}</span>`;

  document.getElementById('repl-sql').innerHTML =
    `<span style="color:${sqlOk?'var(--accent-green)':'var(--accent-red)'}">
      ${sqlOk ? '✔ Running' : '✘ Stopped'}</span>`;

  const lagNum = parseInt(lag) || 0;
  const lagColor = lagNum >= 30 ? 'var(--accent-red)' : lagNum >= 10 ? 'var(--accent-yellow)' : 'var(--accent-green)';
  document.getElementById('repl-lag').innerHTML =
    `<span style="color:${lagColor}">${lag !== null ? lagNum + 's' : 'N/A'}</span>`;

  document.getElementById('repl-host').textContent = rep.Master_Host || '?';
}

// ─────────────────────────────────────────────────────
// COUNTDOWN RING
// ─────────────────────────────────────────────────────
const CIRC = 94.2;
let countdownInterval = null;

function startCountdown() {
  countdown = REFRESH;
  if (countdownInterval) clearInterval(countdownInterval);
  countdownInterval = setInterval(() => {
    countdown--;
    const frac = countdown / REFRESH;
    const offset = CIRC * (1 - frac);
    const ring = document.getElementById('ring-progress');
    if (ring) ring.style.strokeDasharray = `${CIRC * frac} ${CIRC}`;
    const numEl = document.getElementById('countdown-num');
    if (numEl) numEl.textContent = Math.max(0, countdown);
    if (countdown <= 0) clearInterval(countdownInterval);
  }, 1000);
}

// ─────────────────────────────────────────────────────
// FETCH LOOP
// ─────────────────────────────────────────────────────
async function fetchMetrics() {
  const connDot = document.getElementById('conn-status');
  try {
    const res = await fetch('/api/metrics');
    if (res.status === 202) {
      connDot.className = 'conn-dot dot-loading';
      return;
    }
    if (!res.ok) {
      connDot.className = 'conn-dot dot-error';
      return;
    }
    const data = await res.json();
    if (data.error) {
      connDot.className = 'conn-dot dot-error';
      document.getElementById('hdr-version').textContent = data.error;
      return;
    }
    connDot.className = 'conn-dot dot-ok';
    renderMetrics(data);
  } catch (e) {
    connDot.className = 'conn-dot dot-error';
    console.error('Fetch error:', e);
  }
}

async function init() {
  // Cargar config de thresholds
  try {
    const cfg = await fetch('/api/config').then(r => r.json());
    REFRESH    = cfg.refresh_interval || 5;
    THRESHOLDS = cfg.thresholds || {};
  } catch (_) {}

  // Primera carga
  await fetchMetrics();
  startCountdown();

  // Polling
  setInterval(async () => {
    await fetchMetrics();
    startCountdown();
  }, REFRESH * 1000);
}

document.addEventListener('DOMContentLoaded', init);
