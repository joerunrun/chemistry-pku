package com.example.zrgfirstapp

import android.app.Activity
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.MediaPlayer
import android.util.Log
import android.view.KeyEvent
import android.os.Handler
import android.os.Looper

/** Both assistants use the media stream, including prompts and stereo video playback. */
internal object AssistantAudio {
    private val handler = Handler(Looper.getMainLooper())
    private var manager: AudioManager? = null
    private var lastSystemVolume = -1
    private val followSystemVolume = object : Runnable {
        override fun run() {
            val audio = manager ?: return
            val system = audio.getStreamVolume(AudioManager.STREAM_SYSTEM)
            if (system != lastSystemVolume) {
                val maximum = audio.getStreamMaxVolume(AudioManager.STREAM_SYSTEM).coerceAtLeast(1)
                val media = (system * audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC) + maximum / 2) / maximum
                audio.setStreamVolume(AudioManager.STREAM_MUSIC, media, 0)
                lastSystemVolume = system
                Log.i("AssistantVolume", "system=$system, media=$media")
            }
            handler.postDelayed(this, 300L)
        }
    }

    fun followSystem(activity: Activity) {
        stopFollowing()
        activity.volumeControlStream = AudioManager.STREAM_SYSTEM
        manager = activity.getSystemService(AudioManager::class.java)
        lastSystemVolume = -1
        followSystemVolume.run()
    }

    fun stopFollowing() {
        handler.removeCallbacks(followSystemVolume)
        manager = null
    }
    val speechAttributes: AudioAttributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_MEDIA)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()
    val videoAttributes: AudioAttributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_MEDIA)
        .setContentType(AudioAttributes.CONTENT_TYPE_MOVIE).build()

    fun createPrompt(context: Context, resourceId: Int): MediaPlayer? =
        MediaPlayer.create(context, resourceId, speechAttributes, AudioManager.AUDIO_SESSION_ID_GENERATE)

    fun handleVolumeKey(activity: Activity, event: KeyEvent): Boolean {
        val direction = when (event.keyCode) {
            KeyEvent.KEYCODE_VOLUME_UP -> AudioManager.ADJUST_RAISE
            KeyEvent.KEYCODE_VOLUME_DOWN -> AudioManager.ADJUST_LOWER
            else -> return false
        }
        if (event.action == KeyEvent.ACTION_DOWN) {
            val audio = activity.getSystemService(AudioManager::class.java)
            audio.adjustStreamVolume(AudioManager.STREAM_SYSTEM, direction, AudioManager.FLAG_SHOW_UI)
            Log.i("AssistantVolume", "key=${event.keyCode}, device=${event.deviceId}, media=${audio.getStreamVolume(AudioManager.STREAM_MUSIC)}/${audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)}")
        }
        return true
    }
}
