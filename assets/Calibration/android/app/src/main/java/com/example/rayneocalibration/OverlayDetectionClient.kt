package com.example.rayneocalibration

import android.graphics.Color
import android.util.Base64
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

class OverlayDetectionClient(
    private val baseUrl: String,
) {
    data class DetectionBox(
        val label: String,
        val name: String,
        val color: Int,
        val box: FloatArray,
    )

    data class Result(
        val boxes: List<DetectionBox>,
        val annotatedBytes: ByteArray?,
        val message: String,
    )

    fun detect(imageFile: File): Result {
        val boundary = "----RayNeoOverlay${System.currentTimeMillis()}"
        val url = URL("${baseUrl.trimEnd('/')}/api/glasses/overlay-test")
        val connection = (url.openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 8000
            readTimeout = 30000
            doOutput = true
            setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
        }

        connection.outputStream.use { out ->
            fun writeTextPart(name: String, value: String) {
                out.write("--$boundary\r\n".toByteArray())
                out.write("Content-Disposition: form-data; name=\"$name\"\r\n\r\n".toByteArray())
                out.write(value.toByteArray(Charsets.UTF_8))
                out.write("\r\n".toByteArray())
            }
            writeTextPart("experiment_type", "other")
            out.write("--$boundary\r\n".toByteArray())
            out.write(
                "Content-Disposition: form-data; name=\"image\"; filename=\"capture.jpg\"\r\n"
                    .toByteArray(),
            )
            out.write("Content-Type: image/jpeg\r\n\r\n".toByteArray())
            imageFile.inputStream().use { it.copyTo(out) }
            out.write("\r\n--$boundary--\r\n".toByteArray())
        }

        val status = connection.responseCode
        val body = if (status in 200..299) {
            connection.inputStream.use { it.readBytes() }
        } else {
            val err = connection.errorStream?.use { it.readBytes() } ?: ByteArray(0)
            error("HTTP $status: ${String(err, Charsets.UTF_8).take(180)}")
        }

        val root = JSONObject(String(body, Charsets.UTF_8))
        if (root.has("error")) {
            error(root.optString("error"))
        }
        val boxesJson = root.optJSONArray("overlay_boxes")
        val boxes = mutableListOf<DetectionBox>()
        if (boxesJson != null) {
            for (i in 0 until boxesJson.length()) {
                val item = boxesJson.getJSONObject(i)
                val arr = item.getJSONArray("box")
                boxes += DetectionBox(
                    label = item.optString("label"),
                    name = item.optString("name"),
                    color = parseColor(item.optString("color", "#ffcc00")),
                    box = floatArrayOf(
                        arr.getDouble(0).toFloat(),
                        arr.getDouble(1).toFloat(),
                        arr.getDouble(2).toFloat(),
                        arr.getDouble(3).toFloat(),
                    ),
                )
            }
        }
        val annotated = decodeDataUrl(root.optString("preview_data_url", ""))
        return Result(
            boxes = boxes,
            annotatedBytes = annotated,
            message = root.optString("message", "Detection finished."),
        )
    }

    private fun parseColor(value: String): Int {
        return runCatching { Color.parseColor(value) }.getOrDefault(Color.YELLOW)
    }

    private fun decodeDataUrl(value: String): ByteArray? {
        if (value.isBlank()) return null
        val comma = value.indexOf(',')
        val payload = if (comma >= 0) value.substring(comma + 1) else value
        return runCatching { Base64.decode(payload, Base64.DEFAULT) }.getOrNull()
    }
}
