package com.example.zrgfirstapp

import org.junit.Assert.*
import org.junit.Test

class StudentAudioCaptureTest {
    @Test fun speechEndsOnSilenceAndStartsAgainWithoutStoppingCapture() {
        val b = StudentUtteranceBuffer()
        repeat(3) { assertNull(b.append(ByteArray(3200), false)) }
        repeat(3) { assertNull(b.append(ByteArray(3200), true)) }
        repeat(6) { assertNull(b.append(ByteArray(3200), false)) }
        assertNotNull(b.append(ByteArray(3200), false))
        repeat(3) { assertNull(b.append(ByteArray(3200), true)) }
        repeat(6) { assertNull(b.append(ByteArray(3200), false)) }
        assertNotNull(b.append(ByteArray(3200), false))
    }
    @Test fun shortNoiseIsDiscardedAndLongSpeechIsBounded() {
        val b = StudentUtteranceBuffer()
        b.append(ByteArray(3200), true)
        repeat(7) { assertNull(b.append(ByteArray(3200), false)) }
        repeat(79) { assertNull(b.append(ByteArray(3200), true)) }
        assertEquals(256000, b.append(ByteArray(3200), true)!!.size)
    }
}
