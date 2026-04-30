-- ============================================================
--  SCRIPT DE CONFIGURACIÓN - MONITOR DE SALUD MYSQL
--  Ejecutar como usuario root o con privilegios suficientes
-- ============================================================

-- 1. Crear la base de datos para el monitor
CREATE DATABASE IF NOT EXISTS `db_health_monitor`
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE `db_health_monitor`;

-- ============================================================
-- 2. Tabla principal de snapshots de métricas
-- ============================================================
CREATE TABLE IF NOT EXISTS `health_snapshots` (
    `id`                    BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `captured_at`           DATETIME(3)    NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    -- Conexiones
    `max_connections`       INT UNSIGNED   NOT NULL DEFAULT 0,
    `threads_connected`     INT UNSIGNED   NOT NULL DEFAULT 0,
    `threads_running`       INT UNSIGNED   NOT NULL DEFAULT 0,
    `threads_cached`        INT UNSIGNED   NOT NULL DEFAULT 0,
    `threads_created`       BIGINT UNSIGNED NOT NULL DEFAULT 0,
    `connection_pct`        DECIMAL(5,2)   NOT NULL DEFAULT 0.00 COMMENT 'Porcentaje de conexiones usadas',
    -- Rendimiento
    `questions`             BIGINT UNSIGNED NOT NULL DEFAULT 0,
    `qps`                   DECIMAL(10,2)  NOT NULL DEFAULT 0.00 COMMENT 'Queries por segundo',
    `slow_queries`          BIGINT UNSIGNED NOT NULL DEFAULT 0,
    -- InnoDB Buffer Pool
    `innodb_buffer_pool_size`      BIGINT UNSIGNED NOT NULL DEFAULT 0,
    `innodb_buffer_pool_reads`     BIGINT UNSIGNED NOT NULL DEFAULT 0,
    `innodb_buffer_pool_read_reqs` BIGINT UNSIGNED NOT NULL DEFAULT 0,
    `innodb_hit_ratio`             DECIMAL(5,2)   NOT NULL DEFAULT 0.00,
    -- Memoria
    `innodb_buffer_pool_pages_total`  INT UNSIGNED NOT NULL DEFAULT 0,
    `innodb_buffer_pool_pages_free`   INT UNSIGNED NOT NULL DEFAULT 0,
    `innodb_buffer_pool_pages_dirty`  INT UNSIGNED NOT NULL DEFAULT 0,
    -- Uptime
    `uptime_seconds`        BIGINT UNSIGNED NOT NULL DEFAULT 0,
    -- Estado general
    `status`                ENUM('OK','WARNING','CRITICAL') NOT NULL DEFAULT 'OK',
    `notes`                 TEXT           NULL,
    INDEX `idx_captured_at` (`captured_at`),
    INDEX `idx_status` (`status`)
) ENGINE=InnoDB COMMENT='Historial de snapshots de salud del servidor MySQL';

-- ============================================================
-- 3. Tabla de alertas generadas por el monitor
-- ============================================================
CREATE TABLE IF NOT EXISTS `alert_log` (
    `id`            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `alerted_at`    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `severity`      ENUM('INFO','WARNING','CRITICAL') NOT NULL DEFAULT 'INFO',
    `metric_name`   VARCHAR(100)    NOT NULL,
    `metric_value`  VARCHAR(200)    NOT NULL,
    `threshold`     VARCHAR(200)    NOT NULL,
    `message`       TEXT            NOT NULL,
    INDEX `idx_alerted_at` (`alerted_at`),
    INDEX `idx_severity` (`severity`)
) ENGINE=InnoDB COMMENT='Registro de alertas generadas por el monitor';

-- ============================================================
-- 4. Tabla para registro de consultas lentas detectadas
-- ============================================================
CREATE TABLE IF NOT EXISTS `slow_query_log` (
    `id`                BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `detected_at`       DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `query_time`        DECIMAL(10,6)   NOT NULL DEFAULT 0.000000,
    `lock_time`         DECIMAL(10,6)   NOT NULL DEFAULT 0.000000,
    `rows_sent`         INT UNSIGNED    NOT NULL DEFAULT 0,
    `rows_examined`     INT UNSIGNED    NOT NULL DEFAULT 0,
    `db_name`           VARCHAR(64)     NULL,
    `query_digest`      TEXT            NOT NULL,
    INDEX `idx_detected_at` (`detected_at`),
    INDEX `idx_query_time`  (`query_time`)
) ENGINE=InnoDB COMMENT='Consultas lentas registradas por el monitor';

-- ============================================================
-- 5. Tabla de estadísticas por base de datos
-- ============================================================
CREATE TABLE IF NOT EXISTS `database_stats` (
    `id`            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    `captured_at`   DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `schema_name`   VARCHAR(64)     NOT NULL,
    `size_mb`       DECIMAL(15,3)   NOT NULL DEFAULT 0.000,
    `table_count`   INT UNSIGNED    NOT NULL DEFAULT 0,
    `row_count`     BIGINT UNSIGNED NOT NULL DEFAULT 0,
    INDEX `idx_captured_at` (`captured_at`),
    INDEX `idx_schema`      (`schema_name`)
) ENGINE=InnoDB COMMENT='Estadísticas de tamaño por base de datos';

-- ============================================================
-- 6. Procedimiento: Capturar snapshot completo
-- ============================================================
DELIMITER $$

DROP PROCEDURE IF EXISTS `sp_capture_health_snapshot` $$

CREATE PROCEDURE `sp_capture_health_snapshot`()
BEGIN
    DECLARE v_max_conn           INT UNSIGNED DEFAULT 0;
    DECLARE v_threads_conn       INT UNSIGNED DEFAULT 0;
    DECLARE v_threads_run        INT UNSIGNED DEFAULT 0;
    DECLARE v_threads_cached     INT UNSIGNED DEFAULT 0;
    DECLARE v_threads_created    BIGINT UNSIGNED DEFAULT 0;
    DECLARE v_questions          BIGINT UNSIGNED DEFAULT 0;
    DECLARE v_slow_queries       BIGINT UNSIGNED DEFAULT 0;
    DECLARE v_uptime             BIGINT UNSIGNED DEFAULT 0;
    DECLARE v_bp_size            BIGINT UNSIGNED DEFAULT 0;
    DECLARE v_bp_reads           BIGINT UNSIGNED DEFAULT 0;
    DECLARE v_bp_read_reqs       BIGINT UNSIGNED DEFAULT 0;
    DECLARE v_bp_pages_total     INT UNSIGNED DEFAULT 0;
    DECLARE v_bp_pages_free      INT UNSIGNED DEFAULT 0;
    DECLARE v_bp_pages_dirty     INT UNSIGNED DEFAULT 0;
    DECLARE v_hit_ratio          DECIMAL(5,2) DEFAULT 0.00;
    DECLARE v_conn_pct           DECIMAL(5,2) DEFAULT 0.00;
    DECLARE v_qps                DECIMAL(10,2) DEFAULT 0.00;
    DECLARE v_status             ENUM('OK','WARNING','CRITICAL') DEFAULT 'OK';

    -- Obtener variables del servidor
    SELECT VARIABLE_VALUE INTO v_max_conn         FROM performance_schema.global_variables WHERE VARIABLE_NAME = 'max_connections';
    SELECT VARIABLE_VALUE INTO v_threads_conn     FROM performance_schema.global_status   WHERE VARIABLE_NAME = 'Threads_connected';
    SELECT VARIABLE_VALUE INTO v_threads_run      FROM performance_schema.global_status   WHERE VARIABLE_NAME = 'Threads_running';
    SELECT VARIABLE_VALUE INTO v_threads_cached   FROM performance_schema.global_status   WHERE VARIABLE_NAME = 'Threads_cached';
    SELECT VARIABLE_VALUE INTO v_threads_created  FROM performance_schema.global_status   WHERE VARIABLE_NAME = 'Threads_created';
    SELECT VARIABLE_VALUE INTO v_questions        FROM performance_schema.global_status   WHERE VARIABLE_NAME = 'Questions';
    SELECT VARIABLE_VALUE INTO v_slow_queries     FROM performance_schema.global_status   WHERE VARIABLE_NAME = 'Slow_queries';
    SELECT VARIABLE_VALUE INTO v_uptime           FROM performance_schema.global_status   WHERE VARIABLE_NAME = 'Uptime';
    SELECT VARIABLE_VALUE INTO v_bp_size          FROM performance_schema.global_variables WHERE VARIABLE_NAME = 'innodb_buffer_pool_size';
    SELECT VARIABLE_VALUE INTO v_bp_reads         FROM performance_schema.global_status   WHERE VARIABLE_NAME = 'Innodb_buffer_pool_reads';
    SELECT VARIABLE_VALUE INTO v_bp_read_reqs     FROM performance_schema.global_status   WHERE VARIABLE_NAME = 'Innodb_buffer_pool_read_requests';
    SELECT VARIABLE_VALUE INTO v_bp_pages_total   FROM performance_schema.global_status   WHERE VARIABLE_NAME = 'Innodb_buffer_pool_pages_total';
    SELECT VARIABLE_VALUE INTO v_bp_pages_free    FROM performance_schema.global_status   WHERE VARIABLE_NAME = 'Innodb_buffer_pool_pages_free';
    SELECT VARIABLE_VALUE INTO v_bp_pages_dirty   FROM performance_schema.global_status   WHERE VARIABLE_NAME = 'Innodb_buffer_pool_pages_dirty';

    -- Calcular métricas derivadas
    SET v_conn_pct = IF(v_max_conn > 0, (v_threads_conn / v_max_conn) * 100, 0);
    SET v_qps      = IF(v_uptime > 0, v_questions / v_uptime, 0);
    SET v_hit_ratio = IF(v_bp_read_reqs > 0,
                        (1 - (v_bp_reads / v_bp_read_reqs)) * 100, 100);

    -- Determinar estado
    IF v_conn_pct >= 90 OR v_hit_ratio < 80 THEN
        SET v_status = 'CRITICAL';
    ELSEIF v_conn_pct >= 70 OR v_hit_ratio < 90 THEN
        SET v_status = 'WARNING';
    ELSE
        SET v_status = 'OK';
    END IF;

    -- Insertar snapshot
    INSERT INTO `health_snapshots` (
        max_connections, threads_connected, threads_running, threads_cached,
        threads_created, connection_pct, questions, qps, slow_queries,
        innodb_buffer_pool_size, innodb_buffer_pool_reads, innodb_buffer_pool_read_reqs,
        innodb_hit_ratio, innodb_buffer_pool_pages_total, innodb_buffer_pool_pages_free,
        innodb_buffer_pool_pages_dirty, uptime_seconds, status
    ) VALUES (
        v_max_conn, v_threads_conn, v_threads_run, v_threads_cached,
        v_threads_created, v_conn_pct, v_questions, v_qps, v_slow_queries,
        v_bp_size, v_bp_reads, v_bp_read_reqs,
        v_hit_ratio, v_bp_pages_total, v_bp_pages_free,
        v_bp_pages_dirty, v_uptime, v_status
    );

    SELECT 'Snapshot capturado exitosamente.' AS resultado;
END $$

DELIMITER ;

-- ============================================================
-- 7. Vista: Resumen de últimos snapshots
-- ============================================================
CREATE OR REPLACE VIEW `v_health_summary` AS
SELECT
    id,
    captured_at,
    threads_connected,
    threads_running,
    CONCAT(connection_pct, '%') AS uso_conexiones,
    ROUND(qps, 2)               AS queries_por_segundo,
    slow_queries,
    CONCAT(innodb_hit_ratio, '%') AS cache_hit_ratio,
    ROUND(innodb_buffer_pool_size / 1024 / 1024, 0) AS buffer_pool_mb,
    CONCAT(FLOOR(uptime_seconds / 86400), 'd ',
           FLOOR((uptime_seconds % 86400) / 3600), 'h ',
           FLOOR((uptime_seconds % 3600) / 60), 'm') AS uptime_formateado,
    status
FROM `health_snapshots`
ORDER BY captured_at DESC;

-- ============================================================
-- 8. Vista: Historial de alertas recientes (últimas 24h)
-- ============================================================
CREATE OR REPLACE VIEW `v_recent_alerts` AS
SELECT
    id,
    alerted_at,
    severity,
    metric_name,
    metric_value,
    threshold,
    message
FROM `alert_log`
WHERE alerted_at >= NOW() - INTERVAL 24 HOUR
ORDER BY alerted_at DESC;

-- ============================================================
-- 9. Usuario dedicado para el monitor (permisos mínimos)
-- ============================================================
-- NOTA: Ajustar 'monitor_password_2024' por una contraseña segura
-- y reemplazar 'localhost' por el host desde donde corre Python.

-- DROP USER IF EXISTS 'db_monitor'@'localhost';
-- CREATE USER 'db_monitor'@'localhost' IDENTIFIED BY 'monitor_password_2024';
-- GRANT SELECT ON performance_schema.* TO 'db_monitor'@'localhost';
-- GRANT SELECT ON information_schema.*  TO 'db_monitor'@'localhost';
-- GRANT SELECT, INSERT ON db_health_monitor.* TO 'db_monitor'@'localhost';
-- GRANT EXECUTE ON PROCEDURE db_health_monitor.sp_capture_health_snapshot TO 'db_monitor'@'localhost';
-- GRANT PROCESS ON *.* TO 'db_monitor'@'localhost';
-- GRANT REPLICATION CLIENT ON *.* TO 'db_monitor'@'localhost';
-- FLUSH PRIVILEGES;

-- ============================================================
-- 10. Verificar instalación
-- ============================================================
SELECT 'Instalación completada exitosamente.' AS estado;
SHOW TABLES FROM db_health_monitor;
