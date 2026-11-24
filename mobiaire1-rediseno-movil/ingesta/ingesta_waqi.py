"""Ingesta WAQI -> base de datos (el servidor como conmutador).

Este módulo es el ÚNICO que consulta la API de WAQI en nombre de toda la app.
Los clientes móviles nunca hablan con WAQI: piden al servidor, y el servidor
devuelve lo que ya está guardado en la base de datos.

- `refrescar()` consulta la API con el token del backend y guarda el resultado
  (estaciones + contaminantes + ICA estimado) en la tabla `waqi_stations`.
- El servidor programa `refrescar()` cada N minutos (ingesta programada), de
  modo que la base de datos se actualiza cuando la API se actualiza.
- Después de cada ingesta se generan las alertas personalizadas.

No expone nunca el token ni llama a la API desde el frontend.
"""
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import db  # noqa: E402

# Garantiza que las tablas existan aunque el módulo se use como CLI (fuera del
# servidor). init_db() es idempotente.
db.init_db()

# Estado del planificador (visible en /api/salud y /api/admin/ingesta/estado).
_estado = {
    "en_ejecucion": False,
    "ultima_ok": None,
    "ultimo_error": None,
    "ultima_duracion_s": None,
    "intervalo_min": None,
}
_lock = threading.Lock()


def _ahora():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def estado():
    with _lock:
        e = dict(_estado)
    ultimo = db.last_ingest_run()
    e["ultimo_run"] = ultimo
    e["estaciones_en_db"] = db.count_waqi()
    e["waqi_configurado"] = _hay_token()
    return e


def _hay_token():
    try:
        from web import waqi
        return waqi.hay_token()
    except Exception:
        return False


def refrescar(limite=None):
    """Consulta WAQI y guarda el resultado en la base de datos.

    Devuelve un resumen {ok, estaciones, consultado_en}. Lanza la excepción si
    la API falla (el llamador decide si registrar el error).
    """
    from web import waqi

    with _lock:
        _estado["en_ejecucion"] = True
    inicio = _ahora()
    t0 = time.time()
    try:
        datos = waqi.estaciones_con_semaforo(forzar=True)
        estaciones = list(datos.get("estaciones", []) or [])
        if limite:
            estaciones = estaciones[: int(limite)]
        resultado = {
            "fuente": datos.get("fuente") or "WAQI / AQICN",
            "atribucion": datos.get("atribucion") or ["World Air Quality Index Project"],
            "nota_escala": datos.get("nota_escala"),
            "consultado_en": _ahora(),
            "estaciones": estaciones,
        }
        n = db.upsert_waqi(resultado)
        db.set_setting("waqi_ultima_ingesta", resultado["consultado_en"])
        db.set_setting("waqi_error", None)
        with _lock:
            _estado["ultima_ok"] = resultado["consultado_en"]
            _estado["ultimo_error"] = None
            _estado["ultima_duracion_s"] = round(time.time() - t0, 2)
        db.record_ingest_run(inicio, _ahora(), True, n, "")
        # Generar alertas a partir de los datos recién guardados.
        try:
            from web import notificaciones
            notificaciones.generar_alertas()
        except Exception as e:  # nunca tumbar la ingesta por las alertas
            db.set_setting("alertas_error", str(e))
        return {"ok": True, "estaciones": n, "consultado_en": resultado["consultado_en"]}
    except Exception as e:
        with _lock:
            _estado["ultimo_error"] = str(e)
            _estado["ultima_duracion_s"] = round(time.time() - t0, 2)
        db.set_setting("waqi_error", str(e))
        db.record_ingest_run(inicio, _ahora(), False, 0, str(e))
        raise


def programar(intervalo_min=15, limite=None, primera=True):
    """Lanza un hilo que refresca cada `intervalo_min` minutos (ingesta programada)."""
    with _lock:
        _estado["intervalo_min"] = intervalo_min

    def bucle():
        if not primera:
            time.sleep(max(1, intervalo_min) * 60)
        while True:
            try:
                refrescar(limite=limite)
            except Exception:
                pass
            time.sleep(max(1, intervalo_min) * 60)

    hilo = threading.Thread(target=bucle, name="ingesta-waqi", daemon=True)
    hilo.start()
    return hilo


def programar_reportes(intervalo_s=60):
    """Hilo ligero que revisa cada minuto si toca enviar reportes diarios."""
    def bucle():
        while True:
            try:
                from web import notificaciones
                notificaciones.generar_reportes_diarios()
            except Exception:
                pass
            time.sleep(max(10, intervalo_s))

    hilo = threading.Thread(target=bucle, name="reportes-diarios", daemon=True)
    hilo.start()
    return hilo
