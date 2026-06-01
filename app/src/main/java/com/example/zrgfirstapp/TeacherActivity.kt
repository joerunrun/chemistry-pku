package com.example.zrgfirstapp

import android.Manifest
import android.content.ContentValues
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.view.GestureDetector
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.VideoView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.max

class TeacherActivity : AppCompatActivity() {
    private lateinit var gestureDetector: GestureDetector

    @Volatile
    private var continuousRecording = false
    private var continuousRecorder: AudioRecord? = null
    private var continuousFile: File? = null
    private var continuousTotalSamples = 0

    private val mainHandler = Handler(Looper.getMainLooper())
    private var activePlayer: MediaPlayer? = null

    private val requestAudioPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            startContinuousRecording()
        } else {
            setResultAndFinish("麦克风未授权", "请在系统设置里允许麦克风权限。")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        setContentView(R.layout.activity_main)
        findViewById<View>(R.id.main).setBackgroundColor(Color.TRANSPARENT)

        gestureDetector = GestureDetector(this, object : GestureDetector.SimpleOnGestureListener() {
            override fun onDown(e: MotionEvent): Boolean = true
            override fun onDoubleTap(e: MotionEvent): Boolean {
                finish()
                return true
            }
        })

        hideAllViews()

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            == PackageManager.PERMISSION_GRANTED
        ) {
            startContinuousRecording()
        } else {
            requestAudioPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (gestureDetector.onTouchEvent(event)) return true
        return super.onTouchEvent(event)
    }

    private fun hideAllViews() {
        val leftPanel = findViewById<View>(R.id.left_eye_panel)
        val rightPanel = findViewById<View>(R.id.right_eye_panel)
        listOf(leftPanel, rightPanel).forEach { panel ->
            panel?.findViewById<TextView>(R.id.title_text)?.visibility = View.GONE
            panel?.findViewById<TextView>(R.id.subtitle_text)?.visibility = View.GONE
            panel?.findViewById<LinearLayout>(R.id.options_row)?.visibility = View.GONE
            panel?.findViewById<ImageView>(R.id.detection_preview)?.visibility = View.GONE
            panel?.findViewById<FrameLayout>(R.id.detection_overlay_container)?.visibility = View.GONE
            panel?.findViewById<VideoView>(R.id.demo_video_view)?.visibility = View.GONE
            panel?.findViewById<ProgressBar>(R.id.progress)?.visibility = View.GONE
            panel?.findViewById<TextView>(R.id.thinking_text)?.visibility = View.GONE
            panel?.findViewById<TextView>(R.id.status_text)?.visibility = View.GONE
            panel?.findViewById<TextView>(R.id.result_text)?.visibility = View.GONE
        }
    }

    @Suppress("MissingPermission")
    private fun startContinuousRecording() {
        if (continuousRecording) return
        continuousRecording = true
        val file = File(cacheDir, "teacher_cont_${System.currentTimeMillis()}.wav")
        continuousFile = file
        continuousTotalSamples = 0
        val recorder = createRecorder()
        continuousRecorder = recorder
        Thread {
            try {
                FileOutputStream(file).use { output ->
                    writeWavHeader(output, 0)
                    val minBuf = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
                    val buffer = ShortArray(max(minBuf, 3200) / 2)
                    recorder.startRecording()
                    while (continuousRecording) {
                        val read = recorder.read(buffer, 0, buffer.size)
                        if (read > 0) {
                            writePcm16(output, buffer, read)
                            continuousTotalSamples += read
                        }
                    }
                    recorder.stop()
                    rewriteWavHeader(file, continuousTotalSamples)
                }
            } catch (_: Exception) {
            } finally {
                recorder.release()
                if (continuousRecorder === recorder) continuousRecorder = null
            }
        }.start()
    }

    private fun stopContinuousRecording() {
        continuousRecording = false
        continuousRecorder?.let {
            try { it.stop() } catch (_: Exception) {}
        }
        try { Thread.sleep(300) } catch (_: Exception) {}
        val file = continuousFile
        if (file != null && file.exists() && file.length() > 44) {
            saveRecordingToMediaStore(file)
        }
        continuousFile?.delete()
        continuousFile = null
        continuousRecorder = null
    }

    @Suppress("MissingPermission")
    private fun createRecorder(): AudioRecord {
        val minBuffer = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
        if (minBuffer <= 0) {
            throw IOException("麦克风初始化失败")
        }
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            SAMPLE_RATE,
            CHANNEL_CONFIG,
            AUDIO_FORMAT,
            max(minBuffer, 6400),
        )
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            throw IOException("麦克风不可用")
        }
        return recorder
    }

    private fun saveRecordingToMediaStore(file: File) {
        val name = "teacher_record_${SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())}.wav"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val values = ContentValues().apply {
                put(MediaStore.Audio.Media.DISPLAY_NAME, name)
                put(MediaStore.Audio.Media.MIME_TYPE, "audio/wav")
                put(MediaStore.Audio.Media.RELATIVE_PATH, Environment.DIRECTORY_MUSIC + "/ChemTeacherRecords")
                put(MediaStore.Audio.Media.IS_PENDING, 1)
            }
            val uri = contentResolver.insert(MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, values)
                ?: return
            contentResolver.openOutputStream(uri)?.use { output ->
                FileInputStream(file).use { input -> input.copyTo(output) }
            }
            values.clear()
            values.put(MediaStore.Audio.Media.IS_PENDING, 0)
            contentResolver.update(uri, values, null, null)
            return
        }

        val dir = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MUSIC), "ChemTeacherRecords")
        dir.mkdirs()
        val target = File(dir, name)
        FileInputStream(file).use { input ->
            FileOutputStream(target).use { output -> input.copyTo(output) }
        }
    }

    private fun setResultAndFinish(status: String, result: String) {
        finish()
    }

    private fun playPrompt(name: String) {
        val resId = resources.getIdentifier(name, "raw", packageName)
        if (resId == 0) return
        activePlayer?.release()
        activePlayer = MediaPlayer.create(this, resId)?.apply {
            setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANCE_ACCESSIBILITY)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            setOnCompletionListener {
                it.release()
                if (activePlayer === it) activePlayer = null
            }
            start()
        }
    }

    override fun onDestroy() {
        stopContinuousRecording()
        activePlayer?.release()
        activePlayer = null
        super.onDestroy()
    }

    companion object {
        private const val SAMPLE_RATE = 16000
        private const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        private const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT

        private fun writePcm16(output: FileOutputStream, buffer: ShortArray, count: Int) {
            val bytes = ByteArray(count * 2)
            for (i in 0 until count) {
                val value = buffer[i].toInt()
                bytes[i * 2] = (value and 0xff).toByte()
                bytes[i * 2 + 1] = ((value shr 8) and 0xff).toByte()
            }
            output.write(bytes)
        }

        private fun writeWavHeader(output: FileOutputStream, samples: Int) {
            output.write(buildWavHeader(samples))
        }

        private fun rewriteWavHeader(file: File, samples: Int) {
            java.io.RandomAccessFile(file, "rw").use { raf ->
                raf.seek(0)
                raf.write(buildWavHeader(samples))
            }
        }

        private fun buildWavHeader(samples: Int): ByteArray {
            val byteRate = SAMPLE_RATE * 2
            val dataSize = samples * 2
            val totalSize = dataSize + 36
            return ByteArray(44).also { h ->
                fun text(offset: Int, value: String) {
                    value.toByteArray(Charsets.US_ASCII).copyInto(h, offset)
                }
                fun le32(offset: Int, value: Int) {
                    h[offset] = (value and 0xff).toByte()
                    h[offset + 1] = ((value shr 8) and 0xff).toByte()
                    h[offset + 2] = ((value shr 16) and 0xff).toByte()
                    h[offset + 3] = ((value shr 24) and 0xff).toByte()
                }
                fun le16(offset: Int, value: Int) {
                    h[offset] = (value and 0xff).toByte()
                    h[offset + 1] = ((value shr 8) and 0xff).toByte()
                }
                text(0, "RIFF")
                le32(4, totalSize)
                text(8, "WAVE")
                text(12, "fmt ")
                le32(16, 16)
                le16(20, 1)
                le16(22, 1)
                le32(24, SAMPLE_RATE)
                le32(28, byteRate)
                le16(32, 2)
                le16(34, 16)
                text(36, "data")
                le32(40, dataSize)
            }
        }
    }
}