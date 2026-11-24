"""Pruebas del servidor HTTP (conmutador cliente-servidor).

Levanta un servidor real en un puerto efímero y habla con él por HTTP usando la
base de datos temporal (fixture `db_tmp`). Así se valida el backend completo sin
instalar la app en el celular.
"""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
UPZ_COORDS = RAIZ / "datos" / "upz_coords.json"


@pytest.fixture
def servidor(db_tmp, monkeypatch, tmp_path):
    import web.server as server
    monkeypatch.setattr(server, "LOG_SERVIDOR", tmp_path / "server.log")
    monkeypatch.setattr(server, "LOG_CLIENTE", tmp_path / "client.log")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _peticion(metodo, base, camino, cuerpo=None, token=None):
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    req = urllib.request.Request(base + camino, data=datos, method=metodo)
    if datos is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def get(base, camino, token=None):
    return _peticion("GET", base, camino, token=token)


def post(base, camino, cuerpo, token=None):
    return _peticion("POST", base, camino, cuerpo, token=token)


def put(base, camino, cuerpo, token=None):
    return _peticion("PUT", base, camino, cuerpo, token=token)


def _login_admin(base):
    est, datos = post(base, "/api/auth/login",
                      {"username": "admin", "password": "Analissdesistemas"})
    assert est == 200
    return datos["token"]


# --- la PWA ya no existe ------------------------------------------------
@pytest.mark.parametrize("camino", [
    "/service-worker.js", "/manifest.webmanifest", "/app.js", "/estilos.css",
    "/api/estacion/ciudad_bolivar",
])
def test_rutas_pwa_eliminadas(servidor, camino):
    est, _ = get(servidor, camino)
    assert est == 404


def test_salud_reporta_conmutador(servidor):
    est, datos = get(servidor, "/api/salud")
    assert est == 200 and datos["ok"] is True
    assert "conmutador" in datos["rol"]
    assert "waqi" in datos


# --- autenticación y sesión única --------------------------------------
def test_registro_login_y_sesion_unica(servidor):
    est, reg = post(servidor, "/api/auth/register",
                    {"username": "ana", "password": "1234", "profile": {"zona": "lucero"}})
    assert est == 200
    assert reg["user"]["role"] == "gestante"
    assert reg["sesion_previa_cerrada"] is False
    t1 = reg["token"]

    est, me = get(servidor, "/api/auth/me", token=t1)
    assert est == 200 and me["username"] == "ana"

    # Segundo inicio de sesión: cierra el anterior y avisa.
    est, log = post(servidor, "/api/auth/login", {"username": "ana", "password": "1234"})
    assert est == 200 and log["sesion_previa_cerrada"] is True
    t2 = log["token"]

    est, _ = get(servidor, "/api/auth/me", token=t1)
    assert est == 401                                  # token viejo ya no sirve
    est, _ = get(servidor, "/api/auth/me", token=t2)
    assert est == 200


def test_registro_no_permite_admin(servidor):
    est, reg = post(servidor, "/api/auth/register",
                    {"username": "listo", "password": "1234", "role": "admin"})
    assert est == 200 and reg["user"]["role"] == "gestante"


def test_credenciales_invalidas(servidor):
    est, _ = post(servidor, "/api/auth/login", {"username": "admin", "password": "mala"})
    assert est == 401


# --- conmutador WAQI ----------------------------------------------------
def test_waqi_sin_datos_avisa(servidor):
    est, datos = get(servidor, "/api/waqi/estaciones")
    assert est == 200
    assert datos["sin_datos"] is True and datos["aviso"]


def test_waqi_sirve_desde_db(servidor, db_tmp):
    db_tmp.upsert_waqi({"fuente": "WAQI / AQICN", "estaciones": [
        {"uid": "A522148", "nombre": "Ciudad Bolivar", "lat": 4.57, "lon": -74.13,
         "aqi_waqi": 67, "aqi_pm25": 67, "reciente": True,
         "semaforo": {"ica": 67, "nivel": "naranja"}},
    ]})
    est, datos = get(servidor, "/api/waqi/estaciones")
    assert est == 200 and datos["total"] == 1
    assert datos["estaciones"][0]["uid"] == "A522148"

    est, uno = get(servidor, "/api/waqi/estacion/A522148")
    assert est == 200 and uno["estacion"]["nombre"] == "Ciudad Bolivar"
    assert "no es el ICA del proyecto" in uno["escala_indice"]

    est, _ = get(servidor, "/api/waqi/estacion/no-valido")
    assert est == 400
    est, _ = get(servidor, "/api/waqi/estacion/A999999")
    assert est == 404


def test_contaminantes_presentes_y_ausentes(servidor, db_tmp):
    db_tmp.upsert_waqi({"estaciones": [
        {"uid": "A1", "aqi_pm25": 10, "aqi_no2": 5, "aqi_o3": 3},
    ]})
    est, datos = get(servidor, "/api/contaminantes")
    assert est == 200
    assert set(datos["presentes"]) == {"pm25", "no2", "o3"}
    assert "so2" in datos["ausentes"] and "co" in datos["ausentes"]
    assert "no reporta" in datos["aviso"].lower()


def test_zona_semaforo(servidor, db_tmp):
    coords = json.loads(UPZ_COORDS.read_text(encoding="utf-8"))
    c = coords["el_tesoro"]
    db_tmp.upsert_waqi({"estaciones": [
        {"uid": "A522148", "nombre": "CB", "lat": c["lat"], "lon": c["lon"],
         "reciente": True, "semaforo": {"ica": 120, "nivel": "rojo"}},
    ]})
    est, datos = get(servidor, "/api/zona/el_tesoro")
    assert est == 200 and datos["estado"] == "ok"
    assert datos["semaforo"]["nivel"] == "rojo"

    est, zonas = get(servidor, "/api/zonas")
    assert est == 200 and len(zonas["zonas"]) == 8

    est, _ = get(servidor, "/api/zona/noexiste")
    assert est == 404


# --- modo prueba del administrador -------------------------------------
def test_modo_prueba_requiere_admin(servidor):
    est, reg = post(servidor, "/api/auth/register", {"username": "ana", "password": "1234"})
    t = reg["token"]
    est, _ = post(servidor, "/api/admin/test-mode", {"enabled": True}, token=t)
    assert est == 403
    est, _ = get(servidor, "/api/admin/test-mode")
    assert est == 403


def test_modo_prueba_global(servidor, db_tmp):
    db_tmp.upsert_waqi({"estaciones": [
        {"uid": "A522148", "nombre": "CB", "lat": 4.57, "lon": -74.13,
         "aqi_pm25": 50, "reciente": True, "semaforo": {"ica": 50, "nivel": "verde"}},
    ]})
    token = _login_admin(servidor)

    est, resp = post(servidor, "/api/admin/test-mode",
                     {"enabled": True, "global": {"factor": 2, "nivel": "rojo"}},
                     token=token)
    assert est == 200 and resp["ok"] is True
    assert resp["test_mode"]["enabled"] is True

    est, datos = get(servidor, "/api/waqi/estaciones")
    assert datos["modo_prueba"] is True
    est_st = datos["estaciones"][0]
    assert est_st["semaforo"]["nivel"] == "rojo"
    assert est_st["aqi_pm25"] == 100

    est, resp = post(servidor, "/api/admin/test-mode", {"enabled": False}, token=token)
    assert est == 200
    est, datos = get(servidor, "/api/waqi/estaciones")
    assert datos["modo_prueba"] is False


def test_admin_users_y_no_promover(servidor):
    token = _login_admin(servidor)
    est, datos = get(servidor, "/api/admin/users", token=token)
    assert est == 200
    assert [u["username"] for u in datos["users"] if u["role"] == "admin"] == ["admin"]

    est, reg = post(servidor, "/api/auth/register", {"username": "ana", "password": "1234"})
    uid = reg["user"]["id"]
    est, resp = put(servidor, f"/api/admin/users/{uid}", {"role": "admin"}, token=token)
    assert est == 400 and "un administrador" in resp["error"].lower()


def test_logs_admin(servidor):
    token = _login_admin(servidor)
    est, datos = get(servidor, "/api/admin/logs", token=token)
    assert est == 200 and "servidor" in datos and "cliente" in datos
    # El log del cliente se puede escribir sin sesión.
    est, resp = post(servidor, "/api/log", {"msg": "hola", "level": "info", "user": "ana"})
    assert est == 200 and resp["ok"] is True


# --- notificaciones -----------------------------------------------------
def test_notificaciones_endpoint(servidor, db_tmp):
    est, reg = post(servidor, "/api/auth/register", {"username": "ana", "password": "1234"})
    uid, token = reg["user"]["id"], reg["token"]
    db_tmp.add_notification(uid, "alerta", "T", "M", nivel="rojo", estacion="A1")

    est, datos = get(servidor, "/api/notifications", token=token)
    assert est == 200 and len(datos["notificaciones"]) == 1
    nid = datos["notificaciones"][0]["id"]

    est, _ = post(servidor, "/api/notifications/leer", {"ids": [nid]}, token=token)
    assert est == 200
    est, datos = get(servidor, "/api/notifications", token=token)
    assert datos["notificaciones"][0]["leida"] == 1

    est, _ = get(servidor, "/api/notifications")
    assert est == 401


def test_recomendacion_personalizada(servidor, db_tmp):
    est, reg = post(servidor, "/api/auth/register", {
        "username": "ana", "password": "1234",
        "profile": {"zona": "el_tesoro", "condiciones": ["asma"]}})
    token = reg["token"]
    coords = json.loads(UPZ_COORDS.read_text(encoding="utf-8"))["el_tesoro"]
    db_tmp.upsert_waqi({"estaciones": [
        {"uid": "A522148", "nombre": "CB", "lat": coords["lat"], "lon": coords["lon"],
         "reciente": True, "semaforo": {"ica": 120, "nivel": "rojo"}},
    ]})
    est, rec = get(servidor, "/api/perfil/recomendacion", token=token)
    assert est == 200 and rec["nivel"] == "rojo"
    assert any("asma" in c for c in rec["por_condiciones"])


# --- ronda 2: indicaciones, zonas del mapa y ubicación -------------------
def test_indicaciones_personalizadas(servidor, db_tmp):
    est, reg = post(servidor, "/api/auth/register", {
        "username": "ana", "password": "1234",
        "profile": {"zona": "el_tesoro", "condiciones": ["asma"]}})
    token = reg["token"]
    coords = json.loads(UPZ_COORDS.read_text(encoding="utf-8"))["el_tesoro"]
    db_tmp.upsert_waqi({"estaciones": [
        {"uid": "A522148", "nombre": "CB", "lat": coords["lat"], "lon": coords["lon"],
         "reciente": True, "contaminantes": ["pm25", "pm10"],
         "semaforo": {"ica": 120, "nivel": "rojo"}},
    ]})
    est, d = get(servidor, "/api/perfil/indicaciones", token=token)
    assert est == 200 and d["nivel"] == "rojo"
    assert d["color"] == "#b3362f"                       # paleta roja
    assert d["estacion"]["uid"] == "A522148"
    assert any(s["titulo"] == "Qué hacer ahora" for s in d["secciones"])

    est, _ = get(servidor, "/api/perfil/indicaciones")
    assert est == 401


def test_zonas_mapa_colorea_y_deja_gris_sin_dato(servidor, db_tmp):
    db_tmp.upsert_waqi({"estaciones": [
        {"uid": "A1", "nombre": "Con dato", "lat": 4.57, "lon": -74.13,
         "reciente": True, "semaforo": {"ica": 50, "nivel": "verde"}},
        {"uid": "A2", "nombre": "Sin dato", "lat": 4.60, "lon": -74.10,
         "reciente": False, "semaforo": {"ica": 90, "nivel": "rojo"}},
    ]})
    est, d = get(servidor, "/api/zonas-mapa")
    assert est == 200 and d["tipo"] == "estaciones"
    assert d["radio_km"] > 0
    por_uid = {z["uid"]: z for z in d["zonas"]}
    assert por_uid["A1"]["nivel"] == "verde"
    assert por_uid["A2"]["nivel"] == "gris"              # sin dato reciente -> gris


def test_guardar_ubicacion_del_usuario(servidor, db_tmp):
    est, reg = post(servidor, "/api/auth/register", {"username": "ana", "password": "1234"})
    token = reg["token"]
    est, d = post(servidor, "/api/users/me/ubicacion", {"lat": 4.57, "lon": -74.13}, token=token)
    assert est == 200 and d["ok"] is True
    u = db_tmp.get_user_by_username("ana")
    perfil = json.loads(u["profile"])
    assert perfil["lat"] == 4.57 and perfil["lon"] == -74.13

    est, _ = post(servidor, "/api/users/me/ubicacion", {"lat": "x"}, token=token)
    assert est == 400
    est, _ = post(servidor, "/api/users/me/ubicacion", {"lat": 1, "lon": 2})
    assert est == 401


# --- ronda 3: hospitales/centros cercanos (servidor + caché) -------------
def test_salud_cercanos_usa_cache_y_ordena(servidor, db_tmp, monkeypatch):
    import web.salud as salud

    llamadas = {"n": 0}

    def _falso_descargar(lat, lon, radio_m):
        llamadas["n"] += 1
        return [
            {"osm_id": "node/1", "nombre": "Hospital Cerca", "lat": lat + 0.001,
             "lon": lon, "telefono": "6011234567", "tipo": "hospital",
             "direccion": "Calle 1", "descripcion": "", "operador": "", "web": ""},
            {"osm_id": "node/2", "nombre": "Clínica Lejos", "lat": lat + 0.02,
             "lon": lon, "telefono": "", "tipo": "clinic",
             "direccion": "", "descripcion": "", "operador": "", "web": ""},
        ]

    monkeypatch.setattr(salud, "_descargar", _falso_descargar)

    est, d = get(servidor, "/api/salud/cercanos?lat=4.57&lon=-74.13&radio=5000")
    assert est == 200
    assert d["fuente"].startswith("OpenStreetMap")
    assert d["total"] == 2
    assert [p["nombre"] for p in d["lugares"]] == ["Hospital Cerca", "Clínica Lejos"]
    assert d["lugares"][0]["distancia_km"] <= d["lugares"][1]["distancia_km"]
    assert llamadas["n"] == 1

    # Segunda consulta: se sirve desde la base de datos (no vuelve a Overpass).
    est, d2 = get(servidor, "/api/salud/cercanos?lat=4.57&lon=-74.13&radio=5000")
    assert est == 200 and d2["desde_cache"] is True
    assert d2["total"] == 2
    assert llamadas["n"] == 1                       # no hubo otra descarga
    assert db_tmp.count_lugares_salud() == 2

    # `forzar=1` obliga a refrescar la caché.
    est, _ = get(servidor, "/api/salud/cercanos?lat=4.57&lon=-74.13&radio=5000&forzar=1")
    assert est == 200 and llamadas["n"] == 2


def test_salud_cercanos_valida_coordenadas(servidor):
    est, _ = get(servidor, "/api/salud/cercanos?lat=x&lon=-74.13")
    assert est == 400
    est, _ = get(servidor, "/api/salud/cercanos")
    assert est == 400


def test_salud_cercanos_sirve_cache_si_overpass_falla(servidor, db_tmp, monkeypatch):
    import web.salud as salud
    db_tmp.upsert_lugares_salud([
        {"osm_id": "node/9", "nombre": "Puesto Guardado", "lat": 4.571, "lon": -74.13,
         "telefono": "", "tipo": "doctors", "direccion": "", "descripcion": "",
         "operador": "", "web": ""},
    ])

    def _falla(lat, lon, radio_m):
        raise OSError("sin red")

    monkeypatch.setattr(salud, "_descargar", _falla)
    est, d = get(servidor, "/api/salud/cercanos?lat=4.57&lon=-74.13&radio=5000&forzar=1")
    assert est == 200 and d["total"] == 1
    assert d["lugares"][0]["nombre"] == "Puesto Guardado"
