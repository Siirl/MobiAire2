"""Pruebas de la base de datos: usuarios, roles, sesiones, conmutador y modo prueba."""
import json

import pytest


def test_admin_unico_y_password(db_tmp):
    usuarios = db_tmp.list_users()
    admins = [u for u in usuarios if u["role"] == "admin"]
    assert len(admins) == 1
    assert admins[0]["username"] == "admin"
    u = db_tmp.get_user_by_username("admin")
    assert db_tmp.verify_password("Analissdesistemas", u["password_hash"])
    assert not db_tmp.verify_password("otra", u["password_hash"])


def test_no_se_puede_crear_segundo_admin(db_tmp):
    db_tmp.create_user("otro", "1234", role="admin")
    assert len([u for u in db_tmp.list_users() if u["role"] == "admin"]) == 2
    # init_db restaura la regla de un único administrador.
    db_tmp.init_db()
    admins = [u for u in db_tmp.list_users() if u["role"] == "admin"]
    assert [a["username"] for a in admins] == ["admin"]


def test_password_cifrada_no_plana(db_tmp):
    db_tmp.create_user("ana", "secreta")
    u = db_tmp.get_user_by_username("ana")
    assert "secreta" not in u["password_hash"]
    assert u["password_hash"].startswith("pbkdf2$")
    assert db_tmp.verify_password("secreta", u["password_hash"])


def test_una_sesion_por_usuario_cierra_la_previa(db_tmp):
    uid = db_tmp.create_user("ana", "1234")
    t1, prev1 = db_tmp.create_session(uid)
    assert prev1 is False
    assert db_tmp.get_user_by_token(t1)["username"] == "ana"

    t2, prev2 = db_tmp.create_session(uid)
    assert prev2 is True                                   # había una sesión abierta
    assert db_tmp.get_user_by_token(t1) is None            # la anterior quedó cerrada
    assert db_tmp.get_user_by_token(t2)["username"] == "ana"

    db_tmp.delete_session(t2)
    assert db_tmp.get_user_by_token(t2) is None
    assert db_tmp.get_user_by_token("inexistente") is None


def test_roles_y_perfil(db_tmp):
    db_tmp.create_user("ana", "1234")
    db_tmp.create_user("bea", "1234")
    assert len(db_tmp.list_users_by_role("gestante")) == 2
    assert len(db_tmp.list_users_by_role("admin")) == 1

    uid = db_tmp.create_user("caro", "1234", profile={"zona": "lucero"})
    db_tmp.update_user_profile(uid, {"condiciones": ["asma"]})
    perfil = json.loads(db_tmp.get_user_by_id(uid)["profile"])
    assert perfil["zona"] == "lucero"          # se conserva lo previo
    assert perfil["condiciones"] == ["asma"]   # y se agrega lo nuevo


def test_conmutador_guarda_y_sirve_waqi(db_tmp):
    est = {"uid": "A522148", "nombre": "CB", "lat": 4.57, "lon": -74.13,
           "aqi_pm25": 50, "aqi_no2": 20, "semaforo": {"ica": 50, "nivel": "naranja"}}
    assert db_tmp.upsert_waqi({"fuente": "WAQI", "estaciones": [est]}) == 1
    assert db_tmp.count_waqi() == 1
    assert db_tmp.get_waqi_station("A522148")["nombre"] == "CB"
    assert db_tmp.get_waqi_station("noexiste") is None
    lista = db_tmp.get_waqi_stations()
    assert lista["total"] == 1 and lista["fuente"] == "WAQI"
    assert db_tmp.get_waqi_meta()["guardado_en"]


def test_modo_prueba_factor_y_nivel(db_tmp):
    est = {"uid": "A522148", "nombre": "CB", "aqi_pm25": 50, "ica": 50,
           "semaforo": {"ica": 50, "nivel": "verde"}}
    db_tmp.upsert_waqi({"estaciones": [est]})

    db_tmp.set_test_mode(True, global_cfg={"factor": 2, "nivel": "rojo"})
    g = db_tmp.get_waqi_station("A522148")
    assert g["en_prueba"] is True
    assert g["aqi_pm25"] == 100
    assert g["semaforo"]["ica"] == 100
    assert g["semaforo"]["nivel"] == "rojo"

    lista = db_tmp.get_waqi_stations()
    assert lista["modo_prueba"] is True
    assert "aviso_prueba" in lista

    # Al desactivar, vuelve el dato real.
    db_tmp.set_test_mode(False)
    g2 = db_tmp.get_waqi_station("A522148")
    assert g2["semaforo"]["nivel"] == "verde"
    assert g2["aqi_pm25"] == 50
    assert not db_tmp.get_waqi_stations()["modo_prueba"]


def test_modo_prueba_por_estacion(db_tmp):
    a = {"uid": "A1", "nombre": "A", "semaforo": {"ica": 10, "nivel": "verde"}}
    b = {"uid": "A2", "nombre": "B", "semaforo": {"ica": 10, "nivel": "verde"}}
    db_tmp.upsert_waqi({"estaciones": [a, b]})
    db_tmp.set_test_mode(True, estaciones={"A2": {"nivel": "rojo"}})
    assert db_tmp.get_waqi_station("A1")["semaforo"]["nivel"] == "verde"
    assert db_tmp.get_waqi_station("A2")["semaforo"]["nivel"] == "rojo"


def test_notificaciones_ciclo(db_tmp):
    uid = db_tmp.create_user("ana", "1234")
    nid = db_tmp.add_notification(uid, "alerta", "T", "M", nivel="rojo", estacion="A1")
    assert nid

    ns = db_tmp.list_notifications(uid)
    assert len(ns) == 1
    assert ns[0]["nivel"] == "rojo" and ns[0]["leida"] == 0

    assert db_tmp.list_notifications(uid, since_id=nid) == []
    assert len(db_tmp.notifications_recientes(uid, horas=1)) == 1

    db_tmp.mark_notifications_read(uid)
    assert db_tmp.list_notifications(uid)[0]["leida"] == 1


def test_ingest_run_auditoria(db_tmp):
    assert db_tmp.last_ingest_run() is None
    db_tmp.record_ingest_run("2026-06-01T10:00:00", "2026-06-01T10:00:05", True, 3, "")
    db_tmp.record_ingest_run("2026-06-01T10:15:00", "2026-06-01T10:15:01", False, 0, "boom")
    ultimo = db_tmp.last_ingest_run()
    assert ultimo["ok"] == 0 and ultimo["detalle"] == "boom"
