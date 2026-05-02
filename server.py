#!/usr/bin/env python3
"""
Monitor de Salud DB — Backend Flask con PostgreSQL.
Métricas reales: pg_stat_database + psutil (CPU/RAM/disco)
+ contadores internos de queries y tiempo de respuesta.
"""

import os
import threading
import time
import logging
import configparser
from datetime import datetime, timedelta
from pathlib import Path

try:
    import psutil
    _PSUTIL_OK = True
except Exception:
    psutil = None  # type: ignore
    _PSUTIL_OK = False

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_OK = True
    _PSYCOPG2_ERR = None
except ImportError as e:
    psycopg2 = None
    _PSYCOPG2_OK = False
    _PSYCOPG2_ERR = str(e)

from flask import Flask, jsonify, render_template
from flask_cors import CORS

# ─────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("db_monitor")

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

BASE_DIR     = Path(__file__).parent
CONFIG_FILE  = BASE_DIR / "config.ini"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

try:
    _proc = psutil.Process() if _PSUTIL_OK else None
except Exception as e:
    _proc = None
    _PSUTIL_OK = False
    log.warning(f"No se pudo inicializar psutil.Process: {e}")

# ─── Contadores globales ──────────────────────────────────────
_stats = {
    "queries_total":   0,
    "queries_slow":    0,
    "requests_total":  0,
    "requests_active": 0,
    "start_time":      datetime.now(),
}
_stats_lock = threading.Lock()

_cache: dict = {"metrics": None, "alerts": [], "error": None, "last_update": None}
_cache_lock  = threading.Lock()

SLOW_QUERY_MS = 100


# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE, encoding="utf-8")
    return cfg


# ─────────────────────────────────────────────────────────────
# POSTGRESQL — conexión con instrumentación
# ─────────────────────────────────────────────────────────────
def get_db():
    """Devuelve una conexión psycopg2 a PostgreSQL."""
    if not _PSYCOPG2_OK:
        raise RuntimeError(f"psycopg2 no disponible: {_PSYCOPG2_ERR}")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurada.")
    return psycopg2.connect(DATABASE_URL)


def execute_tracked(conn, sql: str, params=()):
    """Ejecuta una query midiendo tiempo real e incrementa contadores."""
    t0 = time.perf_counter()
    cur = conn.cursor()
    cur.execute(sql, params)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    with _stats_lock:
        _stats["queries_total"] += 1
        if elapsed_ms > SLOW_QUERY_MS:
            _stats["queries_slow"] += 1
    return cur


def init_db(retries: int = 5, delay: float = 5.0):
    """Crea las tablas si no existen. Reintenta para tolerar arranques lentos."""
    stmts = [
        """
        CREATE TABLE IF NOT EXISTS health_snapshots (
            id                              SERIAL      PRIMARY KEY,
            captured_at                     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            max_connections                 INTEGER     NOT NULL DEFAULT 0,
            threads_connected               INTEGER     NOT NULL DEFAULT 0,
            threads_running                 INTEGER     NOT NULL DEFAULT 0,
            threads_cached                  INTEGER     NOT NULL DEFAULT 0,
            threads_created                 INTEGER     NOT NULL DEFAULT 0,
            connection_pct                  REAL        NOT NULL DEFAULT 0.0,
            questions                       INTEGER     NOT NULL DEFAULT 0,
            qps                             REAL        NOT NULL DEFAULT 0.0,
            slow_queries                    INTEGER     NOT NULL DEFAULT 0,
            innodb_buffer_pool_size         BIGINT      NOT NULL DEFAULT 0,
            innodb_buffer_pool_reads        INTEGER     NOT NULL DEFAULT 0,
            innodb_buffer_pool_read_reqs    INTEGER     NOT NULL DEFAULT 0,
            innodb_hit_ratio                REAL        NOT NULL DEFAULT 0.0,
            innodb_buffer_pool_pages_total  INTEGER     NOT NULL DEFAULT 0,
            innodb_buffer_pool_pages_free   INTEGER     NOT NULL DEFAULT 0,
            innodb_buffer_pool_pages_dirty  INTEGER     NOT NULL DEFAULT 0,
            uptime_seconds                  INTEGER     NOT NULL DEFAULT 0,
            status                          VARCHAR(20) NOT NULL DEFAULT 'OK',
            cpu_pct                         REAL        NOT NULL DEFAULT 0.0,
            mem_mb                          REAL        NOT NULL DEFAULT 0.0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS alert_log (
            id           SERIAL       PRIMARY KEY,
            alerted_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            severity     VARCHAR(20)  NOT NULL DEFAULT 'INFO',
            metric_name  VARCHAR(100) NOT NULL,
            metric_value VARCHAR(50)  NOT NULL,
            threshold    VARCHAR(100) NOT NULL,
            message      TEXT         NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_snap_at  ON health_snapshots (captured_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_alert_at ON alert_log        (alerted_at  DESC)",
    ]
    for attempt in range(1, retries + 1):
        try:
            conn = get_db()
            cur  = conn.cursor()
            for stmt in stmts:
                cur.execute(stmt)
            conn.commit()
            cur.close()
            conn.close()
            db_host = DATABASE_URL.split("@")[-1].split("/")[0] if "@" in DATABASE_URL else "?"
            log.info("PostgreSQL inicializado: %s", db_host)
            return
        except Exception as e:
            log.warning("init_db intento %d/%d falló: %s. Reintentando en %.0fs...", attempt, retries, e, delay)
            if attempt < retries:
                time.sleep(delay)
    log.error("No se pudo inicializar PostgreSQL. Revisa DATABASE_URL.")


# ─────────────────────────────────────────────────────────────
# RECOLECCIÓN DE MÉTRICAS REALES
# ─────────────────────────────────────────────────────────────
def collect_real_metrics() -> dict:
    """
    Métricas reales de tres fuentes:
      1. PostgreSQL pg_stat_database  — estado real de la BD
      2. psutil                       — CPU, RAM, disco, red
      3. Contadores internos          — queries HTTP, SQL
    """
    conn = get_db()

    # ── 1. pg_stat_database ──────────────────────────────────
    cur = execute_tracked(conn, """
        SELECT
            blks_hit,
            blks_read,
            numbackends,
            xact_commit,
            xact_rollback,
            tup_fetched,
            tup_inserted,
            tup_updated,
            tup_deleted,
            pg_database_size(datname) AS db_size_bytes
        FROM pg_stat_database
        WHERE datname = current_database()
    """)
    row = cur.fetchone() or (0,) * 10
    blks_hit, blks_read  = row[0] or 0, row[1] or 0
    num_backends         = row[2] or 0
    xact_commit          = row[3] or 0
    tup_fetched          = row[5] or 0
    tup_inserted, tup_updated, tup_deleted = row[6] or 0, row[7] or 0, row[8] or 0
    db_size_bytes        = row[9] or 0

    # Cache hit ratio real
    total_blks      = blks_hit + blks_read
    cache_hit_ratio = round(blks_hit * 100.0 / total_blks, 2) if total_blks > 0 else 99.9

    # Max connections del servidor
    cur2 = execute_tracked(conn, "SELECT setting::integer FROM pg_settings WHERE name = 'max_connections'")
    max_conn_db = (cur2.fetchone() or [100])[0]

    # Conexiones activas (excluyendo la propia)
    cur3 = execute_tracked(conn, "SELECT count(*) FROM pg_stat_activity WHERE state = 'active' AND pid <> pg_backend_pid()")
    active_conn = (cur3.fetchone() or [0])[0]

    # Conexiones en espera (lock wait, etc.)
    cur4 = execute_tracked(conn, "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type IS NOT NULL AND pid <> pg_backend_pid()")
    waiting_conn = (cur4.fetchone() or [0])[0]

    # Block size para calcular "páginas"
    cur5 = execute_tracked(conn, "SELECT current_setting('block_size')::integer")
    block_size = (cur5.fetchone() or [8192])[0]

    conn.close()

    db_size_mb  = round(db_size_bytes / 1024 / 1024, 3)
    page_count  = db_size_bytes // block_size if block_size > 0 else 0
    conn_pct    = round(min(99.9, (num_backends / max_conn_db) * 100), 2) if max_conn_db > 0 else 0.0

    # ── 2. psutil ─────────────────────────────────────────────
    try:
        if _PSUTIL_OK and _proc is not None:
            cpu_pct       = _proc.cpu_percent(interval=None)
            mem_info      = _proc.memory_info()
            mem_mb        = round(mem_info.rss / 1024 / 1024, 1)
            threads_os    = _proc.num_threads()
            disk          = psutil.disk_usage(str(BASE_DIR))
            disk_used_pct = disk.percent
            host_cpu      = psutil.cpu_percent(interval=None)
            host_mem      = psutil.virtual_memory()
            host_mem_pct  = host_mem.percent
            net           = psutil.net_io_counters()
            net_sent      = net.bytes_sent
            net_recv      = net.bytes_recv
        else:
            raise RuntimeError("psutil no disponible")
    except Exception:
        cpu_pct = mem_mb = disk_used_pct = 0.0
        threads_os = host_cpu = host_mem_pct = net_sent = net_recv = 0

    # ── 3. Contadores internos ────────────────────────────────
    with _stats_lock:
        queries_total  = _stats["queries_total"]
        queries_slow   = _stats["queries_slow"]
        requests_total = _stats["requests_total"]
        req_active     = _stats["requests_active"]
        start_time     = _stats["start_time"]

    uptime_sec = int((datetime.now() - start_time).total_seconds())
    qps        = round(queries_total / uptime_sec, 2) if uptime_sec > 0 else 0.0

    td = timedelta(seconds=uptime_sec)
    d, h, m, s = td.days, td.seconds // 3600, (td.seconds % 3600) // 60, td.seconds % 60
    uptime_str = f"{d}d {h:02}h {m:02}m {s:02}s"

    processes = []
    try:
        if _proc:
            for t in _proc.threads()[:20]:
                processes.append({
                    "ID": t.id, "USER": "python",
                    "HOST": os.environ.get("WEBSITE_HOSTNAME", "localhost"),
                    "DB": "postgres", "COMMAND": "Thread",
                    "TIME": int(t.system_time + t.user_time),
                    "STATE": "running", "INFO": "",
                })
    except Exception:
        pass

    db_sizes = [{
        "db_name": f"{os.environ.get('PGDATABASE', 'postgres')} (PostgreSQL)",
        "size_mb": db_size_mb, "tables": 2,
    }]

    return {
        "timestamp":      datetime.now().isoformat(),
        "version":        f"PostgreSQL ({os.environ.get('PGHOST', 'Azure')})",
        "hostname":       os.environ.get("WEBSITE_HOSTNAME", "localhost"),
        "uptime_seconds": uptime_sec,
        "uptime_str":     uptime_str,
        # Conexiones
        "max_connections":   max_conn_db,
        "threads_connected": num_backends,
        "threads_running":   active_conn,
        "threads_cached":    waiting_conn,
        "threads_created":   threads_os,
        "connection_pct":    conn_pct,
        # Rendimiento
        "questions":    queries_total,
        "qps":          qps,
        "slow_queries": queries_slow,
        "com_select":   tup_fetched,
        "com_insert":   tup_inserted,
        "com_update":   tup_updated,
        "com_delete":   tup_deleted,
        # Buffer / Cache (PostgreSQL shared_buffers)
        "innodb_hit_ratio": cache_hit_ratio,
        "bp_size_mb":       db_size_mb,
        "bp_used_pct":      min(99.9, round((db_size_bytes / max(db_size_bytes + 1, 1)) * 100, 1)),
        "bp_pages_total":   page_count,
        "bp_pages_free":    0,
        "bp_pages_dirty":   0,
        # Listas
        "processes":   processes,
        "db_sizes":    db_sizes,
        "top_tables":  [],
        "replication": None,
        # Extra
        "host_cpu_pct":    host_cpu,
        "host_mem_pct":    host_mem_pct,
        "process_cpu_pct": cpu_pct,
        "process_mem_mb":  mem_mb,
        "disk_used_pct":   disk_used_pct,
        "net_bytes_sent":  net_sent,
        "net_bytes_recv":  net_recv,
        "db_size_mb":      db_size_mb,
        "journal_mode":    "PostgreSQL WAL",
        "page_size_bytes": block_size,
        "cache_size_pages": page_count,
    }


def _overall_status(metrics: dict) -> str:
    if metrics["connection_pct"] >= 90 or metrics["innodb_hit_ratio"] < 70:
        return "CRITICAL"
    if metrics["connection_pct"] >= 70 or metrics["innodb_hit_ratio"] < 85:
        return "WARNING"
    return "OK"


# ─────────────────────────────────────────────────────────────
# PERSISTENCIA EN POSTGRESQL
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
                %s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,%s,%s,
                %s,%s
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


def save_alerts(alerts: list):
    if not alerts:
        return
    conn = get_db()
    try:
        for a in alerts:
            execute_tracked(conn, """
                INSERT INTO alert_log (severity, metric_name, metric_value, threshold, message)
                VALUES (%s, %s, %s, %s, %s)
            """, (a["severity"], a["metric"], a["value"], a["threshold"],
                  f"{a['metric']} = {a['value']} | umbral: {a['threshold']}"))
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# ALERTAS
# ─────────────────────────────────────────────────────────────
def evaluate_alerts(metrics: dict, cfg) -> list:
    def thr(k, d): return cfg.getfloat("thresholds", k, fallback=d)
    alerts = []
    checks = [
        ("connection_pct",   metrics["connection_pct"],   thr("connections_warning", 70),  thr("connections_critical", 90),  "Uso de Conexiones",    "%",  False),
        ("innodb_hit_ratio", metrics["innodb_hit_ratio"], thr("cache_hit_warning", 85),    thr("cache_hit_critical", 70),    "Cache Hit Ratio PG",   "%",  True),
        ("threads_running",  metrics["threads_running"],  thr("threads_running_warning", 20), thr("threads_running_critical", 50), "Conexiones Activas", "", False),
        ("slow_queries",     metrics["slow_queries"],     thr("slow_queries_warning", 10), thr("slow_queries_critical", 50), "Queries Lentas",       "",   False),
        ("host_cpu_pct",     metrics["host_cpu_pct"],     thr("cpu_warning", 70),          thr("cpu_critical", 90),          "CPU Host",             "%",  False),
        ("host_mem_pct",     metrics["host_mem_pct"],     thr("mem_warning", 80),          thr("mem_critical", 95),          "Memoria Host",         "%",  False),
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
    cfg        = load_config()
    refresh    = cfg.getint("monitor", "refresh_interval", fallback=5)
    save_every = max(1, 60 // refresh)
    tick       = 0
    cpu_warmed = False

    # Inicializar BD en segundo plano para no bloquear el arranque de la app
    log.info("Iniciando conexión a base de datos en segundo plano...")
    init_db(retries=20, delay=15.0)

    while True:
        try:
            if not cpu_warmed and _PSUTIL_OK and _proc is not None:
                _proc.cpu_percent(interval=None)
                psutil.cpu_percent(interval=None)
                cpu_warmed = True

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
            log.error("Error en background_collector: %s", e)
            with _cache_lock:
                _cache["error"] = f"Error al recopilar métricas: {e}"

        time.sleep(refresh)


# ─────────────────────────────────────────────────────────────
# MIDDLEWARE
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


@app.route("/api/health")
def api_health():
    """Health check para Azure."""
    if not _PSYCOPG2_OK:
        return {"status": "error", "error": f"ImportError: {_PSYCOPG2_ERR}"}, 500

    with _cache_lock:
        ok  = _cache["metrics"] is not None
        err = _cache["error"]
    return {"status": "ok" if ok else "starting", "error": err}, 200


@app.route("/api/metrics")
def api_metrics():
    with _cache_lock:
        if _cache["error"] and _cache["metrics"] is None:
            return {"error": _cache["error"]}, 503
        if _cache["metrics"] is None:
            return {"status": "loading"}, 202
        return {
            "metrics":     _cache["metrics"],
            "alerts":      _cache["alerts"],
            "last_update": _cache["last_update"],
        }


@app.route("/api/history")
def api_history():
    try:
        conn = get_db()
        cur  = execute_tracked(conn, """
            SELECT captured_at, threads_connected, threads_running,
                   connection_pct, qps, innodb_hit_ratio, slow_queries,
                   status, cpu_pct, mem_mb
            FROM health_snapshots
            ORDER BY id DESC LIMIT 100
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in reversed(cur.fetchall())]
        conn.close()
        # Serializar timestamps
        for r in rows:
            if hasattr(r.get("captured_at"), "isoformat"):
                r["captured_at"] = r["captured_at"].isoformat()
        return rows
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/api/alerts/history")
def api_alerts_history():
    try:
        conn = get_db()
        cur  = execute_tracked(conn, """
            SELECT alerted_at, severity, metric_name, metric_value, threshold, message
            FROM alert_log
            ORDER BY id DESC LIMIT 50
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        for r in rows:
            if hasattr(r.get("alerted_at"), "isoformat"):
                r["alerted_at"] = r["alerted_at"].isoformat()
        return rows
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/api/config")
def api_config():
    cfg = load_config()
    return {
        "refresh_interval": cfg.getint("monitor", "refresh_interval", fallback=5),
        "storage":          "PostgreSQL",
        "db_host":          os.environ.get("PGHOST", "Azure"),
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
    }


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def startup():
    log.info("=== DB Health Monitor (PostgreSQL) arrancando ===")
    log.info("DATABASE_URL host: %s",
             DATABASE_URL.split("@")[-1].split("/")[0] if "@" in DATABASE_URL else "(no configurada)")
    t = threading.Thread(target=background_collector, daemon=True)
    t.start()
    log.info("Hilo de recolección iniciado.")


if __name__ == "__main__":
    startup()
    print(f"\n  [OK] DB Health Monitor (PostgreSQL) iniciado")
    print(f"  >>  Abre tu navegador en: http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
else:
    startup()
