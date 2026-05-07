#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║    GESTOR DE SERVICIOS - MONITOR DE SALUD                    ║
║    Configuración de servidores disponibles y conectividad     ║
╚════════════════════════════════════════════════════════════════╝
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class Service:
    """Representación de un servicio disponible."""
    id: str
    name: str
    type: str  # mysql, postgresql, sqlserver, elasticsearch
    host: str
    port: int
    database: str
    username: str
    password: str
    description: str
    region: str = "local"  # local, azure, elastika, aws
    is_active: bool = True
    last_check: Optional[str] = None
    status: str = "unknown"  # unknown, connected, error
    connected_clients: int = 0
    
    def to_dict(self):
        return asdict(self)


class ServiceManager:
    """Gestiona todos los servicios y su conectividad."""
    
    def __init__(self):
        self.services: Dict[str, Service] = {}
        self.connection_matrix: Dict[str, Dict[str, bool]] = {}
        self._initialize_services()
    
    def _initialize_services(self):
        """Inicializa los servicios disponibles."""
        # MySQL - Elasticsearch (principal)
        self.add_service(Service(
            id="mysql-elastika",
            name="MySQL Elasticsearch",
            type="mysql",
            host="localhost",
            port=3306,
            database="db_health_monitor",
            username="root",
            password="",
            description="Base de datos MySQL en servidor Elasticsearch",
            region="elastika"
        ))
        
        # PostgreSQL - Local (opcional)
        self.add_service(Service(
            id="postgres-local",
            name="PostgreSQL Local",
            type="postgresql",
            host="localhost",
            port=5432,
            database="db_health_monitor",
            username="postgres",
            password="",
            description="Base de datos PostgreSQL para desarrollo local",
            region="local",
            is_active=False
        ))
        
        # SQL Server - Azure
        self.add_service(Service(
            id="sqlserver-azure",
            name="SQL Server Azure",
            type="sqlserver",
            host="servidor.database.windows.net",
            port=1433,
            database="db_health_monitor",
            username="admin",
            password="",
            description="Base de datos SQL Server en Azure App Service",
            region="azure",
            is_active=False
        ))
        
        # Elasticsearch
        self.add_service(Service(
            id="elasticsearch-elastika",
            name="Elasticsearch Elastika",
            type="elasticsearch",
            host="localhost",
            port=9200,
            database="health-monitor",
            username="elastic",
            password="",
            description="Elasticsearch para logging y análisis",
            region="elastika",
            is_active=False
        ))
    
    def add_service(self, service: Service) -> bool:
        """Agrega un nuevo servicio."""
        try:
            self.services[service.id] = service
            logger.info(f"✓ Servicio agregado: {service.name}")
            return True
        except Exception as e:
            logger.error(f"✘ Error al agregar servicio: {e}")
            return False
    
    def get_service(self, service_id: str) -> Optional[Service]:
        """Obtiene un servicio por ID."""
        return self.services.get(service_id)
    
    def get_active_services(self) -> List[Service]:
        """Obtiene todos los servicios activos."""
        return [s for s in self.services.values() if s.is_active]
    
    def get_services_by_type(self, service_type: str) -> List[Service]:
        """Obtiene servicios de un tipo específico."""
        return [s for s in self.services.values() if s.type == service_type]
    
    def get_services_by_region(self, region: str) -> List[Service]:
        """Obtiene servicios de una región específica."""
        return [s for s in self.services.values() if s.region == region]
    
    def update_service_status(self, service_id: str, status: str, 
                            connected_clients: int = 0):
        """Actualiza el estado de un servicio."""
        service = self.get_service(service_id)
        if service:
            service.status = status
            service.last_check = datetime.now().isoformat()
            service.connected_clients = connected_clients
            logger.info(f"✓ Estado actualizado {service.name}: {status}")
            return True
        return False
    
    def register_connection(self, from_service: str, to_service: str, 
                          connected: bool):
        """Registra una conexión entre dos servicios."""
        if from_service not in self.connection_matrix:
            self.connection_matrix[from_service] = {}
        self.connection_matrix[from_service][to_service] = connected
    
    def get_connections(self, service_id: str) -> Dict[str, bool]:
        """Obtiene las conexiones de un servicio."""
        return self.connection_matrix.get(service_id, {})
    
    def get_connectivity_matrix(self) -> Dict[str, Dict[str, bool]]:
        """Obtiene la matriz de conectividad entre todos los servicios."""
        return self.connection_matrix
    
    def get_all_services(self) -> List[Service]:
        """Obtiene todos los servicios."""
        return list(self.services.values())
    
    def enable_service(self, service_id: str) -> bool:
        """Activa un servicio."""
        service = self.get_service(service_id)
        if service:
            service.is_active = True
            logger.info(f"✓ Servicio activado: {service.name}")
            return True
        return False
    
    def disable_service(self, service_id: str) -> bool:
        """Desactiva un servicio."""
        service = self.get_service(service_id)
        if service:
            service.is_active = False
            logger.info(f"✓ Servicio desactivado: {service.name}")
            return True
        return False
    
    def to_dict(self) -> dict:
        """Convierte el estado completo a diccionario."""
        return {
            "services": {k: v.to_dict() for k, v in self.services.items()},
            "active_services": [s.to_dict() for s in self.get_active_services()],
            "connectivity_matrix": self.connection_matrix,
            "summary": {
                "total_services": len(self.services),
                "active_services": len(self.get_active_services()),
                "by_type": {
                    "mysql": len(self.get_services_by_type("mysql")),
                    "postgresql": len(self.get_services_by_type("postgresql")),
                    "sqlserver": len(self.get_services_by_type("sqlserver")),
                    "elasticsearch": len(self.get_services_by_type("elasticsearch")),
                },
                "by_region": {
                    "local": len(self.get_services_by_region("local")),
                    "azure": len(self.get_services_by_region("azure")),
                    "elastika": len(self.get_services_by_region("elastika")),
                    "aws": len(self.get_services_by_region("aws")),
                }
            }
        }


class ConnectivityChecker:
    """Verifica la conectividad entre servicios."""
    
    def __init__(self, service_manager: ServiceManager):
        self.manager = service_manager
    
    def check_mysql_connection(self, service: Service) -> bool:
        """Verifica conexión a MySQL."""
        try:
            import mysql.connector
            conn = mysql.connector.connect(
                host=service.host,
                port=service.port,
                user=service.username,
                password=service.password,
                database=service.database,
                connection_timeout=5
            )
            conn.close()
            self.manager.update_service_status(service.id, "connected")
            return True
        except Exception as e:
            logger.error(f"✘ Error conexión MySQL {service.name}: {e}")
            self.manager.update_service_status(service.id, f"error: {str(e)[:50]}")
            return False
    
    def check_postgresql_connection(self, service: Service) -> bool:
        """Verifica conexión a PostgreSQL."""
        try:
            import psycopg2
            conn = psycopg2.connect(
                host=service.host,
                port=service.port,
                user=service.username,
                password=service.password,
                database=service.database,
                connect_timeout=5
            )
            conn.close()
            self.manager.update_service_status(service.id, "connected")
            return True
        except Exception as e:
            logger.error(f"✘ Error conexión PostgreSQL {service.name}: {e}")
            self.manager.update_service_status(service.id, f"error: {str(e)[:50]}")
            return False
    
    def check_elasticsearch_connection(self, service: Service) -> bool:
        """Verifica conexión a Elasticsearch."""
        try:
            import requests
            url = f"http://{service.host}:{service.port}/"
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                self.manager.update_service_status(service.id, "connected")
                return True
            else:
                self.manager.update_service_status(service.id, "error: http error")
                return False
        except Exception as e:
            logger.error(f"✘ Error conexión Elasticsearch {service.name}: {e}")
            self.manager.update_service_status(service.id, f"error: {str(e)[:50]}")
            return False
    
    def check_all_connections(self) -> Dict[str, bool]:
        """Verifica todas las conexiones."""
        results = {}
        for service in self.manager.get_all_services():
            if not service.is_active:
                results[service.id] = False
                continue
            
            if service.type == "mysql":
                results[service.id] = self.check_mysql_connection(service)
            elif service.type == "postgresql":
                results[service.id] = self.check_postgresql_connection(service)
            elif service.type == "elasticsearch":
                results[service.id] = self.check_elasticsearch_connection(service)
            else:
                results[service.id] = False
        
        return results
    
    def check_inter_service_connectivity(self) -> Dict[str, Dict[str, bool]]:
        """Verifica conectividad entre servicios."""
        matrix = {}
        active = self.manager.get_active_services()
        
        for from_service in active:
            matrix[from_service.id] = {}
            for to_service in active:
                if from_service.id == to_service.id:
                    matrix[from_service.id][to_service.id] = True
                else:
                    # Verificar conectividad (ejemplo: MySQL a Elasticsearch)
                    can_connect = self._can_service_connect(from_service, to_service)
                    matrix[from_service.id][to_service.id] = can_connect
                    self.manager.register_connection(
                        from_service.id, to_service.id, can_connect
                    )
        
        return matrix
    
    def _can_service_connect(self, from_service: Service, 
                            to_service: Service) -> bool:
        """Determina si un servicio puede conectarse a otro."""
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((to_service.host, to_service.port))
            sock.close()
            return result == 0
        except Exception:
            return False


# Instancia global del gestor de servicios
_service_manager = None
_connectivity_checker = None


def get_service_manager() -> ServiceManager:
    """Obtiene la instancia global del gestor de servicios."""
    global _service_manager
    if _service_manager is None:
        _service_manager = ServiceManager()
    return _service_manager


def get_connectivity_checker() -> ConnectivityChecker:
    """Obtiene la instancia global del checker de conectividad."""
    global _connectivity_checker
    if _connectivity_checker is None:
        manager = get_service_manager()
        _connectivity_checker = ConnectivityChecker(manager)
    return _connectivity_checker


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("\n" + "="*70)
    print("GESTOR DE SERVICIOS - MONITOR DE SALUD")
    print("="*70 + "\n")
    
    manager = get_service_manager()
    
    print("1. SERVICIOS DISPONIBLES:")
    for service in manager.get_all_services():
        status = "✓ ACTIVO" if service.is_active else "○ INACTIVO"
        print(f"   [{status}] {service.name} ({service.type}) en {service.region}")
        print(f"         {service.host}:{service.port}")
        print(f"         {service.description}\n")
    
    print("\n2. SERVICIOS ACTIVOS:")
    active = manager.get_active_services()
    for s in active:
        print(f"   - {s.name}")
    print()
    
    print("3. VERIFICANDO CONECTIVIDAD:")
    checker = get_connectivity_checker()
    results = checker.check_all_connections()
    for service_id, connected in results.items():
        service = manager.get_service(service_id)
        status = "✓ Conectado" if connected else "✘ Error"
        print(f"   [{status}] {service.name}")
    print()
    
    print("4. MATRIZ DE CONECTIVIDAD ENTRE SERVICIOS:")
    matrix = checker.check_inter_service_connectivity()
    for from_id, connections in matrix.items():
        from_service = manager.get_service(from_id)
        print(f"   {from_service.name}:")
        for to_id, connected in connections.items():
            to_service = manager.get_service(to_id)
            status = "✓" if connected else "✘"
            print(f"      {status} → {to_service.name}")
    print()
    
    print("5. RESUMEN:")
    summary = manager.to_dict()["summary"]
    print(f"   Total de servicios: {summary['total_services']}")
    print(f"   Servicios activos: {summary['active_services']}")
    print(f"   Por tipo: MySQL={summary['by_type']['mysql']}, "
          f"PostgreSQL={summary['by_type']['postgresql']}, "
          f"SQLServer={summary['by_type']['sqlserver']}, "
          f"Elasticsearch={summary['by_type']['elasticsearch']}")
    print(f"   Por región: Local={summary['by_region']['local']}, "
          f"Azure={summary['by_region']['azure']}, "
          f"Elastika={summary['by_region']['elastika']}")
    print()
    
    print("="*70 + "\n")
