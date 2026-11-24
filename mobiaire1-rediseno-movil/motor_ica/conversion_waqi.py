"""Conversión del sub-índice AQI de WAQI (escala US-EPA) a nuestro ICA (Res. 2254).

WAQI entrega el AQI ya calculado por contaminante (sub-índice US-EPA, 0-500), NO
la concentración. Para alimentar nuestro motor (que trabaja en µg/m³) hacemos:

    sub-índice AQI (EPA)  --interpolación inversa-->  concentración µg/m³
    concentración µg/m³   --motor_ica.ica (Res 2254)-->  ICA del proyecto

Es una APROXIMACIÓN y así se documenta (ver .kiro/steering/proyecto.md):
  - depende de la versión de la tabla EPA que use WAQI;
  - es un valor horario, no el promedio de 24 h que pide la norma.
Por eso el resultado se rotula "ICA estimado (derivado de WAQI)" y NUNCA se
presenta como el ICA oficial ni se mezcla con el índice WAQI original.

No modifica motor_ica/ica.py: lo reutiliza.
"""
from motor_ica import ica as _ica

# Breakpoints oficiales de la US-EPA (40 CFR Part 58, actualización PM2.5 2024).
# (AQI_lo, AQI_hi, C_lo, C_hi). Concentraciones en µg/m³, promedio 24 h.
# PM2.5 con los cortes vigentes desde 2024 (9.0/35.4/...); PM10 estándar EPA.
EPA = {
    "PM25": [
        (0, 50, 0.0, 9.0),
        (51, 100, 9.1, 35.4),
        (101, 150, 35.5, 55.4),
        (151, 200, 55.5, 125.4),
        (201, 300, 125.5, 225.4),
        (301, 500, 225.5, 500.4),
    ],
    "PM10": [
        (0, 50, 0, 54),
        (51, 100, 55, 154),
        (101, 150, 155, 254),
        (151, 200, 255, 354),
        (201, 300, 355, 424),
        (301, 500, 425, 604),
    ],
}


def subindice_a_concentracion(contaminante, aqi):
    """Interpolación inversa EPA: sub-índice AQI -> concentración µg/m³.

    Devuelve None si el sub-índice no es un número válido en 0-500.
    """
    if contaminante not in EPA:
        raise ValueError(f"Contaminante no soportado: {contaminante}")
    if aqi is None:
        return None
    try:
        aqi = float(aqi)
    except (TypeError, ValueError):
        return None
    if aqi < 0 or aqi > 500:
        return None
    for i_lo, i_hi, c_lo, c_hi in EPA[contaminante]:
        if i_lo <= aqi <= i_hi:
            # inversa de la fórmula AQI lineal por tramos
            return (aqi - i_lo) / (i_hi - i_lo) * (c_hi - c_lo) + c_lo
    return None


# Tabla de semáforo materno (ICA.xlsx):
#   aqi_waqi / aqi_pm25 / aqi_pm10:
#     Verde:   0 – 35
#     Naranja: 36 – 85
#     Rojo:    >= 86
#   aqi_o3 / aqi_no2:
#     Verde:   0 – 35
#     Naranja: 36 – 75
#     Rojo:    >= 76
UMBRALES_MATERNO = {
    "PM25": (35, 85),
    "PM10": (35, 85),
    "WAQI": (35, 85),
    "O3":   (35, 75),
    "NO2":  (35, 75),
    "SO2":  (35, 85),
    "CO":   (35, 85),
}
ORDEN_NIVEL = {"verde": 0, "naranja": 1, "rojo": 2}


def clasificar_subindice_waqi(contaminante, aqi):
    """Clasifica un sub-índice WAQI según la tabla materna de ICA.xlsx."""
    if aqi is None:
        return None
    try:
        v = float(aqi)
    except (TypeError, ValueError):
        return None
    if v < 0:
        return None
    tope_verde, tope_naranja = UMBRALES_MATERNO.get(contaminante.upper(), (35, 85))
    if v <= tope_verde:
        return "verde"
    if v <= tope_naranja:
        return "naranja"
    return "rojo"


def ica_desde_waqi(aqi_pm25=None, aqi_pm10=None, aqi_o3=None, aqi_no2=None,
                   aqi_waqi=None, aqi_so2=None, aqi_co=None):
    """ICA y semáforo del proyecto a partir de los sub-índices WAQI (tabla ICA.xlsx).

    Evalúa los rangos maternos de ICA.xlsx por contaminante:
      - PM2.5 / PM10 / AQI general: Verde 0-35, Naranja 36-85, Rojo >=86
      - O3 / NO2: Verde 0-35, Naranja 36-75, Rojo >=76
    El color final de la estación es el nivel más restrictivo entre los
    contaminantes reportados.
    """
    pm25 = subindice_a_concentracion("PM25", aqi_pm25)
    pm10 = subindice_a_concentracion("PM10", aqi_pm10)
    r = _ica.calcular_ica(pm25=pm25, pm10=pm10)

    # Evaluar todos los sub-índices disponibles contra la tabla ICA.xlsx
    candidatos = [
        ("PM25", aqi_pm25),
        ("PM10", aqi_pm10),
        ("O3", aqi_o3),
        ("NO2", aqi_no2),
        ("SO2", aqi_so2),
        ("CO", aqi_co),
        ("WAQI", aqi_waqi),
    ]
    niveles_por_cont = {}
    valores_por_cont = {}
    for clave, val in candidatos:
        nv = clasificar_subindice_waqi(clave, val)
        if nv is not None:
            niveles_por_cont[clave] = nv
            valores_por_cont[clave] = round(float(val))

    if r is None and not niveles_por_cont:
        return None

    # Determinar el nivel más restrictivo según ICA.xlsx
    if niveles_por_cont:
        # Prioriza contaminantes específicos; usa WAQI general si es mayor o único
        dom_tabla = max(
            niveles_por_cont.keys(),
            key=lambda k: (ORDEN_NIVEL[niveles_por_cont[k]], valores_por_cont[k], k != "WAQI")
        )
        nivel_tabla = niveles_por_cont[dom_tabla]
    else:
        dom_tabla = r["contaminante_dominante"]
        nivel_tabla = _ica.clasificar(r["ica"])

    if r is None:
        ica_val = valores_por_cont.get(dom_tabla, 0)
        r = {
            "ica": ica_val,
            "nivel": nivel_tabla,
            "categoria": _ica.categoria_oficial(min(500, max(0, ica_val))),
            "contaminante_dominante": dom_tabla,
            "ica_pm25": valores_por_cont.get("PM25"),
            "ica_pm10": valores_por_cont.get("PM10"),
            "completo": ("PM25" in valores_por_cont and "PM10" in valores_por_cont),
        }
    else:
        r = dict(r)
        r["nivel"] = nivel_tabla
        if dom_tabla in ("O3", "NO2") and ORDEN_NIVEL[nivel_tabla] > ORDEN_NIVEL.get(
            clasificar_subindice_waqi(r["contaminante_dominante"],
                                      aqi_pm25 if r["contaminante_dominante"] == "PM25" else aqi_pm10) or "verde", 0
        ):
            r["contaminante_dominante"] = dom_tabla

    r["estimado"] = True
    r["niveles_contaminantes"] = niveles_por_cont
    r["origen"] = "WAQI (sub-índices US-EPA) -> Semáforo Materno (ICA.xlsx) + ICA Res. 2254"
    r["nota"] = ("Semáforo materno según tabla ICA.xlsx (Verde 0-35, Naranja 36-85 "
                 "[36-75 O3/NO2], Rojo >=86 [>=76 O3/NO2]).")
    r["pm25_estimado_ugm3"] = round(pm25, 1) if pm25 is not None else None
    r["pm10_estimado_ugm3"] = round(pm10, 1) if pm10 is not None else None
    return r
