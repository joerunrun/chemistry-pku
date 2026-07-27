package com.example.zrgfirstapp

import android.content.Context
import android.provider.Settings
import java.security.MessageDigest
import java.util.Locale
import java.util.UUID

/** Stable, app-local identifier used to separate uploads from multiple glasses. */
object DeviceIdentity {
    fun teacherId(context: Context): String {
        val androidId = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ANDROID_ID,
        ).orEmpty().trim().lowercase(Locale.US)
        if (androidId.isNotEmpty() && androidId != "9774d56d682e549c") {
            val digest = MessageDigest.getInstance("SHA-256")
                .digest(androidId.toByteArray(Charsets.UTF_8))
            val hex = buildString(digest.size * 2) {
                digest.forEach { append(String.format(Locale.US, "%02x", it.toInt() and 0xff)) }
            }
            return "teacher_android_${hex.take(16)}"
        }

        // Very old/vendor builds may not expose ANDROID_ID. Keep a generated
        // value in app storage so different glasses still remain distinct.
        val preferences = context.getSharedPreferences("device_identity", Context.MODE_PRIVATE)
        val generated = preferences.getString("teacher_id", null)
            ?: "teacher_generated_${UUID.randomUUID().toString().replace("-", "")}"
                .also { preferences.edit().putString("teacher_id", it).apply() }
        return generated
    }
}
