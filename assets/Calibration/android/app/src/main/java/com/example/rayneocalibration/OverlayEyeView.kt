package com.example.rayneocalibration

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.util.AttributeSet
import android.view.View

class OverlayEyeView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {
    data class OverlayBox(
        val label: String,
        val name: String,
        val color: Int,
        val points: List<FloatArray>,
    )

    private val titlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textAlign = Paint.Align.CENTER
        textSize = 32f
        typeface = android.graphics.Typeface.DEFAULT_BOLD
    }
    private val statusPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.rgb(205, 190, 255)
        textAlign = Paint.Align.CENTER
        textSize = 22f
    }
    private val boxPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.YELLOW
        strokeWidth = 6f
        style = Paint.Style.STROKE
    }
    private val labelBgPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(220, 255, 204, 0)
        style = Paint.Style.FILL
    }
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.BLACK
        textAlign = Paint.Align.LEFT
        textSize = 24f
        typeface = android.graphics.Typeface.DEFAULT_BOLD
    }

    private var title = "检测框投影测试"
    private var status = "单击拍照检测，双击退出"
    private var boxes: List<OverlayBox> = emptyList()

    init {
        setBackgroundColor(Color.BLACK)
    }

    fun show(title: String, status: String, boxes: List<OverlayBox>) {
        this.title = title
        this.status = status
        this.boxes = boxes
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        canvas.drawText(title, width / 2f, 58f, titlePaint)
        if (status.isNotBlank()) {
            canvas.drawText(status, width / 2f, height - 34f, statusPaint)
        }
        boxes.forEach { drawBox(canvas, it) }
    }

    private fun drawBox(canvas: Canvas, box: OverlayBox) {
        if (box.points.size < 4 || box.points.any { it.any { v -> v.isNaN() } }) return
        boxPaint.color = box.color
        val path = Path()
        val first = box.points.first()
        path.moveTo(first[0] * width, first[1] * height)
        box.points.drop(1).forEach { p ->
            path.lineTo(p[0] * width, p[1] * height)
        }
        path.close()
        canvas.drawPath(path, boxPaint)

        val minX = box.points.minOf { it[0] } * width
        val minY = box.points.minOf { it[1] } * height
        val label = if (box.name.isNotBlank()) box.name else box.label
        val textWidth = labelPaint.measureText(label)
        val left = minX.coerceIn(0f, width - textWidth - 24f)
        val top = (minY - 36f).coerceIn(0f, height - 34f)
        canvas.drawRect(left, top, left + textWidth + 24f, top + 34f, labelBgPaint)
        canvas.drawText(label, left + 12f, top + 25f, labelPaint)
    }
}
