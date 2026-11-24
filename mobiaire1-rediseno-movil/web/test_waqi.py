"""Pruebas de web/waqi.py que no requieren red: rutas, hora y unidades."""
import pytest

from web import waqi


def test_ruta_feed_acepta_formatos():
    assert waqi._ruta_feed("6235") == "/feed/@6235/"
    assert waqi._ruta_feed("@6235") == "/feed/@6235/"
    assert waqi._ruta_feed("A522148") == "/feed/A522148/"
    # idx negativo de /map/bounds (estación externa) -> se consulta como A{abs}
    assert waqi._ruta_feed("-522148") == "/feed/A522148/"
    assert waqi._ruta_feed(-522148) == "/feed/A522148/"


def test_ruta_feed_rechaza_invalidos():
    for mal in ("abc", "../x", "@@3", "A", "@", "-", "--5", "A12x"):
        with pytest.raises(waqi.ErrorWAQI):
            waqi._ruta_feed(mal)


def test_a_hora_bogota_convierte_offset():
    # +09:00 (como da map/bounds) -> -05:00 (Bogotá), mismo instante
    assert waqi.a_hora_bogota("2026-11-09T05:00:00+09:00") == "2026-11-08T15:00-05:00"


def test_texto_hora_bogota_legible():
    t = waqi.texto_hora_bogota("2026-11-08T16:00:00-05:00")
    assert "8 nov 2026" in t and "4:00 p. m." in t and "Bogotá" in t
    # medianoche y mediodía
    assert "12:00 a. m." in waqi.texto_hora_bogota("2026-11-08T00:00:00-05:00")
    assert "12:00 p. m." in waqi.texto_hora_bogota("2026-11-08T12:00:00-05:00")
    assert waqi.texto_hora_bogota(None) is None


def test_estacion_de_feed_unescape_y_unidades():
    d = {"idx": -522148, "aqi": 52, "dominentpol": "pm25",
         "city": {"name": "Ciudad Bol&iacute;var", "geo": ["4.57", "-74.16"]},
         "iaqi": {"pm25": {"v": 52}, "pm10": {"v": 50}},
         "time": {"iso": "2026-11-08T09:00:00-05:00"},
         "attributions": [{"name": "OAB - Bogot&aacute;"}]}
    e = waqi._estacion_de_feed(d)
    assert "&iacute;" not in e["nombre"] and e["nombre"] == "Ciudad Bolívar"
    assert "&aacute;" not in e["atribucion"][0]
    # los valores son sub-índices AQI, no µg/m³
    assert e["aqi_pm25"] == 52 and "pm25" not in e
    assert "NO µg/m³" in e["unidad_valores"]


def test_con_semaforo_agrega_ciudad_bolivar(monkeypatch):
    """Ciudad Bolívar (A522148) se agrega aunque no venga en /map/bounds."""
    from datetime import datetime, timezone

    base = {"fuente": "WAQI / AQICN", "atribucion": [], "consultado_en": "x",
            "estaciones": [{"uid": 6235, "nombre": "Tunal", "lat": 4.58, "lon": -74.13,
                            "reciente": True}]}
    ahora = datetime.now(timezone.utc).isoformat()

    def fake_estacion(uid, forzar=False):
        assert str(uid) in ("6235", "A522148")
        return {"estacion": {"uid": 6235 if str(uid) == "6235" else -522148,
                             "nombre": "Tunal" if str(uid) == "6235" else "Ciudad Bolívar",
                             "lat": 4.57, "lon": -74.13, "reciente": True,
                             "medido_iso": ahora,
                             "ica_estimado": {"ica": 67, "nivel": "naranja"}}}

    monkeypatch.setattr(waqi, "estaciones_bogota", lambda forzar=False: base)
    monkeypatch.setattr(waqi, "estacion", fake_estacion)

    out = waqi.estaciones_con_semaforo()
    uids = {str(e["uid"]) for e in out["estaciones"]}
    assert uids == {"6235", "-522148"}
    cb = next(e for e in out["estaciones"] if str(e["uid"]) == "-522148")
    assert cb["nombre"] == "Ciudad Bolívar"
    assert cb["semaforo"]["nivel"] == "naranja"


def test_estaciones_sin_dato_reciente_quedan_en_gris_sin_desaparecer(db_tmp):
    """Si una estación deja de actualizarse, se mantiene en la BD en gris (reciente=False, semaforo=None)."""
    db_tmp.upsert_waqi({"estaciones": [
        {"uid": "6235", "nombre": "Tunal", "lat": 4.57, "lon": -74.13,
         "medido_iso": "2020-01-01T00:00:00-05:00", "reciente": False,
         "semaforo": {"ica": 50, "nivel": "naranja"}},
        {"uid": "A522148", "nombre": "Ciudad Bolívar", "lat": 4.58, "lon": -74.16,
         "reciente": True, "semaforo": {"ica": 30, "nivel": "verde"}},
    ]})
    # Siguiente ingesta: solo viene Ciudad Bolívar; Tunal NO debe desaparecer, debe quedar en gris.
    db_tmp.upsert_waqi({"estaciones": [
        {"uid": "A522148", "nombre": "Ciudad Bolívar", "lat": 4.58, "lon": -74.16,
         "reciente": True, "semaforo": {"ica": 30, "nivel": "verde"}},
    ]})
    guardadas = {e["uid"]: e for e in db_tmp.get_waqi_stations()["estaciones"]}
    assert "6235" in guardadas and "A522148" in guardadas
    assert guardadas["6235"]["reciente"] is False
    assert guardadas["6235"]["semaforo"] is None


def test_con_semaforo_no_duplica_ciudad_bolivar(monkeypatch):
    base = {"fuente": "WAQI", "atribucion": [], "consultado_en": "x",
            "estaciones": [{"uid": -522148, "nombre": "Ciudad Bolívar", "reciente": True}]}
    monkeypatch.setattr(waqi, "estaciones_bogota", lambda forzar=False: base)
    monkeypatch.setattr(waqi, "estacion", lambda uid, forzar=False: {
        "estacion": {"uid": -522148, "nombre": "Ciudad Bolívar", "reciente": True,
                     "ica_estimado": {"ica": 50, "nivel": "verde"}}})
    out = waqi.estaciones_con_semaforo()
    assert len(out["estaciones"]) == 1
