package com.example.zrgfirstapp

import android.app.Activity
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.InputDevice
import android.view.MotionEvent
import android.widget.LinearLayout
import android.widget.PopupWindow
import android.widget.TextView
import kotlin.math.abs

/** A physical input descriptor is learned once instead of guessing a firmware device number. */
internal class PhotoControls(private val activity: Activity, private val capture: () -> Unit) {
    private val preferences = activity.getSharedPreferences("photo_controls", 0)
    private val handler = Handler(Looper.getMainLooper())
    private var popup: PopupWindow? = null
    private var ready = false
    private var binding = false
    private var downX = 0f
    private var downY = 0f
    private var downAt = 0L
    private var tracking = false
    private val hide = Runnable { popup?.dismiss(); popup = null; binding = false }

    fun ready() {
        ready = true
        if (preferences.getString("left_descriptor", null) == null) {
            show("首次使用：请单击左镜腿绑定拍照键", 60_000L)
            binding = true
        }
    }

    fun touch(event: MotionEvent): Boolean {
        if (!ready) return false
        val device = event.device ?: return false
        val physicalTemple = device.name.startsWith("cyttsp") || event.isFromSource(InputDevice.SOURCE_TOUCHPAD)
        if (!physicalTemple || device.isVirtual) return false
        val saved = preferences.getString("left_descriptor", null)
        if (saved == null && !binding) return false
        if (saved != null && saved != device.descriptor) return false
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = event.x; downY = event.y; downAt = event.eventTime; tracking = true
            }
            MotionEvent.ACTION_UP -> {
                if (tracking && event.eventTime - downAt < 500 && abs(event.x-downX) < 40 && abs(event.y-downY) < 40) {
                    if (saved == null) preferences.edit().putString("left_descriptor", device.descriptor).apply()
                    capture()
                }
                tracking = false
            }
            MotionEvent.ACTION_CANCEL -> tracking = false
        }
        return true
    }

    fun show(message: String, duration: Long = 1_800L) {
        if (activity.isFinishing || activity.isDestroyed) return
        close()
        val row = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
        val padding = (18 * activity.resources.displayMetrics.density).toInt()
        repeat(2) {
            row.addView(TextView(activity).apply {
                text = message; textSize = 26f; gravity = Gravity.CENTER
                setTextColor(Color.WHITE); setBackgroundColor(Color.argb(220, 20, 31, 45))
                setPadding(padding, padding, padding, padding)
                layoutParams = LinearLayout.LayoutParams(0, -2, 1f).apply { setMargins(padding,0,padding,0) }
            })
        }
        popup = PopupWindow(row, -1, -2, false).apply {
            isTouchable = false
            setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            showAtLocation(activity.window.decorView, Gravity.CENTER, 0, 0)
        }
        handler.postDelayed(hide, duration)
    }

    fun close() { handler.removeCallbacks(hide); popup?.dismiss(); popup = null; binding = false }
}
