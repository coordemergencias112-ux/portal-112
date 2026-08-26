#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Asigna a cada estación SAIH (río/embalse/pluviómetro) la zona de aviso
AEMET dentro de la que cae su coordenada, usando los polígonos reales de
zonas_geo.json (mismo dato ya usado para pintar las zonas en el mapa) en
vez de un mapeo manual estación->zona a ojo.

Es un script MANUAL/de un solo uso, como rios_geo_scraper.py y
rios_municipios_scraper.py: el listado de estaciones no cambia de un día
para otro, así que no forma parte del workflow de cada 30 min. Se lanza
a mano y el resultado (alertas-malaga/data/estaciones_zona.json) se
guarda como asset estático en el repo. Se usa en el módulo "Mi Panel"
(Portal/panel-control) para poder filtrar el SAIH "por zona" además de
por estación concreta.

Si una estación no cae dentro de ningún polígono (puede pasar cerca de
un borde, o si algún polígono tiene huecos), se le asigna la zona cuyo
polígono tiene el vértice más cercano - una aproximación razonable, no
una fuente oficial de zonificación de estaciones.

Uso:
    python estaciones_zona_scraper.py
"""
import json
import math
import os


def punto_en_poligono(lat, lon, poligono):
    """Ray casting clásico. poligono: lista de [lat, lon]."""
    dentro = False
    n = len(poligono)
    j = n - 1
    for i in range(n):
        lat_i, lon_i = poligono[i]
        lat_j, lon_j = poligono[j]
        interseca = ((lon_i > lon) != (lon_j > lon)) and \
            (lat < (lat_j - lat_i) * (lon - lon_i) / (lon_j - lon_i + 1e-12) + lat_i)
        if interseca:
            dentro = not dentro
        j = i
    return dentro


def distancia_min_a_poligono(lat, lon, poligono):
    return min(math.hypot(lat - p[0], lon - p[1]) for p in poligono)


def zona_de(lat, lon, zonas):
    for zcode, z in zonas.items():
        if punto_en_poligono(lat, lon, z["points"]):
            return zcode
    # fallback: zona cuyo polígono tiene el vértice más cercano
    mejor, mejor_dist = None, float("inf")
    for zcode, z in zonas.items():
        d = distancia_min_a_poligono(lat, lon, z["points"])
        if d < mejor_dist:
            mejor, mejor_dist = zcode, d
    return mejor


def main():
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    with open(os.path.join(data_dir, "zonas_geo.json"), encoding="utf-8") as f:
        zonas = json.load(f)
    with open(os.path.join(data_dir, "saih.json"), encoding="utf-8") as f:
        saih = json.load(f)

    resultado = {}
    fuera_de_poligono = []
    for categoria in ("rios", "embalses", "lluvia"):
        for s in saih.get(categoria, []):
            if s["estacion"] in resultado:
                continue  # ya asignada (estación con doble membresía, p.ej. río+lluvia)
            if s.get("lat") is None or s.get("lon") is None:
                continue
            dentro = any(punto_en_poligono(s["lat"], s["lon"], z["points"]) for z in zonas.values())
            zcode = zona_de(s["lat"], s["lon"], zonas)
            resultado[s["estacion"]] = zcode
            if not dentro:
                fuera_de_poligono.append((s["nombre"], zonas[zcode]["nombre"]))

    out_path = os.path.join(data_dir, "estaciones_zona.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2, sort_keys=True)

    print(f"OK -> {out_path} ({len(resultado)} estaciones)")
    por_zona = {}
    for zcode in resultado.values():
        por_zona[zcode] = por_zona.get(zcode, 0) + 1
    for zcode, n in sorted(por_zona.items()):
        print(f"  {zonas[zcode]['nombre']:28s} {n} estaciones")
    if fuera_de_poligono:
        print(f"\n{len(fuera_de_poligono)} estaciones fuera de todos los polígonos (asignadas por cercanía):")
        for nombre, zona in fuera_de_poligono:
            print(f"  {nombre} -> {zona}")


if __name__ == "__main__":
    main()
