package com.example.zrgfirstapp

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Timestamp format shared by the student and teacher upload clients. */
object UploadMetadata {
    fun nowIso(): String = isoAt(System.currentTimeMillis())
    fun isoAt(timeMillis: Long): String = SimpleDateFormat(
        "yyyy-MM-dd'T'HH:mm:ss.SSSXXX",
        Locale.US,
    ).format(Date(timeMillis))
}
