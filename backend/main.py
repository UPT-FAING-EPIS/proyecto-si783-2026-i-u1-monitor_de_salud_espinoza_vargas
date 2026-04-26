"""
MSBDD - Monitor de Salud de Base de Datos
Aplicación principal FastAPI

Endpoints:
  GET /               → Estado del sistema
  GET /metricas       → KPIs actuales (RF01)
  GET /metricas/historial → Histórico de métricas (RF05)
  GET /metricas/resumen   → Estadísticas agregadas (RF05)
  GET /alertas        → Alertas activas (RF03)
  GET /alertas/historial  → Historial de alertas (RF03)
  GET /diagnostico    → Diagnóstico avanzado (RF04)
"""
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from base_datos import obtener_sesion, verificar_conexion
from config import obtener_configuracion
from modelos import MetricasKPI, DiagnosticoCompleto, Alerta, EstadoSistema, NivelRiesgo
from motor_alertas import motor_alertas
from repositorio_metricas import repositorio_metricas
from servicio_diagnostico import ejecutar_diagnostico_completo
from servicio_metricas import recolectar_kpis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("msbdd.api")
cfg = obtener_configuracion()

# ─── Scheduler de recolección automática ────────────────────────────────────

scheduler = BackgroundScheduler(timezone="America/Lima")


def ciclo_recoleccion():
    """
    Tarea programada: recolecta KPIs y evalúa alertas cada N segundos.
    Este es el corazón del sistema proactivo (vs. reactivo).
    """
    from base_datos import FabricaSesion
    sesion = FabricaSesion()
    try:
        metricas = recolectar_kpis(sesion)
        repositorio_metricas.guardar(metricas)
        nuevas_alertas = motor_alertas.evaluar_metricas(metricas)
        if nuevas_alertas:
            for alerta in nuevas_alertas:
                logger.warning(f"Nueva alerta generada: {alerta.tipo} - {alerta.mensaje}")
    except Exception as e:
        logger.error(f"Error en ciclo de recolección: {e}")
    finally:
        sesion.close()


# ─── Ciclo de vida de la aplicación ─────────────────────────────────────────

@asynccontextmanager
async def ciclo_vida(app: FastAPI):
    logger.info("=" * 60)
    logger.info("  MSBDD - Monitor de Salud de Base de Datos v1.0")
    logger.info("  Universidad Privada de Tacna - EPIS")
    logger.info("=" * 60)

    # Esperar a que PostgreSQL esté disponible
    for intento in range(10):
        if verificar_conexion():
            break
        logger.info(f"Esperando conexión a PostgreSQL... intento {intento + 1}/10")
        time.sleep(3)
    else:
        logger.error("No se pudo conectar a PostgreSQL. Revisa la configuración.")

    # Ejecutar primer ciclo inmediatamente
    ciclo_recoleccion()

    # Iniciar scheduler
    scheduler.add_job(
        ciclo_recoleccion,
        "interval",
        seconds=cfg.INTERVALO_RECOLECCION,
        id="recoleccion_kpis",
        max_instances=1,
    )
    scheduler.start()
    logger.info(f"Scheduler iniciado. Intervalo: {cfg.INTERVALO_RECOLECCION}s")

    yield  # Aplicación activa

    scheduler.shutdown()
    logger.info("MSBDD detenido correctamente.")


# ─── Instancia FastAPI ───────────────────────────────────────────────────────

app = FastAPI(
    title=cfg.TITULO_API,
    version=cfg.VERSION_API,
    description="""
## Monitor de Salud de Base de Datos - MSBDD

Sistema de monitoreo **proactivo** para motores PostgreSQL.

### Módulos
- **RF01**: Captura de KPIs (CPU, memoria, conexiones, caché)
- **RF03**: Sistema de alertas con validación de ciclos consecutivos
- **RF04**: Diagnóstico avanzado (consultas lentas, bloqueos, índices)
- **RF05**: Reportes e histórico de tendencias

### Regla de negocio
Las alertas se confirman solo si el umbral es superado en **2 ciclos consecutivos**,
evitando falsos positivos por picos transitorios.
    """,
    lifespan=ciclo_vida,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/", response_model=EstadoSistema, tags=["Sistema"])
def estado_sistema():
    """Retorna el estado general del sistema MSBDD."""
    ultimo = repositorio_metricas.ultimo_registro()
    alertas = motor_alertas.alertas_activas

    # Nivel global = el peor nivel activo
    if any(a.nivel == NivelRiesgo.ROJO for a in alertas):
        nivel_global = NivelRiesgo.ROJO
    elif any(a.nivel == NivelRiesgo.AMARILLO for a in alertas):
        nivel_global = NivelRiesgo.AMARILLO
    else:
        nivel_global = NivelRiesgo.VERDE

    return EstadoSistema(
        conectado=verificar_conexion(),
        ultima_recoleccion=ultimo.timestamp if ultimo else None,
        alertas_activas=len(alertas),
        nivel_riesgo_global=nivel_global,
    )


@app.get("/metricas", response_model=MetricasKPI, tags=["KPIs - RF01"])
def obtener_metricas_actuales(sesion: Session = Depends(obtener_sesion)):
    """
    Recolecta y retorna los KPIs actuales del motor PostgreSQL en tiempo real.
    Incluye semáforos de riesgo por cada métrica.
    """
    try:
        metricas = recolectar_kpis(sesion)
        repositorio_metricas.guardar(metricas)
        motor_alertas.evaluar_metricas(metricas)
        return metricas
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Error al conectar con PostgreSQL: {str(e)}")


@app.get("/metricas/historial", response_model=list[MetricasKPI], tags=["KPIs - RF01"])
def obtener_historial_metricas(
    ultimos_n: int = Query(default=60, ge=1, le=960, description="Número de registros recientes")
):
    """Retorna el historial de métricas almacenado en memoria (RF05)."""
    return repositorio_metricas.obtener_ultimos(ultimos_n)


@app.get("/metricas/resumen", tags=["Reportes - RF05"])
def obtener_resumen_estadistico():
    """Retorna estadísticas agregadas (promedio, máximo, mínimo) del período monitoreado."""
    resumen = repositorio_metricas.resumen_estadistico()
    if not resumen:
        raise HTTPException(status_code=404, detail="Sin datos históricos aún. Espera el primer ciclo de recolección.")
    return resumen


@app.get("/alertas", response_model=list[Alerta], tags=["Alertas - RF03"])
def obtener_alertas_activas():
    """
    Retorna todas las alertas activas (no resueltas).
    Las alertas se generan solo tras N ciclos consecutivos superando el umbral.
    """
    return motor_alertas.alertas_activas


@app.get("/alertas/historial", response_model=list[Alerta], tags=["Alertas - RF03"])
def obtener_historial_alertas():
    """Retorna el historial completo de alertas (activas y resueltas)."""
    return motor_alertas.historial_alertas


@app.get("/diagnostico", response_model=DiagnosticoCompleto, tags=["Diagnóstico - RF04"])
def obtener_diagnostico_completo(sesion: Session = Depends(obtener_sesion)):
    """
    Ejecuta el diagnóstico avanzado del motor:
    - Consultas lentas (> umbral configurable)
    - Bloqueos activos entre procesos
    - Índices no utilizados (candidatos a eliminación)

    Solo accede a vistas pg_catalog — nunca a datos de usuario.
    """
    try:
        return ejecutar_diagnostico_completo(sesion)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Error en diagnóstico: {str(e)}")
