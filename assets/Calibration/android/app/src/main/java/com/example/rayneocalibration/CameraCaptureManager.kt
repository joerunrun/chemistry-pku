package com.example.rayneocalibration

import android.annotation.SuppressLint
import android.app.Activity
import android.os.Build
import android.view.Surface
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import java.io.File

class CameraCaptureManager(
    private val activity: Activity,
    private val lifecycleOwner: LifecycleOwner,
) {
    private val mainExecutor = ContextCompat.getMainExecutor(activity)
    private var provider: ProcessCameraProvider? = null
    private var imageCapture: ImageCapture? = null

    @SuppressLint("MissingPermission")
    fun start(onReady: () -> Unit, onError: (Throwable) -> Unit) {
        if (imageCapture != null) {
            onReady()
            return
        }
        val future = ProcessCameraProvider.getInstance(activity)
        future.addListener({
            try {
                val cameraProvider = future.get()
                provider = cameraProvider
                val rotation = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    activity.display?.rotation ?: Surface.ROTATION_0
                } else {
                    @Suppress("DEPRECATION")
                    activity.windowManager.defaultDisplay.rotation
                }
                val capture = ImageCapture.Builder()
                    .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                    .setTargetRotation(rotation)
                    .build()
                cameraProvider.unbindAll()
                try {
                    cameraProvider.bindToLifecycle(
                        lifecycleOwner,
                        CameraSelector.DEFAULT_BACK_CAMERA,
                        capture,
                    )
                } catch (_: Throwable) {
                    cameraProvider.unbindAll()
                    cameraProvider.bindToLifecycle(
                        lifecycleOwner,
                        CameraSelector.DEFAULT_FRONT_CAMERA,
                        capture,
                    )
                }
                imageCapture = capture
                onReady()
            } catch (error: Throwable) {
                onError(error)
            }
        }, mainExecutor)
    }

    fun capture(output: File, callback: (File?, Throwable?) -> Unit) {
        val capture = imageCapture
        if (capture == null) {
            callback(null, IllegalStateException("相机尚未就绪"))
            return
        }
        output.parentFile?.mkdirs()
        capture.takePicture(
            ImageCapture.OutputFileOptions.Builder(output).build(),
            mainExecutor,
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(result: ImageCapture.OutputFileResults) {
                    callback(output, null)
                }

                override fun onError(exception: ImageCaptureException) {
                    callback(null, exception)
                }
            },
        )
    }

    fun shutdown() {
        provider?.unbindAll()
        provider = null
        imageCapture = null
    }
}
