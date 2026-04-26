"""
MSBDD - Configuración central del sistema
Carga variables de entorno y define parámetros globales
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Configuracion(BaseSettings):
    # Conexión a PostgreSQL monitoreado
    BD_HOST: str = "postgres"
    BD_PUERTO: int = 5432
    BD_NOMBRE: str = "msbdd_db"
    BD_USUARIO: str = "msbdd_user"
    BD_CONTRASENA: str = "msbdd_pass"

    # Umbrales de alerta (en porcentaje o valores absolutos)
    UMBRAL_CPU: float = 85.0           # % de uso de CPU
    UMBRAL_MEMORIA: float = 80.0       # % de uso de memoria
    UMBRAL_CONEXIONES: int = 80        # número absoluto de conexiones activas
    UMBRAL_CONSULTA_LENTA: float = 3.0 # segundos para considerar consulta lenta

    # Intervalo de recolección de métricas (segundos)
    INTERVALO_RECOLECCION: int = 30

    # Ciclos consecutivos para confirmar alerta (evita falsos positivos)
    CICLOS_CONFIRMACION: int = 2

    # API
    TITULO_API: str = "MSBDD - Monitor de Salud de Base de Datos"
    VERSION_API: str = "1.0.0"

    @property
    def URL_CONEXION(self) -> str:
        return (
            f"postgresql+psycopg2://{self.BD_USUARIO}:{self.BD_CONTRASENA}"
            f"@{self.BD_HOST}:{self.BD_PUERTO}/{self.BD_NOMBRE}"
        )

    class Config:
        env_file = ".env"


@lru_cache()
def obtener_configuracion() -> Configuracion:
    return Configuracion()
