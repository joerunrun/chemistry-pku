package com.example.rayneocalibration

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.graphics.PointF
import androidx.exifinterface.media.ExifInterface
import java.io.File
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

object CrossDetector {
    data class Result(
        val points: List<PointF>,
        val imageWidth: Int,
        val imageHeight: Int,
        val candidateCount: Int,
    )

    private data class Candidate(
        val x: Float,
        val y: Float,
        val score: Double,
        val area: Int,
    )

    fun detect(file: File): Result {
        val oriented = loadOrientedBitmap(file)
            ?: throw IllegalArgumentException("无法读取相机照片")
        val originalWidth = oriented.width
        val originalHeight = oriented.height
        val scale = min(1f, 1280f / max(originalWidth, originalHeight).toFloat())
        val bitmap = if (scale < 1f) {
            Bitmap.createScaledBitmap(
                oriented,
                max(1, (originalWidth * scale).toInt()),
                max(1, (originalHeight * scale).toInt()),
                true,
            ).also { oriented.recycle() }
        } else {
            oriented
        }

        try {
            val width = bitmap.width
            val height = bitmap.height
            val pixels = IntArray(width * height)
            bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
            val dark = ByteArray(pixels.size)
            for (i in pixels.indices) {
                val color = pixels[i]
                val r = color shr 16 and 0xff
                val g = color shr 8 and 0xff
                val b = color and 0xff
                val gray = (r * 30 + g * 59 + b * 11) / 100
                if (gray < 105) dark[i] = 1
            }

            val visited = BooleanArray(pixels.size)
            val queue = IntArray(pixels.size)
            val candidates = mutableListOf<Candidate>()
            val minSize = max(7, min(width, height) / 180)
            val maxSize = max(40, min(width, height) / 7)

            for (start in dark.indices) {
                if (dark[start].toInt() == 0 || visited[start]) continue
                var head = 0
                var tail = 0
                queue[tail++] = start
                visited[start] = true
                var minX = start % width
                var maxX = minX
                var minY = start / width
                var maxY = minY

                while (head < tail) {
                    val index = queue[head++]
                    val x = index % width
                    val y = index / width
                    if (x < minX) minX = x
                    if (x > maxX) maxX = x
                    if (y < minY) minY = y
                    if (y > maxY) maxY = y

                    if (x > 0) {
                        val next = index - 1
                        if (!visited[next] && dark[next].toInt() != 0) {
                            visited[next] = true
                            queue[tail++] = next
                        }
                    }
                    if (x + 1 < width) {
                        val next = index + 1
                        if (!visited[next] && dark[next].toInt() != 0) {
                            visited[next] = true
                            queue[tail++] = next
                        }
                    }
                    if (y > 0) {
                        val next = index - width
                        if (!visited[next] && dark[next].toInt() != 0) {
                            visited[next] = true
                            queue[tail++] = next
                        }
                    }
                    if (y + 1 < height) {
                        val next = index + width
                        if (!visited[next] && dark[next].toInt() != 0) {
                            visited[next] = true
                            queue[tail++] = next
                        }
                    }
                }

                val boxWidth = maxX - minX + 1
                val boxHeight = maxY - minY + 1
                if (boxWidth !in minSize..maxSize || boxHeight !in minSize..maxSize) continue
                val aspect = boxWidth.toDouble() / boxHeight.toDouble()
                if (aspect !in 0.55..1.45) continue
                val fill = tail.toDouble() / (boxWidth * boxHeight).toDouble()
                if (fill !in 0.10..0.52) continue

                val centerX = (minX + maxX) / 2
                val centerY = (minY + maxY) / 2
                val bandX = max(1, boxWidth / 7)
                val bandY = max(1, boxHeight / 7)
                var armPixels = 0
                var cornerPixels = 0
                for (i in 0 until tail) {
                    val index = queue[i]
                    val x = index % width
                    val y = index / width
                    if (abs(x - centerX) <= bandX || abs(y - centerY) <= bandY) {
                        armPixels++
                    } else {
                        cornerPixels++
                    }
                }
                val shapeScore = (armPixels - cornerPixels * 1.2) / tail.toDouble()
                if (shapeScore < 0.35) continue
                candidates += Candidate(
                    x = centerX.toFloat(),
                    y = centerY.toFloat(),
                    score = shapeScore,
                    area = tail,
                )
            }

            val selected = candidates
                .sortedWith(compareByDescending<Candidate> { it.score }.thenByDescending { it.area })
                .take(9)
                .sortedBy { it.y }
            if (selected.size != 9) {
                throw IllegalStateException(
                    "只识别到 ${selected.size} 个十字，请让整张标定纸进入画面",
                )
            }

            val ordered = selected.chunked(3).flatMap { row -> row.sortedBy { it.x } }
            return Result(
                points = ordered.map { PointF(it.x / width, it.y / height) },
                imageWidth = originalWidth,
                imageHeight = originalHeight,
                candidateCount = candidates.size,
            )
        } finally {
            bitmap.recycle()
        }
    }

    private fun loadOrientedBitmap(file: File): Bitmap? {
        val source = BitmapFactory.decodeFile(file.absolutePath) ?: return null
        val orientation = ExifInterface(file).getAttributeInt(
            ExifInterface.TAG_ORIENTATION,
            ExifInterface.ORIENTATION_NORMAL,
        )
        val degrees = when (orientation) {
            ExifInterface.ORIENTATION_ROTATE_90 -> 90f
            ExifInterface.ORIENTATION_ROTATE_180 -> 180f
            ExifInterface.ORIENTATION_ROTATE_270 -> 270f
            else -> 0f
        }
        if (degrees == 0f) return source
        return Bitmap.createBitmap(
            source,
            0,
            0,
            source.width,
            source.height,
            Matrix().apply { postRotate(degrees) },
            true,
        ).also { source.recycle() }
    }
}
