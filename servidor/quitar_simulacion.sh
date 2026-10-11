#!/bin/bash
# Deja sin simulación un servidor instalado cuando todavía existía el simulador:
# quita su contenedor, borra los sensores simulados con sus mediciones y agrega la variable "gas".
# Se puede ejecutar más de una vez. En instalaciones nuevas no hace falta.
set -euo pipefail
cd "$(dirname "$0")"

SIMULADOS="DC-SENS-001 DC-SENS-002"

valor_env() {
  grep "^$1=" .env | cut -d= -f2-
}

echo "==> Actualizando contenedores y quitando el simulador"
sudo docker compose up -d --build --remove-orphans

echo "==> Borrando los sensores simulados de PostgreSQL y agregando la variable gas"
sudo docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U iotdc -d iotdc_config <<'EOSQL'
DELETE FROM dispositivos WHERE identificador IN ('DC-SENS-001', 'DC-SENS-002');

INSERT INTO variables (codigo, descripcion, unidad)
VALUES ('gas', 'Gases en el aire (salida del MQ135)', 'V')
ON CONFLICT (codigo) DO NOTHING;

INSERT INTO parametros_medicion (dispositivo_id, variable)
SELECT id, 'gas' FROM dispositivos
ON CONFLICT (dispositivo_id, variable) DO NOTHING;
EOSQL

echo "==> Borrando las mediciones simuladas de InfluxDB"
for id in $SIMULADOS; do
  sudo docker compose exec -T influxdb influx delete \
    --org "$(valor_env INFLUX_ORG)" --bucket "$(valor_env INFLUX_BUCKET)" --token "$(valor_env INFLUX_TOKEN)" \
    --start 1970-01-01T00:00:00Z --stop "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --predicate "dispositivo=\"$id\""
done

echo
echo "Listo. Dispositivos registrados:"
sudo docker compose exec -T postgres psql -U iotdc -d iotdc_config \
  -c "SELECT identificador, nombre, ubicacion, intervalo_envio_seg FROM dispositivos ORDER BY id"
