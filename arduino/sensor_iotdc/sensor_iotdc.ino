/*
  Sensor ambiental IOTDC — Arduino UNO R4 WiFi
  Mide temperatura, humedad, polvo en suspensión (PM2.5) y punto de rocío al costado de un servidor.

  Comunicación solo PUSH hacia la API IOTDC:
    1. POST /api/v1/identificar -> "quién soy, a qué empresa pertenezco y mis parámetros de medición"
    2. POST /api/v1/mediciones  -> cada intervalo_seg, solo las variables habilitadas
    Si la API responde otra config_version (o rechaza el token) el equipo vuelve a identificarse.

  El ID del dispositivo es la MAC del módulo WiFi (formato AA:BB:CC:DD:EE:FF), se obtiene por código
  y se muestra en el Monitor Serie (115200 baudios) para registrarlo en la base de datos.

  Conexiones:
    SHT31 (I2C, dirección 0x44)    VIN -> 5V, GND -> GND, SDA -> SDA, SCL -> SCL
                                    (pines SDA/SCL del cabezal; el conector Qwiic usa Wire1)
    PMS5003 (UART 9600 baudios)    VCC -> 5V, GND -> GND, TX -> D0 (RX de Serial1)
                                    El PMS5003 usa lógica de 3,3 V: poner un conversor de nivel en TX.
                                    RX, SET y RESET del PMS5003 quedan sin conectar.

  Librerías (Gestor de librerías del IDE): Adafruit SHT31 Library, ArduinoJson (v7), ArduinoHttpClient
*/
#include <WiFiS3.h>
#include <Wire.h>
#include <Adafruit_SHT31.h>
#include <ArduinoHttpClient.h>
#include <ArduinoJson.h>
#include <math.h>
#include "arduino_secrets.h"

#define FIRMWARE_VERSION "1.0.0"

const unsigned long REINTENTO_MIN_MS = 5000;
const unsigned long REINTENTO_MAX_MS = 60000;
const unsigned long PMS_CALENTAMIENTO_MS = 30000;  // el PMS5003 necesita ~30 s para estabilizarse
const unsigned long PMS_SIN_DATOS_MS = 10000;
const uint8_t MAX_PARAMETROS = 8;

struct Parametro {
  char variable[16];
  bool tieneMin, tieneMax;
  float min, max;
};

struct Configuracion {
  bool valida = false;
  char nombre[64];
  char empresa[64];
  char version[16];
  unsigned long intervaloMs = 60000;
  Parametro parametros[MAX_PARAMETROS];
  uint8_t cantidad = 0;
};

Configuracion config;
char idDispositivo[18] = "";  // MAC "AA:BB:CC:DD:EE:FF"

WiFiClient clienteWifi;
HttpClient http(clienteWifi, SECRET_API_HOST, SECRET_API_PORT);
Adafruit_SHT31 sht31;
bool shtOk = false;

// PMS5003: tramas de 32 bytes que empiezan con 0x42 0x4D; se promedia PM2.5 entre envíos
uint8_t pmsTrama[32];
uint8_t pmsPos = 0;
uint32_t pm25Suma = 0;
uint16_t pm25Muestras = 0;
unsigned long pmsUltimaTrama = 0;

unsigned long ultimoEnvio = 0;
unsigned long proximaIdentificacion = 0;
unsigned long esperaIdentificacion = REINTENTO_MIN_MS;

// ---------------------------------------------------------------------------------------------

// El núcleo Renesas no incluye strlcpy
void copiarTexto(char* destino, const char* origen, size_t tamano) {
  strncpy(destino, origen, tamano - 1);
  destino[tamano - 1] = '\0';
}

// WiFi.macAddress() solo responde cuando el módulo está en modo estación (después de WiFi.begin)
bool obtenerMac() {
  byte mac[6] = {0};
  WiFi.macAddress(mac);
  bool vacia = true;
  for (int i = 0; i < 6; i++) {
    if (mac[i] != 0) vacia = false;
  }
  if (vacia) return false;
  snprintf(idDispositivo, sizeof(idDispositivo), "%02X:%02X:%02X:%02X:%02X:%02X",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return true;
}

void leerPMS() {
  while (Serial1.available()) {
    uint8_t b = Serial1.read();
    if (pmsPos == 0 && b != 0x42) continue;
    if (pmsPos == 1 && b != 0x4D) {
      pmsPos = (b == 0x42) ? 1 : 0;
      continue;
    }
    pmsTrama[pmsPos++] = b;
    if (pmsPos < sizeof(pmsTrama)) continue;

    pmsPos = 0;
    uint16_t largo = (pmsTrama[2] << 8) | pmsTrama[3];
    uint16_t suma = 0;
    for (int i = 0; i < 30; i++) suma += pmsTrama[i];
    uint16_t verificacion = (pmsTrama[30] << 8) | pmsTrama[31];
    if (largo != 28 || suma != verificacion) continue;

    pmsUltimaTrama = millis();
    if (millis() < PMS_CALENTAMIENTO_MS) continue;
    // Bytes 12-13: PM2.5 en condiciones atmosféricas (µg/m³)
    pm25Suma += (pmsTrama[12] << 8) | pmsTrama[13];
    pm25Muestras++;
  }
}

void conectarWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.print("Conectando a WiFi '");
  Serial.print(SECRET_SSID);
  Serial.println("'...");
  while (WiFi.status() != WL_CONNECTED) {
    WiFi.begin(SECRET_SSID, SECRET_PASS);
    if (idDispositivo[0] == '\0' && obtenerMac()) {
      Serial.print("ID del dispositivo (MAC): ");
      Serial.println(idDispositivo);
    }
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("Sin conexión WiFi, reintento en 5 s");
      unsigned long inicio = millis();
      while (millis() - inicio < 5000) leerPMS();
    }
  }
  Serial.print("WiFi conectado. IP: ");
  Serial.print(WiFi.localIP());
  Serial.print("  RSSI: ");
  Serial.print(WiFi.RSSI());
  Serial.println(" dBm");
}

int enviarPost(const char* ruta, const String& cuerpo, String& respuesta) {
  http.setHttpResponseTimeout(10000);
  http.beginRequest();
  if (http.post(ruta) != 0) {
    http.stop();
    return -1;
  }
  http.sendHeader("Content-Type", "application/json");
  http.sendHeader("Content-Length", cuerpo.length());
  http.sendHeader("X-Dispositivo-Id", idDispositivo);
  http.sendHeader("X-Dispositivo-Token", SECRET_DEVICE_TOKEN);
  http.beginBody();
  http.print(cuerpo);
  http.endRequest();
  int estado = http.responseStatusCode();
  respuesta = http.responseBody();
  http.stop();
  return estado;
}

bool identificar() {
  JsonDocument solicitud;
  solicitud["firmware"] = FIRMWARE_VERSION;
  solicitud["ip"] = WiFi.localIP().toString();
  solicitud["rssi"] = WiFi.RSSI();
  String cuerpo, respuesta;
  serializeJson(solicitud, cuerpo);

  int estado = enviarPost("/api/v1/identificar", cuerpo, respuesta);
  if (estado != 200) {
    Serial.print("Identificación rechazada (HTTP ");
    Serial.print(estado);
    Serial.print("): ");
    Serial.println(respuesta);
    if (estado == 401) {
      Serial.print("Registra este equipo en el servidor con el ID ");
      Serial.print(idDispositivo);
      Serial.println(" y copia el token en arduino_secrets.h");
    }
    return false;
  }

  JsonDocument r;
  if (deserializeJson(r, respuesta)) {
    Serial.println("La configuración recibida no es JSON válido");
    return false;
  }
  copiarTexto(config.nombre, r["dispositivo"]["nombre"] | "", sizeof(config.nombre));
  copiarTexto(config.empresa, r["empresa"]["nombre"] | "", sizeof(config.empresa));
  copiarTexto(config.version, r["config_version"] | "", sizeof(config.version));
  config.intervaloMs = (unsigned long)(r["medicion"]["intervalo_seg"] | 60) * 1000UL;
  config.cantidad = 0;
  for (JsonObject p : r["medicion"]["parametros"].as<JsonArray>()) {
    if (config.cantidad >= MAX_PARAMETROS) break;
    Parametro& par = config.parametros[config.cantidad++];
    copiarTexto(par.variable, p["variable"] | "", sizeof(par.variable));
    par.tieneMin = !p["umbral_min"].isNull();
    par.tieneMax = !p["umbral_max"].isNull();
    par.min = p["umbral_min"] | 0.0f;
    par.max = p["umbral_max"] | 0.0f;
  }
  config.valida = true;

  Serial.print("Soy '");
  Serial.print(config.nombre);
  Serial.print("' de '");
  Serial.print(config.empresa);
  Serial.print("'. Envío cada ");
  Serial.print(config.intervaloMs / 1000);
  Serial.print(" s:");
  for (uint8_t i = 0; i < config.cantidad; i++) {
    Serial.print(' ');
    Serial.print(config.parametros[i].variable);
  }
  Serial.println();
  return true;
}

bool habilitada(const char* variable) {
  for (uint8_t i = 0; i < config.cantidad; i++) {
    if (strcmp(config.parametros[i].variable, variable) == 0) return true;
  }
  return false;
}

// Fórmula de Magnus
float puntoRocio(float temperatura, float humedad) {
  const float a = 17.62, b = 243.12;
  float gamma = log(humedad / 100.0) + a * temperatura / (b + temperatura);
  return b * gamma / (a - gamma);
}

void medirYEnviar() {
  JsonDocument medicion;
  float t = NAN, h = NAN;
  if (shtOk) {
    t = sht31.readTemperature();
    h = sht31.readHumidity();
  }
  if (isnan(t) || isnan(h)) Serial.println("Aviso: sin lectura del SHT31");
  if (millis() - pmsUltimaTrama > PMS_SIN_DATOS_MS) Serial.println("Aviso: el PMS5003 no envía datos");

  if (!isnan(t) && habilitada("temperatura")) medicion["temperatura"] = serialized(String(t, 1));
  if (!isnan(h) && habilitada("humedad")) medicion["humedad"] = serialized(String(h, 1));
  if (!isnan(t) && !isnan(h) && h > 0 && habilitada("punto_rocio")) {
    medicion["punto_rocio"] = serialized(String(puntoRocio(t, h), 1));
  }
  if (pm25Muestras > 0 && habilitada("polvo")) {
    medicion["polvo"] = serialized(String((float)pm25Suma / pm25Muestras, 1));
  }
  pm25Suma = 0;
  pm25Muestras = 0;

  if (medicion.size() == 0) {
    Serial.println("No hay lecturas válidas; no se envía la medición");
    return;
  }
  medicion["rssi"] = WiFi.RSSI();

  String cuerpo, respuesta;
  serializeJson(medicion, cuerpo);
  Serial.print("Enviando ");
  Serial.println(cuerpo);
  int estado = enviarPost("/api/v1/mediciones", cuerpo, respuesta);

  if (estado == 201) {
    JsonDocument r;
    if (deserializeJson(r, respuesta)) return;
    JsonArray alertas = r["alertas"];
    digitalWrite(LED_BUILTIN, alertas.size() > 0 ? HIGH : LOW);
    for (JsonObject a : alertas) {
      Serial.print("ALERTA ");
      Serial.print(a["variable"].as<const char*>());
      Serial.print(' ');
      Serial.print(a["tipo"].as<const char*>());
      Serial.print(": ");
      Serial.print(a["valor"].as<float>(), 1);
      Serial.print(" (umbral ");
      Serial.print(a["umbral"].as<float>(), 1);
      Serial.println(")");
    }
    if (strcmp(r["config_version"] | "", config.version) != 0) {
      Serial.println("La configuración cambió en el servidor; me vuelvo a identificar");
      config.valida = false;
      proximaIdentificacion = millis();
    }
  } else if (estado == 401 || estado == 403) {
    Serial.print("Credenciales rechazadas (HTTP ");
    Serial.print(estado);
    Serial.println("); me vuelvo a identificar");
    config.valida = false;
    proximaIdentificacion = millis();
  } else {
    Serial.print("Error al enviar la medición (HTTP ");
    Serial.print(estado);
    Serial.print("): ");
    Serial.println(respuesta);
  }
}

// ---------------------------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  unsigned long inicio = millis();
  while (!Serial && millis() - inicio < 3000);
  Serial.println("Sensor IOTDC, firmware " FIRMWARE_VERSION);
  pinMode(LED_BUILTIN, OUTPUT);

  Serial1.begin(9600);
  Wire.begin();
  shtOk = sht31.begin(0x44);
  if (shtOk) {
    sht31.heater(false);
  } else {
    Serial.println("No se encontró el SHT31 en la dirección 0x44");
  }

  if (WiFi.status() == WL_NO_MODULE) {
    Serial.println("El módulo WiFi no responde; revisa la placa");
    while (true);
  }
  conectarWiFi();
  while (idDispositivo[0] == '\0' && !obtenerMac()) delay(500);
  Serial.print("ID del dispositivo (MAC): ");
  Serial.println(idDispositivo);
}

void loop() {
  leerPMS();
  conectarWiFi();

  if (!config.valida) {
    if ((long)(millis() - proximaIdentificacion) >= 0) {
      if (identificar()) {
        esperaIdentificacion = REINTENTO_MIN_MS;
        ultimoEnvio = millis();
      } else {
        proximaIdentificacion = millis() + esperaIdentificacion;
        esperaIdentificacion = min(esperaIdentificacion * 2, REINTENTO_MAX_MS);
      }
    }
    return;
  }

  if (millis() - ultimoEnvio >= config.intervaloMs) {
    ultimoEnvio = millis();
    medirYEnviar();
  }
}
