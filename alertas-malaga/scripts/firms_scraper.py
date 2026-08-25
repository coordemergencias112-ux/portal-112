#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper NASA FIRMS (detecciones de incendio por satélite) para el
módulo "Alertas Málaga" del Portal 112.

Requiere la variable de entorno FIRMS_MAP_KEY (gratuita e inmediata en
https://firms.modaps.eosdis.nasa.gov/api/map_key/). Nunca escribas la
key en ningún fichero de este repo: en local pásala como variable de
entorno; en GitHub Actions se inyecta como secret.

Si la key no está configurada todavía, el script no falla: escribe un
JSON vacío con "configurado": false para que la página lo muestre como
"no disponible" en vez de romperse.
"""
import csv
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

MAP_KEY = os.environ.get("FIRMS_MAP_KEY", "").strip()

# Bounding box aproximado de la provincia de Málaga: west,south,east,north
BBOX = "-5.55,36.28,-3.90,37.30"
SENSOR = "VIIRS_SNPP_NRT"  # buena resolución (375 m) y baja tasa de falsos positivos
DIAS = 1  # detecciones del último día

URL = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{{key}}/{SENSOR}/{BBOX}/{DIAS}"


def main():
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "incendios.json")

    if not MAP_KEY:
        print("AVISO: falta FIRMS_MAP_KEY - se escribe incendios.json vacío (no configurado)", file=sys.stderr)
        payload = {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "configurado": False,
            "focos": [],
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return

    req = urllib.request.Request(URL.format(key=MAP_KEY), headers={"User-Agent": "portal-112-alertas-malaga"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "replace")

    if raw.lstrip().lower().startswith(("invalid", "error")):
        raise RuntimeError(f"FIRMS respondió con error: {raw[:200]}")

    reader = csv.DictReader(io.StringIO(raw))
    focos = []
    for row in reader:
        try:
            focos.append({
                "lat": float(row["latitude"]),
                "lon": float(row["longitude"]),
                "fecha": row.get("acq_date", ""),
                "hora": row.get("acq_time", ""),
                "confianza": row.get("confidence", ""),
                "frp": float(row["frp"]) if row.get("frp") else None,
                "satelite": row.get("satellite", ""),
                "dianoche": row.get("daynight", ""),
            })
        except (KeyError, ValueError):
            continue

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "configurado": True,
        "focos": focos,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"OK -> {out_path} ({len(focos)} focos activos detectados en el último día)")


if __name__ == "__main__":
    main()
