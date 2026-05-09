#!/usr/bin/env python3
"""
db_connection.py — Capa de conexión para el Monitor de Salud.
Soporta: PostgreSQL, MySQL, MariaDB, SQL Server, MongoDB.
"""

import os
import time
import logging
import configparser
from pathlib import Path
from urllib.parse import quote_plus

# ── PostgreSQL (requerido) ────────────────────────────────────────────────────
import psycopg2
import psycopg2.extras
import psycopg2.pool

# ── MySQL (opcional) ──────────────────────────────────────────────────────────
try:
    import mysql.connector
    _MYSQL_OK = True
except ImportError:
    mysql = None
    _MYSQL_OK = False

# ── SQL Server (opcional) ─────────────────────────────────────────────────────
try:
    import pymssql
    _MSSQL_OK = True
except ImportError:
    pymssql = None
    _MSSQL_OK = False

# ── MongoDB (opcional) ───────────────────────────────────────────────────────
try:
    import pymongo
    _MONGO_OK = True
except ImportError:
    pymongo = None
    _MONGO_OK = False

log = logging.getLogger(__name__)
CONFIG_FILE = Path(__file__).parent / "config.ini"

# Pool global para la BD del monitor
_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_pool_lock = __import__("threading").Lock()


# ── Configuración ─────────────────────────────────────────────────────────────

def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE, encoding="utf-8")
    return cfg


def build_dsn() -> str:
    """Construye el DSN del monitor. Env var DATABASE_URL tiene prioridad."""
    env = os.environ.get("DATABASE_URL", "").strip()
    if env:
        return env
    cfg = load_config()
    if not cfg.has_section("postgresql"):
        raise RuntimeError("Sin configuración PostgreSQL (falta DATABASE_URL o config.ini).")
    h = cfg.get("postgresql", "host", fallback="localhost")
    p = cfg.get("postgresql", "port", fallback="5432")
    u = cfg.get("postgresql", "user", fallback="postgres")
    pw = cfg.get("postgresql", "password", fallback="")
    db = cfg.get("postgresql", "database", fallback="db_health_monitor")
    return f"postgresql://{quote_plus(u)}:{quote_plus(pw)}@{h}:{p}/{db}"


# ── Pool del monitor ──────────────────────────────────────────────────────────

def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            dsn = build_dsn()
            cfg = load_config()
            timeout = cfg.getint("postgresql", "connect_timeout", fallback=10)
            _pool = psycopg2.pool.ThreadedConnectionPool(
                1, 10, dsn,
                connect_timeout=timeout,
            )
            log.info("Pool PostgreSQL creado.")
    return _pool


def get_monitor_conn():
    """Obtiene conexión del pool del monitor. Llamar .putconn() al terminar."""
    return get_pool().getconn()


def release_conn(conn):
    """Devuelve conexión al pool."""
    try:
        get_pool().putconn(conn)
    except Exception:
        pass


# ── Conexiones dinámicas (multi-datasource) ───────────────────────────────────

def connect_to_datasource(ds: dict, timeout: int = 10):
    """
    Abre una conexión directa (no pooled) al datasource indicado.
    ds debe tener: tipo_db, host, puerto, usuario, password, database
    """
    tipo = (ds.get("tipo_db") or "postgresql").lower()

    if tipo == "postgresql":
        return psycopg2.connect(
            host=ds["host"],
            port=int(ds["puerto"]),
            user=ds["usuario"],
            password=ds["password"],
            database=ds["database"],
            connect_timeout=timeout,
        )

    if tipo in ("mysql", "mariadb"):
        if not _MYSQL_OK:
            raise RuntimeError("Driver MySQL no instalado. Añade mysql-connector-python a requirements.txt.")
        return mysql.connector.connect(
            host=ds["host"],
            port=int(ds["puerto"]),
            user=ds["usuario"],
            password=ds["password"],
            database=ds["database"],
            connection_timeout=timeout,
        )

    if tipo in ("sqlserver", "mssql"):
        if not _MSSQL_OK:
            raise RuntimeError("Driver SQL Server no instalado. Añade pymssql a requirements.txt.")
        return pymssql.connect(
            server=ds["host"],
            port=int(ds["puerto"]),
            user=ds["usuario"],
            password=ds["password"],
            database=ds["database"],
            timeout=timeout,
            login_timeout=timeout,
        )

    if tipo == "mongodb":
        if not _MONGO_OK:
            raise RuntimeError("Driver MongoDB no instalado. Añade pymongo a requirements.txt.")
        # Devuelve MongoClient — NO es DB-API; usar solo en collect_mongodb_metrics
        uri_auth = ""
        if ds.get("usuario"):
            from urllib.parse import quote_plus as _qp
            uri_auth = f"{_qp(ds['usuario'])}:{_qp(ds['password'])}@"
        uri = f"mongodb://{uri_auth}{ds['host']}:{ds['puerto']}/{ds['database']}"
        return pymongo.MongoClient(
            uri,
            serverSelectionTimeoutMS=timeout * 1000,
            connectTimeoutMS=timeout * 1000,
            socketTimeoutMS=timeout * 1000,
        )

    raise ValueError(f"tipo_db no soportado: {tipo}")


def test_datasource(ds: dict) -> tuple[bool, float | None, str | None]:
    """
    Prueba la conexión a un datasource.
    Retorna (ok: bool, latencia_ms: float|None, error: str|None)
    """
    tipo = (ds.get("tipo_db") or "postgresql").lower()
    t0 = time.perf_counter()
    try:
        if tipo == "mongodb":
            client = connect_to_datasource(ds, timeout=5)
            client.admin.command("ping")
            client.close()
        else:
            conn = connect_to_datasource(ds, timeout=5)
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchall()  # requerido por mysql-connector para liberar el resultado
            cur.close()
            conn.close()
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return True, ms, None
    except Exception as exc:
        return False, None, str(exc)
