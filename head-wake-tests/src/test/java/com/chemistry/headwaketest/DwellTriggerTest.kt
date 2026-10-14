package com.chemistry.headwaketest

import org.junit.Assert.*
import org.junit.Test

class DwellTriggerTest {
    @Test fun cursorMustEnterSmallIconBeforeUsingExpandedArea() {
        assertFalse(CursorTarget.contains(20f, 480f, false))
        assertTrue(CursorTarget.contains(12f, 480f, false))
        assertTrue(CursorTarget.contains(20f, 480f, true))
        assertFalse(CursorTarget.contains(32f, 480f, true))
        assertFalse(CursorTarget.contains(0f, 0f, false))
    }

    @Test fun gestureAcceptsSmallMovementAndOvershootButRejectsOtherDirections() {
        assertTrue(GestureTarget.contains(9f, 4f, false))
        assertTrue(GestureTarget.contains(40f, 25f, false))
        assertTrue(GestureTarget.contains(7f, 2f, true))
        assertFalse(GestureTarget.contains(7f, 2f, false))
        assertFalse(GestureTarget.contains(-15f, 10f, false))
        assertFalse(GestureTarget.contains(15f, -10f, true))
        assertFalse(GestureTarget.contains(0f, 0f, true))
    }

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
