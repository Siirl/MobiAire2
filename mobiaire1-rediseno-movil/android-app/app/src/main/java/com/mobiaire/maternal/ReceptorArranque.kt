package com.mobiaire.maternal

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.SystemClock

/**
 * Arranca y supervisa automáticamente el servicio en primer plano cuando el teléfono
 * se reinicia, se desbloquea, se actualiza o mediante la alarma de vigilancia periódica.
 */
class ReceptorArranque : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        try {
            val prefs = context.getSharedPreferences("mobiaire", Context.MODE_PRIVATE)
            val base = prefs.getString("server_url", "") ?: ""
            val token = prefs.getString("token", "") ?: ""
            if (base.isNotBlank()) {
                val svc = Intent(context, ServicioAire::class.java).apply {
                    action = "ACTUALIZAR_CONFIG"
                    putExtra("server_url", base)
                    putExtra("token", token)
                }
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    context.startForegroundService(svc)
                } else {
                    context.startService(svc)
                }
            }

            // Reprogramar el watchdog para asegurar vigilancia continua
            programarWatchdog(context)
        } catch (_: Exception) {
        }
    }

    companion object {
        fun programarWatchdog(context: Context, retardoSegundos: Long = 600L) {
            try {
                val am = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
                val intent = Intent(context, ReceptorArranque::class.java).apply {
                    action = "com.mobiaire.maternal.REINICIAR_SERVICIO"
                    setPackage(context.packageName)
                }
                var flags = PendingIntent.FLAG_UPDATE_CURRENT
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) flags = flags or PendingIntent.FLAG_IMMUTABLE
                val pi = PendingIntent.getBroadcast(context, 7005, intent, flags)
                val disparo = SystemClock.elapsedRealtime() + (retardoSegundos * 1000L)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, disparo, pi)
                } else {
                    am.set(AlarmManager.ELAPSED_REALTIME_WAKEUP, disparo, pi)
                }
            } catch (_: Exception) {
            }
        }
    }
}
