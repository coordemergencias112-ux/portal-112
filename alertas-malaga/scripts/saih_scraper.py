#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper SAIH Hidrosur (ríos, embalses y pluviómetros - provincia de
Málaga) para el módulo "Alertas Málaga" del Portal 112.

Fuente: https://www.redhidrosurmedioambiente.es/saih (página pública,
sin API key: los valores en tiempo real vienen incrustados en un bloque
<script> del propio HTML). Mismo origen y misma lógica de umbrales que
el proyecto "SAIH-Malaga" ya existente (scripts/generar_dashboard.py),
adaptada aquí a una sola pasada sin base de datos histórica.

No requiere ninguna variable de entorno / secreto.
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone

URL = "https://www.redhidrosurmedioambiente.es/saih"
PROVINCIA = "Málaga"

# Estaciones que la propia web trata como "aforo" (río) aunque su campo
# 'tipo' no sea 'A' (así lo hace explícito el JS del visor original).
RIO_OVERRIDE_IDS = {"34", "49", "58", "103", "104", "130"}

UMBRAL_LLUVIA_MODERADA = 5.0
UMBRAL_LLUVIA_INTENSA = 15.0
UMBRAL_VIGILANCIA_PCT = 0.85  # a partir de qué % del umbral se avisa "a punto de alcanzarlo"

STATION_RE = re.compile(r'''
    var\s+estacion\s*=\s*"(?P<estacion>[^"]*)";\s*
    var\s+nombre\s*=\s*"(?P<nombre>[^"]*)";\s*
    var\s+latitud\s*=\s*"(?P<latitud>[^"]*)";\s*
    var\s+longitud\s*=\s*"(?P<longitud>[^"]*)";\s*
    var\s+tipo\s*=\s*"(?P<tipo>[^"]*)";\s*
    var\s+estacion_s\s*=\s*"(?P<estacion_s>[^"]*)";\s*
    var\s+nivel\s*=\s*"(?P<nivel>[^"]*)";\s*
    var\s+volumen\s*=\s*"(?P<volumen>[^"]*)";\s*
    var\s+sensor\s*=\s*"(?P<sensor>[^"]*)";\s*
    var\s+porcentaje\s*=\s*"(?P<porcentaje>[^"]*)";\s*
    var\s+provincia\s*=\s*"(?P<provincia>[^"]*)";\s*
    var\s+demarcacion\s*=\s*"(?P<demarcacion>[^"]*)";\s*
    var\s+nivel_rio\s*=\s*"(?P<nivel_rio>[^"]*)";\s*
    var\s+caudal\s*=\s*"(?P<caudal>[^"]*)";\s*
    var\s+tendencia\s*=\s*"(?P<tendencia>[^"]*)";\s*
    var\s+aviso\s*=\s*"(?P<aviso>[^"]*)";\s*
    var\s+prealerta\s*=\s*"(?P<prealerta>[^"]*)";\s*
    var\s+alerta\s*=\s*"(?P<alerta>[^"]*)";\s*
    var\s+visible\s*=\s*"(?P<visible>[^"]*)";\s*
    var\s+sensor_p\s*=\s*"(?P<sensor_p>[^"]*)";\s*
    var\s+temp\s*=\s*"(?P<temp>[^"]*)";\s*
    var\s+precip\s*=\s*"(?P<precip>[^"]*)";\s*
    var\s+precipacum\s*=\s*"(?P<precipacum>[^"]*)";
''', re.VERBOSE)

TIMESTAMP_RE = re.compile(r'Datos actualizados a:\s*(\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2})')


def _get(url, retries=4, timeout=30):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 portal-112-alertas-malaga"
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(3 * (attempt + 1))
    raise last_err


def to_float(value):
    if value is None:
        return None
    v = value.strip()
    if v in ("", "n/d", "N/D", "-"):
        return None
    v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def normaliza_rio(nombre):
    """'RÍO GUADALHORCE (ARCHIDONA) (MA)' -> 'GUADALHORCE'. Misma normalización
    que rios_geo_scraper.py, para poder unir cada estación con el trazado
    del río correspondiente."""
    s = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"^\s*(rio|r[íi]o|arroyo)\s+", "", s.strip(), flags=re.I)
    s = s.strip().upper()
    # El "Azud de Paredones" (Álora) es una estación del propio río Guadalhorce,
    # aunque su nombre no lo diga - lo confirma la propia cuenta de SAIH Hidrosur.
    if s == "AZUD DE PAREDONES":
        return "GUADALHORCE"
    return s


def categoria_de(tipo, estacion_id):
    if tipo == "A" or estacion_id in RIO_OVERRIDE_IDS:
        return "rio"
    if tipo in ("P", "M"):
        return "lluvia"
    if tipo == "E":
        return "embalse"
    return "otro"


def evaluar_rio(nivel, aviso, prealerta, alerta):
    """estado: rojo | naranja | amarillo | vigilancia | verde | sin_datos"""
    if nivel is None:
        return "sin_datos", {}
    if alerta and alerta > 0 and nivel >= alerta:
        return "rojo", {}
    if prealerta and prealerta > 0 and nivel >= prealerta:
        return "naranja", {}
    if aviso and aviso > 0 and nivel >= aviso:
        return "amarillo", {}
    if aviso and aviso > 0 and nivel >= aviso * UMBRAL_VIGILANCIA_PCT:
        return "vigilancia", {"etiqueta": "aviso", "umbral": aviso, "pct": round(nivel / aviso * 100)}
    return "verde", {}


def evaluar_lluvia(precip):
    if precip is None:
        return "sin_datos", {}
    if precip >= UMBRAL_LLUVIA_INTENSA:
        return "rojo", {}
    if precip >= UMBRAL_LLUVIA_MODERADA:
        if precip >= UMBRAL_LLUVIA_INTENSA * UMBRAL_VIGILANCIA_PCT:
            return "vigilancia", {"etiqueta": "lluvia intensa", "umbral": UMBRAL_LLUVIA_INTENSA,
                                   "pct": round(precip / UMBRAL_LLUVIA_INTENSA * 100)}
        return "naranja", {}
    if precip > 0:
        if precip >= UMBRAL_LLUVIA_MODERADA * UMBRAL_VIGILANCIA_PCT:
            return "vigilancia", {"etiqueta": "lluvia moderada", "umbral": UMBRAL_LLUVIA_MODERADA,
                                   "pct": round(precip / UMBRAL_LLUVIA_MODERADA * 100)}
        return "amarillo", {}
    return "verde", {}


def main():
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "saih.json")

    try:
        html = _get(URL).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"ERROR descargando SAIH Hidrosur: {e}", file=sys.stderr)
        sys.exit(1)

    ts_match = TIMESTAMP_RE.search(html)
    data_timestamp = None
    if ts_match:
        try:
            data_timestamp = datetime.strptime(ts_match.group(1), "%d-%m-%Y %H:%M:%S").isoformat()
        except ValueError:
            pass

    stations = [m.groupdict() for m in STATION_RE.finditer(html)]
    if not stations:
        print("ERROR: no se pudo interpretar ninguna estación (la web puede haber cambiado de formato)",
              file=sys.stderr)
        sys.exit(1)

    malaga = [s for s in stations if s["provincia"] == PROVINCIA]

    rios, embalses, lluvia = [], [], []
    for s in malaga:
        cat = categoria_de(s["tipo"], s["estacion"])
        lat, lon = to_float(s["latitud"]), to_float(s["longitud"])
        base = {
            "estacion": s["estacion"],
            "nombre": s["nombre"].strip(),
            "lat": lat,
            "lon": lon,
        }
        if cat == "rio":
            nivel_rio = to_float(s["nivel_rio"])
            aviso, prealerta, alerta = to_float(s["aviso"]), to_float(s["prealerta"]), to_float(s["alerta"])
            estado, info = evaluar_rio(nivel_rio, aviso, prealerta, alerta)
            rios.append({**base, "nivel_rio": nivel_rio, "caudal": to_float(s["caudal"]),
                         "tendencia": s["tendencia"].strip(), "aviso": aviso, "prealerta": prealerta,
                         "alerta": alerta, "estado": estado, "info": info,
                         "rio_base": normaliza_rio(s["nombre"])})
        elif cat == "embalse":
            embalses.append({**base, "volumen": to_float(s["volumen"]), "porcentaje": to_float(s["porcentaje"]),
                              "nivel": to_float(s["nivel"])})
        elif cat == "lluvia":
            precip = to_float(s["precip"])
            estado, info = evaluar_lluvia(precip)
            lluvia.append({**base, "precip": precip, "precipacum": to_float(s["precipacum"]),
                            "estado": estado, "info": info})
        # cat == "otro" (EDAR, bombeos...) se descarta: no aporta a un panel de riesgo hidrológico

    orden_estado = {"rojo": 0, "naranja": 1, "amarillo": 2, "vigilancia": 3, "verde": 4, "sin_datos": 5}
    rios.sort(key=lambda r: (orden_estado.get(r["estado"], 9), r["nombre"]))
    lluvia.sort(key=lambda r: (orden_estado.get(r["estado"], 9), -(r["precip"] or 0), r["nombre"]))
    embalses.sort(key=lambda e: e["nombre"])

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_timestamp": data_timestamp,
        "rios": rios,
        "embalses": embalses,
        "lluvia": lluvia,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    n_activos = sum(1 for r in rios + lluvia if r["estado"] in ("amarillo", "naranja", "rojo"))
    print(f"OK -> {out_path} ({len(rios)} ríos, {len(embalses)} embalses, {len(lluvia)} pluviómetros"
          f" · {n_activos} en aviso/prealerta/alerta)")


if __name__ == "__main__":
    main()
