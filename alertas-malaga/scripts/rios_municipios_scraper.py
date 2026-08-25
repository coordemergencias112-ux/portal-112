#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Para cada estación de río del SAIH Hidrosur: en qué municipio está, y qué
poblaciones hay AGUAS ABAJO (para el módulo "Alertas Málaga").

IMPORTANTE - naturaleza del dato:
El curso de cada río (lista de poblaciones de cabecera a desembocadura) está
tomado a mano de fuentes públicas (Wikipedia, Diputación de Málaga, IECA,
ayuntamientos - ver notas en RIVER_COURSE) y NO es una capa hidrográfica
oficial verificada tramo a tramo. El orden aguas arriba/aguas abajo de cada
estación dentro de su río es una aproximación razonada, no una medición.
Las coordenadas de cada población se obtienen por geocodificación (Nominatim/
OSM) y la distancia estación-población es en LÍNEA RECTA, no siguiendo el
cauce real (que es más largo). Por todo esto, cualquier tiempo estimado de
llegada de una crecida que se calcule a partir de esta distancia es UNA
ESTIMACIÓN MUY ORIENTATIVA, no una previsión hidrológica ni un aviso oficial.
Así se indica también en la propia página.

Al igual que rios_geo_scraper.py, este script NO se ejecuta cada 30 min: el
curso de los ríos y sus municipios no cambian, así que se lanza a mano y el
resultado (alertas-malaga/data/rios_municipios.json) se guarda como asset
estático en el repo.

Uso:
    python rios_municipios_scraper.py
"""
import json
import math
import os
import time
import urllib.request
import urllib.parse

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Curso de cada río, de cabecera (nace) a desembocadura, con solo las
# poblaciones relevantes para orientar "aguas abajo". Fuentes: Wikipedia
# (Guadalhorce, Guadalteba, Turón, Genal, Guadiaro, Campanillas, Benamargosa),
# Diputación de Málaga (ríos de la Serranía de Ronda), cuenta oficial
# @SAIHRedHidrosur (orden de estaciones Bobadilla/Paredones/Aljaima/Cártama).
RIVER_COURSE = {
    "GUADALHORCE": ["Villanueva del Trabuco", "Villanueva del Rosario", "Archidona", "Antequera",
                     "Ardales", "Álora", "Pizarra", "Cártama", "Alhaurín de la Torre", "Málaga"],
    "GUADALTEBA": ["Serrato", "Cuevas del Becerro", "Cañete la Real", "Teba", "Ardales",
                   "Álora", "Pizarra", "Cártama", "Alhaurín de la Torre", "Málaga"],
    "TURON": ["El Burgo", "Ardales", "Álora", "Pizarra", "Cártama", "Alhaurín de la Torre", "Málaga"],
    "GENAL": ["Igualeja", "Pujerra", "Parauta", "Cartajima", "Júzcar", "Faraján", "Alpandeire",
              "Atajate", "Benadalid", "Benalauría", "Algatocín", "Benarrabá", "Jubrique",
              "Genalguacil", "Gaucín", "Casares", "Jimena de la Frontera", "San Roque"],
    "GUADIARO": ["Ronda", "Benaoján", "Jimera de Líbar", "Cortes de la Frontera", "Gaucín",
                 "Jimena de la Frontera", "San Roque"],
    "CAMPANILLAS": ["Casabermeja", "Colmenar", "Málaga"],
    "BENAMARGOSA": ["Comares", "Benamargosa", "Vélez-Málaga"],
    "GRANDE": ["Tolox", "Yunquera", "Pizarra", "Cártama", "Alhaurín de la Torre", "Málaga"],
}

# (río_base, sufijo de la estación en el nombre SAIH) -> índice en RIVER_COURSE
# donde está esa estación (o el más cercano razonable)
STATION_INDEX = {
    ("GUADALHORCE", "ARCHIDONA"): 2,
    ("GUADALHORCE", "BOBADILLA"): 3,
    ("GUADALHORCE", "PAREDONES"): 5,
    ("GUADALHORCE", "ALJAIMA"): 6,
    ("GUADALHORCE", "CÁRTAMA"): 7,
    ("GUADALTEBA", "TEBA"): 3,
    ("TURON", "ARDALES"): 1,
    ("GENAL", "JUBRIQUE"): 12,
    ("GUADIARO", "MAJACEITE"): 3,
    ("CAMPANILLAS", "LOS LLANES"): 1,
    ("BENAMARGOSA", "S. NEGRO"): 0,
    ("GRANDE", "LAS MILLANAS"): 0,
}


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


_geocode_cache = {}


def _buscar(query):
    params = {"q": query, "format": "json", "limit": 1}
    url = NOMINATIM_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": "portal-112-alertas-malaga (uso interno coordinacion 112, contacto: coord.emergencias.112@gmail.com)"
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


# San Roque y Jimena de la Frontera son de Cádiz, no de Málaga - si se
# prueba "Málaga" primero, Nominatim puede colar una pedanía homónima
# equivocada. Para estos nombres se prueba Cádiz primero.
PROVINCIA_PRIORITARIA = {"San Roque": "Cádiz", "Jimena de la Frontera": "Cádiz"}


def geocode(nombre):
    if nombre in _geocode_cache:
        return _geocode_cache[nombre]
    provincias = ["Málaga", "Cádiz"]
    if nombre in PROVINCIA_PRIORITARIA:
        p = PROVINCIA_PRIORITARIA[nombre]
        provincias = [p] + [x for x in provincias if x != p]
    intentos = [f"{nombre}, {p}, España" for p in provincias] + [f"{nombre}, España"]
    # varias provincias no admiten "o" en la query de Nominatim: se prueba una a una
    for intento in intentos:
        try:
            data = _buscar(intento)
        except Exception as e:  # noqa: BLE001
            print(f"  [!] error consultando '{intento}': {e}")
            data = None
        time.sleep(1.1)  # política de uso de Nominatim: máx. 1 petición/seg
        if data:
            coords = (float(data[0]["lat"]), float(data[0]["lon"]))
            _geocode_cache[nombre] = coords
            print(f"  geocodificado: {nombre} -> {coords}  (via '{intento}')")
            return coords
    print(f"  [!] no se pudo geocodificar '{nombre}' con ninguna variante")
    _geocode_cache[nombre] = None
    return None


def main():
    saih_path = os.path.join(os.path.dirname(__file__), "..", "data", "saih.json")
    with open(saih_path, encoding="utf-8") as f:
        saih = json.load(f)

    resultado = {}
    for r in saih.get("rios", []):
        base = r.get("rio_base")
        curso = RIVER_COURSE.get(base)
        if not curso:
            continue

        # sufijo original entre paréntesis, p.ej. "RÍO GUADALHORCE (ARCHIDONA) (MA)" -> "ARCHIDONA"
        import re
        if r["nombre"].upper().startswith("AZUD DE PAREDONES"):
            sufijo = "PAREDONES"  # este nombre no lleva el sufijo entre paréntesis
        else:
            m = re.search(r"\(([^)]+)\)", r["nombre"])
            sufijo = m.group(1).strip().upper() if m else None
            sufijo = re.sub(r"^(AFORO|TR\.?)\s+", "", sufijo or "")
        idx = STATION_INDEX.get((base, sufijo))
        if idx is None:
            continue

        municipio = curso[idx]
        aguas_abajo_nombres = curso[idx + 1:]

        print(f"{r['nombre']} (río {base}) -> municipio: {municipio}, aguas abajo: {aguas_abajo_nombres}")

        aguas_abajo = []
        for nombre_pob in aguas_abajo_nombres:
            coords = geocode(nombre_pob)
            if not coords or r["lat"] is None or r["lon"] is None:
                continue
            dist = haversine_km(r["lat"], r["lon"], coords[0], coords[1])
            aguas_abajo.append({"nombre": nombre_pob, "distancia_km_linea_recta": round(dist, 1)})

        resultado[r["estacion"]] = {
            "nombre_estacion": r["nombre"],
            "municipio": municipio,
            "aguas_abajo": aguas_abajo,
        }

    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "rios_municipios.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"OK -> {out_path} ({len(resultado)} estaciones)")


if __name__ == "__main__":
    main()
