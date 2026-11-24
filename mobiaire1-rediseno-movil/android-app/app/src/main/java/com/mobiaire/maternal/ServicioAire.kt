package com.mobiaire.maternal

import android.Manifest
import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Servicio en primer plano permanente (proceso :vigilancia): mantiene la vigilancia
 * activa e ininterrumpida aunque la app esté cerrada o el teléfono bloqueado.
 *
 *   - Revisa notificaciones y cambios de calidad del aire del servidor cada 15 s
 *     para que las alertas forzadas por el administrador o de WAQI lleguen de inmediato.
 *   - Captura la ubicación periódicamente y la sincroniza con el servidor.
 *   - Si el sistema elimina la tarea visual (swipe), el servicio permanece vivo
 *     y se supervisa mediante ReceptorArranque + AlarmManager.
 */
class ServicioAire : Service() {

    companion object {
        const val CANAL_SERVICIO = "mobiaire_servicio"
        const val CANAL_ALERTAS = "mobiaire_alertas"
        const val ID_SERVICIO = 7001
        const val ID_NOTIF_SEMAFORO = 8001           // Notificación única del semáforo actual
        const val ID_NOTIF_RECOMENDACIONES = 8002    // Notificación de recomendaciones / sistema
        const val INTERVALO_ALERTAS_SEG = 15L    // revisión rápida de alertas y estaciones
        const val INTERVALO_UBIC_SEG = 120L      // captura/envío de ubicación cada 2 min
        const val MAX_REINTENTOS = 3
    }

    private lateinit var locationManager: LocationManager
    private val planificador: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor()
    private var escuchando = false
    private var wakeLock: PowerManager.WakeLock? = null
    @Volatile private var ultimaUbicacionViva: Location? = null
    private var ultimoCicloUbicMs = 0L

    @Volatile private var serverUrlActual: String = ""
    @Volatile private var tokenActual: String = ""

    private val listenerContinuo = object : LocationListener {
        override fun onLocationChanged(location: Location) {
            ultimaUbicacionViva = location
        }
        override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
        override fun onProviderEnabled(provider: String) {}
        override fun onProviderDisabled(provider: String) {}
    }

    override fun onCreate() {
        super.onCreate()
        locationManager = getSystemService(Context.LOCATION_SERVICE) as LocationManager
        cargarConfigGuardada()
        crearCanales()
        iniciarForeground()
        adquirirWakeLock()
        iniciarEscuchaContinuaUbicacion()
        ReceptorArranque.programarWatchdog(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        intent?.getStringExtra("server_url")?.let {
            if (it.isNotBlank()) {
                serverUrlActual = it
                try {
                    getSharedPreferences("mobiaire", Context.MODE_PRIVATE).edit().putString("server_url", it).apply()
                } catch (_: Exception) {}
            }
        }
        intent?.getStringExtra("token")?.let {
            if (it.isNotBlank()) {
                val anterior = tokenActual
                tokenActual = it
                try {
                    val prefs = getSharedPreferences("mobiaire", Context.MODE_PRIVATE)
                    val editor = prefs.edit().putString("token", it)
                    if (anterior != it) {
                        editor.putInt("last_notif", 0)
                        editor.putString("last_uid", "")
                        editor.putString("last_level", "")
                    }
                    editor.apply()
                } catch (_: Exception) {}
            }
        }
        if (serverUrlActual.isBlank() || tokenActual.isBlank()) {
            cargarConfigGuardada()
        }

        iniciarForeground()
        iniciarEscuchaContinuaUbicacion()
        if (!escuchando) {
            escuchando = true
            planificador.scheduleWithFixedDelay({ ciclo() }, 2, INTERVALO_ALERTAS_SEG, TimeUnit.SECONDS)
        } else {
            planificador.execute { ciclo() }
        }
        return START_STICKY
    }

    private fun cargarConfigGuardada() {
        try {
            val prefs = getSharedPreferences("mobiaire", Context.MODE_PRIVATE)
            val u = prefs.getString("server_url", "") ?: ""
            val t = prefs.getString("token", "") ?: ""
            if (u.isNotBlank()) serverUrlActual = u
            if (t.isNotBlank()) tokenActual = t
        } catch (_: Exception) {}
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        super.onTaskRemoved(rootIntent)
        // Si el usuario desliza la app en tareas recientes, asegurar que la vigilancia continúe
        try {
            ReceptorArranque.programarWatchdog(applicationContext, 2L)
        } catch (_: Exception) {
        }
    }

    override fun onDestroy() {
        try { locationManager.removeUpdates(listenerContinuo) } catch (_: Exception) {}
        try {
            if (wakeLock?.isHeld == true) wakeLock?.release()
        } catch (_: Exception) {}
        try { planificador.shutdownNow() } catch (_: Exception) {}
        try {
            ReceptorArranque.programarWatchdog(applicationContext, 5L)
        } catch (_: Exception) {}
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun adquirirWakeLock() {
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "MobiAire::VigilanciaWakeLock").apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (_: Exception) {
        }
    }

    private fun iniciarEscuchaContinuaUbicacion() {
        if (!tieneUbicacion()) return
        try {
            if (locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                locationManager.requestLocationUpdates(
                    LocationManager.NETWORK_PROVIDER, 60000L, 20f, listenerContinuo, Looper.getMainLooper()
                )
            }
            if (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                locationManager.requestLocationUpdates(
                    LocationManager.GPS_PROVIDER, 60000L, 20f, listenerContinuo, Looper.getMainLooper()
                )
            }
        } catch (_: Exception) {
        }
    }

    // ------------------------------------------------------------------ canales
    private fun crearCanales() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val servicio = NotificationChannel(
                CANAL_SERVICIO, "Vigilancia del aire",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Mantiene activa la vigilancia continua de calidad del aire y alertas."
                setShowBadge(false)
            }
            nm.createNotificationChannel(servicio)

            val alertas = NotificationChannel(
                CANAL_ALERTAS, "Alertas de aire",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Alertas personalizadas y cambios críticos en la calidad del aire"
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 450, 200, 450)
                setShowBadge(true)
            }
            nm.createNotificationChannel(alertas)
        }
    }

    private fun iniciarForeground() {
        val notif = NotificationCompat.Builder(this, CANAL_SERVICIO)
            .setSmallIcon(R.drawable.ic_stat_mobiaire)
            .setContentTitle("MobiAire Materno activo")
            .setContentText("Vigilancia continua de calidad del aire y alertas.")
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(intentApp(null))
            .build()
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(ID_SERVICIO, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
            } else {
                startForeground(ID_SERVICIO, notif)
            }
        } catch (_: Exception) {
            try { startForeground(ID_SERVICIO, notif) } catch (_: Exception) {}
        }
    }

    // -------------------------------------------------------------------- ciclo
    private fun ciclo() {
        try {
            val prefs = getSharedPreferences("mobiaire", Context.MODE_PRIVATE)
            var base = serverUrlActual
            if (base.isBlank()) {
                base = prefs.getString("server_url", "") ?: ""
                if (base.isNotBlank()) serverUrlActual = base
            }
            var token = tokenActual
            if (token.isBlank()) {
                token = prefs.getString("token", "") ?: ""
                if (token.isNotBlank()) tokenActual = token
            }
            if (base.isBlank()) return

            // 1. Revisar notificaciones del servidor (alertas forzadas por admin o de WAQI)
            if (token.isNotBlank()) {
                revisarNotificaciones(prefs, base, token)
            }

            // 2. Obtener ubicación y evaluar la estación más cercana
            val ahora = System.currentTimeMillis()
            val tocaUbic = (ahora - ultimoCicloUbicMs >= INTERVALO_UBIC_SEG * 1000L) || ultimaUbicacionViva == null
            val loc = if (tocaUbic) {
                ultimoCicloUbicMs = ahora
                obtenerUbicacion()
            } else {
                ultimaUbicacionViva ?: ultimaConocida()
            }

            if (loc != null) {
                prefs.edit()
                    .putFloat("last_lat", loc.latitude.toFloat())
                    .putFloat("last_lon", loc.longitude.toFloat())
                    .apply()
                if (token.isNotBlank() && tocaUbic) {
                    httpPost("$base/api/users/me/ubicacion", token,
                        JSONObject().put("lat", loc.latitude).put("lon", loc.longitude).toString())
                }
                revisarEstacion(prefs, base, token, loc.latitude, loc.longitude)
            } else {
                val guardadaLat = prefs.getFloat("last_lat", Float.NaN)
                val guardadaLon = prefs.getFloat("last_lon", Float.NaN)
                if (!guardadaLat.isNaN() && !guardadaLon.isNaN()) {
                    revisarEstacion(prefs, base, token, guardadaLat.toDouble(), guardadaLon.toDouble())
                }
            }

            // 3. Re-chequear notificaciones por si la sincronización de ubicación generó una nueva
            if (token.isNotBlank()) {
                revisarNotificaciones(prefs, base, token)
            }
        } catch (_: Exception) {
        }
    }

    private fun revisarEstacion(
        prefs: android.content.SharedPreferences, base: String, token: String, lat: Double, lon: Double
    ) {
        val estJson = httpGet("$base/api/waqi/estaciones", token) ?: return
        val estaciones = JSONObject(estJson).optJSONArray("estaciones") ?: return
        var mejorUid = ""
        var mejorNombre = ""
        var mejorNivel = "gris"
        var mejorDist = Double.MAX_VALUE
        for (i in 0 until estaciones.length()) {
            val e = estaciones.optJSONObject(i) ?: continue
            if (!e.optBoolean("reciente", false)) continue
            val la = e.optDouble("lat", Double.NaN)
            val lo = e.optDouble("lon", Double.NaN)
            if (la.isNaN() || lo.isNaN()) continue
            val d = haversine(lat, lon, la, lo)
            if (d < mejorDist) {
                mejorDist = d
                mejorUid = e.optString("uid")
                mejorNombre = e.optString("nombre")
                mejorNivel = e.optJSONObject("semaforo")?.optString("nivel")
                    ?: e.optString("nivel_forzado").ifBlank { "gris" }
            }
        }
        val anteriorUid = prefs.getString("last_uid", "") ?: ""
        val anteriorNivel = prefs.getString("last_level", "") ?: ""
        val cambioEstacion = anteriorUid.isNotBlank() && mejorUid.isNotBlank() && mejorUid != anteriorUid
        val subioAlerta = mejorUid.isNotBlank() && mejorNivel != anteriorNivel &&
            (mejorNivel == "naranja" || mejorNivel == "rojo")

        if (cambioEstacion && (mejorNivel == "naranja" || mejorNivel == "rojo")) {
            notificar("${emoji(mejorNivel)} Cambió tu estación cercana",
                "Ahora la estación más cercana es $mejorNombre y el aire está en nivel $mejorNivel.",
                "alerta")
        } else if (subioAlerta && !prefs.getBoolean("app_activa", false)) {
            notificar("${emoji(mejorNivel)} Alerta $mejorNivel en $mejorNombre",
                if (mejorNivel == "rojo") "Riesgo alto: permanece en interiores y evita esfuerzos al aire libre."
                else "Precaución: zona de sensibilidad materna en $mejorNombre.",
                "alerta")
        }
        prefs.edit().putString("last_uid", mejorUid)
            .putString("last_level", mejorNivel).apply()
    }

    private fun revisarNotificaciones(
        prefs: android.content.SharedPreferences, base: String, token: String
    ) {
        val desde = prefs.getInt("last_notif", 0)
        val json = httpGet("$base/api/notifications?since=$desde", token) ?: return
        val arr = JSONObject(json).optJSONArray("notificaciones") ?: return
        var maxId = desde
        for (i in 0 until arr.length()) {
            val n = arr.optJSONObject(i) ?: continue
            val id = n.optInt("id")
            if (id > maxId) maxId = id
            notificar(n.optString("titulo"), n.optString("mensaje"), n.optString("tipo"), id)
        }
        if (maxId > desde) {
            prefs.edit().putInt("last_notif", maxId).apply()
        }
    }

    // --------------------------------------------------------------- ubicación
    private fun tieneUbicacion(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    private fun obtenerUbicacion(): Location? {
        if (!tieneUbicacion()) return null
        ultimaUbicacionViva?.let {
            if (System.currentTimeMillis() - it.time <= 10 * 60 * 1000L) return it
        }
        for (intento in 1..MAX_REINTENTOS) {
            ultimaConocida()?.let { return it }
            ubicacionFresca()?.let { return it }
            try { Thread.sleep(1500) } catch (_: InterruptedException) { return null }
        }
        return ultimaUbicacionViva
    }

    private fun ultimaConocida(): Location? {
        var mejor: Location? = null
        for (p in listOf(
            LocationManager.GPS_PROVIDER,
            LocationManager.NETWORK_PROVIDER,
            LocationManager.PASSIVE_PROVIDER
        )) {
            val loc = try { locationManager.getLastKnownLocation(p) } catch (_: Exception) { null } ?: continue
            if (System.currentTimeMillis() - loc.time > 30 * 60 * 1000L) continue
            if (mejor == null || loc.time > mejor!!.time) mejor = loc
        }
        return mejor
    }

    private fun ubicacionFresca(): Location? {
        val proveedor = when {
            locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER) -> LocationManager.NETWORK_PROVIDER
            locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER) -> LocationManager.GPS_PROVIDER
            else -> return null
        }
        val latch = CountDownLatch(1)
        var resultado: Location? = null
        val escucha = object : LocationListener {
            override fun onLocationChanged(location: Location) {
                resultado = location
                ultimaUbicacionViva = location
                latch.countDown()
            }
            override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
            override fun onProviderEnabled(provider: String) {}
            override fun onProviderDisabled(provider: String) {}
        }
        return try {
            locationManager.requestSingleUpdate(proveedor, escucha, Looper.getMainLooper())
            latch.await(5, TimeUnit.SECONDS)
            try { locationManager.removeUpdates(escucha) } catch (_: Exception) {}
            resultado
        } catch (_: Exception) {
            null
        }
    }

    // ------------------------------------------------------------------- avisos
    private fun intentApp(vista: String?): PendingIntent {
        val intent = Intent(this, MainActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            if (!vista.isNullOrBlank()) putExtra("vista", vista)
        }
        var flags = PendingIntent.FLAG_UPDATE_CURRENT
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) flags = flags or PendingIntent.FLAG_IMMUTABLE
        return PendingIntent.getActivity(this, (vista ?: "app").hashCode(), intent, flags)
    }

    private fun notificar(titulo: String, mensaje: String, tipo: String, notifId: Int = 0) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
                PackageManager.PERMISSION_GRANTED) return
            val vista = if (tipo == "reporte_diario" || tipo == "alerta") "indicaciones" else null
            val builder = NotificationCompat.Builder(this, CANAL_ALERTAS)
                .setSmallIcon(R.drawable.ic_stat_mobiaire)
                .setContentTitle(titulo.ifBlank { "MobiAire Materno" })
                .setContentText(mensaje)
                .setStyle(NotificationCompat.BigTextStyle().bigText(mensaje))
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setDefaults(NotificationCompat.DEFAULT_ALL)
                .setContentIntent(intentApp(vista))
                .setAutoCancel(true)
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            // Mantener solo 2 notificaciones en la bandeja:
            // 1) Semáforo en el momento actual (se actualiza/reemplaza si cambia el nivel).
            // 2) Recomendaciones o avisos del sistema.
            val esSemaforo = tipo == "alerta" || tipo == "semaforo" ||
                titulo.contains("Alerta", ignoreCase = true) ||
                titulo.contains("estación", ignoreCase = true) ||
                titulo.contains("calidad del aire", ignoreCase = true)
            val idFinal = if (esSemaforo) ID_NOTIF_SEMAFORO else ID_NOTIF_RECOMENDACIONES
            nm.notify(idFinal, builder.build())
        } catch (_: Exception) {
        }
    }

    // --------------------------------------------------------------------- HTTP
    private fun httpGet(urlStr: String, token: String): String? {
        var conn: HttpURLConnection? = null
        return try {
            conn = (URL(urlStr).openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 10000
                readTimeout = 10000
                if (token.isNotBlank()) setRequestProperty("Authorization", "Bearer $token")
            }
            val codigo = conn.responseCode
            if (codigo in 200..299) {
                BufferedReader(conn.inputStream.reader()).use { it.readText() }
            } else null
        } catch (_: Exception) {
            null
        } finally {
            try { conn?.disconnect() } catch (_: Exception) {}
        }
    }

    private fun httpPost(urlStr: String, token: String, cuerpo: String): String? {
        var conn: HttpURLConnection? = null
        return try {
            conn = (URL(urlStr).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 10000
                readTimeout = 10000
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
                if (token.isNotBlank()) setRequestProperty("Authorization", "Bearer $token")
            }
            conn.outputStream.use { it.write(cuerpo.toByteArray(Charsets.UTF_8)) }
            val codigo = conn.responseCode
            if (codigo in 200..299) {
                BufferedReader(conn.inputStream.reader()).use { it.readText() }
            } else null
        } catch (_: Exception) {
            null
        } finally {
            try { conn?.disconnect() } catch (_: Exception) {}
        }
    }

    private fun emoji(nivel: String): String = when (nivel) {
        "verde" -> "🟢"
        "naranja" -> "🟠"
        "rojo" -> "🔴"
        else -> "⚪"
    }

    private fun haversine(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
        val r = Math.PI / 180.0
        val dLat = (lat2 - lat1) * r
        val dLon = (lon2 - lon1) * r
        val a = sin(dLat / 2).let { it * it } +
            cos(lat1 * r) * cos(lat2 * r) * sin(dLon / 2).let { it * it }
        return 2 * 6371.0 * asin(sqrt(a))
    }
}
