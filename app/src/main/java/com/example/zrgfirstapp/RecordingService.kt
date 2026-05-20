package com.example.zrgfirstapp

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Binder
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.lifecycle.LifecycleService
import java.io.File

class RecordingService : LifecycleService() {
    companion object {
        const val ACTION_START_RECORDING = "com.example.zrgfirstapp.START_RECORDING"
        const val ACTION_START_CAPTURE_SESSION = "com.example.zrgfirstapp.START_CAPTURE_SESSION"
        const val ACTION_STOP_RECORDING = "com.example.zrgfirstapp.STOP_RECORDING"
        private const val CHANNEL_ID = "chem_recording"
        private const val NOTIFICATION_ID = 1208
    }

    inner class LocalBinder : Binder() {
        fun service(): RecordingService = this@RecordingService
    }

    private val binder = LocalBinder()
    private lateinit var cameraSessionManager: CameraSessionManager
    private var foregroundStarted = false
    private var lastStatus = "未录像"

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
            ACTION_START_RECORDING -> startRecording()
            ACTION_START_CAPTURE_SESSION -> startCaptureSession()
            ACTION_STOP_RECORDING -> {
                stopRecording()
                stopSelf()
            }
        }
        return Service.START_STICKY
    }

    fun startRecording() {
        startAsForegroundIfNeeded()
        lastStatus = "录像准备中"
        cameraSessionManager.startRecording()
    }

    fun startCaptureSession() {
        startAsForegroundIfNeeded()
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

    fun stopRecording() {
        cameraSessionManager.stopRecording()
        lastStatus = "未录像"
        if (foregroundStarted) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            foregroundStarted = false
        }
    }

    fun captureStillImage(callback: (File?, Throwable?) -> Unit) {
        val dir = File(cacheDir, "glasses_images")
        dir.mkdirs()
        val file = File(dir, "capture_${System.currentTimeMillis()}.jpg")
        cameraSessionManager.captureStillImage(file, callback)
    }

    fun recordingStatus(): String = cameraSessionManager.statusLabel.ifBlank { lastStatus }

    fun stopCaptureSessionIfIdle() {
        if (cameraSessionManager.isRecordingActive()) {
            return
        }
        cameraSessionManager.shutdown()
        lastStatus = "未录像"
        if (foregroundStarted) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            foregroundStarted = false
        }
        stopSelf()
    }

    override fun onDestroy() {
        cameraSessionManager.shutdown()
        super.onDestroy()
    }

    private fun startAsForegroundIfNeeded() {
        if (foregroundStarted) {
            return
        }
        createNotificationChannel()
        startForeground(NOTIFICATION_ID, buildNotification("录像准备中"))
        foregroundStarted = true
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val channel = NotificationChannel(
                CHANNEL_ID,
                "化学实验后台录像",
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
