package com.example.zrgfirstapp

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.max

class DetectionOverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {
    data class OverlayBox(
        val label: String,
        val name: String,
        val color: Int,
        val rect: RectF,
    )

    private val boxes = mutableListOf<OverlayBox>()
    private val boxPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 4f
    }
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textSize = 22f
        style = Paint.Style.FILL
        typeface = android.graphics.Typeface.DEFAULT_BOLD
    }
    private val labelBgPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        alpha = 190
    }

    fun setBoxes(jsonBoxes: JSONArray?) {
        boxes.clear()
        if (jsonBoxes != null) {
            for (i in 0 until jsonBoxes.length()) {
                val item = jsonBoxes.optJSONObject(i) ?: continue
                val box = item.optJSONArray("box") ?: continue
                if (box.length() != 4) continue
                boxes += OverlayBox(
                    label = item.optString("label", ""),
                    name = item.optString("name", item.optString("label", "")),
                    color = parseColor(item.optString("color", "#FFD54F")),
                    rect = RectF(
                        box.optDouble(0, 0.0).toFloat(),
                        box.optDouble(1, 0.0).toFloat(),
                        box.optDouble(2, 0.0).toFloat(),
                        box.optDouble(3, 0.0).toFloat(),
                    ),
                )
            }
        }
        visibility = if (boxes.isEmpty()) GONE else VISIBLE
        invalidate()
    }

    fun clearBoxes() {
        boxes.clear()
        visibility = GONE
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()
        for (box in boxes) {
            val left = box.rect.left.coerceIn(0f, 1f) * w
            val top = box.rect.top.coerceIn(0f, 1f) * h
            val right = box.rect.right.coerceIn(0f, 1f) * w
            val bottom = box.rect.bottom.coerceIn(0f, 1f) * h
            if (right <= left || bottom <= top) continue

            boxPaint.color = box.color
            canvas.drawRect(left, top, right, bottom, boxPaint)

            val text = box.name.ifBlank { box.label }
            if (text.isNotBlank()) {
                labelBgPaint.color = box.color
                val textWidth = labelPaint.measureText(text)
                val labelHeight = max(30f, labelPaint.textSize + 10f)
                val labelTop = max(0f, top - labelHeight)
                canvas.drawRect(left, labelTop, left + textWidth + 16f, labelTop + labelHeight, labelBgPaint)
                canvas.drawText(text, left + 8f, labelTop + labelHeight - 8f, labelPaint)
            }
        }
    }

    private fun parseColor(value: String): Int {
        return try {
            Color.parseColor(value)
        } catch (_: IllegalArgumentException) {
            Color.rgb(255, 213, 79)
        }
    }
}
