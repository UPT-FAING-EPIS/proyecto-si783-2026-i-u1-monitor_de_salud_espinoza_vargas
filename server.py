#!/usr/bin/env python3
"""Monitor de Salud DB — Flask + PostgreSQL + Multi-datasource + SQL Import."""

import os, re, threading, time, logging, configparser
from datetime import datetime, timedelta
from pathlib import Path

try:
    import psutil
    _proc = psutil.Process()
    _PSUTIL = True
except Exception:
    psutil = None; _proc = None; _PSUTIL = False

import psycopg2, psycopg2.extras
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from db_connection import (
    get_monitor_conn, release_conn, build_dsn,
    connect_to_datasource, test_datasource, load_config
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("monitor")

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

BASE_DIR = Path(__file__).parent
_initialized = threading.Event()
_cache: dict = {}          # {ds_id: {"metrics": ..., "error": ..., "ts": ...}}
_cache_lock = threading.Lock()

# ── Config helpers ────────────────────────────────────────────────────────────

def cfg_int(section, key, fallback):
    try: return load_config().getint(section, key, fallback=fallback)
    except: return fallback

def cfg_bool(section, key, fallback=True):
    try: return load_config().getboolean(section, key, fallback=fallback)
    except: return fallback

# ── DB Init ───────────────────────────────────────────────────────────────────


INIT_SQL = [
    # 1. Tablas nuevas (si no existen)
    """CREATE TABLE IF NOT EXISTS datasources (
        id         SERIAL PRIMARY KEY,
        nombre     VARCHAR(100) NOT NULL,
        tipo_db    VARCHAR(20)  NOT NULL DEFAULT 'postgresql',
        host       VARCHAR(255) NOT NULL,
        puerto     INTEGER      NOT NULL DEFAULT 5432,
        usuario    VARCHAR(100) NOT NULL,
        password   TEXT         NOT NULL DEFAULT '',
        database   VARCHAR(100) NOT NULL,
        activa     BOOLEAN      NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS health_snapshots (
        id                SERIAL PRIMARY KEY,
        datasource_id     INTEGER,
        captured_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        max_connections   INTEGER NOT NULL DEFAULT 0,
        threads_connected INTEGER NOT NULL DEFAULT 0,
        threads_running   INTEGER NOT NULL DEFAULT 0,
        connection_pct    REAL    NOT NULL DEFAULT 0,
        qps               REAL    NOT NULL DEFAULT 0,
        slow_queries      INTEGER NOT NULL DEFAULT 0,
        cache_hit_ratio   REAL    NOT NULL DEFAULT 0,
        db_size_mb        REAL    NOT NULL DEFAULT 0,
        cpu_pct           REAL    NOT NULL DEFAULT 0,
        mem_pct           REAL    NOT NULL DEFAULT 0,
        status            VARCHAR(20) NOT NULL DEFAULT 'OK'
    )""",
    """CREATE TABLE IF NOT EXISTS alert_log (
        id            SERIAL PRIMARY KEY,
        datasource_id INTEGER,
        alerted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        severity      VARCHAR(20) NOT NULL DEFAULT 'INFO',
        metric_name   VARCHAR(100) NOT NULL DEFAULT '',
        metric_value  VARCHAR(50) NOT NULL DEFAULT '',
        threshold     VARCHAR(100) NOT NULL DEFAULT '',
        message       TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS sql_imports (
        id                SERIAL PRIMARY KEY,
        datasource_id     INTEGER,
        filename          VARCHAR(255) NOT NULL,
        uploaded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        status            VARCHAR(20)  NOT NULL DEFAULT 'pending',
        statements_ok     INTEGER NOT NULL DEFAULT 0,
        statements_failed INTEGER NOT NULL DEFAULT 0,
        error_message     TEXT
    )""",
    # 2. Migración: columnas que pueden faltar en BD existente
    "ALTER TABLE health_snapshots ADD COLUMN IF NOT EXISTS datasource_id INTEGER",
    "ALTER TABLE health_snapshots ADD COLUMN IF NOT EXISTS cache_hit_ratio REAL NOT NULL DEFAULT 0",
    "ALTER TABLE health_snapshots ADD COLUMN IF NOT EXISTS db_size_mb REAL NOT NULL DEFAULT 0",
    "ALTER TABLE health_snapshots ADD COLUMN IF NOT EXISTS cpu_pct REAL NOT NULL DEFAULT 0",
    "ALTER TABLE health_snapshots ADD COLUMN IF NOT EXISTS mem_pct REAL NOT NULL DEFAULT 0",
    "ALTER TABLE health_snapshots ADD COLUMN IF NOT EXISTS qps REAL NOT NULL DEFAULT 0",
    "ALTER TABLE health_snapshots ADD COLUMN IF NOT EXISTS slow_queries INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE alert_log ADD COLUMN IF NOT EXISTS datasource_id INTEGER",
    # 3. Índices
    "CREATE INDEX IF NOT EXISTS idx_snap_ds  ON health_snapshots (datasource_id, captured_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_alert_ds ON alert_log        (datasource_id, alerted_at  DESC)",
    "CREATE INDEX IF NOT EXISTS idx_imp_ds   ON sql_imports      (datasource_id, uploaded_at DESC)",
]


def init_db(retries=10, delay=6.0):
    for attempt in range(retries):
        try:
            conn = get_monitor_conn()
            cur  = conn.cursor()
            for stmt in INIT_SQL:
                try:
                    cur.execute(stmt)
                    conn.commit()
                except Exception as e:
                    log.warning("init_db stmt skip: %s", str(e)[:120])
                    conn.rollback()
            # Seed datasource principal si no hay ninguno
            cur.execute("SELECT COUNT(*) FROM datasources")
            if cur.fetchone()[0] == 0:
                cfg = load_config()
                cur.execute("""
                    INSERT INTO datasources (nombre, tipo_db, host, puerto, usuario, password, database)
                    VALUES (%s,'postgresql',%s,%s,%s,%s,%s)
                """, (
                    "Monitor Principal (VM)",
                    cfg.get("postgresql","host",fallback="38.250.116.71"),
                    cfg.getint("postgresql","port",fallback=5432),
                    cfg.get("postgresql","user",fallback="monitor"),
                    cfg.get("postgresql","password",fallback=""),
                    cfg.get("postgresql","database",fallback="db_health_monitor"),
                ))
                conn.commit()
            cur.close()
            release_conn(conn)
            log.info("Base de datos inicializada OK.")
            _initialized.set()
            return True
        except Exception as e:
            log.warning("init_db intento %d/%d: %s", attempt+1, retries, e)
            if attempt < retries-1: time.sleep(delay)
    log.error("No se pudo inicializar la BD tras %d intentos.", retries)
    _initialized.set()
    return False

# ── Recolección de métricas ───────────────────────────────────────────────────

def collect_pg_metrics(ds: dict) -> dict:
    conn = connect_to_datasource(ds, timeout=8)
    cur = conn.cursor()

    cur.execute("""
        SELECT blks_hit, blks_read, numbackends,
               pg_database_size(datname) AS sz
        FROM pg_stat_database WHERE datname = current_database()
    """)
    row = cur.fetchone() or (0,0,0,0)
    blks_hit, blks_read, num_backends, db_size_bytes = row
    total_blks = (blks_hit or 0) + (blks_read or 0)
    cache_hit  = round((blks_hit/total_blks)*100, 2) if total_blks else 99.9

    cur.execute("SELECT setting::int FROM pg_settings WHERE name='max_connections'")
    max_conn = (cur.fetchone() or [100])[0]

    cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state='active' AND pid<>pg_backend_pid()")
    active = (cur.fetchone() or [0])[0]

    cur.execute("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type IS NOT NULL AND pid<>pg_backend_pid()")
    waiting = (cur.fetchone() or [0])[0]

    cur.close(); conn.close()

    db_mb   = round((db_size_bytes or 0)/1024/1024, 2)
    conn_pct= round(min(99.9, num_backends/max_conn*100), 2) if max_conn else 0

    # psutil
    cpu_pct = mem_pct = 0.0
    try:
        if _PSUTIL:
            cpu_pct = psutil.cpu_percent(interval=None)
            mem_pct = psutil.virtual_memory().percent
    except Exception: pass

    status = "OK"
    if conn_pct >= 90 or cache_hit < 70: status = "CRITICAL"
    elif conn_pct >= 70 or cache_hit < 85: status = "WARNING"

    return {
        "datasource_id":    ds["id"],
        "tipo_db":          "postgresql",
        "timestamp":        datetime.now().isoformat(),
        "max_connections":  max_conn,
        "threads_connected":num_backends,
        "threads_running":  active,
        "threads_waiting":  waiting,
        "connection_pct":   conn_pct,
        "qps":              0.0,
        "slow_queries":     0,
        "cache_hit_ratio":  cache_hit,
        "db_size_mb":       db_mb,
        "cpu_pct":          cpu_pct,
        "mem_pct":          mem_pct,
        "status":           status,
    }

def save_snapshot(m: dict):
    conn = get_monitor_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO health_snapshots
              (datasource_id, max_connections, threads_connected, threads_running,
               connection_pct, qps, slow_queries, cache_hit_ratio,
               db_size_mb, cpu_pct, mem_pct, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (m["datasource_id"], m["max_connections"], m["threads_connected"],
              m["threads_running"], m["connection_pct"], m["qps"],
              m["slow_queries"], m["cache_hit_ratio"], m["db_size_mb"],
              m["cpu_pct"], m["mem_pct"], m["status"]))
        cur.execute("""
            DELETE FROM health_snapshots
            WHERE datasource_id=%s AND id NOT IN (
              SELECT id FROM health_snapshots
              WHERE datasource_id=%s ORDER BY id DESC LIMIT 5000
            )
        """, (m["datasource_id"], m["datasource_id"]))
        conn.commit(); cur.close()
    finally:
        release_conn(conn)

def evaluate_alerts(m: dict, cfg) -> list:
    def t(k,d): return cfg.getfloat("thresholds",k,fallback=d)
    alerts = []
    checks = [
        ("connection_pct",  m["connection_pct"],  t("connections_warning",70), t("connections_critical",90), "Conexiones %",     False),
        ("cache_hit_ratio", m["cache_hit_ratio"],  t("cache_hit_warning",85),  t("cache_hit_critical",70),   "Cache Hit Ratio %", True),
        ("cpu_pct",         m["cpu_pct"],          t("cpu_warning",75),        t("cpu_critical",90),         "CPU %",            False),
        ("mem_pct",         m["mem_pct"],          t("mem_warning",80),        t("mem_critical",95),         "Memoria %",        False),
    ]
    for key, val, warn, crit, label, invert in checks:
        if invert:
            sev = "CRITICAL" if val < crit else "WARNING" if val < warn else None
        else:
            sev = "CRITICAL" if val >= crit else "WARNING" if val >= warn else None
        if sev:
            alerts.append({"severity":sev,"metric":label,"value":str(val),
                           "threshold":f"W={warn} C={crit}","ds_id":m["datasource_id"]})
    return alerts

def save_alerts(alerts: list, ds_id: int):
    if not alerts: return
    conn = get_monitor_conn()
    try:
        cur = conn.cursor()
        for a in alerts:
            cur.execute("""
                INSERT INTO alert_log (datasource_id,severity,metric_name,metric_value,threshold,message)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (ds_id, a["severity"], a["metric"], a["value"], a["threshold"],
                  f"{a['metric']}={a['value']} | umbral:{a['threshold']}"))
        conn.commit(); cur.close()
    finally:
        release_conn(conn)

# ── Hilo de fondo ─────────────────────────────────────────────────────────────

def background_collector():
    log.info("Iniciando background_collector...")
    init_db()
    cfg = load_config()
    tick = 0
    if _PSUTIL:
        try: psutil.cpu_percent(interval=None)
        except: pass

    while True:
        try:
            conn = get_monitor_conn()
            cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM datasources WHERE activa=TRUE")
            sources = [dict(r) for r in cur.fetchall()]
            cur.close(); release_conn(conn)

            for ds in sources:
                ds_id = ds["id"]
                try:
                    tipo = (ds.get("tipo_db") or "postgresql").lower()
                    if tipo == "postgresql":
                        m = collect_pg_metrics(ds)
                    else:
                        raise NotImplementedError(f"Tipo '{tipo}' no soportado aún.")
                    with _cache_lock:
                        _cache[ds_id] = {"metrics": m, "error": None,
                                         "ts": datetime.now().isoformat()}
                    tick += 1
                    if tick % 2 == 0:
                        save_snapshot(m)
                        alts = evaluate_alerts(m, cfg)
                        if alts: save_alerts(alts, ds_id)
                except Exception as e:
                    log.error("DS %s error: %s", ds_id, e)
                    with _cache_lock:
                        prev = _cache.get(ds_id, {})
                        _cache[ds_id] = {"metrics": prev.get("metrics"),
                                         "error": str(e),
                                         "ts": datetime.now().isoformat()}
        except Exception as e:
            log.error("background_collector: %s", e)

        interval = cfg_int("monitor", "refresh_interval", 30)
        time.sleep(interval)

# ── SQL Import helpers ────────────────────────────────────────────────────────

DANGEROUS_RE = re.compile(
    r"\b(DROP\s+DATABASE|DROP\s+SCHEMA\s+public|TRUNCATE)\b",
    re.IGNORECASE
)

def split_sql(text: str) -> list:
    text = re.sub(r"--[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return [s.strip() for s in text.split(";") if s.strip()]

def get_ds_by_id(ds_id: int) -> dict | None:
    conn = get_monitor_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM datasources WHERE id=%s", (ds_id,))
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None
    finally:
        release_conn(conn)

# ── Rutas ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index(): return render_template("index.html")

@app.route("/api/health")
def api_health():
    started = _initialized.is_set()
    return {"status": "ok" if started else "starting"}, 200

# ── Datasources CRUD ──────────────────────────────────────────────────────────

@app.route("/api/datasources", methods=["GET"])
def api_ds_list():
    conn = get_monitor_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id,nombre,tipo_db,host,puerto,usuario,database,activa,created_at FROM datasources ORDER BY id")
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            if hasattr(r.get("created_at"), "isoformat"):
                r["created_at"] = r["created_at"].isoformat()
            ds_id = r["id"]
            with _cache_lock:
                cached = _cache.get(ds_id, {})
            r["status"]     = cached.get("metrics", {}).get("status", "unknown") if cached.get("metrics") else "unknown"
            r["last_error"] = cached.get("error")
            r["last_ts"]    = cached.get("ts")
        return jsonify(rows)
    finally:
        release_conn(conn)

@app.route("/api/datasources", methods=["POST"])
def api_ds_create():
    d = request.json or {}
    required = ["nombre","tipo_db","host","puerto","usuario","database"]
    missing = [f for f in required if not d.get(f)]
    if missing:
        return {"error": f"Faltan campos: {', '.join(missing)}"}, 400
    conn = get_monitor_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO datasources (nombre,tipo_db,host,puerto,usuario,password,database,activa)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (d["nombre"], d["tipo_db"], d["host"], int(d["puerto"]),
              d["usuario"], d.get("password",""), d["database"],
              d.get("activa", True)))
        new_id = cur.fetchone()[0]
        conn.commit(); cur.close()
        return {"id": new_id, "message": "Datasource creado."}, 201
    finally:
        release_conn(conn)

@app.route("/api/datasources/<int:ds_id>", methods=["PUT"])
def api_ds_update(ds_id):
    d = request.json or {}
    conn = get_monitor_conn()
    try:
        cur = conn.cursor()
        fields, vals = [], []
        for col in ["nombre","tipo_db","host","puerto","usuario","password","database","activa"]:
            if col in d:
                fields.append(f"{col}=%s")
                vals.append(int(d[col]) if col == "puerto" else d[col])
        if not fields:
            return {"error": "Sin campos para actualizar."}, 400
        vals.append(ds_id)
        cur.execute(f"UPDATE datasources SET {', '.join(fields)} WHERE id=%s", vals)
        if cur.rowcount == 0:
            return {"error": "No encontrado."}, 404
        conn.commit(); cur.close()
        return {"message": "Actualizado."}
    finally:
        release_conn(conn)

@app.route("/api/datasources/<int:ds_id>", methods=["DELETE"])
def api_ds_delete(ds_id):
    conn = get_monitor_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM datasources WHERE id=%s", (ds_id,))
        if cur.rowcount == 0:
            return {"error": "No encontrado."}, 404
        conn.commit(); cur.close()
        with _cache_lock:
            _cache.pop(ds_id, None)
        return {"message": "Eliminado."}
    finally:
        release_conn(conn)

@app.route("/api/datasources/<int:ds_id>/test", methods=["POST"])
def api_ds_test(ds_id):
    ds = get_ds_by_id(ds_id)
    if not ds:
        return {"error": "No encontrado."}, 404
    ok, ms, err = test_datasource(ds)
    return {"ok": ok, "latency_ms": ms, "error": err}

# ── Métricas y resumen ────────────────────────────────────────────────────────

@app.route("/api/metrics")
def api_metrics():
    ds_id = request.args.get("datasource_id", type=int)
    with _cache_lock:
        snap = dict(_cache)
    if ds_id:
        entry = snap.get(ds_id)
        if not entry:
            return {"status": "loading"}, 202
        return jsonify(entry)
    # todos
    return jsonify(snap)

@app.route("/api/summary/global")
def api_summary_global():
    with _cache_lock:
        snap = dict(_cache)
    total  = len(snap)
    online = sum(1 for v in snap.values() if not v.get("error") and v.get("metrics"))
    statuses = [v["metrics"]["status"] for v in snap.values() if v.get("metrics")]
    global_st = "CRITICAL" if "CRITICAL" in statuses else "WARNING" if "WARNING" in statuses else "OK"
    return jsonify({
        "total_datasources": total,
        "online": online,
        "offline": total - online,
        "global_status": global_st,
        "datasources": {
            ds_id: {"status": v.get("metrics",{}).get("status","unknown"),
                    "error":  v.get("error"), "ts": v.get("ts")}
            for ds_id, v in snap.items()
        }
    })

@app.route("/api/summary/<int:ds_id>")
def api_summary_ds(ds_id):
    with _cache_lock:
        entry = _cache.get(ds_id)
    if not entry:
        return {"status": "loading"}, 202
    return jsonify(entry)

@app.route("/api/history")
def api_history():
    ds_id = request.args.get("datasource_id", type=int)
    conn = get_monitor_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if ds_id:
            cur.execute("""
                SELECT * FROM health_snapshots WHERE datasource_id=%s
                ORDER BY id DESC LIMIT 100
            """, (ds_id,))
        else:
            cur.execute("SELECT * FROM health_snapshots ORDER BY id DESC LIMIT 200")
        rows = [dict(r) for r in reversed(cur.fetchall())]
        cur.close()
        for r in rows:
            if hasattr(r.get("captured_at"), "isoformat"):
                r["captured_at"] = r["captured_at"].isoformat()
        return jsonify(rows)
    finally:
        release_conn(conn)

@app.route("/api/alerts/history")
def api_alerts_history():
    ds_id = request.args.get("datasource_id", type=int)
    conn = get_monitor_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if ds_id:
            cur.execute("""
                SELECT * FROM alert_log WHERE datasource_id=%s
                ORDER BY id DESC LIMIT 50
            """, (ds_id,))
        else:
            cur.execute("SELECT * FROM alert_log ORDER BY id DESC LIMIT 100")
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            if hasattr(r.get("alerted_at"), "isoformat"):
                r["alerted_at"] = r["alerted_at"].isoformat()
        return jsonify(rows)
    finally:
        release_conn(conn)

# ── Importación SQL ───────────────────────────────────────────────────────────

@app.route("/api/import-sql", methods=["POST"])
def api_import_sql():
    ds_id = request.form.get("datasource_id", type=int)
    if not ds_id:
        return {"error": "datasource_id requerido"}, 400

    f = request.files.get("file")
    if not f:
        return {"error": "Archivo requerido"}, 400
    if not f.filename.lower().endswith(".sql"):
        return {"error": "Solo se aceptan archivos .sql"}, 400

    max_mb  = cfg_int("monitor", "max_sql_upload_mb", 10)
    content = f.read()
    if len(content) > max_mb * 1024 * 1024:
        return {"error": f"Archivo supera el límite de {max_mb} MB"}, 413

    try:
        sql_text = content.decode("utf-8")
    except Exception:
        sql_text = content.decode("latin-1", errors="replace")

    if cfg_bool("monitor", "block_dangerous_sql", True):
        m = DANGEROUS_RE.search(sql_text)
        if m:
            _record_import(ds_id, f.filename, "blocked", 0, 0, f"Instrucción bloqueada: {m.group()}")
            return {"error": f"Instrucción peligrosa detectada: {m.group()}"}, 400

    ds = get_ds_by_id(ds_id)
    if not ds:
        return {"error": "Datasource no encontrado"}, 404

    statements = split_sql(sql_text)
    if not statements:
        return {"error": "El archivo no contiene sentencias SQL válidas"}, 400

    ok_count = fail_count = 0
    errors = []
    try:
        conn = connect_to_datasource(ds, timeout=30)
        conn.autocommit = False
        cur = conn.cursor()
        try:
            for stmt in statements:
                try:
                    cur.execute(stmt)
                    ok_count += 1
                except Exception as e:
                    fail_count += 1
                    errors.append(str(e)[:200])
                    conn.rollback()
                    break
            else:
                conn.commit()
        finally:
            cur.close(); conn.close()
    except Exception as e:
        return {"error": f"Error de conexión: {e}"}, 502

    status = "success" if fail_count == 0 else "failed"
    _record_import(ds_id, f.filename, status, ok_count, fail_count,
                   errors[0] if errors else None)
    return jsonify({
        "status": status,
        "statements_ok":     ok_count,
        "statements_failed": fail_count,
        "total_statements":  len(statements),
        "errors":            errors[:5],
    })

def _record_import(ds_id, filename, status, ok, fail, err_msg):
    try:
        conn = get_monitor_conn()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO sql_imports
              (datasource_id,filename,status,statements_ok,statements_failed,error_message)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (ds_id, filename, status, ok, fail, err_msg))
        conn.commit(); cur.close()
        release_conn(conn)
    except Exception as e:
        log.error("No se pudo guardar historial de importación: %s", e)

@app.route("/api/import-history")
def api_import_history():
    ds_id = request.args.get("datasource_id", type=int)
    conn  = get_monitor_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if ds_id:
            cur.execute("""
                SELECT i.*, d.nombre as ds_nombre FROM sql_imports i
                LEFT JOIN datasources d ON d.id=i.datasource_id
                WHERE i.datasource_id=%s ORDER BY i.id DESC LIMIT 50
            """, (ds_id,))
        else:
            cur.execute("""
                SELECT i.*, d.nombre as ds_nombre FROM sql_imports i
                LEFT JOIN datasources d ON d.id=i.datasource_id
                ORDER BY i.id DESC LIMIT 100
            """)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            if hasattr(r.get("uploaded_at"), "isoformat"):
                r["uploaded_at"] = r["uploaded_at"].isoformat()
        return jsonify(rows)
    finally:
        release_conn(conn)

# ── Config endpoint ───────────────────────────────────────────────────────────

@app.route("/api/config")
def api_config():
    cfg = load_config()
    return jsonify({
        "refresh_interval": cfg_int("monitor","refresh_interval",30),
        "max_sql_upload_mb": cfg_int("monitor","max_sql_upload_mb",10),
        "block_dangerous_sql": cfg_bool("monitor","block_dangerous_sql",True),
    })

# ── Arranque ──────────────────────────────────────────────────────────────────

def startup():
    log.info("=== DB Health Monitor arrancando ===")
    if _PSUTIL:
        try: psutil.cpu_percent(interval=None)
        except: pass
    t = threading.Thread(target=background_collector, daemon=True)
    t.start()

if __name__ == "__main__":
    startup()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
else:
    startup()
