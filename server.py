#!/usr/bin/env python3
"""
Monitor de Salud MySQL — Backend Flask
Sirve el dashboard web y expone endpoints de métricas via REST/SSE.
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
    import mysql.connector
    from mysql.connector import Error as MySQLError
except ImportError:
    raise SystemExit("Instala: pip install mysql-connector-python flask flask-cors")

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
# CONEXIÓN
# ─────────────────────────────────────────────────────────────
def make_connection(cfg):
    # Variables de entorno tienen prioridad (Azure App Settings)
    return mysql.connector.connect(
        host=os.environ.get("MYSQL_HOST") or cfg.get("mysql", "host", fallback="localhost"),
        port=int(os.environ.get("MYSQL_PORT") or cfg.getint("mysql", "port", fallback=3306)),
        user=os.environ.get("MYSQL_USER") or cfg.get("mysql", "user", fallback="root"),
        password=os.environ.get("MYSQL_PASSWORD") or cfg.get("mysql", "password", fallback=""),
        database=os.environ.get("MYSQL_DATABASE") or cfg.get("mysql", "database", fallback="db_health_monitor"),
        connection_timeout=cfg.getint("mysql", "connect_timeout", fallback=10),
        autocommit=True,
    )


# ─────────────────────────────────────────────────────────────
# RECOLECCIÓN DE MÉTRICAS
# ─────────────────────────────────────────────────────────────
def query_dict(cursor, sql, params=None) -> list[dict]:
    cursor.execute(sql, params or ())
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def collect(conn) -> dict:
    cur = conn.cursor()

    # GLOBAL STATUS & VARIABLES
    cur.execute("SHOW GLOBAL STATUS")
    status = {r[0]: r[1] for r in cur.fetchall()}

    cur.execute("SHOW GLOBAL VARIABLES")
    variables = {r[0]: r[1] for r in cur.fetchall()}

    # PROCESSLIST
    cur.execute("""
        SELECT ID, USER, HOST, DB, COMMAND, TIME, STATE,
               LEFT(IFNULL(INFO,''), 100) AS INFO
        FROM information_schema.PROCESSLIST
        WHERE COMMAND != 'Sleep'
        ORDER BY TIME DESC LIMIT 30
    """)
    cols = [d[0] for d in cur.description]
    processes = [dict(zip(cols, r)) for r in cur.fetchall()]

    # DB SIZES
    cur.execute("""
        SELECT table_schema AS db_name,
               ROUND(SUM(data_length + index_length)/1024/1024, 2) AS size_mb,
               COUNT(*) AS tables,
               IFNULL(SUM(table_rows), 0) AS rows_est
        FROM information_schema.TABLES
        WHERE table_schema NOT IN
              ('information_schema','performance_schema','mysql','sys')
        GROUP BY table_schema ORDER BY size_mb DESC LIMIT 12
    """)
    cols = [d[0] for d in cur.description]
    db_sizes = [dict(zip(cols, r)) for r in cur.fetchall()]

    # TOP TABLES
    cur.execute("""
        SELECT table_schema AS db_name, table_name,
               ROUND((data_length + index_length)/1024/1024, 2) AS size_mb,
               IFNULL(table_rows, 0) AS rows_est,
               engine
        FROM information_schema.TABLES
        WHERE table_schema NOT IN
              ('information_schema','performance_schema','mysql','sys')
        ORDER BY (data_length + index_length) DESC LIMIT 10
    """)
    cols = [d[0] for d in cur.description]
    top_tables = [dict(zip(cols, r)) for r in cur.fetchall()]

    # REPLICATION
    replication = None
    try:
        cur.execute("SHOW SLAVE STATUS")
        row = cur.fetchone()
        if row:
            cols = [d[0] for d in cur.description]
            replication = dict(zip(cols, row))
    except Exception:
        pass

    cur.close()

    # ── Derived metrics ──────────────────────────────────────
    max_conn   = int(variables.get("max_connections", 1))
    th_conn    = int(status.get("Threads_connected", 0))
    th_run     = int(status.get("Threads_running", 0))
    th_cached  = int(status.get("Threads_cached", 0))
    th_created = int(status.get("Threads_created", 0))
    uptime     = int(status.get("Uptime", 1))
    questions  = int(status.get("Questions", 0))
    slow_q     = int(status.get("Slow_queries", 0))

    conn_pct = round((th_conn / max_conn) * 100, 2) if max_conn else 0
    qps      = round(questions / uptime, 2) if uptime else 0

    bp_reads     = int(status.get("Innodb_buffer_pool_reads", 0))
    bp_read_reqs = int(status.get("Innodb_buffer_pool_read_requests", 1))
    hit_ratio    = round((1 - bp_reads / bp_read_reqs) * 100, 2) if bp_read_reqs else 100.0
    bp_size_mb   = round(int(variables.get("innodb_buffer_pool_size", 0)) / 1024 / 1024, 1)

    bp_total = int(status.get("Innodb_buffer_pool_pages_total", 0))
    bp_free  = int(status.get("Innodb_buffer_pool_pages_free", 0))
    bp_dirty = int(status.get("Innodb_buffer_pool_pages_dirty", 0))
    bp_used_pct = round(((bp_total - bp_free) / bp_total) * 100, 1) if bp_total else 0

    td = timedelta(seconds=uptime)
    d, h, m, s = td.days, td.seconds // 3600, (td.seconds % 3600) // 60, td.seconds % 60
    uptime_str = f"{d}d {h:02}h {m:02}m {s:02}s"

    # Serializa los procesos (TIME puede ser int o str)
    for p in processes:
        p["TIME"] = int(p.get("TIME") or 0)
        for k, v in p.items():
            if v is None:
                p[k] = ""

    # Convierte floats/decimals en db_sizes
    for db in db_sizes:
        db["size_mb"] = float(db.get("size_mb") or 0)
        db["tables"]  = int(db.get("tables") or 0)
        db["rows_est"] = int(db.get("rows_est") or 0)

    for t in top_tables:
        t["size_mb"]  = float(t.get("size_mb") or 0)
        t["rows_est"] = int(t.get("rows_est") or 0)

    return {
        "timestamp": datetime.now().isoformat(),
        "version": variables.get("version", "?"),
        "hostname": variables.get("hostname", "?"),
        "uptime_seconds": uptime,
        "uptime_str": uptime_str,
        # Conexiones
        "max_connections": max_conn,
        "threads_connected": th_conn,
        "threads_running": th_run,
        "threads_cached": th_cached,
        "threads_created": th_created,
        "connection_pct": conn_pct,
        # Rendimiento
        "questions": questions,
        "qps": qps,
        "slow_queries": slow_q,
        "com_select": int(status.get("Com_select", 0)),
        "com_insert": int(status.get("Com_insert", 0)),
        "com_update": int(status.get("Com_update", 0)),
        "com_delete": int(status.get("Com_delete", 0)),
        # InnoDB
        "innodb_hit_ratio": hit_ratio,
        "bp_size_mb": bp_size_mb,
        "bp_used_pct": bp_used_pct,
        "bp_pages_total": bp_total,
        "bp_pages_free": bp_free,
        "bp_pages_dirty": bp_dirty,
        # Listas
        "processes": processes,
        "db_sizes": db_sizes,
        "top_tables": top_tables,
        "replication": replication,
    }


def evaluate_alerts(metrics: dict, cfg) -> list[dict]:
    def thr(k, d): return cfg.getfloat("thresholds", k, fallback=d)
    alerts = []
    checks = [
        ("connection_pct",   metrics["connection_pct"],   thr("connections_warning", 70),  thr("connections_critical", 90),  "Uso de Conexiones",       "%",  False),
        ("innodb_hit_ratio", metrics["innodb_hit_ratio"], thr("cache_hit_warning", 95),     thr("cache_hit_critical", 85),    "InnoDB Cache Hit Ratio",  "%",  True),
        ("threads_running",  metrics["threads_running"],  thr("threads_running_warning", 20), thr("threads_running_critical", 50), "Threads Ejecutando", "",   False),
        ("slow_queries",     metrics["slow_queries"],     thr("slow_queries_warning", 100), thr("slow_queries_critical", 500),"Consultas Lentas",        "",   False),
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
            if conn is None or not conn.is_connected():
                conn = make_connection(cfg)

            metrics = collect(conn)
            alerts  = evaluate_alerts(metrics, cfg)

            with _lock:
                _cache["metrics"] = metrics
                _cache["alerts"]  = alerts
                _cache["error"]   = None
                _cache["last_update"] = datetime.now().isoformat()

        except MySQLError as e:
            with _lock:
                _cache["error"] = f"MySQL error: {e}"
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
            "metrics": _cache["metrics"],
            "alerts":  _cache["alerts"],
            "last_update": _cache["last_update"],
        })


@app.route("/api/config")
def api_config():
    cfg = load_config()
    return jsonify({
        "refresh_interval": cfg.getint("monitor", "refresh_interval", fallback=5),
        "thresholds": {
            "connections_warning":      cfg.getfloat("thresholds", "connections_warning", fallback=70),
            "connections_critical":     cfg.getfloat("thresholds", "connections_critical", fallback=90),
            "cache_hit_warning":        cfg.getfloat("thresholds", "cache_hit_warning", fallback=95),
            "cache_hit_critical":       cfg.getfloat("thresholds", "cache_hit_critical", fallback=85),
            "threads_running_warning":  cfg.getfloat("thresholds", "threads_running_warning", fallback=20),
            "threads_running_critical": cfg.getfloat("thresholds", "threads_running_critical", fallback=50),
            "slow_queries_warning":     cfg.getfloat("thresholds", "slow_queries_warning", fallback=100),
            "slow_queries_critical":    cfg.getfloat("thresholds", "slow_queries_critical", fallback=500),
        }
    })


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    t = threading.Thread(target=background_collector, daemon=True)
    t.start()
    print("\n  [OK] Monitor MySQL Web iniciado")
    print("  >>  Abre tu navegador en:  http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
