#!/bin/bash
# Datos de ejemplo: una empresa, dos sensores y sus parámetros (umbrales según ASHRAE A1 recomendado).
# Los tokens vienen del .env; en la base solo se guarda su hash.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v tok1="$DISPOSITIVO1_TOKEN" -v tok2="$DISPOSITIVO2_TOKEN" -v grafpw="$GRAFANA_PG_PASSWORD" <<'EOSQL'
INSERT INTO empresas (nombre, rut, contacto_email)
VALUES ('Empresa Demo IOTDC', '76.000.000-0', 'soporte@example.com');

INSERT INTO dispositivos (empresa_id, identificador, token_hash, nombre, ubicacion, intervalo_envio_seg)
VALUES
    (1, 'DC-SENS-001', encode(sha256(convert_to(:'tok1', 'UTF8')), 'hex'), 'Sensor Rack A01', 'Sala 1 - Rack A01', 10),
    (1, 'DC-SENS-002', encode(sha256(convert_to(:'tok2', 'UTF8')), 'hex'), 'Sensor Rack B03', 'Sala 1 - Rack B03', 10);

INSERT INTO parametros_medicion (dispositivo_id, variable, umbral_min, umbral_max)
SELECT d.id, p.variable, p.umbral_min, p.umbral_max
FROM dispositivos d
CROSS JOIN (VALUES
    ('temperatura', 18.0, 27.0),
    ('humedad',     20.0, 60.0),
    ('polvo',       NULL, 25.0),
    ('punto_rocio', -9.0, 15.0)
) AS p(variable, umbral_min, umbral_max);

-- Usuario de solo lectura para el panel de dispositivos en Grafana
CREATE ROLE grafana_ro LOGIN PASSWORD :'grafpw';
GRANT CONNECT ON DATABASE iotdc_config TO grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON empresas, dispositivos, variables, parametros_medicion TO grafana_ro;
EOSQL
