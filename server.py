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
from flask import Flask, jsonify, render_template, request, session
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from db_connection import (
    get_monitor_conn, release_conn, build_dsn,
    connect_to_datasource, test_datasource, load_config
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("monitor")

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

BASE_DIR = Path(__file__).parent
_initialized = threading.Event()
_cache: dict = {}          # {ds_id: {"metrics": ..., "error": ..., "ts": ...}}
_cache_lock = threading.Lock()

AUTH_PUBLIC_PATHS = {
    "/",
    "/api/health",
    "/api/config",
    "/api/login",
    "/api/register",
    "/api/me",
    "/api/logout",
    "/static/style.css",
    "/static/dashboard.js",
}

FILE_PROFILE_DEFS = {
    "postgresql": [
        {"key": "config", "label": "Configuración", "description": "postgresql.conf, pg_hba.conf", "paths": ["{config_dir}/postgresql.conf", "{config_dir}/pg_hba.conf", "{config_dir}/pg_ident.conf"]},
        {"key": "data", "label": "Datos", "description": "Directorio de datos y WAL", "paths": ["{data_dir}", "{data_dir}/pg_wal"]},
        {"key": "log", "label": "Logs", "description": "Registros del servidor", "paths": ["{log_dir}"]},
        {"key": "backup", "label": "Respaldo", "description": "Directorio de backups", "paths": ["{backup_dir}"]},
    ],
    "mysql": [
        {"key": "config", "label": "Configuración", "description": "my.cnf / mysqld.cnf", "paths": ["{config_dir}/my.cnf", "{config_dir}/mysql.conf.d/mysqld.cnf"]},
        {"key": "data", "label": "Datos", "description": "Directorio de datos", "paths": ["{data_dir}"]},
        {"key": "log", "label": "Logs", "description": "Error log y logs del motor", "paths": ["{log_dir}"]},
        {"key": "backup", "label": "Respaldo", "description": "Directorio de backups", "paths": ["{backup_dir}"]},
    ],
    "mariadb": [
        {"key": "config", "label": "Configuración", "description": "50-server.cnf / my.cnf", "paths": ["{config_dir}/my.cnf", "{config_dir}/mariadb.conf.d/50-server.cnf"]},
        {"key": "data", "label": "Datos", "description": "Directorio de datos", "paths": ["{data_dir}"]},
        {"key": "log", "label": "Logs", "description": "Registro de errores", "paths": ["{log_dir}"]},
        {"key": "backup", "label": "Respaldo", "description": "Directorio de backups", "paths": ["{backup_dir}"]},
    ],
    "sqlserver": [
        {"key": "config", "label": "Configuración", "description": "Archivos de instancia y configuración", "paths": ["{config_dir}"]},
        {"key": "data", "label": "Datos", "description": "Archivos MDF/NDF", "paths": ["{data_dir}"]},
        {"key": "log", "label": "Logs", "description": "Log de error y trazas", "paths": ["{log_dir}"]},
        {"key": "backup", "label": "Respaldo", "description": "Backups", "paths": ["{backup_dir}"]},
    ],
    "mongodb": [
        {"key": "config", "label": "Configuración", "description": "mongod.conf", "paths": ["{config_dir}/mongod.conf"]},
        {"key": "data", "label": "Datos", "description": "dbPath", "paths": ["{data_dir}"]},
        {"key": "log", "label": "Logs", "description": "Log de MongoDB", "paths": ["{log_dir}"]},
        {"key": "backup", "label": "Respaldo", "description": "Backups", "paths": ["{backup_dir}"]},
    ],
}

# ── Config helpers ────────────────────────────────────────────────────────────

def cfg_int(section, key, fallback):
    try: return load_config().getint(section, key, fallback=fallback)
    except: return fallback

def cfg_bool(section, key, fallback=True):
    try: return load_config().getboolean(section, key, fallback=fallback)
    except: return fallback


def bootstrap_admin_credentials() -> tuple[str, str]:
    cfg = load_config()
    user = os.environ.get("APP_BOOTSTRAP_USER", cfg.get("auth", "username", fallback="admin"))
    password = os.environ.get("APP_BOOTSTRAP_PASSWORD", cfg.get("auth", "password", fallback="Admin2026!"))
    return user, password


def ensure_auth_ready() -> None:
    if not _initialized.is_set():
        init_db(retries=1, delay=0.0)


def authenticate_user(username: str, password: str) -> dict | None:
    ensure_auth_ready()
    conn = get_monitor_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id, username, password_hash, role, active
            FROM auth_users
            WHERE username = %s
            """,
            (username,),
        )
        row = cur.fetchone()
        cur.close()
        if not row or not row.get("active"):
            return None
        if not check_password_hash(row["password_hash"], password):
            return None
        return dict(row)
    finally:
        release_conn(conn)


def seed_default_user(conn) -> None:
    username, password = bootstrap_admin_credentials()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO auth_users (username, password_hash, role, active)
        VALUES (%s, %s, %s, TRUE)
        ON CONFLICT (username) DO NOTHING
        """,
        (username, generate_password_hash(password), "admin"),
    )
    cur.execute(
        """
        INSERT INTO auth_users (username, password_hash, role, active)
        VALUES (%s, %s, %s, TRUE)
        ON CONFLICT (username) DO NOTHING
        """,
        ("ariana", generate_password_hash("123456"), "viewer"),
    )
    conn.commit()
    cur.close()


def current_username() -> str | None:
    return session.get("user")


def current_role() -> str:
    return str(session.get("role", "viewer"))


def is_admin() -> bool:
    return current_role() == "admin"


def is_logged_in() -> bool:
    return bool(session.get("user"))


@app.before_request
def require_login():
    if request.path in AUTH_PUBLIC_PATHS:
        return None
    if request.path.startswith("/static/"):
        return None
    if request.path.startswith("/api/") and not is_logged_in():
        return {"error": "No autenticado"}, 401
    return None


def _default_file_roots(ds: dict) -> dict:
    tipo = (ds.get("tipo_db") or "postgresql").lower()
    cfg = load_config()
    if tipo == "postgresql":
        return {
            "config_dir": cfg.get("files", "postgresql_config_dir", fallback="/etc/postgresql/15/main"),
            "data_dir": cfg.get("files", "postgresql_data_dir", fallback="/var/lib/postgresql/15/main"),
            "log_dir": cfg.get("files", "postgresql_log_dir", fallback="/var/log/postgresql"),
            "backup_dir": cfg.get("files", "postgresql_backup_dir", fallback="/var/backups/postgresql"),
        }
    if tipo in ("mysql", "mariadb"):
        return {
            "config_dir": cfg.get("files", "mysql_config_dir", fallback="/etc/mysql"),
            "data_dir": cfg.get("files", "mysql_data_dir", fallback="/var/lib/mysql"),
            "log_dir": cfg.get("files", "mysql_log_dir", fallback="/var/log/mysql"),
            "backup_dir": cfg.get("files", "mysql_backup_dir", fallback="/var/backups/mysql"),
        }
    if tipo in ("sqlserver", "mssql"):
        return {
            "config_dir": cfg.get("files", "sqlserver_config_dir", fallback="C:/Program Files/Microsoft SQL Server"),
            "data_dir": cfg.get("files", "sqlserver_data_dir", fallback="C:/Program Files/Microsoft SQL Server/MSSQL/Data"),
            "log_dir": cfg.get("files", "sqlserver_log_dir", fallback="C:/Program Files/Microsoft SQL Server/MSSQL/Log"),
            "backup_dir": cfg.get("files", "sqlserver_backup_dir", fallback="C:/Backups/SQLServer"),
        }
    if tipo == "mongodb":
        return {
            "config_dir": cfg.get("files", "mongodb_config_dir", fallback="/etc"),
            "data_dir": cfg.get("files", "mongodb_data_dir", fallback="/var/lib/mongodb"),
            "log_dir": cfg.get("files", "mongodb_log_dir", fallback="/var/log/mongodb"),
            "backup_dir": cfg.get("files", "mongodb_backup_dir", fallback="/var/backups/mongodb"),
        }
    return {"config_dir": ".", "data_dir": ".", "log_dir": ".", "backup_dir": "."}


def _safe_stat_path(path: str) -> dict:
    try:
        p = Path(path)
        if not p.exists():
            return {"exists": False, "kind": "missing", "size_mb": 0.0, "modified_at": None, "entries": 0}
        if p.is_file():
            st = p.stat()
            return {
                "exists": True,
                "kind": "file",
                "size_mb": round(st.st_size / 1024 / 1024, 3),
                "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
                "entries": 1,
            }
        total_size = 0
        count = 0
        for child in p.rglob("*"):
            try:
                if child.is_file():
                    total_size += child.stat().st_size
                    count += 1
            except Exception:
                continue
        st = p.stat()
        return {
            "exists": True,
            "kind": "directory",
            "size_mb": round(total_size / 1024 / 1024, 3),
            "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
            "entries": count,
        }
    except Exception as exc:
        return {"exists": False, "kind": "error", "error": str(exc), "size_mb": 0.0, "modified_at": None, "entries": 0}


def get_file_profile_defs(tipo_db: str) -> list[dict]:
    return FILE_PROFILE_DEFS.get((tipo_db or "postgresql").lower(), FILE_PROFILE_DEFS["postgresql"])


def build_file_inventory(ds: dict, selected_types: list[str] | None = None) -> list[dict]:
    roots = _default_file_roots(ds)
    profiles = get_file_profile_defs(ds.get("tipo_db"))
    selected = {t.lower() for t in (selected_types or []) if t}
    if selected:
        profiles = [p for p in profiles if p["key"] in selected]

    inventory = []
    for profile in profiles:
        for raw_path in profile.get("paths", []):
            path = raw_path.format(**roots)
            stat_info = _safe_stat_path(path)
            inventory.append({
                "datasource_id": ds.get("id"),
                "datasource_name": ds.get("nombre"),
                "tipo_db": ds.get("tipo_db"),
                "file_type": profile["key"],
                "label": profile["label"],
                "description": profile["description"],
                "path": path,
                **stat_info,
            })
    return inventory

# ── DB Init ───────────────────────────────────────────────────────────────────


INIT_SQL = [
    # 1. Tablas nuevas (si no existen)
    """CREATE TABLE IF NOT EXISTS auth_users (
        id            SERIAL PRIMARY KEY,
        username      VARCHAR(100) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        role          VARCHAR(30)  NOT NULL DEFAULT 'user',
        active        BOOLEAN      NOT NULL DEFAULT TRUE,
        created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        last_login    TIMESTAMPTZ
    )""",
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
        owner_username VARCHAR(100) NOT NULL DEFAULT 'hashira',
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
    "ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS role VARCHAR(30) NOT NULL DEFAULT 'user'",
    "ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS last_login TIMESTAMPTZ",
    "ALTER TABLE datasources ADD COLUMN IF NOT EXISTS owner_username VARCHAR(100) NOT NULL DEFAULT 'hashira'",
    # 3. Índices
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_users_username ON auth_users (username)",
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
            seed_default_user(conn)
            bootstrap_owner = bootstrap_admin_credentials()[0]
            cur.execute("SELECT COUNT(*) FROM datasources")
            if cur.fetchone()[0] == 0:
                cfg = load_config()
                cur.execute("""
                    INSERT INTO datasources (nombre, tipo_db, host, puerto, usuario, password, database, owner_username)
                    VALUES (%s,'postgresql',%s,%s,%s,%s,%s,%s)
                """, (
                    "Monitor Principal (VM)",
                    cfg.get("postgresql","host",fallback="38.250.116.71"),
                    cfg.getint("postgresql","port",fallback=5432),
                    cfg.get("postgresql","user",fallback="monitor"),
                    cfg.get("postgresql","password",fallback=""),
                    cfg.get("postgresql","database",fallback="db_health_monitor"),
                    bootstrap_owner,
                ))
                conn.commit()
            cur.execute("UPDATE datasources SET owner_username = %s WHERE owner_username IS NULL OR owner_username = ''", (bootstrap_owner,))
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

    cur.execute("SELECT EXTRACT(EPOCH FROM (now() - pg_postmaster_start_time()))::bigint")
    uptime_seconds = int((cur.fetchone() or [0])[0] or 0)

    cur.close(); conn.close()

    db_mb   = round((db_size_bytes or 0)/1024/1024, 2)
    conn_pct= round(min(99.9, num_backends/max_conn*100), 2) if max_conn else 0

    # psutil
    cpu_pct = mem_pct = disk_used_pct = 0.0
    disk_free_gb = 0.0
    host_processes = 0
    try:
        if _PSUTIL:
            cpu_pct = psutil.cpu_percent(interval=None)
            mem_pct = psutil.virtual_memory().percent
            disk = psutil.disk_usage(str(BASE_DIR))
            disk_used_pct = disk.percent
            disk_free_gb = round(disk.free / 1024 / 1024 / 1024, 2)
            host_processes = len(psutil.pids())
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
        "disk_used_pct":    disk_used_pct,
        "disk_free_gb":     disk_free_gb,
        "host_processes":   host_processes,
        "uptime_seconds":   uptime_seconds,
        "status":           status,
    }


def collect_mysql_metrics(ds: dict) -> dict:
    conn = connect_to_datasource(ds, timeout=8)
    cur  = conn.cursor()

    # Variables globales de estado
    cur.execute("SHOW GLOBAL STATUS")
    status_vars = {row[0]: row[1] for row in cur.fetchall()}

    # max_connections
    cur.execute("SHOW GLOBAL VARIABLES LIKE 'max_connections'")
    max_conn = int((cur.fetchone() or [None, 100])[1])

    threads_connected = int(status_vars.get("Threads_connected", 0))
    threads_running   = int(status_vars.get("Threads_running",   0))
    slow_queries      = int(status_vars.get("Slow_queries",       0))
    uptime_seconds    = int(status_vars.get("Uptime", 0))

    # Procesos en espera
    cur.execute("""
        SELECT COUNT(*) FROM information_schema.processlist
        WHERE command != 'Sleep'
    """)
    threads_waiting = int((cur.fetchone() or [0])[0])

    # InnoDB cache hit ratio
    pool_reads    = int(status_vars.get("Innodb_buffer_pool_reads",         0))
    pool_requests = int(status_vars.get("Innodb_buffer_pool_read_requests", 1))
    if pool_requests > 0:
        cache_hit = round((1 - pool_reads / pool_requests) * 100, 2)
    else:
        cache_hit = 99.9
    cache_hit = max(0.0, min(100.0, cache_hit))

    # Tamaño de la base de datos en MB
    cur.execute("""
        SELECT COALESCE(SUM(data_length + index_length), 0) / 1024 / 1024
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
    """)
    db_mb = round(float((cur.fetchone() or [0])[0]), 2)

    cur.close(); conn.close()

    conn_pct = round(min(99.9, threads_connected / max_conn * 100), 2) if max_conn else 0

    # psutil
    cpu_pct = mem_pct = disk_used_pct = 0.0
    disk_free_gb = 0.0
    host_processes = 0
    try:
        if _PSUTIL:
            cpu_pct = psutil.cpu_percent(interval=None)
            mem_pct = psutil.virtual_memory().percent
            disk = psutil.disk_usage(str(BASE_DIR))
            disk_used_pct = disk.percent
            disk_free_gb = round(disk.free / 1024 / 1024 / 1024, 2)
            host_processes = len(psutil.pids())
    except Exception: pass

    status = "OK"
    if conn_pct >= 90 or cache_hit < 70: status = "CRITICAL"
    elif conn_pct >= 70 or cache_hit < 85: status = "WARNING"

    return {
        "datasource_id":    ds["id"],
        "tipo_db":          "mysql",
        "timestamp":        datetime.now().isoformat(),
        "max_connections":  max_conn,
        "threads_connected":threads_connected,
        "threads_running":  threads_running,
        "threads_waiting":  threads_waiting,
        "connection_pct":   conn_pct,
        "qps":              0.0,
        "slow_queries":     slow_queries,
        "cache_hit_ratio":  cache_hit,
        "db_size_mb":       db_mb,
        "cpu_pct":          cpu_pct,
        "mem_pct":          mem_pct,
        "disk_used_pct":    disk_used_pct,
        "disk_free_gb":     disk_free_gb,
        "host_processes":   host_processes,
        "uptime_seconds":   uptime_seconds,
        "status":           status,
    }


def collect_mariadb_metrics(ds: dict) -> dict:
    """MariaDB es compatible con MySQL — reutiliza la misma función."""
    m = collect_mysql_metrics(ds)
    m["tipo_db"] = "mariadb"
    return m


def collect_sqlserver_metrics(ds: dict) -> dict:
    conn = connect_to_datasource(ds, timeout=8)
    cur  = conn.cursor()

    # Max connections
    cur.execute("SELECT value_in_use FROM sys.configurations WHERE name = 'max connections'")
    max_conn_val = int((cur.fetchone() or [0])[0])
    max_conn = max_conn_val if max_conn_val > 0 else 32767

    # Conexiones activas y en espera
    cur.execute("""
        SELECT
            COUNT(*),
            SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END),
            SUM(CASE WHEN wait_type IS NOT NULL THEN 1 ELSE 0 END)
        FROM sys.dm_exec_sessions
        WHERE is_user_process = 1
    """)
    row = cur.fetchone() or (0, 0, 0)
    threads_connected = int(row[0] or 0)
    threads_running   = int(row[1] or 0)
    threads_waiting   = int(row[2] or 0)

    # Cache hit ratio (Buffer Manager)
    cur.execute("""
        SELECT
            MAX(CASE WHEN counter_name = 'Buffer cache hit ratio'
                     THEN CAST(cntr_value AS FLOAT) END),
            MAX(CASE WHEN counter_name = 'Buffer cache hit ratio base'
                     THEN CAST(cntr_value AS FLOAT) END)
        FROM sys.dm_os_performance_counters
        WHERE counter_name IN ('Buffer cache hit ratio', 'Buffer cache hit ratio base')
          AND object_name LIKE '%Buffer Manager%'
    """)
    row = cur.fetchone() or (0, 1)
    hit, base = (float(row[0] or 0)), (float(row[1] or 1))
    cache_hit = round((hit / base) * 100, 2) if base else 99.9
    cache_hit = max(0.0, min(100.0, cache_hit))

    # Tamaño de la base de datos actual en MB
    cur.execute("""
        SELECT CAST(SUM(size) * 8.0 / 1024 AS FLOAT)
        FROM sys.master_files
        WHERE database_id = DB_ID()
    """)
    db_mb = round(float((cur.fetchone() or [0])[0] or 0), 2)

    # Slow queries (queries con duración > 1s)
    cur.execute("""
        SELECT COUNT(*)
        FROM sys.dm_exec_requests r
        CROSS APPLY sys.dm_exec_sql_text(r.sql_handle)
        WHERE r.total_elapsed_time > 1000
    """)
    slow_queries = int((cur.fetchone() or [0])[0])

    cur.execute("SELECT DATEDIFF(SECOND, sqlserver_start_time, SYSDATETIME()) FROM sys.dm_os_sys_info")
    uptime_seconds = int((cur.fetchone() or [0])[0] or 0)

    cur.close(); conn.close()

    conn_pct = round(min(99.9, threads_connected / max_conn * 100), 2) if max_conn else 0

    cpu_pct = mem_pct = disk_used_pct = 0.0
    disk_free_gb = 0.0
    host_processes = 0
    try:
        if _PSUTIL:
            cpu_pct = psutil.cpu_percent(interval=None)
            mem_pct = psutil.virtual_memory().percent
            disk = psutil.disk_usage(str(BASE_DIR))
            disk_used_pct = disk.percent
            disk_free_gb = round(disk.free / 1024 / 1024 / 1024, 2)
            host_processes = len(psutil.pids())
    except Exception: pass

    status = "OK"
    if conn_pct >= 90 or cache_hit < 70: status = "CRITICAL"
    elif conn_pct >= 70 or cache_hit < 85: status = "WARNING"

    return {
        "datasource_id":    ds["id"],
        "tipo_db":          "sqlserver",
        "timestamp":        datetime.now().isoformat(),
        "max_connections":  max_conn,
        "threads_connected":threads_connected,
        "threads_running":  threads_running,
        "threads_waiting":  threads_waiting,
        "connection_pct":   conn_pct,
        "qps":              0.0,
        "slow_queries":     slow_queries,
        "cache_hit_ratio":  cache_hit,
        "db_size_mb":       db_mb,
        "cpu_pct":          cpu_pct,
        "mem_pct":          mem_pct,
        "disk_used_pct":    disk_used_pct,
        "disk_free_gb":     disk_free_gb,
        "host_processes":   host_processes,
        "uptime_seconds":   uptime_seconds,
        "status":           status,
    }


def collect_mongodb_metrics(ds: dict) -> dict:
    from db_connection import _MONGO_OK
    if not _MONGO_OK:
        raise RuntimeError("Driver MongoDB no instalado. Añade pymongo a requirements.txt.")
    import pymongo as _pymongo

    uri_auth = ""
    if ds.get("usuario"):
        from urllib.parse import quote_plus as _qp
        uri_auth = f"{_qp(ds['usuario'])}:{_qp(ds['password'])}@"
    uri = f"mongodb://{uri_auth}{ds['host']}:{ds['puerto']}/{ds['database']}"
    client = _pymongo.MongoClient(
        uri,
        serverSelectionTimeoutMS=8000,
        connectTimeoutMS=8000,
        socketTimeoutMS=8000,
    )
    try:
        srv = client.admin.command("serverStatus")

        conns       = srv.get("connections", {})
        current     = int(conns.get("current",   0))
        available   = int(conns.get("available", 1000))
        max_conn    = current + available

        # WiredTiger cache hit ratio
        wt          = srv.get("wiredTiger", {}).get("cache", {})
        reads_into  = int(wt.get("pages read into cache",        1))
        reads_req   = int(wt.get("pages requested from the cache", 1))
        cache_hit   = round((1 - reads_into / max(reads_req, 1)) * 100, 2)
        cache_hit   = max(0.0, min(100.0, cache_hit))

        # Tamaño de la BD
        db_stats    = client[ds["database"]].command("dbStats")
        db_mb       = round(db_stats.get("dataSize", 0) / 1024 / 1024, 2)

        # Operaciones activas
        cur_op      = client.admin.command("currentOp")
        inprog      = cur_op.get("inprog", [])
        running     = sum(1 for op in inprog if not op.get("waitingForLock", False))
        waiting     = sum(1 for op in inprog if     op.get("waitingForLock", False))

        conn_pct = round(min(99.9, current / max_conn * 100), 2) if max_conn else 0

        cpu_pct = mem_pct = disk_used_pct = 0.0
        disk_free_gb = 0.0
        host_processes = 0
        try:
            if _PSUTIL:
                cpu_pct = psutil.cpu_percent(interval=None)
                mem_pct = psutil.virtual_memory().percent
            disk = psutil.disk_usage(str(BASE_DIR))
            disk_used_pct = disk.percent
            disk_free_gb = round(disk.free / 1024 / 1024 / 1024, 2)
            host_processes = len(psutil.pids())
        except Exception: pass

        status = "OK"
        if conn_pct >= 90 or cache_hit < 70: status = "CRITICAL"
        elif conn_pct >= 70 or cache_hit < 85: status = "WARNING"

        return {
            "datasource_id":    ds["id"],
            "tipo_db":          "mongodb",
            "timestamp":        datetime.now().isoformat(),
            "max_connections":  max_conn,
            "threads_connected":current,
            "threads_running":  running,
            "threads_waiting":  waiting,
            "connection_pct":   conn_pct,
            "qps":              0.0,
            "slow_queries":     0,
            "cache_hit_ratio":  cache_hit,
            "db_size_mb":       db_mb,
            "cpu_pct":          cpu_pct,
            "mem_pct":          mem_pct,
            "disk_used_pct":    disk_used_pct,
            "disk_free_gb":     disk_free_gb,
            "host_processes":   host_processes,
            "uptime_seconds":   int(srv.get("uptime", 0)),
            "status":           status,
        }
    finally:
        client.close()


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
                    elif tipo == "mysql":
                        m = collect_mysql_metrics(ds)
                    elif tipo == "mariadb":
                        m = collect_mariadb_metrics(ds)
                    elif tipo in ("sqlserver", "mssql"):
                        m = collect_sqlserver_metrics(ds)
                    elif tipo == "mongodb":
                        m = collect_mongodb_metrics(ds)
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

        interval = cfg_int("monitor", "refresh_interval", 10)
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
    username = current_username()
    if not username:
        return None
    conn = get_monitor_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if is_admin():
            cur.execute("SELECT * FROM datasources WHERE id=%s", (ds_id,))
        else:
            cur.execute("SELECT * FROM datasources WHERE id=%s AND owner_username=%s", (ds_id, username))
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None
    finally:
        release_conn(conn)


def get_owned_datasource_ids() -> set[int]:
    username = current_username()
    if not username:
        return set()
    conn = get_monitor_conn()
    try:
        cur = conn.cursor()
        if is_admin():
            cur.execute("SELECT id FROM datasources")
        else:
            cur.execute("SELECT id FROM datasources WHERE owner_username=%s", (username,))
        rows = {row[0] for row in cur.fetchall()}
        cur.close()
        return rows
    finally:
        release_conn(conn)

# ── Rutas ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index(): return render_template("index.html")

@app.route("/api/health")
def api_health():
    started = _initialized.is_set()
    return {"status": "ok" if started else "starting"}, 200


@app.route("/api/me")
def api_me():
    if not is_logged_in():
        return {"authenticated": False}, 401
    return {"authenticated": True, "user": session.get("user"), "role": session.get("role", "viewer")}


@app.route("/api/login", methods=["POST"])
def api_login():
    payload = request.get_json(silent=True) or request.form or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    user = authenticate_user(username, password)
    if not user:
        return {"ok": False, "error": "Usuario o contraseña inválidos"}, 401

    ensure_auth_ready()
    conn = get_monitor_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE auth_users SET last_login = NOW() WHERE id = %s", (user["id"],))
        conn.commit()
        cur.close()
    finally:
        release_conn(conn)

    session["user"] = user["username"]
    session["role"] = user.get("role", "user")
    return {"ok": True, "user": user["username"], "role": user.get("role", "user")}


@app.route("/api/register", methods=["POST"])
def api_register():
    payload = request.get_json(silent=True) or request.form or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    confirm = str(payload.get("confirm_password", payload.get("confirm", "")))

    if len(username) < 3:
        return {"ok": False, "error": "El usuario debe tener al menos 3 caracteres"}, 400
    if len(password) < 6:
        return {"ok": False, "error": "La contraseña debe tener al menos 6 caracteres"}, 400
    if password != confirm:
        return {"ok": False, "error": "Las contraseñas no coinciden"}, 400

    ensure_auth_ready()
    conn = get_monitor_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id FROM auth_users WHERE username = %s", (username,))
        if cur.fetchone():
            cur.close()
            return {"ok": False, "error": "El usuario ya existe"}, 409

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO auth_users (username, password_hash, role, active)
            VALUES (%s, %s, %s, TRUE)
            RETURNING id
            """,
            (username, generate_password_hash(password), "user"),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return {"ok": True, "id": new_id, "user": username}
    finally:
        release_conn(conn)


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return {"ok": True}


@app.route("/api/file-types")
def api_file_types():
    ds_id = request.args.get("datasource_id", type=int)
    if ds_id:
        ds = get_ds_by_id(ds_id)
        if not ds:
            return {"error": "Datasource no encontrado"}, 404
        defs = get_file_profile_defs(ds.get("tipo_db"))
        return jsonify(defs)
    return jsonify({k: v for k, v in FILE_PROFILE_DEFS.items()})


@app.route("/api/files")
def api_files():
    ds_id = request.args.get("datasource_id", type=int)
    if not ds_id:
        return {"error": "datasource_id requerido"}, 400
    ds = get_ds_by_id(ds_id)
    if not ds:
        return {"error": "Datasource no encontrado"}, 404
    types_param = request.args.get("types", "")
    selected_types = [t.strip() for t in types_param.split(",") if t.strip()]
    files = build_file_inventory(ds, selected_types if selected_types else None)
    return jsonify({
        "datasource": {
            "id": ds["id"],
            "nombre": ds.get("nombre"),
            "tipo_db": ds.get("tipo_db"),
            "host": ds.get("host"),
            "puerto": ds.get("puerto"),
            "database": ds.get("database"),
        },
        "selected_types": selected_types,
        "files": files,
        "total": len(files),
    })

# ── Datasources CRUD ──────────────────────────────────────────────────────────

@app.route("/api/datasources", methods=["GET"])
def api_ds_list():
    username = current_username()
    if not username:
        return jsonify([])
    conn = get_monitor_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if is_admin():
            cur.execute(
                """
                SELECT id,nombre,tipo_db,host,puerto,usuario,database,activa,created_at,owner_username
                FROM datasources
                ORDER BY id
                """
            )
        else:
            cur.execute(
                """
                SELECT id,nombre,tipo_db,host,puerto,usuario,database,activa,created_at,owner_username
                FROM datasources
                WHERE owner_username=%s
                ORDER BY id
                """,
                (username,),
            )
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            if hasattr(r.get("created_at"), "isoformat"):
                r["created_at"] = r["created_at"].isoformat()
            raw_active = r.get("activa")
            if isinstance(raw_active, str):
                r["activa"] = raw_active.strip().lower() in {"1", "true", "t", "yes", "y", "si", "sí"}
            else:
                r["activa"] = bool(raw_active)
            ds_id = r["id"]
            with _cache_lock:
                cached = _cache.get(ds_id, {})
            if not r["activa"]:
                r["status"] = "disabled"
            elif cached.get("metrics"):
                r["status"] = cached.get("metrics", {}).get("status", "unknown")
            elif cached.get("error"):
                r["status"] = "error"
            else:
                r["status"] = "unknown"
            r["last_error"] = cached.get("error")
            r["last_ts"]    = cached.get("ts")
        return jsonify(rows)
    finally:
        release_conn(conn)

@app.route("/api/datasources", methods=["POST"])
def api_ds_create():
    username = current_username()
    if not username:
        return {"error": "No autenticado"}, 401
    d = request.json or {}
    required = ["nombre","tipo_db","host","puerto","usuario","database"]
    missing = [f for f in required if not d.get(f)]
    if missing:
        return {"error": f"Faltan campos: {', '.join(missing)}"}, 400
    conn = get_monitor_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO datasources (nombre,tipo_db,host,puerto,usuario,password,database,activa,owner_username)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (d["nombre"], d["tipo_db"], d["host"], int(d["puerto"]),
              d["usuario"], d.get("password",""), d["database"],
              d.get("activa", True), username))
        new_id = cur.fetchone()[0]
        conn.commit(); cur.close()
        return {"id": new_id, "message": "Datasource creado."}, 201
    finally:
        release_conn(conn)


@app.route("/api/admin/overview")
def api_admin_overview():
    if not is_admin():
        return {"error": "No autorizado"}, 403
    conn = get_monitor_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, username, role, active, created_at, last_login FROM auth_users ORDER BY id")
        users = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT id, nombre, tipo_db, host, puerto, usuario, database, activa, owner_username, created_at FROM datasources ORDER BY id")
        datasources = [dict(row) for row in cur.fetchall()]
        cur.close()
        for row in users + datasources:
            for key in ("created_at", "last_login"):
                value = row.get(key)
                if hasattr(value, "isoformat"):
                    row[key] = value.isoformat()
        return jsonify({
            "counts": {"users": len(users), "datasources": len(datasources)},
            "users": users,
            "datasources": datasources,
        })
    finally:
        release_conn(conn)

@app.route("/api/datasources/<int:ds_id>", methods=["PUT"])
def api_ds_update(ds_id):
    if not get_ds_by_id(ds_id):
        return {"error": "No encontrado."}, 404
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
    if not get_ds_by_id(ds_id):
        return {"error": "No encontrado."}, 404
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
    return {
        "ok": ok,
        "latency_ms": ms,
        "error": err,
        "datasource": {
            "id": ds.get("id"),
            "nombre": ds.get("nombre"),
            "tipo_db": ds.get("tipo_db"),
            "host": ds.get("host"),
            "puerto": ds.get("puerto"),
            "database": ds.get("database"),
            "activa": bool(ds.get("activa")),
        },
    }

# ── Métricas y resumen ────────────────────────────────────────────────────────

@app.route("/api/metrics")
def api_metrics():
    ds_id = request.args.get("datasource_id", type=int)
    with _cache_lock:
        snap = dict(_cache)
    owned_ids = get_owned_datasource_ids()
    if ds_id:
        if ds_id not in owned_ids:
            return {"error": "Datasource no encontrado"}, 404
        entry = snap.get(ds_id)
        if not entry:
            return {"status": "loading"}, 202
        return jsonify(entry)
    # todos
    return jsonify({ds_id: value for ds_id, value in snap.items() if ds_id in owned_ids})

@app.route("/api/summary/global")
def api_summary_global():
    with _cache_lock:
        snap = dict(_cache)
    owned_ids = get_owned_datasource_ids()
    snap = {ds_id: value for ds_id, value in snap.items() if ds_id in owned_ids}
    total  = len(snap)
    online = sum(1 for v in snap.values() if not v.get("error") and v.get("metrics"))
    statuses = [((v.get("metrics") or {}).get("status")) for v in snap.values() if v.get("metrics")]
    statuses = [status for status in statuses if status]
    global_st = "CRITICAL" if "CRITICAL" in statuses else "WARNING" if "WARNING" in statuses else "OK"
    return jsonify({
        "total_datasources": total,
        "online": online,
        "offline": total - online,
        "global_status": global_st,
        "datasources": {
            ds_id: {"status": (v.get("metrics") or {}).get("status","unknown"),
                    "error":  v.get("error"), "ts": v.get("ts")}
            for ds_id, v in snap.items()
        }
    })

@app.route("/api/summary/<int:ds_id>")
def api_summary_ds(ds_id):
    if ds_id not in get_owned_datasource_ids():
        return {"status": "loading"}, 202
    with _cache_lock:
        entry = _cache.get(ds_id)
    if not entry:
        return {"status": "loading"}, 202
    return jsonify(entry)

@app.route("/api/history")
def api_history():
    ds_id = request.args.get("datasource_id", type=int)
    owned_ids = get_owned_datasource_ids()
    conn = get_monitor_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if ds_id:
            if ds_id not in owned_ids:
                return {"error": "Datasource no encontrado"}, 404
            cur.execute("""
                SELECT * FROM health_snapshots WHERE datasource_id=%s
                ORDER BY id DESC LIMIT 100
            """, (ds_id,))
        else:
            if owned_ids:
                cur.execute("SELECT * FROM health_snapshots WHERE datasource_id = ANY(%s) ORDER BY id DESC LIMIT 200", (list(owned_ids),))
            else:
                cur.execute("SELECT * FROM health_snapshots WHERE 1=0")
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
    owned_ids = get_owned_datasource_ids()
    conn = get_monitor_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if ds_id:
            if ds_id not in owned_ids:
                return {"error": "Datasource no encontrado"}, 404
            cur.execute("""
                SELECT * FROM alert_log WHERE datasource_id=%s
                ORDER BY id DESC LIMIT 50
            """, (ds_id,))
        else:
            if owned_ids:
                cur.execute("SELECT * FROM alert_log WHERE datasource_id = ANY(%s) ORDER BY id DESC LIMIT 200", (list(owned_ids),))
            else:
                cur.execute("SELECT * FROM alert_log WHERE 1=0")
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
