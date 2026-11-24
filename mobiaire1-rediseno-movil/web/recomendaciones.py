"""Advertencias personalizadas por perfil (semanas, condiciones y nivel).

Vive en el servidor para que el mensaje sea consistente entre la app, las
alertas y los reportes diarios, y para poder probarlo sin el celular.

Los textos son orientativos y de autocuidado: NO son indicaciones médicas
(ver .kiro/steering/proyecto.md, reglas 4 y 1).
"""
from datetime import datetime, date

# Contaminantes que la app reconoce y su nombre legible.
CONTAMINANTES = {
    "pm25": "PM2.5",
    "pm10": "PM10",
    "no2": "NO2 (dióxido de nitrógeno)",
    "so2": "SO2 (dióxido de azufre)",
    "o3": "ozono (O3)",
    "co": "CO (monóxido de carbono)",
}

# Mensajes de acción por nivel de contaminación (criterio clínico ICA.xlsx).
BASE_NIVEL = {
    "verde": (
        "Aire limpio real para el binomio madre-feto (Aceptable / Tranquilo, sub-índice 0–35). "
        "Sin riesgo de inflamación bronquial ni paso significativo de partículas finas a placenta. "
        "Se pueden realizar caminatas y actividades al aire libre con normalidad."
    ),
    "naranja": (
        "Zona de sensibilidad materna (Precaución, sub-índice 36–85 / 36–75 O3-NO2). "
        "Aunque otras apps marquen Verde alto o Moderado, ya hay carga suficiente de PM2.5 para "
        "detonar síntomas en gestantes con asma, rinitis o hiperreactividad bronquial y generar estrés oxidativo leve. "
        "Acción: Evitar ejercicio intenso o caminatas largas cerca de vías con tráfico pesado, "
        "ventilar solo fuera de horas pico y tener medicación de rescate disponible."
    ),
    "rojo": (
        "Riesgo alto para toda mujer gestante, con o sin asma previa (Peligro, sub-índice >= 86 / >= 76 O3-NO2). "
        "Elevada probabilidad de broncoespasmo, inflamación sistémica y afectación en el intercambio de oxígeno placentario. "
        "Acción: Permanecer en interiores con ventanas cerradas, suspender actividad física al aire libre "
        "y usar mascarilla N95 o KN95 ajustada si es obligatorio salir."
    ),
    "gris": "No hay dato reciente de calidad del aire para tu zona.",
}

# Efectos por semana de gestación (requisito 4).
SEMANAS = [
    (1, 4, ("Semanas 1-4 (preimplantación): con PM2.5 > 25 µg/m³ o picos de SO₂ aumenta el "
            "riesgo de fallo de implantación y aborto temprano.")),
    (5, 12, ("Semanas 5-12 (organogénesis): es el periodo más crítico; PM2.5 e hidrocarburos "
             "pueden causar malformaciones (cardíacas, tubo neural).")),
    (13, 27, ("Semanas 13-27 (segundo trimestre): PM10/PM2.5 elevan la inflamación; por encima "
              "de 20-25 µg/m³ de PM2.5 o 40 µg/m³ de PM10 sube el riesgo de diabetes gestacional "
              "y preeclampsia (~30%).")),
    (28, 40, ("Semanas 28-40 (tercer trimestre): PM2.5 > 15-20 µg/m³ puede provocar parto "
              "prematuro y bajo peso al nacer.")),
]

CONDICIONES = {
    "asma": ("Como tienes asma o problemas respiratorios, usa mascarilla y ten a mano tu "
             "inhalador; evita salir cuando el nivel sea naranja o rojo."),
    "hipertension": ("Con hipertensión o antecedentes de preeclampsia evita esfuerzos y estrés, "
                     "y monitorea tu presión."),
    "diabetes": ("Con diabetes mantente hidratada y controla tu glucosa; evita la actividad "
                 "intensa cuando el aire esté contaminado."),
    "ninguna": "",
}


def semanas_gestacion(profile, hoy=None):
    """Semanas de gestación desde FUM o FPP. None si no hay fechas válidas."""
    if not profile:
        return None
    hoy = hoy or date.today()
    if isinstance(hoy, datetime):
        hoy = hoy.date()
    fum = _fecha(profile.get("fum"))
    if fum:
        return max(0, (hoy - fum).days // 7)
    fpp = _fecha(profile.get("fpp"))
    if fpp:
        concepcion = fpp - _delta_dias(280)
        return max(0, (hoy - concepcion).days // 7)
    return None


def _delta_dias(n):
    from datetime import timedelta
    return timedelta(days=n)


def _fecha(valor):
    if not valor:
        return None
    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor
    try:
        return datetime.fromisoformat(str(valor)[:10]).date()
    except ValueError:
        return None


def trimestre_de(sem):
    if sem is None:
        return None
    if sem <= 12:
        return 1
    if sem <= 27:
        return 2
    return 3


def _por_semana(sem):
    if sem is None:
        return None
    for lo, hi, texto in SEMANAS:
        if lo <= sem <= hi:
            return texto
    if sem > 40:
        return SEMANAS[-1][2]
    return None


def _por_condiciones(conds):
    salida = []
    for c in conds or []:
        t = CONDICIONES.get(c)
        if t:
            salida.append(t)
    return salida


def _texto_contaminantes(presentes, ausentes):
    presentes = [p for p in (presentes or []) if p]
    ausentes = [a for a in (ausentes or []) if a]
    partes = []
    if presentes:
        nombres = ", ".join(CONTAMINANTES.get(p, p) for p in presentes)
        partes.append("La estación reporta además: " + nombres + ".")
    if ausentes:
        nombres = ", ".join(CONTAMINANTES.get(a, a) for a in ausentes)
        partes.append("La estación NO reporta: " + nombres +
                      " (esa parte se descarta por falta de dato).")
    return " ".join(partes) or None


def advertencias(profile, nivel, semaforo=None, contaminantes=None,
                 contaminantes_ausentes=None, estacion=None, hoy=None):
    """Devuelve la recomendación personalizada completa.

    `semaforo`: dict con pm25_estimado_ugm3 / pm10_estimado_ugm3 / ica (opcional).
    `contaminantes`: lista de claves presentes ('pm25','no2',...).
    Devuelve dict con el desglose y el campo `texto` listo para mostrar/enviar.
    """
    profile = profile or {}
    nivel = nivel if nivel in BASE_NIVEL else "gris"
    sem = semanas_gestacion(profile, hoy=hoy)
    tri = trimestre_de(sem)
    conds = profile.get("condiciones") or []
    if isinstance(conds, str):
        conds = [conds]

    base = BASE_NIVEL[nivel]
    por_semana = _por_semana(sem)
    por_condiciones = _por_condiciones(conds)
    cont_txt = _texto_contaminantes(contaminantes, contaminantes_ausentes)

    comentario = []
    if profile.get("primer_embarazo"):
        comentario.append("Es tu primer embarazo: ante cualquier síntoma, consulta a tu "
                          "profesional de salud.")
    if profile.get("alertas_emergencia") is False:
        comentario.append("Tienes desactivadas las alertas de emergencia en tiempo real.")
    if profile.get("comentario"):
        comentario.append(str(profile.get("comentario")))

    partes = [base]
    if sem is not None:
        partes.append(f"Vas en la semana {sem} de gestación (trimestre {tri}).")
    if por_semana:
        partes.append(por_semana)
    if estacion:
        nombre = estacion.get("nombre") if isinstance(estacion, dict) else estacion
        if nombre:
            partes.append(f"Estación de referencia: {nombre}.")
    partes.extend(por_condiciones)
    if cont_txt:
        partes.append(cont_txt)
    partes.extend(comentario)

    return {
        "nivel": nivel,
        "semanas": sem,
        "trimestre": tri,
        "base": base,
        "por_semana": por_semana,
        "por_condiciones": por_condiciones,
        "contaminantes": cont_txt,
        "comentarios": comentario,
        "texto": " ".join(p for p in partes if p).strip(),
    }


# ---------------------------------------------------------------------------
# Alertas cortas y vista de indicaciones (requisito: alerta breve con color)
# ---------------------------------------------------------------------------
# Mismos colores del semáforo que usa el cliente (verde->naranja->rojo).
COLORES = {
    "verde": "#2f8f54",
    "naranja": "#c9780f",
    "rojo": "#b3362f",
    "gris": "#6b7280",
}

# Emoji del semáforo, para dar "color" a las notificaciones del sistema.
EMOJI_NIVEL = {
    "verde": "🟢",
    "naranja": "🟠",
    "rojo": "🔴",
    "gris": "⚪",
}

# Titular CORTO de la alerta: una sola frase, sin el texto largo.
TITULAR_ALERTA = {
    "verde": "El aire está aceptable en tu zona",
    "naranja": "Precaución: hay contaminación atmosférica en tu zona",
    "rojo": "Estás expuesta a contaminación atmosférica",
    "gris": "Sin dato reciente de aire en tu zona",
}

# Pasos de autocuidado por nivel, para la vista "Ver indicaciones".
PASOS_NIVEL = {
    "verde": [
        "Puedes hacer actividades suaves al aire libre.",
        "Aprovecha para ventilar tu casa en las horas más limpias.",
    ],
    "naranja": [
        "Reduce el tiempo al aire libre y evita el ejercicio intenso.",
        "Usa tapabocas si debes salir y ventila en las horas más limpias.",
        "Si tienes asma, ten a mano tu inhalador.",
    ],
    "rojo": [
        "Permanece en interiores y cierra ventanas y puertas.",
        "Evita cualquier esfuerzo físico al aire libre.",
        "Si sientes dificultad para respirar, mareo o contracciones, busca atención.",
    ],
    "gris": [
        "No hay dato reciente; actúa con precaución hasta tener información.",
        "Puedes volver a consultar más tarde o elegir otra zona.",
    ],
}


def resumen_alerta(nivel):
    """Mensaje corto de alerta (una sola frase) según el nivel."""
    return TITULAR_ALERTA.get(nivel if nivel in TITULAR_ALERTA else "gris")


def emoji_nivel(nivel):
    """Emoji del semáforo para la notificación (color en el celular)."""
    return EMOJI_NIVEL.get(nivel if nivel in EMOJI_NIVEL else "gris")


def indicaciones(profile, nivel, semaforo=None, contaminantes=None,
                 contaminantes_ausentes=None, estacion=None, hoy=None):
    """Contenido completo y ordenado para la vista "Ver indicaciones".

    Reutiliza `advertencias` y lo agrupa en secciones listas para mostrar,
    con el color y el titular del nivel (paleta rojo/naranja/verde/gris).
    """
    d = advertencias(profile, nivel, semaforo=semaforo, contaminantes=contaminantes,
                     contaminantes_ausentes=contaminantes_ausentes, estacion=estacion, hoy=hoy)
    n = d["nivel"]
    secciones = [{"titulo": "Qué significa", "icono": "aire", "items": [d["base"]]}]

    sem = d.get("semanas")
    if sem is not None:
        titulo_sem = f"Tu embarazo · semana {sem} (trimestre {d.get('trimestre')})"
    else:
        titulo_sem = "Tu embarazo"
    items_sem = [d["por_semana"]] if d.get("por_semana") else [
        "Registra tu FUM o FPP en tu perfil para recibir avisos según tus semanas."
    ]
    secciones.append({"titulo": titulo_sem, "icono": "embarazo", "items": items_sem})

    if d.get("por_condiciones"):
        secciones.append({"titulo": "Según tus condiciones", "icono": "salud",
                          "items": d["por_condiciones"]})
    if d.get("contaminantes"):
        secciones.append({"titulo": "Contaminantes", "icono": "contaminantes",
                          "items": [d["contaminantes"]]})
    secciones.append({"titulo": "Qué hacer ahora", "icono": "accion",
                      "items": PASOS_NIVEL.get(n, PASOS_NIVEL["gris"])})
    if d.get("comentarios"):
        secciones.append({"titulo": "Notas", "icono": "nota", "items": d["comentarios"]})

    return {
        "nivel": n,
        "color": COLORES.get(n, COLORES["gris"]),
        "titular": resumen_alerta(n),
        "semanas": sem,
        "trimestre": d.get("trimestre"),
        "secciones": secciones,
        "texto": d["texto"],
    }
