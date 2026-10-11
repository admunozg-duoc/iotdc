"""API IOTDC: recibe la identificación y las mediciones de los sensores ambientales (modo PUSH).

El dispositivo se autentica con las cabeceras X-Dispositivo-Id y X-Dispositivo-Token.
Un equipo nuevo obtiene su token en POST /api/v1/registrar presentando X-Registro-Clave.
"""
import hashlib
import hmac
import json
import math
import os
import re
import secrets
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

pool = ConnectionPool(os.environ["PG_DSN"], min_size=1, max_size=10,
                      kwargs={"row_factory": dict_row}, open=False)
influx = InfluxDBClient(url=os.environ["INFLUX_URL"], token=os.environ["INFLUX_TOKEN"],
                        org=os.environ["INFLUX_ORG"])
write_api = influx.write_api(write_options=SYNCHRONOUS)
BUCKET = os.environ["INFLUX_BUCKET"]
# Clave compartida que presentan los equipos nuevos para registrarse solos; vacía = alta deshabilitada
REGISTRO_CLAVE = os.environ.get("REGISTRO_CLAVE", "")
MAC_VALIDA = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


@asynccontextmanager
async def lifespan(app):
    pool.open(wait=True)
    yield
    pool.close()
    influx.close()


app = FastAPI(title="API IOTDC", version="1.0", lifespan=lifespan)


class Identificacion(BaseModel):
    firmware: Optional[str] = Field(None, max_length=32)
    ip: Optional[str] = Field(None, max_length=45)
    rssi: Optional[int] = Field(None, ge=-120, le=0)


class Registro(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=120)
    ubicacion: Optional[str] = Field(None, max_length=160)
    intervalo_seg: int = Field(60, ge=5, le=3600)
    empresa_id: int = Field(1, ge=1)
    firmware: Optional[str] = Field(None, max_length=32)


class Medicion(BaseModel):
    temperatura: Optional[float] = Field(None, ge=-40, le=85)
    humedad: Optional[float] = Field(None, ge=0, le=100)
    polvo: Optional[float] = Field(None, ge=0, le=1000)
    punto_rocio: Optional[float] = Field(None, ge=-60, le=60)
    gas: Optional[float] = Field(None, ge=0, le=10000)
    rssi: Optional[int] = Field(None, ge=-120, le=0)


SQL_DISPOSITIVO = """
    SELECT d.id, d.identificador, d.token_hash, d.nombre, d.ubicacion, d.intervalo_envio_seg,
           d.activo, e.nombre AS empresa, e.rut AS empresa_rut
    FROM dispositivos d JOIN empresas e ON e.id = d.empresa_id
    WHERE d.identificador = %s
"""

SQL_PARAMETROS = """
    SELECT p.variable, v.unidad, p.umbral_min, p.umbral_max
    FROM parametros_medicion p JOIN variables v ON v.codigo = p.variable
    WHERE p.dispositivo_id = %s AND p.habilitado
    ORDER BY p.variable
"""


# Un equipo que se vuelve a registrar conserva su fila y recibe un token nuevo
SQL_REGISTRAR = """
    INSERT INTO dispositivos (empresa_id, identificador, token_hash, nombre, ubicacion,
                              intervalo_envio_seg, firmware, ultimo_contacto)
    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (identificador) DO UPDATE SET
        empresa_id = EXCLUDED.empresa_id, token_hash = EXCLUDED.token_hash, nombre = EXCLUDED.nombre,
        ubicacion = EXCLUDED.ubicacion, intervalo_envio_seg = EXCLUDED.intervalo_envio_seg,
        firmware = COALESCE(EXCLUDED.firmware, dispositivos.firmware), ultimo_contacto = now()
    RETURNING id
"""

# Umbrales por defecto (ASHRAE A1 recomendado), los mismos de registrar_dispositivo.sh
SQL_PARAMETROS_INICIALES = """
    INSERT INTO parametros_medicion (dispositivo_id, variable, umbral_min, umbral_max)
    SELECT %s, p.variable, p.umbral_min, p.umbral_max
    FROM (VALUES
        ('temperatura', 18.0, 27.0),
        ('humedad',     20.0, 60.0),
        ('polvo',       NULL, 25.0),
        ('punto_rocio', -9.0, 15.0),
        ('gas',         NULL, NULL)
    ) AS p(variable, umbral_min, umbral_max)
    ON CONFLICT (dispositivo_id, variable) DO NOTHING
"""


def numero(valor):
    return float(valor) if valor is not None else None


def autenticar(identificador, token):
    if not identificador or not token:
        raise HTTPException(401, "Faltan las cabeceras X-Dispositivo-Id y X-Dispositivo-Token")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with pool.connection() as conn:
        disp = conn.execute(SQL_DISPOSITIVO, (identificador,)).fetchone()
        if disp is None or not hmac.compare_digest(disp["token_hash"], token_hash):
            raise HTTPException(401, "Dispositivo no autorizado")
        if not disp["activo"]:
            raise HTTPException(403, "Dispositivo deshabilitado")
        params = conn.execute(SQL_PARAMETROS, (disp["id"],)).fetchall()
    return disp, params


def armar_config(disp, params):
    config = {
        "dispositivo": {
            "identificador": disp["identificador"],
            "nombre": disp["nombre"],
            "ubicacion": disp["ubicacion"],
        },
        "empresa": {"nombre": disp["empresa"], "rut": disp["empresa_rut"]},
        "medicion": {
            "intervalo_seg": disp["intervalo_envio_seg"],
            "parametros": [
                {"variable": p["variable"], "unidad": p["unidad"],
                 "umbral_min": numero(p["umbral_min"]), "umbral_max": numero(p["umbral_max"])}
                for p in params
            ],
        },
    }
    # Si cambia la configuración en la base, cambia la versión y el dispositivo se re-identifica
    config["config_version"] = hashlib.sha1(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]
    return config


def registrar_contacto(disp_id, firmware=None):
    with pool.connection() as conn:
        conn.execute("UPDATE dispositivos SET ultimo_contacto = now(), firmware = COALESCE(%s, firmware) "
                     "WHERE id = %s", (firmware, disp_id))


def calcular_punto_rocio(temperatura, humedad):
    # Fórmula de Magnus
    a, b = 17.62, 243.12
    gamma = math.log(humedad / 100) + a * temperatura / (b + temperatura)
    return b * gamma / (a - gamma)


@app.get("/salud")
def salud():
    with pool.connection() as conn:
        conn.execute("SELECT 1")
    if not influx.ping():
        raise HTTPException(503, "InfluxDB no disponible")
    return {"estado": "ok"}


@app.post("/api/v1/registrar", status_code=201)
def registrar(datos: Registro,
              x_dispositivo_id: Optional[str] = Header(None),
              x_registro_clave: Optional[str] = Header(None)):
    """Alta automática: el equipo presenta la clave de registro y recibe su token (solo esta vez)."""
    if not REGISTRO_CLAVE:
        raise HTTPException(403, "El registro automático está deshabilitado en este servidor")
    if not x_registro_clave or not hmac.compare_digest(x_registro_clave.encode(), REGISTRO_CLAVE.encode()):
        raise HTTPException(401, "Clave de registro incorrecta")
    identificador = (x_dispositivo_id or "").upper()
    if not MAC_VALIDA.match(identificador):
        raise HTTPException(422, "X-Dispositivo-Id debe ser una MAC con formato AA:BB:CC:DD:EE:FF")

    token = secrets.token_hex(20)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with pool.connection() as conn:
        if conn.execute("SELECT 1 FROM empresas WHERE id = %s", (datos.empresa_id,)).fetchone() is None:
            raise HTTPException(404, f"No existe la empresa con ID {datos.empresa_id}")
        disp_id = conn.execute(SQL_REGISTRAR, (datos.empresa_id, identificador, token_hash, datos.nombre,
                                               datos.ubicacion or None, datos.intervalo_seg,
                                               datos.firmware)).fetchone()["id"]
        conn.execute(SQL_PARAMETROS_INICIALES, (disp_id,))

    disp, params = autenticar(identificador, token)
    return {"token": token, **armar_config(disp, params)}


@app.post("/api/v1/identificar")
def identificar(datos: Optional[Identificacion] = None,
                x_dispositivo_id: Optional[str] = Header(None),
                x_dispositivo_token: Optional[str] = Header(None)):
    """Responde quién es el dispositivo, a qué empresa pertenece y cuáles son sus parámetros."""
    disp, params = autenticar(x_dispositivo_id, x_dispositivo_token)
    registrar_contacto(disp["id"], datos.firmware if datos else None)
    return armar_config(disp, params)


@app.post("/api/v1/mediciones", status_code=201)
def mediciones(medicion: Medicion,
               x_dispositivo_id: Optional[str] = Header(None),
               x_dispositivo_token: Optional[str] = Header(None)):
    disp, params = autenticar(x_dispositivo_id, x_dispositivo_token)
    config = armar_config(disp, params)
    habilitados = {p["variable"]: p for p in config["medicion"]["parametros"]}

    valores = medicion.model_dump(exclude_none=True)
    rssi = valores.pop("rssi", None)
    if ("punto_rocio" in habilitados and "punto_rocio" not in valores
            and "temperatura" in valores and valores.get("humedad", 0) > 0):
        valores["punto_rocio"] = round(calcular_punto_rocio(valores["temperatura"], valores["humedad"]), 2)
    valores = {k: v for k, v in valores.items() if k in habilitados}
    if not valores:
        raise HTTPException(422, "La medición no contiene variables habilitadas para este dispositivo")

    punto = (Point("ambiente")
             .tag("empresa", disp["empresa"])
             .tag("dispositivo", disp["identificador"])
             .tag("nombre", disp["nombre"])
             .tag("ubicacion", disp["ubicacion"] or "sin_ubicacion"))
    for variable, valor in valores.items():
        punto.field(variable, float(valor))
    if rssi is not None:
        punto.field("rssi", rssi)
    try:
        write_api.write(bucket=BUCKET, record=punto)
    except Exception as e:
        raise HTTPException(503, f"No se pudo registrar la medición: {e}")

    alertas = []
    for variable, valor in valores.items():
        p = habilitados[variable]
        if p["umbral_min"] is not None and valor < p["umbral_min"]:
            alertas.append({"variable": variable, "valor": valor, "tipo": "bajo", "umbral": p["umbral_min"]})
        elif p["umbral_max"] is not None and valor > p["umbral_max"]:
            alertas.append({"variable": variable, "valor": valor, "tipo": "alto", "umbral": p["umbral_max"]})

    registrar_contacto(disp["id"])
    return {"estado": "ok", "registrado": valores, "alertas": alertas,
            "config_version": config["config_version"]}
