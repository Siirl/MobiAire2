"""Pruebas de las advertencias personalizadas por perfil (requisito 4)."""
from datetime import date, timedelta

from web import recomendaciones as rec


def test_semanas_desde_fum():
    hoy = date(2026, 6, 1)
    perfil = {"fum": (hoy - timedelta(weeks=10)).isoformat()}
    assert rec.semanas_gestacion(perfil, hoy=hoy) == 10


def test_semanas_desde_fpp():
    hoy = date(2026, 6, 1)
    fpp = hoy + timedelta(days=70)          # faltan 10 semanas para el parto -> 30 semanas
    assert rec.semanas_gestacion({"fpp": fpp.isoformat()}, hoy=hoy) == 30


def test_trimestre():
    assert rec.trimestre_de(2) == 1
    assert rec.trimestre_de(12) == 1
    assert rec.trimestre_de(20) == 2
    assert rec.trimestre_de(30) == 3
    assert rec.trimestre_de(None) is None


def test_aviso_por_semana_y_nivel():
    perfil = {"fum": (date(2026, 6, 1) - timedelta(weeks=8)).isoformat(),
              "condiciones": ["asma"], "primer_embarazo": True}
    a = rec.advertencias(perfil, "rojo", hoy=date(2026, 6, 1))
    assert a["nivel"] == "rojo"
    assert a["semanas"] == 8 and a["trimestre"] == 1
    assert "organogénesis" in a["texto"]
    assert "interiores" in a["texto"]         # mensaje de acción por nivel rojo
    assert any("asma" in c for c in a["por_condiciones"])
    assert "primer embarazo" in a["texto"]


def test_aviso_tercer_trimestre():
    perfil = {"fum": (date(2026, 6, 1) - timedelta(weeks=30)).isoformat()}
    a = rec.advertencias(perfil, "naranja", hoy=date(2026, 6, 1))
    assert "parto prematuro" in a["texto"]


def test_contaminantes_presentes_y_ausentes():
    perfil = {}
    a = rec.advertencias(perfil, "verde", contaminantes=["pm25", "no2", "o3"],
                         contaminantes_ausentes=["so2", "co"])
    assert "NO2" in a["contaminantes"] and "ozono" in a["contaminantes"]
    assert "NO reporta" in a["contaminantes"]


def test_sin_datos_es_gris():
    a = rec.advertencias({}, None)
    assert a["nivel"] == "gris"
    assert a["texto"]


# --- ronda 2: alerta corta + vista "Ver indicaciones" -------------------
def test_resumen_alerta_es_corto_y_por_nivel():
    assert rec.resumen_alerta("rojo") == "Estás expuesta a contaminación atmosférica"
    assert "contaminación" in rec.resumen_alerta("naranja")
    # Un nivel desconocido o vacío cae en el mensaje gris.
    assert rec.resumen_alerta(None) == rec.resumen_alerta("gris")


def test_indicaciones_trae_secciones_y_color():
    perfil = {"fum": (date(2026, 6, 1) - timedelta(weeks=30)).isoformat(),
              "condiciones": ["asma"]}
    d = rec.indicaciones(perfil, "rojo", contaminantes=["pm25"],
                         contaminantes_ausentes=["so2", "co"], hoy=date(2026, 6, 1))
    assert d["nivel"] == "rojo"
    assert d["color"] == rec.COLORES["rojo"]
    assert d["titular"] == rec.resumen_alerta("rojo")
    titulos = [s["titulo"] for s in d["secciones"]]
    assert any("semana 30" in t for t in titulos)          # personalizado por semanas
    assert any("condiciones" in t.lower() for t in titulos)
    assert "Qué hacer ahora" in titulos                    # pasos por nivel
    assert all(s["items"] for s in d["secciones"])         # ninguna sección vacía
