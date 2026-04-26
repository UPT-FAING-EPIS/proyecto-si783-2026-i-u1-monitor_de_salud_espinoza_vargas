-- ============================================================
-- MSBDD - Monitor de Salud de Base de Datos
-- Script de inicialización para entorno de prueba
-- ============================================================

-- Habilitar extensión pg_stat_statements para consultas lentas
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Base de datos de ejemplo para generar actividad
CREATE TABLE IF NOT EXISTS productos (
    id SERIAL PRIMARY KEY,
    nombre VARCHAR(100),
    precio DECIMAL(10,2),
    stock INTEGER,
    creado_en TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ventas (
    id SERIAL PRIMARY KEY,
    producto_id INTEGER REFERENCES productos(id),
    cantidad INTEGER,
    total DECIMAL(10,2),
    fecha TIMESTAMP DEFAULT NOW()
);

-- Insertar datos de prueba
INSERT INTO productos (nombre, precio, stock)
SELECT
    'Producto ' || i,
    (random() * 1000)::DECIMAL(10,2),
    (random() * 500)::INTEGER
FROM generate_series(1, 500) AS i;

INSERT INTO ventas (producto_id, cantidad, total)
SELECT
    (random() * 499 + 1)::INTEGER,
    (random() * 10 + 1)::INTEGER,
    (random() * 5000)::DECIMAL(10,2)
FROM generate_series(1, 2000) AS i;

-- Índices (algunos usados, otros sin usar para el diagnóstico)
CREATE INDEX IF NOT EXISTS idx_ventas_fecha ON ventas(fecha);
CREATE INDEX IF NOT EXISTS idx_productos_nombre ON productos(nombre);
-- Índice intencionalmente sin usar para demostración de diagnóstico
CREATE INDEX IF NOT EXISTS idx_productos_stock ON productos(stock);

-- Vista resumen para el monitor
CREATE OR REPLACE VIEW vista_resumen_salud AS
SELECT
    (SELECT count(*) FROM pg_stat_activity WHERE state = 'active') AS conexiones_activas,
    (SELECT count(*) FROM pg_stat_activity) AS conexiones_totales,
    pg_database_size(current_database()) AS tamanio_bd_bytes,
    NOW() AS timestamp_consulta;

COMMENT ON TABLE productos IS 'Tabla de ejemplo para generar carga en el motor';
COMMENT ON TABLE ventas IS 'Tabla de ejemplo para generar actividad de lectura/escritura';
