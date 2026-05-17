package com.example.zrgfirstapp

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
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
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import kotlin.math.abs

class MainActivity : AppCompatActivity() {
    private enum class ScreenState {
        WELCOME,
        CONSENT,
        HOME,
        EXPERIMENT_SELECT,
        MORE_EXPERIMENT_SELECT,
        ACTION,
        RUNNING,
        RESULT,
    }

    private enum class Mode {
        EXPERIMENT,
        SAFETY,
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

    private var screenState = ScreenState.WELCOME
    private var focusIndex = 0
    private var selectedMode = Mode.EXPERIMENT
    private var selectedExperimentType = ExperimentType.DISTILLATION
    private var shownPreviewDataUrl: String? = null
    private var thinkingFrame = 0
    private var operationToken = 0
    private var lastStatusText = "前后滑动选择，单击确认"
    private var lastResultText = ""
    private var pendingCameraPermissionAction = CameraPermissionAction.NONE
    private var recordingRequested = false
    private var recordingStatusText = "未录像"
    private var recordingService: RecordingService? = null
    private var serviceBound = false
    private val pendingServiceActions = mutableListOf<(RecordingService) -> Unit>()
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
            screenState = ScreenState.CONSENT
            focusIndex = 0
            renderScreen()
        }
    }

    private val thinkingRunnable = object : Runnable {
        override fun run() {
            if (!::leftPanel.isInitialized || screenState != ScreenState.RUNNING) {
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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_main)

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

            ScreenState.CONSENT -> renderChoiceScreen(
                title = "化学实验识别",
                subtitle = "录像许可",
                primary = "允许后台录像",
                secondary = "暂不录像",
                status = "前后滑动选择，单击确认",
                result = "录像仅保存在本机，用于实验过程记录。",
            )

            ScreenState.HOME -> renderChoiceScreen(
                title = "化学实验识别",
                subtitle = "选择功能",
                primary = "实验纠错",
                secondary = "安全检查",
                status = "前后滑动选择，单击确认",
                result = recordingSummary(),
            )

            ScreenState.EXPERIMENT_SELECT -> renderChoiceScreen(
                title = "实验纠错",
                subtitle = "选择实验",
                primary = "减压蒸馏",
                secondary = "更多实验",
                status = "双击返回主页",
                result = recordingSummary(),
            )

            ScreenState.MORE_EXPERIMENT_SELECT -> renderChoiceScreen(
                title = "更多实验",
                subtitle = "选择实验",
                primary = "光度计",
                secondary = "电解",
                status = "双击返回上一层",
                result = recordingSummary(),
            )

            ScreenState.ACTION -> renderChoiceScreen(
                title = selectedTitle(),
                subtitle = "准备操作",
                primary = "测试连接",
                secondary = "拍照分析",
                status = "默认使用 $connectionLabel",
                result = currentSelectionText(),
            )

            ScreenState.RUNNING -> renderProcessScreen(
                title = selectedTitle(),
                subtitle = "正在处理",
                status = lastStatusText,
                result = lastResultText,
                running = true,
            )

            ScreenState.RESULT -> renderProcessScreen(
                title = selectedTitle(),
                subtitle = "处理结果",
                status = lastStatusText,
                result = lastResultText,
                running = false,
            )
        }
    }

    private fun renderWelcomeScreen() {
        panels.forEach { panel ->
            panel.titleText.text = "化学实验识别"
            panel.titleText.textSize = 30f
            panel.subtitleText.text = "智能眼镜实验辅助"
            panel.optionsRow.visibility = View.GONE
            panel.progress.visibility = View.VISIBLE
            panel.thinkingText.visibility = View.GONE
            panel.detectionPreview.visibility = View.GONE
            panel.statusText.text = "欢迎大家！"
            panel.resultText.text = "实验纠错  |  安全检查"
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
            panel.resultText.text = result
        }
        if (running) {
            startThinking()
        } else {
            stopThinking()
        }
    }

    private fun selectedTitle(): String {
        return when (selectedMode) {
            Mode.SAFETY -> "安全检查"
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
            Mode.EXPERIMENT -> "当前实验：${experimentLabel()}"
        }
        return "$selection\n${recordingSummary()}\n连接方式：$connectionLabel"
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

            ScreenState.CONSENT -> {
                if (focusIndex == 0) {
                    recordingRequested = true
                    recordingStatusText = "正在申请录像权限"
                    enterHome()
                    ensureCameraPermission(CameraPermissionAction.START_RECORDING)
                } else {
                    recordingRequested = false
                    recordingStatusText = "未录像"
                    enterHome()
                }
            }

            ScreenState.HOME -> {
                selectedMode = if (focusIndex == 0) Mode.EXPERIMENT else Mode.SAFETY
                focusIndex = 0
                if (selectedMode == Mode.EXPERIMENT) {
                    screenState = ScreenState.EXPERIMENT_SELECT
                } else {
                    clearTaskOutput()
                    screenState = ScreenState.ACTION
                }
                renderScreen()
            }

            ScreenState.EXPERIMENT_SELECT -> {
                if (focusIndex == 0) {
                    selectedExperimentType = ExperimentType.DISTILLATION
                    clearTaskOutput()
                    focusIndex = 0
                    screenState = ScreenState.ACTION
                } else {
                    focusIndex = 0
                    screenState = ScreenState.MORE_EXPERIMENT_SELECT
                }
                renderScreen()
            }

            ScreenState.MORE_EXPERIMENT_SELECT -> {
                selectedExperimentType = if (focusIndex == 0) {
                    ExperimentType.PHOTOMETER
                } else {
                    ExperimentType.ELECTROLYSIS
                }
                clearTaskOutput()
                focusIndex = 0
                screenState = ScreenState.ACTION
                renderScreen()
            }

            ScreenState.ACTION -> {
                if (focusIndex == 0) {
                    testConnection()
                } else {
                    startCaptureFlow()
                }
            }

            ScreenState.RUNNING,
            ScreenState.RESULT -> Unit
        }
    }

    private fun enterHome() {
        focusIndex = 0
        clearTaskOutput()
        screenState = ScreenState.HOME
        renderScreen()
    }

    private fun handleBackGesture() {
        operationToken += 1
        when (screenState) {
            ScreenState.WELCOME,
            ScreenState.CONSENT,
            ScreenState.HOME -> {
                stopRecordingService()
                finish()
            }
            ScreenState.EXPERIMENT_SELECT -> {
                focusIndex = if (selectedMode == Mode.SAFETY) 1 else 0
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
            ScreenState.ACTION,
            ScreenState.RUNNING,
            ScreenState.RESULT -> {
                focusIndex = 0
                clearTaskOutput()
                screenState = if (selectedMode == Mode.EXPERIMENT) {
                    if (selectedExperimentType == ExperimentType.PHOTOMETER ||
                        selectedExperimentType == ExperimentType.ELECTROLYSIS
                    ) {
                        ScreenState.MORE_EXPERIMENT_SELECT
                    } else {
                        ScreenState.EXPERIMENT_SELECT
                    }
                } else {
                    ScreenState.HOME
                }
                renderScreen()
            }
        }
    }

    private fun ScreenState.hasChoices(): Boolean {
        return this == ScreenState.CONSENT ||
            this == ScreenState.HOME ||
            this == ScreenState.EXPERIMENT_SELECT ||
            this == ScreenState.MORE_EXPERIMENT_SELECT ||
            this == ScreenState.ACTION
    }

    private fun clearTaskOutput() {
        shownPreviewDataUrl = null
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

    private fun startCaptureFlow() {
        clearTaskOutput()
        startRunning("准备抓取当前画面...", "")
        ensureCameraPermission(CameraPermissionAction.CAPTURE)
    }

    private fun captureViaCameraService() {
        withRecordingService { service ->
            runOnUiThread {
                lastStatusText = "正在抓取当前画面..."
                renderScreen()
            }
            service.captureStillImage { file, error ->
                runOnUiThread {
                    if (error != null || file == null) {
                        showResult("无法抓取当前画面：${error?.message ?: "未知错误"}", "拍照失败")
                    } else {
                        submitImage(file)
                    }
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

    private fun submitImage(file: File) {
        val token = startRunning("正在上传图片...", "")
        Thread {
            try {
                val fields = linkedMapOf(
                    "mode" to selectedModeValue(),
                    "experiment_type" to selectedExperimentTypeValue(),
                    "provider" to "gemini",
                    "model" to "gemini-3-flash-preview",
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
                    lastStatusText = if (hasPreview && status != "succeeded") {
                        "检测结果已返回，继续分析中"
                    } else {
                        listOf(stage, message)
                            .filter { it.isNotBlank() }
                            .joinToString("：")
                            .ifBlank { "正在分析..." }
                    }
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
        if (recordingRequested && !isChangingConfigurations) {
            stopRecordingService()
        }
        super.onStop()
    }

    override fun onDestroy() {
        mainHandler.removeCallbacks(welcomeRunnable)
        mainHandler.removeCallbacks(thinkingRunnable)
        mainHandler.removeCallbacks(recordingStatusRunnable)
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
            out.writeBytes("Content-Type: image/jpeg\r\n\r\n")
            FileInputStream(file).use { input ->
                input.copyTo(out)
            }
            out.writeBytes("\r\n--$boundary--\r\n")
            out.flush()
        }

        return readJsonResponse(connection)
    }

    private fun readJsonResponse(connection: HttpURLConnection): JSONObject {
        val code = connection.responseCode
        val stream = if (code in 200..299) connection.inputStream else connection.errorStream
        val body = stream?.use { readAll(it) }.orEmpty()
        connection.disconnect()
        if (code !in 200..299) {
            throw IOException("HTTP $code: $body")
        }
        return JSONObject(body)
    }

    private fun readAll(input: InputStream): String {
        return BufferedReader(input.reader(Charsets.UTF_8)).use { it.readText() }
    }
}
