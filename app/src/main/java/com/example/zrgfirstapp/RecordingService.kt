package com.example.zrgfirstapp

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaRecorder
import android.os.Binder
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.lifecycle.LifecycleService
import java.io.File

class RecordingService : LifecycleService() {
    companion object {
        const val ACTION_START_RECORDING = "com.example.zrgfirstapp.START_RECORDING"
        const val ACTION_START_AUDIO_RECORDING = "com.example.zrgfirstapp.START_AUDIO_RECORDING"
        const val ACTION_START_CAPTURE_SESSION = "com.example.zrgfirstapp.START_CAPTURE_SESSION"
        const val ACTION_STOP_RECORDING = "com.example.zrgfirstapp.STOP_RECORDING"
        const val ACTION_STOP_AUDIO_RECORDING = "com.example.zrgfirstapp.STOP_AUDIO_RECORDING"
        const val ACTION_START_TEACHER_SESSION = "com.example.zrgfirstapp.START_TEACHER_SESSION"
        const val ACTION_STOP_TEACHER_SESSION = "com.example.zrgfirstapp.STOP_TEACHER_SESSION"
        const val EXTRA_VIDEO_AUDIO_ENABLED = "com.example.zrgfirstapp.VIDEO_AUDIO_ENABLED"
        private const val CHANNEL_ID = "chem_recording"
        private const val NOTIFICATION_ID = 1208
    }

    inner class LocalBinder : Binder() {
        fun service(): RecordingService = this@RecordingService
    }

    private val binder = LocalBinder()
    private lateinit var cameraSessionManager: CameraSessionManager
    private var foregroundStarted = false
    private var teacherSessionActive = false
    private var lastStatus = "未记录"
    private var audioRecorder: MediaRecorder? = null
    private var audioRecordingFile: File? = null

    override fun onCreate() {
        super.onCreate()
        cameraSessionManager = CameraSessionManager(this, this) { status ->
            lastStatus = status
            if (foregroundStarted) {
                val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                manager.notify(NOTIFICATION_ID, buildNotification(status))
            }
        }
    }

    override fun onBind(intent: Intent): android.os.IBinder {
        super.onBind(intent)
        return binder
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        when (intent?.action) {
            ACTION_START_RECORDING -> {
                val audioEnabled = if (intent.hasExtra(EXTRA_VIDEO_AUDIO_ENABLED)) {
                    intent.getBooleanExtra(EXTRA_VIDEO_AUDIO_ENABLED, true)
                } else {
                    null
                }
                startRecording(audioEnabled)
            }
            ACTION_START_AUDIO_RECORDING -> startAudioRecording()
            ACTION_START_CAPTURE_SESSION -> startCaptureSession()
            ACTION_STOP_RECORDING -> {
                stopRecording()
                if (!teacherSessionActive) stopSelf()
            }
            ACTION_STOP_AUDIO_RECORDING -> {
                stopAudioRecording()
                if (!teacherSessionActive) stopSelf()
            }
            ACTION_START_TEACHER_SESSION -> startTeacherSession()
            ACTION_STOP_TEACHER_SESSION -> stopTeacherSession()
        }
        return Service.START_STICKY
    }

    fun startRecording(videoAudioEnabled: Boolean? = null) {
        videoAudioEnabled?.let { cameraSessionManager.setVideoAudioEnabled(it) }
        startAsForegroundIfNeeded(cameraAndMicrophoneForegroundType())
        lastStatus = "记录准备中"
        cameraSessionManager.startRecording()
    }

    @Suppress("DEPRECATION")
    fun startAudioRecording() {
        if (audioRecorder != null) return
        startAsForegroundIfNeeded(microphoneForegroundType())
        val file = File(cacheDir, "chem_audio_records/audio_${System.currentTimeMillis()}.m4a")
        file.parentFile?.mkdirs()
        val recorder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            MediaRecorder(this)
        } else {
            MediaRecorder()
        }
        try {
            recorder.setAudioSource(MediaRecorder.AudioSource.MIC)
            recorder.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            recorder.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            recorder.setAudioSamplingRate(16_000)
            recorder.setAudioEncodingBitRate(64_000)
            recorder.setOutputFile(file.absolutePath)
            recorder.prepare()
            recorder.start()
            audioRecorder = recorder
            audioRecordingFile = file
            lastStatus = "录音中"
            notifyStatus(lastStatus)
        } catch (error: Exception) {
            recorder.release()
            file.delete()
            lastStatus = "录音初始化失败"
            notifyStatus(lastStatus)
        }
    }

    fun startTeacherSession() {
        teacherSessionActive = true
        cameraSessionManager.setVideoAudioEnabled(false)
        startAsForegroundIfNeeded(microphoneForegroundType())
        lastStatus = "教师助手监听中"
    }

    fun stopTeacherSession() {
        teacherSessionActive = false
        if (cameraSessionManager.isRecordingActive()) {
            return
        }
        cameraSessionManager.shutdown()
        lastStatus = "未记录"
        if (foregroundStarted) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            foregroundStarted = false
        }
        stopSelf()
    }

    fun startCaptureSession() {
        startAsForegroundIfNeeded(cameraAndMicrophoneForegroundType())
        lastStatus = "相机准备中"
        cameraSessionManager.ensureCamera(
            onReady = {
                lastStatus = cameraSessionManager.statusLabel
            },
            onError = {
                lastStatus = "相机初始化失败"
            },
        )
    }

    fun stopRecording(onStopped: ((List<File>) -> Unit)? = null) {
        cameraSessionManager.stopRecording(onStopped)
        lastStatus = "未记录"
        if (foregroundStarted && !teacherSessionActive) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            foregroundStarted = false
        }
    }

    @Suppress("DEPRECATION")
    fun stopAudioRecording(onStopped: ((List<File>) -> Unit)? = null) {
        val recorder = audioRecorder
        val file = audioRecordingFile
        audioRecorder = null
        audioRecordingFile = null
        if (recorder != null) {
            try {
                recorder.stop()
            } catch (_: RuntimeException) {
                file?.delete()
            }
            recorder.release()
        }
        lastStatus = "未记录"
        if (foregroundStarted && !teacherSessionActive) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            foregroundStarted = false
        }
        val files = if (file != null && file.isFile && file.length() > 0L) listOf(file) else emptyList()
        onStopped?.invoke(files)
    }

    fun captureStillImage(callback: (File?, Throwable?) -> Unit) {
        // Teacher listening starts as microphone-only. Promote the foreground
        // service before opening CameraX so a backgrounded Activity is still
        // allowed to access the camera on stricter RayNeo builds.
        if (teacherSessionActive) {
            try {
                startAsForegroundIfNeeded(cameraAndMicrophoneForegroundType())
            } catch (error: Throwable) {
                callback(null, error)
                return
            }
        }
        val dir = File(cacheDir, "glasses_images")
        dir.mkdirs()
        val file = File(dir, "capture_${System.currentTimeMillis()}.jpg")
        cameraSessionManager.captureStillImage(file, callback)
    }

    fun recordingStatus(): String = if (audioRecorder != null) {
        lastStatus
    } else {
        cameraSessionManager.statusLabel.ifBlank { lastStatus }
    }

    fun getRecordedFiles(): List<File> = cameraSessionManager.getRecordedFiles()

    fun stopCaptureSessionIfIdle() {
        if (cameraSessionManager.isRecordingActive()) {
            return
        }
        cameraSessionManager.shutdown()
        if (teacherSessionActive) {
            // Keep microphone listening active, but release the camera type
            // immediately after the still image is complete.
            startAsForegroundIfNeeded(microphoneForegroundType())
            return
        }
        lastStatus = "未记录"
        if (foregroundStarted && !teacherSessionActive) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            foregroundStarted = false
        }
        if (!teacherSessionActive) stopSelf()
    }

    override fun onDestroy() {
        teacherSessionActive = false
        stopAudioRecording()
        cameraSessionManager.shutdown()
        super.onDestroy()
    }

    private fun notifyStatus(status: String) {
        if (foregroundStarted) {
            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            manager.notify(NOTIFICATION_ID, buildNotification(status))
        }
    }

    private fun startAsForegroundIfNeeded(foregroundType: Int? = null) {
        if (foregroundStarted) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && foregroundType != null) {
                startForeground(NOTIFICATION_ID, buildNotification(lastStatus), foregroundType)
            }
            return
        }
        createNotificationChannel()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && foregroundType != null) {
            startForeground(NOTIFICATION_ID, buildNotification("记录准备中"), foregroundType)
        } else {
            startForeground(NOTIFICATION_ID, buildNotification("记录准备中"))
        }
        foregroundStarted = true
    }

    private fun microphoneForegroundType(): Int {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        } else {
            0
        }
    }

    private fun cameraAndMicrophoneForegroundType(): Int {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA or
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        } else {
            0
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val channel = NotificationChannel(
                CHANNEL_ID,
                "化学实验记录",
                NotificationManager.IMPORTANCE_LOW,
            )
            manager.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(status: String): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("化学实验识别")
            .setContentText(status)
            .setOngoing(true)
            .build()
    }
}
