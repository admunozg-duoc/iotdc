# IOTDC — Monitoreo ambiental de datacenter

Sensor IoT que mide **temperatura, humedad, punto de rocío y gases en el aire** al costado de un servidor,
y las envía por PUSH a una API que guarda la configuración en PostgreSQL, las mediciones en InfluxDB
y las grafica en Grafana.

El prototipo en uso es un **ESP32 con DHT11 y MQ135**. El firmware original para Arduino UNO R4 WiFi
(SHT31 + PMS5003, que mide polvo PM2.5 en vez de gases) sigue en el repositorio y usa la misma API.

```
Sensor ──PUSH HTTP──▶ API (FastAPI :8000) ──▶ PostgreSQL  (empresas, dispositivos, parámetros)
                                          └─▶ InfluxDB    (series de tiempo) ◀── Grafana (:3000)
```

## Formato de las mediciones

`POST /api/v1/mediciones` con las cabeceras `X-Dispositivo-Id` (MAC del equipo) y `X-Dispositivo-Token`.
Todas las variables son opcionales; el equipo envía solo las que mide:

```json
{"temperatura": 20.8, "humedad": 55, "punto_rocio": 11.4, "gas": 420, "rssi": -33}
```

| Campo | Unidad | Origen |
|---|---|---|
| `temperatura` | °C | DHT11 / SHT31 |
| `humedad` | % | DHT11 / SHT31 |
| `punto_rocio` | °C | Calculado con la fórmula de Magnus |
| `gas` | ppm | MQ135, estimado como CO₂ equivalente (depende de la calibración en aire limpio) |
| `polvo` | µg/m³ | PM2.5 del PMS5003 (solo UNO R4) |
| `rssi` | dBm | Señal WiFi |

## Estructura

| Ruta | Contenido |
|---|---|
| `servidor/docker-compose.yml` | Stack completo: postgres, influxdb, api y grafana |
| `servidor/instalar.sh` | Instala Docker, genera `.env` con contraseñas aleatorias y levanta todo |
| `servidor/registrar_dispositivo.sh` | Registra un sensor (MAC como ID) y entrega su token |
| `servidor/quitar_simulacion.sh` | Limpia una instancia antigua: quita el simulador y sus datos |
| `servidor/api/` | API: `POST /api/v1/registrar`, `/api/v1/identificar` y `/api/v1/mediciones` |
| `servidor/postgres/init/` | Esquema relacional y datos iniciales |
| `servidor/grafana/` | Fuentes de datos y dashboard provisionados |
| `arduino/sensor_iotdc/` | Firmware para UNO R4 WiFi + SHT31 + PMS5003 |

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
   Los paneles quedan vacíos hasta que se registre un sensor y empiece a enviar datos.

> Si el repositorio es privado, `git clone` pedirá autenticación: usar un *Personal Access Token*
> de GitHub como contraseña o instalar `gh` y ejecutar `gh auth login`.

## Registrar una empresa

Abrir `http://IP_PUBLICA:8000/empresas` (usuario `admin` y la contraseña de Grafana). El formulario
crea la empresa en PostgreSQL y muestra su ID, que es el número que se escribe en «ID del cliente»
al dar de alta un sensor. La misma página lista las empresas existentes y cuántos sensores tiene cada una.

## Registrar el sensor ESP32

El ESP32 se registra solo la primera vez que se conecta; no hace falta entrar al servidor.

1. Obtener la clave de registro del servidor (una sola para todos los sensores):
   `grep REGISTRO_CLAVE ~/iotdc/servidor/.env`
2. Abrir el portal del ESP32 (red `ESP32-Sensor-XXXX`; con el equipo ya configurado, botón BOOT
   3 segundos) y guardar la red WiFi, la IP del servidor, la clave de registro, el ciclo de reporte,
   el nombre, el ID del cliente (empresa) y la ubicación.
3. Al conectarse, el equipo llama a `POST /api/v1/registrar` con su MAC y la clave. La API lo crea
   con esos datos y le entrega su token, que el equipo guarda y usa desde entonces.

Si el servidor deja de reconocer el token (por ejemplo, tras reinstalarlo), el equipo se vuelve a
registrar solo. Quien conozca la clave de registro puede dar de alta sensores: cambiarla en `.env`
y reiniciar la API (`sudo docker compose up -d api`) si se filtra.

`./registrar_dispositivo.sh` sigue disponible para registrar un equipo a mano.

## Registrar un sensor UNO R4

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
git pull && sudo docker compose up -d --build --remove-orphans   # actualizar
```

Una instancia instalada cuando existía el simulador se limpia una sola vez con
`git pull && bash quitar_simulacion.sh`: quita el contenedor, borra los sensores simulados
(`DC-SENS-001` y `DC-SENS-002`) con sus mediciones y agrega la variable `gas`.

## Qué NO está en el repositorio

- `servidor/.env` (contraseñas y tokens) y `arduino_secrets.h` (WiFi y token del sensor).
- Los datos: dispositivos registrados en PostgreSQL y mediciones en InfluxDB.
  En una instancia nueva hay que volver a registrar los sensores (nuevo token) y actualizar la IP del
  servidor en cada equipo si la IP pública cambió (una Elastic IP lo evita).
