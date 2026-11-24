"""Cliente de WAQI (World Air Quality Index) para MobiAire Materno.

SOLO LECTURA. El token vive en .env (variable WAQI_TOKEN) y NUNCA se expone al
frontend ni se devuelve en las respuestas. Este módulo:
  - Llama a la API de WAQI con el token del backend.
  - Cachea en disco (~10 min) para no golpear la API en cada carga ni gastar
    cuota.
  - Normaliza los datos (estación, uid, coords, AQI de WAQI, PM2.5/PM10, hora)
    SIN convertir el índice: el AQI de WAQI es escala US-EPA, NO es el ICA del
    proyecto. Se entrega como "indice de origen", claramente identificado.

IMPORTANTE: no toca el motor ICA, ni resultados.json, ni la ingesta histórica.
"""
import json
import html
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent

# Reutiliza el cargador de .env del proyecto (sin dependencias nuevas).
sys.path.insert(0, str(RAIZ))
import os
from ingesta import config  # noqa: E402  (ejecuta _cargar_env() al importar)

BASE = "https://api.waqi.info"
CACHE = RAIZ / "ingesta" / "cache_waqi.json"   # junto a los demás datos; ignorado por git
CACHE_SEGUNDOS = int(os.getenv("WAQI_CACHE_S", "600"))   # 10 min por defecto
# Recuadro ampliado de Bogotá, Soacha y Sabana cercana (Sibaté, Mosquera, Funza,
# Madrid, Chía, Cajicá, Cota, La Calera, Usme, Ciudad Bolívar).
# (lat_sur, lon_oeste, lat_norte, lon_este)
BBOX_BOGOTA = os.getenv("WAQI_BBOX", "4.35,-74.38,4.95,-73.90")
TIMEOUT = 20

# Estaciones de interés en el sur de Bogotá, Soacha y zonas cercanas, confirmadas o
# buscadas en WAQI para asegurar cobertura completa de la región metropolitana.
ESTACIONES_SUR = {
    "ciudad_bolivar": "A522148",
}

# Palabras clave adicionales para descubrir estaciones de WAQI vía /search/
# que a veces no aparecen en /map/bounds/ (p. ej. sensores ciudadanos o CAR).
KEYWORDS_REGION = ("bogota", "soacha", "ciudad bolivar", "sibate", "mosquera", "funza", "madrid", "chia", "cota", "cajica", "usme", "bosa", "kennedy", "tunal", "fontibon", "suba")


class ErrorWAQI(Exception):
    """Fallo al consultar WAQI (sin red, token inválido, respuesta inesperada)."""


def hay_token():
    return bool(os.getenv("WAQI_TOKEN", "").strip())


def _token():
    t = os.getenv("WAQI_TOKEN", "").strip()
    if not t:
        raise ErrorWAQI("No hay WAQI_TOKEN en .env. Añádelo para usar datos recientes.")
    return t


def _pedir(ruta, params):
    """GET a WAQI. Nunca registra ni devuelve el token."""
    p = dict(params)
    p["token"] = _token()
    url = BASE + ruta + "?" + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers={"User-Agent": "MobiAire/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            cuerpo = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        # Enmascara el token si apareciera en el mensaje
        msg = str(e).replace(_token(), "***")
        raise ErrorWAQI(f"No se pudo consultar WAQI: {msg}")
    if cuerpo.get("status") != "ok":
        detalle = json.dumps(cuerpo.get("data"), ensure_ascii=False)
        raise ErrorWAQI(f"WAQI respondió error: {detalle}")
    return cuerpo.get("data")


# ---------------------------------------------------------------------------
# Caché en disco
# ---------------------------------------------------------------------------
def _leer_cache(clave, ignorar_expiracion=False):
    if not CACHE.exists():
        return None
    try:
        todo = json.loads(CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    item = todo.get(clave)
    if not item and clave.startswith("feed_"):
        alt_key = clave.replace("feed_A", "feed_-") if "feed_A" in clave else clave.replace("feed_-", "feed_A")
        item = todo.get(alt_key)
    if not item:
        return None
    if not ignorar_expiracion and (time.time() - item.get("ts", 0) > CACHE_SEGUNDOS):
        return None
    return item.get("valor")


def _guardar_cache(clave, valor):
    todo = {}
    if CACHE.exists():
        try:
            todo = json.loads(CACHE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            todo = {}
    todo[clave] = {"ts": time.time(), "valor": valor}
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(todo, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE)


# ---------------------------------------------------------------------------
# Normalización (sin convertir escalas)
# ---------------------------------------------------------------------------
def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _estacion_de_feed(d):
    """Convierte la respuesta /feed/ en un dict normalizado del proyecto.

    IMPORTANTE sobre unidades: los valores de `iaqi` que devuelve WAQI NO están
    en µg/m³: son SUB-ÍNDICES AQI (escala US-EPA, sin unidad), ya convertidos por
    WAQI. Por eso se nombran aqi_pm25 / aqi_pm10 y nunca se rotulan como µg/m³.
    Las concentraciones reales en µg/m³ viven en el histórico del motor (otra vía).
    """
    ciudad = d.get("city", {}) or {}
    iaqi = d.get("iaqi", {}) or {}
    geo = ciudad.get("geo") or [None, None]
    # Nombres de atribución: WAQI los entrega con entidades HTML (ej. "Bogot&aacute;").
    attrs = [html.unescape(a.get("name", "")) for a in (d.get("attributions") or [])]
    nombre = html.unescape(ciudad.get("name") or "")
    iso = (d.get("time", {}) or {}).get("iso")
    return {
        "uid": d.get("idx"),
        "nombre": nombre,
        "lat": _num(geo[0]) if len(geo) > 0 else None,
        "lon": _num(geo[1]) if len(geo) > 1 else None,
        # AQI de WAQI (escala US-EPA, 0-500, SIN unidad). NO es el ICA del proyecto.
        "aqi_waqi": d.get("aqi"),
        "dominante": d.get("dominentpol"),
        # Sub-índices AQI por contaminante (NO son concentraciones en µg/m³).
        "aqi_pm25": _num((iaqi.get("pm25") or {}).get("v")),
        "aqi_pm10": _num((iaqi.get("pm10") or {}).get("v")),
        "aqi_o3": _num((iaqi.get("o3") or {}).get("v")),
        "aqi_no2": _num((iaqi.get("no2") or {}).get("v")),
        "aqi_so2": _num((iaqi.get("so2") or {}).get("v")),
        "aqi_co": _num((iaqi.get("co") or {}).get("v")),
        "unidad_valores": "sub-índice AQI (US-EPA), NO µg/m³",
        "contaminantes": sorted(iaqi.keys()),
        # Hora de medición normalizada a hora de Bogotá (-05:00)
        "medido_en": (d.get("time", {}) or {}).get("s"),
        "medido_iso": iso,
        "medido_bogota": a_hora_bogota(iso),
        "medido_texto": texto_hora_bogota(iso),
        "reciente": _reciente(iso),
        "fuente": "WAQI",
        "atribucion": attrs,
        "escala_indice": "US-EPA (WAQI) — no es el ICA del proyecto",
    }


from datetime import timedelta

TZ_BOGOTA = timezone(timedelta(hours=-5))


def a_hora_bogota(iso):
    """Convierte una hora ISO (con cualquier offset, p. ej. +09:00) a hora de Bogotá.

    WAQI a veces etiqueta el mismo instante con husos distintos (el feed en -05:00
    y map/bounds en +09:00). El instante es el mismo; aquí se normaliza a -05:00.
    Devuelve texto ISO en -05:00, o el original si no se puede parsear.
    """
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if t.tzinfo is None:
        t = t.replace(tzinfo=TZ_BOGOTA)   # sin offset: WAQI local ya es Bogotá
    return t.astimezone(TZ_BOGOTA).isoformat(timespec="minutes")


_MESES = ["", "ene", "feb", "mar", "abr", "may", "jun",
          "jul", "ago", "sep", "oct", "nov", "dic"]


def texto_hora_bogota(iso):
    """Hora de Bogotá legible para la usuaria: '8 nov 2026, 4:00 p. m.'.

    Devuelve None si no hay fecha, o el texto original si no se puede parsear.
    """
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if t.tzinfo is None:
        t = t.replace(tzinfo=TZ_BOGOTA)
    t = t.astimezone(TZ_BOGOTA)
    h24 = t.hour
    ampm = "a. m." if h24 < 12 else "p. m."
    h12 = h24 % 12 or 12
    return f"{t.day} {_MESES[t.month]} {t.year}, {h12}:{t.minute:02d} {ampm} (hora de Bogotá)"


def _reciente(iso, max_horas=24):
    """True si la medición es de las últimas `max_horas` (24 h; descarta estaciones inactivas por días/años)."""
    if not iso:
        return False
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return False
    ahora = datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (ahora - t).total_seconds() <= max_horas * 3600


# ---------------------------------------------------------------------------
# API pública del módulo
# ---------------------------------------------------------------------------
def _en_bbox(lat, lon, bbox_str=None):
    if lat is None or lon is None:
        return False
    try:
        partes = [float(x) for x in (bbox_str or BBOX_BOGOTA).split(",")]
        if len(partes) != 4:
            return True
        lat_s, lon_w, lat_n, lon_e = partes
        return (lat_s <= float(lat) <= lat_n) and (lon_w <= float(lon) <= lon_e)
    except (TypeError, ValueError):
        return False


def _uid_canon(uid):
    """Normaliza UIDs equivalentes de WAQI (ej. -522148 y A522148)."""
    s = str(uid or "").strip()
    if s.startswith("-") and s[1:].isdigit():
        return "A" + s[1:]
    if s.startswith("@") and s[1:].isdigit():
        return s[1:]
    return s.upper()


def estaciones_bogota(forzar=False):
    """Lista de estaciones de Bogotá, Soacha y zonas cercanas. Usa /map/bounds y /search.

    Devuelve dict: {generado_en, consultado_en, fuente, estaciones:[...]}.
    Cacheado ~10 min. Lanza ErrorWAQI si falla y no hay caché.
    """
    clave = "bogota_bounds"
    if not forzar:
        c = _leer_cache(clave)
        if c is not None:
            c = dict(c)
            c["desde_cache"] = True
            return c

    try:
        data = _pedir("/map/bounds/", {"latlng": BBOX_BOGOTA})
    except ErrorWAQI:
        c = _leer_cache(clave, ignorar_expiracion=True)
        if c is not None:
            c = dict(c)
            c["desde_cache"] = True
            return c
        raise
    estaciones = []
    vistos = set()
    for f in data or []:
        uid = f.get("uid")
        canon = _uid_canon(uid)
        if not canon or canon in vistos:
            continue
        st = f.get("station", {}) or {}
        iso = st.get("time")
        lat = _num(f.get("lat"))
        lon = _num(f.get("lon"))
        vistos.add(canon)
        estaciones.append({
            "uid": uid,
            "nombre": html.unescape(st.get("name") or ""),
            "lat": lat,
            "lon": lon,
            "aqi_waqi": f.get("aqi"),
            "medido_iso": iso,
            "medido_bogota": a_hora_bogota(iso),
            "medido_texto": texto_hora_bogota(iso),
            "reciente": _reciente(iso),
            "fuente": "WAQI",
            "escala_indice": "US-EPA (WAQI) — no es el ICA del proyecto",
        })

    # Descubrimiento adicional con /search/ para estaciones en Bogotá, Soacha y Sabana
    # que /map/bounds/ pueda omitir (incluyendo estaciones sin actualización reciente).
    for kw in KEYWORDS_REGION:
        try:
            s_data = _pedir("/search/", {"keyword": kw})
        except Exception:
            continue
        for f in s_data or []:
            uid = f.get("uid")
            canon = _uid_canon(uid)
            if not canon or canon in vistos:
                continue
            st = f.get("station", {}) or {}
            geo = st.get("geo") or [None, None]
            lat = _num(geo[0]) if len(geo) > 0 else None
            lon = _num(geo[1]) if len(geo) > 1 else None
            if not _en_bbox(lat, lon):
                continue
            t_info = f.get("time") or {}
            iso = t_info.get("stime") or st.get("time")
            tz = t_info.get("tz") or "-05:00"
            if iso and "T" not in str(iso) and " " in str(iso):
                iso = str(iso).strip().replace(" ", "T") + tz
            vistos.add(canon)
            estaciones.append({
                "uid": uid,
                "nombre": html.unescape(st.get("name") or ""),
                "lat": lat,
                "lon": lon,
                "aqi_waqi": f.get("aqi"),
                "medido_iso": iso,
                "medido_bogota": a_hora_bogota(iso),
                "medido_texto": texto_hora_bogota(iso),
                "reciente": _reciente(iso),
                "fuente": "WAQI",
                "escala_indice": "US-EPA (WAQI) — no es el ICA del proyecto",
            })

    # Orden: recientes primero, luego por nombre
    estaciones.sort(key=lambda e: (not e["reciente"], str(e["nombre"])))
    resultado = {
        "fuente": "WAQI / AQICN",
        "atribucion": ["World Air Quality Index Project", "OAB - Observatorio Ambiental de Bogotá", "CAR Cundinamarca"],
        "escala_indice": "US-EPA (WAQI) — no es el ICA del proyecto MobiAire",
        "bbox": BBOX_BOGOTA,
        "consultado_en": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "estaciones": estaciones,
    }
    _guardar_cache(clave, resultado)
    resultado["desde_cache"] = False
    return resultado


def _enriquecer(item, forzar=False):
    """Añade el semáforo del proyecto y TODOS los contaminantes de la estación."""
    item = dict(item)
    item["semaforo"] = None   # ICA estimado del proyecto; None = color neutro (gris)
    if item.get("uid") is not None:
        try:
            det = estacion(item["uid"], forzar=forzar).get("estacion", {})
            # Actualizar estado de reciente según el feed directo de la estación
            if det.get("reciente") is not None:
                item["reciente"] = bool(det.get("reciente"))
            if item.get("reciente"):
                item["semaforo"] = det.get("ica_estimado")   # {ica, nivel, ...} o None
            else:
                item["semaforo"] = None
            # Enriquecer con TODOS los contaminantes que reporte la estación
            # (PM2.5, PM10, NO2, SO2, O3, CO) para no perder ninguno.
            for k in ("aqi_waqi", "aqi_pm25", "aqi_pm10", "aqi_o3", "aqi_no2", "aqi_so2", "aqi_co",
                      "contaminantes", "dominante", "medido_iso", "medido_bogota",
                      "medido_texto", "atribucion"):
                if det.get(k) is not None:
                    item[k] = det[k]
        except ErrorWAQI:
            pass   # esa estación queda en color neutro (gris)
    return item


def estaciones_con_semaforo(forzar=False):
    """Estaciones de Bogotá, Soacha y alrededores + semáforo materno por estación.

    Para cada estación consulta su feed (cacheado ~10 min) y calcula el
    semáforo según ICA.xlsx (verde/naranja/rojo) o la deja en gris si no tiene
    datos recientes. Si una estación deja de reportar en una ingesta futura,
    se mantiene en la base de datos en color gris sin desaparecer.
    """
    base = estaciones_bogota(forzar=forzar)
    salida = [_enriquecer(e, forzar) for e in base.get("estaciones", [])]
    vistos = {_uid_canon(i.get("uid")) for i in salida}
    for uid in ESTACIONES_SUR.values():
        if _uid_canon(uid) in vistos:
            continue
        try:
            det = estacion(uid, forzar=forzar).get("estacion", {})
        except ErrorWAQI:
            continue
        if not det.get("uid"):
            continue
        extra = dict(det)
        extra["semaforo"] = det.get("ica_estimado") if det.get("reciente") else None
        salida.append(extra)
        vistos.add(_uid_canon(det.get("uid")))
    return {
        "fuente": base.get("fuente"),
        "atribucion": base.get("atribucion"),
        "nota_escala": ("Color = semáforo materno del proyecto (tabla ICA.xlsx, "
                        "derivado de sub-índices WAQI). El número grande es el índice WAQI de origen."),
        "consultado_en": base.get("consultado_en"),
        "estaciones": salida,
    }


def _ruta_feed(uid):
    """Construye la ruta /feed/ correcta según el tipo de ID de WAQI.

    WAQI tiene dos tipos de identificador:
      - Internos: número puro (ej. 6235) -> se consulta como /feed/@6235/
      - Externos: con prefijo 'A' (ej. A522148) -> se consulta como /feed/A522148/
    Acepta también '@6235' (ya con arroba).
    """
    uid = str(uid).strip()
    if uid.startswith("@") and uid[1:].isdigit():
        return f"/feed/{uid}/"            # ya viene con arroba y es numérico
    if uid[:1].upper() == "A" and uid[1:].isdigit():
        return f"/feed/{uid}/"            # ID externo: sin @
    # En /map/bounds WAQI entrega idx NEGATIVO para estaciones externas
    # (p. ej. -522148). Se consultan como A{abs}: /feed/A522148/.
    if uid.startswith("-") and uid[1:].isdigit():
        return f"/feed/A{uid[1:]}/"
    if uid.isdigit():
        return f"/feed/@{uid}/"           # ID interno: con @
    raise ErrorWAQI(f"Identificador de estación no válido: {uid!r}")


def estacion(uid, forzar=False):
    """Detalle de una estación por uid. Acepta IDs internos (6235) y externos (A522148)."""
    clave = f"feed_{uid}"
    if not forzar:
        c = _leer_cache(clave)
        if c is not None:
            c = dict(c)
            c["desde_cache"] = True
            return c
    try:
        data = _pedir(_ruta_feed(uid), {})
    except ErrorWAQI:
        c = _leer_cache(clave, ignorar_expiracion=True)
        if c is not None:
            c = dict(c)
            c["desde_cache"] = True
            return c
        raise
    est = _estacion_de_feed(data)
    # ICA estimado y semáforo materno del proyecto (ICA.xlsx) derivado de los sub-índices WAQI.
    # Solo si el dato es reciente; si no, no se calcula semáforo vigente (queda en gris).
    est["ica_estimado"] = None
    if est.get("reciente"):
        from motor_ica.conversion_waqi import ica_desde_waqi
        est["ica_estimado"] = ica_desde_waqi(
            aqi_pm25=est.get("aqi_pm25"),
            aqi_pm10=est.get("aqi_pm10"),
            aqi_o3=est.get("aqi_o3"),
            aqi_no2=est.get("aqi_no2"),
            aqi_waqi=est.get("aqi_waqi"),
            aqi_so2=est.get("aqi_so2"),
            aqi_co=est.get("aqi_co"),
        )
    resultado = {
        "fuente": "WAQI / AQICN",
        "atribucion": est.get("atribucion", []),
        "escala_indice": "US-EPA (WAQI) — no es el ICA del proyecto MobiAire",
        "consultado_en": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "estacion": est,
    }
    _guardar_cache(clave, resultado)
    resultado["desde_cache"] = False
    return resultado


def ciudad_bolivar(forzar=False):
    """Estación de Ciudad Bolívar en WAQI (A522148 = Colegio CEDID Ciudad Bolívar)."""
    return estacion(ESTACIONES_SUR["ciudad_bolivar"], forzar=forzar)
