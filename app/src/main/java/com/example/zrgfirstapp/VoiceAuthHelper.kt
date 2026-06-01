package com.example.zrgfirstapp

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.os.Handler
import android.os.Looper
import org.json.JSONObject
import java.io.BufferedReader
import java.io.DataOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import kotlin.math.max
import kotlin.math.sqrt

class VoiceAuthHelper(
    private val context: Context,
    private val baseUrl: String,
    private val onRender: (title: String, subtitle: String, status: String, result: String, running: Boolean) -> Unit,
) {
    private val sampleRate = 16_000
    private val threshold = 0.5
    private val silenceStopMs = 2_000L
    private val verifyDurationMs = 2_500L
    private val enrollDurationMs = 5_000L
    private val mainHandler = Handler(Looper.getMainLooper())
    private var running = false
    private var activeRecorder: AudioRecord? = null
    private var activePlayer: MediaPlayer? = null

    fun start() {
        running = true
        onRender("教师版助手", "连接电脑服务", "正在连接", "服务地址：$baseUrl", true)
        Thread {
            try {
                getJson("$baseUrl/api/glasses/health")
                mainHandler.post {
                    onRender("助手就绪", "录入教师声纹", "请教师说一句话", "例如：我要开始指导学生学习实验。", false)
                    playPrompt("assistant_ready")
                }
                mainHandler.postDelayed({ enrollTeacherReference() }, 900L)
            } catch (exc: Exception) {
                mainHandler.post {
                    onRender("教师版助手", "连接失败", "电脑服务未连接", exc.message ?: "连接失败", false)
                }
            }
        }.start()
    }

    fun stop() {
        running = false
        activeRecorder?.let {
            runCatching { it.stop() }
            it.release()
        }
        activeRecorder = null
        activePlayer?.release()
        activePlayer = null
    }

    private fun enrollTeacherReference() {
        if (!running) return
        Thread {
            try {
                mainHandler.post {
                    onRender("教师版助手", "录入教师声纹", "请保持正常音量说 5 秒", "正在录音...", true)
                }
                val file = recordFixedWav(enrollDurationMs, "teacher_enroll")
                try {
                    val json = postMultipart(
                        "$baseUrl/api/glasses/teacher/enroll",
                        emptyMap(),
                        "audio",
                        file,
                    )
                    if (!json.optBoolean("ok", false)) {
                        throw IOException(json.optString("error", "教师声纹录入失败"))
                    }
                    mainHandler.post {
                        onRender("助手就绪", "教师版", "声纹已录入", "开始持续监听教师声音。", false)
                    }
                    mainHandler.postDelayed({ verifyLoop() }, 700L)
                } finally {
                    file.delete()
                }
            } catch (exc: Exception) {
                mainHandler.post {
                    onRender("教师版助手", "录入失败", "请重新进入教师版", exc.message ?: "录入失败", false)
                }
            }
        }.start()
    }

    private fun verifyLoop() {
        if (!running) return
        Thread {
            try {
                mainHandler.post {
                    onRender("助手就绪", "教师版", "正在判断是否为教师说话", "", true)
                }
                val file = recordFixedWav(verifyDurationMs, "teacher_verify")
                try {
                    val json = postMultipart(
                        "$baseUrl/api/glasses/teacher/verify",
                        mapOf("threshold" to threshold.toString()),
                        "audio",
                        file,
                    )
                    val match = json.optBoolean("match", false)
                    val score = json.optDouble("score", 0.0)
                    val decision = json.optString("decision", "")
                    if (match) {
                        mainHandler.post {
                            onRender(
                                "教师版助手",
                                "已匹配教师",
                                "开始录音",
                                "声纹分数：${"%.2f".format(score)}",
                                true,
                            )
                        }
                        recordTeacherUtterance()
                    } else {
                        mainHandler.post {
                            onRender(
                                "助手就绪",
                                "教师版",
                                "继续监听",
                                "最近判断：$decision  分数：${"%.2f".format(score)}",
                                false,
                            )
                        }
                        mainHandler.postDelayed({ verifyLoop() }, 350L)
                    }
                } finally {
                    file.delete()
                }
            } catch (exc: Exception) {
                mainHandler.post {
                    onRender("教师版助手", "验证异常", "稍后重试", exc.message ?: "验证失败", false)
                }
                mainHandler.postDelayed({ verifyLoop() }, 1_500L)
            }
        }.start()
    }

    private fun recordTeacherUtterance() {
        try {
            val tempFile = recordUntilSilence("teacher_record")
            val savedName = copyRecordingToMediaStore(tempFile)
            tempFile.delete()
            mainHandler.post {
                onRender("助手就绪", "录音已保存", "静音 2 秒，自动停止", savedName, false)
            }
        } catch (exc: Exception) {
            mainHandler.post {
                onRender("教师版助手", "录音失败", "回到监听", exc.message ?: "录音失败", false)
            }
        } finally {
            mainHandler.postDelayed({ verifyLoop() }, 900L)
        }
    }

    @Suppress("MissingPermission")
    private fun recordFixedWav(durationMs: Long, prefix: String): File {
        val recorder = createRecorder()
        val file = File(context.cacheDir, "${prefix}_${System.currentTimeMillis()}.wav")
        val buffer = ShortArray(max(AudioRecord.getMinBufferSize(sampleRate, CHANNEL_CONFIG, AUDIO_FORMAT), 3200) / 2)
        val deadline = System.currentTimeMillis() + durationMs
        FileOutputStream(file).use { output ->
            writeWavHeader(output, 0)
            var totalSamples = 0
            activeRecorder = recorder
            recorder.startRecording()
            while (running && System.currentTimeMillis() < deadline) {
                val read = recorder.read(buffer, 0, buffer.size)
                if (read > 0) {
                    writePcm16(output, buffer, read)
                    totalSamples += read
                }
            }
            recorder.stop()
            rewriteWavHeader(file, totalSamples)
        }
        recorder.release()
        activeRecorder = null
        return file
    }

    @Suppress("MissingPermission")
    private fun recordUntilSilence(prefix: String): File {
        val recorder = createRecorder()
        val file = File(context.cacheDir, "${prefix}_${System.currentTimeMillis()}.wav")
        val buffer = ShortArray(max(AudioRecord.getMinBufferSize(sampleRate, CHANNEL_CONFIG, AUDIO_FORMAT), 3200) / 2)
        val maxDeadline = System.currentTimeMillis() + 120_000L
        var silenceMs = 0L
        var totalSamples = 0
        var hasSpeech = false
        FileOutputStream(file).use { output ->
            writeWavHeader(output, 0)
            activeRecorder = recorder
            recorder.startRecording()
            while (running && System.currentTimeMillis() < maxDeadline) {
                val read = recorder.read(buffer, 0, buffer.size)
                if (read <= 0) continue
                writePcm16(output, buffer, read)
                totalSamples += read
                val rms = shortRms(buffer, read)
                val chunkMs = read * 1000L / sampleRate
                if (rms >= SPEECH_RMS_THRESHOLD) {
                    hasSpeech = true
                    silenceMs = 0L
                } else if (hasSpeech) {
                    silenceMs += chunkMs
                }
                if (hasSpeech && silenceMs >= silenceStopMs) {
                    break
                }
            }
            recorder.stop()
            rewriteWavHeader(file, totalSamples)
        }
        recorder.release()
        activeRecorder = null
        return file
    }

    @Suppress("MissingPermission")
    private fun createRecorder(): AudioRecord {
        val minBuffer = AudioRecord.getMinBufferSize(sampleRate, CHANNEL_CONFIG, AUDIO_FORMAT)
        if (minBuffer <= 0) {
            throw IOException("麦克风初始化失败")
        }
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            sampleRate,
            CHANNEL_CONFIG,
            AUDIO_FORMAT,
            max(minBuffer, 6400),
        )
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            throw IOException("麦克风不可用")
        }
        return recorder
    }

    private fun copyRecordingToMediaStore(file: File): String {
        val name = "teacher_record_${java.text.SimpleDateFormat("yyyyMMdd_HHmmss", java.util.Locale.US).format(java.util.Date())}.wav"
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q) {
            val values = android.content.ContentValues().apply {
                put(android.provider.MediaStore.Audio.Media.DISPLAY_NAME, name)
                put(android.provider.MediaStore.Audio.Media.MIME_TYPE, "audio/wav")
                put(android.provider.MediaStore.Audio.Media.RELATIVE_PATH, android.os.Environment.DIRECTORY_MUSIC + "/ChemTeacherRecords")
                put(android.provider.MediaStore.Audio.Media.IS_PENDING, 1)
            }
            val uri = context.contentResolver.insert(android.provider.MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, values)
                ?: throw IOException("无法创建录音文件")
            context.contentResolver.openOutputStream(uri)?.use { output ->
                FileInputStream(file).use { input -> input.copyTo(output) }
            } ?: throw IOException("无法写入录音文件")
            values.clear()
            values.put(android.provider.MediaStore.Audio.Media.IS_PENDING, 0)
            context.contentResolver.update(uri, values, null, null)
            return "已保存：Music/ChemTeacherRecords/$name"
        }

        val dir = File(android.os.Environment.getExternalStoragePublicDirectory(android.os.Environment.DIRECTORY_MUSIC), "ChemTeacherRecords")
        dir.mkdirs()
        val target = File(dir, name)
        FileInputStream(file).use { input ->
            FileOutputStream(target).use { output -> input.copyTo(output) }
        }
        return "已保存：${target.absolutePath}"
    }

    private fun playPrompt(name: String) {
        val resId = context.resources.getIdentifier(name, "raw", context.packageName)
        if (resId == 0) return
        activePlayer?.release()
        activePlayer = MediaPlayer.create(context, resId)?.apply {
            setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANCE_ACCESSIBILITY)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            setOnCompletionListener {
                it.release()
                if (activePlayer === it) activePlayer = null
            }
            start()
        }
    }

    private fun postMultipart(
        urlText: String,
        fields: Map<String, String>,
        fileField: String,
        file: File,
    ): JSONObject {
        val boundary = "----TeacherBoundary${System.currentTimeMillis()}"
        val connection = (URL(urlText).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            doOutput = true
            connectTimeout = 10_000
            readTimeout = 60_000
            setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
        }
        DataOutputStream(connection.outputStream).use { out ->
            fields.forEach { (name, value) ->
                out.writeBytes("--$boundary\r\n")
                out.writeBytes("Content-Disposition: form-data; name=\"$name\"\r\n\r\n")
                out.write(value.toByteArray(Charsets.UTF_8))
                out.writeBytes("\r\n")
            }
            out.writeBytes("--$boundary\r\n")
            out.writeBytes("Content-Disposition: form-data; name=\"$fileField\"; filename=\"${file.name}\"\r\n")
            out.writeBytes("Content-Type: audio/wav\r\n\r\n")
            FileInputStream(file).use { input -> input.copyTo(out) }
            out.writeBytes("\r\n--$boundary--\r\n")
            out.flush()
        }
        return readJsonResponse(connection)
    }

    private fun getJson(urlText: String): JSONObject {
        val connection = (URL(urlText).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = 8_000
            readTimeout = 20_000
        }
        return readJsonResponse(connection)
    }

    private fun readJsonResponse(connection: HttpURLConnection): JSONObject {
        val code = connection.responseCode
        val stream = if (code in 200..299) connection.inputStream else connection.errorStream
        val body = stream?.use { readAll(it) }.orEmpty()
        connection.disconnect()
        if (code !in 200..299) {
            throw IOException(if (body.isBlank()) "HTTP $code" else "HTTP $code: $body")
        }
        return JSONObject(body)
    }

    private fun readAll(input: InputStream): String {
        return BufferedReader(input.reader(Charsets.UTF_8)).use { it.readText() }
    }

    companion object {
        private const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        private const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT
        private const val SPEECH_RMS_THRESHOLD = 600.0

        private fun shortRms(buffer: ShortArray, count: Int): Double {
            var sum = 0.0
            for (i in 0 until count) {
                val v = buffer[i].toDouble()
                sum += v * v
            }
            return sqrt(sum / max(1, count))
        }

        private fun writePcm16(output: FileOutputStream, buffer: ShortArray, count: Int) {
            val bytes = ByteArray(count * 2)
            for (i in 0 until count) {
                val value = buffer[i].toInt()
                bytes[i * 2] = (value and 0xff).toByte()
                bytes[i * 2 + 1] = ((value shr 8) and 0xff).toByte()
            }
            output.write(bytes)
        }

        private fun writeWavHeader(output: FileOutputStream, samples: Int) {
            output.write(buildWavHeader(samples))
        }

        private fun rewriteWavHeader(file: File, samples: Int) {
            java.io.RandomAccessFile(file, "rw").use { raf ->
                raf.seek(0)
                raf.write(buildWavHeader(samples))
            }
        }

        private fun buildWavHeader(samples: Int): ByteArray {
            val byteRate = 16_000 * 2
            val dataSize = samples * 2
            val totalSize = dataSize + 36
            return ByteArray(44).also { h ->
                fun text(offset: Int, value: String) {
                    value.toByteArray(Charsets.US_ASCII).copyInto(h, offset)
                }
                fun le32(offset: Int, value: Int) {
                    h[offset] = (value and 0xff).toByte()
                    h[offset + 1] = ((value shr 8) and 0xff).toByte()
                    h[offset + 2] = ((value shr 16) and 0xff).toByte()
                    h[offset + 3] = ((value shr 24) and 0xff).toByte()
                }
                fun le16(offset: Int, value: Int) {
                    h[offset] = (value and 0xff).toByte()
                    h[offset + 1] = ((value shr 8) and 0xff).toByte()
                }
                text(0, "RIFF")
                le32(4, totalSize)
                text(8, "WAVE")
                text(12, "fmt ")
                le32(16, 16)
                le16(20, 1)
                le16(22, 1)
                le32(24, 16_000)
                le32(28, byteRate)
                le16(32, 2)
                le16(34, 16)
                text(36, "data")
                le32(40, dataSize)
            }
        }
    }
}