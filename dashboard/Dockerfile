FROM python:3.11-slim

LABEL maintainer="EPIS-UPT"
LABEL descripcion="MSBDD Dashboard - Dash + Plotly"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV API_URL=http://backend:8000 \
    INTERVALO_DASHBOARD_MS=15000 \
    UMBRAL_CPU=85 \
    UMBRAL_MEMORIA=80 \
    UMBRAL_CONEXIONES=80 \
    PYTHONUNBUFFERED=1

EXPOSE 8050

CMD ["python", "app.py"]
