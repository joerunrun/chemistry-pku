package com.example.zrgfirstapp

import android.Manifest
import android.annotation.SuppressLint
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Base64
import android.view.GestureDetector
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
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
import kotlin.math.abs
import kotlin.math.max

class MainActivity : AppCompatActivity() {
    private enum class ScreenState {
        WELCOME,
        CONNECTION_CHECK,
        CONNECTION_FAILED,
        CONSENT,
        HOME,
        EXPERIMENT_SELECT,
        MORE_EXPERIMENT_SELECT,
        LIVE_MINIMAL,
        ACTION_MENU,
        CAPTURE_COUNTDOWN,
        QA_RECORDING,
        RUNNING,
        RESULT,
    }

    private enum class Mode {
        EXPERIMENT,
        SAFETY,
        QA,
    }

    private enum class ExperimentType {
        DISTILLATION,
        PHOTOMETER,
        ELECTROLYSIS,
    }

    private enum class CameraPermissionAction {
        NONE,
        START_RECORDING,
        CAPTURE,
    }

    private data class EyePanel(
        val root: View,
        val titleText: TextView,
        val subtitleText: TextView,
        val optionsRow: LinearLayout,
        val primaryButton: Button,
        val secondaryButton: Button,
        val detectionPreview: ImageView,
        val progress: ProgressBar,
        val thinkingText: TextView,
        val statusText: TextView,
        val resultText: TextView,
    )

    private lateinit var leftPanel: EyePanel
    private lateinit var rightPanel: EyePanel
    private lateinit var mainRoot: View
    private val panels: List<EyePanel>
        get() = listOf(leftPanel, rightPanel)

    private val connectionMode: String by lazy {
        getString(R.string.glasses_connection_mode).trim().lowercase()
    }
    private val baseUrl: String by lazy {
        val urlRes = if (connectionMode == "network") {
            R.string.glasses_service_base_url_network
        } else {
            R.string.glasses_service_base_url_usb
        }
        getString(urlRes).trim().trimEnd('/')
    }
    private val connectionLabel: String by lazy {
        if (connectionMode == "network") "网络连接" else "USB 数据线连接"
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private lateinit var gestureDetector: GestureDetector
    private val qaSampleRate = 16_000
    private val qaRecordDurationMs = 7_000L
    private val actionMenuTimeoutMs = 8_000L
    private val captureCountdownMs = 2_500L

    private var screenState = ScreenState.WELCOME
    private var focusIndex = 0
    private var selectedMode = Mode.EXPERIMENT
    private var selectedExperimentType = ExperimentType.DISTILLATION
    private var shownPreviewDataUrl: String? = null
    private var thinkingFrame = 0
    private var operationToken = 0
    private var activeTaskTitle = ""
    private var lastStatusText = "前后滑动选择，单击确认"
    private var lastResultText = ""
    private var pendingCameraPermissionAction = CameraPermissionAction.NONE
    private var recordingRequested = false
    private var recordingStatusText = "未录像"
    private var connectionStatusText = "未测试"
    private var qaAudioSupported = false
    private var recordingService: RecordingService? = null
    private var serviceBound = false
    private var audioRecord: AudioRecord? = null
    private var isAudioRecording = false
    private var recognizedQuestion = ""
    private val pendingServiceActions = mutableListOf<(RecordingService) -> Unit>()
    private val actionMenuTimeoutRunnable = Runnable {
        if (screenState == ScreenState.ACTION_MENU) {
            enterLiveMinimal()
        }
    }
    private val recordingStatusRunnable = object : Runnable {
        override fun run() {
            val service = recordingService
            if (recordingRequested && service != null) {
                val status = service.recordingStatus()
                if (status.isNotBlank() && status != recordingStatusText) {
                    recordingStatusText = status
                    if (screenState != ScreenState.RUNNING && screenState != ScreenState.RESULT) {
                        renderScreen()
                    }
                }
                mainHandler.postDelayed(this, 1_000L)
            }
        }
    }

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            val service = (binder as RecordingService.LocalBinder).service()
            recordingService = service
            serviceBound = true
            recordingStatusText = service.recordingStatus()
            startRecordingStatusSync()
            val actions = pendingServiceActions.toList()
            pendingServiceActions.clear()
            actions.forEach { it.invoke(service) }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            recordingService = null
            serviceBound = false
            if (recordingRequested) {
                recordingStatusText = "录像异常"
                renderScreen()
            }
        }
    }

    private val welcomeRunnable = Runnable {
        if (screenState == ScreenState.WELCOME) {
            startInitialConnectionCheck()
        }
    }

    private val thinkingRunnable = object : Runnable {
        override fun run() {
            if (!::leftPanel.isInitialized || screenState !in setOf(
                    ScreenState.CONNECTION_CHECK,
                    ScreenState.CAPTURE_COUNTDOWN,
                    ScreenState.QA_RECORDING,
                    ScreenState.RUNNING,
                )
            ) {
                return
            }

            val text = when (thinkingFrame % 4) {
                0 -> "思考中."
                1 -> "思考中.."
                else -> "思考中..."
            }
            panels.forEach { it.thinkingText.text = text }
            thinkingFrame += 1
            mainHandler.postDelayed(this, 500L)
        }
    }

    private val requestCameraPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        val action = pendingCameraPermissionAction
        pendingCameraPermissionAction = CameraPermissionAction.NONE
        if (!granted) {
            if (action == CameraPermissionAction.START_RECORDING) {
                recordingRequested = false
                recordingStatusText = "未授权录像"
                renderScreen()
            } else {
                showResult("未授予相机权限", "权限不足")
            }
            return@registerForActivityResult
        }

        when (action) {
            CameraPermissionAction.START_RECORDING -> startRecordingService()
            CameraPermissionAction.CAPTURE -> captureViaCameraService()
            CameraPermissionAction.NONE -> Unit
        }
    }

    private val requestAudioPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            startAudioQuestionRecording()
        } else {
            showResult("未授予麦克风权限，无法录音提问。", "权限不足")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        window.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        setContentView(R.layout.activity_main)

        mainRoot = findViewById(R.id.main)
        bindViews()
        gestureDetector = GestureDetector(this, TempleGestureListener())
        renderScreen()
        mainHandler.postDelayed(welcomeRunnable, 5_000L)

        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.main)) { v, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(systemBars.left, systemBars.top, systemBars.right, systemBars.bottom)
            insets
        }
    }

    private fun bindViews() {
        leftPanel = bindPanel(findViewById(R.id.left_eye_panel))
        rightPanel = bindPanel(findViewById(R.id.right_eye_panel))
    }

    private fun bindPanel(root: View): EyePanel {
        return EyePanel(
            root = root,
            titleText = root.findViewById(R.id.title_text),
            subtitleText = root.findViewById(R.id.subtitle_text),
            optionsRow = root.findViewById(R.id.options_row),
            primaryButton = root.findViewById(R.id.primary_button),
            secondaryButton = root.findViewById(R.id.secondary_button),
            detectionPreview = root.findViewById(R.id.detection_preview),
            progress = root.findViewById(R.id.progress),
            thinkingText = root.findViewById(R.id.thinking_text),
            statusText = root.findViewById(R.id.status_text),
            resultText = root.findViewById(R.id.result_text),
        )
    }

    private fun renderScreen() {
        when (screenState) {
            ScreenState.WELCOME -> renderWelcomeScreen()

            ScreenState.CONNECTION_CHECK -> renderProcessScreen(
                title = "连接电脑服务",
                subtitle = "正在连接",
                status = lastStatusText,
                result = lastResultText,
                running = true,
            )

            ScreenState.CONNECTION_FAILED -> renderChoiceScreen(
                title = "连接失败",
                subtitle = "电脑服务未连接",
                primary = "重试连接",
                secondary = "继续设置",
                status = "请先确认电脑服务已启动",
                result = lastResultText.ifBlank { "双击可退出，继续设置后仍可稍后重试。" },
            )

            ScreenState.CONSENT -> renderChoiceScreen(
                title = "化学实验识别",
                subtitle = "录像许可",
                primary = "允许后台录像",
                secondary = "暂不录像",
                status = "前后滑动选择，单击确认",
                result = "连接状态：$connectionStatusText\n录像仅保存在本机，用于实验过程记录。",
            )

            ScreenState.HOME -> renderChoiceScreen(
                title = "实验设置",
                subtitle = "选择检测类型",
                primary = "实验纠错",
                secondary = "安全检查",
                status = "前后滑动选择，单击确认",
                result = liveSummary(),
            )

            ScreenState.EXPERIMENT_SELECT -> renderChoiceScreen(
                title = "实验纠错",
                subtitle = "选择实验",
                primary = "减压蒸馏",
                secondary = "更多实验",
                status = "选择后进入实验中界面",
                result = liveSummary(),
            )

            ScreenState.MORE_EXPERIMENT_SELECT -> renderChoiceScreen(
                title = "更多实验",
                subtitle = "选择实验",
                primary = "光度计",
                secondary = "电解",
                status = "双击返回上一层",
                result = liveSummary(),
            )

            ScreenState.LIVE_MINIMAL -> renderLiveMinimalScreen()

            ScreenState.ACTION_MENU -> renderChoiceScreen(
                title = "实验助手",
                subtitle = "选择现场功能",
                primary = "装置检测",
                secondary = "语音问答",
                status = "前后滑动选择，单击确认",
                result = "8 秒无操作自动收起\n${liveSummary()}",
            )

            ScreenState.CAPTURE_COUNTDOWN -> renderProcessScreen(
                title = "装置检测",
                subtitle = "准备拍照",
                status = lastStatusText,
                result = lastResultText,
                running = true,
            )

            ScreenState.QA_RECORDING -> renderProcessScreen(
                title = "语音问答",
                subtitle = "正在录音",
                status = lastStatusText,
                result = lastResultText,
                running = true,
            )

            ScreenState.RUNNING -> renderProcessScreen(
                title = activeTaskTitle.ifBlank { selectedTitle() },
                subtitle = "正在处理",
                status = lastStatusText,
                result = lastResultText,
                running = true,
            )

            ScreenState.RESULT -> renderProcessScreen(
                title = activeTaskTitle.ifBlank { selectedTitle() },
                subtitle = "处理结果",
                status = lastStatusText,
                result = lastResultText,
                running = false,
            )
        }
    }

    private fun renderWelcomeScreen() {
        setTransparentChrome(false)
        panels.forEach { panel ->
            panel.titleText.text = "化学实验识别"
            panel.titleText.textSize = 30f
            panel.subtitleText.text = "智能眼镜实验辅助"
            panel.optionsRow.visibility = View.GONE
            panel.progress.visibility = View.VISIBLE
            panel.thinkingText.visibility = View.GONE
            panel.detectionPreview.visibility = View.GONE
            panel.statusText.text = "欢迎大家！"
            panel.resultText.text = "实验纠错  |  语音问答"
        }
    }

    private fun renderLiveMinimalScreen() {
        stopThinking()
        setTransparentChrome(true)
        panels.forEach { panel ->
            panel.titleText.textSize = 18f
            panel.titleText.text = "助手就绪"
            panel.subtitleText.text = selectedTitle()
            panel.optionsRow.visibility = View.GONE
            panel.progress.visibility = View.GONE
            panel.thinkingText.visibility = View.GONE
            panel.detectionPreview.visibility = View.GONE
            panel.statusText.text = "单击唤出菜单"
            panel.resultText.text = ""
            panel.resultText.visibility = View.GONE
        }
    }

    private fun renderChoiceScreen(
        title: String,
        subtitle: String,
        primary: String,
        secondary: String,
        status: String,
        result: String,
    ) {
        stopThinking()
        setTransparentChrome(false)
        panels.forEach { panel ->
            panel.titleText.textSize = 26f
            panel.titleText.text = title
            panel.subtitleText.text = subtitle
            panel.optionsRow.visibility = View.VISIBLE
            panel.primaryButton.text = primary
            panel.secondaryButton.text = secondary
            panel.primaryButton.isEnabled = true
            panel.secondaryButton.isEnabled = true
            panel.primaryButton.isSelected = focusIndex == 0
            panel.secondaryButton.isSelected = focusIndex == 1
            panel.primaryButton.alpha = if (focusIndex == 0) 1.0f else 0.62f
            panel.secondaryButton.alpha = if (focusIndex == 1) 1.0f else 0.62f
            panel.progress.visibility = View.GONE
            panel.thinkingText.visibility = View.GONE
            panel.detectionPreview.visibility = View.GONE
            panel.statusText.text = status
            panel.resultText.visibility = View.VISIBLE
            panel.resultText.text = result
        }
    }

    private fun renderProcessScreen(
        title: String,
        subtitle: String,
        status: String,
        result: String,
        running: Boolean,
    ) {
        setTransparentChrome(false)
        panels.forEach { panel ->
            panel.titleText.textSize = 26f
            panel.titleText.text = title
            panel.subtitleText.text = subtitle
            panel.optionsRow.visibility = View.GONE
            panel.primaryButton.alpha = 1.0f
            panel.secondaryButton.alpha = 1.0f
            panel.progress.visibility = if (running && panel.detectionPreview.visibility != View.VISIBLE) {
                View.VISIBLE
            } else {
                View.GONE
            }
            panel.thinkingText.visibility = if (running) View.VISIBLE else View.GONE
            panel.statusText.text = status
            panel.resultText.visibility = View.VISIBLE
            panel.resultText.text = result
        }
        if (running) {
            startThinking()
        } else {
            stopThinking()
        }
    }

    private fun setTransparentChrome(transparent: Boolean) {
        if (!::mainRoot.isInitialized) {
            return
        }
        mainRoot.setBackgroundColor(if (transparent) Color.TRANSPARENT else Color.rgb(32, 33, 36))
        panels.forEach { panel ->
            panel.root.setBackgroundColor(Color.TRANSPARENT)
        }
    }

    private fun selectedTitle(): String {
        return when (selectedMode) {
            Mode.SAFETY -> "安全检查"
            Mode.QA -> "语音问答"
            Mode.EXPERIMENT -> when (selectedExperimentType) {
                ExperimentType.DISTILLATION -> "减压蒸馏纠错"
                ExperimentType.PHOTOMETER -> "光度计纠错"
                ExperimentType.ELECTROLYSIS -> "电解纠错"
            }
        }
    }

    private fun currentSelectionText(): String {
        val selection = when (selectedMode) {
            Mode.SAFETY -> "当前模式：安全检查"
            Mode.QA -> "当前模式：语音问答"
            Mode.EXPERIMENT -> "当前实验：${experimentLabel()}"
        }
        return "$selection\n${recordingSummary()}\n连接方式：$connectionLabel"
    }

    private fun liveSummary(): String {
        return "${currentSelectionText()}\n连接状态：$connectionStatusText"
    }

    private fun recordingSummary(): String {
        return "录像状态：$recordingStatusText"
    }

    private fun experimentLabel(): String {
        return when (selectedExperimentType) {
            ExperimentType.DISTILLATION -> "减压蒸馏"
            ExperimentType.PHOTOMETER -> "光度计"
            ExperimentType.ELECTROLYSIS -> "电解"
        }
    }

    private fun moveFocus(delta: Int) {
        if (!screenState.hasChoices()) {
            return
        }
        focusIndex = if (delta >= 0) 1 - focusIndex else 1 - focusIndex
        renderScreen()
    }

    private fun activateFocusedChoice() {
        when (screenState) {
            ScreenState.WELCOME -> Unit

            ScreenState.CONNECTION_CHECK -> Unit

            ScreenState.CONNECTION_FAILED -> {
                if (focusIndex == 0) {
                    startInitialConnectionCheck()
                } else {
                    focusIndex = 0
                    screenState = ScreenState.CONSENT
                    renderScreen()
                }
            }

            ScreenState.CONSENT -> {
                if (focusIndex == 0) {
                    recordingRequested = true
                    recordingStatusText = "正在申请录像权限"
                    enterSetup()
                    ensureCameraPermission(CameraPermissionAction.START_RECORDING)
                } else {
                    recordingRequested = false
                    recordingStatusText = "未录像"
                    enterSetup()
                }
            }

            ScreenState.HOME -> {
                val selectedIndex = focusIndex
                focusIndex = 0
                if (selectedIndex == 0) {
                    selectedMode = Mode.EXPERIMENT
                    screenState = ScreenState.EXPERIMENT_SELECT
                } else {
                    selectedMode = Mode.SAFETY
                    enterLiveMinimal()
                    return
                }
                renderScreen()
            }

            ScreenState.EXPERIMENT_SELECT -> {
                if (focusIndex == 0) {
                    selectedMode = Mode.EXPERIMENT
                    selectedExperimentType = ExperimentType.DISTILLATION
                    enterLiveMinimal()
                } else {
                    focusIndex = 0
                    screenState = ScreenState.MORE_EXPERIMENT_SELECT
                    renderScreen()
                }
            }

            ScreenState.MORE_EXPERIMENT_SELECT -> {
                selectedMode = Mode.EXPERIMENT
                selectedExperimentType = if (focusIndex == 0) {
                    ExperimentType.PHOTOMETER
                } else {
                    ExperimentType.ELECTROLYSIS
                }
                enterLiveMinimal()
            }

            ScreenState.LIVE_MINIMAL -> {
                showActionMenu()
            }

            ScreenState.ACTION_MENU -> {
                mainHandler.removeCallbacks(actionMenuTimeoutRunnable)
                if (focusIndex == 0) {
                    startCaptureCountdown()
                } else {
                    startVoiceQuestionFlow()
                }
            }

            ScreenState.CAPTURE_COUNTDOWN,
            ScreenState.QA_RECORDING -> Unit

            ScreenState.RUNNING,
            ScreenState.RESULT -> Unit
        }
    }

    private fun enterSetup() {
        focusIndex = 0
        clearTaskOutput()
        screenState = ScreenState.HOME
        renderScreen()
    }

    private fun enterLiveMinimal() {
        mainHandler.removeCallbacks(actionMenuTimeoutRunnable)
        focusIndex = 0
        clearTaskOutput()
        screenState = ScreenState.LIVE_MINIMAL
        renderScreen()
    }

    private fun showActionMenu() {
        focusIndex = 0
        clearTaskOutput()
        screenState = ScreenState.ACTION_MENU
        renderScreen()
        mainHandler.removeCallbacks(actionMenuTimeoutRunnable)
        mainHandler.postDelayed(actionMenuTimeoutRunnable, actionMenuTimeoutMs)
    }

    private fun handleBackGesture() {
        operationToken += 1
        when (screenState) {
            ScreenState.WELCOME,
            ScreenState.CONNECTION_CHECK,
            ScreenState.CONNECTION_FAILED,
            ScreenState.CONSENT,
            ScreenState.HOME,
            ScreenState.LIVE_MINIMAL -> {
                stopRecordingService()
                finish()
            }
            ScreenState.EXPERIMENT_SELECT -> {
                focusIndex = 0
                screenState = ScreenState.HOME
                clearTaskOutput()
                renderScreen()
            }
            ScreenState.MORE_EXPERIMENT_SELECT -> {
                focusIndex = 1
                screenState = ScreenState.EXPERIMENT_SELECT
                clearTaskOutput()
                renderScreen()
            }
            ScreenState.ACTION_MENU -> {
                enterLiveMinimal()
            }
            ScreenState.CAPTURE_COUNTDOWN -> {
                enterLiveMinimal()
            }
            ScreenState.QA_RECORDING -> {
                stopAudioQuestionRecording()
                enterLiveMinimal()
            }
            ScreenState.RUNNING,
            ScreenState.RESULT -> {
                focusIndex = 0
                clearTaskOutput()
                enterLiveMinimal()
            }
        }
    }

    private fun ScreenState.hasChoices(): Boolean {
        return this == ScreenState.CONNECTION_FAILED ||
            this == ScreenState.CONSENT ||
            this == ScreenState.HOME ||
            this == ScreenState.EXPERIMENT_SELECT ||
            this == ScreenState.MORE_EXPERIMENT_SELECT ||
            this == ScreenState.ACTION_MENU
    }

    private fun clearTaskOutput() {
        shownPreviewDataUrl = null
        recognizedQuestion = ""
        activeTaskTitle = ""
        lastStatusText = "前后滑动选择，单击确认"
        lastResultText = ""
        panels.forEach { panel ->
            panel.detectionPreview.setImageDrawable(null)
            panel.detectionPreview.visibility = View.GONE
        }
    }

    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        if (::gestureDetector.isInitialized && gestureDetector.onTouchEvent(event)) {
            return true
        }
        return super.dispatchTouchEvent(event)
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.action == KeyEvent.ACTION_UP) {
            when (event.keyCode) {
                KeyEvent.KEYCODE_DPAD_RIGHT,
                KeyEvent.KEYCODE_DPAD_DOWN,
                KeyEvent.KEYCODE_PAGE_DOWN,
                KeyEvent.KEYCODE_FORWARD -> {
                    moveFocus(1)
                    return true
                }

                KeyEvent.KEYCODE_DPAD_LEFT,
                KeyEvent.KEYCODE_DPAD_UP,
                KeyEvent.KEYCODE_PAGE_UP -> {
                    moveFocus(-1)
                    return true
                }

                KeyEvent.KEYCODE_ENTER,
                KeyEvent.KEYCODE_DPAD_CENTER,
                KeyEvent.KEYCODE_SPACE -> {
                    activateFocusedChoice()
                    return true
                }

                KeyEvent.KEYCODE_BACK -> {
                    handleBackGesture()
                    return true
                }
            }
        }
        return super.dispatchKeyEvent(event)
    }

    private inner class TempleGestureListener : GestureDetector.SimpleOnGestureListener() {
        private val swipeThreshold = 40
        private val velocityThreshold = 40

        override fun onDown(e: MotionEvent): Boolean = true

        override fun onSingleTapConfirmed(e: MotionEvent): Boolean {
            activateFocusedChoice()
            return true
        }

        override fun onDoubleTap(e: MotionEvent): Boolean {
            handleBackGesture()
            return true
        }

        override fun onFling(
            e1: MotionEvent?,
            e2: MotionEvent,
            velocityX: Float,
            velocityY: Float,
        ): Boolean {
            if (e1 == null || !screenState.hasChoices()) {
                return false
            }

            val diffX = e2.x - e1.x
            val diffY = e2.y - e1.y
            val horizontal = abs(diffX) >= abs(diffY)
            if (horizontal && abs(diffX) > swipeThreshold && abs(velocityX) > velocityThreshold) {
                moveFocus(if (diffX < 0) 1 else -1)
                return true
            }

            if (!horizontal && abs(diffY) > swipeThreshold && abs(velocityY) > velocityThreshold) {
                moveFocus(if (diffY < 0) 1 else -1)
                return true
            }

            return false
        }
    }

    private fun startVoiceQuestionFlow() {
        clearTaskOutput()
        operationToken += 1
        activeTaskTitle = "语音问答"
        if (!qaAudioSupported) {
            showResult(
                "当前电脑端服务不支持语音问答音频接口。请停止旧服务后重新启动：python -m pipeline.web.app --host 0.0.0.0 --port 5080",
                "服务版本过旧",
            )
            return
        }
        lastStatusText = "正在准备录音..."
        lastResultText = "请在提示后说出你的问题。"
        screenState = ScreenState.QA_RECORDING
        renderScreen()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            == PackageManager.PERMISSION_GRANTED
        ) {
            startAudioQuestionRecording()
        } else {
            requestAudioPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    @SuppressLint("MissingPermission")
    private fun startAudioQuestionRecording() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestAudioPermission.launch(Manifest.permission.RECORD_AUDIO)
            return
        }

        stopAudioQuestionRecording()
        val token = operationToken
        val minBufferSize = AudioRecord.getMinBufferSize(
            qaSampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBufferSize <= 0) {
            showResult("当前设备无法初始化麦克风录音。", "录音失败")
            return
        }

        val bufferSize = max(minBufferSize, 3200)
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            qaSampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufferSize,
        )
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            showResult("麦克风初始化失败，请稍后重试。", "录音失败")
            return
        }

        audioRecord = recorder
        isAudioRecording = true
        val outputFile = File(cacheDir, "qa_audio_${System.currentTimeMillis()}.pcm")
        Thread {
            var bytesWritten = 0
            try {
                FileOutputStream(outputFile).use { output ->
                    recorder.startRecording()
                    runOnUiIfCurrent(token) {
                        lastStatusText = "正在录音"
                        lastResultText = "请说出你的问题，录音约 7 秒。"
                        renderScreen()
                    }
                    val buffer = ByteArray(bufferSize)
                    val deadline = System.currentTimeMillis() + qaRecordDurationMs
                    while (
                        token == operationToken &&
                        isAudioRecording &&
                        System.currentTimeMillis() < deadline
                    ) {
                        val read = recorder.read(buffer, 0, buffer.size)
                        if (read > 0) {
                            output.write(buffer, 0, read)
                            bytesWritten += read
                        }
                    }
                }
                if (token == operationToken && bytesWritten > 0) {
                    runOnUiIfCurrent(token) {
                        lastStatusText = "正在理解你的问题..."
                        lastResultText = ""
                        screenState = ScreenState.RUNNING
                        renderScreen()
                    }
                    submitAudioQuestion(outputFile, token)
                } else if (token == operationToken) {
                    runOnUiIfCurrent(token) {
                        showResult("没有录到有效语音，请重新提问。", "录音失败")
                    }
                }
            } catch (exc: Exception) {
                if (token == operationToken) {
                    runOnUiIfCurrent(token) {
                        showResult(exc.message ?: "录音失败", "录音失败")
                    }
                }
            } finally {
                try {
                    recorder.stop()
                } catch (_: IllegalStateException) {
                    // Recorder may already be stopped during cancellation.
                }
                recorder.release()
                if (audioRecord === recorder) {
                    audioRecord = null
                }
                isAudioRecording = false
            }
        }.start()
    }

    private fun stopAudioQuestionRecording() {
        isAudioRecording = false
        audioRecord?.let { recorder ->
            try {
                recorder.stop()
            } catch (_: IllegalStateException) {
                // Ignore invalid stop calls during lifecycle cancellation.
            }
        }
        audioRecord = null
    }

    private fun ensureCameraPermission(action: CameraPermissionAction) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED
        ) {
            when (action) {
                CameraPermissionAction.START_RECORDING -> startRecordingService()
                CameraPermissionAction.CAPTURE -> captureViaCameraService()
                CameraPermissionAction.NONE -> Unit
            }
        } else {
            pendingCameraPermissionAction = action
            requestCameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    private fun startRecordingService() {
        recordingRequested = true
        recordingStatusText = "录像准备中"
        renderScreen()
        val intent = Intent(this, RecordingService::class.java).setAction(RecordingService.ACTION_START_RECORDING)
        ContextCompat.startForegroundService(this, intent)
        withRecordingService { service ->
            service.startRecording()
            recordingStatusText = service.recordingStatus()
            startRecordingStatusSync()
            renderScreen()
        }
    }

    private fun stopRecordingService() {
        recordingRequested = false
        recordingStatusText = "未录像"
        mainHandler.removeCallbacks(recordingStatusRunnable)
        recordingService?.stopRecording()
        if (serviceBound) {
            unbindService(serviceConnection)
            serviceBound = false
            recordingService = null
        }
        stopService(Intent(this, RecordingService::class.java).setAction(RecordingService.ACTION_STOP_RECORDING))
    }

    private fun startRecordingStatusSync() {
        mainHandler.removeCallbacks(recordingStatusRunnable)
        mainHandler.post(recordingStatusRunnable)
    }

    private fun withRecordingService(action: (RecordingService) -> Unit) {
        val service = recordingService
        if (service != null && serviceBound) {
            action(service)
            return
        }
        pendingServiceActions += action
        bindService(
            Intent(this, RecordingService::class.java),
            serviceConnection,
            Context.BIND_AUTO_CREATE,
        )
    }

    private fun startInitialConnectionCheck() {
        val token = operationToken + 1
        operationToken = token
        focusIndex = 0
        lastStatusText = "正在测试电脑服务连接..."
        lastResultText = "请稍候，默认使用 $connectionLabel"
        screenState = ScreenState.CONNECTION_CHECK
        renderScreen()
        Thread {
            try {
                val json = getJson("$baseUrl/api/glasses/health")
                val defaultModel = json.optString("default_model", "")
                val features = json.optJSONArray("features")
                qaAudioSupported = false
                if (features != null) {
                    for (i in 0 until features.length()) {
                        if (features.optString(i) == "qa_audio") {
                            qaAudioSupported = true
                            break
                        }
                    }
                }
                runOnUiIfCurrent(token) {
                    connectionStatusText = "连接正常"
                    lastStatusText = "连接正常"
                    lastResultText = "服务在线\n默认模型：$defaultModel"
                    focusIndex = 0
                    screenState = ScreenState.CONSENT
                    renderScreen()
                }
            } catch (exc: Exception) {
                runOnUiIfCurrent(token) {
                    connectionStatusText = "连接失败"
                    lastStatusText = "电脑服务未连接"
                    lastResultText = exc.message ?: "连接失败"
                    focusIndex = 0
                    screenState = ScreenState.CONNECTION_FAILED
                    renderScreen()
                }
            }
        }.start()
    }

    private fun startCaptureCountdown() {
        val token = operationToken + 1
        operationToken = token
        clearTaskOutput()
        activeTaskTitle = "装置检测"
        lastStatusText = "请保持装置在视野中央"
        lastResultText = "即将自动拍照"
        screenState = ScreenState.CAPTURE_COUNTDOWN
        renderScreen()
        mainHandler.postDelayed({
            if (token == operationToken && screenState == ScreenState.CAPTURE_COUNTDOWN) {
                startCaptureFlow()
            }
        }, captureCountdownMs)
    }

    private fun startCaptureFlow() {
        clearTaskOutput()
        activeTaskTitle = "装置检测"
        startRunning("正在观察装置...", "")
        ensureCameraPermission(CameraPermissionAction.CAPTURE)
    }

    private fun captureViaCameraService() {
        val captureOnly = !recordingRequested
        val captureToken = operationToken
        if (captureOnly) {
            val intent = Intent(this, RecordingService::class.java)
                .setAction(RecordingService.ACTION_START_RECORDING)
            ContextCompat.startForegroundService(this, intent)
        }
        withRecordingService { service ->
            if (captureOnly) {
                service.startRecording()
            }
            runOnUiThread {
                lastStatusText = if (captureOnly) {
                    "正在准备相机，请保持稳定"
                } else {
                    "正在拍照，请保持稳定"
                }
                renderScreen()
            }
            val runCapture = {
                runOnUiThread {
                    if (captureToken == operationToken) {
                        lastStatusText = "正在拍照，请保持稳定"
                        renderScreen()
                    }
                }
                captureWithService(service, captureOnly, captureToken)
            }
            if (captureOnly) {
                mainHandler.postDelayed(runCapture, 1_500L)
            } else {
                runCapture()
            }
        }
    }

    private fun captureWithService(
        service: RecordingService,
        captureOnly: Boolean,
        captureToken: Int,
    ) {
        var completed = false
        fun cleanupCaptureOnly() {
            if (!captureOnly) {
                return
            }
            service.stopRecording()
            if (serviceBound) {
                unbindService(serviceConnection)
                serviceBound = false
                recordingService = null
            }
            stopService(Intent(this, RecordingService::class.java).setAction(RecordingService.ACTION_STOP_RECORDING))
        }

        mainHandler.postDelayed({
            if (!completed && captureToken == operationToken) {
                completed = true
                cleanupCaptureOnly()
                showResult("相机抓拍超时。请重试一次，或先允许后台录像后再拍照。", "拍照失败")
            }
        }, 12_000L)

        service.captureStillImage { file, error ->
            runOnUiThread {
                if (completed || captureToken != operationToken) {
                    if (captureOnly) {
                        cleanupCaptureOnly()
                    }
                    return@runOnUiThread
                }
                completed = true
                cleanupCaptureOnly()
                if (error != null || file == null) {
                    showResult("无法抓取当前画面：${error?.message ?: "未知错误"}", "拍照失败")
                } else {
                    submitImage(file)
                }
            }
        }
    }

    private fun testConnection() {
        val token = startRunning("正在测试连接...", "")
        Thread {
            try {
                val json = getJson("$baseUrl/api/glasses/health")
                val defaultModel = json.optString("default_model", "")
                runOnUiIfCurrent(token) {
                    showResult("服务在线\n默认模型：$defaultModel", "连接正常")
                }
            } catch (exc: Exception) {
                runOnUiIfCurrent(token) {
                    showResult(exc.message ?: "连接失败", "连接失败")
                }
            }
        }.start()
    }

    private fun submitAudioQuestion(file: File, token: Int) {
        try {
            val json = postMultipart(
                "$baseUrl/api/glasses/qa/audio",
                mapOf("model" to "qwen3.5-omni-plus-realtime"),
                "audio",
                file,
                "application/octet-stream",
            )
            val transcript = json.optString("transcript", "").ifBlank { "未返回转写文本" }
            val answer = json.optString("answer_text", "").ifBlank { "AI 没有返回内容。" }
            runOnUiIfCurrent(token) {
                showResult("识别：$transcript\n\n回答：$answer", "问答完成")
            }
        } catch (exc: Exception) {
            runOnUiIfCurrent(token) {
                showResult(exc.message ?: "问答失败", "问答失败")
            }
        } finally {
            file.delete()
        }
    }

    private fun submitImage(file: File) {
        val token = startRunning("正在核对实验要求...", "")
        Thread {
            try {
                val fields = linkedMapOf(
                    "mode" to selectedModeValue(),
                    "experiment_type" to selectedExperimentTypeValue(),
                    "provider" to "ali",
                    "model" to "qwen3.6-flash",
                )
                val json = postMultipart(
                    "$baseUrl/api/glasses/jobs",
                    fields,
                    "image",
                    file,
                )
                pollJob(resolveUrl(json.getString("poll_url")), token)
            } catch (exc: Exception) {
                runOnUiIfCurrent(token) {
                    showResult(exc.message ?: "提交失败", "提交失败")
                }
            }
        }.start()
    }

    private fun pollJob(url: String, token: Int) {
        val deadline = System.currentTimeMillis() + 180_000L
        while (System.currentTimeMillis() < deadline && token == operationToken) {
            try {
                val json = getJson(url)
                val status = json.optString("status", "")
                val stage = json.optString("stage", "")
                val message = json.optString("message", "")
                val previewDataUrl = json.optString("preview_data_url", "")

                runOnUiIfCurrent(token) {
                    if (previewDataUrl.isNotBlank() && previewDataUrl != "null") {
                        showPreview(previewDataUrl)
                    }
                    val hasPreview = panels.any { it.detectionPreview.visibility == View.VISIBLE }
                    lastStatusText = fieldFriendlyStatus(stage, message, hasPreview, status)
                    renderScreen()
                }

                when (status) {
                    "succeeded" -> {
                        val result = json.optJSONObject("result")
                        val analysisText = result?.optString("analysis_text", "") ?: ""
                        runOnUiIfCurrent(token) {
                            showResult(analysisText.ifBlank { "任务完成，但结果为空。" }, "分析完成")
                        }
                        return
                    }

                    "failed", "expired" -> {
                        val error = json.optString("error", "任务失败")
                        runOnUiIfCurrent(token) {
                            showResult(error, if (status == "expired") "任务已过期" else "任务失败")
                        }
                        return
                    }
                }
                Thread.sleep(1_000L)
            } catch (exc: Exception) {
                runOnUiIfCurrent(token) {
                    showResult(exc.message ?: "轮询失败", "轮询失败")
                }
                return
            }
        }

        runOnUiIfCurrent(token) {
            showResult("等待超过 180 秒，请重新拍照。", "任务超时")
        }
    }

    private fun fieldFriendlyStatus(
        stage: String,
        message: String,
        hasPreview: Boolean,
        status: String,
    ): String {
        if (hasPreview && status != "succeeded") {
            return "检测结果已返回，正在整理建议"
        }
        return when (stage) {
            "upload", "queued" -> "正在准备分析"
            "cv_detection" -> "正在观察装置"
            "retrieval" -> "正在核对实验要求"
            "vlm" -> "正在整理建议"
            "done" -> "分析完成"
            else -> if (message.isNotBlank()) "正在分析" else "正在观察装置"
        }
    }

    private fun startRunning(status: String, result: String): Int {
        operationToken += 1
        lastStatusText = status
        lastResultText = result
        screenState = ScreenState.RUNNING
        renderScreen()
        return operationToken
    }

    private fun showResult(result: String, status: String) {
        lastResultText = result
        lastStatusText = "$status，双击返回"
        screenState = ScreenState.RESULT
        renderScreen()
    }

    private fun selectedModeValue(): String {
        return if (selectedMode == Mode.SAFETY) "safety" else "experiment"
    }

    private fun selectedExperimentTypeValue(): String {
        return when (selectedExperimentType) {
            ExperimentType.DISTILLATION -> "distillation"
            ExperimentType.PHOTOMETER -> "photometer"
            ExperimentType.ELECTROLYSIS -> "electrolysis"
        }
    }

    private fun resolveUrl(path: String): String {
        return if (path.startsWith("http://") || path.startsWith("https://")) {
            path
        } else {
            "$baseUrl$path"
        }
    }

    private fun startThinking() {
        thinkingFrame = 0
        panels.forEach { it.thinkingText.visibility = View.VISIBLE }
        mainHandler.removeCallbacks(thinkingRunnable)
        thinkingRunnable.run()
    }

    private fun stopThinking() {
        mainHandler.removeCallbacks(thinkingRunnable)
        panels.forEach { it.thinkingText.visibility = View.GONE }
    }

    private fun showPreview(dataUrl: String) {
        if (shownPreviewDataUrl == dataUrl) {
            return
        }
        try {
            val base64Text = dataUrl.substringAfter(",", "")
            if (base64Text.isBlank()) {
                return
            }
            val bytes = Base64.decode(base64Text, Base64.DEFAULT)
            val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return
            panels.forEach { panel ->
                panel.detectionPreview.setImageBitmap(bitmap)
                panel.detectionPreview.visibility = View.VISIBLE
                panel.progress.visibility = View.GONE
            }
            shownPreviewDataUrl = dataUrl
        } catch (_: IllegalArgumentException) {
            // Ignore malformed preview payloads; final text result can still arrive.
        }
    }

    override fun onStop() {
        if (screenState == ScreenState.QA_RECORDING) {
            stopAudioQuestionRecording()
        }
        if (recordingRequested && !isChangingConfigurations) {
            stopRecordingService()
        }
        super.onStop()
    }

    override fun onDestroy() {
        mainHandler.removeCallbacks(welcomeRunnable)
        mainHandler.removeCallbacks(thinkingRunnable)
        mainHandler.removeCallbacks(recordingStatusRunnable)
        mainHandler.removeCallbacks(actionMenuTimeoutRunnable)
        stopAudioQuestionRecording()
        if (isFinishing) {
            stopRecordingService()
        } else if (serviceBound) {
            unbindService(serviceConnection)
            serviceBound = false
            recordingService = null
        }
        super.onDestroy()
    }

    private fun runOnUiIfCurrent(token: Int, block: () -> Unit) {
        mainHandler.post {
            if (token == operationToken) {
                block()
            }
        }
    }

    private fun getJson(urlText: String): JSONObject {
        val connection = (URL(urlText).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = 8_000
            readTimeout = 20_000
        }
        return readJsonResponse(connection)
    }

    private fun postMultipart(
        urlText: String,
        fields: Map<String, String>,
        fileField: String,
        file: File,
        contentType: String = "image/jpeg",
    ): JSONObject {
        val boundary = "----RayNeoBoundary${System.currentTimeMillis()}"
        val connection = (URL(urlText).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            doOutput = true
            connectTimeout = 10_000
            readTimeout = 30_000
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
            out.writeBytes(
                "Content-Disposition: form-data; name=\"$fileField\"; filename=\"${file.name}\"\r\n",
            )
            out.writeBytes("Content-Type: $contentType\r\n\r\n")
            FileInputStream(file).use { input ->
                input.copyTo(out)
            }
            out.writeBytes("\r\n--$boundary--\r\n")
            out.flush()
        }

        return readJsonResponse(connection)
    }

    private fun postJson(urlText: String, payload: JSONObject): JSONObject {
        val connection = (URL(urlText).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            doOutput = true
            connectTimeout = 10_000
            readTimeout = 60_000
            setRequestProperty("Content-Type", "application/json; charset=utf-8")
        }
        connection.outputStream.use { output ->
            output.write(payload.toString().toByteArray(Charsets.UTF_8))
        }
        return readJsonResponse(connection)
    }

    private fun readJsonResponse(connection: HttpURLConnection): JSONObject {
        val code = connection.responseCode
        val stream = if (code in 200..299) connection.inputStream else connection.errorStream
        val body = stream?.use { readAll(it) }.orEmpty()
        connection.disconnect()
        if (code !in 200..299) {
            val lowerBody = body.trimStart().lowercase()
            val message = when {
                code == 404 && lowerBody.startsWith("<!doctype html") ->
                    "HTTP 404：电脑端服务没有这个接口。请重启新版 pipeline.web.app 服务。"
                lowerBody.startsWith("<!doctype html") || lowerBody.startsWith("<html") ->
                    "HTTP $code：电脑端返回了网页错误，请检查服务地址和后端版本。"
                body.isBlank() ->
                    "HTTP $code：电脑端没有返回错误详情。"
                else ->
                    "HTTP $code: $body"
            }
            throw IOException(message)
        }
        return JSONObject(body)
    }

    private fun readAll(input: InputStream): String {
        return BufferedReader(input.reader(Charsets.UTF_8)).use { it.readText() }
    }
}
