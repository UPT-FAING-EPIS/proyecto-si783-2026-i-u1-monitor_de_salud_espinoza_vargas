#!/bin/bash
set -e

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║     INSTALACIÓN MARIADB - ELASTIKA VM                         ║"
echo "║     Sistema: Debian 12 | Host: 38.250.116.71                 ║"
echo "╚════════════════════════════════════════════════════════════════╝"

# [1/5] Actualizar sistema
echo ""
echo "[1/5] Actualizando sistema..."
apt-get update -qq
apt-get upgrade -y -qq

# [2/5] Instalar MariaDB (la versión correcta para Debian 12)
echo ""
echo "[2/5] Instalando MariaDB..."
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mariadb-server mariadb-client

# [3/5] Iniciar MariaDB
echo ""
echo "[3/5] Iniciando MariaDB..."
systemctl start mariadb
systemctl enable mariadb

# Esperar a que MariaDB esté completamente listo
sleep 5
echo "Esperando a que MariaDB esté listo..."
for i in {1..30}; do
    if mysql --defaults-file=/etc/mysql/debian.cnf -e "SELECT 1" &>/dev/null; then
        echo "✓ MariaDB está listo"
        break
    fi
    echo "Intento $i/30..."
    sleep 2
done

# [4/5] Configurar acceso remoto
echo ""
echo "[4/5] Configurando acceso remoto..."
# La ruta correcta para MariaDB en Debian 12
CONFIG_FILE="/etc/mysql/mariadb.conf.d/50-server.cnf"

if [ -f "$CONFIG_FILE" ]; then
    cp "$CONFIG_FILE" "$CONFIG_FILE.bak"
    sed -i 's/^bind-address.*/bind-address = 0.0.0.0/' "$CONFIG_FILE"
    systemctl restart mariadb
    sleep 3
    echo "✓ Acceso remoto configurado"
else
    echo "✗ Archivo de configuración no encontrado: $CONFIG_FILE"
    exit 1
fi

# Preparar MariaDB en modo temporal sin permisos para crear usuarios y tablas
echo ""
echo "Preparando acceso administrativo temporal..."
systemctl stop mariadb
install -d -o mysql -g mysql /run/mysqld
mysqld_safe --skip-grant-tables --skip-networking --socket=/run/mysqld/mysqld.sock >/tmp/mariadb-bootstrap.log 2>&1 &
BOOTSTRAP_PID=$!

for i in {1..30}; do
    if mysql --protocol=socket -uroot --socket=/run/mysqld/mysqld.sock -e "SELECT 1" &>/dev/null; then
        echo "✓ Modo administrativo temporal listo"
        break
    fi
    echo "Esperando arranque temporal... intento $i/30"
    sleep 2
done

# [5/5] Configurar base de datos y usuarios
echo ""
echo "[5/5] Configurando base de datos..."

# Generar contraseñas seguras
ROOT_PASSWORD="UPT_Monitor_2026!@#"
MONITOR_PASSWORD="Monitor2026!@#"
READONLY_PASSWORD="ReadOnly2026!@#"

# Crear la base de datos y las tablas mientras MariaDB está en modo temporal
cat > /tmp/bootstrap.sql << 'SQLEOF'
-- Crear base de datos
CREATE DATABASE IF NOT EXISTS db_health_monitor CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE db_health_monitor;

-- Crear tabla de snapshots
CREATE TABLE IF NOT EXISTS health_snapshots (
    id INT AUTO_INCREMENT PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    cpu_percent FLOAT,
    memory_percent FLOAT,
    disk_percent FLOAT,
    network_sent BIGINT,
    network_recv BIGINT,
    INDEX idx_timestamp (timestamp),
    INDEX idx_date (DATE(timestamp))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Crear tabla de alertas
CREATE TABLE IF NOT EXISTS alert_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    alert_type VARCHAR(50),
    severity VARCHAR(20),
    message TEXT,
    resolved BOOLEAN DEFAULT FALSE,
    INDEX idx_timestamp (timestamp),
    INDEX idx_severity (severity)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Crear tabla de conexiones activas
CREATE TABLE IF NOT EXISTS active_connections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    server_id VARCHAR(100),
    connection_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat DATETIME DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) DEFAULT 'active',
    INDEX idx_server_id (server_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Crear tabla de eventos del servidor
CREATE TABLE IF NOT EXISTS server_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    server_id VARCHAR(100),
    event_type VARCHAR(50),
    event_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    details TEXT,
    INDEX idx_server_id (server_id),
    INDEX idx_event_type (event_type),
    INDEX idx_event_time (event_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Habilitar contraseña para root y permitir el paso a modo normal
SET PASSWORD FOR 'root'@'localhost' = PASSWORD('UPT_Monitor_2026!@#');
FLUSH PRIVILEGES;

-- Crear usuarios
SQLEOF

# Ejecutar el archivo bootstrap SQL
if mysql --protocol=socket -uroot --socket=/run/mysqld/mysqld.sock < /tmp/bootstrap.sql; then
    echo "✓ Base de datos y tablas creadas"
    rm /tmp/bootstrap.sql
else
    echo "✗ Error configurando la base de datos"
    cat /tmp/bootstrap.sql
    exit 1
fi

# Cerrar completamente el modo temporal antes de reiniciar el servicio normal
mysqladmin --protocol=socket -uroot --socket=/run/mysqld/mysqld.sock shutdown >/dev/null 2>&1 || true
kill "$BOOTSTRAP_PID" >/dev/null 2>&1 || true
wait "$BOOTSTRAP_PID" >/dev/null 2>&1 || true
pkill -9 -f 'mariadbd.*skip-grant-tables' >/dev/null 2>&1 || true
pkill -9 -f 'mysqld_safe.*skip-grant-tables' >/dev/null 2>&1 || true
sleep 3
systemctl start mariadb

# Esperar y conectar con la nueva contraseña de root para crear usuarios y permisos
for i in {1..30}; do
    if mysql -uroot -p"$ROOT_PASSWORD" -e "SELECT 1" &>/dev/null; then
        echo "✓ MariaDB reiniciado con autenticación normal"
        break
    fi
    sleep 2
done

cat > /tmp/users.sql << 'SQLEOF'
CREATE USER IF NOT EXISTS 'monitor'@'%' IDENTIFIED BY 'Monitor2026!@#';
CREATE USER IF NOT EXISTS 'readonly'@'%' IDENTIFIED BY 'ReadOnly2026!@#';
CREATE USER IF NOT EXISTS 'monitor'@'localhost' IDENTIFIED BY 'Monitor2026!@#';
CREATE USER IF NOT EXISTS 'readonly'@'localhost' IDENTIFIED BY 'ReadOnly2026!@#';

GRANT ALL PRIVILEGES ON db_health_monitor.* TO 'monitor'@'%';
GRANT ALL PRIVILEGES ON db_health_monitor.* TO 'monitor'@'localhost';
GRANT SELECT ON db_health_monitor.* TO 'readonly'@'%';
GRANT SELECT ON db_health_monitor.* TO 'readonly'@'localhost';

FLUSH PRIVILEGES;
SQLEOF

if mysql -uroot -p"$ROOT_PASSWORD" < /tmp/users.sql; then
    echo "✓ Base de datos y usuarios configurados exitosamente"
    rm /tmp/users.sql
else
    echo "✗ Error creando usuarios y permisos"
    cat /tmp/users.sql
    exit 1
fi

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║          ✓ INSTALACIÓN COMPLETADA EXITOSAMENTE               ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "📊 INFORMACIÓN DE CONEXIÓN:"
echo "   Host: 38.250.116.71"
echo "   Puerto: 3306"
echo "   Base de datos: db_health_monitor"
echo ""
echo "👤 CREDENCIALES:"
echo "   Usuario monitor:"
echo "      Usuario: monitor"
echo "      Contraseña: Monitor2026!@#"
echo "   Usuario readonly:"
echo "      Usuario: readonly"
echo "      Contraseña: ReadOnly2026!@#"
echo ""
echo "✓ El servicio MariaDB está habilitado y se iniciará automáticamente"
echo ""
