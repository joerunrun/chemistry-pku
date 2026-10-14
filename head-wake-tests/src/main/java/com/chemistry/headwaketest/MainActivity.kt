package com.chemistry.headwaketest

import android.app.Activity
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.GestureDetector
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.Surface
import android.view.View
import android.view.WindowManager
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.min
import kotlin.math.sqrt

class MainActivity : Activity(), SensorEventListener {
    private lateinit var panel: TestView
    private lateinit var touch: GestureDetector
    private val sensors by lazy { getSystemService(SensorManager::class.java) }
    private val sensor by lazy {
        sensors.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR)
            ?: sensors.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)
    }
    private val trigger = DwellTrigger()
    private val handler = Handler(Looper.getMainLooper())
    private val matrix = FloatArray(9)
    private val mapped = FloatArray(9)
    private var reference: FloatArray? = null
    private var orientation = -1
    private var lastSample = 0L
    private var lastKeyTap = 0L
    private var lastTouchTap = 0L
    private var visibleTarget = false
    private var yaw = 0f
    private var pitch = 0f
    private var count = 0
    private var successUntil = 0L
    private var tone: ToneGenerator? = null
    private val keyTap = Runnable { calibrate() }
    private val watchdog = object : Runnable {
        override fun run() {
            if (SystemClock.elapsedRealtime() - lastSample > 300L) {
                trigger.reset()
                visibleTarget = false
                panel.status = if (sensor == null) "设备不支持姿态传感器" else "等待姿态数据，请保持正视"
                panel.invalidate()
            }
            handler.postDelayed(this, 100L)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN or
            View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        volumeControlStream = AudioManager.STREAM_MUSIC
        tone = runCatching { ToneGenerator(AudioManager.STREAM_MUSIC, 70) }.getOrNull()
        panel = TestView()
        setContentView(panel)
        touch = GestureDetector(this, object : GestureDetector.SimpleOnGestureListener() {
            override fun onDown(e: MotionEvent) = true
            override fun onSingleTapConfirmed(e: MotionEvent): Boolean {
                lastTouchTap = SystemClock.uptimeMillis()
                handler.removeCallbacks(keyTap)
                calibrate()
                return true
            }
            override fun onDoubleTap(e: MotionEvent): Boolean {
                lastTouchTap = SystemClock.uptimeMillis()
                handler.removeCallbacks(keyTap)
                finish()
                return true
            }
        })
    }

    private fun calibrate() {
        reference = null
        trigger.reset()
        visibleTarget = false
        successUntil = 0L
        yaw = 0f
        pitch = 0f
        panel.status = "已回正，请正视前方保持片刻"
        panel.invalidate()
    }

    override fun onResume() {
        super.onResume()
        calibrate()
        lastSample = 0L
        val available = sensor != null && sensors.registerListener(this, sensor, SensorManager.SENSOR_DELAY_GAME)
        panel.status = if (available) "请正视前方，建立基准" else "设备不支持姿态传感器"
        handler.post(watchdog)
    }

    override fun onPause() {
        sensors.unregisterListener(this)
        handler.removeCallbacks(watchdog)
        handler.removeCallbacks(keyTap)
        trigger.reset()
        super.onPause()
    }

    override fun onDestroy() {
        tone?.release()
        super.onDestroy()
    }

    override fun dispatchTouchEvent(event: MotionEvent): Boolean =
        touch.onTouchEvent(event) || super.dispatchTouchEvent(event)

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.keyCode == KeyEvent.KEYCODE_BACK && event.action == KeyEvent.ACTION_UP) {
            finish()
            return true
        }
        if (event.keyCode in setOf(KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER,
                KeyEvent.KEYCODE_SPACE, KeyEvent.KEYCODE_F1, KeyEvent.KEYCODE_F2,
                KeyEvent.KEYCODE_F3, KeyEvent.KEYCODE_F4, KeyEvent.KEYCODE_F5,
                KeyEvent.KEYCODE_F6, KeyEvent.KEYCODE_F7, KeyEvent.KEYCODE_F8)) {
            if (event.action == KeyEvent.ACTION_UP) {
                val now = SystemClock.uptimeMillis()
                if (now - lastTouchTap > 320L) {
                    handler.removeCallbacks(keyTap)
                    if (now - lastKeyTap < 320L) finish()
                    else handler.postDelayed(keyTap, 320L)
                    lastKeyTap = now
                }
            }
            return true
        }
        return super.dispatchKeyEvent(event)
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    override fun onSensorChanged(event: SensorEvent) {
        val now = SystemClock.elapsedRealtime()
        if (now - event.timestamp / 1_000_000L > 300L) return
        if (lastSample != 0L && now - lastSample > 300L) {
            trigger.reset()
            visibleTarget = false
        }
        lastSample = now
        @Suppress("DEPRECATION")
        val display = windowManager.defaultDisplay.rotation
        val (x, y) = when (display) {
            Surface.ROTATION_90 -> SensorManager.AXIS_Y to SensorManager.AXIS_MINUS_X
            Surface.ROTATION_180 -> SensorManager.AXIS_MINUS_X to SensorManager.AXIS_MINUS_Y
            Surface.ROTATION_270 -> SensorManager.AXIS_MINUS_Y to SensorManager.AXIS_X
            else -> SensorManager.AXIS_X to SensorManager.AXIS_Y
        }
        SensorManager.getRotationMatrixFromVector(matrix, event.values)
        SensorManager.remapCoordinateSystem(matrix, x, y, mapped)
        if (reference == null || orientation != display) {
            reference = mapped.copyOf()
            orientation = display
            yaw = 0f
            pitch = 0f
            trigger.reset()
        }
        val base = reference!!
        val fx = -(base[0] * mapped[2] + base[3] * mapped[5] + base[6] * mapped[8])
        val fy = -(base[1] * mapped[2] + base[4] * mapped[5] + base[7] * mapped[8])
        val fz = -(base[2] * mapped[2] + base[5] * mapped[5] + base[8] * mapped[8])
        yaw += 0.25f * (Math.toDegrees(atan2(fx, -fz).toDouble()).toFloat() - yaw)
        pitch += 0.25f * (Math.toDegrees(atan2(fy, sqrt(fx * fx + fz * fz)).toDouble()).toFloat() - pitch)
        if (!yaw.isFinite() || !pitch.isFinite()) { calibrate(); return }
        val neutral = abs(yaw) <= 6f && abs(pitch) <= 6f
        val hit: Boolean
        if (BuildConfig.CURSOR_MODE) {
            val cx = cursorX()
            val cy = cursorY()
            if (trigger.armed && cx >= 0.72f && cy <= 0.35f) visibleTarget = true
            if (cx < 0.65f || cy > 0.42f || !trigger.armed) visibleTarget = false
            val eyeWidth = panel.width / 2f
            val eyeHeight = panel.height.toFloat()
            val radius = min(eyeWidth, eyeHeight) * 0.065f
            hit = visibleTarget && hypot((cx - 0.80f) * eyeWidth, (cy - 0.25f) * eyeHeight) <=
                radius * if (trigger.progress > 0f) 1.8f else 1.5f
        } else {
            hit = if (trigger.progress > 0f) yaw in 10f..30f && pitch in 6f..24f
                else yaw in 12f..28f && pitch in 8f..22f
        }
        if (trigger.update(neutral, hit, now)) {
            count++
            successUntil = now + 1200L
            visibleTarget = false
            tone?.startTone(ToneGenerator.TONE_PROP_ACK, 180)
        }
        panel.status = when {
            now < successUntil -> "触发成功！"
            !trigger.armed -> "请回正头部保持片刻"
            trigger.progress > 0f -> "保持姿态，等待 3 秒"
            BuildConfig.CURSOR_MODE -> "转向右上方，让十字对准出现的图标"
            else -> "向右转并抬头，保持 3 秒"
        }
        panel.invalidate()
    }

    private fun cursorX() = (0.5f + yaw * 0.015f).coerceIn(0.04f, 0.96f)
    private fun cursorY() = (0.5f - pitch / 60f).coerceIn(0.04f, 0.96f)

    private inner class TestView : View(this@MainActivity) {
        var status = "请正视前方"
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG)

        override fun onDraw(canvas: Canvas) {
            val w = width / 2f
            val h = height.toFloat()
            val unit = min(w, h)
            for (eye in 0..1) {
                canvas.save()
                canvas.translate(eye * w, 0f)
                canvas.clipRect(0f, 0f, w, h)
                fun text(value: String, y: Float, size: Float, color: Int = Color.WHITE) {
                    paint.style = Paint.Style.FILL
                    paint.textAlign = Paint.Align.CENTER
                    paint.textSize = unit * size
                    paint.color = color
                    canvas.drawText(value, w * 0.5f, h * y, paint)
                }
                text(if (BuildConfig.CURSOR_MODE) "B · 十字边沿测试" else "A · 头部动作测试", 0.13f, 0.038f)
                text(status, 0.70f, 0.030f, Color.rgb(110, 240, 215))
                text("触发次数：$count", 0.78f, 0.034f)
                text("左右 %.1f°   上下 %.1f°".format(yaw, pitch), 0.84f, 0.025f)
                text("单击回正校准 · 双击退出", 0.91f, 0.025f)
                val success = SystemClock.elapsedRealtime() < successUntil
                if (!BuildConfig.CURSOR_MODE || visibleTarget || success) {
                    val tx = w * 0.80f
                    val ty = h * 0.25f
                    val radius = unit * 0.065f
                    paint.style = Paint.Style.STROKE
                    paint.strokeWidth = unit * 0.004f
                    paint.color = if (trigger.progress > 0f || success) Color.rgb(100, 255, 200) else Color.WHITE
                    canvas.drawCircle(tx, ty, radius, paint)
                    paint.style = Paint.Style.FILL
                    paint.textAlign = Paint.Align.CENTER
                    paint.textSize = unit * 0.052f
                    canvas.drawText("北", tx, ty - (paint.ascent() + paint.descent()) / 2f, paint)
                    if (trigger.progress > 0f) {
                        paint.style = Paint.Style.STROKE
                        val r = radius * 1.2f
                        canvas.drawArc(tx - r, ty - r, tx + r, ty + r, -90f, trigger.progress * 360f, false, paint)
                    }
                }
                if (BuildConfig.CURSOR_MODE) {
                    paint.color = Color.rgb(180, 210, 220)
                    paint.strokeWidth = unit * 0.002f
                    val cx = cursorX() * w
                    val cy = cursorY() * h
                    val length = unit * 0.010f
                    canvas.drawLine(cx - length, cy, cx + length, cy, paint)
                    canvas.drawLine(cx, cy - length, cx, cy + length, paint)
                }
                canvas.restore()
            }
        }
    }
}
