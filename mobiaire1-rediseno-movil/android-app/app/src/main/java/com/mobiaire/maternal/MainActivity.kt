package com.mobiaire.maternal

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Looper
import android.os.PowerManager
import android.provider.ContactsContract
import android.provider.Settings
import android.webkit.GeolocationPermissions
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject

class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private lateinit var locationManager: LocationManager
    private val defaultUrl = "http://192.168.20.37:8000"
    private val prefsName = "mobiaire"
    private val urlKey = "server_url"
    private val tokenKey = "token"
    private val canalNotif = "mobiaire_alertas"

    private var pendienteUbicacion = false
    private var pendienteContacto = false
    private var escuchandoUbicacion = false
    private var ultimoEnvio = 0L

    /** Vista pendiente de abrir (viene de una notificación pulsada). */
    private var vistaPendiente: String? = null

    private val listenerUbicacion = object : LocationListener {
        override fun onLocationChanged(location: Location) {
            val ahora = System.currentTimeMillis()
            if (ahora - ultimoEnvio < 3000) return   // no saturar el puente
            ultimoEnvio = ahora
            enviarUbicacionAWeb(location.latitude, location.longitude)
        }
        override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
        override fun onProviderEnabled(provider: String) {}
        override fun onProviderDisabled(provider: String) {}
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WebView.setWebContentsDebuggingEnabled(true)
        setContentView(R.layout.activity_main)
        webView = findViewById(R.id.webView)
        locationManager = getSystemService(Context.LOCATION_SERVICE) as LocationManager

        vistaPendiente = intent?.getStringExtra("vista")

        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.databaseEnabled = true
        settings.javaScriptCanOpenWindowsAutomatically = true
        settings.setGeolocationEnabled(true)
        settings.cacheMode = WebSettings.LOAD_NO_CACHE
        settings.mediaPlaybackRequiresUserGesture = false

        webView.clearCache(true)
        webView.clearHistory()

        // Puente nativo para SOS (llamadas, SMS, contactos), ubicación y notificaciones.
        webView.addJavascriptInterface(PuenteNativo(), "AndroidBridge")

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val uri = request?.url ?: return false
                val urlStr = uri.toString()
                val serverBase = getServerUrl()
                if (urlStr.startsWith(serverBase) || urlStr.startsWith("file:///android_asset")) {
                    return false
                }
                try {
                    val intent = Intent(Intent.ACTION_VIEW, uri)
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    startActivity(intent)
                    return true
                } catch (_: Exception) {
                    return false
                }
            }
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                abrirVistaPendiente()
            }
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onGeolocationPermissionsShowPrompt(origin: String, callback: GeolocationPermissions.Callback) {
                callback.invoke(origin, true, true)
            }
        }

        crearCanalNotificaciones()
        requestPermissions()
        pedirIgnorarOptimizacionBateria()
        val srvUrl = getServerUrl()
        webView.loadUrl(srvUrl)
        iniciarServicioAire()
        iniciarUbicacionAhora()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val v = intent.getStringExtra("vista")
        if (!v.isNullOrBlank()) {
            vistaPendiente = v
            abrirVistaPendiente()
        }
    }

    override fun onResume() {
        super.onResume()
        marcarAppActiva(true)
    }

    override fun onStop() {
        super.onStop()
        marcarAppActiva(false)
    }

    override fun onDestroy() {
        super.onDestroy()
        ReceptorArranque.programarWatchdog(this, 3L)
    }

    private fun pedirIgnorarOptimizacionBateria() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            try {
                val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
                if (!pm.isIgnoringBatteryOptimizations(packageName)) {
                    val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                        data = Uri.parse("package:$packageName")
                    }
                    startActivity(intent)
                }
            } catch (_: Exception) {}
        }
    }

    /** Marca si la app está visible; el servicio no duplica avisos con la app abierta. */
    private fun marcarAppActiva(activa: Boolean) {
        try {
            getSharedPreferences(prefsName, Context.MODE_PRIVATE).edit()
                .putBoolean("app_activa", activa).apply()
        } catch (_: Exception) {
        }
    }

    /** Abre la pestaña pedida por una notificación, si la web ya está lista. */
    private fun abrirVistaPendiente() {
        val v = vistaPendiente ?: return
        webView.post {
            try {
                webView.evaluateJavascript(
                    "window.mobiaireAbrirVista && window.mobiaireAbrirVista('${v.replace("'", "\\'")}')", null
                )
            } catch (_: Exception) {
            }
        }
        vistaPendiente = null
    }

    private fun getServerUrl(): String {
        val prefs = getSharedPreferences(prefsName, Context.MODE_PRIVATE)
        val guardada = prefs.getString(urlKey, null)
        if (!guardada.isNullOrBlank()) return guardada
        val input = EditText(this)
        input.setText(defaultUrl)
        AlertDialog.Builder(this)
            .setTitle("Servidor MobiAire")
            .setMessage("Ingresa la URL del servidor (PC):")
            .setView(input)
            .setPositiveButton("Guardar") { _, _ ->
                val v = input.text.toString().trim()
                val finalUrl = if (v.isBlank()) defaultUrl else v
                prefs.edit().putString(urlKey, finalUrl).apply()
                webView.loadUrl(finalUrl)
                iniciarServicioAire()
            }
            .setNegativeButton("Usar por defecto") { _, _ ->
                prefs.edit().putString(urlKey, defaultUrl).apply()
                webView.loadUrl(defaultUrl)
                iniciarServicioAire()
            }
            .setCancelable(false)
            .show()
        return defaultUrl
    }

    private fun crearCanalNotificaciones() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val canal = NotificationChannel(canalNotif, "Alertas de aire", NotificationManager.IMPORTANCE_HIGH)
            canal.description = "Alertas personalizadas de calidad del aire"
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            nm.createNotificationChannel(canal)
        }
    }

    private fun requestPermissions() {
        val perms = mutableListOf(
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION,
            Manifest.permission.READ_CONTACTS
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            perms.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        val toRequest = perms.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (toRequest.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, toRequest.toTypedArray(), 1001)
        }
    }

    private fun tienePermiso(permiso: String): Boolean =
        ContextCompat.checkSelfPermission(this, permiso) == PackageManager.PERMISSION_GRANTED

    private fun enviarUbicacionAWeb(lat: Double, lon: Double) {
        webView.post {
            try {
                webView.evaluateJavascript(
                    "window.mobiaireUbicacion && window.mobiaireUbicacion($lat,$lon)", null
                )
            } catch (_: Exception) {
            }
        }
    }

    private fun enviarErrorUbicacion(msg: String) {
        webView.post {
            try {
                webView.evaluateJavascript(
                    "window.mobiaireUbicacionError && window.mobiaireUbicacionError('$msg')", null
                )
            } catch (_: Exception) {
            }
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 1001) {
            if (tienePermiso(Manifest.permission.ACCESS_FINE_LOCATION) ||
                tienePermiso(Manifest.permission.ACCESS_COARSE_LOCATION)) {
                pendienteUbicacion = false
                pedirUbicacionAhora()
                iniciarUbicacionAhora()
                iniciarServicioAire()
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q &&
                    !tienePermiso(Manifest.permission.ACCESS_BACKGROUND_LOCATION)) {
                    try {
                        ActivityCompat.requestPermissions(
                            this, arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION), 1002
                        )
                    } catch (_: Exception) {}
                }
            } else if (pendienteUbicacion) {
                pendienteUbicacion = false
                enviarErrorUbicacion("permiso denegado")
            }
            if (pendienteContacto && tienePermiso(Manifest.permission.READ_CONTACTS)) {
                pendienteContacto = false
                abrirSelectorContacto()
            } else if (pendienteContacto) {
                pendienteContacto = false
                webView.post {
                    try { webView.evaluateJavascript("window.mobiaireContacto && window.mobiaireContacto('')", null) } catch (_: Exception) {}
                }
            }
        } else if (requestCode == 1002) {
            iniciarServicioAire()
        }
    }

    /** Puente que expone funciones nativas al JavaScript de la app. */
    inner class PuenteNativo {
        @JavascriptInterface
        fun llamar(numero: String) {
            try {
                val intent = Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(numero)))
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(intent)
            } catch (_: Exception) {
            }
        }

        /**
         * Abre la app de mensajes con el texto y la ubicación listos para enviar.
         * Se usa ACTION_SENDTO (smsto:) para no requerir el permiso SEND_SMS y
         * que el usuario vea y confirme el envío.
         */
        @JavascriptInterface
        fun enviarSMS(numero: String, mensaje: String) {
            try {
                val intent = Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + Uri.encode(numero)))
                intent.putExtra("sms_body", mensaje)
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(intent)
            } catch (_: Exception) {
                try {
                    val respaldo = Intent(Intent.ACTION_VIEW, Uri.parse("sms:" + Uri.encode(numero)))
                    respaldo.putExtra("sms_body", mensaje)
                    respaldo.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    startActivity(respaldo)
                } catch (_: Exception) {
                }
            }
        }

        /** Comparte texto (p. ej. la ubicación de un centro de salud) con otras apps. */
        @JavascriptInterface
        fun compartir(titulo: String, texto: String) {
            try {
                val intent = Intent(Intent.ACTION_SEND).apply {
                    type = "text/plain"
                    putExtra(Intent.EXTRA_SUBJECT, titulo)
                    putExtra(Intent.EXTRA_TEXT, texto)
                }
                val chooser = Intent.createChooser(intent, titulo)
                chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(chooser)
            } catch (_: Exception) {
            }
        }

        @JavascriptInterface
        fun notificar(titulo: String, mensaje: String, tipo: String) {
            mostrarNotificacion(titulo, mensaje, tipo)
        }

        @JavascriptInterface
        fun pedirNotificaciones() {
            runOnUiThread { requestPermissions() }
        }

        /** Abre la app nativa de mapas (Google Maps, Waze) para navegar al centro de salud. */
        @JavascriptInterface
        fun abrirMapa(lat: Double, lon: Double, nombre: String) {
            runOnUiThread {
                try {
                    val uriGeo = Uri.parse("geo:$lat,$lon?q=$lat,$lon(" + Uri.encode(nombre) + ")")
                    val mapIntent = Intent(Intent.ACTION_VIEW, uriGeo)
                    mapIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    if (mapIntent.resolveActivity(packageManager) != null) {
                        startActivity(mapIntent)
                        return@runOnUiThread
                    }
                } catch (_: Exception) {}
                try {
                    val uriWeb = Uri.parse("https://www.google.com/maps/dir/?api=1&destination=$lat,$lon")
                    val webIntent = Intent(Intent.ACTION_VIEW, uriWeb)
                    webIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    startActivity(webIntent)
                } catch (_: Exception) {}
            }
        }

        /** Guarda el token y la URL del servidor para que el servicio en segundo plano los use. */
        @JavascriptInterface
        fun guardarToken(token: String, baseUrl: String) {
            try {
                val prefs = getSharedPreferences(prefsName, Context.MODE_PRIVATE)
                val tokenAnt = prefs.getString(tokenKey, "")
                val editor = prefs.edit()
                    .putString(tokenKey, token)
                    .putString(urlKey, baseUrl)
                if (tokenAnt != token) {
                    editor.putInt("last_notif", 0)
                    editor.putString("last_uid", "")
                    editor.putString("last_level", "")
                }
                editor.apply()
                iniciarServicioAire(token, baseUrl)
            } catch (_: Exception) {
            }
        }

        @JavascriptInterface
        fun iniciarServicio() {
            runOnUiThread { iniciarServicioAire() }
        }

        @JavascriptInterface
        fun detenerServicio() {
            runOnUiThread {
                try { stopService(Intent(this@MainActivity, ServicioAire::class.java)) } catch (_: Exception) {}
            }
        }

        /** Última ubicación conocida como JSON {lat,lon}; "" si no hay. */
        @JavascriptInterface
        fun obtenerUbicacion(): String {
            if (!tienePermiso(Manifest.permission.ACCESS_FINE_LOCATION) &&
                !tienePermiso(Manifest.permission.ACCESS_COARSE_LOCATION)) {
                return ""
            }
            try {
                val proveedores = listOf(
                    LocationManager.GPS_PROVIDER,
                    LocationManager.NETWORK_PROVIDER,
                    LocationManager.PASSIVE_PROVIDER
                )
                var mejor: Location? = null
                for (p in proveedores) {
                    val loc = try { locationManager.getLastKnownLocation(p) } catch (_: Exception) { null }
                    if (loc != null && (mejor == null || loc.time > mejor!!.time)) mejor = loc
                }
                if (mejor != null) {
                    val o = JSONObject()
                    o.put("lat", mejor!!.latitude)
                    o.put("lon", mejor!!.longitude)
                    return o.toString()
                }
            } catch (_: Exception) {
            }
            return ""
        }

        /** Pide UNA ubicación fresca; responde por window.mobiaireUbicacion(lat,lon). */
        @JavascriptInterface
        fun pedirUbicacion() {
            runOnUiThread { pedirUbicacionAhora() }
        }

        /** Empieza a enviar la ubicación en tiempo real (solo con la app abierta). */
        @JavascriptInterface
        fun iniciarUbicacion() {
            runOnUiThread { iniciarUbicacionAhora() }
        }

        @JavascriptInterface
        fun detenerUbicacion() {
            runOnUiThread {
                try { locationManager.removeUpdates(listenerUbicacion) } catch (_: Exception) {}
                escuchandoUbicacion = false
            }
        }

        /** Abre el selector de contactos; responde por window.mobiaireContacto(json). */
        @JavascriptInterface
        fun elegirContacto() {
            runOnUiThread {
                if (!tienePermiso(Manifest.permission.READ_CONTACTS)) {
                    pendienteContacto = true
                    requestPermissions()
                    return@runOnUiThread
                }
                abrirSelectorContacto()
            }
        }
    }

    private fun iniciarServicioAire(token: String? = null, baseUrl: String? = null) {
        try {
            val prefs = getSharedPreferences(prefsName, Context.MODE_PRIVATE)
            val base = baseUrl ?: (prefs.getString(urlKey, "") ?: "")
            val tok = token ?: (prefs.getString(tokenKey, "") ?: "")
            if (base.isBlank()) return
            val intent = Intent(this, ServicioAire::class.java).apply {
                action = "ACTUALIZAR_CONFIG"
                putExtra("server_url", base)
                putExtra("token", tok)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(intent)
            } else {
                startService(intent)
            }
            ReceptorArranque.programarWatchdog(this)
        } catch (_: Exception) {
        }
    }

    private fun pedirUbicacionAhora() {
        if (!tienePermiso(Manifest.permission.ACCESS_FINE_LOCATION) &&
            !tienePermiso(Manifest.permission.ACCESS_COARSE_LOCATION)) {
            pendienteUbicacion = true
            requestPermissions()
            return
        }
        try {
            val ultima = locationManager.getLastKnownLocation(LocationManager.GPS_PROVIDER)
                ?: locationManager.getLastKnownLocation(LocationManager.NETWORK_PROVIDER)
            if (ultima != null) {
                enviarUbicacionAWeb(ultima.latitude, ultima.longitude)
                return
            }
            val proveedor = if (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER))
                LocationManager.GPS_PROVIDER else LocationManager.NETWORK_PROVIDER
            locationManager.requestSingleUpdate(proveedor, listenerUbicacion, Looper.getMainLooper())
        } catch (e: SecurityException) {
            enviarErrorUbicacion("permiso denegado")
        } catch (_: Exception) {
            enviarErrorUbicacion("sin ubicación")
        }
    }

    private fun iniciarUbicacionAhora() {
        if (!tienePermiso(Manifest.permission.ACCESS_FINE_LOCATION) &&
            !tienePermiso(Manifest.permission.ACCESS_COARSE_LOCATION)) {
            pendienteUbicacion = true
            requestPermissions()
            return
        }
        if (escuchandoUbicacion) return
        try {
            val proveedor = if (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER))
                LocationManager.GPS_PROVIDER else LocationManager.NETWORK_PROVIDER
            locationManager.requestLocationUpdates(proveedor, 10000L, 15f, listenerUbicacion, Looper.getMainLooper())
            escuchandoUbicacion = true
        } catch (_: Exception) {
        }
    }

    private fun abrirSelectorContacto() {
        try {
            val intent = Intent(Intent.ACTION_PICK, ContactsContract.CommonDataKinds.Phone.CONTENT_URI)
            startActivityForResult(intent, 2001)
        } catch (_: Exception) {
            webView.post {
                try { webView.evaluateJavascript("window.mobiaireContacto && window.mobiaireContacto('')", null) } catch (_: Exception) {}
            }
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != 2001) return
        var salida = ""
        if (resultCode == RESULT_OK && data?.data != null) {
            try {
                val proyeccion = arrayOf(
                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                    ContactsContract.CommonDataKinds.Phone.NUMBER
                )
                contentResolver.query(data.data!!, proyeccion, null, null, null)?.use { c ->
                    if (c.moveToFirst()) {
                        val o = JSONObject()
                        o.put("name", c.getString(0) ?: "Contacto")
                        o.put("phone", c.getString(1) ?: "")
                        salida = o.toString()
                    }
                }
            } catch (_: Exception) {
            }
        }
        val js = if (salida.isEmpty()) "" else salida.replace("'", "\\'")
        webView.post {
            try { webView.evaluateJavascript("window.mobiaireContacto && window.mobiaireContacto('$js')", null) } catch (_: Exception) {}
        }
    }

    private fun intentApp(vista: String?): PendingIntent {
        val intent = Intent(this, MainActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            if (!vista.isNullOrBlank()) putExtra("vista", vista)
        }
        var flags = PendingIntent.FLAG_UPDATE_CURRENT
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) flags = flags or PendingIntent.FLAG_IMMUTABLE
        return PendingIntent.getActivity(this, (vista ?: "app").hashCode(), intent, flags)
    }

    private fun mostrarNotificacion(titulo: String, mensaje: String, tipo: String) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
                requestPermissions()
                return
            }
            // El reporte diario lleva a la pestaña de indicaciones al pulsar.
            val vista = if (tipo == "reporte_diario") "indicaciones" else null
            val builder = NotificationCompat.Builder(this, canalNotif)
                .setSmallIcon(R.drawable.ic_stat_mobiaire)
                .setContentTitle(titulo.ifBlank { "MobiAire Materno" })
                .setContentText(mensaje)
                .setStyle(NotificationCompat.BigTextStyle().bigText(mensaje))
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setContentIntent(intentApp(vista))
                .setAutoCancel(true)
            // Mantener solo 2 notificaciones activas:
            // 8001: Semáforo actual (reemplaza cualquier semáforo anterior sin acumular)
            // 8002: Recomendaciones u avisos del sistema
            val esSemaforo = tipo == "alerta" || tipo == "semaforo" ||
                titulo.contains("Alerta", ignoreCase = true) ||
                titulo.contains("estación", ignoreCase = true) ||
                titulo.contains("calidad del aire", ignoreCase = true)
            val idFinal = if (esSemaforo) 8001 else 8002
            NotificationManagerCompat.from(this).notify(idFinal, builder.build())
        } catch (_: Exception) {
        }
    }

    @Deprecated("Compatibilidad con versiones antiguas de Android.")
    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }
}
