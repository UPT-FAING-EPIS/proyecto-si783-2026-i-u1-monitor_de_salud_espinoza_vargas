"""
MSBDD - Repositorio de métricas en memoria (RF05 - Reportes)
Almacena el historial de KPIs para análisis de tendencias.
Usa una deque circular para eficiencia de memoria.
"""
from collections import deque
from datetime import datetime, timedelta
from modelos import MetricasKPI
from typing import Optional
import threading

# Máximo de puntos en memoria (~8 horas con intervalo de 30s)
MAX_PUNTOS = 960


class RepositorioMetricas:
    """Almacén thread-safe de historial de métricas."""

    def __init__(self):
        self._datos: deque[MetricasKPI] = deque(maxlen=MAX_PUNTOS)
        self._lock = threading.Lock()

    def guardar(self, metricas: MetricasKPI):
        with self._lock:
            self._datos.append(metricas)

    def obtener_todos(self) -> list[MetricasKPI]:
        with self._lock:
            return list(self._datos)

    def obtener_ultimos(self, n: int = 60) -> list[MetricasKPI]:
        with self._lock:
            datos = list(self._datos)
            return datos[-n:] if len(datos) >= n else datos

    def obtener_ultima_hora(self) -> list[MetricasKPI]:
        limite = datetime.now() - timedelta(hours=1)
        with self._lock:
            return [m for m in self._datos if m.timestamp >= limite]

    def ultimo_registro(self) -> Optional[MetricasKPI]:
        with self._lock:
            return self._datos[-1] if self._datos else None

    def resumen_estadistico(self) -> dict:
        """Calcula estadísticas de tendencia para el dashboard."""
        with self._lock:
            datos = list(self._datos)

        if not datos:
            return {}

        cpu_vals = [m.uso_cpu_porcentaje for m in datos]
        mem_vals = [m.uso_memoria_porcentaje for m in datos]
        conn_vals = [m.conexiones_activas for m in datos]

        return {
            "total_puntos": len(datos),
            "desde": datos[0].timestamp.isoformat() if datos else None,
            "hasta": datos[-1].timestamp.isoformat() if datos else None,
            "cpu": {
                "promedio": round(sum(cpu_vals) / len(cpu_vals), 1),
                "maximo": round(max(cpu_vals), 1),
                "minimo": round(min(cpu_vals), 1),
            },
            "memoria": {
                "promedio": round(sum(mem_vals) / len(mem_vals), 1),
                "maximo": round(max(mem_vals), 1),
                "minimo": round(min(mem_vals), 1),
            },
            "conexiones": {
                "promedio": round(sum(conn_vals) / len(conn_vals), 1),
                "maximo": max(conn_vals),
                "minimo": min(conn_vals),
            },
        }


# Instancia global del repositorio
repositorio_metricas = RepositorioMetricas()
