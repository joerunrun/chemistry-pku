package com.example.zrgfirstapp

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TeacherVoicePipelineTest {
    @Test
    fun ringSnapshotsOverlapWithoutStoppingTheWriter() {
        val ring = PcmRingBuffer(20)
        ring.append(ByteArray(10) { it.toByte() })
        val first = ring.snapshot(10)
        ring.append(byteArrayOf(10, 11, 12, 13))
        val second = ring.snapshot(10)

        assertArrayEquals(ByteArray(10) { it.toByte() }, first)
        assertArrayEquals(byteArrayOf(4, 5, 6, 7, 8, 9, 10, 11, 12, 13), second)
    }

    @Test
    fun audioGateDropsSilenceAndKeepsSpeech() {
        assertFalse(PcmAudioGate.hasMeaningfulAudio(ByteArray(3_200)))
        val speech = ByteArray(3_200)
        for (index in speech.indices step 2) {
            speech[index] = 0xd0.toByte()
            speech[index + 1] = 0x07
        }
        assertTrue(PcmAudioGate.hasMeaningfulAudio(speech))
    }

    @Test
    fun limiterKeepsOnlyNewestOverflowWindow() {
        val limiter = LatestVoiceWindowLimiter(2)
        fun window(sequence: Long) = TeacherVoiceWindow(1, sequence, "teacher_sleep", byteArrayOf())

        assertEquals(1L, limiter.offer(window(1))?.sequence)
        assertEquals(2L, limiter.offer(window(2))?.sequence)
        assertNull(limiter.offer(window(3)))
        assertNull(limiter.offer(window(4)))
        assertEquals(4L, limiter.complete(1, currentEpoch = 1)?.sequence)
    }

    @Test
    fun staleEpochIsRejectedAfterWakeTransition() {
        val epoch = VoiceEpoch()
        val sleepingRequest = epoch.current()
        assertTrue(epoch.accepts(sleepingRequest))
        epoch.advance()
        assertFalse(epoch.accepts(sleepingRequest))
    }
}
