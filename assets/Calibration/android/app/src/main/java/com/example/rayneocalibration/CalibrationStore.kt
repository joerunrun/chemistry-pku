package com.example.rayneocalibration

import android.content.Context
import android.os.Environment
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object CalibrationStore {
    fun save(
        context: Context,
        left: HomographySolver.Result,
        right: HomographySolver.Result,
        leftPoints: List<HomographySolver.PairPoint>,
        rightPoints: List<HomographySolver.PairPoint>,
        imageWidth: Int,
        imageHeight: Int,
    ): File {
        val directory = File(
            context.getExternalFilesDir(Environment.DIRECTORY_DOCUMENTS) ?: context.filesDir,
            "Calibration",
        )
        directory.mkdirs()
        val output = File(directory, "calibration.json")
        val payload = JSONObject()
            .put("version", 1)
            .put(
                "created_at",
                SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssZ", Locale.US).format(Date()),
            )
            .put("camera_width", imageWidth)
            .put("camera_height", imageHeight)
            .put("coordinate_system", "normalized_camera_to_normalized_eye_display")
            .put("left", eyeJson(left, leftPoints))
            .put("right", eyeJson(right, rightPoints))
        output.writeText(payload.toString(2), Charsets.UTF_8)
        return output
    }

    private fun eyeJson(
        result: HomographySolver.Result,
        points: List<HomographySolver.PairPoint>,
    ): JSONObject {
        val matrix = JSONArray()
        for (row in 0 until 3) {
            matrix.put(JSONArray().apply {
                for (column in 0 until 3) put(result.matrix[row * 3 + column])
            })
        }
        val samples = JSONArray()
        points.forEach { point ->
            samples.put(
                JSONObject()
                    .put("camera", JSONArray(listOf(point.cameraX, point.cameraY)))
                    .put("display", JSONArray(listOf(point.displayX, point.displayY)))
                    .put("capture_file", point.captureFile),
            )
        }
        return JSONObject()
            .put("H", matrix)
            .put("rms_error", result.rmsError)
            .put("max_error", result.maxError)
            .put("inliers", result.inlierCount)
            .put("samples", samples)
    }
}
