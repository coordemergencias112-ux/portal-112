#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Descarga el TRAZADO (geometría) de los ríos de Málaga que tienen estación
SAIH con nivel, para poder dibujarlos como línea sobre el mapa del módulo
"Alertas Málaga" (y así ver por qué zonas pasan), no solo el punto de la
estación.

Fuente: OpenStreetMap, vía Overpass API (datos abiertos, sin API key).

A DIFERENCIA de aemet_scraper.py / firms_scraper.py / saih_scraper.py,
este script NO se ejecuta cada 30 minutos: el trazado de un río no
cambia, así que se lanza a mano cuando haga falta (p. ej. si algún día
se añade una estación de un río nuevo) y el resultado
(alertas-malaga/data/rios_geo.json) se guarda en el repo como asset
estático. Machacar la Overpass API pública cada 30 min sin necesidad
sería un mal uso de un servicio compartido.

Uso:
    python rios_geo_scraper.py
"""
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
import urllib.parse

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Igual bbox que saih_scraper.py: west,south,east,north de la provincia de Málaga
BBOX = (36.28, -5.55, 37.30, -3.90)  # south, west, north, east (orden Overpass)

QUERY = f"""
[out:json][timeout:90];
way["waterway"="river"]["name"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
out geom;
"""


def normaliza(nombre):
    """'RÍO GUADALHORCE (ARCHIDONA) (MA)' / 'Rio Guadalhorce' -> 'GUADALHORCE'"""
    s = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    s = re.sub(r"\([^)]*\)", "", s)  # quita paréntesis: "(ARCHIDONA)", "(MA)"
    s = re.sub(r"^\s*(rio|r[íi]o|arroyo)\s+", "", s.strip(), flags=re.I)
    s = s.strip().upper()
    if s == "AZUD DE PAREDONES":  # estación del propio Guadalhorce (Álora), ver saih_scraper.py
        return "GUADALHORCE"
    return s


def rios_objetivo():
    """Nombres base de río a buscar: los de las estaciones SAIH ya
    descargadas (si existe data/saih.json) más una lista de respaldo."""
    saih_path = os.path.join(os.path.dirname(__file__), "..", "data", "saih.json")
    objetivo = set()
    if os.path.exists(saih_path):
        with open(saih_path, encoding="utf-8") as f:
            data = json.load(f)
        for r in data.get("rios", []):
            n = normaliza(r["nombre"])
            if n:
                objetivo.add(n)
    if not objetivo:
        objetivo = {"GUADALHORCE", "GUADALTEBA", "GENAL", "GRANDE", "CAMPANILLAS",
                    "BENAMARGOSA", "GUADIARO", "TURON", "PAREDONES"}
    return objetivo


def _get(url, data=None, retries=3, timeout=90):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "portal-112-alertas-malaga"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise last_err


def main():
    objetivo = rios_objetivo()
    print(f"Buscando trazado para: {sorted(objetivo)}")

    body = urllib.parse.urlencode({"data": QUERY}).encode("utf-8")
    raw = _get(OVERPASS_URL, data=body)
    data = json.loads(raw.decode("utf-8", "replace"))
    elements = data.get("elements", [])
    print(f"Overpass devolvió {len(elements)} tramos de río con nombre en la provincia.")

    rios = {}  # nombre normalizado -> lista de tramos, cada tramo = [[lat,lon], ...]
    for el in elements:
        nombre = el.get("tags", {}).get("name", "")
        clave = normaliza(nombre)
        if clave not in objetivo:
            continue
        geom = el.get("geometry")
        if not geom:
            continue
        tramo = [[pt["lat"], pt["lon"]] for pt in geom]
        rios.setdefault(clave, []).append(tramo)

    out_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "rios_geo.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rios, f, ensure_ascii=False)

    for k in sorted(objetivo):
        n_tramos = len(rios.get(k, []))
        print(f"  {k:15s} -> {n_tramos} tramo(s)" + ("" if n_tramos else "  [!] sin coincidencia en OSM"))
    print(f"OK -> {out_path}")


if __name__ == "__main__":
    main()
