"""Pruebas de la ingesta WAQI: el servidor como conmutador (requisitos 1-3).

Se sustituye la llamada real a WAQI para no usar red ni el token.
"""
import pytest

from ingesta import ingesta_waqi


def _fake_estaciones():
    return {
        "fuente": "WAQI / AQICN",
        "atribucion": ["World Air Quality Index Project"],
        "consultado_en": "2026-06-01T10:00:00-05:00",
        "estaciones": [
            {"uid": "A522148", "nombre": "Ciudad Bolivar", "lat": 4.57, "lon": -74.13,
             "aqi_waqi": 67, "aqi_pm25": 67, "aqi_pm10": 40, "reciente": True,
             "contaminantes": ["pm25", "pm10"], "semaforo": {"ica": 67, "nivel": "naranja"}},
            {"uid": "6235", "nombre": "Tunal", "lat": 4.58, "lon": -74.13,
             "aqi_waqi": 30, "aqi_pm25": 30, "reciente": True,
             "contaminantes": ["pm25"], "semaforo": {"ica": 30, "nivel": "verde"}},
        ],
    }


def test_refrescar_guarda_en_db(db_tmp, monkeypatch):
    from web import waqi
    monkeypatch.setattr(waqi, "estaciones_con_semaforo",
                        lambda forzar=False: _fake_estaciones())

    res = ingesta_waqi.refrescar()
    assert res["ok"] is True
    assert res["estaciones"] == 2
    assert db_tmp.count_waqi() == 2
    assert db_tmp.get_waqi_station("A522148")["nombre"] == "Ciudad Bolivar"
    # Se registra la corrida de ingesta (auditoría del conmutador).
    ultimo = db_tmp.last_ingest_run()
    assert ultimo["ok"] == 1 and ultimo["estaciones"] == 2
    estado = ingesta_waqi.estado()
    assert estado["estaciones_en_db"] == 2
    assert estado["ultima_ok"]


def test_refrescar_registra_error_sin_tumbar(db_tmp, monkeypatch):
    from web import waqi

    def boom(forzar=False):
        raise waqi.ErrorWAQI("sin red")

    monkeypatch.setattr(waqi, "estaciones_con_semaforo", boom)
    with pytest.raises(waqi.ErrorWAQI):
        ingesta_waqi.refrescar()
    ultimo = db_tmp.last_ingest_run()
    assert ultimo["ok"] == 0
    assert "sin red" in ultimo["detalle"]
    assert "sin red" in (db_tmp.get_setting("waqi_error") or "")
    assert db_tmp.count_waqi() == 0


def test_refrescar_respeta_limite(db_tmp, monkeypatch):
    from web import waqi
    monkeypatch.setattr(waqi, "estaciones_con_semaforo",
                        lambda forzar=False: _fake_estaciones())
    res = ingesta_waqi.refrescar(limite=1)
    assert res["estaciones"] == 1
    assert db_tmp.count_waqi() == 1
