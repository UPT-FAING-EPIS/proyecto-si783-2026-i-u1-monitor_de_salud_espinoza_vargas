#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║    CLI - GESTOR DE SERVICIOS PARA MONITOR DE SALUD           ║
║    Prueba y configura servicios desde la línea de comandos   ║
╚══════════════════════════════════════════════════════════════╝
"""

import sys
import os
import argparse
import logging
from pathlib import Path

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Importar gestores
from services import get_service_manager, get_connectivity_checker
from db_connection import get_mysql_config, test_connection, get_server_info, init_pool


def print_header(title: str):
    """Imprime un encabezado formateado."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def cmd_list_services():
    """Lista todos los servicios disponibles."""
    print_header("SERVICIOS DISPONIBLES")
    
    manager = get_service_manager()
    services = manager.get_all_services()
    
    if not services:
        print("No hay servicios disponibles.\n")
        return
    
    for i, service in enumerate(services, 1):
        status = "✓ ACTIVO" if service.is_active else "○ INACTIVO"
        print(f"{i}. [{status}] {service.name}")
        print(f"   Tipo: {service.type}")
        print(f"   Región: {service.region}")
        print(f"   Host: {service.host}:{service.port}")
        print(f"   BD: {service.database}")
        print(f"   Descripción: {service.description}")
        print(f"   Estado: {service.status}")
        print()


def cmd_check_service(service_id: str):
    """Verifica la conectividad de un servicio específico."""
    print_header(f"VERIFICAR SERVICIO: {service_id}")
    
    manager = get_service_manager()
    checker = get_connectivity_checker()
    
    service = manager.get_service(service_id)
    if not service:
        print(f"✘ Servicio '{service_id}' no encontrado.\n")
        return False
    
    print(f"Servicio: {service.name}")
    print(f"Tipo: {service.type}")
    print(f"Host: {service.host}:{service.port}")
    print(f"BD: {service.database}\n")
    
    print("Verificando conectividad...")
    
    if service.type == "mysql":
        connected = checker.check_mysql_connection(service)
    elif service.type == "postgresql":
        connected = checker.check_postgresql_connection(service)
    elif service.type == "elasticsearch":
        connected = checker.check_elasticsearch_connection(service)
    else:
        connected = False
    
    if connected:
        print(f"✓ Conexión exitosa a {service.name}\n")
        return True
    else:
        print(f"✘ No se pudo conectar a {service.name}")
        print(f"   Estado: {service.status}\n")
        return False


def cmd_check_all():
    """Verifica la conectividad de todos los servicios."""
    print_header("VERIFICAR TODOS LOS SERVICIOS")
    
    manager = get_service_manager()
    checker = get_connectivity_checker()
    
    results = checker.check_all_connections()
    
    print(f"Total de servicios: {len(results)}")
    print(f"Servicios activos: {len(manager.get_active_services())}\n")
    
    connected_count = 0
    for service_id, connected in results.items():
        service = manager.get_service(service_id)
        status = "✓ Conectado" if connected else "✘ Error"
        print(f"[{status}] {service.name} ({service.type})")
        if connected:
            connected_count += 1
    
    print(f"\n✓ Conectados: {connected_count}/{len(results)}\n")


def cmd_connectivity_matrix():
    """Muestra la matriz de conectividad entre servicios."""
    print_header("MATRIZ DE CONECTIVIDAD ENTRE SERVICIOS")
    
    manager = get_service_manager()
    checker = get_connectivity_checker()
    
    matrix = checker.check_inter_service_connectivity()
    
    if not matrix:
        print("No hay servicios activos.\n")
        return
    
    # Encabezados
    service_names = [manager.get_service(sid).name for sid in matrix.keys()]
    max_name_len = max(len(name) for name in service_names) if service_names else 0
    
    # Encabezado de columnas
    print(" " * (max_name_len + 2), end="")
    for name in service_names:
        print(f" {name[:15]:15}", end="")
    print("\n")
    
    # Datos
    for from_id in matrix.keys():
        from_service = manager.get_service(from_id)
        print(f"{from_service.name:>{max_name_len}}", end=" ")
        
        for to_id in matrix.keys():
            connected = matrix[from_id].get(to_id, False)
            symbol = "✓" if connected else "✘"
            print(f" {symbol:^15}", end="")
        print()
    
    print()


def cmd_summary():
    """Muestra un resumen del estado general del sistema."""
    print_header("RESUMEN DEL SISTEMA")
    
    manager = get_service_manager()
    checker = get_connectivity_checker()
    
    # Información de servicios
    all_services = manager.get_all_services()
    active_services = manager.get_active_services()
    connectivity_status = checker.check_all_connections()
    connected_count = sum(1 for v in connectivity_status.values() if v)
    
    print("📊 SERVICIOS:")
    print(f"   Total: {len(all_services)}")
    print(f"   Activos: {len(active_services)}")
    print(f"   Conectados: {connected_count}/{len(active_services)}")
    print()
    
    # Por tipo
    summary = manager.to_dict()["summary"]
    print("📋 POR TIPO:")
    for db_type, count in summary["by_type"].items():
        if count > 0:
            print(f"   {db_type.capitalize()}: {count}")
    print()
    
    # Por región
    print("🌍 POR REGIÓN:")
    for region, count in summary["by_region"].items():
        if count > 0:
            print(f"   {region.capitalize()}: {count}")
    print()
    
    # Estado de cada servicio
    print("📌 ESTADO DETALLADO:")
    for service in all_services:
        status_icon = "✓" if connectivity_status.get(service.id, False) else "✘"
        active_icon = "●" if service.is_active else "○"
        print(f"   [{active_icon} {status_icon}] {service.name}")
        print(f"       {service.type} en {service.region} ({service.host}:{service.port})")
        if service.status != "unknown":
            print(f"       Estado: {service.status}")
    print()


def cmd_enable(service_id: str):
    """Activa un servicio."""
    manager = get_service_manager()
    
    if manager.enable_service(service_id):
        service = manager.get_service(service_id)
        print(f"\n✓ Servicio '{service.name}' activado.\n")
    else:
        print(f"\n✘ Servicio '{service_id}' no encontrado.\n")


def cmd_disable(service_id: str):
    """Desactiva un servicio."""
    manager = get_service_manager()
    
    if manager.disable_service(service_id):
        service = manager.get_service(service_id)
        print(f"\n✓ Servicio '{service.name}' desactivado.\n")
    else:
        print(f"\n✘ Servicio '{service_id}' no encontrado.\n")


def cmd_mysql_test():
    """Prueba conexión específica a MySQL."""
    print_header("PRUEBA DE CONEXIÓN A MYSQL")
    
    print("1. Obteniendo configuración...")
    try:
        config = get_mysql_config()
        print(f"   Host: {config['host']}:{config['port']}")
        print(f"   Usuario: {config['user']}")
        print(f"   BD: {config['database']}\n")
    except Exception as e:
        print(f"   ✘ Error: {e}\n")
        return
    
    print("2. Inicializando pool...")
    try:
        if init_pool():
            print("   ✓ Pool inicializado\n")
        else:
            print("   ✘ Error al inicializar\n")
            return
    except Exception as e:
        print(f"   ✘ Error: {e}\n")
        return
    
    print("3. Probando conexión...")
    try:
        if test_connection():
            print("   ✓ Conexión exitosa\n")
        else:
            print("   ✘ Conexión fallida\n")
            return
    except Exception as e:
        print(f"   ✘ Error: {e}\n")
        return
    
    print("4. Obteniendo información del servidor...")
    try:
        info = get_server_info()
        if info:
            for key, value in info.items():
                print(f"   {key}: {value}")
        print()
    except Exception as e:
        print(f"   ✘ Error: {e}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Gestor de Servicios para Monitor de Salud - CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos de uso:
  %(prog)s list              # Listar todos los servicios
  %(prog)s check mysql-elastika  # Verificar un servicio
  %(prog)s check-all         # Verificar todos
  %(prog)s matrix            # Mostrar matriz de conectividad
  %(prog)s summary           # Mostrar resumen del sistema
  %(prog)s enable mysql-elastika  # Activar servicio
  %(prog)s disable postgres-local # Desactivar servicio
  %(prog)s mysql-test        # Prueba específica de MySQL
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Comando a ejecutar")
    
    # Comando list
    subparsers.add_parser("list", help="Listar todos los servicios")
    
    # Comando check
    check_parser = subparsers.add_parser("check", help="Verificar conectividad de un servicio")
    check_parser.add_argument("service_id", help="ID del servicio a verificar")
    
    # Comando check-all
    subparsers.add_parser("check-all", help="Verificar conectividad de todos los servicios")
    
    # Comando matrix
    subparsers.add_parser("matrix", help="Mostrar matriz de conectividad")
    
    # Comando summary
    subparsers.add_parser("summary", help="Mostrar resumen del sistema")
    
    # Comando enable
    enable_parser = subparsers.add_parser("enable", help="Activar un servicio")
    enable_parser.add_argument("service_id", help="ID del servicio a activar")
    
    # Comando disable
    disable_parser = subparsers.add_parser("disable", help="Desactivar un servicio")
    disable_parser.add_argument("service_id", help="ID del servicio a desactivar")
    
    # Comando mysql-test
    subparsers.add_parser("mysql-test", help="Prueba de conexión a MySQL")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # Ejecutar comando
    if args.command == "list":
        cmd_list_services()
    elif args.command == "check":
        cmd_check_service(args.service_id)
    elif args.command == "check-all":
        cmd_check_all()
    elif args.command == "matrix":
        cmd_connectivity_matrix()
    elif args.command == "summary":
        cmd_summary()
    elif args.command == "enable":
        cmd_enable(args.service_id)
    elif args.command == "disable":
        cmd_disable(args.service_id)
    elif args.command == "mysql-test":
        cmd_mysql_test()


if __name__ == "__main__":
    main()
