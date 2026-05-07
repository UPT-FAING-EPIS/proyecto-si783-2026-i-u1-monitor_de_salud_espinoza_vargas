#!/usr/bin/env python3
"""Conexion centralizada para MySQL/PostgreSQL."""

import os
import logging
import configparser
from pathlib import Path

# ── MySQL driver (opcional) ──────────────────────────────────────────────────
try:
    import mysql.connector
    from mysql.connector import pooling as mysql_pooling
    _MYSQL_OK = True
except ImportError:
    mysql = None
    mysql_pooling = None
    _MYSQL_OK = False

# ── PostgreSQL driver — psycopg2 preferido, psycopg3 como fallback ────────────
try:
    import psycopg2
    import psycopg2.pool
    import psycopg2.extras
    _PG_DRIVER = "psycopg2"
    _PG_OK = True
except ImportError:
    psycopg2 = None
    try:
        import psycopg
        from psycopg.rows import dict_row as _pg3_dict_row
        _PG_DRIVER = "psycopg3"
        _PG_OK = True
    except ImportError:
        _PG_DRIVER = None
        _PG_OK = False

logger = logging.getLogger(__name__)
CONFIG_FILE = Path(__file__).parent / "config.ini"
_pool = None
_db_type = "mysql"


def load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_FILE, encoding="utf-8")
    return cfg


def get_mysql_config() -> dict:
    cfg = load_config()
    return {
        "host": os.environ.get("MYSQL_HOST", cfg.get("mysql", "host", fallback="localhost")),
        "port": int(os.environ.get("MYSQL_PORT", cfg.getint("mysql", "port", fallback=3306))),
        "user": os.environ.get("MYSQL_USER", cfg.get("mysql", "user", fallback="root")),
        "password": os.environ.get("MYSQL_PASSWORD", cfg.get("mysql", "password", fallback="")),
        "database": os.environ.get("MYSQL_DB", cfg.get("mysql", "database", fallback="db_health_monitor")),
        "connection_timeout": int(os.environ.get("MYSQL_TIMEOUT", cfg.getint("mysql", "connect_timeout", fallback=30))),
        "autocommit": True,
    }


def get_postgres_config() -> dict:
    cfg = load_config()
    return {
        "host": os.environ.get("PGHOST", cfg.get("postgresql", "host", fallback="localhost")),
        "port": int(os.environ.get("PGPORT", cfg.getint("postgresql", "port", fallback=5432))),
        "user": os.environ.get("PGUSER", cfg.get("postgresql", "user", fallback="postgres")),
        "password": os.environ.get("PGPASSWORD", cfg.get("postgresql", "password", fallback="")),
        "database": os.environ.get("PGDATABASE", cfg.get("postgresql", "database", fallback="db_health_monitor")),
        "connect_timeout": int(os.environ.get("PGCONNECT_TIMEOUT", cfg.getint("postgresql", "connect_timeout", fallback=30))),
    }


def init_pool(pool_name: str = "health_monitor_pool", pool_size: int = 5):
    global _pool, _db_type
    cfg = load_config()
    primary = cfg.get("database", "primary_db", fallback="mysql").lower()

    if primary == "postgresql":
        if not _PG_OK:
            raise RuntimeError("Ningún driver PostgreSQL disponible (psycopg2/psycopg3).")
        pg = get_postgres_config()
        _db_type = "postgresql"
        if _PG_DRIVER == "psycopg2":
            _pool = psycopg2.pool.ThreadedConnectionPool(
                1,
                pool_size,
                user=pg["user"],
                password=pg["password"],
                host=pg["host"],
                port=pg["port"],
                database=pg["database"],
                connect_timeout=pg["connect_timeout"],
            )
        else:
            _pool = {"config": pg}
        return True

    if not _MYSQL_OK:
        raise RuntimeError("Driver MySQL no disponible (mysql-connector-python).")
    my = get_mysql_config()
    _db_type = "mysql"
    _pool = mysql_pooling.MySQLConnectionPool(
        pool_name=pool_name,
        pool_size=pool_size,
        pool_reset_session=True,
        **my,
    )
    return True


def get_connection():
    global _pool
    if _pool is None:
        init_pool()

    if _db_type == "postgresql":
        if _PG_DRIVER == "psycopg2":
            return _pool.getconn()
        pg = _pool["config"]
        import psycopg
        return psycopg.connect(
            host=pg["host"],
            port=pg["port"],
            user=pg["user"],
            password=pg["password"],
            dbname=pg["database"],
            connect_timeout=pg["connect_timeout"],
        )
    return _pool.get_connection()


def execute_query(sql: str, params=None) -> list:
    conn = get_connection()
    if _db_type == "postgresql":
        if _PG_DRIVER == "psycopg2":
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params or ())
            rows = cur.fetchall()
            cur.close()
            _pool.putconn(conn)
            return rows
        cur = conn.cursor(row_factory=_pg3_dict_row)
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows

    cur = conn.cursor(dictionary=True)
    cur.execute(sql, params or ())
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def execute_update(sql: str, params=None) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(sql, params or ())
    affected = cur.rowcount
    conn.commit()
    cur.close()
    if _db_type == "postgresql" and _PG_DRIVER == "psycopg2":
        _pool.putconn(conn)
    else:
        conn.close()
    return affected


def execute_batch(sql: str, data_list: list) -> int:
    conn = get_connection()
    cur = conn.cursor()
    if _db_type == "postgresql" and _PG_DRIVER == "psycopg2":
        psycopg2.extras.execute_batch(cur, sql, data_list)
    else:
        cur.executemany(sql, data_list)
    affected = cur.rowcount
    conn.commit()
    cur.close()
    if _db_type == "postgresql" and _PG_DRIVER == "psycopg2":
        _pool.putconn(conn)
    else:
        conn.close()
    return affected


def test_connection() -> bool:
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        ok = cur.fetchone() is not None
        cur.close()
        if _db_type == "postgresql" and _PG_DRIVER == "psycopg2":
            _pool.putconn(conn)
        else:
            conn.close()
        return ok
    except Exception as exc:
        logger.error("Error de conexion: %s", exc)
        return False


def get_server_info() -> dict:
    if _db_type == "postgresql":
        rows = execute_query("SELECT version() AS version, current_database() AS database")
    else:
        rows = execute_query("SELECT VERSION() as version, DATABASE() as database")
    return rows[0] if rows else {}
