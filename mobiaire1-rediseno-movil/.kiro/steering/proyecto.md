---
inclusion: always
---
# MobiAire Materno · contexto del proyecto

Semáforo informativo de calidad del aire (PM2.5, PM10 y demás contaminantes) para gestantes de Ciudad Bolívar, Bogotá. Trabajo académico de Análisis de Sistemas (Universidad Distrital). No es un diagnóstico médico.

## Arquitectura vigente: cliente-servidor
- **Servidor = este PC** (`web/server.py`). Es el **conmutador**: el ÚNICO que consulta la API (WAQI) con el token del backend, guarda el resultado en SQLite (`ingesta/mobiaire.db`) y la actualiza de forma programada (ingesta cada N minutos). También gestiona usuarios, sesiones, notificaciones, modo prueba y logs.
- **Clientes = teléfonos** (APK Android en `android-app/` y app iOS en `ios-app/`). Son `WebView`/`WKWebView` nativos que cargan la interfaz desde el servidor. **Nunca** llaman a la API de WAQI: piden los datos ya guardados.
- Ya **no hay PWA**: sin `service-worker`, sin `manifest`, sin botón "Instalar".

## Fuente de datos y cálculo (decisión del equipo)
- Fuente principal en tiempo real: WAQI (datos del Observatorio Ambiental de Bogotá, ya convertidos a escala AQI de EE. UU.).
- El equipo convierte los sub-índices WAQI de PM2.5 y PM10 a concentraciones (interpolación inversa con los cortes de la EPA) y calcula con su motor el ICA de la Resolución 2254 de 2017. Resultado: "ICA estimado (derivado de WAQI)". Es una aproximación y se documenta: depende de la versión de la tabla EPA que use WAQI y es un dato horario, no el promedio de 24 h que pide la norma.
- El índice WAQI original nunca se muestra como si fuera el ICA; si se muestra, se rotula "Índice WAQI (escala US-EPA)".
- Se consideran también NO₂, SO₂, O₃ y CO cuando la estación los reporta; si no, se avisa y se descarta esa parte.
- IDEAM/SISAIRE (dataset g4t8-zkc3) es HISTÓRICO (hasta 31/12/2024): sirve para validar el motor con datos reales y como modo demo. Se muestra aparte y rotulado, y nunca alimenta el semáforo ni las alertas.
- Un dato es reciente si tiene menos de 6 horas. Sin dato reciente no hay semáforo vigente, ni recomendación, ni alerta.

## Estado actual
- Motor ICA (Res. 2254) en `motor_ica/`, ingesta histórica en `ingesta/`, asociación zona↔estación en `zonas/`.
- **Servidor cliente-servidor** (`web/server.py`) con: conmutador WAQI, base de datos de usuarios (contraseña cifrada con PBKDF2), sesiones (una por usuario), notificaciones, modo prueba, logs del servidor y del cliente.
- **Clientes**: APK Android (`android-app/`, `MobiAireMaterno.apk` en la raíz) e iOS (`ios-app/`). Interfaz en `web/prototipo/index.html` + `web/app-movil.js`.
- **122 pruebas** pytest (motor, ingesta, zonas, recomendaciones, BD y servidor HTTP con base de datos temporal).

## Niveles del semáforo
verde ICA 0-100 · naranja 101-150 · rojo 151-500 (agrupan las 6 categorías oficiales).

## Roles y sesiones
- Dos roles: **Usuario (gestante)** y **Administrador**. Existe **un único administrador**: usuario `admin`, contraseña `Analissdesistemas`. No se pueden crear ni promover más administradores.
- Las contraseñas se guardan **cifradas** (PBKDF2-HMAC-SHA256 con sal) en la tabla `users`.
- **Una sola sesión por usuario**: al iniciar sesión en otro dispositivo se cierra la anterior y se avisa al usuario.
- El token de sesión se guarda en el cliente (`localStorage`) para sostener el login.

## Reglas que no se negocian
1. No inventar coordenadas, distancias, tablas, datos de estaciones ni textos clínicos. Si falta un dato, dejarlo en null o "pendiente" (o marcarlo VERIFICAR) y avisar.
2. **El token de WAQI vive SOLO en el backend** (`.env`): nunca en JavaScript, HTML ni en las respuestas. Los clientes no llaman a la API directamente.
3. Mostrar siempre la hora de la medición y de la última sincronización (hora de Bogotá) y el aviso cuando el dato esté desactualizado.
4. Lenguaje simple, sin tecnicismos para la usuaria e informativo, sin indicaciones médicas.
5. Alcance geográfico: solo Ciudad Bolívar.
6. Todo dato de demostración va rotulado "DATOS SIMULADOS" y nunca se mezcla con los datos reales.
7. Privacidad de la gestante: los datos del perfil (nombre/pseudónimo, edad, FUM/FPP, condiciones, zona, contacto de emergencia) se guardan en la BD del servidor solo para personalizar avisos y alertas; no se publican ni se comparten con terceros.

> Nota: la regla histórica «sin registro ni inicio de sesión» y la exclusión de login/SMS quedaron **derogadas** por el rediseño cliente-servidor solicitado (cuentas de usuario, un admin, SOS con SMS/llamada y contacto de emergencia).

## Calendario 2026
Sprint 2 (zonas y app base) 19-30 oct · Sprint 3 (push y recomendaciones) 3-13 nov · Sprint 4 (privacidad y estabilidad) 17-20 nov · Sprint 5 (piloto y cierre) 24-27 nov.
