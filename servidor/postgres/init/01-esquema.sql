-- Configuración de los prototipos IoT de monitoreo ambiental de datacenter

CREATE TABLE empresas (
    id              SERIAL PRIMARY KEY,
    nombre          VARCHAR(120) NOT NULL UNIQUE,
    rut             VARCHAR(12) UNIQUE,
    contacto_email  VARCHAR(160),
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dispositivos (
    id                   SERIAL PRIMARY KEY,
    empresa_id           INT NOT NULL REFERENCES empresas(id),
    identificador        VARCHAR(64) NOT NULL UNIQUE,  -- serie/MAC grabada en el firmware
    token_hash           CHAR(64) NOT NULL,            -- SHA-256 del token del dispositivo
    nombre               VARCHAR(120) NOT NULL,
    ubicacion            VARCHAR(160),
    intervalo_envio_seg  INT NOT NULL DEFAULT 60 CHECK (intervalo_envio_seg BETWEEN 5 AND 3600),
    activo               BOOLEAN NOT NULL DEFAULT true,
    firmware             VARCHAR(32),
    ultimo_contacto      TIMESTAMPTZ,
    creado_en            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Catálogo de variables que el prototipo sabe medir
CREATE TABLE variables (
    codigo       VARCHAR(32) PRIMARY KEY,
    descripcion  VARCHAR(120) NOT NULL,
    unidad       VARCHAR(16) NOT NULL
);

CREATE TABLE parametros_medicion (
    dispositivo_id  INT NOT NULL REFERENCES dispositivos(id) ON DELETE CASCADE,
    variable        VARCHAR(32) NOT NULL REFERENCES variables(codigo),
    habilitado      BOOLEAN NOT NULL DEFAULT true,
    umbral_min      NUMERIC(8,2),
    umbral_max      NUMERIC(8,2),
    PRIMARY KEY (dispositivo_id, variable),
    CHECK (umbral_min IS NULL OR umbral_max IS NULL OR umbral_min < umbral_max)
);

INSERT INTO variables (codigo, descripcion, unidad) VALUES
    ('temperatura', 'Temperatura ambiente', '°C'),
    ('humedad',     'Humedad relativa',     '%'),
    ('polvo',       'Polvo en suspensión (PM2.5)', 'µg/m³'),
    ('punto_rocio', 'Punto de rocío',       '°C'),
    ('gas',         'Gases en el aire (salida del MQ135)', 'V');
