"""
MSBDD - Módulo de conexión a la base de datos
Gestiona el pool de conexiones con SQLAlchemy + psycopg2
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from config import obtener_configuracion
import logging

logger = logging.getLogger("msbdd.bd")

cfg = obtener_configuracion()

# Motor principal: psycopg2 para máxima eficiencia con PostgreSQL
motor = create_engine(
    cfg.URL_CONEXION,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,  # Verifica conexiones antes de usarlas
    pool_recycle=300,    # Recicla conexiones cada 5 minutos
    echo=False,
)

FabricaSesion = sessionmaker(autocommit=False, autoflush=False, bind=motor)


def obtener_sesion() -> Session:
    """Generador de sesiones para inyección de dependencias en FastAPI."""
    sesion = FabricaSesion()
    try:
        yield sesion
    finally:
        sesion.close()


def verificar_conexion() -> bool:
    """Verifica que la conexión al motor PostgreSQL esté activa."""
    try:
        with motor.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Conexión a PostgreSQL verificada correctamente.")
        return True
    except Exception as e:
        logger.error(f"Error de conexión a PostgreSQL: {e}")
        return False
