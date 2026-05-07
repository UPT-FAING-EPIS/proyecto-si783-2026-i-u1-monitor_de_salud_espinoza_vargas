#!/bin/bash
set -euo pipefail

# INSTALACION_POSTGRESQL.sh
# Instala y configura PostgreSQL en Debian/Ubuntu (idempotente)

PG_USER="monitor"
PG_PASS="Monitor2026!@#"
PG_DB="db_health_monitor"

echo "Actualizando paquetes..."
apt-get update -y
apt-get install -y postgresql postgresql-contrib

# Determine config paths (supports multiple PG versions)
PG_CONF_DIR=$(ls -d /etc/postgresql/*/main 2>/dev/null | head -n1 || true)
if [ -z "$PG_CONF_DIR" ]; then
  echo "No se encontró /etc/postgresql/*/main, abortando."
  exit 1
fi
PG_CONF="$PG_CONF_DIR/postgresql.conf"
PG_HBA="$PG_CONF_DIR/pg_hba.conf"

echo "Configurando listen_addresses en $PG_CONF"
# set listen_addresses = '*'
# Use POSIX character classes to be portable
perl -pi -e "s/^[[:space:]]*#?[[:space:]]*listen_addresses.*\$/listen_addresses = '*'/;" "$PG_CONF"

echo "Permitiendo conexiones remotas en $PG_HBA"
# Add rule if not present (portable check)
if ! grep -q "host all all 0.0.0.0/0 md5" "$PG_HBA"; then
  echo "host all all 0.0.0.0/0 md5" >> "$PG_HBA"
fi

echo "Reiniciando servicio postgresql"
systemctl restart postgresql
systemctl enable postgresql

# Create database and user
sudo -u postgres psql -v ON_ERROR_STOP=1 <<-SQL || true
DO
	exbegin
		BEGIN;
	EXCEPTION WHEN others THEN
		END;
END
SQL

# Create user if not exists
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname = '$PG_USER'" | grep -q 1 || sudo -u postgres psql -c "CREATE USER $PG_USER WITH PASSWORD '$PG_PASS';"
# Create database if not exists
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname = '$PG_DB'" | grep -q 1 || sudo -u postgres psql -c "CREATE DATABASE $PG_DB OWNER $PG_USER;"
# Grant privileges (redundant if owner)
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE \"$PG_DB\" TO $PG_USER;"

# Optional: open firewall
if command -v ufw >/dev/null 2>&1; then
  echo "Abriendo puerto 5432 en ufw"
  ufw allow 5432/tcp || true
fi

echo "PostgreSQL instalado y configurado."
echo "Usuario: $PG_USER"
echo "Base de datos: $PG_DB"
echo "Contraseña: $PG_PASS"

echo "Comprueba desde tu máquina: psql -h <IP_VM> -U $PG_USER -d $PG_DB"
