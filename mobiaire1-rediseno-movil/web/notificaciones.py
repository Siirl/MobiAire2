"""Genera alertas personalizadas y reportes diarios para las usuarias.

El servidor es quien decide y guarda las notificaciones (tabla `notifications`).
El cliente solo las pide y las muestra, así que no hay lógica de riesgo en el
celular y las alertas también funcionan en MODO PRUEBA del administrador.
"""
from datetime import datetime

import db
from zonas import asociacion
from web import recomendaciones


def _nivel_de(est):
    if not est:
        return None
    sem = est.get("semaforo") or {}
    return sem.get("nivel") or est.get("nivel_forzado") or est.get("nivel")


def _mapa_zonas():
    """{zona_id: {estacion: {...}, ...}} a partir de las estaciones guardadas."""
    estaciones = db.get_waqi_stations().get("estaciones", [])
    candidatas = [
        {"uid": e.get("uid"), "nombre": e.get("nombre"), "lat": e.get("lat"), "lon": e.get("lon")}
        for e in estaciones
        if e.get("lat") is not None and e.get("lon") is not None
    ]
    tabla = asociacion.asociar(candidatas)
    return {z["id"]: z for z in tabla.get("zonas", [])}


def _estacion_de_usuario(profile, estaciones, zonas):
    """Estación de referencia del usuario.

    1) Si eligió una zona (UPZ), la estación asociada a esa zona.
    2) Si no hay zona, la estación MÁS CERCANA a su última ubicación conocida
       (el cliente envía lat/lon al servidor). Así la persona igual recibe
       avisos de la estación más cercana que tenga datos.
    """
    profile = profile or {}
    uid = None
    zona_id = profile.get("zona")
    if zona_id and zona_id in zonas:
        uid = (zonas[zona_id].get("estacion") or {}).get("uid")
    if uid is not None:
        return estaciones.get(str(uid))

    lat, lon = profile.get("lat"), profile.get("lon")
    if lat is None or lon is None:
        return None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    candidatas = [e for e in estaciones.values()
                  if e.get("lat") is not None and e.get("lon") is not None and e.get("reciente", True)]
    if not candidatas:
        candidatas = [e for e in estaciones.values()
                      if e.get("lat") is not None and e.get("lon") is not None]
    est, _ = asociacion.estacion_mas_cercana(lat, lon, candidatas)
    if est is None:
        return None
    return estaciones.get(str(est.get("uid")))


def generar_alertas(forzar=False):
    """Crea alertas naranja/rojo para cada usuaria según su zona o estación más cercana."""
    estaciones = {str(e.get("uid")): e for e in db.get_waqi_stations().get("estaciones", [])}
    zonas = _mapa_zonas()
    creadas = 0
    for u in db.list_users_by_role("gestante"):
        prof = u.get("profile") or {}
        if prof.get("alertas_emergencia") is False:
            continue
        est = _estacion_de_usuario(prof, estaciones, zonas)
        if not est and not forzar:
            continue
        if not est and forzar and estaciones:
            # Si el admin forzó una alerta global y la usuaria aún no tiene zona ni GPS,
            # usar Ciudad Bolívar o la primera estación en alerta como respaldo.
            est = (estaciones.get("A522148") or estaciones.get("-522148") or
                   next((x for x in estaciones.values() if _nivel_de(x) in ("naranja", "rojo")), None))
        nivel = _nivel_de(est)
        if nivel not in ("naranja", "rojo"):
            continue
        uid = str((est or {}).get("uid"))
        if not forzar:
            recientes = db.notifications_recientes(u["id"], horas=6)
            ya = any(n["tipo"] == "alerta" and n.get("nivel") == nivel and
                     str(n.get("estacion")) == uid for n in recientes)
            if ya:
                continue
        emoji = recomendaciones.emoji_nivel(nivel)
        db.add_notification(
            u["id"], "alerta",
            f"{emoji} Alerta {nivel}",
            recomendaciones.resumen_alerta(nivel), nivel, uid,
        )
        creadas += 1
    return creadas


def generar_reportes_diarios(ahora=None):
    """Crea el reporte diario en la hora preferida de cada usuaria."""
    ahora = ahora or datetime.now()
    hhmm = ahora.strftime("%H:%M")
    estaciones = {str(e.get("uid")): e for e in db.get_waqi_stations().get("estaciones", [])}
    zonas = _mapa_zonas()
    creadas = 0
    for u in db.list_users_by_role("gestante"):
        prof = u.get("profile") or {}
        hora = prof.get("hora_reporte") or "08:00"
        if hhmm < hora:
            continue
        recientes = db.notifications_recientes(u["id"], horas=20)
        if any(n["tipo"] == "reporte_diario" for n in recientes):
            continue
        est = _estacion_de_usuario(prof, estaciones, zonas)
        nivel = _nivel_de(est) or "gris"
        emoji = recomendaciones.emoji_nivel(nivel)
        db.add_notification(
            u["id"], "reporte_diario",
            f"{emoji} Recomendaciones para hoy",
            "Toca para ver qué hacer según tu zona y tu embarazo.",
            nivel, str((est or {}).get("uid") or ""),
        )
        creadas += 1
    return creadas
