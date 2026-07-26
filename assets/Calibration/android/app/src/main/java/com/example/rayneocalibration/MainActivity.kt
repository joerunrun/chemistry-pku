package com.example.rayneocalibration

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.PointF
import android.os.Bundle
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.View
import android.widget.LinearLayout
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import java.io.File
import java.util.concurrent.Executors

class MainActivity : ComponentActivity() {
    private enum class Eye(val label: String) {
        LEFT("左眼"),
        RIGHT("右眼"),
    }

    private enum class Phase {
        INTRO,
        CALIBRATING,
        CAPTURING,
        COMPLETE,
    }

    private val displayPoints = listOf(
        PointF(0.15f, 0.15f),
        PointF(0.50f, 0.15f),
        PointF(0.85f, 0.15f),
        PointF(0.15f, 0.50f),
        PointF(0.50f, 0.50f),
        PointF(0.85f, 0.50f),
        PointF(0.15f, 0.85f),
        PointF(0.50f, 0.85f),
        PointF(0.85f, 0.85f),
    )

    private val pointNames = listOf(
        "左上", "上中", "右上",
        "左中", "中心", "右中",
        "左下", "下中", "右下",
    )

    private lateinit var leftView: CalibrationEyeView
    private lateinit var rightView: CalibrationEyeView
    private lateinit var camera: CameraCaptureManager
    private lateinit var gestureDetector: GestureDetector

    private val worker = Executors.newSingleThreadExecutor()
    private val leftPairs = mutableListOf<HomographySolver.PairPoint>()
    private val rightPairs = mutableListOf<HomographySolver.PairPoint>()

    private var phase = Phase.INTRO
    private var activeEye = Eye.LEFT
    private var pointIndex = 0
    private var cameraReady = false
    private var leftResult: HomographySolver.Result? = null
    private var rightResult: HomographySolver.Result? = null
    private var lastImageWidth = 0
    private var lastImageHeight = 0
    private var transientMessage: String? = null

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            initializeCamera()
        } else {
            transientMessage = "需要相机权限，双击退出后重新授权"
            render()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        configureWindow()
        buildBinocularUi()
        camera = CameraCaptureManager(this, this)
        configureGestures()
        requestCamera()
        render()
    }

    private fun configureWindow() {
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    private fun buildBinocularUi() {
        leftView = CalibrationEyeView(this)
        rightView = CalibrationEyeView(this)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(android.graphics.Color.BLACK)
            addView(
                leftView,
                LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1f),
            )
            addView(
                rightView,
                LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1f),
            )
        }
        setContentView(root)
    }

    private fun configureGestures() {
        gestureDetector = GestureDetector(
            this,
            object : GestureDetector.SimpleOnGestureListener() {
                override fun onDown(e: MotionEvent): Boolean = true

                override fun onSingleTapConfirmed(e: MotionEvent): Boolean {
                    when (phase) {
                        Phase.INTRO, Phase.COMPLETE -> startCalibration()
                        Phase.CALIBRATING -> captureCurrentPoint()
                        Phase.CAPTURING -> Unit
                    }
                    return true
                }

                override fun onDoubleTap(e: MotionEvent): Boolean {
                    finish()
                    return true
                }
            },
        )
    }

    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        return if (gestureDetector.onTouchEvent(event)) {
            true
        } else {
            super.dispatchTouchEvent(event)
        }
    }

    private fun requestCamera() {
        if (
            ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            initializeCamera()
        } else {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    private fun initializeCamera() {
        camera.start(
            onReady = {
                cameraReady = true
                transientMessage = null
                render()
            },
            onError = { error ->
                cameraReady = false
                transientMessage = error.message ?: "相机初始化失败"
                render()
            },
        )
    }

    private fun startCalibration() {
        if (!cameraReady) {
            transientMessage = "相机尚未就绪，请稍候再单击"
            render()
            return
        }
        leftPairs.clear()
        rightPairs.clear()
        leftResult = null
        rightResult = null
        activeEye = Eye.LEFT
        pointIndex = 0
        phase = Phase.CALIBRATING
        transientMessage = null
        render()
    }

    private fun captureCurrentPoint() {
        if (!cameraReady || pointIndex !in displayPoints.indices) return
        phase = Phase.CAPTURING
        transientMessage = "正在拍照，请保持头部稳定"
        render()

        val captureDir = File(
            getExternalFilesDir(android.os.Environment.DIRECTORY_PICTURES),
            "CalibrationCaptures",
        )
        captureDir.mkdirs()
        val filename = "${activeEye.name.lowercase()}_${pointIndex + 1}_" +
            "${System.currentTimeMillis()}.jpg"
        val output = File(captureDir, filename)

        camera.capture(output) { file, error ->
            if (file != null) {
                worker.execute {
                    runCatching { CrossDetector.detect(file) }
                        .onSuccess { detection ->
                            runOnUiThread {
                                acceptDetection(file, detection)
                            }
                        }
                        .onFailure { error ->
                            runOnUiThread {
                                phase = Phase.CALIBRATING
                                transientMessage =
                                    error.message ?: "未检测到九个十字，请调整后重试"
                                render()
                            }
                        }
                }
            } else {
                phase = Phase.CALIBRATING
                transientMessage = error?.message ?: "拍照失败"
                render()
            }
        }
    }

    private fun acceptDetection(file: File, detection: CrossDetector.Result) {
        if (detection.points.size != displayPoints.size) {
            phase = Phase.CALIBRATING
            transientMessage = "检测到 ${detection.points.size} 个十字，需要完整看到 9 个"
            render()
            return
        }

        lastImageWidth = detection.imageWidth
        lastImageHeight = detection.imageHeight
        val cameraPoint = detection.points[pointIndex]
        val displayPoint = displayPoints[pointIndex]
        val pair = HomographySolver.PairPoint(
            cameraX = cameraPoint.x.toDouble(),
            cameraY = cameraPoint.y.toDouble(),
            displayX = displayPoint.x.toDouble(),
            displayY = displayPoint.y.toDouble(),
            captureFile = file.name,
        )
        if (activeEye == Eye.LEFT) {
            leftPairs += pair
        } else {
            rightPairs += pair
        }

        pointIndex += 1
        transientMessage = null
        if (pointIndex < displayPoints.size) {
            phase = Phase.CALIBRATING
            render()
            return
        }

        finishCurrentEye()
    }

    private fun finishCurrentEye() {
        val pairs = if (activeEye == Eye.LEFT) leftPairs else rightPairs
        val result = runCatching { HomographySolver.solveRobust(pairs) }.getOrElse { error ->
            pairs.clear()
            pointIndex = 0
            phase = Phase.CALIBRATING
            transientMessage = "${activeEye.label}标定失败：${error.message}。请重新标定该眼。"
            render()
            return
        }

        if (activeEye == Eye.LEFT) {
            leftResult = result
            activeEye = Eye.RIGHT
            pointIndex = 0
            phase = Phase.CALIBRATING
            transientMessage = "左眼完成，现在标定右眼"
            render()
        } else {
            rightResult = result
            saveCalibration()
        }
    }

    private fun saveCalibration() {
        val left = requireNotNull(leftResult)
        val right = requireNotNull(rightResult)
        val output = runCatching {
            CalibrationStore.save(
                context = this,
                left = left,
                right = right,
                leftPoints = leftPairs,
                rightPoints = rightPairs,
                imageWidth = lastImageWidth,
                imageHeight = lastImageHeight,
            )
        }.getOrElse { error ->
            phase = Phase.CALIBRATING
            activeEye = Eye.RIGHT
            pointIndex = 0
            rightPairs.clear()
            transientMessage = "保存失败：${error.message}"
            render()
            return
        }

        phase = Phase.COMPLETE
        transientMessage = "已保存：${output.absolutePath}"
        render()
    }

    private fun render() {
        when (phase) {
            Phase.INTRO -> {
                val status = transientMessage ?: if (cameraReady) {
                    "单击开始，双击退出"
                } else {
                    "正在初始化相机"
                }
                leftView.showTarget(null, "九点标定", status)
                rightView.showTarget(null, "九点标定", status)
            }

            Phase.CALIBRATING, Phase.CAPTURING -> {
                val title = "${activeEye.label} ${pointIndex + 1}/9"
                val status = transientMessage
                    ?: "对准纸上${pointNames[pointIndex]}十字后单击"
                val point = displayPoints[pointIndex]
                if (activeEye == Eye.LEFT) {
                    leftView.showTarget(point, title, status)
                    rightView.showTarget(null, "", "")
                } else {
                    leftView.showTarget(null, "", "")
                    rightView.showTarget(point, title, status)
                }
            }

            Phase.COMPLETE -> {
                val left = requireNotNull(leftResult)
                val right = requireNotNull(rightResult)
                val status = "左误差 %.3f，右误差 %.3f\n单击重新标定，双击退出"
                    .format(left.rmsError, right.rmsError)
                leftView.showTarget(null, "标定完成", status)
                rightView.showTarget(null, "标定完成", status)
            }
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) configureWindow()
    }

    override fun onDestroy() {
        camera.shutdown()
        worker.shutdownNow()
        super.onDestroy()
    }
}
