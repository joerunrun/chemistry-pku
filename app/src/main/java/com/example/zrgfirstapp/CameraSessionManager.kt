package com.example.zrgfirstapp

import android.content.ContentValues
import android.content.Context
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.provider.MediaStore.Video.Media.DATE_TAKEN
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.FallbackStrategy
import androidx.camera.video.FileOutputOptions
import androidx.camera.video.MediaStoreOutputOptions
import androidx.camera.video.Quality
import androidx.camera.video.QualitySelector
import androidx.camera.video.Recorder
import androidx.camera.video.Recording
import androidx.camera.video.VideoCapture
import androidx.camera.video.VideoRecordEvent
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class CameraSessionManager(
    private val context: Context,
    private val lifecycleOwner: LifecycleOwner,
    private val statusListener: (String) -> Unit,
) {
    companion object {
        private const val SEGMENT_DURATION_MS = 5 * 60 * 1000L
        private const val MAX_SEGMENTS = 20
    }

    private val mainExecutor = ContextCompat.getMainExecutor(context)
    private val mainHandler = Handler(Looper.getMainLooper())
    private val pendingReadyCallbacks = mutableListOf<() -> Unit>()
    private val pendingErrorCallbacks = mutableListOf<(Throwable) -> Unit>()

    private var cameraProvider: ProcessCameraProvider? = null
    private var imageCapture: ImageCapture? = null
    private var videoCapture: VideoCapture<Recorder>? = null
    private var activeRecording: Recording? = null
    private var cameraInitializing = false
    private var recordingDesired = false
    private var currentStatus = "未录像"

    val statusLabel: String
        get() = currentStatus

    fun ensureCamera(onReady: () -> Unit, onError: (Throwable) -> Unit) {
        if (imageCapture != null && videoCapture != null) {
            onReady()
            return
        }

        pendingReadyCallbacks += onReady
        pendingErrorCallbacks += onError
        if (cameraInitializing) {
            return
        }

        cameraInitializing = true
        val providerFuture = ProcessCameraProvider.getInstance(context)
        providerFuture.addListener({
            try {
                val provider = providerFuture.get()
                cameraProvider = provider
                bindUseCases(provider)
                cameraInitializing = false
                val callbacks = pendingReadyCallbacks.toList()
                pendingReadyCallbacks.clear()
                pendingErrorCallbacks.clear()
                callbacks.forEach { it.invoke() }
            } catch (exc: Throwable) {
                cameraInitializing = false
                updateStatus("相机初始化失败")
                val callbacks = pendingErrorCallbacks.toList()
                pendingReadyCallbacks.clear()
                pendingErrorCallbacks.clear()
                callbacks.forEach { it.invoke(exc) }
            }
        }, mainExecutor)
    }

    fun startRecording() {
        recordingDesired = true
        ensureCamera(
            onReady = { startNewSegmentIfNeeded() },
            onError = { updateStatus("录像异常") },
        )
    }

    fun stopRecording() {
        recordingDesired = false
        mainHandler.removeCallbacksAndMessages(null)
        activeRecording?.stop()
        activeRecording = null
        updateStatus("未录像")
    }

    fun isRecordingActive(): Boolean = recordingDesired || activeRecording != null

    fun captureStillImage(outputFile: File, callback: (File?, Throwable?) -> Unit) {
        ensureCamera(
            onReady = {
                val capture = imageCapture
                if (capture == null) {
                    callback(null, IllegalStateException("相机未就绪"))
                    return@ensureCamera
                }
                outputFile.parentFile?.mkdirs()
                val options = ImageCapture.OutputFileOptions.Builder(outputFile).build()
                capture.takePicture(
                    options,
                    mainExecutor,
                    object : ImageCapture.OnImageSavedCallback {
                        override fun onImageSaved(outputFileResults: ImageCapture.OutputFileResults) {
                            callback(outputFile, null)
                        }

                        override fun onError(exception: ImageCaptureException) {
                            callback(null, exception)
                        }
                    },
                )
            },
            onError = { callback(null, it) },
        )
    }

    fun shutdown() {
        stopRecording()
        cameraProvider?.unbindAll()
        cameraProvider = null
        imageCapture = null
        videoCapture = null
    }

    private fun bindUseCases(provider: ProcessCameraProvider) {
        provider.unbindAll()
        val capture = ImageCapture.Builder()
            .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
            .build()
        val recorder = Recorder.Builder()
            .setQualitySelector(
                QualitySelector.from(
                    Quality.SD,
                    FallbackStrategy.lowerQualityOrHigherThan(Quality.SD),
                ),
            )
            .build()
        val video = VideoCapture.withOutput(recorder)

        try {
            provider.bindToLifecycle(
                lifecycleOwner,
                CameraSelector.DEFAULT_BACK_CAMERA,
                capture,
                video,
            )
        } catch (_: Throwable) {
            provider.unbindAll()
            provider.bindToLifecycle(
                lifecycleOwner,
                CameraSelector.DEFAULT_FRONT_CAMERA,
                capture,
                video,
            )
        }

        imageCapture = capture
        videoCapture = video
        updateStatus(if (recordingDesired) "录像准备中" else "相机已就绪")
    }

    private fun startNewSegmentIfNeeded() {
        if (!recordingDesired || activeRecording != null) {
            return
        }
        val recorder = videoCapture?.output ?: run {
            updateStatus("录像异常")
            return
        }
        cleanupOldPrivateSegments()
        val pendingRecording = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            recorder.prepareRecording(context, nextMediaStoreOutputOptions())
        } else {
            recorder.prepareRecording(context, nextPrivateFileOutputOptions())
        }
        activeRecording = pendingRecording.start(mainExecutor) { event ->
                when (event) {
                    is VideoRecordEvent.Start -> {
                        updateStatus("录像中")
                        scheduleSegmentRollover()
                    }

                    is VideoRecordEvent.Finalize -> {
                        activeRecording = null
                        updateStatus(
                            if (event.hasError()) {
                                "录像异常"
                            } else if (recordingDesired) {
                                "录像分段已保存"
                            } else {
                                "未录像"
                            },
                        )
                        if (recordingDesired) {
                            mainHandler.post { startNewSegmentIfNeeded() }
                        }
                    }
                }
            }
    }

    private fun scheduleSegmentRollover() {
        mainHandler.removeCallbacksAndMessages(null)
        mainHandler.postDelayed({
            if (recordingDesired) {
                activeRecording?.stop()
            }
        }, SEGMENT_DURATION_MS)
    }

    private fun nextMediaStoreOutputOptions(): MediaStoreOutputOptions {
        val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val displayName = "chem_recording_$timestamp.mp4"
        val values = ContentValues().apply {
            put(MediaStore.Video.Media.DISPLAY_NAME, displayName)
            put(MediaStore.Video.Media.MIME_TYPE, "video/mp4")
            put(MediaStore.Video.Media.RELATIVE_PATH, "DCIM/Camera")
            put(DATE_TAKEN, System.currentTimeMillis())
        }
        return MediaStoreOutputOptions.Builder(
            context.contentResolver,
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
        )
            .setContentValues(values)
            .build()
    }

    private fun nextPrivateFileOutputOptions(): FileOutputOptions {
        val dir = File(
            context.getExternalFilesDir(Environment.DIRECTORY_MOVIES) ?: context.filesDir,
            "chem_recordings",
        )
        dir.mkdirs()
        val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val displayName = "chem_recording_$timestamp"
        return FileOutputOptions.Builder(File(dir, "$displayName.mp4")).build()
    }

    private fun cleanupOldPrivateSegments() {
        val dir = File(
            context.getExternalFilesDir(Environment.DIRECTORY_MOVIES) ?: context.filesDir,
            "chem_recordings",
        )
        val files = dir.listFiles { file -> file.extension.equals("mp4", ignoreCase = true) }
            ?.sortedByDescending { it.lastModified() }
            .orEmpty()
        files.drop(MAX_SEGMENTS).forEach { it.delete() }
    }

    private fun updateStatus(status: String) {
        currentStatus = status
        statusListener(status)
    }
}
