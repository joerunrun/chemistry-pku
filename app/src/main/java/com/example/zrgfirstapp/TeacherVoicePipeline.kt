package com.example.zrgfirstapp

import kotlin.math.sqrt

internal data class TeacherVoiceWindow(
    val epoch: Int,
    val sequence: Long,
    val context: String,
    val pcm: ByteArray,
)

/** Fixed-capacity PCM ring used by the uninterrupted teacher microphone. */
internal class PcmRingBuffer(private val capacity: Int) {
    private val data = ByteArray(capacity)
    private var start = 0
    private var length = 0

    @Synchronized
    fun append(bytes: ByteArray, offset: Int = 0, count: Int = bytes.size) {
        require(offset >= 0 && count >= 0 && offset + count <= bytes.size)
        for (index in offset until offset + count) {
            if (length < capacity) {
                data[(start + length) % capacity] = bytes[index]
                length += 1
            } else {
                data[start] = bytes[index]
                start = (start + 1) % capacity
            }
        }
    }

    @Synchronized
    fun snapshot(byteCount: Int): ByteArray? {
        if (byteCount <= 0 || length < byteCount) return null
        val result = ByteArray(byteCount)
        val first = (start + length - byteCount) % capacity
        for (index in result.indices) {
            result[index] = data[(first + index) % capacity]
        }
        return result
    }
}

internal object PcmAudioGate {
    fun hasMeaningfulAudio(
        pcm: ByteArray,
        minimumRms: Double = 180.0,
        minimumPeak: Int = 1_200,
    ): Boolean {
        if (pcm.size < 2) return false
        var sumSquares = 0.0
        var peak = 0
        var samples = 0
        var index = 0
        while (index + 1 < pcm.size) {
            val sample = ((pcm[index].toInt() and 0xff) or
                (pcm[index + 1].toInt() shl 8)).toShort().toInt()
            val absolute = kotlin.math.abs(sample)
            if (absolute > peak) peak = absolute
            sumSquares += sample.toDouble() * sample.toDouble()
            samples += 1
            index += 2
        }
        val rms = sqrt(sumSquares / samples.coerceAtLeast(1))
        return rms >= minimumRms || peak >= minimumPeak
    }
}

/** Limits cloud work to two requests and retains only the newest overflow window. */
internal class LatestVoiceWindowLimiter(private val maximumInFlight: Int = 2) {
    private val active = mutableSetOf<Long>()
    private var pending: TeacherVoiceWindow? = null

    @Synchronized
    fun offer(window: TeacherVoiceWindow): TeacherVoiceWindow? {
        if (active.size >= maximumInFlight) {
            pending = window
            return null
        }
        active += window.sequence
        return window
    }

    @Synchronized
    fun complete(sequence: Long, currentEpoch: Int): TeacherVoiceWindow? {
        active -= sequence
        val next = pending
        if (next == null || next.epoch != currentEpoch || active.size >= maximumInFlight) {
            if (next != null && next.epoch != currentEpoch) pending = null
            return null
        }
        pending = null
        active += next.sequence
        return next
    }

    @Synchronized
    fun discardPending() {
        pending = null
    }
}

internal class VoiceEpoch {
    @Volatile
    private var value = 0

    @Synchronized
    fun advance(): Int {
        value += 1
        return value
    }

    fun current(): Int = value

    fun accepts(epoch: Int): Boolean = epoch == value
}
