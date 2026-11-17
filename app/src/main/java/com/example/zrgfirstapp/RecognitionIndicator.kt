package com.example.zrgfirstapp

import android.animation.ObjectAnimator
import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/** Request tokens prevent an old response from hiding a newer request's indicator. */
internal class RecognitionIndicator(activity: Activity) {
    private val handler = Handler(Looper.getMainLooper())
    private val requests = mutableSetOf<Any>()
    private var resumed = false
    private val density = activity.resources.displayMetrics.density
    private fun dp(value: Int) = (value * density).toInt()
    private val row = LinearLayout(activity).apply {
        orientation = LinearLayout.HORIZONTAL
        visibility = View.GONE
        isClickable = false
    }
    private val animations = mutableListOf<ObjectAnimator>()

    init {
        repeat(2) {
            val slot = FrameLayout(activity)
            val icon = TextView(activity).apply {
                text = "北"
                textSize = 26f
                gravity = Gravity.CENTER
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(Color.rgb(135, 224, 255))
                contentDescription = "正在识别语音"
            }
            slot.addView(icon, FrameLayout.LayoutParams(dp(40), dp(40), Gravity.END).apply {
                marginEnd = dp(48)
            })
            row.addView(slot, LinearLayout.LayoutParams(0, dp(40), 1f))
            animations += ObjectAnimator.ofFloat(icon, View.ALPHA, 1f, 0.25f, 1f).apply {
                duration = 1_000L
                repeatCount = ObjectAnimator.INFINITE
            }
        }
        activity.findViewById<FrameLayout>(android.R.id.content).addView(row,
            FrameLayout.LayoutParams(-1, dp(40), Gravity.TOP).apply { topMargin = dp(40) })
    }

    fun resume() { resumed = true; render() }
    fun pause() { resumed = false; render() }
    fun clear() { requests.clear(); render() }

    fun begin(): Any = Any().also { token ->
        handler.post { requests += token; render() }
    }

    fun end(token: Any) { handler.post { requests -= token; render() } }

    private fun render() {
        val visible = resumed && requests.isNotEmpty()
        if (visible == (row.visibility == View.VISIBLE)) return
        row.visibility = if (visible) View.VISIBLE else View.GONE
        animations.forEach { if (visible) it.start() else it.cancel() }
    }

    fun close() {
        pause()
        clear()
        handler.removeCallbacksAndMessages(null)
        (row.parent as? ViewGroup)?.removeView(row)
    }
}
