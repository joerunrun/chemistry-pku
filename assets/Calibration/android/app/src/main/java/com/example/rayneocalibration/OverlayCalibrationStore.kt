package com.example.rayneocalibration

import android.content.Context
import android.os.Environment
import org.json.JSONObject
import java.io.File

object OverlayCalibrationStore {
    data class Calibration(
        val left: DoubleArray,
        val right: DoubleArray,
    )

    fun calibrationFile(context: Context): File {
        return File(
            File(
                context.getExternalFilesDir(Environment.DIRECTORY_DOCUMENTS) ?: context.filesDir,
                "Calibration",
            ),
            "calibration.json",
        )
    }

    fun load(context: Context): Calibration {
        val file = calibrationFile(context)
        if (!file.isFile) {
            error("未找到标定文件：${file.absolutePath}")
        }
        val root = JSONObject(file.readText(Charsets.UTF_8))
        return Calibration(
            left = parseMatrix(root.getJSONObject("left")),
            right = parseMatrix(root.getJSONObject("right")),
        )
    }

    private fun parseMatrix(eye: JSONObject): DoubleArray {
        val rows = eye.getJSONArray("H")
        val values = DoubleArray(9)
        var index = 0
        for (row in 0 until 3) {
            val arr = rows.getJSONArray(row)
            for (col in 0 until 3) {
                values[index++] = arr.getDouble(col)
            }
        }
        return values
    }

    fun project(matrix: DoubleArray, x: Double, y: Double): FloatArray {
        val px = matrix[0] * x + matrix[1] * y + matrix[2]
        val py = matrix[3] * x + matrix[4] * y + matrix[5]
        val pz = matrix[6] * x + matrix[7] * y + matrix[8]
        if (kotlin.math.abs(pz) < 1e-9) {
            return floatArrayOf(Float.NaN, Float.NaN)
        }
        return floatArrayOf((px / pz).toFloat(), (py / pz).toFloat())
    }
}
