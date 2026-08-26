#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper AEMET OpenData para el módulo "Alertas Málaga" del Portal 112.

Descarga:
  - Avisos meteorológicos CAP vigentes (nivel por zona de aviso de Málaga)
  - Predicción diaria (lluvia / viento) para dos puntos de referencia:
      Málaga capital (costa) y Ronda (interior/serranía)
  - Predicción horaria de HOY (temperatura, precipitación, prob. de lluvia
      y viento por hora) para esos mismos dos puntos

Escribe un único JSON estático (data/aemet_malaga.json) que la página
alertas-malaga/index.html consume sin necesitar la API key.

Requiere la variable de entorno AEMET_API_KEY. Nunca escribas la key
en ningún fichero de este repo: en local pásala como variable de
entorno; en GitHub Actions se inyecta como secret.
"""
import io
import json
import os
import re
import sys
import tarfile
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

API_KEY = re.sub(r"\s+", "", os.environ.get("AEMET_API_KEY", ""))
BASE = "https://opendata.aemet.es/opendata/api"

# Zonas oficiales de aviso AEMET para la provincia de Málaga
# (código de zona -> nombre, tipo, y polígono real extraído de un CAP de AEMET)
ZONE_META = {
    "612901": {"nombre": "Antequera", "tipo": "interior"},
    "612902": {"nombre": "Ronda", "tipo": "interior"},
    "612903": {"nombre": "Sol y Guadalhorce", "tipo": "interior"},
    "612903C": {"nombre": "Costa - Sol y Guadalhorce", "tipo": "litoral"},
    "612904": {"nombre": "Axarquía", "tipo": "interior"},
    "612904C": {"nombre": "Costa - Axarquía", "tipo": "litoral"},
}

# Puntos de referencia para la previsión (código INE de municipio AEMET)
FORECAST_POINTS = [
    {"id": "29067", "nombre": "Málaga capital", "referencia": "Costa"},
    {"id": "29079", "nombre": "Ronda", "referencia": "Interior / Serranía"},
]

NIVEL_ORDEN = {"verde": 0, "amarillo": 1, "naranja": 2, "rojo": 3}
HISTORIAL_DIAS = 7  # cuánto conservamos el historial de avisos no-verdes


def _get(url, params=None, retries=5, timeout=30):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "portal-112-alertas-malaga"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last_err = e
            wait = 5 * (attempt + 1) if e.code == 429 else 2
            time.sleep(wait)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2)
    raise last_err


def aemet_call(path):
    """Llama a un endpoint de AEMET OpenData (2 pasos: metadatos -> datos)."""
    meta_raw = _get(BASE + path, {"api_key": API_KEY})
    meta = json.loads(meta_raw.decode("utf-8", "replace"))
    if meta.get("estado") != 200:
        raise RuntimeError(f"AEMET {path} -> {meta}")
    time.sleep(1.2)  # evitar el límite de ráfaga de AEMET (429) entre metadatos y datos
    data_raw = _get(meta["datos"])
    time.sleep(1.2)  # ... y antes de la siguiente llamada a la API
    return data_raw


def fetch_avisos():
    """Descarga el paquete de avisos CAP vigentes de toda España y se
    queda solo con los ficheros que mencionan alguna zona de Málaga."""
    raw = aemet_call("/avisos_cap/ultimoelaborado/area/esp")
    tf = tarfile.open(fileobj=io.BytesIO(raw))
    records = []  # {zona, fenomeno, nivel, evento, severidad, desde, hasta}
    for member in tf.getmembers():
        if not member.name.endswith(".xml"):
            continue
        f = tf.extractfile(member)
        if f is None:
            continue
        content = f.read().decode("utf-8", "replace")
        info_blocks = re.findall(r"<info>.*?</info>", content, re.S)
        for info in info_blocks:
            if "<language>es-ES</language>" not in info:
                continue
            areas = re.findall(r"<area>.*?</area>", info, re.S)
            zonas_en_este_info = []
            for area in areas:
                m = re.search(r"AEMET-Meteoalerta zona</valueName>\s*<value>(.*?)</value>", area, re.S)
                if m and m.group(1) in ZONE_META:
                    zonas_en_este_info.append(m.group(1))
            if not zonas_en_este_info:
                continue

            def field(tag):
                mm = re.search(f"<{tag}>(.*?)</{tag}>", info, re.S)
                return mm.group(1).strip() if mm else ""

            nivel_m = re.search(r"AEMET-Meteoalerta nivel</valueName>\s*<value>(.*?)</value>", info, re.S)
            fen_m = re.search(r"AEMET-Meteoalerta fenomeno</valueName>\s*<value>(.*?)</value>", info, re.S)
            nivel = nivel_m.group(1).strip() if nivel_m else "verde"
            fenomeno = fen_m.group(1).split(";", 1)[-1].strip() if fen_m else field("event")

            rec = {
                "fenomeno": fenomeno,
                "nivel": nivel,
                "evento": field("event"),
                "severidad": field("severity"),
                "desde": field("onset") or field("effective"),
                "hasta": field("expires"),
            }
            for zona in zonas_en_este_info:
                records.append({**rec, "zona": zona})

    now = datetime.now(timezone.utc)

    def parse_dt(s):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            return None

    zones_out = {}
    for zcode, meta in ZONE_META.items():
        recs = [r for r in records if r["zona"] == zcode]
        # nivel vigente ahora mismo (onset <= now < hasta)
        vigentes = []
        for r in recs:
            d0, d1 = parse_dt(r["desde"]), parse_dt(r["hasta"])
            if d0 and d1 and d0 <= now < d1:
                vigentes.append(r)
        nivel_actual = "verde"
        if vigentes:
            nivel_actual = max((r["nivel"] for r in vigentes), key=lambda n: NIVEL_ORDEN.get(n, 0))

        # avisos no-verdes futuros/vigentes en las próximas 72h, para la lista de detalle,
        # marcados con su estado (activo ahora mismo, o próximo) para poder filtrarlos
        proximos = []
        for r in recs:
            if r["nivel"] == "verde":
                continue
            d0, d1 = parse_dt(r["desde"]), parse_dt(r["hasta"])
            if d1 and d1 >= now:
                estado_tiempo = "activo" if (d0 and d0 <= now) else "proximo"
                proximos.append({**r, "estado_tiempo": estado_tiempo})
        proximos.sort(key=lambda r: r["desde"])

        zones_out[zcode] = {
            "nombre": meta["nombre"],
            "tipo": meta["tipo"],
            "nivel": nivel_actual,
            "avisos": [
                {k: v for k, v in r.items() if k != "zona"} for r in proximos
            ],
        }

    # todos los registros no-verdes vistos en esta pasada (para fundir con el historial)
    vistos_ahora = [r for r in records if r["nivel"] != "verde"]
    return zones_out, vistos_ahora


def build_historial(vistos_ahora, ruta_json_previo):
    """Mantiene una lista con los avisos no-verdes de los últimos
    HISTORIAL_DIAS días, leyendo el JSON ya publicado (si existe) y
    añadiendo lo nuevo visto en esta pasada. Así el historial sobrevive
    aunque AEMET dé de baja el CAP en cuanto expira."""
    now = datetime.now(timezone.utc)
    previo = []
    if os.path.exists(ruta_json_previo):
        try:
            with open(ruta_json_previo, encoding="utf-8") as f:
                previo = json.load(f).get("historial", [])
        except Exception:  # noqa: BLE001
            previo = []

    nuevos = [
        {
            "zona": ZONE_META.get(r["zona"], {}).get("nombre", r["zona"]),
            "fenomeno": r["fenomeno"],
            "nivel": r["nivel"],
            "desde": r["desde"],
            "hasta": r["hasta"],
        }
        for r in vistos_ahora
    ]

    combinados = {}
    for item in previo + nuevos:
        clave = (item["zona"], item["fenomeno"], item["nivel"], item["desde"])
        combinados[clave] = item  # dedupe

    corte = now.timestamp() - HISTORIAL_DIAS * 86400

    def ts(item):
        try:
            return datetime.fromisoformat(item["desde"].replace("Z", "+00:00")).timestamp()
        except Exception:  # noqa: BLE001
            return 0

    historial = [item for item in combinados.values() if ts(item) >= corte]
    historial.sort(key=ts, reverse=True)
    return historial


def fetch_forecast():
    out = []
    for point in FORECAST_POINTS:
        try:
            raw = aemet_call(f"/prediccion/especifica/municipio/diaria/{point['id']}")
            data = json.loads(raw.decode("latin-1", "replace"))
            dias_raw = data[0]["prediccion"]["dia"]
        except Exception as e:  # noqa: BLE001
            out.append({**point, "error": str(e), "dias": []})
            continue

        dias = []
        for d in dias_raw[:4]:  # hoy + próximos 3 días
            prob = next((p["value"] for p in d.get("probPrecipitacion", []) if p.get("periodo") == "00-24"), None)
            vientos = [v for v in d.get("viento", []) if v.get("velocidad")]
            viento_max = max(vientos, key=lambda v: v["velocidad"], default=None)
            temp = d.get("temperatura", {})
            dias.append({
                "fecha": d.get("fecha", "")[:10],
                "probPrecipitacion": prob,
                "vientoVelocidad": viento_max["velocidad"] if viento_max else None,
                "vientoDireccion": viento_max["direccion"] if viento_max else None,
                "tempMax": temp.get("maxima"),
                "tempMin": temp.get("minima"),
            })
        out.append({**point, "dias": dias})
    return out


def _to_num(v):
    """AEMET devuelve casi todo como string (a veces vacío); normaliza a número o None."""
    if v is None or v == "":
        return None
    try:
        return float(v) if "." in str(v) else int(v)
    except (TypeError, ValueError):
        return None


def _indexar_por_hora(items):
    """Indexa una lista [{value, periodo}] de la API horaria de AEMET por hora
    (0-23). La mayoría de campos usan periodo de 2 dígitos (una hora exacta);
    probPrecipitacion a veces usa un rango de 4 dígitos (p.ej. "0006" = 00h-06h),
    en cuyo caso el valor se replica en todas las horas de ese rango."""
    out = {}
    for it in items or []:
        periodo = str(it.get("periodo", ""))
        valor = it.get("value")
        if len(periodo) == 2 and periodo.isdigit():
            out[int(periodo)] = valor
        elif len(periodo) == 4 and periodo.isdigit():
            ini, fin = int(periodo[:2]), int(periodo[2:])
            for h in range(ini, fin if fin > ini else 24):
                out.setdefault(h, valor)
    return out


def _viento_por_hora(items):
    out = {}
    for it in items or []:
        periodo = str(it.get("periodo", ""))
        if not (len(periodo) == 2 and periodo.isdigit()):
            continue
        vel, direc = it.get("velocidad"), it.get("direccion")
        vel = vel[0] if isinstance(vel, list) and vel else vel
        direc = direc[0] if isinstance(direc, list) and direc else direc
        out[int(periodo)] = {"velocidad": _to_num(vel), "direccion": direc}
    return out


def fetch_forecast_horaria():
    """Previsión HORARIA de hoy (temperatura, precipitación, prob. de lluvia
    y viento por hora) para los mismos puntos de referencia que la diaria."""
    out = []
    for point in FORECAST_POINTS:
        try:
            raw = aemet_call(f"/prediccion/especifica/municipio/horaria/{point['id']}")
            data = json.loads(raw.decode("latin-1", "replace"))
            dia0 = data[0]["prediccion"]["dia"][0]  # primer día = hoy
        except Exception as e:  # noqa: BLE001
            out.append({"id": point["id"], "horas": [], "error": str(e)})
            continue

        # DEBUG temporal (quitar tras diagnosticar el formato real de AEMET horaria):
        print(f"DEBUG horaria {point['id']} keys={list(dia0.keys())}", file=sys.stderr)
        for campo in ("temperatura", "precipitacion", "probPrecipitacion", "estadoCielo", "vientoAndRachaMax"):
            print(f"DEBUG {campo} = {json.dumps(dia0.get(campo))[:700]}", file=sys.stderr)

        temps = _indexar_por_hora(dia0.get("temperatura", []))
        precs = _indexar_por_hora(dia0.get("precipitacion", []))
        probs = _indexar_por_hora(dia0.get("probPrecipitacion", []))
        cielos = _indexar_por_hora(dia0.get("estadoCielo", []))
        vientos = _viento_por_hora(dia0.get("viento", []))  # NB: en la horaria el campo se llama "viento", no "vientoAndRachaMax" (eso es de la diaria)

        horas_presentes = sorted(set(temps) | set(precs) | set(cielos))
        horas = []
        for h in horas_presentes:
            v = vientos.get(h, {})
            horas.append({
                "hora": h,
                "temp": _to_num(temps.get(h)),
                "precip": _to_num(precs.get(h)),
                "probPrecipitacion": _to_num(probs.get(h)),
                "estadoCielo": cielos.get(h) or None,
                "vientoVelocidad": v.get("velocidad"),
                "vientoDireccion": v.get("direccion"),
            })
        out.append({"id": point["id"], "fecha": dia0.get("fecha", "")[:10], "horas": horas})
    return out


def main():
    if not API_KEY:
        print("ERROR: falta la variable de entorno AEMET_API_KEY", file=sys.stderr)
        sys.exit(1)

    out_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "aemet_malaga.json")

    print("Descargando avisos AEMET...")
    avisos, vistos_ahora = fetch_avisos()
    print("Descargando previsión diaria...")
    prevision = fetch_forecast()
    print("Descargando previsión horaria (hoy)...")
    horaria_por_id = {h["id"]: h for h in fetch_forecast_horaria()}
    for p in prevision:
        h = horaria_por_id.get(p["id"])
        p["horas_hoy"] = h.get("horas", []) if h else []
    historial = build_historial(vistos_ahora, out_path)

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "avisos": avisos,
        "prevision": prevision,
        "historial": historial,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"OK -> {out_path}")
    for zcode, z in avisos.items():
        print(f"  {z['nombre']:28s} nivel={z['nivel']}  avisos_proximos={len(z['avisos'])}")
    print(f"  historial: {len(historial)} avisos en los últimos {HISTORIAL_DIAS} días")
    for p in prevision:
        print(f"  {p['nombre']:20s} horas_hoy={len(p.get('horas_hoy', []))}")


if __name__ == "__main__":
    main()
