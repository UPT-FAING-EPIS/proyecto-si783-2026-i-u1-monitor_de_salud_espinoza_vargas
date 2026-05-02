# Monitor de Salud MySQL — Proyecto

Descripción
- Aplicación en Python (Flask) que monitoriza métricas de MySQL y del host,
  almacena snapshots en MySQL y genera alertas. Provee una interfaz web
  (templates/static) y endpoints HTTP JSON para integraciones.

Estado del repositorio
- Backend: `server.py` (Flask) — recolector en hilo de fondo.
- Esquema de BD: `db_health_setup.sql` (crea `db_health_monitor` y tablas).
- Script de ayuda: `db_monitor.py` (si existe, para operaciones adicionales).
- Dependencias: `requirements.txt`.

Requisitos
- Python 3.10+ (probado con 3.11).
- MySQL 8+ o compatible.
- Paquetes en `requirements.txt` (instalación mostrada abajo).

Instalación (local / desarrollo)
1. Clonar el repositorio y entrar en la carpeta del proyecto.
2. Crear y activar un entorno virtual:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # PowerShell
```

```bash
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
```

3. Instalar dependencias:

```bash
pip install -r requirements.txt
```

Configuración
- Variables de entorno (opcionales — valores por defecto en `server.py`):
  - `MYSQL_HOST` (por defecto `localhost`)
  - `MYSQL_PORT` (por defecto `3306`)
  - `MYSQL_USER` (por defecto `root`)
  - `MYSQL_PASSWORD` (por defecto vacío)
  - `MYSQL_DATABASE` (por defecto `db_health_monitor`)

- Archivo `config.ini` (opcional): sección `[monitor]` y `[thresholds]`.
  El código usa `load_config()` para valores como `refresh_interval`.

Base de datos
- Puedes crear el esquema manualmente ejecutando `db_health_setup.sql` en MySQL:

```sql
-- En cliente mysql
SOURCE db_health_setup.sql;
```

- El servidor también intentará crear la base de datos y tablas automáticamente
  en el arranque mediante la función `init_db()` en `server.py`.

Ejecución
- Modo desarrollo / local:

```bash
python server.py
```

Después de arrancar verás: "Abre tu navegador en: http://localhost:5000".

- Producción (ejemplo con Gunicorn):

```bash
gunicorn -w 4 -b 0.0.0.0:8000 server:app
```

Rutas / Endpoints principales (JSON)
- `GET /api/health` — chequeo de salud (status de inicialización y errores).
- `GET /api/metrics` — devuelve métricas actuales, alerts y `last_update`.
- `GET /api/history` — últimos snapshots guardados en MySQL (histórico).
- `GET /api/alerts/history` — historial de alertas guardadas.
- `GET /api/config` — configuración activa (intervalos y thresholds).

Interfaz web
- La UI está en `templates/index.html` y recursos en `static/`.
  Abrir `http://localhost:5000` mostrará el dashboard que consume los
  endpoints JSON listados arriba.

Notas importantes
- `psutil` se utiliza para métricas del host; si no está disponible el
  servicio seguirá funcionando pero con métricas limitadas.
- `mysql-connector-python` es requerido para conectar con MySQL. Asegúrate
  de que las credenciales y el acceso desde el host donde corre la app
  están correctamente configurados.

Ejemplos rápidos
- Iniciar con variables de entorno (PowerShell):

```powershell
$env:MYSQL_HOST = '127.0.0.1'
$env:MYSQL_USER = 'root'
$env:MYSQL_PASSWORD = 'tu_pass'
python server.py
```

- Consumir métricas con `curl`:

```bash
curl http://localhost:5000/api/metrics
```

Desarrollo y contribución
- Abrir issues y PRs para mejoras.
- Encontrarás la lógica de recolección en `server.py` y el esquema SQL
  en `db_health_setup.sql`.

Archivos clave
- `server.py` — aplicación Flask, recolector y endpoints.
- `db_health_setup.sql` — script SQL para crear la BD y tablas.
- `requirements.txt` — dependencias del proyecto.
- `templates/index.html` — interfaz web.
- `static/` — recursos JS/CSS (dashboard.js, style.css).

--
Este README fue generado para ofrecer una guía completa de instalación y
uso del sistema.
`config.ini` de ejemplo.

[![Review Assignment Due Date](https://classroom.github.com/assets/deadline-readme-button-22041afd0340ce965d47ae6ef1cefeee28c7c493a6346c4f15d667ab976d596c.svg)](https://classroom.github.com/a/O8I-PXKI)
[![Open in Codespaces](https://classroom.github.com/assets/launch-codespace-2972f46106e565e64193e422d61a12cf1da4916b45550586e14ef0a7c637dd04.svg)](https://classroom.github.com/open-in-codespaces?assignment_repo_id=23223050)
# proyecto-formatos-01
