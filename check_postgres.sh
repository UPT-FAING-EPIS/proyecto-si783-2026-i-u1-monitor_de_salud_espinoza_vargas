#!/bin/bash
set -e

echo "SERVICE: $(systemctl is-active postgresql || true)"
ss -ltnp | grep 5432 || true
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='monitor';"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='db_health_monitor';"
