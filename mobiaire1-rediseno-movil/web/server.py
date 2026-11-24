"""Servidor de MobiAire Materno (cliente-servidor, sin aplicación web/PWA).

El servidor es el CONMUTADOR:
  - Es el único que consulta la API de WAQI (con el token del backend).
  - Guarda los resultados en la base de datos (tabla `waqi_stations`).
  - Actualiza la base de datos de forma programada (ingesta cada N minutos).
  - Los clientes móviles (APK Android / app iOS) consultan al servidor, nunca a
    la API directamente, para no saturar el token.
  - Gestiona usuarios (contraseña cifrada con PBKDF2), roles, sesiones (una por
    usuario), notificaciones, modo prueba y logs.

Usa solo la biblioteca estándar de Python (http.server).

Uso:
    python -m web.server                       # http://0.0.0.0:8000 + ingesta
    python -m web.server --port 8080
    python -m web.server --sin-ingesta         # no programa la ingesta
    python -m web.server --ingesta-min 20      # intervalo de ingesta (min)
    python -m web.server --demo-refrescar      # regenera resultados_demo.json

Privacidad: nunca sirve .env ni claves. El token de WAQI jamás sale del backend.
"""
import argparse
import json
import re
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

AQUI = Path(__file__).resolve().parent          # .../web
RAIZ = AQUI.parent                               # .../mobiaire1
DIR_DATOS = RAIZ / "ingesta"
ARCHIVO_REAL = DIR_DATOS / "resultados.json"
ARCHIVO_DEMO = DIR_DATOS / "resultados_demo.json"
ARCHIVO_ZONAS = AQUI / "zonas.json"
ARCHIVO_UPZ = RAIZ / "datos" / "upz.geojson"
LOG_SERVIDOR = DIR_DATOS / "server.log"
LOG_CLIENTE = DIR_DATOS / "client.log"
LOG_MAX_BYTES = 2 * 1024 * 1024
_UPZ_CB_CACHE = None   # caché del GeoJSON filtrado de Ciudad Bolívar

db.init_db()


def sembrar_si_vacio():
    try:
        st = db.get_stations()
        if not (st.get("estaciones") or {}):
            if ARCHIVO_REAL.exists():
                datos = json.loads(ARCHIVO_REAL.read_text(encoding="utf-8"))
                db.upsert_stations(datos)
    except Exception:
        pass


sembrar_si_vacio()

# Lista blanca de archivos estáticos. Ya NO hay manifest ni service worker:
# la app es un cliente móvil (APK / iOS), no una PWA instalable desde el navegador.
ESTATICOS = {
    "/": ("prototipo/index.html", "text/html; charset=utf-8"),
    "/index.html": ("prototipo/index.html", "text/html; charset=utf-8"),
    "/iconos/icono.svg": ("iconos/icono.svg", "image/svg+xml; charset=utf-8"),
    "/iconos/icono-192.png": ("iconos/icono-192.png", "image/png"),
    "/iconos/icono-512.png": ("iconos/icono-512.png", "image/png"),
    "/img/embarazada.png": ("img/embarazada.png", "image/png"),
    "/recomendaciones.json": ("recomendaciones.json", "application/json; charset=utf-8"),
    "/app-movil.js": ("app-movil.js", "application/javascript; charset=utf-8"),
}


def _leer_json(ruta):
    if not ruta.exists():
        return None, f"No existe {ruta.name}. Ejecute la ingesta primero."
    try:
        return json.loads(ruta.read_text(encoding="utf-8")), None
    except (json.JSONDecodeError, OSError) as e:
        return None, f"No se pudo leer {ruta.name}: {e}"


def _append_log(ruta, linea):
    """Escribe una línea de log con rotación simple por tamaño."""
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        if ruta.exists() and ruta.stat().st_size > LOG_MAX_BYTES:
            respaldo = ruta.with_suffix(ruta.suffix + ".1")
            try:
                if respaldo.exists():
                    respaldo.unlink()
                ruta.rename(respaldo)
            except OSError:
                pass
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "MobiAire/2.0"

    # --- utilidades de respuesta ------------------------------------------
    def _json(self, cuerpo, codigo=200):
        datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(datos)

    def _archivo(self, nombre, tipo):
        ruta = AQUI / nombre
        if not ruta.exists():
            return self._json({"error": f"Falta {nombre}"}, 404)
        datos = ruta.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def _error(self, mensaje, codigo=404):
        self._json({"error": mensaje}, codigo)

    def _body_json(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        raw = self.rfile.read(n) if n > 0 else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _token_actual(self):
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth.split(" ", 1)[1].strip()
        return self.headers.get("X-Token", "").strip()

    def _usuario_actual(self):
        return db.get_user_by_token(self._token_actual())

    def _requiere_admin(self):
        u = self._usuario_actual()
        if not u or u.get("role") != "admin":
            self._error("Se requiere rol administrador", 403)
            return None
        return u

    def _requiere_usuario(self):
        u = self._usuario_actual()
        if not u:
            self._error("No autenticado", 401)
            return None
        return u

    # --- ruteo ------------------------------------------------------------
    def do_GET(self):
        ruta = urlparse(self.path)
        camino = ruta.path
        params = parse_qs(ruta.query)
        es_demo = params.get("demo", ["0"])[0] in ("1", "true", "si", "sí")

        if camino in ESTATICOS:
            nombre, tipo = ESTATICOS[camino]
            return self._archivo(nombre, tipo)

        if camino == "/api/salud":
            return self._salud()
        if camino == "/api/estaciones":
            return self._estaciones(es_demo)
        if camino.startswith("/api/estacion/"):
            sid = camino[len("/api/estacion/"):].strip("/")
            return self._estacion(sid, es_demo)
        if camino == "/api/upz-geojson":
            return self._upz_geojson()
        if camino == "/api/upz-ciudad-bolivar":
            return self._upz_ciudad_bolivar()
        if camino == "/api/zonas-mapa":
            return self._zonas_mapa()
        if camino == "/api/zonas":
            return self._zonas()
        if camino == "/api/zonas/asociacion":
            return self._zonas_asociacion()
        if camino.startswith("/api/zona/"):
            zid = camino[len("/api/zona/"):].strip("/")
            return self._zona_semaforo(zid)

        # --- WAQI: se sirve desde la base de datos (conmutador) -----------
        if camino == "/api/waqi/estaciones":
            return self._waqi_estaciones()
        if camino == "/api/waqi/ciudad-bolivar":
            return self._waqi_ciudad_bolivar()
        if camino.startswith("/api/waqi/estacion/"):
            uid = camino[len("/api/waqi/estacion/"):].strip("/")
            return self._waqi_estacion(uid)
        if camino == "/api/contaminantes":
            return self._contaminantes()
        if camino == "/api/salud/cercanos":
            return self._salud_cercanos(params)

        # --- Autenticación / notificaciones -------------------------------
        if camino == "/api/auth/me":
            return self._auth_me()
        if camino == "/api/notifications":
            since = params.get("since", ["0"])[0]
            return self._notifications(since)
        if camino == "/api/perfil/recomendacion":
            return self._perfil_recomendacion()
        if camino == "/api/perfil/indicaciones":
            return self._perfil_indicaciones()

        # --- Administrador ------------------------------------------------
        if camino == "/api/admin/users":
            return self._admin_users()
        if camino == "/api/admin/test-mode":
            return self._admin_test_mode_get()
        if camino == "/api/admin/ingesta/estado":
            return self._admin_ingesta_estado()
        if camino == "/api/admin/refresh/status":
            return self._admin_refresh_status()
        if camino == "/api/admin/logs":
            return self._admin_logs()

        return self._error("Ruta no encontrada", 404)

    def do_POST(self):
        ruta = urlparse(self.path).path
        if ruta == "/api/auth/register":
            return self._auth_register()
        if ruta == "/api/auth/login":
            return self._auth_login()
        if ruta == "/api/auth/logout":
            return self._auth_logout()
        if ruta == "/api/admin/refresh":
            return self._admin_refresh()
        if ruta == "/api/admin/test-mode":
            return self._admin_test_mode_set()
        if ruta == "/api/notifications/leer":
            return self._notifications_leer()
        if ruta == "/api/users/me/ubicacion":
            return self._user_ubicacion()
        if ruta == "/api/log":
            return self._client_log()
        self._error("Ruta no encontrada", 404)

    def do_PUT(self):
        ruta = urlparse(self.path).path
        if ruta == "/api/users/me/profile":
            return self._user_profile_update()
        if ruta.startswith("/api/admin/users/"):
            uid = ruta[len("/api/admin/users/"):].strip("/")
            return self._admin_user_update(uid)
        self._error("Ruta no encontrada", 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Token")
        self.end_headers()

    def do_DELETE(self):
        self._error("DELETE no soportado", 405)

    # --- handlers de datos históricos ------------------------------------
    def _cargar(self, es_demo):
        ruta = ARCHIVO_DEMO if es_demo else ARCHIVO_REAL
        datos, err = _leer_json(ruta)
        if err:
            return None, err
        datos = dict(datos)
        datos["modo"] = "demo" if es_demo else "real"
        datos["consultado_en"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        return datos, None

    def _estaciones(self, es_demo):
        if es_demo:
            datos, err = self._cargar(True)
            if err:
                return self._error(err, 503)
            return self._json(datos)
        try:
            datos = db.get_stations()
        except Exception as e:
            datos, err = self._cargar(False)
            if err:
                return self._error(str(e), 503)
        datos = db.apply_test_mode(datos)
        datos["modo"] = "real"
        return self._json(datos)

    def _estacion(self, sid, es_demo):
        if es_demo:
            datos, err = self._cargar(True)
            if err:
                return self._error(err, 503)
            est = datos.get("estaciones", {}).get(sid)
            if est is None:
                return self._error("no encontrada", 404)
            return self._json({"modo": datos["modo"], "id": sid, "estacion": est,
                              "consultado_en": datos["consultado_en"]})
        est = db.get_station(sid)
        if est is None:
            return self._error("no encontrada", 404)
        est = db.apply_test_mode_station(est, sid)
        return self._json({"modo": "real", "id": sid, "estacion": est,
                          "consultado_en": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")})

    def _zonas(self):
        datos, err = _leer_json(ARCHIVO_ZONAS)
        if err:
            return self._error(err, 503)
        return self._json(datos)

    # --- WAQI desde la base de datos (el servidor es el conmutador) ------
    def _waqi_estaciones(self):
        datos = db.get_waqi_stations()
        estaciones = datos.get("estaciones", [])
        if not estaciones:
            datos["sin_datos"] = True
            datos["aviso"] = ("El servidor aún no tiene datos de WAQI. Un administrador "
                              "debe ejecutar 'Actualizar datos API' o esperar la ingesta "
                              "programada.")
        return self._json(datos)

    def _waqi_estacion(self, uid):
        if not re.fullmatch(r"@?\d+|A\d+|-\d+", uid):
            return self._error("uid de estación inválido.", 400)
        est = db.get_waqi_station(uid)
        if est is None:
            return self._error("Estación no encontrada en la base de datos del servidor.", 404)
        return self._json({
            "fuente": "WAQI / AQICN",
            "atribucion": est.get("atribucion", []),
            "escala_indice": "US-EPA (WAQI) — no es el ICA del proyecto MobiAire",
            "consultado_en": est.get("_updated_at"),
            "estacion": est,
        })

    def _waqi_ciudad_bolivar(self):
        from zonas.asociacion import UID_CIUDAD_BOLIVAR
        est = db.get_waqi_station(UID_CIUDAD_BOLIVAR)
        if est is None:
            # A veces WAQI la entrega con idx negativo.
            est = db.get_waqi_station("-522148")
        if est is None:
            return self._error("Ciudad Bolívar no está en la base de datos todavía.", 404)
        return self._json({
            "fuente": "WAQI / AQICN",
            "atribucion": est.get("atribucion", []),
            "consultado_en": est.get("_updated_at"),
            "estacion": est,
        })

    def _estaciones_candidatas_db(self):
        """Estaciones guardadas con coordenadas, como candidatas para asociar UPZ.

        Prioriza estaciones con dato reciente; si ninguna es reciente, usa todas.
        """
        todas = [
            {"uid": e.get("uid"), "nombre": e.get("nombre"), "lat": e.get("lat"),
             "lon": e.get("lon"), "reciente": bool(e.get("reciente"))}
            for e in db.get_waqi_stations().get("estaciones", [])
            if e.get("lat") is not None and e.get("lon") is not None
        ]
        recientes = [e for e in todas if e.get("reciente")]
        return recientes if recientes else todas

    def _zonas_asociacion(self):
        from zonas import asociacion
        return self._json(asociacion.asociar(self._estaciones_candidatas_db()))

    def _zona_semaforo(self, zid):
        from zonas import asociacion
        zid_str = str(zid).strip()

        # 1. ¿Es directamente el UID de una estación de WAQI?
        est = db.get_waqi_station(zid_str)
        if est is None and (zid_str.startswith("A") or zid_str.startswith("-")):
            alt = zid_str.replace("A", "-") if zid_str.startswith("A") else zid_str.replace("-", "A")
            est = db.get_waqi_station(alt)
        if est is not None:
            sem = est.get("semaforo")
            reciente = bool(est.get("reciente"))
            aqi_val = est.get("aqi_waqi")
            ica_val = (sem or {}).get("ica") or aqi_val
            return self._json({
                "zona": {"id": str(est.get("uid")), "nombre": est.get("nombre"), "distancia_km": None},
                "estado": "ok" if reciente else "sin_dato_reciente",
                "estacion": est,
                "semaforo": sem,
                "indice_waqi": aqi_val,
                "ica": ica_val,
                "aqi_pm25": est.get("aqi_pm25"),
                "aqi_pm10": est.get("aqi_pm10"),
                "aqi_o3": est.get("aqi_o3"),
                "aqi_no2": est.get("aqi_no2"),
                "aqi_so2": est.get("aqi_so2"),
                "aqi_co": est.get("aqi_co"),
                "no2": est.get("aqi_no2"), "so2": est.get("aqi_so2"),
                "o3": est.get("aqi_o3"), "co": est.get("aqi_co"),
                "contaminantes": est.get("contaminantes") or [],
                "atribucion": est.get("atribucion", []),
                "modo_prueba": bool(db.get_test_mode().get("enabled")),
                "consultado_en": est.get("_updated_at"),
            })

        # 2. Si es una UPZ tradicional de Ciudad Bolívar
        tabla = asociacion.asociar(self._estaciones_candidatas_db())
        zona = next((z for z in tabla["zonas"] if z["id"] == zid_str), None)
        if zona is None:
            return self._error(f"Zona o estación '{zid}' no encontrada", 404)
        if zona["estado"] != "ok" or not zona.get("estacion"):
            return self._json({"zona": zona, "estado": zona["estado"],
                               "semaforo": None, "consultado_en": db.get_waqi_meta().get("guardado_en")})
        uid = zona["estacion"]["uid"]
        est = db.get_waqi_station(uid)
        if est is None:
            return self._json({
                "zona": {"id": zona["id"], "nombre": zona["nombre"], "distancia_km": zona["distancia_km"]},
                "estado": "sin_dato_reciente",
                "estacion": {"uid": uid, "nombre": zona["estacion"].get("nombre"), "reciente": False},
                "semaforo": None, "indice_waqi": None,
                "consultado_en": db.get_waqi_meta().get("guardado_en"),
            })
        sem = est.get("semaforo")
        reciente = bool(est.get("reciente"))
        aqi_val = est.get("aqi_waqi")
        return self._json({
            "zona": {"id": zona["id"], "nombre": zona["nombre"], "distancia_km": zona["distancia_km"]},
            "estado": "ok" if reciente else "sin_dato_reciente",
            "estacion": {"uid": est.get("uid"), "nombre": est.get("nombre"),
                         "medido_bogota": est.get("medido_bogota"),
                         "medido_texto": est.get("medido_texto"),
                         "reciente": est.get("reciente")},
            "semaforo": sem,
            "indice_waqi": aqi_val,
            "ica": (sem or {}).get("ica") or aqi_val,
            "aqi_pm25": est.get("aqi_pm25"),
            "aqi_pm10": est.get("aqi_pm10"),
            "aqi_o3": est.get("aqi_o3"),
            "aqi_no2": est.get("aqi_no2"),
            "aqi_so2": est.get("aqi_so2"),
            "aqi_co": est.get("aqi_co"),
            "no2": est.get("aqi_no2"), "so2": est.get("aqi_so2"),
            "o3": est.get("aqi_o3"), "co": est.get("aqi_co"),
            "contaminantes": est.get("contaminantes") or [],
            "atribucion": est.get("atribucion", []),
            "modo_prueba": bool(db.get_test_mode().get("enabled")),
            "consultado_en": est.get("_updated_at"),
        })

    def _contaminantes(self):
        """Qué contaminantes reporta realmente la API y cuáles no (requisito 5)."""
        estaciones = db.get_waqi_stations().get("estaciones", [])
        claves = ["pm25", "pm10", "no2", "so2", "o3", "co"]
        presentes, ausentes = [], []
        for k in claves:
            if any(e.get("aqi_" + k) is not None for e in estaciones):
                presentes.append(k)
            else:
                ausentes.append(k)
        return self._json({
            "presentes": presentes,
            "ausentes": ausentes,
            "aviso": (None if not ausentes else
                      "La API/estación no reporta: " + ", ".join(ausentes) +
                      ". Esa parte se descarta por falta de dato."),
            "estaciones": len(estaciones),
        })

    def _upz_geojson(self):
        if not ARCHIVO_UPZ.exists():
            return self._error("No existe upz.geojson", 404)
        try:
            datos = json.loads(ARCHIVO_UPZ.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return self._error(str(e), 500)
        return self._json(datos)

    def _upz_ciudad_bolivar(self):
        """Solo las UPZ de Ciudad Bolívar (el geojson completo pesa ~11 MB)."""
        global _UPZ_CB_CACHE
        if _UPZ_CB_CACHE is None:
            if not ARCHIVO_UPZ.exists():
                return self._error("No existe upz.geojson", 404)
            try:
                gj = json.loads(ARCHIVO_UPZ.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                return self._error(str(e), 500)
            feats = [f for f in gj.get("features", [])
                     if "CIUDAD BOLIVAR" in str((f.get("properties") or {}).get("LocNombre", "")).upper()]
            _UPZ_CB_CACHE = {"type": "FeatureCollection", "features": feats}
        return self._json(_UPZ_CB_CACHE)

    def _zonas_mapa(self):
        """Zonas alrededor de cada estación (radar), coloreadas por nivel.

        Si una estación no tiene dato reciente, su zona queda en gris. El
        cliente dibuja un círculo por zona con el color del semáforo.
        """
        estaciones = db.get_waqi_stations().get("estaciones", [])
        zonas = []
        for e in estaciones:
            if e.get("lat") is None or e.get("lon") is None:
                continue
            sem = e.get("semaforo") or {}
            nivel = sem.get("nivel") or e.get("nivel_forzado") or e.get("nivel")
            reciente = bool(e.get("reciente"))
            if not reciente or nivel not in ("verde", "naranja", "rojo"):
                nivel = "gris"
            zonas.append({
                "uid": e.get("uid"), "nombre": e.get("nombre"),
                "lat": e.get("lat"), "lon": e.get("lon"),
                "nivel": nivel, "reciente": reciente,
                "aqi": e.get("aqi_waqi"), "ica": sem.get("ica"),
            })
        return self._json({
            "tipo": "estaciones",
            "radio_km": 1.5,
            "modo_prueba": bool(db.get_test_mode().get("enabled")),
            "zonas": zonas,
        })

    def _salud_cercanos(self, params):
        """Hospitales/centros cercanos: el servidor consulta y cachea (Overpass)."""
        from web import salud
        try:
            lat = float(params.get("lat", [""])[0])
            lon = float(params.get("lon", [""])[0])
        except (TypeError, ValueError):
            return self._error("lat/lon inválidos", 400)
        try:
            radio = int(params.get("radio", ["5000"])[0])
        except (TypeError, ValueError):
            radio = 5000
        forzar = params.get("forzar", ["0"])[0] in ("1", "true", "si", "sí")
        try:
            return self._json(salud.buscar_cercanos(lat, lon, radio_m=radio, forzar=forzar))
        except Exception as e:  # nunca tumbar el servidor por una búsqueda
            return self._error(f"No se pudieron consultar los centros: {e}", 502)

    # --- Autenticación ----------------------------------------------------
    def _auth_register(self):
        body = self._body_json()
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        role = body.get("role") or "gestante"
        if role not in ("gestante",):
            role = "gestante"   # el rol admin no se auto-asigna (solo existe 'admin')
        if not username or len(password) < 4:
            return self._error("username y password (>=4) requeridos", 400)
        if db.get_user_by_username(username):
            return self._error("El usuario ya existe", 409)
        profile = body.get("profile") or {}
        if not isinstance(profile, dict):
            profile = {}
        uid = db.create_user(username, password, role, profile)
        token, had_prev = db.create_session(uid)
        return self._json({"token": token,
                           "user": {"id": uid, "username": username, "role": role, "profile": profile},
                           "sesion_previa_cerrada": had_prev})

    def _auth_login(self):
        body = self._body_json()
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        u = db.get_user_by_username(username)
        if not u or not db.verify_password(password, u["password_hash"]):
            return self._error("Credenciales invalidas", 401)
        token, had_prev = db.create_session(u["id"])
        profile = u["profile"]
        if isinstance(profile, str):
            try:
                profile = json.loads(profile)
            except json.JSONDecodeError:
                profile = {}
        return self._json({"token": token, "user": {"id": u["id"], "username": u["username"],
                          "role": u["role"], "profile": profile},
                          "sesion_previa_cerrada": had_prev})

    def _auth_logout(self):
        db.delete_session(self._token_actual())
        return self._json({"ok": True})

    def _auth_me(self):
        u = self._usuario_actual()
        if not u:
            return self._error("No autenticado", 401)
        profile = u["profile"]
        if isinstance(profile, str):
            try:
                profile = json.loads(profile)
            except json.JSONDecodeError:
                profile = {}
        return self._json({"id": u["id"], "username": u["username"], "role": u["role"], "profile": profile})

    def _user_profile_update(self):
        u = self._requiere_usuario()
        if not u:
            return
        body = self._body_json()
        profile = body.get("profile")
        if not isinstance(profile, dict):
            profile = body
        db.update_user_profile(u["id"], profile)
        return self._json({"ok": True})

    # --- Notificaciones ---------------------------------------------------
    def _notifications(self, since):
        u = self._requiere_usuario()
        if not u:
            return
        try:
            since_id = int(since or 0)
        except ValueError:
            since_id = 0
        return self._json({"notificaciones": db.list_notifications(u["id"], since_id=since_id)})

    def _notifications_leer(self):
        u = self._requiere_usuario()
        if not u:
            return
        body = self._body_json()
        ids = body.get("ids")
        db.mark_notifications_read(u["id"], ids if isinstance(ids, list) else None)
        return self._json({"ok": True})

    def _perfil_estacion(self, profile):
        """Estación de referencia del usuario: su zona (UPZ) o la más cercana."""
        zona_id = (profile or {}).get("zona")
        if zona_id:
            from zonas import asociacion
            tabla = asociacion.asociar(self._estaciones_candidatas_db())
            zona = next((z for z in tabla["zonas"] if z["id"] == zona_id), None)
            if zona and zona.get("estacion"):
                est = db.get_waqi_station(zona["estacion"]["uid"])
                if est is not None:
                    return est
        lat, lon = (profile or {}).get("lat"), (profile or {}).get("lon")
        if lat is not None and lon is not None:
            from zonas import asociacion
            try:
                lat, lon = float(lat), float(lon)
            except (TypeError, ValueError):
                return None
            est, _ = asociacion.estacion_mas_cercana(lat, lon, self._estaciones_candidatas_db())
            if est is not None:
                return db.get_waqi_station(est.get("uid"))
        return None

    def _perfil_recomendacion(self):
        u = self._requiere_usuario()
        if not u:
            return
        from web import recomendaciones
        profile = u.get("profile") or {}
        if isinstance(profile, str):
            try:
                profile = json.loads(profile)
            except json.JSONDecodeError:
                profile = {}
        est = self._perfil_estacion(profile)
        nivel = "gris"
        if est:
            nivel = ((est.get("semaforo") or {}).get("nivel") or est.get("nivel_forzado") or "gris")
        rec = recomendaciones.advertencias(
            profile, nivel, semaforo=(est or {}).get("semaforo"),
            contaminantes=(est or {}).get("contaminantes"), estacion=est,
        )
        rec["estacion"] = ({"uid": est.get("uid"), "nombre": est.get("nombre")}
                           if est else None)
        return self._json(rec)

    def _perfil_indicaciones(self):
        """Vista completa "Ver indicaciones" (según perfil, semanas y zona)."""
        u = self._requiere_usuario()
        if not u:
            return
        from web import recomendaciones
        profile = u.get("profile") or {}
        if isinstance(profile, str):
            try:
                profile = json.loads(profile)
            except json.JSONDecodeError:
                profile = {}
        est = self._perfil_estacion(profile)
        nivel = "gris"
        if est:
            nivel = ((est.get("semaforo") or {}).get("nivel") or est.get("nivel_forzado") or "gris")
        cont = (est or {}).get("contaminantes")
        claves = ["pm25", "pm10", "no2", "so2", "o3", "co"]
        presentes = cont or [k for k in claves if (est or {}).get("aqi_" + k) is not None]
        ausentes = [k for k in claves if k not in presentes]
        data = recomendaciones.indicaciones(
            profile, nivel, semaforo=(est or {}).get("semaforo"),
            contaminantes=presentes, contaminantes_ausentes=ausentes, estacion=est,
        )
        data["estacion"] = {"uid": (est or {}).get("uid"), "nombre": (est or {}).get("nombre")} if est else None
        return self._json(data)

    def _user_ubicacion(self):
        """Guarda la última ubicación conocida (para avisos por estación cercana)."""
        u = self._requiere_usuario()
        if not u:
            return
        body = self._body_json()
        try:
            lat = float(body.get("lat"))
            lon = float(body.get("lon"))
        except (TypeError, ValueError):
            return self._error("lat/lon inválidos", 400)
        db.update_user_profile(u["id"], {
            "lat": lat, "lon": lon,
            "ubicacion_en": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        })
        try:
            from web import notificaciones
            notificaciones.generar_alertas()
        except Exception:
            pass
        return self._json({"ok": True})

    # --- Administrador ----------------------------------------------------
    def _admin_users(self):
        u = self._requiere_admin()
        if not u:
            return
        return self._json({"users": db.list_users()})

    def _admin_user_update(self, uid):
        u = self._requiere_admin()
        if not u:
            return
        try:
            uid_int = int(uid)
        except ValueError:
            return self._error("uid invalido", 400)
        body = self._body_json()
        # Solo existe un administrador: no se permite promover a admin.
        if "role" in body and body["role"] == "admin":
            return self._error("Solo existe un administrador; no se pueden crear más.", 400)
        if "profile" in body and isinstance(body["profile"], dict):
            db.update_user_profile(uid_int, body["profile"])
        return self._json({"ok": True})

    def _admin_test_mode_get(self):
        u = self._requiere_admin()
        if not u:
            return
        return self._json(db.get_test_mode())

    def _admin_test_mode_set(self):
        u = self._requiere_admin()
        if not u:
            return
        body = self._body_json()
        enabled = bool(body.get("enabled", False))
        global_cfg = body.get("global")
        estaciones = body.get("estaciones")
        # Compatibilidad con el formato antiguo {"levels": {...}}.
        if estaciones is None and isinstance(body.get("levels"), dict):
            estaciones = body["levels"]
        tm = db.set_test_mode(enabled, global_cfg=global_cfg, estaciones=estaciones)
        # En modo prueba se avisa a los usuarios según su ubicación y nivel.
        creadas = 0
        if enabled:
            try:
                from web import notificaciones
                creadas = notificaciones.generar_alertas(forzar=True)
            except Exception:
                creadas = 0
        return self._json({"ok": True, "test_mode": tm, "alertas_generadas": creadas})

    def _admin_ingesta_estado(self):
        u = self._requiere_admin()
        if not u:
            return
        from ingesta import ingesta_waqi
        return self._json(ingesta_waqi.estado())

    def _admin_refresh(self):
        u = self._requiere_admin()
        if not u:
            return
        def tarea():
            from ingesta import ingesta_waqi
            try:
                ingesta_waqi.refrescar()
                db.set_setting("refresh_status", "completado")
            except Exception as e:
                db.set_setting("refresh_status", "error")
                db.set_setting("refresh_error", str(e))
        db.set_setting("refresh_status", "iniciado")
        db.set_setting("refresh_error", None)
        threading.Thread(target=tarea, daemon=True).start()
        return self._json({"ok": True, "mensaje": "Actualizacion de la API iniciada"})

    def _admin_refresh_status(self):
        u = self._requiere_usuario()
        if not u:
            return
        return self._json({
            "refresh_status": db.get_setting("refresh_status", "sin iniciar"),
            "refresh_error": db.get_setting("refresh_error", None),
            "waqi_error": db.get_setting("waqi_error", None),
        })

    def _admin_logs(self):
        u = self._requiere_admin()
        if not u:
            return
        def cola(ruta, n=200):
            if not ruta.exists():
                return ""
            try:
                return "\n".join(ruta.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
            except OSError:
                return ""
        return self._json({"servidor": cola(LOG_SERVIDOR), "cliente": cola(LOG_CLIENTE)})

    # --- Logs -------------------------------------------------------------
    def _client_log(self):
        body = self._body_json()
        msg = body.get("msg") or body.get("message") or ""
        nivel = body.get("level") or "info"
        usuario = body.get("user") or "anon"
        linea = "{} [{}] [{}] {}".format(
            datetime.now().isoformat(timespec="seconds"), nivel, usuario, str(msg)[:2000])
        _append_log(LOG_CLIENTE, linea)
        return self._json({"ok": True})

    def _salud(self):
        meta = db.get_waqi_meta()
        from ingesta import ingesta_waqi
        est = ingesta_waqi.estado()
        return self._json({
            "ok": True,
            "rol": "conmutador (el servidor consulta la API y guarda en la base de datos)",
            "datos_reales": {"existe": ARCHIVO_REAL.exists()},
            "datos_demo": {"existe": ARCHIVO_DEMO.exists()},
            "waqi": {
                "configurado": est.get("waqi_configurado"),
                "estaciones_en_db": est.get("estaciones_en_db"),
                "ultima_ingesta": est.get("ultima_ok") or meta.get("guardado_en"),
                "ultimo_error": est.get("ultimo_error"),
                "intervalo_min": est.get("intervalo_min"),
            },
            "usuarios": len(db.list_users()),
            "consultado_en": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        })

    # logs sobrios (sin datos personales) + archivo con rotación
    def log_message(self, formato, *args):
        linea = "%s - %s" % (self.address_string(), formato % args)
        sys.stderr.write(linea + "\n")
        _append_log(LOG_SERVIDOR, linea)


def regenerar_demo():
    """Regenera ingesta/resultados_demo.json con el motor existente (sin red)."""
    sys.path.insert(0, str(RAIZ))
    from ingesta import demo, almacen, config
    from ingesta.normalizador import normalizar_registro
    from ingesta.calculo import calcular_estaciones

    config.ESTACIONES = []
    ahora = datetime.now()
    mediciones = [m for m in (normalizar_registro(r) for r in demo.generar_registros(ahora)) if m]
    estaciones = calcular_estaciones(mediciones, ahora)
    resultado = {
        "generado_en": ahora.isoformat(timespec="seconds"),
        "datos_historicos": False,
        "aviso": "DEMO - DATOS SIMULADOS (no son mediciones reales)",
        "fuente": "datos_demo (sintéticos)",
        "estaciones": estaciones,
    }
    almacen.guardar_json(resultado, config.SALIDA_DEMO)
    print(f"Demo regenerada en {config.SALIDA_DEMO} ({len(estaciones)} estaciones).")


def main():
    ap = argparse.ArgumentParser(description="Servidor de MobiAire Materno (conmutador cliente-servidor)")
    default_port = int(os.environ.get("PORT", 8000))
    ap.add_argument("--port", type=int, default=default_port, help=f"puerto (defecto {default_port})")
    ap.add_argument("--host", default="0.0.0.0",
                    help="host (defecto 0.0.0.0 para que el celular se conecte)")
    ap.add_argument("--sin-ingesta", action="store_true",
                    help="no programa la ingesta automática de la API")
    ap.add_argument("--ingesta-min", type=int, default=15,
                    help="intervalo de la ingesta programada en minutos (defecto 15)")
    ap.add_argument("--demo-refrescar", action="store_true",
                    help="regenera resultados_demo.json y termina")
    args = ap.parse_args()

    if args.demo_refrescar:
        regenerar_demo()
        return

    if not args.sin_ingesta:
        from ingesta import ingesta_waqi
        from web import salud
        ingesta_waqi.programar(intervalo_min=args.ingesta_min, primera=True)
        ingesta_waqi.programar_reportes()
        salud.programar_ingesta_salud(intervalo_horas=24)
        print(f"Ingesta programada cada {args.ingesta_min} min (WAQI) y cada 24 h (centros de salud).")
    else:
        from web import salud
        salud.asegurar_base_salud()
        print("Ingesta programada desactivada (--sin-ingesta).")

    servidor = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"MobiAire Materno en http://{args.host}:{args.port}")
    print("Ctrl+C para detener.")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
        servidor.server_close()


if __name__ == "__main__":
    main()
