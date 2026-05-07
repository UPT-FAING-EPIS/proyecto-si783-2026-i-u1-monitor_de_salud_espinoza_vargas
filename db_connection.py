#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║    CONEXIÓN CENTRALIZADA A MYSQL - MONITOR DE SALUD DB       ║
║    Gestión de conexiones, pool y utilidades                  ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import sys
import logging
import configparser
from pathlib import Path
from datetime import datetime

try:
    import mysql.connector
    from mysql.connector import Error as MySQLError
    from mysql.connector import pooling
    _MYSQL_OK = True
except ImportError:
    print("[ERROR] Módulo 'mysql-connector-python' no instalado.")
    print("        Ejecuta: pip install mysql-connector-python")
    sys.exit(1)

try:
    import psycopg2
    import psycopg2.pool
    import psycopg2.extras
    _PG_OK = True
except ImportError:
    _PG_OK = False

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent / "config.ini"
_pool = None
_db_type = 'mysql'


def load_config() -> configparser.ConfigParser:
    """Carga la configuración de config.ini."""
    cfg = configparser.ConfigParser()
    if not CONFIG_FILE.exists():
        logger.error(f"No se encontró config.ini en {CONFIG_FILE}")
        raise FileNotFoundError(f"config.ini no encontrado en {CONFIG_FILE}")
    cfg.read(CONFIG_FILE, encoding="utf-8")
    return cfg


def get_mysql_config() -> dict:
    """Obtiene la configuración de MySQL desde config.ini o variables de entorno."""
    cfg = load_config()
    
    config = {
        "host": os.environ.get("MYSQL_HOST", cfg.get("mysql", "host", fallback="localhost")),
        "port": int(os.environ.get("MYSQL_PORT", cfg.getint("mysql", "port", fallback=3306))),
        "user": os.environ.get("MYSQL_USER", cfg.get("mysql", "user", fallback="root")),
        "password": os.environ.get("MYSQL_PASSWORD", cfg.get("mysql", "password", fallback="")),
        "database": os.environ.get("MYSQL_DB", cfg.get("mysql", "database", fallback="db_health_monitor")),
        "connection_timeout": int(os.environ.get("MYSQL_TIMEOUT", cfg.getint("mysql", "connect_timeout", fallback=30))),
        "autocommit": True,
        "raise_on_warnings": False,
    }
    
    return config


def get_postgres_config() -> dict:
    cfg = load_config()
    config = {
        "host": os.environ.get("PGHOST", cfg.get("postgresql", "host", fallback="localhost")),
        "port": int(os.environ.get("PGPORT", cfg.getint("postgresql", "port", fallback=5432))),
        "user": os.environ.get("PGUSER", cfg.get("postgresql", "user", fallback="postgres")),
        "password": os.environ.get("PGPASSWORD", cfg.get("postgresql", "password", fallback="")),
        "database": os.environ.get("PGDATABASE", cfg.get("postgresql", "database", fallback="db_health_monitor")),
        "connect_timeout": int(os.environ.get("PGCONNECT_TIMEOUT", cfg.getint("postgresql", "connect_timeout", fallback=30)))
    }
    return config


def init_pool(pool_name: str = "health_monitor_pool", pool_size: int = 5):
    """Inicializa el pool de conexiones MySQL."""
    global _pool
    
    global _db_type
    cfg = load_config()
    primary = cfg.get("database", "primary_db", fallback="mysql").lower()
    if primary == "postgresql":
        if not _PG_OK:
            logger.error("psycopg2 no está instalado. Instala psycopg2-binary")
            return False
        try:
            pg = get_postgres_config()
            logger.info(f"Inicializando pool PostgreSQL: {pg['host']}:{pg['port']}/{pg['database']}")
            # Use threaded pool: minconn=1, maxconn=pool_size
            _pool = psycopg2.pool.ThreadedConnectionPool(1, pool_size, user=pg['user'], password=pg['password'], host=pg['host'], port=pg['port'], database=pg['database'], connect_timeout=pg['connect_timeout'])
            _db_type = 'postgresql'
            logger.info("Pool de conexiones PostgreSQL inicializado correctamente")
            return True
        except Exception as e:
            logger.error(f"Error al inicializar pool PostgreSQL: {e}")
            return False
    else:
        try:
            config = get_mysql_config()
            logger.info(f"Inicializando pool MySQL: {config['host']}:{config['port']}/{config['database']}")
            _pool = pooling.MySQLConnectionPool(
                pool_name=pool_name,
                pool_size=pool_size,
                pool_reset_session=True,
                **config
            )
            _db_type = 'mysql'
            logger.info("Pool de conexiones MySQL inicializado correctamente")
            return True
        except MySQLError as e:
            logger.error(f"Error al inicializar pool MySQL: {e}")
            return False


def get_connection():
    """Obtiene una conexión del pool. Si no existe el pool, crea uno."""
    global _pool
    
    if _pool is None:
        if not init_pool():
            raise RuntimeError("No se pudo inicializar el pool de conexiones")
    
    try:
        if _db_type == 'postgresql':
            return _pool.getconn()
        else:
            return _pool.get_connection()
    except Exception as e:
        logger.error(f"Error al obtener conexión del pool: {e}")
        raise


def execute_query(sql: str, params=None) -> list:
    """Ejecuta una query SELECT y devuelve los resultados."""
    conn = None
    try:
        conn = get_connection()
        if _db_type == 'postgresql':
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params or ())
            results = cur.fetchall()
            cur.close()
            # return connection to pool
            _pool.putconn(conn)
            return results
        else:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, params or ())
            results = cursor.fetchall()
            cursor.close()
            conn.close()
            return results
    except Exception as e:
        logger.error(f"Error al ejecutar query: {e}")
        raise


def execute_update(sql: str, params=None) -> int:
    """Ejecuta una query INSERT, UPDATE o DELETE y devuelve el número de filas afectadas."""
    conn = None
    try:
        conn = get_connection()
        if _db_type == 'postgresql':
            cur = conn.cursor()
            cur.execute(sql, params or ())
            affected = cur.rowcount
            conn.commit()
            cur.close()
            _pool.putconn(conn)
            return affected
        else:
            cursor = conn.cursor()
            cursor.execute(sql, params or ())
            affected_rows = cursor.rowcount
            cursor.close()
            conn.commit()
            conn.close()
            return affected_rows
    except Exception as e:
        logger.error(f"Error al ejecutar update: {e}")
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        raise


def execute_batch(sql: str, data_list: list) -> int:
    """Ejecuta múltiples queries en batch. Útil para inserts masivos."""
    conn = None
    try:
        conn = get_connection()
        if _db_type == 'postgresql':
            cur = conn.cursor()
            psycopg2.extras.execute_batch(cur, sql, data_list)
            affected = cur.rowcount
            conn.commit()
            cur.close()
            _pool.putconn(conn)
            logger.info(f"Batch ejecutado: {affected} filas afectadas")
            return affected
        else:
            cursor = conn.cursor()
            cursor.executemany(sql, data_list)
            affected_rows = cursor.rowcount
            cursor.close()
            conn.commit()
            conn.close()
            logger.info(f"Batch ejecutado: {affected_rows} filas afectadas")
            return affected_rows
    except Exception as e:
        logger.error(f"Error al ejecutar batch: {e}")
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        raise


def test_connection() -> bool:
    """Prueba la conexión a MySQL."""
    try:
        conn = get_connection()
        if _db_type == 'postgresql':
            cur = conn.cursor()
            cur.execute("SELECT 1")
            result = cur.fetchone()
            cur.close()
            _pool.putconn(conn)
        else:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            cursor.close()
            conn.close()

        if result:
            logger.info(f"✓ Conexión a {_db_type} exitosa")
            return True
        return False
    except Exception as e:
        logger.error(f"✘ Error en conexión a {_db_type}: {e}")
        return False


def get_server_info() -> dict:
    """Obtiene información del servidor MySQL."""
    try:
        results = execute_query("SELECT VERSION() as version, @@hostname as hostname, DATABASE() as database")
        if results:
            return results[0]
        return {}
    except Exception as e:
        logger.error(f"Error al obtener info del servidor: {e}")
        return {}


def get_connection_info() -> dict:
    """Obtiene información de conexiones actuales."""
    try:
        query = """
        SELECT 
            @@max_connections as max_connections,
            (SELECT COUNT(*) FROM information_schema.PROCESSLIST) as current_connections,
            @@version as mysql_version
        """
        results = execute_query(query)
        if results:
            return results[0]
        return {}
    except Exception as e:
        logger.error(f"Error al obtener info de conexiones: {e}")
        return {}


def get_active_processes() -> list:
    """Obtiene la lista de procesos activos en MySQL."""
    try:
        query = """
        SELECT 
            ID,
            USER,
            HOST,
            DB,
            COMMAND,
            TIME,
            STATE,
            LEFT(INFO, 100) as INFO
        FROM information_schema.PROCESSLIST
        WHERE COMMAND != 'Sleep'
        ORDER BY TIME DESC
        """
        return execute_query(query)
    except Exception as e:
        logger.error(f"Error al obtener procesos activos: {e}")
        return []


if __name__ == "__main__":
    # Script de prueba
    print("\n" + "="*60)
    print("PRUEBA DE CONEXIÓN A MYSQL")
    print("="*60 + "\n")
    
    print("1. Probando configuración...")
    config = get_mysql_config()
    print(f"   Host: {config['host']}:{config['port']}")
    print(f"   Database: {config['database']}")
    print(f"   Usuario: {config['user']}\n")
    
    print("2. Inicializando pool...")
    if not init_pool():
        print("   ✘ Error al inicializar pool\n")
        sys.exit(1)
    print("   ✓ Pool inicializado\n")
    
    print("3. Probando conexión...")
    if not test_connection():
        print("   ✘ Error en conexión\n")
        sys.exit(1)
    print("   ✓ Conexión exitosa\n")
    
    print("4. Información del servidor:")
    info = get_server_info()
    for key, value in info.items():
        print(f"   {key}: {value}")
    print()
    
    print("5. Información de conexiones:")
    conn_info = get_connection_info()
    for key, value in conn_info.items():
        print(f"   {key}: {value}")
    print()
    
    print("="*60)
    print("✓ TODAS LAS PRUEBAS COMPLETADAS")
    print("="*60 + "\n")
