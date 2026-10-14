package com.example.zrgfirstapp

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.RandomAccessFile
import java.util.concurrent.atomic.AtomicBoolean

internal data class StudentUtterance(val pcm: ByteArray, val endedAtSample: Long)

/** Speech ending after 700ms of silence, bounded to eight seconds. */
internal class StudentUtteranceBuffer {
    private val bytes = ByteArrayOutputStream()
    private var silenceBytes = 0
    private var voicedBytes = 0

    fun clear() { bytes.reset(); silenceBytes = 0; voicedBytes = 0 }

    fun append(pcm: ByteArray, speech: Boolean): ByteArray? {
        if (speech) { voicedBytes += pcm.size; silenceBytes = 0 }
        else silenceBytes += pcm.size
        if (speech || bytes.size() > 0) bytes.write(pcm)
        if (bytes.size() == 0 || (silenceBytes < 22_400 && bytes.size() < 256_000)) return null
        val utterance = if (voicedBytes >= 6_400) bytes.toByteArray() else null
        clear()
        return utterance
    }
}

/** Owns the only student microphone. Cloud requests never stop its reader. */
internal class StudentAudioCapture(
    private val onUtterance: (StudentUtterance) -> Unit,
    private val onError: (Throwable) -> Unit,
) {
    private val running = AtomicBoolean(false)
    private val ring = PcmRingBuffer(SAMPLE_RATE * 2 * 30)
    private val phrases = StudentUtteranceBuffer()
    @Volatile var acceptCommands = false
    @Volatile private var recorder: AudioRecord? = null
    private var totalSamples = 0L
    private var archive: RandomAccessFile? = null
    private var archiveFile: File? = null

    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        if (running.get()) return true
        return try {
            val min = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            check(min > 0) { "麦克风不可用" }
            val mic = AudioRecord(MediaRecorder.AudioSource.MIC, SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, maxOf(min, 6400))
            if (mic.state != AudioRecord.STATE_INITIALIZED) { mic.release(); error("麦克风初始化失败") }
            try { mic.startRecording() } catch (error: Exception) { mic.release(); throw error }
            recorder = mic
            running.set(true)
            Thread {
                try {
                    val buffer = ByteArray(3200)
                    while (running.get()) {
                        val read = mic.read(buffer, 0, buffer.size)
                        check(read > 0 || !running.get()) { "麦克风采集失败：$read" }
                        if (read <= 0) break
                        val pcm = buffer.copyOf(read - read % 2)
                        val endSample = synchronized(this) {
                            ring.append(pcm)
                            totalSamples += pcm.size / 2
                            archive?.write(pcm)
                            totalSamples
                        }
                        if (!acceptCommands) { phrases.clear(); continue }
                        val sentence = phrases.append(pcm, PcmAudioGate.hasMeaningfulAudio(pcm, 120.0, 900))
                        if (sentence != null) onUtterance(StudentUtterance(sentence, endSample))
                    }
                } catch (error: Throwable) {
                    if (running.get()) onError(error)
                } finally {
                    running.set(false)
                    recorder = null
                    mic.release()
                    endArchive()
                }
            }.start()
            true
        } catch (error: Exception) { onError(error); false }
    }

    @Synchronized
    fun beginArchive(file: File, source: StudentUtterance? = null): Boolean {
        if (!running.get() || archive != null) return false
        file.parentFile?.mkdirs()
        val output = RandomAccessFile(file, "rw")
        try {
            output.setLength(0)
            output.write(ByteArray(44))
            if (source != null) {
                val tailSize = ((totalSamples - source.endedAtSample).coerceAtLeast(0) * 2).toInt()
                val tail = if (tailSize == 0) ByteArray(0) else ring.snapshot(tailSize)
                if (tail != null) { output.write(source.pcm); output.write(tail) }
            }
            archive = output
            archiveFile = file
            return true
        } catch (error: Exception) { output.close(); throw error }
    }

    @Synchronized
    fun endArchive(): File? {
        val file = archiveFile ?: return null
        archive?.let { output -> writeHeader(output); output.close() }
        archive = null
        archiveFile = null
        return file.takeIf { it.length() > 44 }
    }

    fun stop() {
        acceptCommands = false
        running.set(false)
        runCatching { recorder?.stop() }
    }

    companion object Header {
        const val SAMPLE_RATE = 16_000
        fun repair(file: File) {
            if (file.length() >= 44) RandomAccessFile(file, "rw").use { writeHeader(it) }
        }
        private fun writeHeader(output: RandomAccessFile) {
            val size = (output.length() - 44).coerceAtLeast(0).toInt()
            fun int(value: Int) { output.write(byteArrayOf(value.toByte(), (value shr 8).toByte(), (value shr 16).toByte(), (value shr 24).toByte())) }
            fun short(value: Int) { output.write(byteArrayOf(value.toByte(), (value shr 8).toByte())) }
            output.seek(0)
            output.writeBytes("RIFF"); int(size + 36); output.writeBytes("WAVEfmt ")
            int(16); short(1); short(1); int(16_000); int(32_000); short(2); short(16)
            output.writeBytes("data"); int(size)
        }
    }
}
