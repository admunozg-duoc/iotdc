"""Simulador de sensores IOTDC: imita el ciclo del firmware del Arduino (modo PUSH).

1. Se identifica ante la API para saber quién es, su empresa y sus parámetros.
2. Envía mediciones cada `intervalo_seg` segundos, solo de las variables habilitadas.
3. Si la API informa otra config_version (o rechaza el token), vuelve a identificarse.

SIM_DISPOSITIVOS = "ID:TOKEN:TEMP_BASE,ID:TOKEN:TEMP_BASE,..."
"""
import math
import os
import random
import threading
import time

import requests

API_URL = os.environ.get("API_URL", "http://api:8000")


def punto_rocio(temperatura, humedad):
    a, b = 17.62, 243.12
    gamma = math.log(humedad / 100) + a * temperatura / (b + temperatura)
    return b * gamma / (a - gamma)


class Sensor:
    def __init__(self, identificador, token, temp_base):
        self.identificador = identificador
        self.token = token
        self.temp_base = temp_base
        self.config = None
        self.deriva = 0.0
        self.calor_hasta = 0
        self.polvo_hasta = 0

    @property
    def cabeceras(self):
        return {"X-Dispositivo-Id": self.identificador, "X-Dispositivo-Token": self.token}

    def log(self, mensaje):
        print(f"[{self.identificador}] {mensaje}", flush=True)

    def identificar(self):
        espera = 5
        while True:
            try:
                r = requests.post(f"{API_URL}/api/v1/identificar", headers=self.cabeceras, timeout=10,
                                  json={"firmware": "sim-1.0", "rssi": random.randint(-70, -55)})
                if r.status_code == 200:
                    self.config = r.json()
                    variables = ", ".join(p["variable"] for p in self.config["medicion"]["parametros"])
                    self.log(f"Soy '{self.config['dispositivo']['nombre']}' de "
                             f"'{self.config['empresa']['nombre']}'; envío cada "
                             f"{self.config['medicion']['intervalo_seg']} s: {variables}")
                    return
                self.log(f"Identificación rechazada ({r.status_code}): {r.text}")
            except requests.RequestException as e:
                self.log(f"API no disponible: {e}")
            time.sleep(espera)
            espera = min(espera * 2, 60)

    def leer(self):
        ahora = time.time()
        hora = (ahora / 3600) % 24
        self.deriva = max(-1.0, min(1.0, self.deriva + random.gauss(0, 0.05)))
        temperatura = (self.temp_base + 1.2 * math.sin(2 * math.pi * (hora - 9) / 24)
                       + self.deriva + random.gauss(0, 0.1))
        polvo = max(0.0, random.gauss(6, 1.2))

        # Eventos ocasionales para ver alertas en Grafana
        if ahora < self.calor_hasta:
            temperatura += 6
        elif random.random() < 0.01:
            self.calor_hasta = ahora + 180
            self.log("Simulando falla de refrigeración (3 min)")
        if ahora < self.polvo_hasta:
            polvo += 30
        elif random.random() < 0.01:
            self.polvo_hasta = ahora + 120
            self.log("Simulando ingreso de polvo (2 min)")

        humedad = max(5.0, min(95.0, 45 - (temperatura - self.temp_base) * 1.5 + random.gauss(0, 0.8)))
        lectura = {
            "temperatura": round(temperatura, 1),
            "humedad": round(humedad, 1),
            "polvo": round(polvo, 1),
            "punto_rocio": round(punto_rocio(temperatura, humedad), 1),
        }
        habilitadas = {p["variable"] for p in self.config["medicion"]["parametros"]}
        lectura = {k: v for k, v in lectura.items() if k in habilitadas}
        lectura["rssi"] = random.randint(-72, -55)
        return lectura

    def ejecutar(self):
        self.identificar()
        while True:
            try:
                r = requests.post(f"{API_URL}/api/v1/mediciones", json=self.leer(),
                                  headers=self.cabeceras, timeout=10)
                if r.status_code == 201:
                    respuesta = r.json()
                    for a in respuesta["alertas"]:
                        self.log(f"ALERTA {a['variable']} {a['tipo']}: {a['valor']} (umbral {a['umbral']})")
                    if respuesta["config_version"] != self.config["config_version"]:
                        self.log("La configuración cambió en el servidor; me vuelvo a identificar")
                        self.identificar()
                elif r.status_code in (401, 403):
                    self.log(f"Credenciales rechazadas ({r.status_code}); me vuelvo a identificar")
                    self.identificar()
                else:
                    self.log(f"Error al enviar medición ({r.status_code}): {r.text}")
            except requests.RequestException as e:
                self.log(f"Sin conexión con la API: {e}")
            time.sleep(self.config["medicion"]["intervalo_seg"])


def main():
    hilos = []
    for definicion in os.environ["SIM_DISPOSITIVOS"].split(","):
        identificador, token, temp_base = definicion.split(":")
        sensor = Sensor(identificador, token, float(temp_base))
        hilo = threading.Thread(target=sensor.ejecutar, daemon=True)
        hilo.start()
        hilos.append(hilo)
    for hilo in hilos:
        hilo.join()


if __name__ == "__main__":
    main()
