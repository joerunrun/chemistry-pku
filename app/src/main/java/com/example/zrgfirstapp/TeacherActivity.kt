package com.example.zrgfirstapp

import android.Manifest
import android.app.Service
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import android.view.GestureDetector
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.ContextCompat.startForegroundService
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import kotlin.math.max
import kotlin.math.sqrt

class TeacherActivity : AppCompatActivity() {
    private lateinit var gestureDetector: GestureDetector
    private val mainHandler = Handler(Looper.getMainLooper())
    private var activePlayer: MediaPlayer? = null

    // 持续录音（单路，同时承担保存 + 唤醒检测的环形缓冲来源）
    @Volatile private var continuousRunning = false
    private var continuousRecorder: AudioRecord? = null
    private var continuousFile: File? = null
    private var continuousTotalSamples = 0
    private val ringBuffer = ByteArrayOutputStream()
    private val ringLock = Any()
    private val ringCapacityBytes = SAMPLE_RATE * 2 * 4 // 最近 4 秒 PCM

    // 唤醒/指令状态
    private enum class TeacherState { LISTENING, AWAKE, INSTRUCTION, RECORDING }
    private var teacherState = TeacherState.LISTENING
    private val operationToken = java.util.concurrent.atomic.AtomicInteger(0)
    private var wakeCheckScheduled = false
    private var videoRecordingActive = false

    // 录像服务
    private var recordingService: RecordingService? = null
    private var serviceBound = false
    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            val binder = service as? RecordingService.LocalBinder
            recordingService = binder?.service()
            serviceBound = true
        }
        override fun onServiceDisconnected(name: ComponentName?) {
            recordingService = null
            serviceBound = false
        }
    }

    private val connectionMode: String by lazy {
        getString(R.string.glasses_connection_mode).trim().lowercase()
    }

    private val baseUrl: String by lazy {
        val resourceId = if (connectionMode == "network") {
            R.string.glasses_service_base_url_network
        } else {
            R.string.glasses_service_base_url_usb
        }
        getString(resourceId).trim().trimEnd('/')
    }

    private val requestAudioPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (!granted) {
            finish()
        } else if (teacherConnectionReady) {
            startTeacherSession()
        } else {
            checkTeacherConnection()
        }
    }

    private val requestCameraPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (!granted) playPrompt("unclear")
    }

    private var lastTempleKeyTapAt = 0L
    private var teacherConnectionReady = false
    private var teacherConnectionCheckInProgress = false
    private var teacherSessionStarted = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        setContentView(R.layout.activity_main)
        findViewById<View>(R.id.main).setBackgroundColor(Color.TRANSPARENT)

        gestureDetector = GestureDetector(this, object : GestureDetector.SimpleOnGestureListener() {
            override fun onDown(e: MotionEvent): Boolean = true
            override fun onSingleTapConfirmed(e: MotionEvent): Boolean {
                if (!teacherConnectionReady) checkTeacherConnection()
                return true
            }
            override fun onDoubleTap(e: MotionEvent): Boolean {
                exitTeacherApp()
                return true
            }
        })
        hideAllViews()

        checkTeacherConnection()
    }

    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        if (gestureDetector.onTouchEvent(event)) return true
        return super.dispatchTouchEvent(event)
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.action != KeyEvent.ACTION_UP) {
            return super.dispatchKeyEvent(event)
        }
        if (event.keyCode == KeyEvent.KEYCODE_BACK || event.keyCode == KeyEvent.KEYCODE_ESCAPE) {
            exitTeacherApp()
            return true
        }

        // RayNeo firmwares may expose temple taps as DPAD/ENTER or function keys.
        // Only a second tap within the normal double-tap window exits this voice-only page.
        if (event.keyCode in setOf(
                KeyEvent.KEYCODE_DPAD_CENTER,
                KeyEvent.KEYCODE_ENTER,
                KeyEvent.KEYCODE_SPACE,
                KeyEvent.KEYCODE_F1,
                KeyEvent.KEYCODE_F2,
                KeyEvent.KEYCODE_F3,
                KeyEvent.KEYCODE_F4,
                KeyEvent.KEYCODE_F5,
                KeyEvent.KEYCODE_F6,
                KeyEvent.KEYCODE_F7,
                KeyEvent.KEYCODE_F8,
            )
        ) {
            val now = android.os.SystemClock.uptimeMillis()
            if (now - lastTempleKeyTapAt <= 500L) {
                exitTeacherApp()
            } else {
                lastTempleKeyTapAt = now
            }
            return true
        }
        return super.dispatchKeyEvent(event)
    }

    override fun onBackPressed() {
        exitTeacherApp()
    }

    private fun exitTeacherApp() {
        continuousRunning = false
        finishAndRemoveTask()
    }

    private fun hideAllViews() {
        listOf(R.id.left_eye_panel, R.id.right_eye_panel).forEach { id ->
            val panel = findViewById<View>(id) ?: return@forEach
            listOf(
                R.id.title_text, R.id.subtitle_text, R.id.options_row,
                R.id.detection_preview, R.id.detection_overlay_container,
                R.id.demo_video_view, R.id.progress, R.id.thinking_text,
                R.id.status_text, R.id.result_text,
            ).forEach { v -> panel.findViewById<View>(v)?.visibility = View.GONE }
        }
    }

    private fun showTeacherConnectionState(title: String, status: String, detail: String) {
        listOf(R.id.left_eye_panel, R.id.right_eye_panel).forEach { id ->
            val panel = findViewById<View>(id) ?: return@forEach
            panel.findViewById<android.widget.TextView>(R.id.title_text).apply {
                text = title
                visibility = View.VISIBLE
            }
            panel.findViewById<android.widget.TextView>(R.id.subtitle_text).apply {
                text = status
                visibility = View.VISIBLE
            }
            panel.findViewById<android.widget.TextView>(R.id.status_text).apply {
                text = detail
                visibility = View.VISIBLE
            }
        }
    }

    private fun checkTeacherConnection() {
        if (teacherConnectionCheckInProgress || teacherSessionStarted) return
        teacherConnectionCheckInProgress = true
        hideAllViews()
        showTeacherConnectionState("教师助手", "正在连接", "正在检查电脑服务...")
        Thread {
            try {
                val connection = (URL("$baseUrl/api/glasses/health").openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = 8_000
                    readTimeout = 8_000
                }
                val code = connection.responseCode
                connection.inputStream?.close()
                connection.disconnect()
                if (code !in 200..299) throw IOException("HTTP $code")
                runOnUiThread {
                    teacherConnectionCheckInProgress = false
                    teacherConnectionReady = true
                    hideAllViews()
                    if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                        == PackageManager.PERMISSION_GRANTED
                    ) {
                        startTeacherSession()
                    } else {
                        requestAudioPermission.launch(Manifest.permission.RECORD_AUDIO)
                    }
                }
            } catch (exc: Exception) {
                runOnUiThread {
                    teacherConnectionCheckInProgress = false
                    teacherConnectionReady = false
                    hideAllViews()
                    showTeacherConnectionState(
                        "教师助手",
                        "连接失败",
                        "电脑服务未连接。单击重试，双击退出。",
                    )
                }
            }
        }.start()
    }

    private fun startTeacherSession() {
        if (!teacherConnectionReady || teacherSessionStarted) return
        teacherSessionStarted = true
        bindService(
            Intent(this, RecordingService::class.java), serviceConnection, Context.BIND_AUTO_CREATE
        )
        playPrompt("assistant_ready") {
            startContinuousRecording()
        }
    }

    // ===== 单路持续录音：写 wav + 维护最近 4 秒环形缓冲 =====
    @Suppress("MissingPermission")
    private fun startContinuousRecording() {
        if (continuousRunning) return
        continuousRunning = true
        val recorder = createRecorder()
        continuousRecorder = recorder
        Thread {
            try {
                val minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
                val buffer = ShortArray(max(minBuf, 3200) / 2)
                recorder.startRecording()
                while (continuousRunning && teacherState != TeacherState.INSTRUCTION) {
                    val file = File(cacheDir, "teacher_session_${System.currentTimeMillis()}.wav")
                    continuousFile = file
                    continuousTotalSamples = 0
                    val segmentDeadline = System.currentTimeMillis() + CONTINUOUS_SEGMENT_MS
                    FileOutputStream(file).use { output ->
                        writeWavHeader(output, 0)
                        while (
                            continuousRunning &&
                            teacherState != TeacherState.INSTRUCTION &&
                            System.currentTimeMillis() < segmentDeadline
                        ) {
                            val read = recorder.read(buffer, 0, buffer.size)
                            if (read > 0) {
                                writePcm16(output, buffer, read)
                                continuousTotalSamples += read
                                appendRing(buffer, read)
                            }
                        }
                    }
                    rewriteWavHeader(file, continuousTotalSamples)
                    if (file.length() > 44) uploadRecordFiles(listOf(file))
                }
                recorder.stop()
            } catch (exc: Exception) {
                Log.e("TeacherAudio", "continuous recording stopped unexpectedly", exc)
            } finally {
                recorder.release()
                if (continuousRecorder === recorder) {
                    continuousRecorder = null
                }
            }
        }.start()
        scheduleWakeCheck()
    }

    private fun appendRing(buffer: ShortArray, count: Int) {
        val bytes = ByteArray(count * 2)
        for (i in 0 until count) {
            val v = buffer[i].toInt()
            bytes[i * 2] = (v and 0xff).toByte()
            bytes[i * 2 + 1] = ((v shr 8) and 0xff).toByte()
        }
        synchronized(ringLock) {
            ringBuffer.write(bytes, 0, bytes.size)
            val excess = ringBuffer.size() - ringCapacityBytes
            if (excess > 0) {
                val dropped = ringBuffer.toByteArray()
                ringBuffer.reset()
                ringBuffer.write(dropped, excess, dropped.size - excess)
            }
        }
    }

    private fun takeRingSnapshotSeconds(seconds: Int): ByteArray? {
        val need = SAMPLE_RATE * 2 * seconds
        synchronized(ringLock) {
            val all = ringBuffer.toByteArray()
            if (all.size < need) return null
            return all.copyOfRange(all.size - need, all.size)
        }
    }

    // ===== 唤醒/停止检测：每 3 秒取最近 3 秒 PCM 发服务端 =====
    // LISTENING 状态检测唤醒词（sleep），RECORDING 状态检测停止录像（teacher_recording）
    private fun scheduleWakeCheck() {
        if (!continuousRunning) return
        if (teacherState != TeacherState.LISTENING && teacherState != TeacherState.RECORDING) return
        if (wakeCheckScheduled) return
        wakeCheckScheduled = true
        mainHandler.postDelayed({
            wakeCheckScheduled = false
            if (teacherState != TeacherState.LISTENING && teacherState != TeacherState.RECORDING) return@postDelayed
            val pcm = takeRingSnapshotSeconds(3) ?: run {
                scheduleWakeCheck(); return@postDelayed
            }
            val context = if (teacherState == TeacherState.RECORDING) "teacher_recording" else "sleep"
            val file = File(cacheDir, "teacher_wake_${System.currentTimeMillis()}.pcm")
            try { FileOutputStream(file).use { it.write(pcm) } } catch (_: Exception) {
                scheduleWakeCheck(); return@postDelayed
            }
            submitVoiceIntent(file, context = context, isSleepMode = (context == "sleep"))
        }, 3000L)
    }

    private fun submitVoiceIntent(file: File, context: String, isSleepMode: Boolean) {
        val token = operationToken.incrementAndGet()
        Thread {
            try {
                val json = postMultipart(
                    "$baseUrl/api/glasses/voice/intent",
                    mapOf(
                        "model" to "qwen3.5-omni-flash-realtime",
                        "context" to context,
                        "demo_options" to "",
                    ),
                    "audio",
                    file,
                    "application/octet-stream",
                )
                if (context == "teacher_awake") {
                    archiveTeacherCommandAudio(file)
                }
                runOnUiIfCurrent(token) {
                    when {
                        isSleepMode -> handleWakeResponse(json)
                        context == "teacher_recording" -> handleStopRecordingIntent(json)
                        else -> handleInstructionIntent(json)
                    }
                }
            } catch (_: Exception) {
                runOnUiIfCurrent(token) {
                    when {
                        isSleepMode -> scheduleWakeCheck()
                        context == "teacher_recording" -> scheduleWakeCheck()
                        else -> {
                            playPrompt("unclear")
                            resumeListening()
                        }
                    }
                }
            } finally {
                file.delete()
            }
        }.start()
    }

    private fun handleWakeResponse(json: org.json.JSONObject) {
        val wake = json.optBoolean("wake", false)
        if (wake) {
            teacherState = TeacherState.AWAKE
            playPrompt("wake_response") { startInstructionCapture() }
        } else {
            scheduleWakeCheck()
        }
    }

    // 录像中检测到停止意图
    private fun handleStopRecordingIntent(json: org.json.JSONObject) {
        val intent = json.optString("intent", "unclear")
        if (intent == "stop_recording") {
            stopTeacherRecording()
        } else {
            // 没听清停止，继续在录像状态监听
            scheduleWakeCheck()
        }
    }

    private fun stopTeacherRecording() {
        videoRecordingActive = false
        val service = recordingService
        if (service != null && serviceBound) {
            service.stopRecording { files ->
                if (files.isNotEmpty()) uploadRecordFiles(files)
                stopService(Intent(this, RecordingService::class.java))
            }
        } else {
            stopService(Intent(this, RecordingService::class.java))
        }
        playPrompt("recording_stop")
        teacherState = TeacherState.LISTENING
        scheduleWakeCheck()
    }

    // 唤醒后：停持续录音，录 3.5 秒短片段做主控指令识别（复用学生端 AWAKE_PROMPT）
    @Suppress("MissingPermission")
    private fun startInstructionCapture() {
        teacherState = TeacherState.INSTRUCTION
        continuousRunning = false
        // The continuous recorder needs one read cycle to close its WAV segment.
        mainHandler.postDelayed({ captureInstructionClip() }, 250L)
    }

    @Suppress("MissingPermission")
    private fun captureInstructionClip() {
        val token = operationToken.incrementAndGet()
        val file = File(cacheDir, "teacher_intent_${System.currentTimeMillis()}.pcm")
        val minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
        val bufferSize = max(minBuf, 3200)
        val recorder = createRecorder()
        Thread {
            try {
                FileOutputStream(file).use { output ->
                    recorder.startRecording()
                    val buffer = ByteArray(bufferSize)
                    val deadline = System.currentTimeMillis() + 3500L
                    while (System.currentTimeMillis() < deadline) {
                        val read = recorder.read(buffer, 0, buffer.size)
                        if (read > 0) output.write(buffer, 0, read)
                    }
                    recorder.stop()
                }
                runOnUiIfCurrent(token) { submitVoiceIntent(file, context = "teacher_awake", isSleepMode = false) }
            } catch (_: Exception) {
                runOnUiIfCurrent(token) { resumeListening() }
            } finally {
                recorder.release()
            }
        }.start()
    }

    private fun handleInstructionIntent(json: org.json.JSONObject) {
        val intent = json.optString("intent", "unclear")
        when (intent) {
            "capture_photo" -> {
                if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                    != PackageManager.PERMISSION_GRANTED
                ) {
                    requestCameraPermission.launch(Manifest.permission.CAMERA)
                    resumeListening(); return
                }
                playPrompt("start_capture")
                withService { service ->
                    service.captureStillImage { file, error ->
                        if (file != null) {
                            uploadTeacherPhoto(file)
                        } else {
                            Log.w("TeacherPhoto", "capture failed: ${error?.message}")
                        }
                        runOnUiThread { resumeListening() }
                    }
                }
            }
            "start_recording" -> {
                playPrompt("recording_start") {
                    startForegroundService(
                        this, Intent(this, RecordingService::class.java)
                            .setAction(RecordingService.ACTION_START_RECORDING)
                    )
                    withService { it.startRecording() }
                    // 进入录像状态：恢复持续录音，唤醒检测切到检测"停止录像"
                    videoRecordingActive = true
                    teacherState = TeacherState.RECORDING
                    if (!continuousRunning) startContinuousRecording() else scheduleWakeCheck()
                }
            }
            "stop_recording" -> {
                stopTeacherRecording()
            }
            else -> {
                playPrompt("unclear")
                resumeListening()
            }
        }
    }

    private fun resumeListening() {
        teacherState = TeacherState.LISTENING
        if (!continuousRunning) startContinuousRecording()
        else scheduleWakeCheck()
    }

    private fun withService(action: (RecordingService) -> Unit) {
        val s = recordingService
        if (s != null && serviceBound) { action(s); return }
        mainHandler.postDelayed({ withService(action) }, 200L)
    }

    // ===== 退出：停录音 + 上传持续录音 wav =====
    private fun uploadRecordFiles(files: List<File>) {
        Thread {
            for (file in files) {
                val isAudio = file.extension.lowercase() in listOf("wav", "m4a", "aac", "mp3")
                val field = if (isAudio) "audio" else "video"
                val mime = if (isAudio) "audio/wav" else "video/mp4"
                try {
                    postMultipart(
                        "$baseUrl/api/glasses/teacher/records/upload",
                        mapOf(
                            "device_id" to ("teacher_" + Build.MODEL),
                            "experiment_type" to "teacher",
                        ),
                        field, file, mime,
                    )
                    if (!file.delete()) {
                        Log.w("TeacherRecord", "Uploaded but could not delete ${file.name}")
                    }
                } catch (e: Exception) {
                    Log.w("TeacherRecord", "upload fail ${file.name}: ${e.message}")
                }
            }
        }.start()
    }

    private fun uploadTeacherPhoto(file: File) {
        Thread {
            try {
                postMultipart(
                    "$baseUrl/api/glasses/teacher/photos/upload",
                    mapOf(
                        "device_id" to ("teacher_" + Build.MODEL),
                        "experiment_type" to "teacher",
                        "captured_at" to System.currentTimeMillis().toString(),
                    ),
                    "photo",
                    file,
                    "image/jpeg",
                )
                if (!file.delete()) {
                    Log.w("TeacherPhoto", "Uploaded but could not delete ${file.name}")
                }
            } catch (e: Exception) {
                Log.w("TeacherPhoto", "upload fail ${file.name}: ${e.message}")
            }
        }.start()
    }

    private fun archiveTeacherCommandAudio(pcmFile: File) {
        try {
            val pcm = pcmFile.readBytes()
            if (pcm.isEmpty()) return
            val wavFile = File(cacheDir, "teacher_command_${System.currentTimeMillis()}.wav")
            FileOutputStream(wavFile).use { output ->
                output.write(buildWavHeader(pcm.size / 2))
                output.write(pcm)
            }
            uploadRecordFiles(listOf(wavFile))
        } catch (e: Exception) {
            Log.w("TeacherRecord", "archive command audio failed: ${e.message}")
        }
    }

    private fun runOnUiIfCurrent(token: Int, action: () -> Unit) {
        mainHandler.post { if (operationToken.get() == token) action() }
    }

    // ===== 音频播放 =====
    private fun playPrompt(name: String, onDone: (() -> Unit)? = null) {
        val resId = resources.getIdentifier(name, "raw", packageName)
        if (resId == 0) { onDone?.invoke(); return }
        activePlayer?.release()
        activePlayer = MediaPlayer.create(this, resId)?.apply {
            setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANCE_ACCESSIBILITY)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()
            )
            setOnCompletionListener {
                it.release()
                if (activePlayer === it) activePlayer = null
                onDone?.invoke()
            }
            start()
        }
    }

    // ===== 录音工具 =====
    @Suppress("MissingPermission")
    private fun createRecorder(): AudioRecord {
        val minBuffer = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
        if (minBuffer <= 0) throw IOException("麦克风初始化失败")
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.MIC, SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT,
            max(minBuffer, 6400),
        )
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release(); throw IOException("麦克风不可用")
        }
        return recorder
    }

    override fun onDestroy() {
        continuousRunning = false
        // 如果正在录像，停止并上传录像文件
        if (videoRecordingActive) {
            recordingService?.stopRecording { files ->
                if (files.isNotEmpty()) uploadRecordFiles(files)
                stopService(Intent(this, RecordingService::class.java))
            } ?: stopService(Intent(this, RecordingService::class.java))
        }
        // Let the recorder loop close the current WAV header and enqueue its upload.
        try { Thread.sleep(350) } catch (_: Exception) {}
        if (serviceBound) { unbindService(serviceConnection); serviceBound = false }
        activePlayer?.release(); activePlayer = null
        super.onDestroy()
    }

    // ===== 网络 =====
    private fun postMultipart(
        urlText: String, fields: Map<String, String>,
        fileField: String, file: File, contentType: String = "application/octet-stream",
    ): org.json.JSONObject {
        val boundary = "----TeacherBoundary${System.currentTimeMillis()}"
        val connection = (URL(urlText).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"; doOutput = true
            connectTimeout = 10_000; readTimeout = 30_000
            setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
        }
        java.io.DataOutputStream(connection.outputStream).use { out ->
            fields.forEach { (name, value) ->
                out.writeBytes("--$boundary\r\n")
                out.writeBytes("Content-Disposition: form-data; name=\"$name\"\r\n\r\n")
                out.write(value.toByteArray(Charsets.UTF_8)); out.writeBytes("\r\n")
            }
            out.writeBytes("--$boundary\r\n")
            out.writeBytes("Content-Disposition: form-data; name=\"$fileField\"; filename=\"${file.name}\"\r\n")
            out.writeBytes("Content-Type: $contentType\r\n\r\n")
            java.io.FileInputStream(file).use { it.copyTo(out) }
            out.writeBytes("\r\n--$boundary--\r\n"); out.flush()
        }
        val code = connection.responseCode
        val stream = if (code in 200..299) connection.inputStream else connection.errorStream
        val body = stream?.use { it.readBytes().toString(Charsets.UTF_8) }.orEmpty()
        connection.disconnect()
        if (code !in 200..299) throw IOException("HTTP $code: $body")
        return org.json.JSONObject(body)
    }

    companion object {
        private const val SAMPLE_RATE = 16_000
        private const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        private const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT
        private const val CONTINUOUS_SEGMENT_MS = 120_000L

        private fun writePcm16(output: FileOutputStream, buffer: ShortArray, count: Int) {
            val bytes = ByteArray(count * 2)
            for (i in 0 until count) {
                val value = buffer[i].toInt()
                bytes[i * 2] = (value and 0xff).toByte()
                bytes[i * 2 + 1] = ((value shr 8) and 0xff).toByte()
            }
            output.write(bytes)
        }
        private fun writeWavHeader(output: FileOutputStream, samples: Int) = output.write(buildWavHeader(samples))
        private fun rewriteWavHeader(file: File, samples: Int) {
            java.io.RandomAccessFile(file, "rw").use { it.seek(0); it.write(buildWavHeader(samples)) }
        }
        private fun buildWavHeader(samples: Int): ByteArray {
            val byteRate = SAMPLE_RATE * 2
            val dataSize = samples * 2
            val totalSize = dataSize + 36
            return ByteArray(44).also { h ->
                fun text(o: Int, v: String) = v.toByteArray(Charsets.US_ASCII).copyInto(h, o)
                fun le32(o: Int, v: Int) { h[o]=(v and 0xff).toByte(); h[o+1]=((v shr 8) and 0xff).toByte(); h[o+2]=((v shr 16) and 0xff).toByte(); h[o+3]=((v shr 24) and 0xff).toByte() }
                fun le16(o: Int, v: Int) { h[o]=(v and 0xff).toByte(); h[o+1]=((v shr 8) and 0xff).toByte() }
                text(0,"RIFF"); le32(4,totalSize); text(8,"WAVE"); text(12,"fmt ")
                le32(16,16); le16(20,1); le16(22,1); le32(24,SAMPLE_RATE); le32(28,byteRate)
                le16(32,2); le16(34,16); text(36,"data"); le32(40,dataSize)
            }
        }
    }
}
