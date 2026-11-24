# MobiAire Materno — cliente-servidor

Semáforo **informativo** de calidad del aire para gestantes de Ciudad Bolívar
(Bogotá). Trabajo académico de Análisis de Sistemas (Universidad Distrital).
**No es un diagnóstico médico.**

Este repositorio pasó de ser una aplicación web/PWA a un sistema
**cliente-servidor**:

- **Servidor** = este PC (`web/server.py`). Es el **conmutador**: el único que
  consulta la API (WAQI) con el token, guarda todo en su base de datos SQLite y
  la actualiza de forma programada. También gestiona usuarios, sesiones,
  notificaciones, modo prueba y logs.
- **Clientes** = teléfonos (**APK Android** e **iOS**). Nunca llaman a la API:
  piden los datos ya guardados al servidor.

```
┌────────────┐    HTTP     ┌──────────────────────────────┐   HTTPS   ┌────────┐
│ APK Android│ ─────────▶  │  Servidor (PC) — CONMUTADOR  │ ────────▶ │  WAQI  │
│ app iOS    │ ◀─────────  │  SQLite: usuarios, estaciones│ ◀──────── │ (API)  │
└────────────┘             │  notificaciones, modo prueba │           └────────┘
                           └──────────────────────────────┘
```

## Requisitos implementados

| # | Requisito | Dónde |
|---|-----------|-------|
| 1 | Quitar web-app/PWA; clientes móviles + servidor | `web/server.py`, `android-app/`, `ios-app/` |
| 2 | El servidor consulta la API y guarda en la BD (conmutador) | `ingesta/ingesta_waqi.py`, tabla `waqi_stations` |
| 3 | La BD se auto-actualiza (ingesta programada) | `ingesta_waqi.programar()` (defecto 15 min) |
| 4 | Usuarios con contraseña cifrada (PBKDF2) en la BD | `db.py` (`hash_password`) |
| 5 | Dos roles; **un solo admin** (`admin` / `Analissdesistemas`) | `db._ensure_admin`, `server._auth_*` |
| 6 | Una sesión activa por usuario (cerrar la anterior y avisar) | `db.create_session`, `sesion_previa_cerrada` |
| 7 | Registro gestante: nombre, edad, FUM/FPP, primer embarazo, condiciones, hora de reportes, alertas | `web/app-movil.js` (`renderPerfil`), `web/recomendaciones.py` |
| 8 | Admin: actualizar API, modo prueba subir/bajar contaminación (por estación o global), notificar por ubicación/nivel, volver a normal | `server._admin_*`, `db.set_test_mode`, `web/notificaciones.py` |
| 9 | Avisos personalizados por semanas (1-4, 5-12, 13-27, 28-40), condiciones y nivel, con comentarios | `web/recomendaciones.py` |
| 10 | Contaminantes adicionales NO₂, SO₂, O₃ y CO; si faltan, avisar y descartar | `web/waqi.py`, `server._contaminantes` |
| 11 | Botón flotante rojo SOS: hospitales cercanos (llamar), avisar contacto por SMS/llamada con ubicación, 123 opcional; permisos | `web/app-movil.js` (`abrirSOS`), `MainActivity.kt` |
| 12 | Ubicación en tiempo real automática, estación más cercana, tema por nivel con animación | `app-movil.js` (`iniciarUbicacionAutomatica`, `aplicarTema`) |
| 13 | Zonas por estación con polígonos verde→naranja→rojo (transparencia) y estaciones en el mapa | `app-movil.js` (`dibujarZonasMapa`) |
| 14 | Colores verde→naranja→rojo en UI y notificaciones; demo rotulado y sin mezclar | `app-movil.js`, `server._estaciones` |
| 15 | Sección "Info": implicaciones por semana, glosario, contaminantes | `app-movil.js` (`pintarInfo`) |
| 16 | Notificaciones: reporte diario a la hora preferida, alertas en tiempo real y personalizadas | `web/notificaciones.py`, `ingesta_waqi.programar_reportes` |
| 17 | APK Android + app iOS; permisos al abrir; pantalla de login con "Explorar app"; sin botón "Instalar" | `android-app/`, `ios-app/` |
| 18 | Log del servidor y log del cliente | `server._append_log`, `POST /api/log` |

## Ronda 2 (mejoras de interfaz y usabilidad)

| Mejora | Dónde |
|--------|-------|
| El perfil ya **no bloquea** la app: es un modal ("Mi perfil") y, tras iniciar sesión, se sigue viendo Inicio/Mapa/Indicaciones | `web/prototipo/index.html`, `app-movil.js` (`abrirPerfil`) |
| **Permisos**: puente nativo de ubicación y contactos (reintenta tras conceder el permiso) | `MainActivity.kt` (`pedirUbicacionAhora`, `elegirContacto`) |
| **Mapa** con zonas "radar" alrededor de cada estación (no solo Bogotá); sin dato = gris, pero igual se avisa por la estación más cercana | `server._zonas_mapa`, `app-movil.js` (`dibujarZonasMapa`) |
| **Modo admin** solo con variables globales para probar alertas (sin datos de gestante) | `app-movil.js` (`renderAdmin`) |
| **Alertas cortas** con icono circular de color y botón "Ver indicaciones" | `recomendaciones.resumen_alerta`, `app-movil.js` (`renderNotif`) |
| **Logo** nuevo (gestante + hoja) para web y Android | `scripts/generar_iconos.py`, `web/iconos/icono.svg` |

## Ronda 3 (emergencias, segundo plano, notificaciones y contenido)

| Mejora | Dónde |
|--------|-------|
| **SOS: hospitales cercanos** consultados por el **servidor** (Overpass/OSM) con **caché en la BD** (`lugares_salud`, 24 h) para búsquedas instantáneas | `web/salud.py`, `db.upsert_lugares_salud`, `GET /api/salud/cercanos` |
| **Detalle de cada centro** en una subinterfaz: distancia, mapa con el centro y tu ubicación, **Cómo llegar**, **Compartir ubicación**, teléfono y botón **Llamar** | `app-movil.js` (`abrirDetalleHospital`), `MainActivity.compartir` |
| **Contacto de emergencia guardado en el perfil** (se usa sin buscarlo durante la emergencia) | `app-movil.js` (`avisarContacto`), `PUT /api/users/me/profile` |
| **SMS con ubicación** arreglado: abre la app de mensajes con texto y ubicación (`smsto:`, sin permiso `SEND_SMS`) | `MainActivity.enviarSMS` |
| Se **quitó** "Mostrar mi ubicación en tiempo real" del SOS (innecesario) | `app-movil.js` (`abrirSOS`) |
| Las **casillas de contaminantes** también cambian de color según el semáforo (no solo la línea) | `web/prototipo/index.html`, `app-movil.js` (`aplicarTema`) |
| Recomendación de Inicio **estructurada** (tablas, viñetas, bloques) en vez de texto plano | `app-movil.js` (`pintarRecoSimple`, `pintarRecoPersonalizada`) |
| **Mapa sincronizado al instante**: relee y repinta al entrar a la pestaña, tras el modo prueba y cada 60 s (sin reiniciar la app) | `app-movil.js` (`refrescarMapa`, `MAPA_TIMER`) |
| **Indicaciones** e **Info** con tablas e iconos; al tocar una semana/glosario/contaminante se abre una **subinterfaz** con el detalle (riesgos, PM bueno/malo, causas, prevención) | `app-movil.js` (`abrirDetalle*`), `web/recomendaciones.json` |
| **Guía**: manual de uso (iniciar sesión, modificar datos, pedir ayuda) + tabla de colores | `web/recomendaciones.json` (`manual`), `app-movil.js` (`pintarGuia`) |
| **"Acerca de"** ampliado: APIs, programas, servicios y cómo se generan los mapas | `app-movil.js` (`pintarInfo`) |
| **Servicio en segundo plano** (Android): extrae la ubicación y actualiza cada **5 min**; avisa **solo** si la estación más cercana cambia y el aire está **naranja/rojo** | `ServicioAire.kt`, `AndroidManifest.xml` |
| Si falla la ubicación: **hasta 10 reintentos** y luego avisa «📍 Ubicación desconocida: ingresa a la app y actualízala» | `ServicioAire.kt` (`obtenerUbicacion`) |
| **Notificaciones** con el **símbolo de la app** (`ic_stat_mobiaire`), **emoji de color** (🟢🟠🔴) y que **abren la app** (el reporte diario, en **Indicaciones**) | `ServicioAire.kt`, `MainActivity.mostrarNotificacion`, `web/notificaciones.py` |
| Reporte diario **corto**: «🟠 Recomendaciones para hoy» | `web/notificaciones.py` |
| **Icono de la app** corregido: se copia el mismo logo de la cabecera (antes se veía deformado) | `scripts/generar_iconos.py` |

## Puesta en marcha (servidor)

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env                                # y pegue WAQI_TOKEN
python -m web.server                                  # http://0.0.0.0:8000 + ingesta programada
```

Opciones:

```bash
python -m web.server --port 8080          # otro puerto
python -m web.server --sin-ingesta        # no programa la ingesta automática
python -m web.server --ingesta-min 20     # intervalo de ingesta (min)
python -m web.server --demo-refrescar     # regenera resultados_demo.json (sin internet)
```

El servidor escucha en `0.0.0.0` para que el celular se conecte por la red local.
El **token de WAQI vive solo en el backend** (`.env`) y nunca se envía al cliente.

## API principal

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/salud` | Estado del conmutador (rol, estaciones en BD, última ingesta) |
| GET | `/api/waqi/estaciones` | Estaciones guardadas (con modo prueba aplicado) |
| GET | `/api/waqi/estacion/<uid>` | Detalle de una estación |
| GET | `/api/waqi/ciudad-bolivar` | Estación de Ciudad Bolívar |
| GET | `/api/contaminantes` | Qué contaminantes reporta la API y cuáles no |
| GET | `/api/zonas`, `/api/zonas/asociacion`, `/api/zona/<id>` | Zonas UPZ y su semáforo |
| GET | `/api/upz-geojson` | Polígonos de las UPZ (mapa) |
| GET | `/api/upz-ciudad-bolivar` | Solo las UPZ de Ciudad Bolívar (el geojson completo pesa ~11 MB) |
| GET | `/api/zonas-mapa` | Zonas tipo "radar" alrededor de cada estación, coloreadas por nivel (gris si no hay dato) |
| GET | `/api/salud/cercanos?lat=..&lon=..&radio=..` | Hospitales/centros de salud cercanos (Overpass/OSM) con caché en la BD; el servidor es el único que consulta Overpass |
| POST | `/api/auth/register`, `/api/auth/login`, `/api/auth/logout` | Usuarios y sesiones |
| GET | `/api/auth/me` | Usuario actual (token Bearer) |
| PUT | `/api/users/me/profile` | Guardar el perfil de la gestante |
| POST | `/api/users/me/ubicacion` | Guardar la última ubicación conocida (para avisar por la estación más cercana) |
| GET | `/api/notifications`, POST `/api/notifications/leer` | Alertas y reportes |
| GET | `/api/perfil/recomendacion` | Aviso personalizado (lo calcula el servidor) |
| GET | `/api/perfil/indicaciones` | Vista "Ver indicaciones": secciones por semanas, condiciones, contaminantes y nivel, con su color |
| POST | `/api/log` | Log del cliente |
| GET/POST | `/api/admin/test-mode` | Modo prueba (solo admin) |
| POST | `/api/admin/refresh`, GET `/api/admin/refresh/status` | Actualizar datos de la API |
| GET | `/api/admin/ingesta/estado`, `/api/admin/logs` | Estado de ingesta y logs (solo admin) |

## Usuarios y roles

- **Usuario (gestante):** se registra desde la app (usuario + contraseña ≥ 4).
- **Administrador:** existe **uno solo**, creado automáticamente:
  - usuario `admin`, contraseña `Analissdesistemas`.
  - No se pueden crear ni promover más administradores.
- **Una sesión por usuario:** al iniciar sesión en otro dispositivo se cierra la
  anterior y el cliente avisa («Tu sesión se cerró porque se inició sesión en
  otro dispositivo»).

## Modo prueba (administrador)

Desde el panel admin de la app:

- **Actualizar datos API**: lanza una ingesta inmediata.
- **Modo prueba**: sube o baja la contaminación con un `factor` (multiplica el
  ICA y los sub-índices) y/o forzando el `nivel` (verde/naranja/rojo), de forma
  **global** o **por estación**. Al activarlo, se generan alertas a las usuarias
  según su zona y nivel.
- **Volver a normal**: restaura los datos reales.

## Notificaciones

- **Reporte diario** a la hora preferida de cada gestante, con título corto
  **«🟠 Recomendaciones para hoy»**; al pulsarlo, la app abre en **Indicaciones**.
- **Alertas en tiempo real** (naranja/rojo) si la usuaria las permitió, con
  título **«🔴 Alerta rojo»** (emoji según el nivel).
- **Cortas**: una sola frase («Estás expuesta a contaminación atmosférica»)
  con un icono circular del color del nivel y un botón **"Ver indicaciones"**
  que abre la app en la vista con todas las recomendaciones según el perfil.
- **Con el símbolo de la app** (`ic_stat_mobiaire`) y color por **emoji**
  (🟢🟠🔴 / ⚪); al pulsarlas abren la aplicación.
- **Personalizadas** por perfil (semanas, condiciones) y por ubicación: si la
  usuaria no eligió zona, el servidor usa la estación **más cercana** a su
  última ubicación conocida (que el cliente envía al servidor).
- En Android se muestran como notificación del sistema; en el navegador, con la
  API `Notification`.

## Funcionamiento en segundo plano (Android)

Las gestantes no tienen la app abierta todo el tiempo, por eso `ServicioAire`
es un **servicio en primer plano** (con notificación persistente discreta) que
mantiene la vigilancia activa aunque el teléfono esté bloqueado:

1. Cada **5 minutos** extrae la ubicación actual (con **hasta 10 reintentos**).
2. La envía al servidor (`POST /api/users/me/ubicacion`).
3. Mira la **estación más cercana**: si **cambió a otra** y el aire está en
   **naranja/rojo**, avisa; si sigue **verde**, no notifica nada.
4. Revisa las notificaciones nuevas del servidor y las muestra con el ícono de
   la app (el reporte diario abre la app en **Indicaciones**).
5. Si tras los reintentos no logra ubicación, avisa con un mensaje corto:
   **«📍 Ubicación desconocida: ingresa a la app y actualízala»**.

El servicio solo se inicia cuando hay sesión: el cliente comparte el token y la
URL del servidor con el puente nativo (`guardarToken`) y arranca/detiene el
servicio al iniciar/cerrar sesión.

## Icono de la app

El logo (una gestante con su vientre coral y una hoja de aire limpio) se genera
sin dependencias externas y se reparte a la web y a Android:

```bash
python -m scripts.generar_iconos
# web/iconos/icono-192.png, icono-512.png
# android-app/app/src/main/res/mipmap-*/ic_launcher.png (+ ic_launcher_round.png)
# android-app/app/src/main/res/drawable-*/ic_stat_mobiaire.png   (icono de notificación)
```

El generador **copia el mismo logo de la cabecera** de la app (la gestante con
su vientre coral y la hoja), de modo que el icono del sistema coincide con el
logo que se ve arriba a la izquierda dentro de la aplicación. La fuente
vectorial es `web/iconos/icono.svg`.

## Compilar el APK (Android)

Requisitos: **JDK 17+**, **Android SDK** (build-tools + platform 34) y **Gradle 8.7**.

```powershell
cd android-app
$env:JAVA_HOME    = "C:\Program Files\Java\jdk-23"
$env:ANDROID_HOME = "C:\Android\Sdk"
& "C:\Gradle\gradle-8.7\bin\gradle.bat" assembleDebug
# APK -> android-app\app\build\outputs\apk\debug\app-debug.apk
```

El APK ya compilado está en la raíz del proyecto: **`MobiAireMaterno.apk`**.
Instálelo en el celular (activando "orígenes desconocidos") o con `adb install`.

Al abrir, la app pide la **URL del servidor** (la IP del PC, p. ej.
`http://192.168.20.37:8000`) y luego pide los permisos de **ubicación**,
**contactos** y **notificaciones**. Muestra la **pantalla de login** con la
opción **"Explorar app"**.

Las llamadas y los SMS **no** piden permisos especiales: se abren con las apps
nativas del teléfono (`tel:` para llamar y `smsto:` para el SMS con la ubicación
ya escrita), de modo que el usuario siempre ve y confirma la acción.

## App iOS

`ios-app/` contiene el cliente iOS (`WKWebView`) y las instrucciones. **iOS solo
se compila en macOS con Xcode** (Apple no permite compilar `.ipa` en Windows).
Ver `ios-app/README.md`.

## Pruebas

```bash
python -m pytest -q        # 135 pruebas (motor ICA, ingesta, zonas, recomendaciones, BD y servidor HTTP)
```

Las pruebas del servidor levantan un `ThreadingHTTPServer` real en un puerto
efímero sobre una **base de datos temporal**, y validan los endpoints por HTTP
sin tocar la red ni el token. Así se valida el backend sin instalar la app.

## Fuente de datos

- **WAQI (AQICN)** en tiempo real: el servidor trae las estaciones de Bogotá
  (y la de Ciudad Bolívar `A522148`) y calcula el **ICA estimado** (Res. 2254)
  a partir de los sub-índices US-EPA. El índice WAQI original nunca se muestra
  como si fuera el ICA: se rotula «índice WAQI (escala US-EPA)».
- Un dato es **reciente** si tiene menos de 6 horas. Sin dato reciente no hay
  semáforo, ni recomendación, ni alerta (se muestra gris «Sin dato reciente»).
- El dataset histórico IDEAM (`g4t8-zkc3`, hasta dic-2024) se conserva para
  validar el motor y como modo demo, siempre rotulado «DATOS SIMULADOS».

## Límites conocidos

- El **ICA es estimado y horario** (derivado del sub-índice WAQI), no el
  promedio de 24 h de la Resolución 2254.
- La estación más cercana al centroide de una UPZ **no equivale** a la calidad
  del aire de toda la zona.
- La app iOS no se puede compilar en Windows (requiere macOS + Xcode).
- En iOS, el permiso de contactos y las notificaciones del sistema dependen de
  las APIs del `WKWebView`; el SMS y la llamada se abren con las apps nativas
  (`sms:` / `tel:`).
