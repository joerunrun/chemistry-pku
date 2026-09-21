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
import android.view.WindowManager
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.ContextCompat.startForegroundService
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.max

class TeacherActivity : AppCompatActivity() {
    private lateinit var gestureDetector: GestureDetector
    private val mainHandler = Handler(Looper.getMainLooper())
    private var activePlayer: MediaPlayer? = null
    private val teacherDeviceId by lazy { DeviceIdentity.teacherId(this) }

    // 持续录音（单路，同时承担保存 + 唤醒检测的环形缓冲来源）
    @Volatile private var continuousRunning = false
    private var continuousRecorder: AudioRecord? = null
    @Volatile private var continuousStopLatch: CountDownLatch? = null
    private var continuousFile: File? = null
    private var continuousTotalSamples = 0
    private val ringBuffer = PcmRingBuffer(SAMPLE_RATE * PCM_BYTES_PER_SAMPLE * RING_SECONDS)

    // 唤醒/指令状态
    private enum class TeacherState { LISTENING, AWAKE, INSTRUCTION, RECORDING }
    private var teacherState = TeacherState.LISTENING
    private val operationToken = java.util.concurrent.atomic.AtomicInteger(0)
    private val voiceEpoch = VoiceEpoch()
    private val voiceRequestSequence = AtomicLong(0)
    private val voiceWindowLimiter = LatestVoiceWindowLimiter(MAX_VOICE_REQUESTS)
    private val voiceExecutor = Executors.newFixedThreadPool(MAX_VOICE_REQUESTS)
    private val voiceConnections = mutableMapOf<Long, HttpURLConnection>()
    private val voiceConnectionsLock = Any()
    @Volatile private var voiceRecognitionPaused = false
    private var wakeCheckScheduled = false
    private var continuousRestartScheduled = false
    private var continuousRestartAttempts = 0
    private var videoRecordingActive = false
    private var videoStartedAt: String? = null
    private var awakeChoiceIndex = 0
    private val awakeTimeoutRunnable = Runnable { handleAwakeTimeout() }
    private val templeSingleTapRunnable = Runnable {
        lastTempleKeyTapAt = 0L
        handleTempleSingleTap()
    }

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
    private var lastTempleTouchTapAt = 0L
    private var teacherConnectionReady = false
    private var teacherConnectionCheckInProgress = false
    private var teacherSessionStarted = false
    private var teacherExitInProgress = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.i("DeviceIdentity", "hardware_id=$teacherDeviceId")
        // Keep the voice-only teacher page in the foreground on RayNeo. If
        // the display sleeps, LockScreenActivity takes input focus and the
        // temple double-tap never reaches this Activity.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        window.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        setContentView(R.layout.activity_main)
        findViewById<View>(R.id.main).setBackgroundColor(Color.TRANSPARENT)

        gestureDetector = GestureDetector(this, object : GestureDetector.SimpleOnGestureListener() {
            override fun onDown(e: MotionEvent): Boolean = true
            override fun onSingleTapConfirmed(e: MotionEvent): Boolean {
                lastTempleTouchTapAt = android.os.SystemClock.uptimeMillis()
                mainHandler.removeCallbacks(templeSingleTapRunnable)
                lastTempleKeyTapAt = 0L
                handleTempleSingleTap()
                return true
            }
            override fun onDoubleTap(e: MotionEvent): Boolean {
                lastTempleTouchTapAt = android.os.SystemClock.uptimeMillis()
                mainHandler.removeCallbacks(templeSingleTapRunnable)
                lastTempleKeyTapAt = 0L
                exitTeacherApp()
                return true
            }
            override fun onFling(
                e1: MotionEvent?,
                e2: MotionEvent,
                velocityX: Float,
                velocityY: Float,
            ): Boolean {
                if (e1 == null || teacherState != TeacherState.AWAKE) return false
                val deltaX = e2.x - e1.x
                if (kotlin.math.abs(deltaX) < SWIPE_THRESHOLD ||
                    kotlin.math.abs(velocityX) < SWIPE_VELOCITY_THRESHOLD
                ) return false
                moveAwakeChoice(if (deltaX > 0) 1 else -1)
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

        if (teacherState == TeacherState.AWAKE && event.keyCode in setOf(
                KeyEvent.KEYCODE_DPAD_LEFT,
                KeyEvent.KEYCODE_DPAD_UP,
                KeyEvent.KEYCODE_PAGE_UP,
            )
        ) {
            moveAwakeChoice(-1)
            return true
        }
        if (teacherState == TeacherState.AWAKE && event.keyCode in setOf(
                KeyEvent.KEYCODE_DPAD_RIGHT,
                KeyEvent.KEYCODE_DPAD_DOWN,
                KeyEvent.KEYCODE_PAGE_DOWN,
                KeyEvent.KEYCODE_FORWARD,
            )
        ) {
            moveAwakeChoice(1)
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
            if (now - lastTempleTouchTapAt <= DOUBLE_TAP_WINDOW_MS) return true
            if (now - lastTempleKeyTapAt <= DOUBLE_TAP_WINDOW_MS) {
                mainHandler.removeCallbacks(templeSingleTapRunnable)
                lastTempleKeyTapAt = 0L
                exitTeacherApp()
            } else {
                lastTempleKeyTapAt = now
                mainHandler.removeCallbacks(templeSingleTapRunnable)
                mainHandler.postDelayed(templeSingleTapRunnable, DOUBLE_TAP_WINDOW_MS)
            }
            return true
        }
        return super.dispatchKeyEvent(event)
    }

    override fun onBackPressed() {
        exitTeacherApp()
    }

    private fun exitTeacherApp() {
        if (teacherExitInProgress) return
        teacherExitInProgress = true
        mainHandler.removeCallbacks(awakeTimeoutRunnable)
        mainHandler.removeCallbacks(templeSingleTapRunnable)
        advanceVoiceEpoch()
        operationToken.incrementAndGet()
        teacherState = TeacherState.INSTRUCTION
        val service = recordingService
        val hasActiveVideo = videoRecordingActive && service != null && serviceBound
        val videoStartedAtForUpload = videoStartedAt
        val videoEndedAtForUpload = UploadMetadata.nowIso()
        videoRecordingActive = false
        videoStartedAt = null

        if (hasActiveVideo) {
            val completed = AtomicBoolean(false)
            fun finishVideoCleanup(files: List<File>) {
                if (!completed.compareAndSet(false, true)) return
                if (files.isNotEmpty()) uploadRecordFiles(files, videoStartedAtForUpload, videoEndedAtForUpload)
                service.stopCaptureSessionIfIdle()
                service.stopTeacherSession()
            }
            // Keep media cleanup in the background; do not hold the Activity
            // on screen while AudioRecord/CameraX finish their callbacks.
            stopContinuousRecording {
                service.stopRecording { files -> finishVideoCleanup(files) }
                mainHandler.postDelayed({ finishVideoCleanup(service.getRecordedFiles()) }, 8_000L)
            }
        } else {
            stopContinuousRecording()
            stopTeacherService()
        }
        finishAndRemoveTask()
    }

    private fun stopTeacherService() {
        recordingService?.stopTeacherSession()
            ?: stopService(Intent(this, RecordingService::class.java))
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

    private fun handleTempleSingleTap() {
        if (!teacherConnectionReady) {
            checkTeacherConnection()
            return
        }
        when (teacherState) {
            TeacherState.LISTENING -> enterTeacherAwake()
            TeacherState.AWAKE -> handleInstructionIntent(
                org.json.JSONObject().put(
                    "intent",
                    if (awakeChoiceIndex == 0) "capture_photo" else "start_recording",
                ),
            )
            TeacherState.RECORDING -> stopTeacherRecording()
            TeacherState.INSTRUCTION -> Unit
        }
    }

    private fun enterTeacherAwake(pendingIntent: String? = null) {
        if (teacherExitInProgress || !teacherSessionStarted || teacherState != TeacherState.LISTENING) return
        advanceVoiceEpoch()
        val awakeToken = operationToken.incrementAndGet()
        teacherState = TeacherState.AWAKE
        awakeChoiceIndex = if (pendingIntent == "start_recording") 1 else 0
        voiceRecognitionPaused = true
        showAwakeChoices()
        playPrompt("wake_response") {
            if (teacherExitInProgress || teacherState != TeacherState.AWAKE ||
                operationToken.get() != awakeToken
            ) return@playPrompt
            voiceRecognitionPaused = false
            if (pendingIntent in setOf("capture_photo", "start_recording")) {
                handleInstructionIntent(org.json.JSONObject().put("intent", pendingIntent))
            } else {
                refreshAwakeTimeout()
                scheduleWakeCheck()
            }
        }
    }

    private fun showAwakeChoices() {
        hideAllViews()
        listOf(R.id.left_eye_panel, R.id.right_eye_panel).forEach { id ->
            val panel = findViewById<View>(id) ?: return@forEach
            panel.findViewById<android.widget.TextView>(R.id.title_text).apply {
                text = "化小北"
                visibility = View.VISIBLE
            }
            panel.findViewById<android.widget.TextView>(R.id.subtitle_text).apply {
                text = "请选择功能"
                visibility = View.VISIBLE
            }
            panel.findViewById<View>(R.id.options_row).visibility = View.VISIBLE
            panel.findViewById<android.widget.Button>(R.id.primary_button).apply {
                text = "拍照"
                alpha = if (awakeChoiceIndex == 0) 1.0f else 0.62f
                isSelected = awakeChoiceIndex == 0
            }
            panel.findViewById<android.widget.Button>(R.id.secondary_button).apply {
                text = "录像"
                alpha = if (awakeChoiceIndex == 1) 1.0f else 0.62f
                isSelected = awakeChoiceIndex == 1
            }
            panel.findViewById<android.widget.TextView>(R.id.status_text).apply {
                text = "左右滑动选择，单击确认；也可以直接说指令"
                visibility = View.VISIBLE
            }
        }
    }

    private fun moveAwakeChoice(delta: Int) {
        if (teacherState != TeacherState.AWAKE) return
        awakeChoiceIndex = if (delta == 0) awakeChoiceIndex else 1 - awakeChoiceIndex
        showAwakeChoices()
        refreshAwakeTimeout()
    }

    private fun refreshAwakeTimeout() {
        mainHandler.removeCallbacks(awakeTimeoutRunnable)
        if (teacherState == TeacherState.AWAKE) {
            mainHandler.postDelayed(awakeTimeoutRunnable, AWAKE_TIMEOUT_MS)
        }
    }

    private fun handleAwakeTimeout() {
        if (teacherState != TeacherState.AWAKE || teacherExitInProgress) return
        val timeoutToken = operationToken.incrementAndGet()
        advanceVoiceEpoch()
        voiceRecognitionPaused = true
        hideAllViews()
        teacherState = TeacherState.INSTRUCTION
        playPrompt("unclear") { resumeListening(timeoutToken) }
    }

    private fun clearAwakeControls() {
        mainHandler.removeCallbacks(awakeTimeoutRunnable)
        hideAllViews()
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
        startForegroundService(
            this,
            Intent(this, RecordingService::class.java)
                .setAction(RecordingService.ACTION_START_TEACHER_SESSION),
        )
        bindService(
            Intent(this, RecordingService::class.java), serviceConnection, Context.BIND_AUTO_CREATE
        )
        playPrompt("assistant_ready") {
            startContinuousRecording()
        }
    }

    // ===== 单路持续录音：写 wav + 维护最近 10 秒环形缓冲 =====
    @Suppress("MissingPermission")
    private fun startContinuousRecording() {
        if (teacherExitInProgress || continuousRunning) return
        if (continuousStopLatch != null) {
            if (!continuousRestartScheduled && continuousRestartAttempts < 40) {
                continuousRestartAttempts += 1
                continuousRestartScheduled = true
                mainHandler.postDelayed({
                    continuousRestartScheduled = false
                    if (!teacherExitInProgress && !continuousRunning) {
                        startContinuousRecording()
                    }
                }, 250L)
            }
            return
        }
        continuousRestartAttempts = 0
        continuousRunning = true
        val recorder = createRecorder()
        val stopped = CountDownLatch(1)
        continuousRecorder = recorder
        continuousStopLatch = stopped
        Thread {
            try {
                val minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
                val buffer = ShortArray(max(minBuf, 3200) / 2)
                recorder.startRecording()
                while (continuousRunning) {
                    val file = File(cacheDir, "teacher_session_${System.currentTimeMillis()}.wav")
                    val segmentStartedAt = UploadMetadata.nowIso()
                    continuousFile = file
                    continuousTotalSamples = 0
                    val segmentDeadline = System.currentTimeMillis() + CONTINUOUS_SEGMENT_MS
                    FileOutputStream(file).use { output ->
                        writeWavHeader(output, 0)
                        while (
                            continuousRunning && System.currentTimeMillis() < segmentDeadline
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
                    if (file.length() > 44) uploadRecordFiles(listOf(file), segmentStartedAt, UploadMetadata.nowIso())
                }
                recorder.stop()
            } catch (exc: Exception) {
                Log.e("TeacherAudio", "continuous recording stopped unexpectedly", exc)
            } finally {
                recorder.release()
                if (continuousRecorder === recorder) {
                    continuousRecorder = null
                    continuousRunning = false
                }
                if (continuousStopLatch === stopped) continuousStopLatch = null
                stopped.countDown()
            }
        }.start()
        scheduleWakeCheck()
    }

    private fun stopContinuousRecording(onStopped: (() -> Unit)? = null) {
        continuousRunning = false
        val recorder = continuousRecorder
        try {
            recorder?.stop()
        } catch (_: IllegalStateException) {
            // The recording thread may already have stopped the recorder.
        }
        val latch = continuousStopLatch
        if (latch == null) {
            onStopped?.invoke()
            return
        }
        Thread {
            try {
                latch.await(3, TimeUnit.SECONDS)
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
            }
            mainHandler.post { onStopped?.invoke() }
        }.start()
    }

    private fun appendRing(buffer: ShortArray, count: Int) {
        val bytes = ByteArray(count * 2)
        for (i in 0 until count) {
            val v = buffer[i].toInt()
            bytes[i * 2] = (v and 0xff).toByte()
            bytes[i * 2 + 1] = ((v shr 8) and 0xff).toByte()
        }
        ringBuffer.append(bytes)
    }

    private fun takeRingSnapshotSeconds(seconds: Int): ByteArray? {
        return ringBuffer.snapshot(SAMPLE_RATE * PCM_BYTES_PER_SAMPLE * seconds)
    }

    // ===== 无空档语音检测：每 2 秒取最近 5 秒 PCM，最多并发两个云端请求 =====
    private fun scheduleWakeCheck() {
        if (!continuousRunning) return
        if (voiceContextForCurrentState() == null) return
        if (continuousRecorder == null) {
            continuousRunning = false
            startContinuousRecording()
            return
        }
        if (wakeCheckScheduled) return
        wakeCheckScheduled = true
        mainHandler.postDelayed({
            wakeCheckScheduled = false
            val context = voiceContextForCurrentState()
            if (context == null || !continuousRunning) return@postDelayed
            if (!voiceRecognitionPaused) {
                val pcm = takeRingSnapshotSeconds(VOICE_WINDOW_SECONDS)
                if (pcm != null && PcmAudioGate.hasMeaningfulAudio(pcm)) {
                    enqueueVoiceWindow(pcm, context)
                }
            }
            scheduleWakeCheck()
        }, VOICE_WINDOW_STEP_MS)
    }

    private fun voiceContextForCurrentState(): String? = when (teacherState) {
        TeacherState.LISTENING -> "teacher_sleep"
        TeacherState.AWAKE -> "teacher_awake"
        TeacherState.RECORDING -> "teacher_recording"
        TeacherState.INSTRUCTION -> null
    }

    private fun enqueueVoiceWindow(pcm: ByteArray, context: String) {
        val window = TeacherVoiceWindow(
            epoch = voiceEpoch.current(),
            sequence = voiceRequestSequence.incrementAndGet(),
            context = context,
            pcm = pcm,
        )
        if (context == "teacher_awake") refreshAwakeTimeout()
        voiceWindowLimiter.offer(window)?.let(::submitVoiceWindow)
    }

    private fun submitVoiceWindow(window: TeacherVoiceWindow) {
        voiceExecutor.execute {
            val file = File(cacheDir, "teacher_voice_${window.sequence}.pcm")
            try {
                FileOutputStream(file).use { it.write(window.pcm) }
                val json = postMultipart(
                    "$baseUrl/api/glasses/voice/intent",
                    mapOf(
                        "model" to "qwen3.5-omni-flash-realtime",
                        "context" to window.context,
                        "demo_options" to "",
                    ),
                    "audio",
                    file,
                    "application/octet-stream",
                    onConnected = { connection ->
                        synchronized(voiceConnectionsLock) {
                            voiceConnections[window.sequence] = connection
                        }
                    },
                )
                mainHandler.post {
                    if (isCurrentVoiceWindow(window)) {
                        when (window.context) {
                            "teacher_sleep" -> handleWakeResponse(json)
                            "teacher_awake" -> handleInstructionIntent(json)
                            "teacher_recording" -> handleStopRecordingIntent(json)
                        }
                    }
                }
            } catch (exc: Exception) {
                if (voiceEpoch.accepts(window.epoch)) {
                    Log.w("TeacherVoice", "request ${window.sequence} failed: ${exc.message}")
                }
            } finally {
                synchronized(voiceConnectionsLock) {
                    voiceConnections.remove(window.sequence)
                }
                file.delete()
                voiceWindowLimiter.complete(window.sequence, voiceEpoch.current())
                    ?.let(::submitVoiceWindow)
            }
        }
    }

    private fun isCurrentVoiceWindow(window: TeacherVoiceWindow): Boolean {
        return !teacherExitInProgress && voiceEpoch.accepts(window.epoch) &&
            voiceContextForCurrentState() == window.context
    }

    private fun advanceVoiceEpoch(): Int {
        val epoch = voiceEpoch.advance()
        voiceWindowLimiter.discardPending()
        synchronized(voiceConnectionsLock) {
            voiceConnections.values.forEach { connection ->
                try { connection.disconnect() } catch (_: Exception) {}
            }
            voiceConnections.clear()
        }
        return epoch
    }

    private fun handleWakeResponse(json: org.json.JSONObject) {
        if (!json.optBoolean("wake", false)) return
        val intent = json.optString("intent", "").takeIf {
            it == "capture_photo" || it == "start_recording"
        }
        enterTeacherAwake(intent)
    }

    // 录像中检测到停止意图
    private fun handleStopRecordingIntent(json: org.json.JSONObject) {
        val intent = json.optString("intent", "unclear")
        if (intent == "stop_recording") {
            stopTeacherRecording()
        }
    }

    private fun stopTeacherRecording() {
        videoRecordingActive = false
        val videoStartedAtForUpload = videoStartedAt
        val videoEndedAtForUpload = UploadMetadata.nowIso()
        videoStartedAt = null
        teacherState = TeacherState.INSTRUCTION
        voiceRecognitionPaused = true
        advanceVoiceEpoch()
        val stopToken = operationToken.incrementAndGet()
        val service = recordingService
        if (service != null && serviceBound) {
            val completed = AtomicBoolean(false)
            fun completeStop(files: List<File>) {
                if (!completed.compareAndSet(false, true)) return
                if (files.isNotEmpty()) uploadRecordFiles(files, videoStartedAtForUpload, videoEndedAtForUpload)
                service.stopCaptureSessionIfIdle()
                resumeAfterCameraSettle(stopToken, "recording_stop")
            }
            service.stopRecording { files -> completeStop(files) }
            mainHandler.postDelayed({ completeStop(service.getRecordedFiles()) }, 8_000L)
        } else {
            resumeAfterCameraSettle(stopToken, "recording_stop")
        }
    }

    private fun handleInstructionIntent(json: org.json.JSONObject) {
        val intent = json.optString("intent", "unclear")
        if (intent !in setOf("capture_photo", "start_recording", "stop_recording")) {
            return
        }
        clearAwakeControls()
        voiceRecognitionPaused = true
        advanceVoiceEpoch()
        val actionToken = operationToken.incrementAndGet()
        when (intent) {
            "capture_photo" -> {
                teacherState = TeacherState.INSTRUCTION
                if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                    != PackageManager.PERMISSION_GRANTED
                ) {
                    requestCameraPermission.launch(Manifest.permission.CAMERA)
                    resumeListening(actionToken); return
                }
                playPrompt("start_capture")
                withService(actionToken, action = { service ->
                    val captureCompleted = AtomicBoolean(false)
                    fun completeCapture(file: File?, error: Throwable?) {
                        if (!captureCompleted.compareAndSet(false, true)) return
                        if (file != null) {
                            uploadTeacherPhoto(file)
                        } else {
                            Log.w("TeacherPhoto", "capture failed: ${error?.message}")
                        }
                        service.stopCaptureSessionIfIdle()
                        resumeAfterCameraSettle(actionToken)
                    }
                    service.captureStillImage { file, error -> completeCapture(file, error) }
                    mainHandler.postDelayed({
                        completeCapture(null, IOException("拍照超时"))
                    }, PHOTO_CAPTURE_TIMEOUT_MS)
                })
            }
            "start_recording" -> {
                teacherState = TeacherState.INSTRUCTION
                playPrompt("recording_start") {
                    if (teacherExitInProgress || operationToken.get() != actionToken) return@playPrompt
                    startForegroundService(
                        this, Intent(this, RecordingService::class.java)
                            .setAction(RecordingService.ACTION_START_RECORDING)
                            .putExtra(RecordingService.EXTRA_VIDEO_AUDIO_ENABLED, false)
                    )
                    // 进入录像状态：恢复持续录音，唤醒检测切到检测"停止录像"
                    videoRecordingActive = true
                    videoStartedAt = UploadMetadata.nowIso()
                    teacherState = TeacherState.RECORDING
                    voiceRecognitionPaused = false
                    advanceVoiceEpoch()
                    scheduleWakeCheck()
                }
            }
            "stop_recording" -> {
                stopTeacherRecording()
            }
        }
    }

    private fun resumeListening() {
        resumeListening(null)
    }

    private fun resumeListening(expectedToken: Int?) {
        if (teacherExitInProgress) return
        if (expectedToken != null && operationToken.get() != expectedToken) return
        mainHandler.removeCallbacks(awakeTimeoutRunnable)
        hideAllViews()
        teacherState = TeacherState.LISTENING
        voiceRecognitionPaused = false
        advanceVoiceEpoch()
        if (!continuousRunning) startContinuousRecording()
        else scheduleWakeCheck()
    }

    private fun resumeAfterCameraSettle(token: Int, prompt: String? = null) {
        mainHandler.postDelayed({
            runOnUiIfCurrent(token) {
                if (prompt == null) {
                    resumeListening(token)
                } else {
                    playPrompt(prompt) { resumeListening(token) }
                }
            }
        }, CAMERA_SETTLE_DELAY_MS)
    }

    private fun withService(
        token: Int,
        action: (RecordingService) -> Unit,
        deadline: Long = android.os.SystemClock.uptimeMillis() + SERVICE_WAIT_TIMEOUT_MS,
    ) {
        if (teacherExitInProgress || operationToken.get() != token) return
        val s = recordingService
        if (s != null && serviceBound) { action(s); return }
        if (android.os.SystemClock.uptimeMillis() >= deadline) {
            Log.w("TeacherCamera", "recording service bind timed out")
            resumeListening(token)
            return
        }
        mainHandler.postDelayed({ withService(token, action, deadline) }, 200L)
    }

    // ===== 退出：停录音 + 上传持续录音 wav =====
    private fun uploadRecordFiles(
        files: List<File>,
        startedAt: String? = null,
        endedAt: String? = null,
    ) {
        Thread {
            for (file in files) {
                val isAudio = file.extension.lowercase() in listOf("wav", "m4a", "aac", "mp3")
                val field = if (isAudio) "audio" else "video"
                val mime = if (isAudio) "audio/wav" else "video/mp4"
                try {
                    val fields = linkedMapOf(
                        "device_id" to teacherDeviceId,
                        "experiment_type" to "teacher",
                    )
                    startedAt?.let { fields["started_at"] = it }
                    endedAt?.let { fields["ended_at"] = it }
                    postMultipart(
                        "$baseUrl/api/glasses/teacher/records/upload",
                        fields,
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
                        "device_id" to teacherDeviceId,
                        "experiment_type" to "teacher",
                        "captured_at" to UploadMetadata.nowIso(),
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

    private fun runOnUiIfCurrent(token: Int, action: () -> Unit) {
        mainHandler.post {
            if (!teacherExitInProgress && operationToken.get() == token) action()
        }
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
        mainHandler.removeCallbacks(awakeTimeoutRunnable)
        mainHandler.removeCallbacks(templeSingleTapRunnable)
        advanceVoiceEpoch()
        voiceExecutor.shutdownNow()
        continuousRunning = false
        try {
            continuousRecorder?.stop()
        } catch (_: IllegalStateException) {
            // Ignore an already stopped recorder during Activity teardown.
        }
        // exitTeacherApp owns cleanup once exit has started. Do not stop the
        // service a second time from Activity teardown while CameraX is saving.
        if (!teacherExitInProgress) {
            val videoStartedAtForUpload = videoStartedAt
            val videoEndedAtForUpload = UploadMetadata.nowIso()
            videoStartedAt = null
            if (videoRecordingActive) {
                recordingService?.stopRecording { files ->
                    if (files.isNotEmpty()) uploadRecordFiles(files, videoStartedAtForUpload, videoEndedAtForUpload)
                    recordingService?.stopCaptureSessionIfIdle()
                    recordingService?.stopTeacherSession()
                    stopService(Intent(this, RecordingService::class.java))
                } ?: stopService(Intent(this, RecordingService::class.java))
            } else {
                recordingService?.stopTeacherSession()
            }
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
        onConnected: ((HttpURLConnection) -> Unit)? = null,
    ): org.json.JSONObject {
        val boundary = "----TeacherBoundary${System.currentTimeMillis()}"
        val connection = (URL(urlText).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"; doOutput = true
            connectTimeout = 10_000; readTimeout = 30_000
            setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
        }
        onConnected?.invoke(connection)
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
        private const val PCM_BYTES_PER_SAMPLE = 2
        private const val RING_SECONDS = 10
        private const val VOICE_WINDOW_SECONDS = 5
        private const val VOICE_WINDOW_STEP_MS = 2_000L
        private const val MAX_VOICE_REQUESTS = 2
        private const val CONTINUOUS_SEGMENT_MS = 120_000L
        private const val CAMERA_SETTLE_DELAY_MS = 700L
        private const val PHOTO_CAPTURE_TIMEOUT_MS = 5_000L
        private const val SERVICE_WAIT_TIMEOUT_MS = 5_000L
        private const val AWAKE_TIMEOUT_MS = 8_000L
        private const val DOUBLE_TAP_WINDOW_MS = 500L
        private const val SWIPE_THRESHOLD = 40
        private const val SWIPE_VELOCITY_THRESHOLD = 40

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
