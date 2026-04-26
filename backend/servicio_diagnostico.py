"""
MSBDD - Servicio de Diagnóstico Avanzado (RF04)
Detecta consultas lentas, bloqueos activos e índices no utilizados.
Solo accede a vistas del esquema pg_catalog y pg_stat_* (sin datos de usuario).
"""
from sqlalchemy import text
from sqlalchemy.orm import Session
from datetime import datetime
from modelos import DiagnosticoCompleto, ConsultaLenta, Bloqueo, IndiceNoUsado
from config import obtener_configuracion
import logging

logger = logging.getLogger("msbdd.diagnostico")
cfg = obtener_configuracion()


def diagnosticar_consultas_lentas(sesion: Session) -> list[ConsultaLenta]:
    """
    Detecta consultas activas que superan el umbral de duración configurado.
    Excluye el propio proceso del monitor para no contaminar resultados.
    """
    filas = sesion.execute(text("""
        SELECT
            pid,
            usename AS usuario,
            EXTRACT(EPOCH FROM (NOW() - query_start)) AS duracion_segundos,
            state AS estado,
            LEFT(query, 300) AS texto_consulta,
            datname AS bd_nombre,
            wait_event IS NOT NULL AS esperando
        FROM pg_stat_activity
        WHERE
            state = 'active'
            AND query_start IS NOT NULL
            AND query NOT ILIKE '%pg_stat_activity%'
            AND EXTRACT(EPOCH FROM (NOW() - query_start)) > :umbral
        ORDER BY duracion_segundos DESC
        LIMIT 20
    """), {"umbral": cfg.UMBRAL_CONSULTA_LENTA}).fetchall()

    return [
        ConsultaLenta(
            pid=fila.pid,
            usuario=fila.usuario or "desconocido",
            duracion_segundos=round(float(fila.duracion_segundos), 2),
            estado=fila.estado or "",
            texto_consulta=fila.texto_consulta or "",
            bd_nombre=fila.bd_nombre or "",
            esperando=bool(fila.esperando),
        )
        for fila in filas
    ]


def diagnosticar_bloqueos(sesion: Session) -> list[Bloqueo]:
    """
    Detecta bloqueos activos entre procesos usando pg_locks y pg_stat_activity.
    """
    filas = sesion.execute(text("""
        SELECT
            bloqueado.pid AS pid_bloqueado,
            bloqueante.pid AS pid_bloqueante,
            bloqueado_actividad.relname AS relacion_bloqueada,
            bloqueado_locks.locktype AS tipo_bloqueo,
            LEFT(bloqueado_actividad_q.query, 200) AS consulta_bloqueada,
            EXTRACT(EPOCH FROM (NOW() - bloqueado_actividad_q.query_start)) AS duracion_espera
        FROM pg_catalog.pg_locks AS bloqueado
        JOIN pg_catalog.pg_locks AS bloqueante
            ON bloqueado.transactionid = bloqueante.transactionid
            AND bloqueado.pid != bloqueante.pid
        JOIN pg_catalog.pg_stat_activity AS bloqueado_actividad_q
            ON bloqueado_actividad_q.pid = bloqueado.pid
        LEFT JOIN pg_catalog.pg_class AS bloqueado_actividad
            ON bloqueado_actividad.oid = bloqueado.relation
        WHERE
            NOT bloqueado.granted
            AND bloqueante.granted
        LIMIT 15
    """)).fetchall()

    return [
        Bloqueo(
            pid_bloqueado=int(fila.pid_bloqueado),
            pid_bloqueante=int(fila.pid_bloqueante),
            relacion_bloqueada=fila.relacion_bloqueada or "desconocida",
            tipo_bloqueo=fila.tipo_bloqueo or "",
            consulta_bloqueada=fila.consulta_bloqueada or "",
            duracion_espera_segundos=round(float(fila.duracion_espera or 0), 2),
        )
        for fila in filas
    ]


def diagnosticar_indices_no_usados(sesion: Session) -> list[IndiceNoUsado]:
    """
    Detecta índices que no han sido utilizados desde el último reinicio de estadísticas.
    Excluye índices de claves primarias y únicos que son necesarios para integridad.
    """
    filas = sesion.execute(text("""
        SELECT
            schemaname AS esquema,
            relname AS tabla,
            indexrelname AS nombre_indice,
            ROUND(pg_relation_size(indexrelid) / (1024.0 * 1024.0), 3) AS tamanio_mb,
            idx_scan AS escaneos_indice
        FROM pg_stat_user_indexes
        JOIN pg_index USING (indexrelid)
        WHERE
            idx_scan = 0
            AND NOT indisprimary
            AND NOT indisunique
            AND schemaname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY tamanio_mb DESC
        LIMIT 30
    """)).fetchall()

    return [
        IndiceNoUsado(
            esquema=fila.esquema,
            tabla=fila.tabla,
            nombre_indice=fila.nombre_indice,
            tamanio_mb=float(fila.tamanio_mb or 0),
            escaneos_indice=int(fila.escaneos_indice or 0),
        )
        for fila in filas
    ]


def ejecutar_diagnostico_completo(sesion: Session) -> DiagnosticoCompleto:
    """Ejecuta el diagnóstico avanzado completo (RF04)."""
    consultas_lentas = diagnosticar_consultas_lentas(sesion)
    bloqueos = diagnosticar_bloqueos(sesion)
    indices = diagnosticar_indices_no_usados(sesion)

    return DiagnosticoCompleto(
        timestamp=datetime.now(),
        consultas_lentas=consultas_lentas,
        bloqueos_activos=bloqueos,
        indices_no_usados=indices,
        total_consultas_lentas=len(consultas_lentas),
        total_bloqueos=len(bloqueos),
        total_indices_no_usados=len(indices),
    )
