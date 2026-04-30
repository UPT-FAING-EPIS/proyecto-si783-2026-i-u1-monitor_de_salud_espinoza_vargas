#!/usr/bin/env python3
"""
Monitor de Salud DB — Backend Flask
Sirve el dashboard web y expone endpoints de métricas via REST/SSE.
Conecta a Azure SQL Database usando pyodbc con ODBC Driver 18.
"""

import configparser
import json
import os
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, Response, send_from_directory
from flask_cors import CORS

try:
    import pyodbc
except ImportError:
    raise SystemExit("Instala: pip install pyodbc flask flask-cors")

# ─────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.ini"

# Caché global de métricas (actualizado por hilo de fondo)
_cache: dict = {"metrics": None, "alerts": [], "error": None, "last_update": None}
_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE, encoding="utf-8")
    return cfg


# ─────────────────────────────────────────────────────────────
# CONEXIÓN (pyodbc → Azure SQL Database)
# ─────────────────────────────────────────────────────────────
def make_connection(cfg):
    """
    Construye la cadena de conexión ODBC para Azure SQL Database.
    Variables de entorno tienen prioridad sobre config.ini.
    """
    server   = os.environ.get("SQL_SERVER")   or cfg.get("sqlserver", "server",   fallback="localhost")
    user     = os.environ.get("SQL_USER")     or cfg.get("sqlserver", "user",     fallback="sa")
    password = os.environ.get("SQL_PASSWORD") or cfg.get("sqlserver", "password", fallback="")
    database = os.environ.get("SQL_DATABASE") or cfg.get("sqlserver", "database", fallback="db_health_monitor")
    port     = int(os.environ.get("SQL_PORT") or cfg.get("sqlserver", "port", fallback="1433"))

    # Driver en orden de preferencia (Linux = ODBC 18, fallback 17)
    drivers = [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "FreeTDS",
    ]
    driver = next((d for d in drivers if d in pyodbc.drivers()), drivers[0])

    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server},{port};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={password};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )
    conn = pyodbc.connect(conn_str, autocommit=True)
    return conn


# ─────────────────────────────────────────────────────────────
# RECOLECCIÓN DE MÉTRICAS (adaptado a SQL Server DMVs)
# ─────────────────────────────────────────────────────────────
def query_rows(cursor, sql: str) -> list[dict]:
    cursor.execute(sql)
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def collect(conn) -> dict:
    cur = conn.cursor()

    # ── Conexiones y sesiones activas ────────────────────────
    cur.execute("""
        SELECT
            COUNT(*)                                         AS sessions_total,
            SUM(CASE WHEN status = 'running'  THEN 1 ELSE 0 END) AS sessions_running,
            SUM(CASE WHEN status = 'sleeping' THEN 1 ELSE 0 END) AS sessions_sleeping
        FROM sys.dm_exec_sessions
        WHERE is_user_process = 1
    """)
    row = cur.fetchone()
    sessions_total   = row[0] or 0
    sessions_running = row[1] or 0
    sessions_sleeping= row[2] or 0

    # Límite configurado en SQL Server (max worker threads como proxy)
    cur.execute("SELECT value_in_use FROM sys.configurations WHERE name = 'max connections'")
    r = cur.fetchone()
    max_connections = int(r[0]) if r and r[0] else 32767

    # ── Estadísticas de rendimiento ──────────────────────────
    cur.execute("""
        SELECT
            cntr_value
        FROM sys.dm_os_performance_counters
        WHERE counter_name = 'Batch Requests/sec'
          AND object_name LIKE '%SQL Statistics%'
    """)
    r = cur.fetchone()
    batch_req_sec = float(r[0]) if r else 0.0

    cur.execute("""
        SELECT cntr_value
        FROM sys.dm_os_performance_counters
        WHERE counter_name = 'SQL Compilations/sec'
          AND object_name LIKE '%SQL Statistics%'
    """)
    r = cur.fetchone()
    compilations_sec = float(r[0]) if r else 0.0

    # ── Buffer Pool (equivalente InnoDB) ─────────────────────
    cur.execute("""
        SELECT
            SUM(pages_kb) * 1024                   AS buffer_pool_bytes,
            SUM(CASE WHEN is_modified = 1
                THEN pages_kb ELSE 0 END) * 1024   AS dirty_bytes,
            SUM(pages_kb)                           AS total_pages_kb,
            (SELECT physical_memory_in_use_kb
             FROM sys.dm_os_process_memory) * 1024 AS memory_in_use
        FROM sys.dm_os_buffer_descriptors
    """)
    r = cur.fetchone()
    bp_bytes   = int(r[0] or 0)
    dirty_bytes= int(r[1] or 0)
    bp_size_mb = round(bp_bytes / 1024 / 1024, 1)

    # ── Buffer hit ratio ─────────────────────────────────────
    cur.execute("""
        SELECT
            (a.cntr_value * 1.0 / NULLIF(b.cntr_value, 0)) * 100
        FROM sys.dm_os_performance_counters a
        JOIN sys.dm_os_performance_counters b
          ON b.counter_name = 'Buffer cache hit ratio base'
         AND b.object_name  = a.object_name
        WHERE a.counter_name = 'Buffer cache hit ratio'
          AND a.object_name LIKE '%Buffer Manager%'
    """)
    r = cur.fetchone()
    hit_ratio = round(float(r[0]), 2) if r and r[0] else 100.0

    # ── Uptime del servidor ──────────────────────────────────
    cur.execute("SELECT DATEDIFF(SECOND, sqlserver_start_time, GETDATE()) FROM sys.dm_os_sys_info")
    r = cur.fetchone()
    uptime = int(r[0]) if r else 0

    # ── Versión ──────────────────────────────────────────────
    cur.execute("SELECT @@VERSION")
    version_full = cur.fetchone()[0] or ""
    version = version_full.split("\n")[0].strip()

    cur.execute("SELECT @@SERVERNAME")
    r = cur.fetchone()
    hostname = r[0] if r else "unknown"

    # ── Procesos activos ─────────────────────────────────────
    cur.execute("""
        SELECT TOP 30
            s.session_id                        AS ID,
            s.login_name                        AS [USER],
            s.host_name                         AS HOST,
            s.database_id                       AS DB,
            r.command                           AS COMMAND,
            ISNULL(r.wait_time / 1000, 0)       AS TIME,
            ISNULL(r.wait_type, '')              AS STATE,
            ISNULL(LEFT(t.text, 100), '')        AS INFO
        FROM sys.dm_exec_sessions s
        LEFT JOIN sys.dm_exec_requests r ON r.session_id = s.session_id
        OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t
        WHERE s.is_user_process = 1
          AND r.command IS NOT NULL
        ORDER BY TIME DESC
    """)
    cols = [d[0] for d in cur.description]
    processes = [dict(zip(cols, row)) for row in cur.fetchall()]

    # ── Tamaño de tablas ─────────────────────────────────────
    cur.execute("""
        SELECT TOP 10
            OBJECT_SCHEMA_NAME(i.object_id)         AS db_name,
            OBJECT_NAME(i.object_id)                AS table_name,
            ROUND(SUM(a.total_pages) * 8.0 / 1024, 2) AS size_mb,
            SUM(p.rows)                             AS rows_est
        FROM sys.indexes i
        JOIN sys.partitions p     ON p.object_id = i.object_id AND p.index_id = i.index_id
        JOIN sys.allocation_units a ON a.container_id = p.partition_id
        WHERE OBJECT_SCHEMA_NAME(i.object_id) NOT IN ('sys','INFORMATION_SCHEMA')
          AND OBJECT_NAME(i.object_id) NOT LIKE 'sys%'
        GROUP BY i.object_id
        ORDER BY size_mb DESC
    """)
    cols = [d[0] for d in cur.description]
    top_tables = [dict(zip(cols, row)) for row in cur.fetchall()]

    # ── DB sizes ─────────────────────────────────────────────
    cur.execute("""
        SELECT
            DB_NAME()                                AS db_name,
            ROUND(SUM(a.total_pages) * 8.0 / 1024 / 1024, 2) AS size_mb,
            COUNT(DISTINCT i.object_id)              AS tables,
            SUM(p.rows)                              AS rows_est
        FROM sys.indexes i
        JOIN sys.partitions p     ON p.object_id = i.object_id AND p.index_id = i.index_id
        JOIN sys.allocation_units a ON a.container_id = p.partition_id
        WHERE OBJECT_SCHEMA_NAME(i.object_id) NOT IN ('sys','INFORMATION_SCHEMA')
    """)
    cols = [d[0] for d in cur.description]
    db_sizes = [dict(zip(cols, row)) for row in cur.fetchall()]

    cur.close()

    # ── Métricas derivadas ───────────────────────────────────
    conn_pct = round((sessions_total / max_connections) * 100, 2) if max_connections else 0

    td = timedelta(seconds=uptime)
    d, h, m, s = td.days, td.seconds // 3600, (td.seconds % 3600) // 60, td.seconds % 60
    uptime_str = f"{d}d {h:02}h {m:02}m {s:02}s"

    # Normalizar tipos
    for p in processes:
        p["TIME"] = int(p.get("TIME") or 0)
        for k, v in p.items():
            if v is None:
                p[k] = ""

    for db in db_sizes:
        db["size_mb"]  = float(db.get("size_mb") or 0)
        db["tables"]   = int(db.get("tables") or 0)
        db["rows_est"] = int(db.get("rows_est") or 0)

    for t in top_tables:
        t["size_mb"]  = float(t.get("size_mb") or 0)
        t["rows_est"] = int(t.get("rows_est") or 0)
        t["engine"]   = "SQL Server"

    return {
        "timestamp": datetime.now().isoformat(),
        "version": version,
        "hostname": hostname,
        "uptime_seconds": uptime,
        "uptime_str": uptime_str,
        # Conexiones
        "max_connections":   max_connections,
        "threads_connected": sessions_total,
        "threads_running":   sessions_running,
        "threads_cached":    sessions_sleeping,
        "threads_created":   sessions_total,
        "connection_pct":    conn_pct,
        # Rendimiento
        "questions":    int(batch_req_sec),
        "qps":          round(batch_req_sec, 2),
        "slow_queries": 0,
        "com_select":   int(compilations_sec),
        "com_insert":   0,
        "com_update":   0,
        "com_delete":   0,
        # Buffer Pool
        "innodb_hit_ratio": hit_ratio,
        "bp_size_mb":       bp_size_mb,
        "bp_used_pct":      round((bp_bytes - dirty_bytes) / bp_bytes * 100, 1) if bp_bytes else 0,
        "bp_pages_total":   bp_bytes // 8192,
        "bp_pages_free":    0,
        "bp_pages_dirty":   dirty_bytes // 8192,
        # Listas
        "processes":   processes,
        "db_sizes":    db_sizes,
        "top_tables":  top_tables,
        "replication": None,
    }


def evaluate_alerts(metrics: dict, cfg) -> list[dict]:
    def thr(k, d): return cfg.getfloat("thresholds", k, fallback=d)
    alerts = []
    checks = [
        ("connection_pct",   metrics["connection_pct"],   thr("connections_warning", 70),  thr("connections_critical", 90),  "Uso de Conexiones",      "%",  False),
        ("innodb_hit_ratio", metrics["innodb_hit_ratio"], thr("cache_hit_warning", 95),     thr("cache_hit_critical", 85),    "Buffer Cache Hit Ratio", "%",  True),
        ("threads_running",  metrics["threads_running"],  thr("threads_running_warning", 20), thr("threads_running_critical", 50), "Sesiones Activas",  "",   False),
        ("slow_queries",     metrics["slow_queries"],     thr("slow_queries_warning", 100), thr("slow_queries_critical", 500), "Consultas Lentas",      "",   False),
    ]
    for key, value, warn, crit, label, unit, invert in checks:
        if invert:
            sev = "CRITICAL" if value < crit else "WARNING" if value < warn else None
        else:
            sev = "CRITICAL" if value >= crit else "WARNING" if value >= warn else None
        if sev:
            alerts.append({"severity": sev, "metric": label,
                           "value": f"{value}{unit}",
                           "threshold": f"WARN={warn}{unit}  CRIT={crit}{unit}"})
    return alerts


# ─────────────────────────────────────────────────────────────
# HILO DE FONDO
# ─────────────────────────────────────────────────────────────
def background_collector():
    cfg = load_config()
    refresh = cfg.getint("monitor", "refresh_interval", fallback=5)
    conn = None

    while True:
        try:
            if conn is None:
                conn = make_connection(cfg)

            metrics = collect(conn)
            alerts  = evaluate_alerts(metrics, cfg)

            with _lock:
                _cache["metrics"]     = metrics
                _cache["alerts"]      = alerts
                _cache["error"]       = None
                _cache["last_update"] = datetime.now().isoformat()

        except pyodbc.Error as e:
            with _lock:
                _cache["error"] = f"SQL Server error: {e}"
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
            conn = None
        except Exception as e:
            with _lock:
                _cache["error"] = f"Error: {e}"

        time.sleep(refresh)


# ─────────────────────────────────────────────────────────────
# RUTAS
# ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/metrics")
def api_metrics():
    with _lock:
        if _cache["error"]:
            return jsonify({"error": _cache["error"]}), 503
        if _cache["metrics"] is None:
            return jsonify({"status": "loading"}), 202
        return jsonify({
            "metrics":     _cache["metrics"],
            "alerts":      _cache["alerts"],
            "last_update": _cache["last_update"],
        })


@app.route("/api/config")
def api_config():
    cfg = load_config()
    return jsonify({
        "refresh_interval": cfg.getint("monitor", "refresh_interval", fallback=5),
        "thresholds": {
            "connections_warning":      cfg.getfloat("thresholds", "connections_warning",      fallback=70),
            "connections_critical":     cfg.getfloat("thresholds", "connections_critical",     fallback=90),
            "cache_hit_warning":        cfg.getfloat("thresholds", "cache_hit_warning",        fallback=95),
            "cache_hit_critical":       cfg.getfloat("thresholds", "cache_hit_critical",       fallback=85),
            "threads_running_warning":  cfg.getfloat("thresholds", "threads_running_warning",  fallback=20),
            "threads_running_critical": cfg.getfloat("thresholds", "threads_running_critical", fallback=50),
            "slow_queries_warning":     cfg.getfloat("thresholds", "slow_queries_warning",     fallback=100),
            "slow_queries_critical":    cfg.getfloat("thresholds", "slow_queries_critical",    fallback=500),
        }
    })


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    t = threading.Thread(target=background_collector, daemon=True)
    t.start()
    print("\n  [OK] Monitor DB Web iniciado (Azure SQL Database)")
    print("  >>  Abre tu navegador en:  http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
