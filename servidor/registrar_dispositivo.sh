#!/bin/bash
# Registra un sensor real en la base de configuración usando su MAC como ID y genera su token.
# Uso: ./registrar_dispositivo.sh AA:BB:CC:DD:EE:FF "Sensor Rack C02" "Sala 1 - Rack C02" [intervalo_seg] [empresa_id]
set -euo pipefail
cd "$(dirname "$0")"

MAC=$(echo "${1:-}" | tr 'a-f' 'A-F')
NOMBRE=${2:-}
UBICACION=${3:-}
INTERVALO=${4:-60}
EMPRESA=${5:-1}

if ! [[ $MAC =~ ^([0-9A-F]{2}:){5}[0-9A-F]{2}$ ]] || [ -z "$NOMBRE" ] \
   || ! [[ $INTERVALO =~ ^[0-9]+$ ]] || ! [[ $EMPRESA =~ ^[0-9]+$ ]]; then
  echo "Uso: $0 AA:BB:CC:DD:EE:FF \"Nombre\" \"Ubicación\" [intervalo_seg=60] [empresa_id=1]" >&2
  exit 1
fi

TOKEN=$(openssl rand -hex 20)

# Umbrales por defecto (ASHRAE A1 recomendado); se pueden ajustar luego en parametros_medicion
sudo docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U iotdc -d iotdc_config \
  -v mac="$MAC" -v tok="$TOKEN" -v nombre="$NOMBRE" -v ubic="$UBICACION" \
  -v intervalo="$INTERVALO" -v empresa="$EMPRESA" <<'EOSQL'
WITH d AS (
    INSERT INTO dispositivos (empresa_id, identificador, token_hash, nombre, ubicacion, intervalo_envio_seg)
    VALUES (:empresa, :'mac', encode(sha256(convert_to(:'tok', 'UTF8')), 'hex'), :'nombre', NULLIF(:'ubic', ''), :intervalo)
    RETURNING id
)
INSERT INTO parametros_medicion (dispositivo_id, variable, umbral_min, umbral_max)
SELECT d.id, p.variable, p.umbral_min, p.umbral_max
FROM d CROSS JOIN (VALUES
    ('temperatura', 18.0, 27.0),
    ('humedad',     20.0, 60.0),
    ('polvo',       NULL, 25.0),
    ('punto_rocio', -9.0, 15.0)
) AS p(variable, umbral_min, umbral_max);
EOSQL

echo "Dispositivo $MAC registrado como '$NOMBRE'."
echo "Copia esta línea en arduino_secrets.h (el token no se vuelve a mostrar):"
echo "#define SECRET_DEVICE_TOKEN \"$TOKEN\""
