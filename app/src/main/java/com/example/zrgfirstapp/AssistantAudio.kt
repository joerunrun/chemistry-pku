package com.example.zrgfirstapp

import android.app.Activity
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.MediaPlayer
import android.util.Log
import android.view.KeyEvent

/** Both assistants use the media stream, including prompts and stereo video playback. */
internal object AssistantAudio {
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
            audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, direction, AudioManager.FLAG_SHOW_UI)
            Log.i("AssistantVolume", "key=${event.keyCode}, device=${event.deviceId}, media=${audio.getStreamVolume(AudioManager.STREAM_MUSIC)}/${audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)}")
        }
        return true
    }
}
