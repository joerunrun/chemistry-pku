package com.example.zrgfirstapp

import android.content.Context
import org.json.JSONObject
import java.io.DataOutputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean

/** Durable files and one small retry queue. A receipt is saved before local media is removed. */
internal class StudentRecordStore(context: Context) {
    private val root = File(context.filesDir, "student_records").apply { mkdirs() }
    private val uploading = AtomicBoolean(false)

    fun create(kind: String, baseUrl: String, device: String, experiment: String): File {
        val id = SimpleDateFormat("yyyyMMdd", Locale.US).format(Date()) + "_" + UUID.randomUUID().toString().replace("-", "")
        val dir = File(root, id).apply { mkdirs() }
        write(dir, JSONObject().put("client_record_id", id).put("kind", kind).put("base_url", baseUrl)
            .put("device_id", device).put("experiment_type", experiment).put("started_at", UploadMetadata.nowIso()).put("state", "recording"))
        return dir
    }

    fun markStarted(dir: File, startedAtMs: Long) {
        write(dir, read(dir).put("started_at", UploadMetadata.isoAt(startedAtMs)))
    }

    fun ready(dir: File, audio: File?, video: File?, photo: File? = null, reason: String = "user", endedAt: String = UploadMetadata.nowIso()) {
        val job = read(dir).put("ended_at", endedAt).put("reason", reason)
        audio?.let { job.put("audio", it.name) }
        video?.let { job.put("video", it.name) }
        photo?.let { job.put("photo", it.name) }
        if (audio == null && video == null && photo == null) job.put("state", "failed")
        else job.put("state", "ready")
        write(dir, job)
        retry()
    }

    fun fail(dir: File) { write(dir, read(dir).put("state", "failed")) }

    fun recover() {
        for (dir in root.listFiles().orEmpty().filter { it.isDirectory }) {
            val job = runCatching { read(dir) }.getOrNull() ?: continue
            if (job.optString("state") != "recording") continue
            val audio = File(dir, "record_audio.wav").takeIf { it.length() > 44 }
            audio?.let { StudentAudioCapture.repair(it) }
            val video = dir.listFiles().orEmpty().firstOrNull { it.extension == "mp4" && it.length() > 0 }
            val photo = File(dir, "photo.jpg").takeIf { it.length() > 0 }
            ready(dir, audio, video, photo, reason = "interrupted")
        }
        retry()
    }

    fun retry() {
        if (!uploading.compareAndSet(false, true)) return
        Thread {
            try {
                for (dir in root.listFiles().orEmpty().filter { it.isDirectory }) {
                    val job = runCatching { read(dir) }.getOrNull() ?: continue
                    if (job.optString("state") != "ready") continue
                    try { upload(dir, job) }
                    catch (error: Exception) {
                        runCatching { write(dir, job.put("last_error", (error.message ?: "upload failed").take(200))) }
                    }
                }
            } finally { uploading.set(false) }
        }.start()
    }

    private fun upload(dir: File, job: JSONObject) {
        val photo = job.has("photo")
        val path = if (photo) "/api/glasses/photos/upload" else "/api/glasses/records/upload"
        val connection = URL(job.getString("base_url") + path).openConnection() as HttpURLConnection
        try {
            val boundary = "ChemStudent${UUID.randomUUID()}"
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.connectTimeout = 15_000
            connection.readTimeout = 120_000
            connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            connection.setChunkedStreamingMode(64 * 1024)
            DataOutputStream(connection.outputStream).use { out ->
                val fields = listOf("client_record_id", "device_id", "experiment_type", "started_at", "ended_at")
                for (name in fields) {
                    out.writeBytes("--$boundary\r\nContent-Disposition: form-data; name=\"$name\"\r\n\r\n")
                    out.write(job.optString(name).toByteArray(Charsets.UTF_8)); out.writeBytes("\r\n")
                }
                out.writeBytes("--$boundary\r\nContent-Disposition: form-data; name=\"metadata\"\r\n\r\n")
                out.write(JSONObject().put("recording_type", job.optString("kind")).put("end_reason", job.optString("reason"))
                    .put("video_audio_status", if (job.has("video") && job.optString("video") != "record_video.mp4") "separate" else "embedded_or_audio_only")
                    .toString().toByteArray())
                out.writeBytes("\r\n")
                for (part in listOf("audio", "video", "photo")) {
                    if (!job.has(part)) continue
                    val file = File(dir, job.getString(part))
                    val mime = when (part) { "audio" -> "audio/wav"; "video" -> "video/mp4"; else -> "image/jpeg" }
                    out.writeBytes("--$boundary\r\nContent-Disposition: form-data; name=\"$part\"; filename=\"${file.name}\"\r\nContent-Type: $mime\r\n\r\n")
                    file.inputStream().use { it.copyTo(out) }; out.writeBytes("\r\n")
                }
                out.writeBytes("--$boundary--\r\n")
            }
            check(connection.responseCode in 200..299) { "HTTP ${connection.responseCode}" }
            val receipt = JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
            check(receipt.optString("status") == "ok") { "Missing upload receipt" }
            val objectName = if (photo) "photo" else "record"
            check(receipt.optJSONObject(objectName)?.optString(if (photo) "photo_id" else "record_id").orEmpty().isNotBlank())
            write(dir, job.put("state", "uploaded").put("receipt", receipt))
            dir.listFiles().orEmpty().filter { it.extension in setOf("wav", "mp4", "m4a", "jpg") }.forEach { it.delete() }
        } finally { connection.disconnect() }
    }

    private fun read(dir: File) = JSONObject(File(dir, "job.json").readText())
    private fun write(dir: File, job: JSONObject) {
        val temp = File(dir, "job.tmp")
        temp.writeText(job.toString())
        check(temp.renameTo(File(dir, "job.json"))) { "Cannot save record job" }
    }
}
