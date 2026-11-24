"""Pruebas de las alertas cortas y de la estación de referencia (ronda 2).

La alerta debe ser BREVE (una frase) y la usuaria sin zona debe igual recibir
avisos de la estación MÁS CERCANA a su última ubicación conocida.
"""
import json
from datetime import datetime
from pathlib import Path

from web import notificaciones, recomendaciones

RAIZ = Path(__file__).resolve().parent.parent
UPZ_COORDS = json.loads((RAIZ / "datos" / "upz_coords.json").read_text(encoding="utf-8"))


def _estacion(uid, lat, lon, nivel, reciente=True):
    return {"uid": uid, "nombre": "E" + uid, "lat": lat, "lon": lon,
            "reciente": reciente, "semaforo": {"ica": 120, "nivel": nivel}}


def test_alerta_corta_con_nivel(db_tmp):
    c = UPZ_COORDS["el_tesoro"]
    db_tmp.upsert_waqi({"estaciones": [_estacion("A522148", c["lat"], c["lon"], "rojo")]})
    db_tmp.create_user("ana", "1234", role="gestante", profile={"zona": "el_tesoro"})

    assert notificaciones.generar_alertas() == 1

    uid = db_tmp.get_user_by_username("ana")["id"]
    notis = db_tmp.list_notifications(uid)
    assert len(notis) == 1
    n = notis[0]
    assert n["tipo"] == "alerta" and n["nivel"] == "rojo"
    assert n["titulo"] == f"{recomendaciones.emoji_nivel('rojo')} Alerta rojo"
    assert n["mensaje"] == recomendaciones.resumen_alerta("rojo")
    assert len(n["mensaje"]) < 120          # es corta: una sola frase


def test_sin_alertas_en_verde(db_tmp):
    c = UPZ_COORDS["el_tesoro"]
    db_tmp.upsert_waqi({"estaciones": [_estacion("A522148", c["lat"], c["lon"], "verde")]})
    db_tmp.create_user("ana", "1234", role="gestante", profile={"zona": "el_tesoro"})
    assert notificaciones.generar_alertas() == 0


def test_estacion_por_ubicacion_cercana():
    estaciones = {
        "A1": _estacion("A1", 4.60, -74.10, "verde"),
        "A2": _estacion("A2", 4.57, -74.13, "rojo"),
    }
    est = notificaciones._estacion_de_usuario({"lat": 4.5701, "lon": -74.1301}, estaciones, {})
    assert est["uid"] == "A2"


def test_sin_zona_ni_ubicacion_no_hay_estacion():
    estaciones = {"A1": _estacion("A1", 4.60, -74.10, "verde")}
    assert notificaciones._estacion_de_usuario({}, estaciones, {}) is None


def test_reporte_diario_corto(db_tmp):
    c = UPZ_COORDS["el_tesoro"]
    db_tmp.upsert_waqi({"estaciones": [_estacion("A522148", c["lat"], c["lon"], "naranja")]})
    db_tmp.create_user("ana", "1234", role="gestante",
                       profile={"zona": "el_tesoro", "hora_reporte": "08:00"})

    assert notificaciones.generar_reportes_diarios(ahora=datetime(2026, 6, 1, 23, 59)) == 1

    uid = db_tmp.get_user_by_username("ana")["id"]
    n = db_tmp.list_notifications(uid)[0]
    assert n["tipo"] == "reporte_diario"
    assert n["titulo"] == f"{recomendaciones.emoji_nivel('naranja')} Recomendaciones para hoy"
    assert "Recomendaciones para hoy" in n["titulo"]
