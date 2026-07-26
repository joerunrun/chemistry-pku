package com.example.rayneocalibration

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Bundle
import android.os.Environment
import android.view.GestureDetector
import android.view.MotionEvent
import android.widget.LinearLayout
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

class ObjectOverlayActivity : ComponentActivity() {
    private enum class Phase {
        INIT,
        READY,
        CAPTURING,
        DETECTING,
        DONE,
        ERROR,
    }

    private companion object {
        const val SERVICE_BASE_URL = "http://127.0.0.1:5080"
    }

    private lateinit var leftView: OverlayEyeView
    private lateinit var rightView: OverlayEyeView
    private lateinit var camera: CameraCaptureManager
    private lateinit var gestureDetector: GestureDetector
    private val worker = Executors.newSingleThreadExecutor()
    private val client = OverlayDetectionClient(SERVICE_BASE_URL)

    private var phase = Phase.INIT
    private var cameraReady = false
    private var calibration: OverlayCalibrationStore.Calibration? = null
    private var status = "正在初始化"
    private var leftBoxes: List<OverlayEyeView.OverlayBox> = emptyList()
    private var rightBoxes: List<OverlayEyeView.OverlayBox> = emptyList()

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            initialize()
        } else {
            phase = Phase.ERROR
            status = "需要相机权限，双击退出后重新授权"
            render()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        configureWindow()
        buildUi()
        configureGestures()
        camera = CameraCaptureManager(this, this)
        requestCamera()
        loadCalibration()
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

    private fun buildUi() {
        leftView = OverlayEyeView(this)
        rightView = OverlayEyeView(this)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(Color.BLACK)
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
                    if (phase == Phase.READY || phase == Phase.DONE || phase == Phase.ERROR) {
                        captureAndDetect()
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
        return if (gestureDetector.onTouchEvent(event)) true else super.dispatchTouchEvent(event)
    }

    private fun requestCamera() {
        if (
            ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            initialize()
        } else {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    private fun initialize() {
        camera.start(
            onReady = {
                cameraReady = true
                phase = if (calibration != null) Phase.READY else Phase.ERROR
                status = if (calibration != null) {
                    "单击拍照检测，双击退出"
                } else {
                    "未找到标定文件，请先完成九点标定"
                }
                render()
            },
            onError = { error ->
                cameraReady = false
                phase = Phase.ERROR
                status = error.message ?: "相机初始化失败"
                render()
            },
        )
    }

    private fun loadCalibration() {
        calibration = runCatching { OverlayCalibrationStore.load(this) }.getOrNull()
        if (calibration == null) {
            phase = Phase.ERROR
            status = "未找到标定文件，请先完成九点标定"
        }
    }

    private fun captureAndDetect() {
        if (!cameraReady) {
            phase = Phase.ERROR
            status = "相机尚未就绪"
            render()
            return
        }
        val cal = calibration
        if (cal == null) {
            phase = Phase.ERROR
            status = "未找到标定文件，请先完成九点标定"
            render()
            return
        }

        phase = Phase.CAPTURING
        status = "正在拍照，请保持头部稳定"
        leftBoxes = emptyList()
        rightBoxes = emptyList()
        render()

        val outputDir = outputDirectory()
        val stamp = timestamp()
        val rawFile = File(outputDir, "raw_$stamp.jpg")
        camera.capture(rawFile) { file, error ->
            if (file == null) {
                phase = Phase.ERROR
                status = error?.message ?: "拍照失败"
                render()
                return@capture
            }
            phase = Phase.DETECTING
            status = "正在检测 object_d"
            render()
            worker.execute {
                runCatching {
                    val result = client.detect(file)
                    val annotatedFile = saveAnnotated(outputDir, stamp, result.annotatedBytes)
                    val projectedLeft = projectBoxes(result.boxes, cal.left)
                    val projectedRight = projectBoxes(result.boxes, cal.right)
                    Triple(result, annotatedFile, Pair(projectedLeft, projectedRight))
                }.onSuccess { (result, annotatedFile, projected) ->
                    runOnUiThread {
                        leftBoxes = projected.first
                        rightBoxes = projected.second
                        phase = Phase.DONE
                        status = if (result.boxes.isEmpty()) {
                            "未检测到框。原图已保存：${file.name}"
                        } else {
                            "检测到 ${result.boxes.size} 个框。带框图：${annotatedFile?.name ?: "未返回"}"
                        }
                        render()
                    }
                }.onFailure { err ->
                    runOnUiThread {
                        phase = Phase.ERROR
                        status = err.message ?: "检测失败"
                        render()
                    }
                }
            }
        }
    }

    private fun projectBoxes(
        boxes: List<OverlayDetectionClient.DetectionBox>,
        matrix: DoubleArray,
    ): List<OverlayEyeView.OverlayBox> {
        return boxes.map { box ->
            val x1 = box.box[0].toDouble()
            val y1 = box.box[1].toDouble()
            val x2 = box.box[2].toDouble()
            val y2 = box.box[3].toDouble()
            OverlayEyeView.OverlayBox(
                label = box.label,
                name = box.name,
                color = box.color,
                points = listOf(
                    OverlayCalibrationStore.project(matrix, x1, y1),
                    OverlayCalibrationStore.project(matrix, x2, y1),
                    OverlayCalibrationStore.project(matrix, x2, y2),
                    OverlayCalibrationStore.project(matrix, x1, y2),
                ),
            )
        }
    }

    private fun outputDirectory(): File {
        val dir = File(
            getExternalFilesDir(Environment.DIRECTORY_PICTURES) ?: filesDir,
            "ObjectOverlay",
        )
        dir.mkdirs()
        return dir
    }

    private fun saveAnnotated(dir: File, stamp: String, bytes: ByteArray?): File? {
        if (bytes == null || bytes.isEmpty()) return null
        val output = File(dir, "annotated_$stamp.jpg")
        output.writeBytes(bytes)
        return output
    }

    private fun timestamp(): String {
        return SimpleDateFormat("yyyyMMdd_HHmmss_SSS", Locale.US).format(Date())
    }

    private fun render() {
        val title = when (phase) {
            Phase.INIT -> "检测框投影测试"
            Phase.READY -> "检测框投影测试"
            Phase.CAPTURING -> "正在拍照"
            Phase.DETECTING -> "正在检测"
            Phase.DONE -> "投影测试完成"
            Phase.ERROR -> "投影测试"
        }
        leftView.show(title, status, leftBoxes)
        rightView.show(title, status, rightBoxes)
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
