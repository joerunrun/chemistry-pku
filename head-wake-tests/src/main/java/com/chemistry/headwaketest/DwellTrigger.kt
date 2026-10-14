package com.chemistry.headwaketest

internal object GestureTarget {
    fun contains(yaw: Float, pitch: Float, holding: Boolean): Boolean =
        if (holding) yaw in 6f..60f && pitch in 1f..50f
        else yaw in 8f..55f && pitch in 3f..45f
}

/** Requires a return to neutral between activations. Times use a monotonic clock. */
internal class DwellTrigger {
    var armed = false
        private set
    var progress = 0f
        private set
    private var neutralSince: Long? = null
    private var hitSince: Long? = null

    fun reset() {
        armed = false
        neutralSince = null
        hitSince = null
        progress = 0f
    }

    fun update(neutral: Boolean, hit: Boolean, now: Long): Boolean {
        if (!armed) {
            if (neutral) {
                val start = neutralSince ?: now.also { neutralSince = it }
                armed = now - start >= 400L
            } else neutralSince = null
            return false
        }
        if (!hit) {
            hitSince = null
            progress = 0f
            return false
        }
        val start = hitSince ?: now.also { hitSince = it }
        progress = ((now - start) / 3000f).coerceIn(0.001f, 1f)
        if (now - start < 3000L) return false
        reset()
        return true
    }
}
