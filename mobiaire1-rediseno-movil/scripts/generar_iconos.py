"""Genera los iconos de la app SIN dependencias (solo stdlib: zlib, struct, re).

Reproduce FIELMENTE el logo de la app (el mismo de la esquina superior izquierda:
una gestante con su vientre coral y una hoja de aire limpio) aplanando las curvas
del SVG a polígonos y rellenándolos. Genera:

  - Web / cliente:  web/iconos/icono-192.png, icono-512.png
  - Android:        android-app/.../res/mipmap-*/ic_launcher.png (+ round)
  - Notificación:   android-app/.../res/drawable-*/ic_stat_mobiaire.png (blanco)

Uso:  python -m scripts.generar_iconos
La fuente vectorial es web/iconos/icono.svg (y el SVG del encabezado).
"""
import math
import re
import struct
import zlib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DEST_WEB = RAIZ / "web" / "iconos"
DEST_RES = RAIZ / "android-app" / "app" / "src" / "main" / "res"

# --- Paleta (idéntica al SVG del encabezado) --------------------------------
VERDE = (47, 107, 79)        # #2f6b4f  fondo
CREMA = (231, 241, 234)      # #e7f1ea  círculo
HOJA = (143, 174, 155)       # #8fae9b
CABEZA = (47, 107, 79)       # #2f6b4f
CUERPO = (61, 131, 98)       # #3d8362
VIENTRE = (232, 146, 124)    # #e8927c
BLANCO = (255, 255, 255)
TRANSPARENTE = (0, 0, 0, 0)

# --- Geometría del logo (coordenadas del SVG de 48x48) ----------------------
D_HOJA = ("M31 9c5 .6 7.6 5.4 5.6 10.4-4.2-1.1-6.4-5.2-6.2-8.2"
          ".1-1 .3-1.7 .6-2.2z")
D_CUERPO = ("M20.4 18.8c3.7-.5 7.3 .7 8.7 4.6 1.6 4.2 .7 9.6-.7 15.1h-9.1"
            "c-1-4.2-1.3-8.7 .4-12.6-2.7-1.2-4.8-3.7-4-6.4.4-1.2 1.2-1.9 4.7-.7z")
CABEZA_C = (24.5, 13.6, 4.5)
VIENTRE_C = (20.6, 30.2, 5.7)

ANDROID = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
NOTIF = {"mdpi": 24, "hdpi": 36, "xhdpi": 48, "xxhdpi": 72, "xxxhdpi": 96}


# ---------------------------------------------------------------------------
# Mini-aplanador de rutas SVG (M/m, L/l, H/h, V/v, C/c, Z/z) -> polígonos
# ---------------------------------------------------------------------------
def _tokenizar(d):
    tokens = []
    i = 0
    while i < len(d):
        ch = d[i]
        if ch in "MmLlHhVvCcZz":
            tokens.append(ch)
            i += 1
        elif ch in " ,\t\n":
            i += 1
        else:
            m = re.match(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?", d[i:])
            if not m:
                i += 1
                continue
            tokens.append(float(m.group()))
            i += len(m.group())
    return tokens


def _bezier(p0, p1, p2, p3, n=20):
    pts = []
    for k in range(1, n + 1):
        t = k / n
        mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def _aplanar(d):
    """Devuelve una lista de subrutas (cada una lista de puntos)."""
    toks = _tokenizar(d)
    sub, poly = [], []
    cur = [0.0, 0.0]
    inicio = [0.0, 0.0]
    cmd = None
    i = 0

    def num():
        nonlocal i
        v = toks[i]
        i += 1
        return v

    while i < len(toks):
        t = toks[i]
        if isinstance(t, str):
            cmd = t
            i += 1
            if cmd in "Zz":
                if poly:
                    poly.append(tuple(inicio))
                    sub.append(poly)
                    poly = []
                cur = list(inicio)
                continue
        if cmd in "Mm":
            x, y = num(), num()
            if cmd == "m":
                x += cur[0]
                y += cur[1]
            if poly:
                sub.append(poly)
                poly = []
            cur = [x, y]
            inicio = [x, y]
            poly = [(x, y)]
            cmd = "l" if cmd == "m" else "L"
        elif cmd in "Ll":
            x, y = num(), num()
            if cmd == "l":
                x += cur[0]
                y += cur[1]
            cur = [x, y]
            poly.append((x, y))
        elif cmd in "Hh":
            x = num()
            if cmd == "h":
                x += cur[0]
            cur = [x, cur[1]]
            poly.append(tuple(cur))
        elif cmd in "Vv":
            y = num()
            if cmd == "v":
                y += cur[1]
            cur = [cur[0], y]
            poly.append(tuple(cur))
        elif cmd in "Cc":
            x1, y1, x2, y2, x, y = (num(), num(), num(), num(), num(), num())
            if cmd == "c":
                p1 = (cur[0] + x1, cur[1] + y1)
                p2 = (cur[0] + x2, cur[1] + y2)
                p3 = (cur[0] + x, cur[1] + y)
            else:
                p1, p2, p3 = (x1, y1), (x2, y2), (x, y)
            poly.extend(_bezier(tuple(cur), p1, p2, p3))
            cur = [p3[0], p3[1]]
        else:
            i += 1
    if poly:
        poly.append(tuple(cur))
        sub.append(poly)
    return sub


# Aplanar y escalar al espacio 512x512, centrando la figura.
_CRUDO = _aplanar(D_HOJA) + _aplanar(D_CUERPO)
_puntos = [p for sp in _CRUDO for p in sp]
# Incluir los círculos (cabeza y vientre) en la caja envolvente.
for (cx, cy, r) in (CABEZA_C, VIENTRE_C):
    _puntos += [(cx - r, cy - r), (cx + r, cy + r)]
_minx = min(p[0] for p in _puntos)
_maxx = max(p[0] for p in _puntos)
_miny = min(p[1] for p in _puntos)
_maxy = max(p[1] for p in _puntos)
_cx = (_minx + _maxx) / 2
_cy = (_miny + _maxy) / 2
_ESCALA = 320.0 / (_maxy - _miny)          # alto de la figura ~320 px
_OX = 256 - _cx * _ESCALA
_OY = 256 - _cy * _ESCALA


def _t(p):
    return (_OX + p[0] * _ESCALA, _OY + p[1] * _ESCALA)


POLY_HOJA = [[_t(p) for p in sp] for sp in _aplanar(D_HOJA)]
POLY_CUERPO = [[_t(p) for p in sp] for sp in _aplanar(D_CUERPO)]
CABEZA_GEO = _t((CABEZA_C[0], CABEZA_C[1])) + (CABEZA_C[2] * _ESCALA,)
VIENTRE_GEO = _t((VIENTRE_C[0], VIENTRE_C[1])) + (VIENTRE_C[2] * _ESCALA,)
R_CREMA = 214.0


def _circulo(px, py, cx, cy, r):
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def _en_poligonos(px, py, subrutas):
    dentro = False
    for poly in subrutas:
        n = len(poly)
        j = n - 1
        for k in range(n):
            xi, yi = poly[k]
            xj, yj = poly[j]
            if (yi > py) != (yj > py):
                xcorte = (xj - xi) * (py - yi) / (yj - yi) + xi
                if px < xcorte:
                    dentro = not dentro
            j = k
    return dentro


def _en_figura(px, py):
    """¿El punto cae en la silueta de la gestante/hoja (sin fondo)?"""
    if _en_poligonos(px, py, POLY_HOJA):
        return True
    if _circulo(px, py, *CABEZA_GEO):
        return True
    if _en_poligonos(px, py, POLY_CUERPO):
        return True
    if _circulo(px, py, *VIENTRE_GEO):
        return True
    return False


def _color(fx, fy, redondo=False, silueta=False):
    """Color RGBA de un punto en el espacio 512x512."""
    if silueta:                                  # icono de notificación (blanco)
        return BLANCO + (255,) if _en_figura(fx, fy) else TRANSPARENTE
    if redondo and not _circulo(fx, fy, 256, 256, 256):
        return TRANSPARENTE
    col = VERDE
    if _circulo(fx, fy, 256, 256, R_CREMA):
        col = CREMA
        if _en_poligonos(fx, fy, POLY_HOJA):
            col = HOJA
        if _circulo(fx, fy, *CABEZA_GEO):
            col = CABEZA
        if _en_poligonos(fx, fy, POLY_CUERPO):
            col = CUERPO
        if _circulo(fx, fy, *VIENTRE_GEO):
            col = VIENTRE
    return (col[0], col[1], col[2], 255)


def _dibujar(n, redondo=False, silueta=False, ss=4):
    filas = []
    paso = 512.0 / n
    for y in range(n):
        fila = bytearray()
        for x in range(n):
            r = g = b = a = 0
            for sy in range(ss):
                fy = (y + (sy + 0.5) / ss) * paso
                for sx in range(ss):
                    fx = (x + (sx + 0.5) / ss) * paso
                    cr, cg, cb, ca = _color(fx, fy, redondo, silueta)
                    r += cr * ca
                    g += cg * ca
                    b += cb * ca
                    a += ca
            total = ss * ss
            if a == 0:
                fila += bytes((0, 0, 0, 0))
            else:
                fila += bytes((r // a, g // a, b // a, a // total))
        filas.append(fila)
    return filas


def _png(n, ruta, redondo=False, silueta=False, ss=4):
    filas = _dibujar(n, redondo=redondo, silueta=silueta, ss=ss)
    cruda = bytearray()
    for fila in filas:
        cruda.append(0)
        cruda += fila
    comprimido = zlib.compress(bytes(cruda), 9)

    def chunk(tipo, datos):
        c = struct.pack(">I", len(datos)) + tipo + datos
        return c + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)

    firma = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)   # RGBA
    datos = firma + chunk(b"IHDR", ihdr) + chunk(b"IDAT", comprimido) + chunk(b"IEND", b"")
    ruta.write_bytes(datos)
    return len(datos)


def main():
    DEST_WEB.mkdir(parents=True, exist_ok=True)
    for n in (192, 512):
        tam = _png(n, DEST_WEB / f"icono-{n}.png")
        print(f"Escrito web/iconos/icono-{n}.png ({n}x{n}, {tam} bytes)")

    for carpeta, n in ANDROID.items():
        dest = DEST_RES / f"mipmap-{carpeta}"
        dest.mkdir(parents=True, exist_ok=True)
        t1 = _png(n, dest / "ic_launcher.png")
        t2 = _png(n, dest / "ic_launcher_round.png", redondo=True)
        print(f"Escrito mipmap-{carpeta}/ic_launcher(.round).png ({n}x{n})")

    for carpeta, n in NOTIF.items():
        dest = DEST_RES / f"drawable-{carpeta}"
        dest.mkdir(parents=True, exist_ok=True)
        t = _png(n, dest / "ic_stat_mobiaire.png", silueta=True)
        print(f"Escrito drawable-{carpeta}/ic_stat_mobiaire.png ({n}x{n}, {t} bytes)")


if __name__ == "__main__":
    main()
