"""
MSBDD - Motor de Alertas (RF03)
Gestiona la generación de alertas con validación de ciclos consecutivos.
Regla de negocio: una alerta se confirma solo si el umbral es superado
en N ciclos consecutivos (configurable), evitando falsos positivos por picos transitorios.
"""
from datetime import datetime
from collections import defaultdict, deque
from modelos import Alerta, MetricasKPI, NivelRiesgo
from config import obtener_configuracion
import uuid
import logging

logger = logging.getLogger("msbdd.alertas")
cfg = obtener_configuracion()


class MotorAlertas:
    """
    Gestiona el ciclo de vida de alertas del sistema.
    Implementa la regla de negocio de ciclos consecutivos.
    """

    def __init__(self):
        # Historial de ciclos por tipo de métrica
        self._historial_ciclos: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=cfg.CICLOS_CONFIRMACION)
        )
        # Alertas activas (no resueltas)
        self._alertas_activas: dict[str, Alerta] = {}
        # Historial completo de alertas (para reportes RF05)
        self._historial_alertas: list[Alerta] = []

    def _registrar_ciclo(self, tipo: str, en_umbral: bool) -> bool:
        """
        Registra si el umbral fue superado en este ciclo.
        Retorna True si se deben N ciclos consecutivos superando el umbral.
        """
        self._historial_ciclos[tipo].append(en_umbral)
        ciclos = self._historial_ciclos[tipo]
        # Verificar que tenemos suficientes ciclos y todos superan el umbral
        return (
            len(ciclos) >= cfg.CICLOS_CONFIRMACION
            and all(ciclos)
        )

    def _crear_alerta(self, tipo: str, nivel: NivelRiesgo, mensaje: str,
                      valor: float, umbral: float) -> Alerta:
        alerta = Alerta(
            id=str(uuid.uuid4())[:8],
            timestamp=datetime.now(),
            tipo=tipo,
            nivel=nivel,
            mensaje=mensaje,
            valor_actual=round(valor, 2),
            umbral_configurado=umbral,
            resuelta=False,
        )
        self._alertas_activas[tipo] = alerta
        self._historial_alertas.append(alerta)
        logger.warning(f"🔴 ALERTA [{tipo}]: {mensaje} | Valor: {valor:.1f} | Umbral: {umbral}")
        return alerta

    def _resolver_alerta(self, tipo: str):
        """Marca la alerta de un tipo como resuelta cuando el sistema vuelve a la normalidad."""
        if tipo in self._alertas_activas:
            alerta = self._alertas_activas.pop(tipo)
            alerta.resuelta = True
            logger.info(f"✅ Alerta resuelta [{tipo}]: el sistema volvió a la normalidad.")

    def evaluar_metricas(self, metricas: MetricasKPI) -> list[Alerta]:
        """
        Evalúa las métricas actuales y genera alertas según las reglas de negocio.
        Retorna lista de nuevas alertas generadas en este ciclo.
        """
        nuevas_alertas = []

        # --- CPU ---
        cpu_en_umbral = metricas.uso_cpu_porcentaje >= cfg.UMBRAL_CPU
        if self._registrar_ciclo("cpu", cpu_en_umbral):
            if "cpu" not in self._alertas_activas:
                alerta = self._crear_alerta(
                    tipo="cpu",
                    nivel=NivelRiesgo.ROJO if metricas.uso_cpu_porcentaje >= cfg.UMBRAL_CPU * 1.1 else NivelRiesgo.AMARILLO,
                    mensaje=f"Uso de CPU en {metricas.uso_cpu_porcentaje:.1f}% supera el umbral de {cfg.UMBRAL_CPU}%",
                    valor=metricas.uso_cpu_porcentaje,
                    umbral=cfg.UMBRAL_CPU,
                )
                nuevas_alertas.append(alerta)
        elif not cpu_en_umbral:
            self._resolver_alerta("cpu")

        # --- Memoria ---
        mem_en_umbral = metricas.uso_memoria_porcentaje >= cfg.UMBRAL_MEMORIA
        if self._registrar_ciclo("memoria", mem_en_umbral):
            if "memoria" not in self._alertas_activas:
                alerta = self._crear_alerta(
                    tipo="memoria",
                    nivel=NivelRiesgo.ROJO,
                    mensaje=f"Uso de memoria en {metricas.uso_memoria_porcentaje:.1f}% supera el umbral de {cfg.UMBRAL_MEMORIA}%",
                    valor=metricas.uso_memoria_porcentaje,
                    umbral=cfg.UMBRAL_MEMORIA,
                )
                nuevas_alertas.append(alerta)
        elif not mem_en_umbral:
            self._resolver_alerta("memoria")

        # --- Conexiones ---
        conn_en_umbral = metricas.conexiones_activas >= cfg.UMBRAL_CONEXIONES
        if self._registrar_ciclo("conexiones", conn_en_umbral):
            if "conexiones" not in self._alertas_activas:
                alerta = self._crear_alerta(
                    tipo="conexiones",
                    nivel=NivelRiesgo.AMARILLO,
                    mensaje=f"Conexiones activas: {metricas.conexiones_activas} supera el umbral de {cfg.UMBRAL_CONEXIONES}",
                    valor=float(metricas.conexiones_activas),
                    umbral=float(cfg.UMBRAL_CONEXIONES),
                )
                nuevas_alertas.append(alerta)
        elif not conn_en_umbral:
            self._resolver_alerta("conexiones")

        # --- Caché hit ratio bajo ---
        cache_bajo = metricas.tasa_cache_hit < 90.0
        if self._registrar_ciclo("cache", cache_bajo):
            if "cache" not in self._alertas_activas:
                alerta = self._crear_alerta(
                    tipo="cache",
                    nivel=NivelRiesgo.AMARILLO,
                    mensaje=f"Tasa de caché en {metricas.tasa_cache_hit:.1f}% — posible presión en I/O de disco",
                    valor=metricas.tasa_cache_hit,
                    umbral=90.0,
                )
                nuevas_alertas.append(alerta)
        elif not cache_bajo:
            self._resolver_alerta("cache")

        return nuevas_alertas

    @property
    def alertas_activas(self) -> list[Alerta]:
        return list(self._alertas_activas.values())

    @property
    def historial_alertas(self) -> list[Alerta]:
        return list(reversed(self._historial_alertas[-100:]))  # Últimas 100


# Instancia global del motor de alertas
motor_alertas = MotorAlertas()
