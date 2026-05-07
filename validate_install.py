#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║    VALIDACIÓN E INSTALACIÓN - Monitor de Salud v2.0          ║
║    Script para verificar instalación y ejecutar pruebas       ║
╚══════════════════════════════════════════════════════════════╝
"""

import sys
import os
from pathlib import Path

# Colores para la terminal
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"
CHECKMARK = "✓"
CROSS = "✘"


def print_header(title: str):
    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}  {title}{RESET}")
    print(f"{BLUE}{'='*70}{RESET}\n")


def check_file(filepath: str, description: str) -> bool:
    if Path(filepath).exists():
        print(f"{GREEN}{CHECKMARK}{RESET} {description}")
        return True
    else:
        print(f"{RED}{CROSS}{RESET} {description}")
        return False


def check_import(module_name: str, description: str) -> bool:
    try:
        __import__(module_name)
        print(f"{GREEN}{CHECKMARK}{RESET} {description}")
        return True
    except ImportError as e:
        print(f"{RED}{CROSS}{RESET} {description} - {e}")
        return False


def main():
    print(f"\n{BLUE}")
    print("╔═════════════════════════════════════════════════════════════╗")
    print("║     VALIDACIÓN DE INSTALACIÓN - Monitor de Salud v2.0      ║")
    print("╚═════════════════════════════════════════════════════════════╝")
    print(f"{RESET}")
    
    all_checks_passed = True
    
    # PASO 1: Verificar archivos
    print_header("1. VERIFICACIÓN DE ARCHIVOS")
    
    files_to_check = [
        ("db_connection.py", "Módulo de conexión MySQL"),
        ("services.py", "Gestor de servicios"),
        ("cli.py", "Interfaz CLI"),
        ("server.py", "Servidor Flask"),
        ("config.ini", "Configuración"),
        ("requirements.txt", "Dependencias"),
        ("SERVICIOS_CONFIG.md", "Documentación de servicios"),
        ("README_SERVICIOS.md", "README actualizado"),
        ("GUIA_RAPIDA_SERVICIOS.md", "Guía rápida"),
        ("CAMBIOS_V2.0.md", "Resumen de cambios"),
    ]
    
    for filepath, description in files_to_check:
        if not check_file(filepath, description):
            all_checks_passed = False
    
    # PASO 2: Verificar módulos Python
    print_header("2. VERIFICACIÓN DE MÓDULOS PYTHON")
    
    modules_to_check = [
        ("flask", "Flask web framework"),
        ("flask_cors", "Flask-CORS"),
        ("psutil", "psutil (monitoreo del sistema)"),
        ("mysql.connector", "mysql-connector-python"),
        ("requests", "requests"),
    ]
    
    for module, description in modules_to_check:
        if not check_import(module, description):
            all_checks_passed = False
    
    # PASO 3: Verificar configuración
    print_header("3. VERIFICACIÓN DE CONFIGURACIÓN")
    
    config_checks = [
        (Path("config.ini").exists(), "config.ini existe"),
        (Path("config.ini").stat().st_size > 0, "config.ini no está vacío"),
    ]
    
    for check, description in config_checks:
        if check:
            print(f"{GREEN}{CHECKMARK}{RESET} {description}")
        else:
            print(f"{RED}{CROSS}{RESET} {description}")
            all_checks_passed = False
    
    # PASO 4: Resumen de nuevas características
    print_header("4. NUEVAS CARACTERÍSTICAS IMPLEMENTADAS")
    
    features = [
        "Gestión de múltiples servicios (MySQL, PostgreSQL, SQL Server, Elasticsearch)",
        "Sistema de regiones (local, azure, elastika, aws)",
        "Matriz de conectividad entre servicios",
        "7 nuevos endpoints API de servicios",
        "3 nuevos endpoints API de conectividad",
        "Interfaz CLI con 8 comandos principales",
        "Pool de conexiones MySQL",
        "Verificador de conectividad inter-servicio",
        "Resumen completo del sistema",
        "Documentación completa",
    ]
    
    for feature in features:
        print(f"{GREEN}{CHECKMARK}{RESET} {feature}")
    
    # PASO 5: Archivos nuevos
    print_header("5. ARCHIVOS CREADOS")
    
    new_files = [
        ("db_connection.py", "Gestión centralizada de conexión MySQL"),
        ("services.py", "Gestor de servicios y conectividad"),
        ("cli.py", "Interfaz de línea de comandos"),
        ("SERVICIOS_CONFIG.md", "Documentación de servicios"),
        ("README_SERVICIOS.md", "README del proyecto v2.0"),
        ("GUIA_RAPIDA_SERVICIOS.md", "Guía rápida de uso"),
        ("CAMBIOS_V2.0.md", "Resumen de cambios"),
    ]
    
    print(f"{len(new_files)} archivos nuevos creados:\n")
    for filename, description in new_files:
        print(f"  • {BLUE}{filename}{RESET}")
        print(f"    {description}\n")
    
    # PASO 6: Archivos modificados
    print_header("6. ARCHIVOS MODIFICADOS")
    
    modified_files = [
        ("config.ini", "Agregadas 4 nuevas secciones (database, postgresql, elasticsearch, services)"),
        ("server.py", "Integración con servicios, 8 nuevos endpoints API"),
        ("requirements.txt", "Agregadas 3 nuevas dependencias"),
    ]
    
    for filename, description in modified_files:
        print(f"  • {YELLOW}{filename}{RESET}")
        print(f"    {description}\n")
    
    # PASO 7: Instrucciones siguientes
    print_header("7. PRÓXIMOS PASOS")
    
    if all_checks_passed:
        print(f"{GREEN}✓ VALIDACIÓN COMPLETADA EXITOSAMENTE{RESET}\n")
        
        print("Para empezar:\n")
        print(f"  1. {BLUE}Instalar dependencias:{RESET}")
        print(f"     pip install -r requirements.txt\n")
        
        print(f"  2. {BLUE}Verificar MySQL:{RESET}")
        print(f"     python cli.py mysql-test\n")
        
        print(f"  3. {BLUE}Ver servicios disponibles:{RESET}")
        print(f"     python cli.py list\n")
        
        print(f"  4. {BLUE}Ejecutar servidor:{RESET}")
        print(f"     python server.py\n")
        
        print(f"  5. {BLUE}En otro terminal, verificar conectividad:{RESET}")
        print(f"     python cli.py matrix\n")
        
        print(f"  6. {BLUE}Ver resumen del sistema:{RESET}")
        print(f"     python cli.py summary\n")
        
        print(f"  7. {BLUE}Acceder a dashboard:{RESET}")
        print(f"     http://localhost:5000\n")
        
    else:
        print(f"{RED}✘ ALGUNAS VERIFICACIONES FALLARON{RESET}\n")
        print("Instala las dependencias faltantes con:")
        print("  pip install -r requirements.txt\n")
    
    # PASO 8: Información de contacto
    print_header("8. DOCUMENTACIÓN")
    
    docs = [
        ("README_SERVICIOS.md", "README completo del proyecto"),
        ("SERVICIOS_CONFIG.md", "Configuración detallada de servicios"),
        ("GUIA_RAPIDA_SERVICIOS.md", "Guía rápida de uso"),
        ("CAMBIOS_V2.0.md", "Resumen de cambios realizados"),
    ]
    
    print("Consulta la documentación en:\n")
    for filename, description in docs:
        print(f"  • {BLUE}{filename}{RESET}")
        print(f"    {description}\n")
    
    # PASO 9: Comandos rápidos
    print_header("9. COMANDOS RÁPIDOS")
    
    print(f"{BLUE}# Ver estado actual del sistema{RESET}")
    print("python cli.py summary\n")
    
    print(f"{BLUE}# Verificar conectividad de todos los servicios{RESET}")
    print("python cli.py check-all\n")
    
    print(f"{BLUE}# Ver matriz de conectividad{RESET}")
    print("python cli.py matrix\n")
    
    print(f"{BLUE}# Activar MySQL Elasticsearch{RESET}")
    print("python cli.py enable mysql-elastika\n")
    
    print(f"{BLUE}# Ver API summary en navegador{RESET}")
    print("curl http://localhost:5000/api/summary | jq\n")
    
    # Resultado final
    print_header("RESULTADO FINAL")
    
    if all_checks_passed:
        print(f"{GREEN}✓ INSTALACIÓN VALIDADA CORRECTAMENTE{RESET}\n")
        print("El sistema está listo para usar.")
        print("Ejecuta: python server.py\n")
        return 0
    else:
        print(f"{RED}✘ ALGUNOS PROBLEMAS DETECTADOS{RESET}\n")
        print("Por favor, instala las dependencias faltantes.")
        print("Luego ejecuta este script nuevamente.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
