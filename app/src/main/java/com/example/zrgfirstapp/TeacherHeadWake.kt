package com.example.zrgfirstapp

import android.app.Activity
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.Surface
import android.view.View
import android.view.ViewGroup
import kotlin.math.atan2
import kotlin.math.sqrt

/** Teacher-only overlay and local 3DoF gesture; never owns the camera or microphone. */
internal class TeacherHeadWake(
    private val activity: Activity,
    private val canWake: () -> Boolean,
    private val wake: () -> Unit,
) : SensorEventListener {
    private val sensors = activity.getSystemService(SensorManager::class.java)
    private val sensor = sensors.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR)
        ?: sensors.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)
    private val handler = Handler(Looper.getMainLooper())
    private val gesture = HeadWakeGesture()
    private val raw = FloatArray(9)
    private val rotation = FloatArray(9)
    private var reference: FloatArray? = null
    private var screenRotation = -1
    private var lastSampleMs = 0L
    private var running = false
    private val icon = WakeIcon(activity)
    private val watchdog = object : Runnable {
        override fun run() {
            if (!running) return
            if (!canWake() || SystemClock.elapsedRealtime() - lastSampleMs > 300L) {
                gesture.reset()
                icon.show(false, 0f)
            }
            handler.postDelayed(this, 100L)
        }
    }

    init {
        activity.addContentView(icon, ViewGroup.LayoutParams(-1, -1))
    }

    fun start() {
        if (running) return
        reference = null
        lastSampleMs = 0L
        gesture.reset()
        running = sensor != null && sensors.registerListener(this, sensor, SensorManager.SENSOR_DELAY_GAME)
        Log.i("TeacherHeadWake", "available=$running sensor=${sensor?.name}")
        if (running) handler.post(watchdog)
    }

    fun stop() {
        running = false
        sensors.unregisterListener(this)
        handler.removeCallbacks(watchdog)
        gesture.reset()
        reference = null
        icon.show(false, 0f)
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    override fun onSensorChanged(event: SensorEvent) {
        if (!running) return
        val now = SystemClock.elapsedRealtime()
        // Never complete a dwell from stale or interrupted sensor delivery.
        if (now - event.timestamp / 1_000_000L > 300L) {
            gesture.reset()
            icon.show(false, 0f)
            return
        }
        if (lastSampleMs != 0L && now - lastSampleMs > 300L) reference = null
        lastSampleMs = now
        if (!canWake()) {
            gesture.reset()
            icon.show(false, 0f)
            return
        }
        @Suppress("DEPRECATION")
        val displayRotation = activity.windowManager.defaultDisplay.rotation
        val (x, y) = when (displayRotation) {
            Surface.ROTATION_90 -> SensorManager.AXIS_Y to SensorManager.AXIS_MINUS_X
            Surface.ROTATION_180 -> SensorManager.AXIS_MINUS_X to SensorManager.AXIS_MINUS_Y
            Surface.ROTATION_270 -> SensorManager.AXIS_MINUS_Y to SensorManager.AXIS_X
            else -> SensorManager.AXIS_X to SensorManager.AXIS_Y
        }
        SensorManager.getRotationMatrixFromVector(raw, event.values)
        SensorManager.remapCoordinateSystem(raw, x, y, rotation)
        if (reference == null || screenRotation != displayRotation) {
            reference = rotation.copyOf()
            screenRotation = displayRotation
            gesture.reset()
        }
        val base = reference!!
        // Forward (-Z) in the initial screen frame: transpose(base) * current * -Z.
        val fx = -(base[0] * rotation[2] + base[3] * rotation[5] + base[6] * rotation[8])
        val fy = -(base[1] * rotation[2] + base[4] * rotation[5] + base[7] * rotation[8])
        val fz = -(base[2] * rotation[2] + base[5] * rotation[5] + base[8] * rotation[8])
        val yaw = Math.toDegrees(atan2(fx, -fz).toDouble()).toFloat()
        val pitch = Math.toDegrees(atan2(fy, sqrt(fx * fx + fz * fz)).toDouble()).toFloat()
        if (gesture.update(yaw, pitch, now)) {
            icon.show(false, 0f)
            if (canWake()) wake()
        } else {
            icon.show(true, gesture.progress)
        }
    }

    private class WakeIcon(activity: Activity) : View(activity) {
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        private var progress = 0f

        init {
            visibility = GONE
            isClickable = false
            isFocusable = false
            importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
        }

        fun show(show: Boolean, value: Float) {
            visibility = if (show) VISIBLE else GONE
            if (progress != value) {
                progress = value
                invalidate()
            }
        }

        override fun onDraw(canvas: Canvas) {
            val dp = resources.displayMetrics.density
            val radius = 25f * dp
            val top = 80f * dp
            // One logical gesture, identical fixed icon in each eye's right corner.
            for (edge in listOf(width / 2f, width.toFloat())) {
                val cx = edge - 90f * dp
                paint.style = Paint.Style.STROKE
                paint.strokeWidth = 3f * dp
                paint.color = if (progress > 0f) Color.rgb(114, 255, 224) else Color.rgb(225, 245, 250)
                canvas.drawCircle(cx, top, radius, paint)
                paint.style = Paint.Style.FILL
                paint.textAlign = Paint.Align.CENTER
                paint.textSize = 28f * dp
                canvas.drawText("北", cx, top - (paint.ascent() + paint.descent()) / 2f, paint)
                if (progress > 0f) {
                    paint.style = Paint.Style.STROKE
                    paint.strokeWidth = 4f * dp
                    val ring = radius + 7f * dp
                    canvas.drawArc(cx - ring, top - ring, cx + ring, top + ring, -90f, 360f * progress, false, paint)
                }
            }
        }
    }
}
