package com.example.rayneocalibration

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PointF
import android.util.AttributeSet
import android.view.View

class CalibrationEyeView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {
    private val crossPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.CYAN
        strokeWidth = 5f
        style = Paint.Style.STROKE
    }
    private val centerPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        style = Paint.Style.FILL
    }
    private val titlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textAlign = Paint.Align.CENTER
        textSize = 30f
        typeface = android.graphics.Typeface.DEFAULT_BOLD
    }
    private val statusPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.rgb(205, 190, 255)
        textAlign = Paint.Align.CENTER
        textSize = 21f
    }

    private var target: PointF? = null
    private var title = ""
    private var status = ""

    init {
        setBackgroundColor(Color.BLACK)
    }

    fun showTarget(point: PointF?, title: String, status: String) {
        target = point
        this.title = title
        this.status = status
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (title.isNotBlank()) {
            canvas.drawText(title, width / 2f, 56f, titlePaint)
        }
        if (status.isNotBlank()) {
            canvas.drawText(status, width / 2f, height - 34f, statusPaint)
        }
        val point = target ?: return
        val x = point.x * width
        val y = point.y * height
        val arm = 34f
        canvas.drawLine(x - arm, y, x + arm, y, crossPaint)
        canvas.drawLine(x, y - arm, x, y + arm, crossPaint)
        canvas.drawCircle(x, y, 4f, centerPaint)
    }
}
