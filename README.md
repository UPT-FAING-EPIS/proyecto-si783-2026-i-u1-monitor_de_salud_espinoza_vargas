# 🗄️ MSBDD - Monitor de Salud de Base de Datos

**Universidad Privada de Tacna · EPIS · Base de Datos II**  
Docente: Patrick José Cuadros Quiroga  
Integrantes: Espinoza Castañeda Ariana · Vargas Candia Hashira

---

## 🚀 Inicio Rápido (Un solo comando)

```bash
# Clonar/copiar el proyecto y ejecutar:
docker compose up --build
```

Luego abrir en el navegador:

| Servicio | URL |
|---|---|
| 📊 **Dashboard Visual** | http://localhost:8050 |
| 🔌 **API REST (Swagger)** | http://localhost:8000/docs |
| 🗄️ **PostgreSQL** | localhost:5432 |

---

## 📁 Estructura del Proyecto

```
msbdd/
├── docker-compose.yml          ← Orquestador principal
├── postgres-init/
│   └── 01_init.sql             ← BD de prueba con datos simulados
├── backend/                    ← FastAPI + SQLAlchemy + psycopg2
│   ├── main.py                 ← Endpoints REST + Scheduler
│   ├── config.py               ← Configuración y umbrales
│   ├── base_datos.py           ← Pool de conexiones
│   ├── modelos.py              ← Esquemas Pydantic
│   ├── servicio_metricas.py    ← RF01: Captura de KPIs
│   ├── servicio_diagnostico.py ← RF04: Diagnóstico avanzado
│   ├── motor_alertas.py        ← RF03: Sistema de alertas
│   ├── repositorio_metricas.py ← RF05: Histórico en memoria
│   └── Dockerfile
└── dashboard/                  ← Dash + Plotly
    ├── app.py                  ← Dashboard interactivo
    └── Dockerfile
```

---

## 🔧 Requerimientos Implementados

| ID | Requerimiento | Estado |
|---|---|---|
| RF01 | Captura de KPIs (CPU, memoria, conexiones, caché) | ✅ |
| RF02 | Dashboard visual con semáforos de riesgo | ✅ |
| RF03 | Alertas automáticas por umbrales | ✅ |
| RF04 | Consultas lentas, bloqueos, índices no usados | ✅ |
| RF05 | Histórico y estadísticas de tendencia | ✅ |

### Reglas de Negocio implementadas
- ✅ Las alertas se confirman solo tras **2 ciclos consecutivos** superando el umbral (evita falsos positivos)
- ✅ El sistema accede **únicamente** a vistas `pg_catalog` — nunca a datos de usuario
- ✅ Overhead de monitoreo **< 2%** en el motor

---

## ⚙️ Configuración de Umbrales

Edita las variables en `docker-compose.yml` bajo el servicio `backend`:

```yaml
UMBRAL_CPU: 85            # % de CPU para disparar alerta
UMBRAL_MEMORIA: 80        # % de RAM para disparar alerta
UMBRAL_CONEXIONES: 80     # Número de conexiones activas
UMBRAL_CONSULTA_LENTA: 3  # Segundos para considerar consulta lenta
INTERVALO_RECOLECCION: 30 # Segundos entre recolecciones
CICLOS_CONFIRMACION: 2    # Ciclos para confirmar una alerta
```

---

## 🔌 Endpoints de la API

```
GET /                       → Estado general del sistema
GET /metricas               → KPIs actuales en tiempo real
GET /metricas/historial     → Histórico (últimos N registros)
GET /metricas/resumen       → Estadísticas agregadas
GET /alertas                → Alertas activas
GET /alertas/historial      → Historial de alertas
GET /diagnostico            → Diagnóstico avanzado completo
```

Documentación interactiva: http://localhost:8000/docs

---

## 🛠️ Comandos Útiles

```bash
# Ver logs en tiempo real
docker compose logs -f backend
docker compose logs -f dashboard

# Reiniciar solo el backend
docker compose restart backend

# Conectar a PostgreSQL directamente
docker exec -it msbdd_postgres psql -U msbdd_user -d msbdd_db

# Detener todo
docker compose down

# Detener y eliminar volúmenes (reset completo)
docker compose down -v
```

---

## 🏗️ Stack Tecnológico

| Componente | Tecnología |
|---|---|
| Backend | Python 3.11 + FastAPI + SQLAlchemy + psycopg2 |
| Dashboard | Dash + Plotly + Bootstrap |
| Motor BD | PostgreSQL 15 |
| Infraestructura | Docker + Docker Compose |
| Scheduler | APScheduler |
