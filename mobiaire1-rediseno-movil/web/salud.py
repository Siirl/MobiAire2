"""Hospitales y centros de salud cercanos (servidor como conmutador).

El cliente NUNCA llama a Overpass/OpenStreetMap directamente: pide al servidor
`/api/salud/cercanos?lat=..&lon=..`, y el servidor:

  1) Busca en la base de datos (caché) los lugares dentro del radio pedido.
  2) Si no hay (o la caché es vieja), consulta la API de Overpass de
     OpenStreetMap, NORMALIZA los resultados y los GUARDA en la base de datos.
  3) Devuelve los lugares ordenados por distancia.

Así las búsquedas siguientes son instantáneas y funcionan aunque Overpass esté
lento o caído (se sirve lo último guardado).
"""
import json
import math
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import db

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
OVERPASS = OVERPASS_URLS[0]
FUENTE = "OpenStreetMap (Overpass API) + Directorio Oficial Salud Bogotá/Soacha"
CACHE_HORAS = 24
AMENITIES = "hospital|clinic|doctors"
# Recuadro Bogotá, Soacha y municipios cercanos (sur, oeste, norte, este)
BBOX_SALUD = (4.42, -74.30, 4.86, -74.00)

# Directorio base verificado de hospitales y centros de salud públicos/privados
# de Ciudad Bolívar, Sur de Bogotá, Soacha y red hospitalaria principal de Bogotá.
# Se siembra al iniciar para que la base de datos siempre tenga cobertura inmediata.
CENTROS_BASE = [
    {"osm_id": "base/hospital_meissen", "nombre": "Hospital de Meissen II Nivel E.S.E. (Subred Sur)", "lat": 4.5594, "lon": -74.1369, "telefono": "6017693131", "tipo": "hospital", "direccion": "Calle 60G Sur # 18B-56, Ciudad Bolívar", "descripcion": "Urgencias obstétricas y ginecológicas 24h - Subred Integrada de Servicios de Salud Sur", "operador": "Subred Sur E.S.E.", "web": ""},
    {"osm_id": "base/hospital_vista_hermosa", "nombre": "Hospital Vista Hermosa I Nivel (USS Vista Hermosa)", "lat": 4.5503, "lon": -74.1453, "telefono": "6017926161", "tipo": "hospital", "direccion": "Carrera 18C # 66A-55 Sur, Ciudad Bolívar", "descripcion": "Atención materna y urgencias - Ciudad Bolívar", "operador": "Subred Sur E.S.E.", "web": ""},
    {"osm_id": "base/caps_candelaria", "nombre": "CAPS Candelaria La Nueva (USS Candelaria)", "lat": 4.5681, "lon": -74.1489, "telefono": "6017150300", "tipo": "clinic", "direccion": "Carrera 28 # 63B-07 Sur, Ciudad Bolívar", "descripcion": "Centro de Atención Prioritaria en Salud - Ciudad Bolívar", "operador": "Subred Sur E.S.E.", "web": ""},
    {"osm_id": "base/uss_jerusalen", "nombre": "Centro de Salud Jerusalén (USS Jerusalén)", "lat": 4.5665, "lon": -74.1651, "telefono": "6017311000", "tipo": "clinic", "direccion": "Calle 72 Sur # 46A-08, Ciudad Bolívar", "descripcion": "Unidad de Servicios de Salud Jerusalén - Control prenatal", "operador": "Subred Sur E.S.E.", "web": ""},
    {"osm_id": "base/uss_perdomo", "nombre": "Centro de Salud Ismael Perdomo (USS Perdomo)", "lat": 4.5861, "lon": -74.1703, "telefono": "6017751200", "tipo": "clinic", "direccion": "Calle 63A Sur # 71F-49, Ciudad Bolívar", "descripcion": "Unidad de Servicios de Salud Ismael Perdomo", "operador": "Subred Sur E.S.E.", "web": ""},
    {"osm_id": "base/uss_lucero", "nombre": "Centro de Salud Lucero Medio (USS Lucero)", "lat": 4.5495, "lon": -74.1502, "telefono": "6017901234", "tipo": "clinic", "direccion": "Carrera 17F # 64B-22 Sur, Ciudad Bolívar", "descripcion": "Unidad de Servicios de Salud Lucero", "operador": "Subred Sur E.S.E.", "web": ""},
    {"osm_id": "base/uss_paraiso", "nombre": "Centro de Salud Paraíso (USS Paraíso)", "lat": 4.5411, "lon": -74.1567, "telefono": "6017926161", "tipo": "clinic", "direccion": "Carrera 27L # 71H-35 Sur, Ciudad Bolívar", "descripcion": "Unidad de Servicios de Salud El Paraíso", "operador": "Subred Sur E.S.E.", "web": ""},
    {"osm_id": "base/uss_san_francisco", "nombre": "Centro de Salud San Francisco (USS San Francisco)", "lat": 4.5648, "lon": -74.1471, "telefono": "6017150300", "tipo": "clinic", "direccion": "Carrera 20B # 65-03 Sur, Ciudad Bolívar", "descripcion": "Unidad de Servicios de Salud San Francisco", "operador": "Subred Sur E.S.E.", "web": ""},
    {"osm_id": "base/uss_mochuelo", "nombre": "Centro de Salud Mochuelo Bajo (USS Mochuelo)", "lat": 4.5315, "lon": -74.1398, "telefono": "6017926161", "tipo": "clinic", "direccion": "Vereda Mochuelo Bajo, Ciudad Bolívar", "descripcion": "Unidad de Servicios de Salud Mochuelo", "operador": "Subred Sur E.S.E.", "web": ""},
    {"osm_id": "base/uss_pasquilla", "nombre": "Centro de Salud Pasquilla (USS Pasquilla)", "lat": 4.4512, "lon": -74.1518, "telefono": "6017926161", "tipo": "clinic", "direccion": "Corregimiento Pasquilla, Ciudad Bolívar", "descripcion": "Unidad de Servicios de Salud Rural Pasquilla", "operador": "Subred Sur E.S.E.", "web": ""},
    {"osm_id": "base/hospital_tunal", "nombre": "Hospital El Tunal III Nivel E.S.E. (Subred Sur)", "lat": 4.5721, "lon": -74.1294, "telefono": "6017428585", "tipo": "hospital", "direccion": "Carrera 20 # 47B-35 Sur, Tunjuelito", "descripcion": "Alta complejidad obstétrica, UCI neonatal y materna 24h", "operador": "Subred Sur E.S.E.", "web": ""},
    {"osm_id": "base/hospital_tunjuelito", "nombre": "Hospital Tunjuelito (USS Abraham Lincoln)", "lat": 4.5668, "lon": -74.1361, "telefono": "6017142100", "tipo": "hospital", "direccion": "Transversal 44 # 51B-16 Sur, Tunjuelito", "descripcion": "Atención prioritaria y urgencias", "operador": "Subred Sur E.S.E.", "web": ""},
    {"osm_id": "base/hospital_bosa", "nombre": "Hospital de Bosa II Nivel", "lat": 4.6184, "lon": -74.1972, "telefono": "6017801515", "tipo": "hospital", "direccion": "Calle 73 Sur # 100A-53, Bosa", "descripcion": "Urgencias y maternidad 24h - Subred Sur Occidente", "operador": "Subred Sur Occidente E.S.E.", "web": ""},
    {"osm_id": "base/uss_pablo_vi_bosa", "nombre": "Hospital Pablo VI Bosa I Nivel", "lat": 4.6098, "lon": -74.1856, "telefono": "6017799800", "tipo": "hospital", "direccion": "Calle 70 Sur # 77I-60, Bosa", "descripcion": "Urgencias y atención materna - Bosa", "operador": "Subred Sur Occidente E.S.E.", "web": ""},
    {"osm_id": "base/hospital_kennedy", "nombre": "Hospital Occidente de Kennedy III Nivel", "lat": 4.6171, "lon": -74.1534, "telefono": "6014480030", "tipo": "hospital", "direccion": "Transversal 74F # 40B-54 Sur, Kennedy", "descripcion": "Alta complejidad obstétrica y urgencias 24h", "operador": "Subred Sur Occidente E.S.E.", "web": ""},
    {"osm_id": "base/clinica_occidente", "nombre": "Clínica del Occidente", "lat": 4.6305, "lon": -74.1365, "telefono": "6014254620", "tipo": "hospital", "direccion": "Avenida de las Américas # 71C-29, Kennedy", "descripcion": "Urgencias 24h y alta complejidad gineco-obstétrica", "operador": "Clínica del Occidente", "web": ""},
    {"osm_id": "base/hospital_usme", "nombre": "Hospital de Usme I Nivel (Subred Sur)", "lat": 4.4828, "lon": -74.1197, "telefono": "6017660666", "tipo": "hospital", "direccion": "Carrera 13 # 135A-42 Sur, Usme", "descripcion": "Urgencias y atención materna - Usme", "operador": "Subred Sur E.S.E.", "web": ""},
    {"osm_id": "base/uss_santa_librada", "nombre": "CAMI Santa Librada (USS Santa Librada)", "lat": 4.5169, "lon": -74.1135, "telefono": "6017631100", "tipo": "hospital", "direccion": "Carrera 9B # 75-31 Sur, Usme", "descripcion": "Urgencias 24h y atención de partos de bajo riesgo", "operador": "Subred Sur E.S.E.", "web": ""},
    {"osm_id": "base/hospital_soacha_mario_gaitan", "nombre": "Hospital Mario Gaitán Yanguas E.S.E. de Soacha", "lat": 4.5786, "lon": -74.2156, "telefono": "6017309230", "tipo": "hospital", "direccion": "Calle 13 # 9-45, Soacha Centro", "descripcion": "Hospital público principal de Soacha - Urgencias y maternidad 24h", "operador": "E.S.E. Hospital Mario Gaitán Yanguas", "web": ""},
    {"osm_id": "base/cardiovascular_soacha", "nombre": "Hospital Cardiovascular de Cundinamarca (Soacha)", "lat": 4.5882, "lon": -74.1989, "telefono": "6015922999", "tipo": "hospital", "direccion": "Carrera 1 Este # 31-58, San Mateo, Soacha", "descripcion": "Urgencias de alta complejidad 24h, ginecología y obstetricia", "operador": "Hospital Cardiovascular del Niño de Cundinamarca", "web": ""},
    {"osm_id": "base/ese_soacha_compartir", "nombre": "E.S.E. Municipal de Soacha Julio César Peñaloza - Compartir", "lat": 4.5675, "lon": -74.2361, "telefono": "6017212583", "tipo": "clinic", "direccion": "Carrera 15 # 22A-55 Sur, Compartir, Soacha", "descripcion": "Centro de salud y urgencias de primer nivel - Soacha", "operador": "E.S.E. Municipal de Soacha", "web": ""},
    {"osm_id": "base/ese_soacha_san_marcos", "nombre": "Centro de Salud San Marcos - E.S.E. Municipal Soacha", "lat": 4.5819, "lon": -74.2054, "telefono": "6017212583", "tipo": "clinic", "direccion": "Calle 25 # 4-25 Este, San Marcos, Soacha", "descripcion": "Centro de salud materno-infantil Soacha", "operador": "E.S.E. Municipal de Soacha", "web": ""},
    {"osm_id": "base/ese_soacha_ciudadela_sucre", "nombre": "Centro de Salud Ciudadela Sucre / Cazucá", "lat": 4.5795, "lon": -74.1812, "telefono": "6017212583", "tipo": "clinic", "direccion": "Altos de Cazucá, Comuna 4, Soacha", "descripcion": "Centro de salud cercano al límite Ciudad Bolívar - Soacha", "operador": "E.S.E. Municipal de Soacha", "web": ""},
    {"osm_id": "base/hospital_santa_clara", "nombre": "Hospital Santa Clara III Nivel (Subred Centro Oriente)", "lat": 4.5858, "lon": -74.0933, "telefono": "6013282828", "tipo": "hospital", "direccion": "Carrera 14B # 1-45 Sur, Antonio Nariño", "descripcion": "Urgencias y alta complejidad 24h", "operador": "Subred Centro Oriente E.S.E.", "web": ""},
    {"osm_id": "base/hospital_san_rafael", "nombre": "Hospital Universitario Clínica San Rafael", "lat": 4.5761, "lon": -74.0914, "telefono": "6013282300", "tipo": "hospital", "direccion": "Carrera 8 # 17-45 Sur, San Cristóbal", "descripcion": "Urgencias obstétricas y UCI neonatal 24h", "operador": "Clínica San Rafael", "web": ""},
    {"osm_id": "base/hospital_victoria", "nombre": "Hospital La Victoria III Nivel (Unidad Materno Infantil)", "lat": 4.5525, "lon": -74.0911, "telefono": "6013726925", "tipo": "hospital", "direccion": "Diagonal 39 Sur # 3-20 Este, San Cristóbal", "descripcion": "Instituto Materno Infantil y urgencias gineco-obstétricas 24h", "operador": "Subred Centro Oriente E.S.E.", "web": ""},
    {"osm_id": "base/instituto_materno_infantil", "nombre": "Hospital Materno Infantil (Subred Centro Oriente)", "lat": 4.5892, "lon": -74.0844, "telefono": "6013282828", "tipo": "hospital", "direccion": "Carrera 10 # 1-66 Sur, Bogotá", "descripcion": "Centro especializado en atención materna, alto riesgo obstétrico y neonatología", "operador": "Subred Centro Oriente E.S.E.", "web": ""},
    {"osm_id": "base/hospital_fontibon", "nombre": "Hospital de Fontibón II Nivel", "lat": 4.6745, "lon": -74.1421, "telefono": "6014860033", "tipo": "hospital", "direccion": "Carrera 99 # 16I-41, Fontibón", "descripcion": "Urgencias y maternidad 24h", "operador": "Subred Sur Occidente E.S.E.", "web": ""},
    {"osm_id": "base/hospital_engativa", "nombre": "Hospital de Engativá II Nivel", "lat": 4.7102, "lon": -74.1112, "telefono": "6012913300", "tipo": "hospital", "direccion": "Transversal 100A # 80A-50, Engativá", "descripcion": "Urgencias y maternidad 24h", "operador": "Subred Norte E.S.E.", "web": ""},
    {"osm_id": "base/hospital_suba", "nombre": "Hospital de Suba II Nivel", "lat": 4.7418, "lon": -74.0894, "telefono": "6016621111", "tipo": "hospital", "direccion": "Carrera 104 # 152C-50, Suba", "descripcion": "Urgencias y maternidad 24h", "operador": "Subred Norte E.S.E.", "web": ""},
    {"osm_id": "base/hospital_simon_bolivar", "nombre": "Hospital Simón Bolívar III Nivel", "lat": 4.7481, "lon": -74.0272, "telefono": "6016767940", "tipo": "hospital", "direccion": "Calle 165 # 7-06, Usaquén", "descripcion": "Urgencias y alta complejidad 24h", "operador": "Subred Norte E.S.E.", "web": ""},
]


def _haversine_km(lat1, lon1, lat2, lon2):
    r = math.radians
    dlat = r(lat2 - lat1)
    dlon = r(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _consulta(lat, lon, radio_m):
    return (
        f"[out:json][timeout:25];"
        f"(node[\"amenity\"~\"{AMENITIES}\"](around:{radio_m},{lat},{lon});"
        f"way[\"amenity\"~\"{AMENITIES}\"](around:{radio_m},{lat},{lon}););"
        f"out center tags;"
    )


def _texto(tags, *claves):
    for k in claves:
        v = tags.get(k)
        if v:
            return str(v)
    return ""


def _normalizar(el):
    tags = el.get("tags") or {}
    centro = el.get("center") or {}
    lat = el.get("lat", centro.get("lat"))
    lon = el.get("lon", centro.get("lon"))
    if lat is None or lon is None:
        return None
    calle = _texto(tags, "addr:street")
    numero = _texto(tags, "addr:housenumber")
    ciudad = _texto(tags, "addr:city", "addr:suburb")
    direccion = " ".join(p for p in (calle, numero) if p)
    if ciudad:
        direccion = (direccion + ", " + ciudad).strip(", ")
    return {
        "osm_id": f"{el.get('type', 'node')}/{el.get('id')}",
        "nombre": _texto(tags, "name") or "Centro de salud",
        "lat": float(lat),
        "lon": float(lon),
        "telefono": _texto(tags, "phone", "contact:phone", "contact:mobile"),
        "tipo": _texto(tags, "amenity", "healthcare") or "salud",
        "direccion": direccion,
        "descripcion": _texto(tags, "description", "healthcare:speciality", "operator"),
        "operador": _texto(tags, "operator"),
        "web": _texto(tags, "website", "contact:website"),
    }


def _consulta_bbox(bbox=BBOX_SALUD):
    s, w, n, e = bbox
    return (
        f"[out:json][timeout:40];"
        f"(node[\"amenity\"~\"{AMENITIES}\"]({s},{w},{n},{e});"
        f"way[\"amenity\"~\"{AMENITIES}\"]({s},{w},{n},{e}););"
        f"out center tags;"
    )


def _ejecutar_overpass(query_str, timeout=30):
    datos = urllib.parse.urlencode({"data": query_str}).encode("utf-8")
    ultimo_err = None
    for url in OVERPASS_URLS:
        try:
            req = urllib.request.Request(url, data=datos,
                                         headers={"User-Agent": "MobiAireMaterno/2.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.loads(r.read().decode("utf-8"))
            salida = []
            for el in j.get("elements", []):
                n = _normalizar(el)
                if n:
                    salida.append(n)
            return salida
        except Exception as e:
            ultimo_err = e
    if ultimo_err:
        raise ultimo_err
    return []


def _descargar(lat, lon, radio_m):
    return _ejecutar_overpass(_consulta(lat, lon, radio_m), timeout=30)


def _descargar_region():
    return _ejecutar_overpass(_consulta_bbox(BBOX_SALUD), timeout=45)


def asegurar_base_salud():
    """Garantiza que la base de datos tenga al menos el directorio base de hospitales."""
    try:
        if db.count_lugares_salud() < len(CENTROS_BASE):
            db.upsert_lugares_salud(CENTROS_BASE)
    except Exception:
        pass


def refrescar_salud_region(forzar=False):
    """Ingesta en el servidor de hospitales de Bogotá, Soacha y alrededores (cada 24 h).

    Se ejecuta al iniciar el servidor y una vez al día en segundo plano,
    como con WAQI, para que el cliente consulte únicamente lo que el servidor
    ya tiene en SQLite sin esperar a la API externa.
    """
    asegurar_base_salud()
    guardados = db.get_lugares_salud()
    if not forzar and len(guardados) > len(CENTROS_BASE) and _cache_fresca(guardados):
        return {"ok": True, "lugares": len(guardados), "desde_cache": True}
    try:
        nuevos = _descargar_region()
        if nuevos:
            db.upsert_lugares_salud(CENTROS_BASE + nuevos)
        else:
            db.upsert_lugares_salud(CENTROS_BASE)
        db.set_setting("salud_ultima_ingesta", datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"))
        return {"ok": True, "lugares": db.count_lugares_salud(), "desde_cache": False}
    except Exception as e:
        db.set_setting("salud_error", str(e))
        return {"ok": False, "lugares": db.count_lugares_salud(), "error": str(e)}


def programar_ingesta_salud(intervalo_horas=24):
    """Lanza un hilo en segundo plano que actualiza la BD de centros de salud cada día."""
    asegurar_base_salud()

    def bucle():
        while True:
            try:
                refrescar_salud_region(forzar=False)
            except Exception:
                pass
            time.sleep(max(1, intervalo_horas) * 3600)

    hilo = threading.Thread(target=bucle, name="ingesta-salud", daemon=True)
    hilo.start()
    return hilo


def _en_radio(lugares, lat, lon, radio_m):
    cerca = []
    for p in lugares:
        try:
            d = _haversine_km(lat, lon, float(p["lat"]), float(p["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        if d * 1000.0 <= radio_m:
            q = dict(p)
            q["distancia_km"] = round(d, 2)
            cerca.append(q)
    cerca.sort(key=lambda p: p["distancia_km"])
    return cerca


def _mas_cercanos(lugares, lat, lon, limite=10):
    todos = []
    for p in lugares:
        try:
            d = _haversine_km(lat, lon, float(p["lat"]), float(p["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        q = dict(p)
        q["distancia_km"] = round(d, 2)
        todos.append(q)
    todos.sort(key=lambda p: p["distancia_km"])
    return todos[:limite]


def _cache_fresca(lugares):
    if not lugares:
        return False
    corte = datetime.now(timezone.utc).astimezone() - timedelta(hours=CACHE_HORAS)
    for p in lugares:
        try:
            t = datetime.fromisoformat(p.get("_cache_en", ""))
        except (ValueError, TypeError):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=corte.tzinfo)
        if t >= corte:
            return True
    return False


def buscar_cercanos(lat, lon, radio_m=5000, forzar=False, solo_cache=False):
    """Lugares de salud cerca de (lat, lon). Sirve directamente desde la BD del servidor.

    Si la BD ya tiene centros cercanos y la caché es fresca (o se pide `solo_cache`),
    responde de inmediato sin esperar a la API externa. Si la BD está vacía o
    vencida, consulta Overpass (o usa el directorio base si la red falla).
    """
    radio_m = max(500, min(int(radio_m or 5000), 25000))
    guardados = db.get_lugares_salud()
    cerca = _en_radio(guardados, lat, lon, radio_m)

    desde_cache = bool(cerca) and not forzar and (_cache_fresca(guardados) or solo_cache)
    if not solo_cache and (forzar or not cerca or not _cache_fresca(guardados)):
        try:
            nuevos = _descargar(lat, lon, radio_m)
            if nuevos:
                db.upsert_lugares_salud(nuevos)
            guardados = db.get_lugares_salud()
            cerca = _en_radio(guardados, lat, lon, radio_m)
            desde_cache = False
        except Exception:  # red caída o Overpass lento: servimos de la BD local
            if not guardados:
                asegurar_base_salud()
                guardados = db.get_lugares_salud()
            cerca = _en_radio(guardados, lat, lon, radio_m)
            desde_cache = True

    # Si en el radio exacto no salió ninguno pero la BD tiene centros (o podemos sembrarlos),
    # devolver los más cercanos de la base de datos para nunca dejar vacía la emergencia.
    if not cerca:
        if not guardados:
            asegurar_base_salud()
            guardados = db.get_lugares_salud()
        cerca = _en_radio(guardados, lat, lon, max(radio_m, 15000))
        if not cerca and guardados:
            cerca = _mas_cercanos(guardados, lat, lon, limite=8)

    return {
        "fuente": FUENTE,
        "atribucion": "© OpenStreetMap contributors / Red Hospitalaria Bogotá-Soacha",
        "radio_m": radio_m,
        "desde_cache": desde_cache,
        "total": len(cerca),
        "lugares": cerca,
    }
