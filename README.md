# IOTDC — Monitoreo ambiental de datacenter

Sensor IoT (Arduino UNO R4 WiFi) que mide **temperatura, humedad, polvo en suspensión (PM2.5) y punto de rocío**
al costado de un servidor, y las envía por PUSH a una API que guarda la configuración en PostgreSQL,
las mediciones en InfluxDB y las grafica en Grafana.

```
Arduino ──PUSH HTTP──▶ API (FastAPI :8000) ──▶ PostgreSQL  (empresas, dispositivos, parámetros)
                                           └─▶ InfluxDB    (series de tiempo) ◀── Grafana (:3000)
```

## Estructura

| Ruta | Contenido |
|---|---|
| `servidor/docker-compose.yml` | Stack completo: postgres, influxdb, api, grafana y simulador |
| `servidor/instalar.sh` | Instala Docker, genera `.env` con contraseñas aleatorias y levanta todo |
| `servidor/registrar_dispositivo.sh` | Registra un sensor real (MAC como ID) y entrega su token |
| `servidor/api/` | API: `POST /api/v1/identificar` y `POST /api/v1/mediciones` |
| `servidor/postgres/init/` | Esquema relacional y datos de ejemplo |
| `servidor/grafana/` | Fuentes de datos y dashboard provisionados |
| `servidor/simulador/` | Imita el firmware con dos sensores virtuales |
| `arduino/sensor_iotdc/` | Firmware del sensor (UNO R4 WiFi + SHT31 + PMS5003) |

## Instalar en una instancia nueva

1. Crear una instancia EC2 **Ubuntu 24.04** (t3.medium o similar, 20 GB de disco).
   En el Security Group abrir: 22 (SSH), 3000 (Grafana) y 8000 (API), idealmente solo para las IP necesarias.
2. Conectarse y clonar el repositorio:
   ```bash
   ssh -i millave.pem ubuntu@IP_PUBLICA
   git clone https://github.com/USUARIO/iotdc.git
   cd iotdc/servidor
   ./instalar.sh
   ```
3. Abrir `http://IP_PUBLICA:3000` (usuario `admin`; contraseña con `grep GRAFANA_ADMIN_PASSWORD .env`).
   El simulador empieza a enviar datos de inmediato.

> Si el repositorio es privado, `git clone` pedirá autenticación: usar un *Personal Access Token*
> de GitHub como contraseña o instalar `gh` y ejecutar `gh auth login`.

## Registrar un sensor real

1. Copiar `arduino/sensor_iotdc/arduino_secrets.example.h` como `arduino_secrets.h` y completar WiFi e IP del servidor.
2. Instalar en el IDE de Arduino: *Adafruit SHT31 Library*, *ArduinoJson* (v7) y *ArduinoHttpClient*.
3. Cargar el sketch y leer la MAC en el Monitor Serie (115200 baudios): `ID del dispositivo (MAC): AA:BB:...`
4. En el servidor:
   ```bash
   cd ~/iotdc/servidor
   ./registrar_dispositivo.sh AA:BB:CC:DD:EE:FF "Sensor Rack C02" "Sala 1 - Rack C02" 60
   ```
5. Pegar el `SECRET_DEVICE_TOKEN` entregado en `arduino_secrets.h` y volver a cargar el sketch.

Conexiones: SHT31 a SDA/SCL del cabezal (no al conector Qwiic); TX del PMS5003 a D0 con conversor de nivel 3,3 V → 5 V.

## Operación

```bash
cd ~/iotdc/servidor
sudo docker compose ps                  # estado
sudo docker compose logs -f api         # registros de la API
sudo docker compose stop simulador      # detener el simulador cuando haya sensores reales
git pull && sudo docker compose up -d --build   # actualizar
```

## Qué NO está en el repositorio

- `servidor/.env` (contraseñas y tokens) y `arduino_secrets.h` (WiFi y token del sensor).
- Los datos: dispositivos registrados en PostgreSQL y mediciones en InfluxDB.
  En una instancia nueva hay que volver a registrar los sensores reales (nuevo token) y actualizar
  `SECRET_API_HOST` en el firmware si la IP pública cambió (una Elastic IP lo evita).
