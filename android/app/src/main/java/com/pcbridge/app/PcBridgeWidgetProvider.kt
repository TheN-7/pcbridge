package com.pcbridge.app

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.net.HttpURLConnection
import java.net.URL

/**
 * Home-screen widget showing the status of whichever PC the app is
 * currently pointed at (name, online/offline, free storage). It doesn't
 * talk to the WebView at all -- MainActivity mirrors the "active device"
 * into EncryptedSharedPreferences whenever it changes (see
 * MainActivity.WebAppInterface.syncActiveDevice), and this widget reads
 * that plus makes its own small HTTP request to /api/stats.
 *
 * Tap the widget to open the app. Android enforces a minimum background
 * refresh interval of ~30 minutes for widgets (updatePeriodMillis in
 * pcbridge_widget_info.xml); opening the app updates it immediately too.
 */
class PcBridgeWidgetProvider : AppWidgetProvider() {

    override fun onUpdate(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray) {
        appWidgetIds.forEach { id -> updateWidget(context, appWidgetManager, id) }
    }

    private fun readDevice(context: Context): Triple<String, String, String>? {
        val prefs = try {
            val masterKey = MasterKey.Builder(context)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            EncryptedSharedPreferences.create(
                context,
                "pcbridge_widget",
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
            )
        } catch (e: Exception) {
            context.getSharedPreferences("pcbridge_widget_fallback", Context.MODE_PRIVATE)
        }

        val name = prefs.getString("device_name", null) ?: return null
        val address = prefs.getString("device_address", null) ?: return null
        val pin = prefs.getString("device_pin", null) ?: return null
        return Triple(name, address, pin)
    }

    private fun launchPendingIntent(context: Context): PendingIntent {
        val launchIntent = Intent(context, MainActivity::class.java)
        return PendingIntent.getActivity(
            context, 0, launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    private fun updateWidget(context: Context, appWidgetManager: AppWidgetManager, appWidgetId: Int) {
        val device = readDevice(context)
        val views = RemoteViews(context.packageName, R.layout.widget_pcbridge)
        views.setOnClickPendingIntent(R.id.widget_root, launchPendingIntent(context))

        if (device == null) {
            views.setTextViewText(R.id.widget_name, "PC Bridge")
            views.setTextViewText(R.id.widget_status, "Open the app to set up")
            appWidgetManager.updateAppWidget(appWidgetId, views)
            return
        }

        val (name, address, pin) = device
        views.setTextViewText(R.id.widget_name, name)
        views.setTextViewText(R.id.widget_status, "Checking…")
        appWidgetManager.updateAppWidget(appWidgetId, views)

        // Widget updates run on the main thread with a short time budget --
        // goAsync() extends that so the network check can finish.
        val pendingResult = goAsync()
        Thread {
            try {
                val freeText = fetchFreeSpace(address, pin)
                val updated = RemoteViews(context.packageName, R.layout.widget_pcbridge)
                updated.setOnClickPendingIntent(R.id.widget_root, launchPendingIntent(context))
                updated.setTextViewText(R.id.widget_name, name)
                updated.setTextViewText(
                    R.id.widget_status,
                    if (freeText != null) "Online · $freeText free" else "Offline"
                )
                appWidgetManager.updateAppWidget(appWidgetId, updated)
            } finally {
                pendingResult.finish()
            }
        }.start()
    }

    /** Minimal manual JSON field extraction so the widget doesn't need a JSON library. */
    private fun fetchFreeSpace(address: String, pin: String): String? {
        var connection: HttpURLConnection? = null
        return try {
            val url = URL("http://$address/api/stats?pin=$pin")
            connection = (url.openConnection() as HttpURLConnection).apply {
                connectTimeout = 3000
                readTimeout = 3000
                requestMethod = "GET"
            }
            if (connection.responseCode != 200) return null
            val body = connection.inputStream.bufferedReader().readText()
            Regex(""""free_human"\s*:\s*"([^"]+)"""").find(body)?.groupValues?.get(1)
        } catch (e: Exception) {
            null
        } finally {
            connection?.disconnect()
        }
    }
}
