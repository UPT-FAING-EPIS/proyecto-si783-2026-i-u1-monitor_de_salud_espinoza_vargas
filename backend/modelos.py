"""
MSBDD - Modelos de datos (esquemas Pydantic)
Define las estructuras de respuesta para la API REST
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from enum import Enum


class NivelRiesgo(str, Enum):
    """Semáforo de riesgo del sistema."""
    VERDE = "verde"      # Normal
    AMARILLO = "amarillo"  # Precaución
    ROJO = "rojo"        # Crítico


class MetricasKPI(BaseModel):
    """RF01 - Métricas principales del motor PostgreSQL."""
    timestamp: datetime
    uso_cpu_porcentaje: float = Field(..., description="% de CPU usado por PostgreSQL")
    uso_memoria_porcentaje: float = Field(..., description="% de memoria RAM usada")
    conexiones_activas: int = Field(..., description="Conexiones en estado 'active'")
    conexiones_totales: int = Field(..., description="Total de conexiones abiertas")
    conexiones_inactivas: int = Field(..., description="Conexiones en estado 'idle'")
    tamanio_bd_mb: float = Field(..., description="Tamaño de la base de datos en MB")
    tasa_cache_hit: float = Field(..., description="% de aciertos en caché de buffers")
    transacciones_por_segundo: float = Field(..., description="TPS estimado")
    nivel_riesgo_cpu: NivelRiesgo
    nivel_riesgo_memoria: NivelRiesgo
    nivel_riesgo_conexiones: NivelRiesgo


class ConsultaLenta(BaseModel):
    """RF04 - Diagnóstico de consultas lentas."""
    pid: int
    usuario: str
    duracion_segundos: float
    estado: str
    texto_consulta: str
    bd_nombre: str
    esperando: bool


class Bloqueo(BaseModel):
    """RF04 - Diagnóstico de bloqueos entre procesos."""
    pid_bloqueado: int
    pid_bloqueante: int
    relacion_bloqueada: str
    tipo_bloqueo: str
    consulta_bloqueada: str
    duracion_espera_segundos: float


class IndiceNoUsado(BaseModel):
    """RF04 - Índices que no están siendo utilizados."""
    esquema: str
    tabla: str
    nombre_indice: str
    tamanio_mb: float
    escaneos_indice: int


class Alerta(BaseModel):
    """RF03 - Estructura de una alerta generada."""
    id: str
    timestamp: datetime
    tipo: str
    nivel: NivelRiesgo
    mensaje: str
    valor_actual: float
    umbral_configurado: float
    resuelta: bool = False


class DiagnosticoCompleto(BaseModel):
    """RF04 - Respuesta del diagnóstico avanzado completo."""
    timestamp: datetime
    consultas_lentas: list[ConsultaLenta]
    bloqueos_activos: list[Bloqueo]
    indices_no_usados: list[IndiceNoUsado]
    total_consultas_lentas: int
    total_bloqueos: int
    total_indices_no_usados: int


class EstadoSistema(BaseModel):
    """Estado general del sistema MSBDD."""
    sistema: str = "MSBDD - Monitor de Salud de Base de Datos"
    version: str = "1.0.0"
    conectado: bool
    ultima_recoleccion: Optional[datetime]
    alertas_activas: int
    nivel_riesgo_global: NivelRiesgo
