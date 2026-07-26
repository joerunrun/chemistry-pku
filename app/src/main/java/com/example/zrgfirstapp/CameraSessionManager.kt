package com.example.zrgfirstapp

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.os.Environment
import android.os.Handler
import android.os.Looper
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.FallbackStrategy
import androidx.camera.video.FileOutputOptions
import androidx.camera.video.PendingRecording
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
    private var cameraGeneration = 0L
    private var recordingDesired = false
    // Student recordings keep the camera audio track. Teacher mode supplies
    // its own AudioRecord stream so CameraX must not open a second mic input.
    private var videoAudioEnabled = true
    private var currentStatus = "未记录"
    private val savedSegments = mutableListOf<File>()
    private val stopCallbacks = mutableListOf<(List<File>) -> Unit>()

    val statusLabel: String
        get() = currentStatus

    fun setVideoAudioEnabled(enabled: Boolean) {
        videoAudioEnabled = enabled
    }

    fun ensureCamera(onReady: () -> Unit, onError: (Throwable) -> Unit) {
        val cameraReady = imageCapture != null &&
            (!recordingDesired || videoCapture != null)
        if (cameraReady) {
            onReady()
            return
        }

        pendingReadyCallbacks += onReady
        pendingErrorCallbacks += onError
        if (cameraInitializing) {
            return
        }

        cameraInitializing = true
        val generation = cameraGeneration
        val providerFuture = ProcessCameraProvider.getInstance(context)
        providerFuture.addListener({
            if (generation != cameraGeneration) {
                return@addListener
            }
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
        savedSegments.clear()
        ensureCamera(
            onReady = { startNewSegmentIfNeeded() },
            onError = { updateStatus("记录异常") },
        )
    }

    fun stopRecording(onStopped: ((List<File>) -> Unit)? = null) {
        if (onStopped != null) {
            stopCallbacks.add(onStopped)
        }
        recordingDesired = false
        mainHandler.removeCallbacksAndMessages(null)
        val recording = activeRecording
        if (recording != null) {
            recording.stop()
        } else {
            updateStatus("未记录")
            deliverStopCallbacks()
        }
    }

    fun isRecordingActive(): Boolean = recordingDesired || activeRecording != null

    fun getRecordedFiles(): List<File> = savedSegments.filter { it.exists() && it.length() > 0 }

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
        cameraGeneration += 1
        cameraInitializing = false
        pendingReadyCallbacks.clear()
        pendingErrorCallbacks.clear()
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
        val video = if (recordingDesired) {
            val recorder = Recorder.Builder()
                .setQualitySelector(
                    QualitySelector.from(
                        Quality.SD,
                        FallbackStrategy.lowerQualityOrHigherThan(Quality.SD),
                    ),
                )
                .build()
            VideoCapture.withOutput(recorder)
        } else {
            null
        }

        try {
            bindUseCasesWithSelector(
                provider,
                CameraSelector.DEFAULT_BACK_CAMERA,
                capture,
                video,
            )
        } catch (_: Throwable) {
            provider.unbindAll()
            bindUseCasesWithSelector(
                provider,
                CameraSelector.DEFAULT_FRONT_CAMERA,
                capture,
                video,
            )
        }

        imageCapture = capture
        videoCapture = video
        updateStatus(if (recordingDesired) "记录准备中" else "相机已就绪")
    }

    private fun bindUseCasesWithSelector(
        provider: ProcessCameraProvider,
        selector: CameraSelector,
        capture: ImageCapture,
        video: VideoCapture<Recorder>?,
    ) {
        if (video == null) {
            provider.bindToLifecycle(lifecycleOwner, selector, capture)
        } else {
            provider.bindToLifecycle(lifecycleOwner, selector, capture, video)
        }
    }

    @SuppressLint("MissingPermission")
    private fun startNewSegmentIfNeeded() {
        if (!recordingDesired || activeRecording != null) {
            return
        }
        val recorder = videoCapture?.output ?: run {
            updateStatus("记录异常")
            return
        }
        cleanupOldPrivateSegments()
        val output = nextPrivateFileOutputOptions()
        var pendingRecording: PendingRecording = recorder.prepareRecording(context, output.options)
        if (videoAudioEnabled &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO)
            == PackageManager.PERMISSION_GRANTED
        ) {
            pendingRecording = pendingRecording.withAudioEnabled()
        }
        val segmentFile = output.file
        activeRecording = pendingRecording.start(mainExecutor) { event ->
                when (event) {
                    is VideoRecordEvent.Start -> {
                        updateStatus("记录中")
                        scheduleSegmentRollover()
                    }

                    is VideoRecordEvent.Finalize -> {
                        activeRecording = null
                        if (!event.hasError() && segmentFile.exists() && segmentFile.length() > 0) {
                            savedSegments.add(segmentFile)
                        }
                        updateStatus(
                            if (event.hasError()) {
                                "记录异常"
                            } else if (recordingDesired) {
                                "记录分段已保存"
                            } else {
                                "未记录"
                            },
                        )
                        if (recordingDesired) {
                            mainHandler.post { startNewSegmentIfNeeded() }
                        } else {
                            deliverStopCallbacks()
                        }
                    }
                }
            }
    }

    private fun deliverStopCallbacks() {
        if (stopCallbacks.isEmpty()) {
            return
        }
        val callbacks = stopCallbacks.toList()
        stopCallbacks.clear()
        val files = getRecordedFiles()
        callbacks.forEach { it.invoke(files) }
    }

    private fun scheduleSegmentRollover() {
        mainHandler.removeCallbacksAndMessages(null)
        mainHandler.postDelayed({
            if (recordingDesired) {
                activeRecording?.stop()
            }
        }, SEGMENT_DURATION_MS)
    }

    private data class PrivateFileOutput(val file: File, val options: FileOutputOptions)

    private fun nextPrivateFileOutputOptions(): PrivateFileOutput {
        val dir = File(
            context.getExternalFilesDir(Environment.DIRECTORY_MOVIES) ?: context.filesDir,
            "ChemistryRecords",
        )
        dir.mkdirs()
        val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val displayName = "chem_record_$timestamp"
        val file = File(dir, "$displayName.mp4")
        return PrivateFileOutput(file, FileOutputOptions.Builder(file).build())
    }

    private fun cleanupOldPrivateSegments() {
        val dir = File(
            context.getExternalFilesDir(Environment.DIRECTORY_MOVIES) ?: context.filesDir,
            "ChemistryRecords",
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
