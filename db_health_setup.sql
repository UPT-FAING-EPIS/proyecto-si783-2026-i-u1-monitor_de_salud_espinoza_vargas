-- ============================================================
--  SCRIPT DE CONFIGURACIÓN - MONITOR DE SALUD DB
--  Azure SQL Database (T-SQL)
--  Ejecutar con sqlcmd o Azure Data Studio
-- ============================================================

-- ============================================================
-- 1. Tabla principal de snapshots de métricas
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'health_snapshots')
BEGIN
    CREATE TABLE health_snapshots (
        id                              BIGINT IDENTITY(1,1) PRIMARY KEY,
        captured_at                     DATETIME2(3)    NOT NULL DEFAULT SYSDATETIME(),
        -- Conexiones
        max_connections                 INT             NOT NULL DEFAULT 0,
        threads_connected               INT             NOT NULL DEFAULT 0,
        threads_running                 INT             NOT NULL DEFAULT 0,
        threads_cached                  INT             NOT NULL DEFAULT 0,
        threads_created                 BIGINT          NOT NULL DEFAULT 0,
        connection_pct                  DECIMAL(5,2)    NOT NULL DEFAULT 0.00,
        -- Rendimiento
        questions                       BIGINT          NOT NULL DEFAULT 0,
        qps                             DECIMAL(10,2)   NOT NULL DEFAULT 0.00,
        slow_queries                    BIGINT          NOT NULL DEFAULT 0,
        -- Buffer Pool (equivalente InnoDB → simulado en monitor Python)
        innodb_buffer_pool_size         BIGINT          NOT NULL DEFAULT 0,
        innodb_buffer_pool_reads        BIGINT          NOT NULL DEFAULT 0,
        innodb_buffer_pool_read_reqs    BIGINT          NOT NULL DEFAULT 0,
        innodb_hit_ratio                DECIMAL(5,2)    NOT NULL DEFAULT 0.00,
        innodb_buffer_pool_pages_total  INT             NOT NULL DEFAULT 0,
        innodb_buffer_pool_pages_free   INT             NOT NULL DEFAULT 0,
        innodb_buffer_pool_pages_dirty  INT             NOT NULL DEFAULT 0,
        -- Uptime
        uptime_seconds                  BIGINT          NOT NULL DEFAULT 0,
        -- Estado general
        status                          NVARCHAR(10)    NOT NULL DEFAULT 'OK'
                                        CONSTRAINT chk_status CHECK (status IN ('OK','WARNING','CRITICAL')),
        notes                           NVARCHAR(MAX)   NULL
    );
    CREATE INDEX idx_captured_at ON health_snapshots (captured_at DESC);
    CREATE INDEX idx_status      ON health_snapshots (status);
    PRINT 'Tabla health_snapshots creada.';
END
ELSE
    PRINT 'Tabla health_snapshots ya existe.';
GO

-- ============================================================
-- 2. Tabla de alertas generadas por el monitor
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'alert_log')
BEGIN
    CREATE TABLE alert_log (
        id              BIGINT IDENTITY(1,1) PRIMARY KEY,
        alerted_at      DATETIME2(3)    NOT NULL DEFAULT SYSDATETIME(),
        severity        NVARCHAR(10)    NOT NULL DEFAULT 'INFO'
                        CONSTRAINT chk_severity CHECK (severity IN ('INFO','WARNING','CRITICAL')),
        metric_name     NVARCHAR(100)   NOT NULL,
        metric_value    NVARCHAR(200)   NOT NULL,
        threshold       NVARCHAR(200)   NOT NULL,
        message         NVARCHAR(MAX)   NOT NULL
    );
    CREATE INDEX idx_alerted_at ON alert_log (alerted_at DESC);
    CREATE INDEX idx_severity   ON alert_log (severity);
    PRINT 'Tabla alert_log creada.';
END
ELSE
    PRINT 'Tabla alert_log ya existe.';
GO

-- ============================================================
-- 3. Tabla de consultas lentas detectadas
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'slow_query_log')
BEGIN
    CREATE TABLE slow_query_log (
        id              BIGINT IDENTITY(1,1) PRIMARY KEY,
        detected_at     DATETIME2(3)    NOT NULL DEFAULT SYSDATETIME(),
        query_time      DECIMAL(10,6)   NOT NULL DEFAULT 0.000000,
        lock_time       DECIMAL(10,6)   NOT NULL DEFAULT 0.000000,
        rows_sent       INT             NOT NULL DEFAULT 0,
        rows_examined   INT             NOT NULL DEFAULT 0,
        db_name         NVARCHAR(128)   NULL,
        query_digest    NVARCHAR(MAX)   NOT NULL
    );
    CREATE INDEX idx_detected_at ON slow_query_log (detected_at DESC);
    CREATE INDEX idx_query_time  ON slow_query_log (query_time DESC);
    PRINT 'Tabla slow_query_log creada.';
END
ELSE
    PRINT 'Tabla slow_query_log ya existe.';
GO

-- ============================================================
-- 4. Tabla de estadísticas por base de datos
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'database_stats')
BEGIN
    CREATE TABLE database_stats (
        id              BIGINT IDENTITY(1,1) PRIMARY KEY,
        captured_at     DATETIME2(3)    NOT NULL DEFAULT SYSDATETIME(),
        schema_name     NVARCHAR(128)   NOT NULL,
        size_mb         DECIMAL(15,3)   NOT NULL DEFAULT 0.000,
        table_count     INT             NOT NULL DEFAULT 0,
        row_count       BIGINT          NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_db_captured_at ON database_stats (captured_at DESC);
    CREATE INDEX idx_schema         ON database_stats (schema_name);
    PRINT 'Tabla database_stats creada.';
END
ELSE
    PRINT 'Tabla database_stats ya existe.';
GO

-- ============================================================
-- 5. Vista: Resumen de últimos snapshots
-- ============================================================
IF EXISTS (SELECT * FROM sys.views WHERE name = 'v_health_summary')
    DROP VIEW v_health_summary;
GO
CREATE VIEW v_health_summary AS
SELECT
    id,
    captured_at,
    threads_connected,
    threads_running,
    CAST(connection_pct AS NVARCHAR) + '%'      AS uso_conexiones,
    ROUND(qps, 2)                                AS queries_por_segundo,
    slow_queries,
    CAST(innodb_hit_ratio AS NVARCHAR) + '%'    AS cache_hit_ratio,
    ROUND(CAST(innodb_buffer_pool_size AS FLOAT) / 1024 / 1024, 0) AS buffer_pool_mb,
    CAST(uptime_seconds / 86400 AS NVARCHAR) + 'd ' +
    CAST((uptime_seconds % 86400) / 3600 AS NVARCHAR) + 'h ' +
    CAST((uptime_seconds % 3600) / 60 AS NVARCHAR) + 'm'  AS uptime_formateado,
    status
FROM health_snapshots;
GO
PRINT 'Vista v_health_summary creada.';
GO

-- ============================================================
-- 6. Vista: Alertas recientes (últimas 24h)
-- ============================================================
IF EXISTS (SELECT * FROM sys.views WHERE name = 'v_recent_alerts')
    DROP VIEW v_recent_alerts;
GO
CREATE VIEW v_recent_alerts AS
SELECT
    id,
    alerted_at,
    severity,
    metric_name,
    metric_value,
    threshold,
    message
FROM alert_log
WHERE alerted_at >= DATEADD(HOUR, -24, SYSDATETIME());
GO
PRINT 'Vista v_recent_alerts creada.';
GO

-- ============================================================
-- 7. Verificar instalación
-- ============================================================
PRINT 'Instalación completada exitosamente.';
SELECT name AS tabla FROM sys.tables ORDER BY name;
GO
