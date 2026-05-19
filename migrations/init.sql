-- ============================================================
-- DB Health Monitor — Script de inicialización de tablas
-- Ejecutar en PostgreSQL (db_health_monitor)
-- ============================================================

-- Datasources: fuentes de datos a monitorear
CREATE TABLE IF NOT EXISTS datasources (
    id           SERIAL       PRIMARY KEY,
    nombre       VARCHAR(100) NOT NULL,
    tipo_db      VARCHAR(20)  NOT NULL DEFAULT 'postgresql', -- postgresql | mysql
    host         VARCHAR(255) NOT NULL,
    puerto       INTEGER      NOT NULL DEFAULT 5432,
    usuario      VARCHAR(100) NOT NULL,
    password     TEXT         NOT NULL DEFAULT '',
    database     VARCHAR(100) NOT NULL,
    activa       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Snapshots de métricas por datasource
CREATE TABLE IF NOT EXISTS health_snapshots (
    id                  SERIAL       PRIMARY KEY,
    datasource_id       INTEGER      REFERENCES datasources(id) ON DELETE CASCADE,
    captured_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    max_connections     INTEGER      NOT NULL DEFAULT 0,
    threads_connected   INTEGER      NOT NULL DEFAULT 0,
    threads_running     INTEGER      NOT NULL DEFAULT 0,
    connection_pct      REAL         NOT NULL DEFAULT 0,
    qps                 REAL         NOT NULL DEFAULT 0,
    slow_queries        INTEGER      NOT NULL DEFAULT 0,
    cache_hit_ratio     REAL         NOT NULL DEFAULT 0,
    db_size_mb          REAL         NOT NULL DEFAULT 0,
    cpu_pct             REAL         NOT NULL DEFAULT 0,
    mem_pct             REAL         NOT NULL DEFAULT 0,
    status              VARCHAR(20)  NOT NULL DEFAULT 'OK'
);

-- Log de alertas por datasource
CREATE TABLE IF NOT EXISTS alert_log (
    id            SERIAL       PRIMARY KEY,
    datasource_id INTEGER      REFERENCES datasources(id) ON DELETE CASCADE,
    alerted_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    severity      VARCHAR(20)  NOT NULL DEFAULT 'INFO',
    metric_name   VARCHAR(100) NOT NULL,
    metric_value  VARCHAR(50)  NOT NULL,
    threshold     VARCHAR(100) NOT NULL,
    message       TEXT         NOT NULL
);

-- Historial de importaciones SQL
CREATE TABLE IF NOT EXISTS sql_imports (
    id                SERIAL       PRIMARY KEY,
    datasource_id     INTEGER      REFERENCES datasources(id) ON DELETE SET NULL,
    filename          VARCHAR(255) NOT NULL,
    uploaded_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    status            VARCHAR(20)  NOT NULL DEFAULT 'pending', -- success | failed | blocked
    statements_ok     INTEGER      NOT NULL DEFAULT 0,
    statements_failed INTEGER      NOT NULL DEFAULT 0,
    error_message     TEXT
);

-- Usuarios del sistema de monitoreo
CREATE TABLE IF NOT EXISTS auth_users (
    id             SERIAL        PRIMARY KEY,
    username       VARCHAR(100)  NOT NULL UNIQUE,
    password_hash  TEXT          NOT NULL,
    role           VARCHAR(20)   NOT NULL DEFAULT 'viewer',
    active         BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Usuarios iniciales para el sistema
INSERT INTO auth_users (username, password_hash, role, active)
VALUES
    ('ariana',  'scrypt:32768:8:1$bwQ1Pvgd1RSE4TEr$200c0dc79fa360f1d76a568d74b6900a61c4ac6cff44f953d8d5a4b8b1e2136f41729255489b1fcbb3f49b712c1a031f48b77dedddac2b18ad00e08b8b897786',  'viewer', TRUE),
    ('hashira', 'scrypt:32768:8:1$2qn63IXq4p8tGfls$b8c35fdd5215d6acc92a4b05c704cc502314844bc6551045adb439469ba0792c8b39d4b7b4fcb9106db88b18a5c5e19ecd07833a1505de8f0cc8bced15c3cf9e', 'admin',  TRUE)
ON CONFLICT (username) DO NOTHING;

-- Índices
CREATE INDEX IF NOT EXISTS idx_snap_ds_at  ON health_snapshots (datasource_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_ds_at ON alert_log        (datasource_id, alerted_at  DESC);
CREATE INDEX IF NOT EXISTS idx_import_ds   ON sql_imports      (datasource_id, uploaded_at DESC);
