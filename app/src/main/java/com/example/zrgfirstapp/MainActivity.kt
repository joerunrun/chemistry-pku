package com.example.zrgfirstapp

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Base64
import android.view.GestureDetector
import android.view.Gravity
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.VideoView
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import org.json.JSONObject
import org.json.JSONArray
import java.io.BufferedReader
import java.io.DataOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import kotlin.math.abs

class MainActivity : AppCompatActivity() {
    private enum class ScreenState {
        WELCOME,
        CONNECTION_CHECK,
        CONNECTION_FAILED,
        CONSENT,
        HOME,
        LIVE_MINIMAL,
        ACTION_MENU,
        CAPTURE_COUNTDOWN,
        DEMO_SELECT,
        DEMO_PLAYING,
        VOICE_LISTENING,
        QA_RECORDING,
        RUNNING,
        RESULT,
    }

    private enum class Mode {
        ASSISTANT,
        EXPERIMENT,
        SAFETY,
        QA,
        OVERLAY_TEST,
    }

    private enum class CameraPermissionAction {
        NONE,
        START_RECORDING,
        CAPTURE,
    }

    private enum class AudioPermissionAction {
        NONE,
        START_AUDIO_RECORDING,
        START_VIDEO_RECORDING,
        QA,
        VOICE_CONTROL,
    }

    private enum class PassiveListenMode {
        NONE,
        DEMO_STOP,
        RECORD_STOP,
        DEMO_SELECT,
    }

    private data class EyePanel(
        val root: View,
        val titleText: TextView,
        val subtitleText: TextView,
        val optionsRow: LinearLayout,
        val primaryButton: Button,
        val secondaryButton: Button,
        val detectionOverlayContainer: FrameLayout,
        val detectionPreview: ImageView,
        val detectionOverlay: DetectionOverlayView,
        val demoVideoView: VideoView,
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
        if (BuildConfig.STUDENT_USB) "usb" else getString(R.string.glasses_connection_mode).trim().lowercase()
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
    private val actionMenuTimeoutMs = 8_000L
    private val captureCountdownMs = 2_500L

    private var screenState = ScreenState.WELCOME
    private var passiveListenMode = PassiveListenMode.NONE
    private var focusIndex = 0
    private var selectedMode = Mode.ASSISTANT
    private var shownPreviewDataUrl: String? = null
    private var thinkingFrame = 0
    private var operationToken = 0
    private var activeTaskTitle = ""
    private var lastStatusText = "前后滑动选择，单击确认"
    private var lastResultText = ""
    private var pendingCameraPermissionAction = CameraPermissionAction.NONE
    private var pendingAudioPermissionAction = AudioPermissionAction.NONE
    private var recordingRequested = false
    private var recordingVideo = false
    private var standalonePhotoRequested = false
    private var recordingStatusText = "未记录"
    private var connectionStatusText = "未测试"
    private var qaAudioSupported = false
    private var voiceIntentSupported = false
    private var voiceControlEnabled = false
    private var voiceControlPausedUntilMs = 0L
    private var recordingService: RecordingService? = null
    private var serviceBound = false
    private var serviceBindRequested = false
    private var studentQuestionPending = false
    private var studentCommandsEnabled = false
    private var studentRequestInFlight = false
    private val studentPendingUtterances = java.util.ArrayDeque<StudentUtterance>()
    private var studentCommandSource: StudentUtterance? = null
    private var studentRecentDialogue = ""
    private var studentDialogueUntil = 0L
    private var studentForeground = false
    private var assistantPlayer: MediaPlayer? = null
    private var assistantAudioFile: File? = null
    private data class DemoVideo(
        val id: String,
        val label: String,
        val streamUrl: String,
    )
    private var demoVideos: List<DemoVideo> = emptyList()
    private var activeDemoVideo: DemoVideo? = null
    private val resultReturnRunnable = Runnable {
        if (screenState == ScreenState.RESULT) {
            enterLiveMinimal()
        }
    }
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
            serviceBindRequested = false
            recordingRequested = service.studentState != StudentRecordState.IDLE
            recordingVideo = service.studentState in setOf(StudentRecordState.STARTING_VIDEO, StudentRecordState.VIDEO_RECORDING)
            service.onStudentUtterance = { acceptStudentUtterance(it) }
            service.onStudentError = { error ->
                recordingStatusText = error
                if (studentForeground) showResult(error, "记录异常")
            }
            service.onStudentState = { state, status ->
                val wasPreparing = recordingStatusText.contains("准备")
                recordingRequested = state != StudentRecordState.IDLE
                recordingVideo = state in setOf(StudentRecordState.STARTING_VIDEO, StudentRecordState.VIDEO_RECORDING)
                recordingStatusText = status
                if (studentForeground) {
                    renderScreen()
                    if (wasPreparing && state in setOf(StudentRecordState.AUDIO_RECORDING, StudentRecordState.VIDEO_RECORDING)) {
                        playPrompt("recording_start") { startVoiceIntentLoop() }
                    }
                }
            }
            recordingStatusText = service.recordingStatus()
            startRecordingStatusSync()
            val actions = pendingServiceActions.toList()
            pendingServiceActions.clear()
            actions.forEach { it.invoke(service) }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            recordingService = null
            serviceBound = false
            serviceBindRequested = false
            if (recordingRequested) {
                recordingStatusText = "记录异常"
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
                    ScreenState.DEMO_SELECT,
                    ScreenState.VOICE_LISTENING,
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
                recordingVideo = false
                recordingStatusText = "未授权记录"
                renderScreen()
            } else {
                showResult("未授予相机权限", "权限不足")
            }
            return@registerForActivityResult
        }

        when (action) {
            CameraPermissionAction.START_RECORDING -> ensureRecordingPermissions()
            CameraPermissionAction.CAPTURE -> captureViaCameraService()
            CameraPermissionAction.NONE -> Unit
        }
    }

    private val requestAudioPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        val action = pendingAudioPermissionAction
        pendingAudioPermissionAction = AudioPermissionAction.NONE
        if (granted) {
            when (action) {
                AudioPermissionAction.START_AUDIO_RECORDING -> startAudioRecordingService()
                AudioPermissionAction.START_VIDEO_RECORDING -> startVideoRecordingService()
                AudioPermissionAction.QA -> startAudioQuestionRecording()
                AudioPermissionAction.VOICE_CONTROL -> startVoiceIntentLoop()
                AudioPermissionAction.NONE -> Unit
            }
        } else {
            if (action == AudioPermissionAction.START_AUDIO_RECORDING ||
                action == AudioPermissionAction.START_VIDEO_RECORDING
            ) {
                recordingRequested = false
                recordingVideo = false
                recordingStatusText = "未授权记录"
                renderScreen()
            } else {
                showResult("Microphone permission denied.", "Permission denied")
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (BuildConfig.APP_MODE == "teacher") {
            startActivity(Intent(this, TeacherActivity::class.java))
            finish()
            return
        }
        android.util.Log.i("DeviceIdentity", "hardware_id=${DeviceIdentity.hardwareId(this)}")
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        enableEdgeToEdge()
        window.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        setContentView(R.layout.activity_main)

        mainRoot = findViewById(R.id.main)
        bindViews()
        gestureDetector = GestureDetector(this, TempleGestureListener())
        renderScreen()
        mainHandler.postDelayed(welcomeRunnable, 300L)

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
            detectionOverlayContainer = root.findViewById(R.id.detection_overlay_container),
            detectionPreview = root.findViewById(R.id.detection_preview),
            detectionOverlay = root.findViewById(R.id.detection_overlay),
            demoVideoView = root.findViewById(R.id.demo_video_view),
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
                subtitle = "记录许可",
                primary = "允许记录",
                secondary = "暂不记录",
                status = "前后滑动选择，单击确认",
                result = "连接状态：$connectionStatusText\n记录会同时保存画面和声音。",
            )

            ScreenState.HOME -> renderChoiceScreen(
                title = "小北助手",
                subtitle = "选择检测类型",
                primary = "实验纠错",
                secondary = "安全检查",
                status = "前后滑动选择，单击确认",
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
                title = activeTaskTitle.ifBlank { "装置检测" },
                subtitle = "准备拍照",
                status = lastStatusText,
                result = lastResultText,
                running = true,
            )

            ScreenState.DEMO_SELECT -> renderProcessScreen(
                title = "演示指引",
                subtitle = "请选择视频",
                status = lastStatusText,
                result = lastResultText,
                running = false,
            )

            ScreenState.DEMO_PLAYING -> renderDemoPlayingScreen()

            ScreenState.VOICE_LISTENING -> renderProcessScreen(
                title = "Voice control",
                subtitle = "Listening",
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
        setTransparentChrome(true)
        panels.forEach { panel ->
            setPanelGravity(panel, Gravity.TOP or Gravity.CENTER_HORIZONTAL)
            panel.titleText.visibility = View.VISIBLE
            panel.subtitleText.visibility = View.VISIBLE
            panel.titleText.text = "小北助手"
            panel.titleText.textSize = 30f
            panel.subtitleText.text = "正在启动"
            panel.optionsRow.visibility = View.GONE
            panel.progress.visibility = View.VISIBLE
            panel.thinkingText.visibility = View.GONE
            panel.detectionOverlayContainer.visibility = View.GONE
            panel.detectionPreview.visibility = View.GONE
            panel.detectionOverlay.clearBoxes()
            panel.demoVideoView.visibility = View.GONE
            panel.statusText.text = ""
            panel.resultText.text = ""
        }
    }

    private fun renderLiveMinimalScreen() {
        stopThinking()
        setTransparentChrome(true)
        panels.forEach { panel ->
            setPanelGravity(panel, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL)
            panel.titleText.textSize = 18f
            panel.titleText.text = ""
            panel.titleText.visibility = View.GONE
            panel.subtitleText.text = ""
            panel.subtitleText.visibility = View.GONE
            panel.optionsRow.visibility = View.GONE
            panel.progress.visibility = View.GONE
            panel.thinkingText.visibility = View.GONE
            panel.detectionOverlayContainer.visibility = View.GONE
            panel.detectionPreview.visibility = View.GONE
            panel.detectionOverlay.clearBoxes()
            panel.statusText.visibility = View.GONE
            panel.statusText.text = ""
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
        setTransparentChrome(true)
        panels.forEach { panel ->
            setPanelGravity(panel, Gravity.TOP or Gravity.CENTER_HORIZONTAL)
            panel.titleText.visibility = View.VISIBLE
            panel.subtitleText.visibility = View.VISIBLE
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
            panel.detectionOverlayContainer.visibility = View.GONE
            panel.detectionPreview.visibility = View.GONE
            panel.detectionOverlay.clearBoxes()
            panel.demoVideoView.visibility = View.GONE
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
        setTransparentChrome(true)
        panels.forEach { panel ->
            setPanelGravity(panel, Gravity.TOP or Gravity.CENTER_HORIZONTAL)
            panel.titleText.visibility = View.VISIBLE
            panel.subtitleText.visibility = View.VISIBLE
            panel.titleText.textSize = 26f
            panel.titleText.text = title
            panel.subtitleText.text = subtitle
            panel.optionsRow.visibility = View.GONE
            panel.primaryButton.alpha = 1.0f
            panel.secondaryButton.alpha = 1.0f
            panel.demoVideoView.visibility = View.GONE
            val hasDetectionView = panel.detectionPreview.visibility == View.VISIBLE ||
                panel.detectionOverlay.visibility == View.VISIBLE
            panel.progress.visibility = if (running && !hasDetectionView) {
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

    private fun setPanelGravity(panel: EyePanel, gravity: Int) {
        (panel.root as? LinearLayout)?.gravity = gravity
    }

    private fun selectedTitle(): String {
        return when (selectedMode) {
            Mode.ASSISTANT -> "小北助手"
            Mode.SAFETY -> "安全检查"
            Mode.QA -> "语音问答"
            Mode.OVERLAY_TEST -> "投影测试"
            Mode.EXPERIMENT -> "实验纠错"
        }
    }

    private fun currentSelectionText(): String {
        val selection = when (selectedMode) {
            Mode.ASSISTANT -> "助手待命"
            Mode.SAFETY -> "当前模式：安全检查"
            Mode.QA -> "当前模式：语音问答"
            Mode.OVERLAY_TEST -> "当前模式：投影测试"
            Mode.EXPERIMENT -> "当前模式：实验纠错"
        }
        return "$selection\n${recordingSummary()}\n连接方式：$connectionLabel"
    }

    private fun liveSummary(): String {
        return "${currentSelectionText()}\n连接状态：$connectionStatusText"
    }

    private fun recordingSummary(): String {
        return "记录状态：$recordingStatusText"
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
                    voiceControlEnabled = voiceIntentSupported
                    if (voiceIntentSupported) {
                        enterStudentReady()
                    } else {
                        screenState = ScreenState.CONSENT
                        renderScreen()
                    }
                }
            }


            ScreenState.CONSENT -> {
                if (focusIndex == 0) {
                    recordingRequested = true
                    recordingStatusText = "正在申请记录权限"
                    enterSetup()
                    ensureRecordingPermissions()
                } else {
                    recordingRequested = false
                    recordingStatusText = "未记录"
                    enterSetup()
                }
            }

            ScreenState.HOME -> {
                val selectedIndex = focusIndex
                focusIndex = 0
                if (selectedIndex == 0) {
                    selectedMode = Mode.EXPERIMENT
                    enterLiveMinimal()
                } else {
                    selectedMode = Mode.SAFETY
                    enterLiveMinimal()
                    return
                }
                renderScreen()
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
            ScreenState.DEMO_SELECT,
            ScreenState.VOICE_LISTENING,
            ScreenState.QA_RECORDING -> Unit

            ScreenState.DEMO_PLAYING -> {
                stopDemoPlayback()
                enterLiveMinimal()
            }

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

    private fun enterStudentReady() {
        selectedMode = Mode.ASSISTANT
        studentQuestionPending = false
        enterLiveMinimal()
        playPrompt("assistant_ready")
    }

    private fun enterLiveMinimal() {
        mainHandler.removeCallbacks(actionMenuTimeoutRunnable)
        focusIndex = 0
        clearTaskOutput()
        voiceControlPausedUntilMs = 0L
        screenState = ScreenState.LIVE_MINIMAL
        renderScreen()
        mainHandler.post { startVoiceIntentLoop() }
        mainHandler.postDelayed({ startVoiceIntentLoop() }, 600L)
        mainHandler.postDelayed({ startVoiceIntentLoop() }, 1_500L)
    }

    private fun showActionMenu() {
        stopVoiceIntentRecording()
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
                finishAndRemoveTask()
            }
            ScreenState.ACTION_MENU -> {
                enterLiveMinimal()
            }
            ScreenState.CAPTURE_COUNTDOWN -> {
                enterLiveMinimal()
            }
            ScreenState.DEMO_SELECT -> {
                enterLiveMinimal()
            }
            ScreenState.DEMO_PLAYING -> {
                stopDemoPlayback()
                enterLiveMinimal()
            }
            ScreenState.VOICE_LISTENING -> {
                stopVoiceIntentRecording()
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
            this == ScreenState.ACTION_MENU
    }

    private fun clearTaskOutput() {
        shownPreviewDataUrl = null
        activeTaskTitle = ""
        lastStatusText = "前后滑动选择，单击确认"
        lastResultText = ""
        panels.forEach { panel ->
            panel.detectionPreview.setImageDrawable(null)
            panel.detectionPreview.visibility = View.GONE
            panel.detectionOverlay.clearBoxes()
            panel.detectionOverlayContainer.visibility = View.GONE
            panel.demoVideoView.stopPlayback()
            panel.demoVideoView.visibility = View.GONE
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

    override fun onBackPressed() {
        handleBackGesture()
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
        stopVoiceIntentRecording()
        selectedMode = Mode.QA
        studentQuestionPending = true
        operationToken += 1
        screenState = ScreenState.QA_RECORDING
        lastStatusText = "请说出你的问题"
        lastResultText = ""
        renderScreen()
        playPrompt("qa_start") { startVoiceIntentLoop() }
    }

    private fun renderDemoPlayingScreen() {
        stopThinking()
        setTransparentChrome(true)
        panels.forEach { panel ->
            setPanelGravity(panel, Gravity.TOP or Gravity.CENTER_HORIZONTAL)
            panel.titleText.visibility = View.VISIBLE
            panel.subtitleText.visibility = View.VISIBLE
            panel.titleText.textSize = 22f
            panel.titleText.text = "演示指引"
            panel.subtitleText.text = activeDemoVideo?.let { demoDisplayLabel(it) } ?: "正在播放"
            panel.optionsRow.visibility = View.GONE
            panel.progress.visibility = View.GONE
            panel.thinkingText.visibility = View.GONE
            panel.detectionOverlayContainer.visibility = View.GONE
            panel.detectionPreview.visibility = View.GONE
            panel.detectionOverlay.clearBoxes()
            panel.demoVideoView.visibility = View.VISIBLE
            panel.statusText.text = "说“停止播放”可退出"
            panel.resultText.visibility = View.GONE
        }
    }

    private fun startAudioQuestionRecording() {
        studentQuestionPending = true
        startVoiceIntentLoop()
    }

    private fun stopAudioQuestionRecording() {
        studentQuestionPending = false
    }

    private fun startVoiceIntentLoop() {
        if (!studentForeground || !voiceControlEnabled || !voiceIntentSupported ||
            (!screenState.allowsVoiceIntent() && !recordingRequested)) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            pendingAudioPermissionAction = AudioPermissionAction.VOICE_CONTROL
            requestAudioPermission.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        val wait = voiceControlPausedUntilMs - System.currentTimeMillis()
        if (assistantPlayer != null || wait > 0) {
            mainHandler.postDelayed({ startVoiceIntentLoop() }, maxOf(wait, 300L))
            return
        }
        studentCommandsEnabled = true
        if (recordingService == null) {
            ContextCompat.startForegroundService(this, Intent(this, RecordingService::class.java)
                .setAction(RecordingService.ACTION_START_STUDENT_SESSION))
        }
        withRecordingService { service ->
            if (service.startStudentSession()) service.acceptStudentCommands = studentCommandsEnabled && studentForeground
        }
    }

    private fun stopVoiceIntentRecording() {
        studentCommandsEnabled = recordingRequested && studentForeground
        studentPendingUtterances.clear()
        recordingService?.acceptStudentCommands = studentCommandsEnabled && assistantPlayer == null
    }

    private fun acceptStudentUtterance(utterance: StudentUtterance) {
        if (!studentForeground || !studentCommandsEnabled || assistantPlayer != null ||
            (!screenState.allowsVoiceIntent() && !recordingRequested)) return
        if (studentRequestInFlight) {
            // Bound queued decisions while the microphone/archive keep running.
            if (studentPendingUtterances.size == 4) studentPendingUtterances.removeFirst()
            studentPendingUtterances.addLast(utterance)
            return
        }
        studentRequestInFlight = true
        val token = operationToken
        val context = voiceContextValue()
        val state = recordingService?.studentState?.name ?: "IDLE"
        val dialogue = if (System.currentTimeMillis() < studentDialogueUntil) studentRecentDialogue else ""
        val file = File(cacheDir, "student_intent_${System.nanoTime()}.pcm")
        Thread {
            try {
                file.writeBytes(utterance.pcm)
                val json = postMultipart("$baseUrl/api/glasses/voice/intent", mapOf(
                    "context" to context, "recording_state" to state, "recent_dialogue" to dialogue,
                    "demo_options" to demoOptionsForVoice(), "timeout_seconds" to "8",
                    "model" to "qwen3.5-omni-flash-realtime",
                ), "audio", file, "application/octet-stream")
                runOnUiIfCurrent(token) {
                    if (studentForeground && state == (recordingService?.studentState?.name ?: "IDLE") &&
                        json.optString("intent", "no_action") !in setOf("no_action", "unclear", "unknown")) {
                        studentRecentDialogue = json.optString("transcript").take(700)
                        studentDialogueUntil = System.currentTimeMillis() + 10_000L
                        studentCommandSource = utterance
                        handleStudentIntent(json, context, passiveListenMode)
                        studentCommandSource = null
                    }
                }
            } catch (error: Exception) {
                android.util.Log.w("StudentVoice", "Intent request failed; continue listening", error)
            } finally {
                file.delete()
                mainHandler.post {
                    studentRequestInFlight = false
                    if (token != operationToken) studentPendingUtterances.clear()
                    val pending = studentPendingUtterances.pollFirst()
                    if (pending != null) acceptStudentUtterance(pending)
                }
            }
        }.start()
    }


    private fun voiceContextValue(): String = when {
        studentQuestionPending -> "student_question"
        screenState == ScreenState.DEMO_SELECT -> "student_demo_select"
        screenState == ScreenState.DEMO_PLAYING -> "student_demo_playing"
        else -> "student_idle"
    }

    private fun demoOptionsForVoice(): String {
        if (demoVideos.isEmpty()) {
            return ""
        }
        return demoVideos.mapIndexed { index, video -> "${index + 1}. ${demoDisplayLabel(video)}" }.joinToString("\n")
    }

    private fun demoDisplayLabel(video: DemoVideo): String {
        return video.label.replace(Regex("^\\s*\\d+\\.\\s*"), "")
    }

    private fun isPassiveListenMode(mode: PassiveListenMode): Boolean {
        return mode != PassiveListenMode.NONE
    }

    private fun resumePassiveStopOnlyListening(mode: PassiveListenMode) {
        if (!isPassiveListenMode(mode)) {
            mainHandler.postDelayed({ startVoiceIntentLoop() }, 500L)
            return
        }
        passiveListenMode = mode
        startVoiceIntentLoop()
        when (mode) {
            PassiveListenMode.DEMO_STOP -> {
                if (activeDemoVideo != null) {
                    activeTaskTitle = "演示指引"
                    lastStatusText = "正在播放"
                    lastResultText = ""
                    screenState = ScreenState.DEMO_PLAYING
                    renderScreen()
                }
            }
            PassiveListenMode.DEMO_SELECT -> {
                screenState = ScreenState.DEMO_SELECT
                renderScreen()
            }
            PassiveListenMode.RECORD_STOP -> {
                focusIndex = 0
                clearTaskOutput()
                screenState = ScreenState.LIVE_MINIMAL
                renderScreen()
            }
            PassiveListenMode.NONE -> Unit
        }
        mainHandler.postDelayed({ startVoiceIntentLoop() }, 500L)
    }

    private fun finishPassiveVoiceControl(delayMs: Long = 800L) {
        mainHandler.postDelayed({ enterLiveMinimal() }, delayMs)
    }

    private fun handleStudentIntent(json: JSONObject, context: String, passiveMode: PassiveListenMode) {
        val intent = json.optString("intent", "no_action")
        if (intent in setOf("no_action", "unclear", "unknown")) return
        if (recordingRequested && !screenState.allowsVoiceIntent() && intent != "stop_recording") return
        val slots = json.optJSONObject("slots")

        if (passiveMode == PassiveListenMode.DEMO_STOP) {
            if (intent == "stop_demo_video") {
                stopDemoPlayback(announce = true, returnToLive = true)
            } else {
                resumePassiveStopOnlyListening(passiveMode)
            }
            return
        }

        if (passiveMode == PassiveListenMode.DEMO_SELECT) {
            if (intent != "select_demo_video") {
                resumePassiveStopOnlyListening(passiveMode)
                return
            }
        }

        when (intent) {
            "start_audio_recording", "start_recording" -> {
                startVoiceIntentLoop()
                ensureAudioRecordingPermissions()
            }
            "start_video_recording" -> {
                startVoiceIntentLoop()
                ensureRecordingPermissions()
            }
            "stop_recording" -> {
                stopRecordingService(announce = true, returnToLive = true)
            }
            "capture_photo" -> {
                startCaptureCountdown(photoOnly = true)
            }
            "safety_check" -> {
                selectedMode = Mode.SAFETY
                startCaptureCountdown()
            }
            "experiment_check" -> {
                selectedMode = Mode.EXPERIMENT
                startCaptureCountdown()
            }
            "qa" -> {
                val question = json.optString("question").ifBlank { json.optString("transcript") }
                if (question.isNotBlank()) submitTextQuestion(question)
            }
            "demo_guide" -> {
                startDemoGuide()
            }
            "select_demo_video" -> {
                val index = if (slots?.has("video_index") == true && !slots.isNull("video_index")) {
                    slots.optInt("video_index")
                } else {
                    null
                }
                val query = slots?.optString("video_query", "")?.ifBlank {
                    json.optString("transcript", "")
                } ?: json.optString("transcript", "")
                selectAndPlayDemoVideo(index, query)
            }
            "stop_demo_video" -> {
                stopDemoPlayback(announce = true, returnToLive = true)
            }
            else -> Unit
        }
    }

    private fun ScreenState.allowsVoiceIntent(): Boolean {
        return this == ScreenState.QA_RECORDING ||
            this == ScreenState.LIVE_MINIMAL ||
            this == ScreenState.DEMO_SELECT ||
            this == ScreenState.DEMO_PLAYING ||
            this == ScreenState.VOICE_LISTENING
    }

    private fun startDemoGuide() {
        stopVoiceIntentRecording()
        val token = startRunning("正在读取演示视频...", "")
        activeTaskTitle = "演示指引"
        Thread {
            try {
                val json = getJson("$baseUrl/api/glasses/demo/videos")
                val videosJson = json.optJSONArray("videos")
                val videos = mutableListOf<DemoVideo>()
                if (videosJson != null) {
                    for (i in 0 until videosJson.length()) {
                        val item = videosJson.optJSONObject(i) ?: continue
                        val id = item.optString("id", "")
                        val label = item.optString("label", "")
                        val streamUrl = item.optString("stream_url", "")
                        if (id.isNotBlank() && label.isNotBlank() && streamUrl.isNotBlank()) {
                            videos += DemoVideo(id, label, resolveUrl(streamUrl))
                        }
                    }
                }
                runOnUiIfCurrent(token) {
                    if (videos.isEmpty()) {
                        showResult("当前实验没有可播放的演示视频。", "演示指引")
                    } else {
                        demoVideos = videos
                        enterDemoSelect()
                    }
                }
            } catch (exc: Exception) {
                runOnUiIfCurrent(token) {
                    showResult(exc.message ?: "无法读取演示视频。", "演示指引失败")
                }
            }
        }.start()
    }

    private fun enterDemoSelect() {
        operationToken += 1
        passiveListenMode = PassiveListenMode.DEMO_SELECT
        startVoiceIntentLoop()  // 选择视频期间保持苏醒
        activeTaskTitle = "演示指引"
        val options = demoVideos.mapIndexed { index, video -> "${index + 1}. ${demoDisplayLabel(video)}" }
        lastStatusText = "请说视频序号或名称"
        lastResultText = options.joinToString("\n")
        screenState = ScreenState.DEMO_SELECT
        renderScreen()
        playPrompt("demo_select") {
            if (screenState == ScreenState.DEMO_SELECT) {
                startVoiceIntentLoop()
            }
        }
    }

    private fun selectAndPlayDemoVideo(index: Int?, query: String) {
        if (demoVideos.isEmpty()) {
            startDemoGuideThenSelect(index, query)
            return
        }
        if (index == null && isGenericDemoGuideRequest(query)) {
            enterDemoSelect()
            return
        }
        val video = when {
            index != null && index in demoVideos.indices -> demoVideos[index]
            else -> matchDemoVideoByText(query)
        }
        if (video == null) {
            lastStatusText = "没有匹配到视频"
            lastResultText = "请说视频序号或名称。\n" + demoVideos.mapIndexed { i, item -> "${i + 1}. ${demoDisplayLabel(item)}" }.joinToString("\n")
            screenState = ScreenState.DEMO_SELECT
            renderScreen()
            passiveListenMode = PassiveListenMode.DEMO_SELECT
            startVoiceIntentLoop()
            mainHandler.postDelayed({ startVoiceIntentLoop() }, 500L)
            return
        }
        playDemoVideo(video)
    }

    private fun startDemoGuideThenSelect(index: Int?, query: String) {
        val token = startRunning("正在读取演示视频...", "")
        activeTaskTitle = "演示指引"
        Thread {
            try {
                val json = getJson("$baseUrl/api/glasses/demo/videos")
                val videosJson = json.optJSONArray("videos")
                val videos = mutableListOf<DemoVideo>()
                if (videosJson != null) {
                    for (i in 0 until videosJson.length()) {
                        val item = videosJson.optJSONObject(i) ?: continue
                        val id = item.optString("id", "")
                        val label = item.optString("label", "")
                        val streamUrl = item.optString("stream_url", "")
                        if (id.isNotBlank() && label.isNotBlank() && streamUrl.isNotBlank()) {
                            videos += DemoVideo(id, label, resolveUrl(streamUrl))
                        }
                    }
                }
                runOnUiIfCurrent(token) {
                    demoVideos = videos
                    if (index == null && isGenericDemoGuideRequest(query)) {
                        enterDemoSelect()
                    } else {
                        selectAndPlayDemoVideo(index, query)
                    }
                }
            } catch (exc: Exception) {
                runOnUiIfCurrent(token) {
                    showResult(exc.message ?: "无法读取演示视频。", "演示指引失败")
                }
            }
        }.start()
    }

    private fun isGenericDemoGuideRequest(query: String): Boolean {
        val normalized = query.replace(" ", "")
        if (normalized.isBlank()) {
            return true
        }
        val wantsDemo = listOf("演示指引", "播放演示", "放演示", "演示视频", "看视频", "看演示").any {
            normalized.contains(it)
        }
        val hasSpecificVideo = parseDemoVideoIndex(normalized) != null ||
            listOf("操作流程", "操作视频", "原理", "小结").any { normalized.contains(it) }
        return wantsDemo && !hasSpecificVideo
    }

    private fun matchDemoVideoByText(query: String): DemoVideo? {
        val normalized = query.replace(" ", "")
        parseDemoVideoIndex(normalized)?.let { index ->
            if (index in demoVideos.indices) {
                return demoVideos[index]
            }
        }
        return demoVideos.firstOrNull { video ->
            val label = video.label.replace(" ", "")
            normalized.contains(label) ||
                label.contains(normalized) ||
                (normalized.contains("操作") && label.contains("操作")) ||
                (normalized.contains("流程") && label.contains("流程")) ||
                (normalized.contains("原理") && label.contains("原理")) ||
                (normalized.contains("小结") && label.contains("小结"))
        }
    }

    private fun parseDemoVideoIndex(text: String): Int? {
        Regex("(?:第)?([0-9]{1,2})(?:个|段|号|部|集|条)?").find(text)?.let { match ->
            val value = match.groupValues[1].toIntOrNull()
            if (value != null && value in 1..20) {
                return value - 1
            }
        }

        val numerals = listOf(
            "二十" to 20,
            "十九" to 19,
            "十八" to 18,
            "十七" to 17,
            "十六" to 16,
            "十五" to 15,
            "十四" to 14,
            "十三" to 13,
            "十二" to 12,
            "十一" to 11,
            "十" to 10,
            "九" to 9,
            "八" to 8,
            "七" to 7,
            "六" to 6,
            "五" to 5,
            "四" to 4,
            "三" to 3,
            "两" to 2,
            "二" to 2,
            "一" to 1,
        )
        for ((word, value) in numerals) {
            if (
                text == word ||
                text.contains("第${word}") ||
                text.contains("${word}号") ||
                text.contains("${word}个") ||
                text.contains("${word}段") ||
                text.contains("${word}部")
            ) {
                return value - 1
            }
        }
        return null
    }

    private fun playDemoVideo(video: DemoVideo) {
        stopAssistantAudio()
        stopVoiceIntentRecording()
        passiveListenMode = PassiveListenMode.DEMO_STOP
        startVoiceIntentLoop()  // 视频播放期间保持苏醒
        activeDemoVideo = video
        activeTaskTitle = "演示指引"
        lastStatusText = "视频加载中"
        lastResultText = ""
        screenState = ScreenState.DEMO_PLAYING
        renderScreen()

        playPrompt("demo_start") {
            if (screenState != ScreenState.DEMO_PLAYING || activeDemoVideo != video) {
                return@playPrompt
            }
            panels.forEachIndexed { index, panel ->
                panel.demoVideoView.setOnPreparedListener { player ->
                    player.isLooping = false
                    player.setVolume(if (index == 0) 1.0f else 0.0f, if (index == 0) 1.0f else 0.0f)
                    panel.demoVideoView.start()
                    lastStatusText = "正在播放"
                    renderScreen()
                    mainHandler.postDelayed({ startVoiceIntentLoop() }, 800L)
                }
                panel.demoVideoView.setOnCompletionListener {
                    if (screenState == ScreenState.DEMO_PLAYING) {
                        stopDemoPlayback()
                        enterLiveMinimal()
                    }
                }
                panel.demoVideoView.setOnErrorListener { _, _, _ ->
                    if (screenState == ScreenState.DEMO_PLAYING) {
                        showResult("视频暂时无法播放。", "演示指引失败")
                    }
                    true
                }
                panel.demoVideoView.setVideoURI(Uri.parse(video.streamUrl))
            }
        }
    }

    private fun stopDemoPlayback(
        announce: Boolean = false,
        returnToLive: Boolean = false,
    ) {
        panels.forEach { panel ->
            panel.demoVideoView.stopPlayback()
            panel.demoVideoView.visibility = View.GONE
        }
        passiveListenMode = PassiveListenMode.NONE
        activeDemoVideo = null
        if (announce) {
            playPrompt("demo_stop") {
                if (returnToLive) {
                    enterLiveMinimal()
                }
            }
        } else if (returnToLive) {
            enterLiveMinimal()
        }
    }
    private fun playPrompt(name: String, onDone: (() -> Unit)? = null) {
        val resId = resources.getIdentifier(name, "raw", packageName)
        if (resId == 0) {
            onDone?.invoke()
            return
        }
        stopAssistantAudio()
        voiceControlPausedUntilMs = System.currentTimeMillis() + 2_500L
        val player = MediaPlayer.create(this, resId) ?: return
        player.setVolume(1.0f, 1.0f)
        recordingService?.acceptStudentCommands = false
        assistantPlayer = player
        player.setOnCompletionListener {
            stopAssistantAudio()
            if (onDone != null) {
                onDone.invoke()
            } else if (screenState == ScreenState.LIVE_MINIMAL) {
                startVoiceIntentLoop()
            }
        }
        player.setOnErrorListener { _, _, _ ->
            stopAssistantAudio()
            if (onDone != null) {
                onDone.invoke()
            } else if (screenState == ScreenState.LIVE_MINIMAL) {
                startVoiceIntentLoop()
            }
            true
        }
        player.start()
    }

    private fun playDynamicPrompt(text: String, onDone: (() -> Unit)? = null) {
        if (text.isBlank()) {
            onDone?.invoke()
            return
        }
        val token = operationToken
        Thread {
            try {
                val json = postJson(
                    "$baseUrl/api/glasses/tts",
                    JSONObject().put("text", text).put("model", "qwen3.5-omni-plus-realtime"),
                )
                val audioDataUrl = json.optString("audio_data_url", "")
                runOnUiIfCurrent(token) {
                    playAudioDataUrl(audioDataUrl, onDone)
                }
            } catch (_: Exception) {
                runOnUiIfCurrent(token) {
                    onDone?.invoke()
                }
            }
        }.start()
    }

    private fun playAudioDataUrl(audioDataUrl: String?, onDone: (() -> Unit)? = null) {
        if (audioDataUrl.isNullOrBlank() || audioDataUrl == "null") {
            onDone?.invoke()
            return
        }
        try {
            stopAssistantAudio()
            val bytes = Base64.decode(audioDataUrl.substringAfter(",", ""), Base64.DEFAULT)
            val audioFile = File(cacheDir, "assistant_prompt_${System.currentTimeMillis()}.wav")
            audioFile.writeBytes(bytes)
            assistantAudioFile = audioFile
            val player = MediaPlayer().apply {
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build(),
                )
                setDataSource(audioFile.absolutePath)
                setVolume(1.0f, 1.0f)
                setOnCompletionListener {
                    stopAssistantAudio()
                    onDone?.invoke()
                }
                setOnErrorListener { _, _, _ ->
                    stopAssistantAudio()
                    onDone?.invoke()
                    true
                }
                prepare()
            }
            recordingService?.acceptStudentCommands = false
            assistantPlayer = player
            voiceControlPausedUntilMs = System.currentTimeMillis() + 2_500L
            player.start()
        } catch (_: Exception) {
            onDone?.invoke()
        }
    }

    private fun playResultAudio(audioDataUrl: String?, fallbackText: String) {
        stopAssistantAudio()
        mainHandler.removeCallbacks(resultReturnRunnable)
        val estimatedMs = (fallbackText.length * 180L + 1600L).coerceIn(2500L, 18_000L)
        voiceControlPausedUntilMs = System.currentTimeMillis() + estimatedMs + 1_000L
        if (audioDataUrl.isNullOrBlank() || audioDataUrl == "null") {
            mainHandler.postDelayed({
                voiceControlPausedUntilMs = 0L
                resultReturnRunnable.run()
            }, estimatedMs)
            return
        }
        try {
            val bytes = Base64.decode(audioDataUrl.substringAfter(",", ""), Base64.DEFAULT)
            val audioFile = File(cacheDir, "assistant_result_${System.currentTimeMillis()}.wav")
            audioFile.writeBytes(bytes)
            assistantAudioFile = audioFile
            val player = MediaPlayer().apply {
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build(),
                )
                setDataSource(audioFile.absolutePath)
                setOnPreparedListener { prepared ->
                    prepared.setVolume(1.0f, 1.0f)
                    prepared.start()
                }
                setOnCompletionListener {
                    mainHandler.removeCallbacks(resultReturnRunnable)
                    stopAssistantAudio()
                    voiceControlPausedUntilMs = 0L
                    if (screenState == ScreenState.RESULT) {
                        enterLiveMinimal()
                    }
                }
                setOnErrorListener { _, _, _ ->
                    stopAssistantAudio()
                    voiceControlPausedUntilMs = 0L
                    mainHandler.postDelayed(resultReturnRunnable, 800L)
                    true
                }
            }
            recordingService?.acceptStudentCommands = false
            assistantPlayer = player
            mainHandler.postDelayed(resultReturnRunnable, estimatedMs + 1200L)
            player.prepareAsync()
        } catch (_: Exception) {
            mainHandler.postDelayed({
                voiceControlPausedUntilMs = 0L
                resultReturnRunnable.run()
            }, estimatedMs)
        }
    }

    private fun stopAssistantAudio() {
        assistantPlayer?.let { player ->
            try {
                player.stop()
            } catch (_: IllegalStateException) {
            }
            player.release()
        }
        assistantPlayer = null
        if (studentRecentDialogue.isNotBlank()) studentDialogueUntil = System.currentTimeMillis() + 10_000L
        if (studentForeground && studentCommandsEnabled) startVoiceIntentLoop()
        assistantAudioFile?.delete()
        assistantAudioFile = null
    }

    private fun ensureCameraPermission(action: CameraPermissionAction) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED
        ) {
            when (action) {
                CameraPermissionAction.START_RECORDING -> ensureRecordingPermissions()
                CameraPermissionAction.CAPTURE -> captureViaCameraService()
                CameraPermissionAction.NONE -> Unit
            }
        } else {
            pendingCameraPermissionAction = action
            requestCameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    private fun ensureAudioRecordingPermissions() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            pendingAudioPermissionAction = AudioPermissionAction.START_AUDIO_RECORDING
            requestAudioPermission.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        startAudioRecordingService()
    }

    private fun ensureRecordingPermissions() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            != PackageManager.PERMISSION_GRANTED
        ) {
            pendingCameraPermissionAction = CameraPermissionAction.START_RECORDING
            requestCameraPermission.launch(Manifest.permission.CAMERA)
            return
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            pendingAudioPermissionAction = AudioPermissionAction.START_VIDEO_RECORDING
            requestAudioPermission.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        startVideoRecordingService()
    }

    private fun startAudioRecordingService() { startStudentRecord(false) }
    private fun startVideoRecordingService() { startStudentRecord(true) }

    private fun startStudentRecord(video: Boolean) {
        if (recordingRequested || recordingService?.studentState?.let { it != StudentRecordState.IDLE } == true) return
        operationToken += 1
        passiveListenMode = PassiveListenMode.NONE
        recordingRequested = true
        recordingVideo = video
        recordingStatusText = if (video) "录像准备中" else "录音准备中"
        screenState = ScreenState.LIVE_MINIMAL
        renderScreen()
        val source = studentCommandSource
        ContextCompat.startForegroundService(this, Intent(this, RecordingService::class.java)
            .setAction(RecordingService.ACTION_START_STUDENT_SESSION))
        withRecordingService { service ->
            val started = service.startStudentRecording(video, baseUrl, "unknown_experiment", source)
            if (!started) {
                recordingRequested = false
                recordingVideo = false
                recordingStatusText = "未开始记录"
            }
            startRecordingStatusSync()
            startVoiceIntentLoop()
            renderScreen()
        }
    }

    private fun stopRecordingService(announce: Boolean = false, returnToLive: Boolean = false) {
        val service = recordingService ?: return
        if (service.studentState in setOf(StudentRecordState.IDLE, StudentRecordState.STOPPING)) return
        operationToken += 1
        studentPendingUtterances.clear()
        passiveListenMode = PassiveListenMode.NONE
        service.stopStudentRecording {
            if (studentForeground) {
                if (returnToLive) enterLiveMinimal()
                if (announce) playDynamicPrompt(recordingStatusText) { startVoiceIntentLoop() }
            }
        }
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
        if (serviceBindRequested) return
        serviceBindRequested = true
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
                voiceIntentSupported = false
                if (features != null) {
                    for (i in 0 until features.length()) {
                        val feature = features.optString(i)
                        if (feature == "qa_audio") {
                            qaAudioSupported = true
                        }
                        if (feature == "student_direct_control") {
                            voiceIntentSupported = true
                        }
                    }
                }
                if (!voiceIntentSupported) throw IOException("请先更新后端，再使用学生自然语音控制版本。")
                runOnUiIfCurrent(token) {
                    connectionStatusText = "连接正常"
                    lastStatusText = "连接正常"
                    lastResultText = "服务在线\n默认模型：$defaultModel"
                    focusIndex = 0
                    voiceControlEnabled = voiceIntentSupported
                    if (voiceIntentSupported) {
                        enterStudentReady()
                    } else {
                        screenState = ScreenState.CONSENT
                        renderScreen()
                    }
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

    private fun startCaptureCountdown(photoOnly: Boolean = false) {
        stopVoiceIntentRecording()
        standalonePhotoRequested = photoOnly
        val token = operationToken + 1
        operationToken = token
        clearTaskOutput()
        activeTaskTitle = when {
            standalonePhotoRequested -> "拍照"
            selectedMode == Mode.OVERLAY_TEST -> "投影测试"
            else -> "装置检测"
        }
        lastStatusText = if (standalonePhotoRequested) {
            "请保持画面稳定"
        } else if (selectedMode == Mode.OVERLAY_TEST) {
            "请保持目标在视野中央"
        } else {
            "请保持装置在视野中央"
        }
        lastResultText = "即将自动拍照"
        screenState = ScreenState.CAPTURE_COUNTDOWN
        renderScreen()
        val promptName = when {
            standalonePhotoRequested -> "start_capture"
            selectedMode == Mode.SAFETY -> "start_safety"
            selectedMode == Mode.EXPERIMENT -> "start_experiment_check"
            else -> "start_capture"
        }
        playPrompt(promptName) {
            mainHandler.postDelayed({
                if (token == operationToken && screenState == ScreenState.CAPTURE_COUNTDOWN) {
                    startCaptureFlow()
                }
            }, captureCountdownMs)
        }
    }

    private fun startOverlayTestCountdown() {
        selectedMode = Mode.OVERLAY_TEST
        startCaptureCountdown()
    }

    private fun startCaptureFlow() {
        clearTaskOutput()
        activeTaskTitle = when {
            standalonePhotoRequested -> "拍照"
            selectedMode == Mode.OVERLAY_TEST -> "投影测试"
            else -> "装置检测"
        }
        val status = if (standalonePhotoRequested) {
            "正在拍照，请保持稳定"
        } else if (selectedMode == Mode.OVERLAY_TEST) {
            "正在检测投影框..."
        } else {
            "正在观察装置..."
        }
        startRunning(status, "")
        ensureCameraPermission(CameraPermissionAction.CAPTURE)
    }

    private fun captureViaCameraService() {
        val captureOnly = !recordingRequested
        val captureToken = operationToken
        if (captureOnly) {
            val intent = Intent(this, RecordingService::class.java)
                .setAction(RecordingService.ACTION_START_CAPTURE_SESSION)
            ContextCompat.startForegroundService(this, intent)
        }
        withRecordingService { service ->
            if (captureOnly) {
                service.startCaptureSession()
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
            service.stopCaptureSessionIfIdle()
        }

        mainHandler.postDelayed({
            if (!completed && captureToken == operationToken) {
                completed = true
                cleanupCaptureOnly()
                showResult("相机初始化或抓拍超时，请重试一次。", "拍照失败")
            }
        }, 20_000L)

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
                    standalonePhotoRequested = false
                    showResult("无法抓取当前画面：${error?.message ?: "未知错误"}", "拍照失败")
                } else if (standalonePhotoRequested) {
                    standalonePhotoRequested = false
                    saveStandalonePhoto(file)
                } else if (selectedMode == Mode.OVERLAY_TEST) {
                    submitOverlayTest(file)
                } else {
                    submitImage(file)
                }
            }
        }
    }

    private fun saveStandalonePhoto(file: File) {
        withRecordingService { service ->
            Thread {
                try {
                    service.saveStudentPhoto(file, baseUrl, "unknown_experiment")
                    runOnUiThread { showResult("照片已保存在眼镜上，等待上传。", "拍照完成") }
                } catch (error: Exception) {
                    runOnUiThread { showResult(error.message ?: "照片保存失败", "拍照失败") }
                }
            }.start()
        }
    }

    private fun submitTextQuestion(question: String) {
        studentQuestionPending = false
        stopVoiceIntentRecording()
        val token = startRunning("正在回答你的问题...", question)
        Thread {
            try {
                val json = postJson("$baseUrl/api/glasses/qa", JSONObject().put("question", question))
                val answer = json.optString("answer", "")
                runOnUiIfCurrent(token) {
                    studentRecentDialogue = "用户：$question\n助手：${answer.take(700)}"
                    showResult(answer, "问答完成")
                }
            } catch (error: Exception) {
                runOnUiIfCurrent(token) { showResult(error.message ?: "问答失败", "问答失败") }
            }
        }.start()
    }

    private fun submitImage(file: File) {
        val token = startRunning("正在分析实验照片...", "")
        Thread {
            try {
                val fields = linkedMapOf(
                    "mode" to selectedModeValue(),
                    "provider" to "qiniu",
                    "model" to "google/gemini-3.5-flash",
                )
                val json = postMultipart(
                    "$baseUrl/api/glasses/jobs",
                    fields,
                    "image",
                    file,
                )
                if (!file.delete()) {
                    android.util.Log.w("ChemCapture", "Submitted but could not delete ${file.name}")
                }
                pollJob(resolveUrl(json.getString("poll_url")), token)
            } catch (exc: Exception) {
                runOnUiIfCurrent(token) {
                    showResult(exc.message ?: "提交失败", "提交失败")
                }
            }
        }.start()
    }

    private fun submitOverlayTest(file: File) {
        val token = startRunning("正在计算叠加框...", "")
        Thread {
            try {
                val json = postMultipart(
                    "$baseUrl/api/glasses/overlay-test",
                    mapOf("experiment_type" to "other"),
                    "image",
                    file,
                )
                if (!file.delete()) {
                    android.util.Log.w("ChemOverlay", "Submitted but could not delete ${file.name}")
                }
                val boxes = json.optJSONArray("overlay_boxes")
                runOnUiIfCurrent(token) {
                    val overlayShown = showOverlayBoxes(boxes)
                    if (overlayShown) {
                        showResult("已显示透明叠加框。请保持头部稳定。", "投影测试完成")
                    } else {
                        showResult("未检测到可叠加框。请确认已用“其它测试标签”标注参考图。", "未检测到框")
                    }
                }
            } catch (exc: Exception) {
                runOnUiIfCurrent(token) {
                    showResult(exc.message ?: "投影测试失败", "投影测试失败")
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
                val overlayBoxes = json.optJSONArray("overlay_boxes")

                runOnUiIfCurrent(token) {
                    val overlayShown = showOverlayBoxes(overlayBoxes)
                    if (!overlayShown && !shouldUseWorldOverlay() && previewDataUrl.isNotBlank() && previewDataUrl != "null") {
                        showPreview(previewDataUrl)
                    }
                    val hasPreview = panels.any {
                        it.detectionPreview.visibility == View.VISIBLE ||
                            it.detectionOverlay.visibility == View.VISIBLE
                    }
                    lastStatusText = fieldFriendlyStatus(stage, message, hasPreview, status)
                    renderScreen()
                }

                when (status) {
                    "succeeded" -> {
                        val result = json.optJSONObject("result")
                        val analysisText = result?.optString("analysis_text", "") ?: ""
                        val audioDataUrl = result?.optString("analysis_audio_data_url", "") ?: ""
                        runOnUiIfCurrent(token) {
                            showResult(analysisText.ifBlank { "Task done, empty result." }, "Analysis done", audioDataUrl)
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

    private fun showResult(result: String, status: String, audioDataUrl: String? = null) {
        lastResultText = result
        lastStatusText = status
        screenState = ScreenState.RESULT
        renderScreen()
        playResultAudio(audioDataUrl, (status + ". " + result).take(180))
    }

    private fun selectedModeValue(): String {
        return when (selectedMode) {
            Mode.SAFETY -> "safety"
            Mode.OVERLAY_TEST -> "overlay_test"
            else -> "general_check"
        }
    }

    private fun shouldUseWorldOverlay(): Boolean {
        return selectedMode == Mode.OVERLAY_TEST
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
                panel.detectionOverlayContainer.visibility = View.VISIBLE
                panel.detectionOverlay.clearBoxes()
                panel.detectionPreview.setImageBitmap(bitmap)
                panel.detectionPreview.visibility = View.VISIBLE
                panel.progress.visibility = View.GONE
            }
            shownPreviewDataUrl = dataUrl
        } catch (_: IllegalArgumentException) {
            // Ignore malformed preview payloads; final text result can still arrive.
        }
    }

    private fun showOverlayBoxes(boxes: JSONArray?): Boolean {
        if (boxes == null || boxes.length() == 0) {
            return false
        }
        panels.forEach { panel ->
            panel.detectionOverlayContainer.visibility = View.VISIBLE
            panel.detectionPreview.setImageDrawable(null)
            panel.detectionPreview.visibility = View.GONE
            panel.detectionOverlay.setBoxes(boxes)
            panel.progress.visibility = View.GONE
        }
        return true
    }

    override fun onResume() {
        super.onResume()
        studentForeground = true
        if (::leftPanel.isInitialized) startVoiceIntentLoop()
    }

    override fun onPause() {
        studentForeground = false
        operationToken += 1
        stopVoiceIntentRecording()
        super.onPause()
    }

    override fun onStop() {
        if (screenState == ScreenState.QA_RECORDING) {
            stopAudioQuestionRecording()
        }
        if (recordingRequested && !isChangingConfigurations) {
            stopRecordingService()
        }
        if (!isChangingConfigurations) recordingService?.stopStudentSession()
        super.onStop()
    }

    override fun onDestroy() {
        mainHandler.removeCallbacks(welcomeRunnable)
        mainHandler.removeCallbacks(thinkingRunnable)
        mainHandler.removeCallbacks(recordingStatusRunnable)
        mainHandler.removeCallbacks(actionMenuTimeoutRunnable)
        mainHandler.removeCallbacks(resultReturnRunnable)
        stopAudioQuestionRecording()
        stopVoiceIntentRecording()
        stopAssistantAudio()
        if (isFinishing) {
            stopRecordingService()
            recordingService?.stopStudentSession()
        }
        recordingService?.onStudentUtterance = null
        recordingService?.onStudentState = null
        recordingService?.onStudentError = null
        if (serviceBound) {
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











