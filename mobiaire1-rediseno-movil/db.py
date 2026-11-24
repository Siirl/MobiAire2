"""Base de datos SQLite para MobiAire Materno."""
import hashlib
import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(RAIZ / "ingesta" / "mobiaire.db")))

_lock = threading.RLock()


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stations (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'gestante',
            profile TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        -- El servidor actúa como conmutador: guarda aquí el resultado de la API
        -- (WAQI) para que los clientes NUNCA consulten la API directamente.
        CREATE TABLE IF NOT EXISTS waqi_stations (
            uid TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        -- Alertas y reportes generados para cada usuaria (notificaciones).
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            nivel TEXT,
            titulo TEXT,
            mensaje TEXT,
            estacion TEXT,
            creada_en TEXT NOT NULL,
            leida INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, id);
        -- Historial de cada ingesta programada (auditoría del conmutador).
        CREATE TABLE IF NOT EXISTS ingest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            iniciado_en TEXT,
            terminado_en TEXT,
            ok INTEGER,
            estaciones INTEGER,
            detalle TEXT
        );
        -- Caché de hospitales / centros de salud consultados (OSM/Overpass).
        -- Así el servidor los guarda y las búsquedas siguientes son instantáneas.
        CREATE TABLE IF NOT EXISTS lugares_salud (
            osm_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            actualizado_en TEXT NOT NULL
        );
        """)
        _ensure_admin(c)
    return True


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _ensure_admin(c):
    # Solo un administrador: "admin" con la clave indicada. Se eliminan otros admins.
    c.execute("DELETE FROM users WHERE role='admin' AND username != 'admin'")
    h = hash_password("Analissdesistemas")
    row = c.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    if row:
        c.execute("UPDATE users SET password_hash=?, role='admin', profile=? WHERE id=?",
                  (h, json.dumps({"nombre": "Administrador"}), row["id"]))
    else:
        c.execute("INSERT INTO users (username,password_hash,role,profile,created_at) VALUES (?,?,?,?,?)",
                  ("admin", h, "admin", json.dumps({"nombre": "Administrador"}), _now()))


def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_bytes(16)
    elif isinstance(salt, str):
        salt = bytes.fromhex(salt)
    it = int(os.getenv("PBKDF2_ITERS", "100000"))
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, it)
    return "pbkdf2${}${}".format(salt.hex(), dk.hex())


def verify_password(password, stored):
    try:
        _algo, salt_hex, _hash = stored.split("$", 2)
    except ValueError:
        return False
    return hash_password(password, salt_hex) == stored


def get_setting(key, default=None):
    with _lock:
        with _conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if not row:
                return default
            try:
                return json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                return row["value"]


def set_setting(key, value):
    with _lock:
        with _conn() as c:
            c.execute(
                "INSERT INTO settings (key,value,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), _now()),
            )
    return True


def upsert_stations(resultado):
    estaciones = (resultado or {}).get("estaciones", {}) or {}
    ahora = _now()
    with _lock:
        with _conn() as c:
            for sid, est in estaciones.items():
                c.execute(
                    "INSERT INTO stations (id,data,updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                    (sid, json.dumps(est, ensure_ascii=False), ahora),
                )
    meta = {k: v for k, v in (resultado or {}).items() if k != "estaciones"}
    set_setting("ingesta_meta", meta)
    return len(estaciones)


def get_stations():
    with _lock:
        with _conn() as c:
            rows = c.execute("SELECT id,data,updated_at FROM stations").fetchall()
    est = {}
    for r in rows:
        try:
            est[r["id"]] = json.loads(r["data"])
        except json.JSONDecodeError:
            est[r["id"]] = {}
        est[r["id"]]["_updated_at"] = r["updated_at"]
    meta = get_setting("ingesta_meta", {})
    out = dict(meta) if isinstance(meta, dict) else {}
    out["estaciones"] = est
    out["consultado_en"] = _now()
    tm = get_setting("test_mode", {"enabled": False})
    out["modo_prueba"] = bool(tm.get("enabled")) if isinstance(tm, dict) else False
    return out


def get_station(sid):
    with _lock:
        with _conn() as c:
            row = c.execute("SELECT data,updated_at FROM stations WHERE id=?", (sid,)).fetchone()
    if not row:
        return None
    try:
        d = json.loads(row["data"])
    except json.JSONDecodeError:
        d = {}
    d["_updated_at"] = row["updated_at"]
    return d


def create_user(username, password, role="gestante", profile=None):
    profile = profile or {}
    with _lock:
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO users (username,password_hash,role,profile,created_at) VALUES (?,?,?,?,?)",
                (username, hash_password(password), role, json.dumps(profile, ensure_ascii=False), _now()),
            )
            return cur.lastrowid


def get_user_by_username(username):
    with _lock:
        with _conn() as c:
            row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return dict(row) if row else None


def get_user_by_id(uid):
    with _lock:
        with _conn() as c:
            row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return dict(row) if row else None


def list_users():
    with _lock:
        with _conn() as c:
            rows = c.execute("SELECT id,username,role,profile,created_at FROM users").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["profile"] = json.loads(d["profile"])
        except (json.JSONDecodeError, TypeError):
            d["profile"] = {}
        out.append(d)
    return out


def update_user_profile(uid, profile):
    u = get_user_by_id(uid)
    if not u:
        return False
    try:
        old = json.loads(u["profile"])
    except (json.JSONDecodeError, TypeError):
        old = {}
    old.update(profile or {})
    with _lock:
        with _conn() as c:
            c.execute("UPDATE users SET profile=? WHERE id=?", (json.dumps(old, ensure_ascii=False), uid))
    return True


def update_user_role(uid, role):
    with _lock:
        with _conn() as c:
            c.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
    return True


def delete_user(uid):
    with _lock:
        with _conn() as c:
            c.execute("DELETE FROM users WHERE id=?", (uid,))
    return True


def create_session(user_id):
    with _lock:
        with _conn() as c:
            cur = c.execute("SELECT COUNT(*) AS n FROM sessions WHERE user_id=?", (user_id,))
            had_previous = cur.fetchone()["n"] > 0
            c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            token = secrets.token_urlsafe(32)
            c.execute("INSERT INTO sessions (token,user_id,created_at) VALUES (?,?,?)",
                      (token, user_id, _now()))
    return token, had_previous


def get_user_by_token(token):
    if not token:
        return None
    with _lock:
        with _conn() as c:
            row = c.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
                (token,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["profile"] = json.loads(d["profile"])
    except (json.JSONDecodeError, TypeError):
        d["profile"] = {}
    return d


def delete_session(token):
    with _lock:
        with _conn() as c:
            c.execute("DELETE FROM sessions WHERE token=?", (token,))
    return True


def get_test_mode():
    """Modo prueba del administrador.

    Estructura: {"enabled": bool,
                 "global": {"factor": float, "nivel": "verde|naranja|rojo"},
                 "estaciones": {uid: {"factor": float, "nivel": str}},
                 "actualizado_en": iso}
    El factor multiplica el ICA y los sub-índices; el nivel fuerza el semáforo.
    """
    tm = get_setting("test_mode", {})
    if not isinstance(tm, dict):
        tm = {}
    tm.setdefault("enabled", False)
    tm.setdefault("global", {})
    tm.setdefault("estaciones", {})
    if not isinstance(tm["global"], dict):
        tm["global"] = {}
    if not isinstance(tm["estaciones"], dict):
        tm["estaciones"] = {}
    return tm


def set_test_mode(enabled, global_cfg=None, estaciones=None):
    """Activa/desactiva el modo prueba. Conserva lo que no se envíe."""
    tm = get_test_mode()
    tm["enabled"] = bool(enabled)
    if global_cfg is not None:
        tm["global"] = dict(global_cfg or {})
    if estaciones is not None:
        tm["estaciones"] = dict(estaciones or {})
    tm["actualizado_en"] = _now()
    set_setting("test_mode", tm)
    return tm


def _aplicar_override(est, cfg):
    """Aplica un override de prueba a una estación (dict). No muta el original."""
    if not isinstance(cfg, dict) or not cfg:
        return est
    from motor_ica import ica as _ica

    e = dict(est)
    factor = cfg.get("factor")
    try:
        factor = float(factor)
    except (TypeError, ValueError):
        factor = None
    if factor is not None and factor > 0:
        for k in ("ica", "aqi_waqi", "aqi_pm25", "aqi_pm10", "aqi_no2", "aqi_so2", "aqi_o3", "aqi_co"):
            if isinstance(e.get(k), (int, float)) and not isinstance(e.get(k), bool):
                e[k] = round(e[k] * factor)
            elif isinstance(e.get(k), str) and e.get(k).strip().isdigit():
                e[k] = round(float(e[k]) * factor)
        if isinstance(e.get("semaforo"), dict):
            s = dict(e["semaforo"])
            if isinstance(s.get("ica"), (int, float)):
                s["ica"] = min(500, round(s["ica"] * factor))
                s["nivel"] = _ica.clasificar(s["ica"])
            try:
                from motor_ica.conversion_waqi import ica_desde_waqi
                recalc = ica_desde_waqi(
                    aqi_pm25=e.get("aqi_pm25"),
                    aqi_pm10=e.get("aqi_pm10"),
                    aqi_o3=e.get("aqi_o3"),
                    aqi_no2=e.get("aqi_no2"),
                    aqi_waqi=e.get("aqi_waqi"),
                    aqi_so2=e.get("aqi_so2"),
                    aqi_co=e.get("aqi_co"),
                )
                if recalc and recalc.get("nivel"):
                    s["nivel"] = recalc["nivel"]
                    s["niveles_contaminantes"] = recalc.get("niveles_contaminantes", {})
            except Exception:
                pass
            e["semaforo"] = s
    nivel = cfg.get("nivel")
    if nivel in ("verde", "naranja", "rojo"):
        e["nivel_forzado"] = nivel
        if isinstance(e.get("semaforo"), dict):
            s = dict(e["semaforo"])
            s["nivel"] = nivel
            e["semaforo"] = s
        else:
            e["nivel"] = nivel
    e["en_prueba"] = True
    return e


def apply_test_mode_station(est, uid=None):
    """Aplica el modo prueba a UNA estación (dict) según su uid."""
    tm = get_test_mode()
    if not tm.get("enabled"):
        return est
    cfg = {}
    if tm.get("global"):
        cfg.update(tm["global"])
    if uid is not None:
        cfg.update((tm.get("estaciones") or {}).get(str(uid), {}) or {})
    return _aplicar_override(est, cfg)


def apply_test_mode_lista(estaciones):
    """Aplica el modo prueba a una lista de estaciones y añade avisos de modo."""
    tm = get_test_mode()
    if not tm.get("enabled"):
        return list(estaciones), False
    salida = [apply_test_mode_station(e, e.get("uid")) for e in estaciones]
    return salida, True


def apply_test_mode(data):
    """Compatibilidad con el formato {estaciones: {id: est}} (datos históricos)."""
    tm = get_test_mode()
    if not tm.get("enabled"):
        return data
    out = dict(data)
    ests = out.get("estaciones", {}) or {}
    new_ests = {}
    for sid, est in ests.items():
        cfg = {}
        if tm.get("global"):
            cfg.update(tm["global"])
        cfg.update((tm.get("estaciones") or {}).get(str(sid), {}) or {})
        new_ests[sid] = _aplicar_override(est, cfg)
    out["estaciones"] = new_ests
    out["modo_prueba"] = True
    out["aviso_prueba"] = "MODO PRUEBA: niveles de contaminacion modificados por el administrador."
    return out


# ---------------------------------------------------------------------------
# Conmutador WAQI: la API se consulta SOLO desde el servidor y se guarda aquí.
# ---------------------------------------------------------------------------
def _verificar_vigencia_estacion(e):
    """Revalida si una estación guardada sigue siendo reciente (<24 h) y recalcula su semáforo.

    Si la estación dejó de actualizarse (su `medido_iso` superó las 24 h), queda en
    la base de datos como reciente=False y semaforo=None (color gris en el mapa),
    pero NUNCA desaparece.
    """
    e = dict(e)
    iso = e.get("medido_iso")
    if iso:
        try:
            from web.waqi import _reciente
            e["reciente"] = _reciente(iso, max_horas=24)
        except Exception:
            pass
    if e.get("reciente") is False:
        e["semaforo"] = None
    elif e.get("reciente") and (not e.get("semaforo") or not (e.get("semaforo") or {}).get("nivel")):
        try:
            from motor_ica.conversion_waqi import ica_desde_waqi
            e["semaforo"] = ica_desde_waqi(
                aqi_pm25=e.get("aqi_pm25"),
                aqi_pm10=e.get("aqi_pm10"),
                aqi_o3=e.get("aqi_o3"),
                aqi_no2=e.get("aqi_no2"),
                aqi_waqi=e.get("aqi_waqi"),
                aqi_so2=e.get("aqi_so2"),
                aqi_co=e.get("aqi_co"),
            )
        except Exception:
            pass
    return e


def upsert_waqi(resultado):
    """Guarda/actualiza las estaciones WAQI y su metadatos en la base de datos.

    Las estaciones que ya estaban en la BD y no vinieron en esta ingesta NO se
    eliminan: se conservan y cuando pierden vigencia (>6 h) quedan en gris.
    """
    estaciones = (resultado or {}).get("estaciones", []) or []
    ahora = _now()
    vistos_uids = set()
    with _lock:
        with _conn() as c:
            for est in estaciones:
                uid = est.get("uid")
                if uid is None:
                    continue
                vistos_uids.add(str(uid))
                c.execute(
                    "INSERT INTO waqi_stations (uid,data,updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(uid) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                    (str(uid), json.dumps(est, ensure_ascii=False), ahora),
                )
            # Si había estaciones previas que no se reportaron en esta corrida,
            # actualizar su estado de vigencia para que queden en gris si expiraron.
            if estaciones:
                rows = c.execute("SELECT uid,data FROM waqi_stations").fetchall()
                for r in rows:
                    if r["uid"] not in vistos_uids:
                        try:
                            prev = json.loads(r["data"])
                            prev = _verificar_vigencia_estacion(prev)
                            c.execute("UPDATE waqi_stations SET data=? WHERE uid=?",
                                      (json.dumps(prev, ensure_ascii=False), r["uid"]))
                        except Exception:
                            pass
    meta = {k: v for k, v in (resultado or {}).items() if k != "estaciones"}
    meta["guardado_en"] = ahora
    set_setting("waqi_meta", meta)
    return len(estaciones)


def get_waqi_meta():
    meta = get_setting("waqi_meta", {})
    return meta if isinstance(meta, dict) else {}


def get_waqi_stations():
    """Lista de estaciones WAQI guardadas (con vigencia verificada y modo prueba aplicado)."""
    with _lock:
        with _conn() as c:
            rows = c.execute("SELECT uid,data,updated_at FROM waqi_stations").fetchall()
    estaciones = []
    for r in rows:
        try:
            e = json.loads(r["data"])
        except json.JSONDecodeError:
            continue
        e = _verificar_vigencia_estacion(e)
        e["_updated_at"] = r["updated_at"]
        estaciones.append(e)
    estaciones, en_prueba = apply_test_mode_lista(estaciones)
    meta = dict(get_waqi_meta())
    meta["estaciones"] = estaciones
    meta["total"] = len(estaciones)
    meta["modo_prueba"] = en_prueba
    meta["consultado_en"] = _now()
    if en_prueba:
        meta["aviso_prueba"] = "MODO PRUEBA: niveles de contaminacion modificados por el administrador."
    return meta


def get_waqi_station(uid):
    with _lock:
        with _conn() as c:
            row = c.execute("SELECT data,updated_at FROM waqi_stations WHERE uid=?", (str(uid),)).fetchone()
    if not row:
        return None
    try:
        e = json.loads(row["data"])
    except json.JSONDecodeError:
        e = {}
    e = _verificar_vigencia_estacion(e)
    e["_updated_at"] = row["updated_at"]
    return apply_test_mode_station(e, uid)


def count_waqi():
    with _lock:
        with _conn() as c:
            return c.execute("SELECT COUNT(*) AS n FROM waqi_stations").fetchone()["n"]


# ---------------------------------------------------------------------------
# Ingesta programada (auditoría)
# ---------------------------------------------------------------------------
def record_ingest_run(iniciado_en, terminado_en, ok, estaciones, detalle=""):
    with _lock:
        with _conn() as c:
            c.execute(
                "INSERT INTO ingest_runs (iniciado_en,terminado_en,ok,estaciones,detalle) VALUES (?,?,?,?,?)",
                (iniciado_en, terminado_en, 1 if ok else 0, int(estaciones or 0), detalle),
            )
    return True


def last_ingest_run():
    with _lock:
        with _conn() as c:
            row = c.execute("SELECT * FROM ingest_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Caché de hospitales / centros de salud (OSM / Overpass)
# ---------------------------------------------------------------------------
def upsert_lugares_salud(lista):
    """Guarda/actualiza lugares de salud. Devuelve cuántos se escribieron."""
    ahora = _now()
    n = 0
    with _lock:
        with _conn() as c:
            for lugar in lista or []:
                oid = lugar.get("osm_id")
                if not oid:
                    continue
                c.execute(
                    "INSERT INTO lugares_salud (osm_id,data,actualizado_en) VALUES (?,?,?) "
                    "ON CONFLICT(osm_id) DO UPDATE SET data=excluded.data, "
                    "actualizado_en=excluded.actualizado_en",
                    (str(oid), json.dumps(lugar, ensure_ascii=False), ahora),
                )
                n += 1
    return n


def get_lugares_salud():
    with _lock:
        with _conn() as c:
            rows = c.execute("SELECT osm_id,data,actualizado_en FROM lugares_salud").fetchall()
    salida = []
    for r in rows:
        try:
            d = json.loads(r["data"])
        except json.JSONDecodeError:
            continue
        d["_cache_en"] = r["actualizado_en"]
        salida.append(d)
    return salida


def count_lugares_salud():
    with _lock:
        with _conn() as c:
            return c.execute("SELECT COUNT(*) AS n FROM lugares_salud").fetchone()["n"]


# ---------------------------------------------------------------------------
# Notificaciones / alertas
# ---------------------------------------------------------------------------
def add_notification(user_id, tipo, titulo, mensaje, nivel=None, estacion=None):
    with _lock:
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO notifications (user_id,tipo,nivel,titulo,mensaje,estacion,creada_en,leida) "
                "VALUES (?,?,?,?,?,?,?,0)",
                (user_id, tipo, nivel, titulo, mensaje, estacion, _now()),
            )
            return cur.lastrowid
    return None


def list_notifications(user_id, since_id=0, limit=50):
    with _lock:
        with _conn() as c:
            rows = c.execute(
                "SELECT * FROM notifications WHERE user_id=? AND id>? ORDER BY id ASC LIMIT ?",
                (user_id, int(since_id or 0), int(limit)),
            ).fetchall()
    return [dict(r) for r in rows]


def notifications_recientes(user_id, horas=24):
    """Notificaciones creadas en las últimas `horas` (para evitar duplicados)."""
    with _lock:
        with _conn() as c:
            rows = c.execute(
                "SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 200",
                (user_id,),
            ).fetchall()
    from datetime import datetime, timedelta
    corte = datetime.now(timezone.utc).astimezone() - timedelta(hours=horas)
    salida = []
    for r in rows:
        try:
            t = datetime.fromisoformat(r["creada_en"])
        except (ValueError, TypeError):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=corte.tzinfo)
        if t >= corte:
            salida.append(dict(r))
    return salida


def mark_notifications_read(user_id, ids=None):
    with _lock:
        with _conn() as c:
            if ids:
                marcas = ",".join("?" for _ in ids)
                c.execute(f"UPDATE notifications SET leida=1 WHERE user_id=? AND id IN ({marcas})",
                          (user_id, *ids))
            else:
                c.execute("UPDATE notifications SET leida=1 WHERE user_id=?", (user_id,))
    return True


def list_users_by_role(role):
    with _lock:
        with _conn() as c:
            rows = c.execute("SELECT * FROM users WHERE role=? ORDER BY id", (role,)).fetchall()
    salida = []
    for r in rows:
        d = dict(r)
        try:
            d["profile"] = json.loads(d["profile"])
        except (json.JSONDecodeError, TypeError):
            d["profile"] = {}
        salida.append(d)
    return salida