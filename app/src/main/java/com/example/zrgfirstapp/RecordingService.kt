package com.example.zrgfirstapp

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Binder
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.core.app.NotificationCompat
import androidx.lifecycle.LifecycleService
import java.io.File

internal enum class StudentRecordState { IDLE, STARTING_AUDIO, STARTING_VIDEO, AUDIO_RECORDING, VIDEO_RECORDING, STOPPING }

class RecordingService : LifecycleService() {
    companion object {
        const val ACTION_START_RECORDING = "com.example.zrgfirstapp.START_RECORDING"
        const val ACTION_START_CAPTURE_SESSION = "com.example.zrgfirstapp.START_CAPTURE_SESSION"
        const val ACTION_STOP_RECORDING = "com.example.zrgfirstapp.STOP_RECORDING"
        const val ACTION_START_TEACHER_SESSION = "com.example.zrgfirstapp.START_TEACHER_SESSION"
        const val ACTION_START_STUDENT_SESSION = "com.example.zrgfirstapp.START_STUDENT_SESSION"
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
    private val handler = Handler(Looper.getMainLooper())
    private val studentStore by lazy {
        StudentRecordStore(this) { id, uploaded -> handler.post { onStudentPhotoUpload?.invoke(id, uploaded) } }
    }
    private var studentAudio: StudentAudioCapture? = null
    private var studentDir: File? = null
    private var studentShutdownRequested = false
    private var studentRecovered = false
    internal var studentState = StudentRecordState.IDLE
        private set
    internal var onStudentUtterance: ((StudentUtterance) -> Unit)? = null
    internal var onStudentState: ((StudentRecordState, String) -> Unit)? = null
    internal var onStudentError: ((String) -> Unit)? = null
    internal var onStudentPhotoUpload: ((String, Boolean) -> Unit)? = null
    internal var acceptStudentCommands: Boolean
        get() = studentAudio?.acceptCommands == true
        set(value) { studentAudio?.acceptCommands = value }
    private val retryStudentUploads = object : Runnable {
        override fun run() { studentStore.retry(); handler.postDelayed(this, 30_000L) }
    }

    override fun onCreate() {
        super.onCreate()
        cameraSessionManager = CameraSessionManager(this, this) { status ->
            if (studentAudio == null || studentState !in setOf(StudentRecordState.AUDIO_RECORDING, StudentRecordState.STOPPING)) lastStatus = status
            if (studentAudio != null && studentState == StudentRecordState.STARTING_VIDEO) {
                if (status == "记录中") {
                    val dir = studentDir!!
                    if (studentAudio!!.beginArchive(File(dir, "record_audio.wav"))) {
                        studentStore.markStarted(dir, System.currentTimeMillis())
                        setStudentState(StudentRecordState.VIDEO_RECORDING, "录像中")
                    } else stopStudentRecording(reason = "audio_failed")
                } else if (status.contains("异常") || status.contains("失败")) {
                    stopStudentRecording(reason = "camera_failed")
                }
            } else if (studentState == StudentRecordState.VIDEO_RECORDING && status.contains("异常")) {
                stopStudentRecording(reason = "camera_failed")
            }
            if (foregroundStarted) {
                val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                manager.notify(NOTIFICATION_ID, buildNotification(lastStatus))
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
            ACTION_START_CAPTURE_SESSION -> startCaptureSession()
            ACTION_STOP_RECORDING -> {
                stopRecording()
                if (!teacherSessionActive) stopSelf()
            }
            ACTION_START_TEACHER_SESSION -> startTeacherSession()
            ACTION_START_STUDENT_SESSION -> startStudentSession()
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

    internal fun startStudentSession(): Boolean {
        studentShutdownRequested = false
        if (studentAudio != null) return true
        startAsForegroundIfNeeded(microphoneForegroundType())
        val capture = StudentAudioCapture(
            onUtterance = { phrase -> handler.post { onStudentUtterance?.invoke(phrase) } },
            onError = { error -> handler.post {
                if (studentState != StudentRecordState.IDLE) stopStudentRecording(reason = "microphone_failed")
                studentAudio?.stop()
                studentAudio = null
                onStudentError?.invoke(error.message ?: "麦克风采集中断")
            } },
        )
        studentAudio = capture
        if (!capture.start()) { studentAudio = null; return false }
        studentShutdownRequested = false
        if (!studentRecovered) { studentStore.recover(); studentRecovered = true }
        handler.removeCallbacks(retryStudentUploads)
        handler.post(retryStudentUploads)
        lastStatus = "助手监听中"
        return true
    }

    internal fun startStudentRecording(video: Boolean, baseUrl: String, experiment: String, source: StudentUtterance?): Boolean {
        if (studentState != StudentRecordState.IDLE || !startStudentSession()) return false
        val dir = studentStore.create(if (video) "video" else "audio", baseUrl, DeviceIdentity.hardwareId(this), experiment)
        studentDir = dir
        setStudentState(if (video) StudentRecordState.STARTING_VIDEO else StudentRecordState.STARTING_AUDIO, "记录准备中")
        try {
            if (video) {
                startAsForegroundIfNeeded(cameraAndMicrophoneForegroundType())
                cameraSessionManager.setVideoAudioEnabled(false)
                cameraSessionManager.setStudentOutputDirectory(dir)
                cameraSessionManager.startRecording()
            } else {
                check(studentAudio!!.beginArchive(File(dir, "record_audio.wav"), source)) { "无法开始录音" }
                studentStore.markStarted(dir, System.currentTimeMillis() - studentAudio!!.archiveLeadInMs)
                setStudentState(StudentRecordState.AUDIO_RECORDING, "录音中")
            }
            return true
        } catch (error: Exception) {
            studentAudio?.endArchive()
            studentStore.fail(dir)
            studentDir = null
            setStudentState(StudentRecordState.IDLE, "记录启动失败")
            onStudentError?.invoke(error.message ?: "记录启动失败")
            return false
        }
    }

    internal fun stopStudentRecording(reason: String = "user", onSaved: ((Boolean) -> Unit)? = null) {
        val dir = studentDir ?: return
        if (studentState == StudentRecordState.STOPPING) return
        val video = studentState in setOf(StudentRecordState.STARTING_VIDEO, StudentRecordState.VIDEO_RECORDING)
        setStudentState(StudentRecordState.STOPPING, "正在保存记录")
        val endedAt = UploadMetadata.nowIso()
        val audio = studentAudio?.endArchive() ?: File(dir, "record_audio.wav").takeIf { it.length() > 44 }
        fun finish(files: List<File>) {
            Thread {
                var movie = files.firstOrNull()
                var separateAudio = false
                if (movie != null && audio != null) {
                    val output = File(dir, "record_video.mp4")
                    try { StudentVideoAudio.merge(movie, audio, output); movie = output }
                    catch (error: Exception) {
                        android.util.Log.w("StudentRecord", "Video audio merge failed; keeping both originals", error)
                        separateAudio = true
                    }
                }
                val persisted = runCatching { studentStore.ready(dir, audio, movie, reason = reason, endedAt = endedAt) }
                val saved = audio != null || movie != null
                handler.post {
                    studentDir = null
                    setStudentState(StudentRecordState.IDLE, when {
                        persisted.isFailure -> "文件已保留，保存清单失败"
                        !saved -> "没有可保存的记录"
                        separateAudio -> "已保存在眼镜上，视频音轨分开保存"
                        else -> "已保存在眼镜上，等待上传"
                    })
                    onSaved?.invoke(saved)
                    if (studentShutdownRequested) stopStudentSession()
                }
            }.start()
        }
        if (video) cameraSessionManager.stopRecording { finish(it) } else finish(emptyList())
    }

    internal fun saveStudentPhoto(file: File, baseUrl: String, experiment: String, onQueued: (String) -> Unit) {
        val dir = studentStore.create("photo", baseUrl, DeviceIdentity.hardwareId(this), experiment)
        val saved = File(dir, "photo.jpg")
        file.copyTo(saved, overwrite = true)
        studentStore.ready(dir, null, null, photo = saved, onQueued = onQueued)
        file.delete()
    }

    internal fun stopStudentSession() {
        studentShutdownRequested = true
        if (studentState != StudentRecordState.IDLE) { stopStudentRecording(reason = "app_exit"); return }
        studentAudio?.stop()
        studentAudio = null
        onStudentUtterance = null
        onStudentPhotoUpload = null
        handler.removeCallbacks(retryStudentUploads)
        cameraSessionManager.shutdown()
        stopSelf()
    }

    private fun setStudentState(state: StudentRecordState, status: String) {
        studentState = state
        lastStatus = status
        notifyStatus(status)
        onStudentState?.invoke(state, status)
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

    fun captureStillImage(callback: (File?, Throwable?) -> Unit) {
        // Teacher listening starts as microphone-only. Promote the foreground
        // service before opening CameraX so a backgrounded Activity is still
        // allowed to access the camera on stricter RayNeo builds.
        if (teacherSessionActive || studentAudio != null) {
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

    fun recordingStatus(): String = if (studentAudio != null) {
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
        if (studentAudio != null) {
            startAsForegroundIfNeeded(microphoneForegroundType())
            return
        }
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
        handler.removeCallbacks(retryStudentUploads)
        studentAudio?.stop()
        teacherSessionActive = false
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
            .setSmallIcon(R.drawable.role_launcher_foreground)
            .setContentTitle("化学实验识别")
            .setContentText(status)
            .setOngoing(true)
            .build()
    }
}
