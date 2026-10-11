#!/bin/bash
# Datos iniciales: la empresa y el usuario de solo lectura de Grafana.
# Los sensores se agregan después con registrar_dispositivo.sh.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v grafpw="$GRAFANA_PG_PASSWORD" <<'EOSQL'
INSERT INTO empresas (nombre, rut, contacto_email)
VALUES ('Empresa Demo IOTDC', '76.000.000-0', 'soporte@example.com');

-- Usuario de solo lectura para el panel de dispositivos en Grafana
CREATE ROLE grafana_ro LOGIN PASSWORD :'grafpw';
GRANT CONNECT ON DATABASE iotdc_config TO grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON empresas, dispositivos, variables, parametros_medicion TO grafana_ro;
EOSQL
