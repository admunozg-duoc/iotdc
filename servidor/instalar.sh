#!/bin/bash
# Instala el stack IOTDC (PostgreSQL, InfluxDB, API, Grafana y simulador) en una instancia Ubuntu 24.04.
# Se puede ejecutar más de una vez: no reinstala Docker ni sobrescribe el .env existente.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null; then
  echo "==> Instalando Docker"
  export DEBIAN_FRONTEND=noninteractive
  sudo -E apt-get update -qq
  sudo -E apt-get install -y -q ca-certificates curl openssl
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo -E apt-get update -qq
  sudo -E apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER"
  sudo systemctl enable --now docker
fi

if [ ! -f .env ]; then
  echo "==> Generando contraseñas y tokens en .env"
  cat > .env <<EOF
POSTGRES_PASSWORD=$(openssl rand -hex 16)
GRAFANA_PG_PASSWORD=$(openssl rand -hex 16)
INFLUX_PASSWORD=$(openssl rand -hex 16)
INFLUX_TOKEN=$(openssl rand -hex 32)
INFLUX_ORG=iotdc
INFLUX_BUCKET=iotdc
GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 12 | tr -d '/+=')
DISPOSITIVO1_TOKEN=$(openssl rand -hex 20)
DISPOSITIVO2_TOKEN=$(openssl rand -hex 20)
EOF
  chmod 600 .env
fi

# Los contenedores leen estos archivos con otro usuario
chmod +x postgres/init/02-datos.sh registrar_dispositivo.sh
chmod -R a+rX api simulador postgres grafana docker-compose.yml

echo "==> Levantando contenedores"
sudo docker compose up -d --build

echo "==> Esperando a la API"
for _ in $(seq 1 60); do
  estado=$(sudo docker inspect --format '{{.State.Health.Status}}' "$(sudo docker compose ps -q api)" 2>/dev/null || true)
  [ "$estado" = "healthy" ] && break
  sleep 3
done
sudo docker compose ps --format 'table {{.Service}}\t{{.Status}}'

TOKEN_IMDS=$(curl -s -m 2 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' || true)
IP=$(curl -s -m 2 -H "X-aws-ec2-metadata-token: $TOKEN_IMDS" http://169.254.169.254/latest/meta-data/public-ipv4 || true)
IP=${IP:-$(hostname -I | awk '{print $1}')}
echo
echo "Listo."
echo "  Grafana: http://$IP:3000  (usuario admin; contraseña: grep GRAFANA_ADMIN_PASSWORD $(pwd)/.env)"
echo "  API:     http://$IP:8000/docs"
echo "  Registrar un sensor real: ./registrar_dispositivo.sh AA:BB:CC:DD:EE:FF \"Nombre\" \"Ubicación\" 60"
