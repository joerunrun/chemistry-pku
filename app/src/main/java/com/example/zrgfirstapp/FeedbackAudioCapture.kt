package com.example.zrgfirstapp

import android.content.Context
import android.os.Handler
import android.os.Looper
import java.io.File
import java.io.RandomAccessFile

/** Copies the teacher's existing microphone stream into a durable feedback recording. */
internal class FeedbackAudioCapture(context: Context, onUpload: (String, Boolean) -> Unit) {
    private val handler = Handler(Looper.getMainLooper())
    private val store = StudentRecordStore(context, onFeedbackUpload = onUpload)
    private var directory: File? = null
    private var output: RandomAccessFile? = null
    @Volatile var active = false
        private set
    private val retry = object : Runnable {
        override fun run() { store.retry(); handler.postDelayed(this, 30_000L) }
    }

    init { store.recover(); handler.post(retry) }

    @Synchronized
    fun start(baseUrl: String, deviceId: String) {
        if (active) return
        val dir = store.create("feedback", baseUrl, deviceId, "unknown_experiment")
        output = RandomAccessFile(File(dir, "record_audio.wav"), "rw").apply { write(ByteArray(44)) }
        directory = dir
        active = true
    }

    @Synchronized
    fun append(pcm: ByteArray) { if (active) output?.write(pcm) }

    @Synchronized
    fun finish(): String? {
        val dir = directory ?: return null
        active = false
        output?.close()
        output = null
        directory = null
        val audio = File(dir, "record_audio.wav")
        StudentAudioCapture.repair(audio)
        store.ready(dir, audio.takeIf { it.length() > 44 }, null)
        return dir.name
    }

    fun close() { finish(); handler.removeCallbacks(retry) }
}
