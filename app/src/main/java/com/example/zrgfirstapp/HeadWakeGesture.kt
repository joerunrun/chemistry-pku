package com.example.zrgfirstapp

import kotlin.math.abs

/** Angles are relative to the forward pose: positive means right / up. */
internal class HeadWakeGesture {
    private val dwellMs = 3000L
    var progress = 0f
        private set
    private var armed = false
    private var neutralSince: Long? = null
    private var dwellSince: Long? = null

    fun reset() {
        armed = false
        neutralSince = null
        dwellSince = null
        progress = 0f
    }

    fun update(yaw: Float, pitch: Float, nowMs: Long): Boolean {
        if (!yaw.isFinite() || !pitch.isFinite()) {
            reset()
            return false
        }
        if (!armed) {
            if (abs(yaw) <= 6f && abs(pitch) <= 6f) {
                val start = neutralSince ?: nowMs.also { neutralSince = it }
                if (nowMs - start >= 400L) armed = true
            } else {
                neutralSince = null
            }
            return false
        }
        // Slightly wider exit bounds tolerate natural tremor without counting a glance.
        val inside = if (dwellSince == null) {
            yaw in 12f..28f && pitch in 8f..22f
        } else {
            yaw in 10f..30f && pitch in 6f..24f
        }
        if (!inside) {
            dwellSince = null
            progress = 0f
            return false
        }
        val start = dwellSince ?: nowMs.also { dwellSince = it }
        progress = ((nowMs - start).toFloat() / dwellMs).coerceIn(0.001f, 1f)
        if (nowMs - start < dwellMs) return false
        reset()
        return true
    }
}
