package com.example.zrgfirstapp

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** Tiny persistent outbox; business actions never wait for the statistics server. */
internal object UsageEvents {
    private val executor = Executors.newSingleThreadExecutor()
    private val flushing = AtomicBoolean(false)

    fun record(context: Context, action: String, label: String = "") {
        val app = context.applicationContext
        try {
            val id = UUID.randomUUID().toString()
            val event = JSONObject().put("event_id", id).put("action", action)
                .put("role", BuildConfig.APP_MODE).put("device_id", DeviceIdentity.hardwareId(app))
                .put("occurred_at", UploadMetadata.nowIso()).put("label", label.take(200))
            val folder = File(app.filesDir, "usage_pending").apply { mkdirs() }
            val temporary = File(folder, "$id.tmp")
            temporary.writeText(event.toString())
            check(temporary.renameTo(File(folder, "$id.json")))
            retry(app)
        } catch (error: Exception) {
            Log.w("UsageEvents", "Could not queue usage event", error)
        }
    }

    fun retry(context: Context) {
        val app = context.applicationContext
        if (!flushing.compareAndSet(false, true)) return
        executor.execute {
            var failed = false
            val folder = File(app.filesDir, "usage_pending")
            try {
                val network = !BuildConfig.LOCAL_USB && app.getString(R.string.glasses_connection_mode).trim() == "network"
                val base = app.getString(if (network) R.string.glasses_service_base_url_network else R.string.glasses_service_base_url_usb).trimEnd('/')
                while (true) {
                    val files = folder.listFiles()?.filter { it.extension == "json" }.orEmpty()
                    if (files.isEmpty()) break
                    for (file in files) {
                        val connection = URL("$base/api/glasses/usage").openConnection() as HttpURLConnection
                        try {
                            connection.requestMethod = "POST"
                            connection.connectTimeout = 3_000
                            connection.readTimeout = 3_000
                            connection.doOutput = true
                            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                            connection.outputStream.use { it.write(file.readBytes()) }
                            check(connection.responseCode in 200..299) { "HTTP ${connection.responseCode}" }
                            val reply = connection.inputStream.bufferedReader().use { JSONObject(it.readText()) }
                            check(reply.optBoolean("ok")) { "Statistics not acknowledged" }
                            check(file.delete()) { "Could not remove acknowledged event" }
                        } finally { connection.disconnect() }
                    }
                }
            } catch (error: Exception) {
                failed = true
                Log.w("UsageEvents", "Usage upload deferred: ${error.message}")
            } finally {
                flushing.set(false)
                if (!failed && folder.listFiles()?.any { it.extension == "json" } == true) retry(app)
            }
        }
    }
}
