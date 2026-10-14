package com.example.zrgfirstapp

import org.junit.Assert.*
import org.junit.Test

class HeadWakeGestureTest {
    private fun armed() = HeadWakeGesture().apply {
        update(0f, 0f, 0L)
        update(0f, 0f, 400L)
    }

    @Test fun requiresFullDwellAndReturnToNeutralBeforeAnotherWake() {
        val gesture = armed()
        assertFalse(gesture.update(18f, 14f, 500L))
        assertFalse(gesture.update(18f, 14f, 2000L))
        assertEquals(0.5f, gesture.progress, 0.001f)
        assertFalse(gesture.update(18f, 14f, 3499L))
        assertTrue(gesture.update(18f, 14f, 3500L))
        assertFalse(gesture.update(18f, 14f, 6000L))
        gesture.update(0f, 0f, 6100L)
        gesture.update(0f, 0f, 6500L)
        gesture.update(18f, 14f, 6600L)
        assertTrue(gesture.update(18f, 14f, 9600L))
    }

    @Test fun leavingTargetCancelsAccumulatedTime() {
        val gesture = armed()
        gesture.update(18f, 14f, 500L)
        gesture.update(18f, 14f, 1500L)
        assertFalse(gesture.update(0f, 14f, 1600L))
        assertEquals(0f, gesture.progress, 0f)
        assertFalse(gesture.update(18f, 14f, 1700L))
        assertFalse(gesture.update(18f, 14f, 4699L))
        assertTrue(gesture.update(18f, 14f, 4700L))
    }

    @Test fun wrongDirectionAndUncalibratedPoseDoNotWake() {
        val gesture = HeadWakeGesture()
        gesture.update(18f, 14f, 0L)
        assertFalse(gesture.update(18f, 14f, 5000L))
        val ready = armed()
        for ((yaw, pitch) in listOf(-18f to 14f, 18f to -14f, 0f to 14f, 18f to 0f)) {
            assertFalse(ready.update(yaw, pitch, 6000L))
            assertFalse(ready.update(yaw, pitch, 9000L))
        }
    }

    @Test fun resetDropsDwellAndHysteresisOnlyAppliesAfterEntry() {
        val gesture = armed()
        gesture.update(11f, 7f, 500L)
        assertEquals(0f, gesture.progress, 0f)
        gesture.update(18f, 14f, 600L)
        assertFalse(gesture.update(11f, 7f, 1600L))
        assertTrue(gesture.progress > 0f)
        gesture.reset()
        assertFalse(gesture.update(18f, 14f, 5000L))
        assertEquals(0f, gesture.progress, 0f)
    }
}
