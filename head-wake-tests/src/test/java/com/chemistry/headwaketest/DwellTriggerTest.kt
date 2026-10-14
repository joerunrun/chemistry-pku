package com.chemistry.headwaketest

import org.junit.Assert.*
import org.junit.Test

class DwellTriggerTest {
    private fun ready() = DwellTrigger().apply {
        update(true, false, 0)
        update(true, false, 400)
    }

    @Test fun requiresThreeSecondsAndCannotRepeatUntilNeutral() {
        val t = ready()
        assertFalse(t.update(false, true, 500))
        assertFalse(t.update(false, true, 3499))
        assertTrue(t.update(false, true, 3500))
        assertFalse(t.update(false, true, 9000))
        t.update(true, false, 10000)
        t.update(true, false, 10400)
        t.update(false, true, 10500)
        assertTrue(t.update(false, true, 13500))
    }

    @Test fun leavingOrSensorResetDiscardsElapsedDwell() {
        val t = ready()
        t.update(false, true, 500)
        t.update(false, true, 2000)
        t.update(false, false, 2200)
        assertEquals(0f, t.progress, 0f)
        assertFalse(t.update(false, true, 2300))
        assertFalse(t.update(false, true, 5000))
        t.reset()
        assertFalse(t.update(false, true, 6000))
        assertFalse(t.armed)
    }
}
