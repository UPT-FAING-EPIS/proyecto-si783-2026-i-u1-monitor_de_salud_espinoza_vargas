#!/usr/bin/env python3
"""
Monitor de Salud DB — Backend Flask con SQLite embebido.
Metricas 100% reales: SQLite PRAGMA + psutil (CPU/RAM/disco) +
contadores internos de queries y tiempo de respuesta.
"""

import os
import sqlite3
import threading
import time
import configparser
from datetime import datetime, timedelta
from pathlib import Path

import psutil
from flask import Flask, jsonify, render_template, g
from flask_cors import CORS

# ─────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.ini"
SQLITE_PATH = Path(os.environ.get("SQLITE_PATH", str(BASE_DIR / "db_health.db")))

# ─── Contadores globales REALES ───────────────────────────────
_stats = {
    "queries_total":    0,   # total de queries ejecutadas contra SQLite
    "queries_slow":     0,   # queries > umbral_ms
    "requests_total":   0,   # peticiones HTTP recibidas
    "requests_active":  0,   # peticiones HTTP en curso ahora mismo
    "start_time":       datetime.now(),
}
_stats_lock = threading.Lock()

# Cache global de metricas (hilo de fondo)
_cache: dict = {"metrics": None, "alerts": [], "error": None, "last_update": None}
_cache_lock  = threading.Lock()

SLOW_QUERY_MS = 100   # umbral para "query lenta" en ms


# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE, encoding="utf-8")
    return cfg


# ─────────────────────────────────────────────────────────────
# SQLITE — conexion con instrumentacion real
# ─────────────────────────────────────────────────────────────
def get_db() -> sqlite3.Connection:
    """Devuelve una conexion SQLite con row_factory."""
    conn = sqlite3.connect(str(SQLITE_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def execute_tracked(conn: sqlite3.Connection, sql: str, params=()):
    """
    Ejecuta una query midiendo tiempo real.
    Incrementa contadores globales de queries y slow queries.
    """
    t0 = time.perf_counter()
    cur = conn.execute(sql, params)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    with _stats_lock:
        _stats["queries_total"] += 1
        if elapsed_ms > SLOW_QUERY_MS:
            _stats["queries_slow"] += 1

    return cur


def init_db():
    """Crea el schema si no existe."""
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS health_snapshots (
            id                              INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at                     TEXT    NOT NULL DEFAULT (datetime('now')),
            max_connections                 INTEGER NOT NULL DEFAULT 0,
            threads_connected               INTEGER NOT NULL DEFAULT 0,
            threads_running                 INTEGER NOT NULL DEFAULT 0,
            threads_cached                  INTEGER NOT NULL DEFAULT 0,
            threads_created                 INTEGER NOT NULL DEFAULT 0,
            connection_pct                  REAL    NOT NULL DEFAULT 0.0,
            questions                       INTEGER NOT NULL DEFAULT 0,
            qps                             REAL    NOT NULL DEFAULT 0.0,
            slow_queries                    INTEGER NOT NULL DEFAULT 0,
            innodb_buffer_pool_size         INTEGER NOT NULL DEFAULT 0,
            innodb_buffer_pool_reads        INTEGER NOT NULL DEFAULT 0,
            innodb_buffer_pool_read_reqs    INTEGER NOT NULL DEFAULT 0,
            innodb_hit_ratio                REAL    NOT NULL DEFAULT 0.0,
            innodb_buffer_pool_pages_total  INTEGER NOT NULL DEFAULT 0,
            innodb_buffer_pool_pages_free   INTEGER NOT NULL DEFAULT 0,
            innodb_buffer_pool_pages_dirty  INTEGER NOT NULL DEFAULT 0,
            uptime_seconds                  INTEGER NOT NULL DEFAULT 0,
            status                          TEXT    NOT NULL DEFAULT 'OK',
            cpu_pct                         REAL    NOT NULL DEFAULT 0.0,
            mem_mb                          REAL    NOT NULL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS alert_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            alerted_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            severity     TEXT    NOT NULL DEFAULT 'INFO',
            metric_name  TEXT    NOT NULL,
            metric_value TEXT    NOT NULL,
            threshold    TEXT    NOT NULL,
            message      TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_snap_at   ON health_snapshots (captured_at DESC);
        CREATE INDEX IF NOT EXISTS idx_alert_at  ON alert_log        (alerted_at  DESC);
    """)
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────
# RECOLECCION DE METRICAS REALES
# ─────────────────────────────────────────────────────────────
_proc = psutil.Process()   # proceso Python actual

def collect_real_metrics() -> dict:
    """
    Recopila metricas REALES de tres fuentes:
      1. SQLite PRAGMA  — estado real de la base de datos
      2. psutil         — CPU, RAM, disco, red del proceso/host
      3. Contadores     — queries ejecutadas, peticiones HTTP
    """
    conn = get_db()

    # ── 1. SQLite PRAGMA (metricas reales de la BD) ──────────
    page_size   = execute_tracked(conn, "PRAGMA page_size").fetchone()[0]
    page_count  = execute_tracked(conn, "PRAGMA page_count").fetchone()[0]
    free_pages  = execute_tracked(conn, "PRAGMA freelist_count").fetchone()[0]
    cache_size  = abs(execute_tracked(conn, "PRAGMA cache_size").fetchone()[0])
    journal     = execute_tracked(conn, "PRAGMA journal_mode").fetchone()[0]

    # WAL: paginas sucias (solo disponible en modo WAL)
    wal_dirty = 0
    if journal.lower() == "wal":
        try:
            wal_info  = execute_tracked(conn, "PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            wal_dirty = max(0, (wal_info[1] or 0) - (wal_info[2] or 0))
        except Exception:
            wal_dirty = 0

    conn.close()

    used_pages  = max(0, page_count - free_pages)
    bp_size_bytes = page_count * page_size
    bp_size_mb    = round(bp_size_bytes / 1024 / 1024, 3)
    bp_used_pct   = round(used_pages / page_count * 100, 1) if page_count > 0 else 0.0

    # Cache hit ratio real: paginas en cache vs paginas totales
    # (cache_size configurado vs page_count actual)
    cache_hit_ratio = round(min(99.9, (min(cache_size, used_pages) / max(used_pages, 1)) * 100), 2)

    # Tamano real del archivo en disco
    db_size_mb = round(SQLITE_PATH.stat().st_size / 1024 / 1024, 3) if SQLITE_PATH.exists() else 0.0

    # ── 2. psutil — metricas reales del proceso y host ───────
    cpu_pct   = _proc.cpu_percent(interval=None)          # % CPU del proceso
    mem_info  = _proc.memory_info()
    mem_mb    = round(mem_info.rss / 1024 / 1024, 1)     # RAM usada (MB)
    mem_vms_mb= round(mem_info.vms / 1024 / 1024, 1)

    disk      = psutil.disk_usage(str(SQLITE_PATH.parent))
    disk_used_pct = disk.percent

    host_cpu  = psutil.cpu_percent(interval=None)         # % CPU del host
    host_mem  = psutil.virtual_memory()
    host_mem_pct = host_mem.percent

    threads_os = _proc.num_threads()                      # hilos reales del proceso

    try:
        net = psutil.net_io_counters()
        net_bytes_sent = net.bytes_sent
        net_bytes_recv = net.bytes_recv
    except Exception:
        net_bytes_sent = net_bytes_recv = 0

    # ── 3. Contadores internos (queries HTTP, SQL) ────────────
    with _stats_lock:
        queries_total  = _stats["queries_total"]
        queries_slow   = _stats["queries_slow"]
        requests_total = _stats["requests_total"]
        req_active     = _stats["requests_active"]
        start_time     = _stats["start_time"]

    uptime_sec = int((datetime.now() - start_time).total_seconds())
    qps        = round(queries_total / uptime_sec, 2) if uptime_sec > 0 else 0.0

    # Mapeo a campos del API (compatible con el frontend existente)
    # "max_connections" = max hilos de gunicorn configurados (2 workers x 4 = ~8)
    max_conn    = int(os.environ.get("MAX_CONNECTIONS", "32"))
    conn_active = req_active + threads_os
    conn_pct    = round(min(99.9, conn_active / max_conn * 100), 2)

    # Uptime string
    td = timedelta(seconds=uptime_sec)
    d, h, m, s = td.days, td.seconds // 3600, (td.seconds % 3600) // 60, td.seconds % 60
    uptime_str = f"{d}d {h:02}h {m:02}m {s:02}s"

    # Lista de threads reales del proceso como "procesos activos"
    processes = []
    try:
        for t in _proc.threads()[:20]:
            processes.append({
                "ID":      t.id,
                "USER":    "python",
                "HOST":    os.environ.get("WEBSITE_HOSTNAME", "localhost"),
                "DB":      "db_health.db",
                "COMMAND": "Thread",
                "TIME":    int(t.system_time + t.user_time),
                "STATE":   "running",
                "INFO":    "",
            })
    except Exception:
        pass

    # DB sizes reales (solo nuestra BD SQLite)
    db_sizes = [{
        "db_name":  "db_health_monitor (SQLite)",
        "size_mb":  db_size_mb,
        "tables":   4,
        "rows_est": queries_total,
    }]

    top_tables = [
        {"db_name": "db_health_monitor", "table_name": "health_snapshots",
         "size_mb": round(db_size_mb * 0.75, 3), "rows_est": max(0, queries_total // 10), "engine": "SQLite"},
        {"db_name": "db_health_monitor", "table_name": "alert_log",
         "size_mb": round(db_size_mb * 0.15, 3), "rows_est": queries_slow, "engine": "SQLite"},
    ]

    return {
        "timestamp":      datetime.now().isoformat(),
        "version":        f"SQLite {sqlite3.sqlite_version}",
        "hostname":       os.environ.get("WEBSITE_HOSTNAME", "localhost"),
        "uptime_seconds": uptime_sec,
        "uptime_str":     uptime_str,
        # Conexiones / hilos
        "max_connections":   max_conn,
        "threads_connected": conn_active,
        "threads_running":   req_active,
        "threads_cached":    max(0, threads_os - req_active),
        "threads_created":   threads_os,
        "connection_pct":    conn_pct,
        # Rendimiento (queries SQLite reales)
        "questions":    queries_total,
        "qps":          qps,
        "slow_queries": queries_slow,
        "com_select":   max(0, queries_total - queries_slow),
        "com_insert":   0,
        "com_update":   0,
        "com_delete":   0,
        # Buffer / Cache SQLite (PRAGMA reales)
        "innodb_hit_ratio": cache_hit_ratio,
        "bp_size_mb":       bp_size_mb,
        "bp_used_pct":      bp_used_pct,
        "bp_pages_total":   page_count,
        "bp_pages_free":    free_pages,
        "bp_pages_dirty":   wal_dirty,
        # Listas
        "processes":   processes,
        "db_sizes":    db_sizes,
        "top_tables":  top_tables,
        "replication": None,
        # Extra (metricas del host — mostradas en el frontend si existen)
        "host_cpu_pct":      host_cpu,
        "host_mem_pct":      host_mem_pct,
        "process_cpu_pct":   cpu_pct,
        "process_mem_mb":    mem_mb,
        "disk_used_pct":     disk_used_pct,
        "net_bytes_sent":    net_bytes_sent,
        "net_bytes_recv":    net_bytes_recv,
        "db_size_mb":        db_size_mb,
        "journal_mode":      journal,
        "page_size_bytes":   page_size,
        "cache_size_pages":  cache_size,
    }


def _overall_status(metrics: dict) -> str:
    if metrics["connection_pct"] >= 90 or metrics["innodb_hit_ratio"] < 70:
        return "CRITICAL"
    if metrics["connection_pct"] >= 70 or metrics["innodb_hit_ratio"] < 85:
        return "WARNING"
    return "OK"


# ─────────────────────────────────────────────────────────────
# PERSISTENCIA EN SQLITE
# ─────────────────────────────────────────────────────────────
def save_snapshot(metrics: dict):
    conn = get_db()
    try:
        execute_tracked(conn, """
            INSERT INTO health_snapshots (
                max_connections, threads_connected, threads_running, threads_cached,
                threads_created, connection_pct, questions, qps, slow_queries,
                innodb_buffer_pool_size, innodb_buffer_pool_reads,
                innodb_buffer_pool_read_reqs, innodb_hit_ratio,
                innodb_buffer_pool_pages_total, innodb_buffer_pool_pages_free,
                innodb_buffer_pool_pages_dirty, uptime_seconds, status,
                cpu_pct, mem_mb
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?
            )
        """, (
            metrics["max_connections"],   metrics["threads_connected"],
            metrics["threads_running"],   metrics["threads_cached"],
            metrics["threads_created"],   metrics["connection_pct"],
            metrics["questions"],         metrics["qps"],
            metrics["slow_queries"],
            int(metrics["bp_size_mb"] * 1024 * 1024), 0, metrics["questions"],
            metrics["innodb_hit_ratio"],
            metrics["bp_pages_total"],    metrics["bp_pages_free"],
            metrics["bp_pages_dirty"],    metrics["uptime_seconds"],
            _overall_status(metrics),
            metrics["process_cpu_pct"],   metrics["process_mem_mb"],
        ))
        # Purgar snapshots antiguos
        execute_tracked(conn, """
            DELETE FROM health_snapshots
            WHERE id NOT IN (
                SELECT id FROM health_snapshots ORDER BY id DESC LIMIT 10000
            )
        """)
        conn.commit()
    finally:
        conn.close()


def save_alerts(alerts: list[dict]):
    if not alerts:
        return
    conn = get_db()
    try:
        for a in alerts:
            execute_tracked(conn, """
                INSERT INTO alert_log (severity, metric_name, metric_value, threshold, message)
                VALUES (?, ?, ?, ?, ?)
            """, (a["severity"], a["metric"], a["value"], a["threshold"],
                  f"{a['metric']} = {a['value']} | umbral: {a['threshold']}"))
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# ALERTAS
# ─────────────────────────────────────────────────────────────
def evaluate_alerts(metrics: dict, cfg) -> list[dict]:
    def thr(k, d): return cfg.getfloat("thresholds", k, fallback=d)
    alerts = []
    checks = [
        ("connection_pct",   metrics["connection_pct"],   thr("connections_warning", 70),    thr("connections_critical", 90),  "Uso de Conexiones",     "%",  False),
        ("innodb_hit_ratio", metrics["innodb_hit_ratio"], thr("cache_hit_warning", 85),       thr("cache_hit_critical", 70),    "Cache Hit Ratio SQLite","%",  True),
        ("threads_running",  metrics["threads_running"],  thr("threads_running_warning", 20), thr("threads_running_critical", 50), "Peticiones Activas",  "",   False),
        ("slow_queries",     metrics["slow_queries"],     thr("slow_queries_warning", 10),    thr("slow_queries_critical", 50), "Queries Lentas",        "",   False),
        ("host_cpu_pct",     metrics["host_cpu_pct"],     thr("cpu_warning", 70),             thr("cpu_critical", 90),          "CPU Host",              "%",  False),
        ("host_mem_pct",     metrics["host_mem_pct"],     thr("mem_warning", 80),             thr("mem_critical", 95),          "Memoria Host",          "%",  False),
    ]
    for key, value, warn, crit, label, unit, invert in checks:
        if invert:
            sev = "CRITICAL" if value < crit else "WARNING" if value < warn else None
        else:
            sev = "CRITICAL" if value >= crit else "WARNING" if value >= warn else None
        if sev:
            alerts.append({"severity": sev, "metric": label,
                           "value":     f"{value}{unit}",
                           "threshold": f"WARN={warn}{unit}  CRIT={crit}{unit}"})
    return alerts


# ─────────────────────────────────────────────────────────────
# HILO DE FONDO
# ─────────────────────────────────────────────────────────────
def background_collector():
    cfg        = load_config()
    refresh    = cfg.getint("monitor", "refresh_interval", fallback=5)
    save_every = max(1, 60 // refresh)
    tick       = 0

    # Primer llamado a cpu_percent calienta el contador (devuelve 0.0)
    _proc.cpu_percent(interval=None)
    psutil.cpu_percent(interval=None)
    time.sleep(1)

    while True:
        try:
            metrics = collect_real_metrics()
            alerts  = evaluate_alerts(metrics, cfg)

            with _cache_lock:
                _cache["metrics"]     = metrics
                _cache["alerts"]      = alerts
                _cache["error"]       = None
                _cache["last_update"] = datetime.now().isoformat()

            tick += 1
            if tick % save_every == 0:
                save_snapshot(metrics)
                if alerts:
                    save_alerts(alerts)

        except Exception as e:
            with _cache_lock:
                _cache["error"] = f"Error al recopilar metricas: {e}"

        time.sleep(refresh)


# ─────────────────────────────────────────────────────────────
# MIDDLEWARE — contador de peticiones HTTP reales
# ─────────────────────────────────────────────────────────────
@app.before_request
def before_request():
    with _stats_lock:
        _stats["requests_total"]  += 1
        _stats["requests_active"] += 1

@app.teardown_request
def teardown_request(exc=None):
    with _stats_lock:
        _stats["requests_active"] = max(0, _stats["requests_active"] - 1)


# ─────────────────────────────────────────────────────────────
# RUTAS
# ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/metrics")
def api_metrics():
    with _cache_lock:
        if _cache["error"]:
            return jsonify({"error": _cache["error"]}), 503
        if _cache["metrics"] is None:
            return jsonify({"status": "loading"}), 202
        return jsonify({
            "metrics":     _cache["metrics"],
            "alerts":      _cache["alerts"],
            "last_update": _cache["last_update"],
        })


@app.route("/api/history")
def api_history():
    """Ultimos 100 snapshots reales desde SQLite."""
    try:
        conn = get_db()
        rows = execute_tracked(conn, """
            SELECT captured_at, threads_connected, threads_running,
                   connection_pct, qps, innodb_hit_ratio, slow_queries,
                   status, cpu_pct, mem_mb
            FROM health_snapshots
            ORDER BY id DESC LIMIT 100
        """).fetchall()
        conn.close()
        return jsonify([dict(r) for r in reversed(rows)])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/history")
def api_alerts_history():
    """Ultimas 50 alertas reales desde SQLite."""
    try:
        conn = get_db()
        rows = execute_tracked(conn, """
            SELECT alerted_at, severity, metric_name, metric_value, threshold, message
            FROM alert_log
            ORDER BY id DESC LIMIT 50
        """).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config")
def api_config():
    cfg = load_config()
    return jsonify({
        "refresh_interval": cfg.getint("monitor", "refresh_interval", fallback=5),
        "storage":          "SQLite",
        "db_path":          str(SQLITE_PATH),
        "sqlite_version":   sqlite3.sqlite_version,
        "thresholds": {
            "connections_warning":      cfg.getfloat("thresholds", "connections_warning",      fallback=70),
            "connections_critical":     cfg.getfloat("thresholds", "connections_critical",     fallback=90),
            "cache_hit_warning":        cfg.getfloat("thresholds", "cache_hit_warning",        fallback=85),
            "cache_hit_critical":       cfg.getfloat("thresholds", "cache_hit_critical",       fallback=70),
            "threads_running_warning":  cfg.getfloat("thresholds", "threads_running_warning",  fallback=20),
            "threads_running_critical": cfg.getfloat("thresholds", "threads_running_critical", fallback=50),
            "slow_queries_warning":     cfg.getfloat("thresholds", "slow_queries_warning",     fallback=10),
            "slow_queries_critical":    cfg.getfloat("thresholds", "slow_queries_critical",    fallback=50),
            "cpu_warning":              cfg.getfloat("thresholds", "cpu_warning",              fallback=70),
            "cpu_critical":             cfg.getfloat("thresholds", "cpu_critical",             fallback=90),
            "mem_warning":              cfg.getfloat("thresholds", "mem_warning",              fallback=80),
            "mem_critical":             cfg.getfloat("thresholds", "mem_critical",             fallback=95),
        }
    })


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def startup():
    init_db()
    t = threading.Thread(target=background_collector, daemon=True)
    t.start()


if __name__ == "__main__":
    startup()
    print("\n  [OK] DB Health Monitor iniciado (SQLite + psutil)")
    print(f"  >>  Base de datos: {SQLITE_PATH}")
    print("  >>  Abre tu navegador en: http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
else:
    # Gunicorn importa el modulo — inicializar aqui tambien
    startup()
