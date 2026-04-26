FROM python:3.11-slim

# Metadatos
LABEL maintainer="EPIS-UPT"
LABEL descripcion="MSBDD Backend - FastAPI + SQLAlchemy + psycopg2"

WORKDIR /app

# Dependencias del sistema necesarias para psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY . .

# Variables de entorno por defecto (sobreescritas por docker-compose)
ENV BD_HOST=postgres \
    BD_PUERTO=5432 \
    BD_NOMBRE=msbdd_db \
    BD_USUARIO=msbdd_user \
    BD_CONTRASENA=msbdd_pass \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Iniciar con uvicorn en modo producción
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
