package com.example.wifisetting

import android.content.ActivityNotFoundException
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.provider.Settings
import android.view.GestureDetector
import android.view.KeyEvent
import android.view.MotionEvent
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import kotlin.math.abs

class MainActivity : AppCompatActivity() {
    private lateinit var titleText: TextView
    private lateinit var statusText: TextView
    private lateinit var wifiButton: Button
    private lateinit var portalButton: Button
    private lateinit var gestureDetector: GestureDetector
    private var focusIndex = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        titleText = findViewById(R.id.titleText)
        statusText = findViewById(R.id.statusText)
        wifiButton = findViewById(R.id.wifiButton)
        portalButton = findViewById(R.id.portalButton)
        gestureDetector = GestureDetector(this, GlassesGestureListener())

        wifiButton.setOnClickListener { openWifiSettings() }
        portalButton.setOnClickListener { openPortalPage() }
        renderFocus()
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.action == KeyEvent.ACTION_UP) {
            when (event.keyCode) {
                KeyEvent.KEYCODE_DPAD_LEFT,
                KeyEvent.KEYCODE_DPAD_RIGHT,
                KeyEvent.KEYCODE_DPAD_UP,
                KeyEvent.KEYCODE_DPAD_DOWN,
                KeyEvent.KEYCODE_PAGE_UP,
                KeyEvent.KEYCODE_PAGE_DOWN,
                KeyEvent.KEYCODE_FORWARD -> {
                    toggleFocus()
                    return true
                }
                KeyEvent.KEYCODE_DPAD_CENTER,
                KeyEvent.KEYCODE_ENTER,
                KeyEvent.KEYCODE_BUTTON_A -> {
                    activateFocus()
                    return true
                }
                KeyEvent.KEYCODE_BACK -> {
                    finish()
                    return true
                }
            }
        }
        return super.dispatchKeyEvent(event)
    }

    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        gestureDetector.onTouchEvent(event)
        return true
    }

    private fun toggleFocus() {
        focusIndex = 1 - focusIndex
        renderFocus()
    }

    private fun activateFocus() {
        if (focusIndex == 0) {
            openWifiSettings()
        } else {
            openPortalPage()
        }
    }

    private fun renderFocus() {
        setButtonState(wifiButton, focusIndex == 0)
        setButtonState(portalButton, focusIndex == 1)
        statusText.text = if (focusIndex == 0) {
            "单击进入系统 WiFi 设置，双击退出"
        } else {
            "单击打开校园网认证页面，双击退出"
        }
    }

    private fun setButtonState(button: Button, focused: Boolean) {
        button.isSelected = focused
        button.alpha = if (focused) 1.0f else 0.58f
        button.setTextColor(if (focused) Color.rgb(38, 25, 78) else Color.WHITE)
        button.setBackgroundColor(if (focused) Color.rgb(205, 187, 255) else Color.rgb(88, 79, 118))
    }

    private fun openWifiSettings() {
        try {
            startActivity(Intent(Settings.ACTION_WIFI_SETTINGS))
            statusText.text = "已打开 WiFi 设置"
        } catch (_: ActivityNotFoundException) {
            statusText.text = "无法打开 WiFi 设置"
        }
    }

    private fun openPortalPage() {
        try {
            startActivity(Intent(this, PortalActivity::class.java))
            statusText.text = "已打开校园网认证"
        } catch (_: ActivityNotFoundException) {
            statusText.text = "无法打开校园网认证"
        }
    }

    private inner class GlassesGestureListener : GestureDetector.SimpleOnGestureListener() {
        override fun onDown(e: MotionEvent): Boolean = true

        override fun onSingleTapConfirmed(e: MotionEvent): Boolean {
            activateFocus()
            return true
        }

        override fun onDoubleTap(e: MotionEvent): Boolean {
            finish()
            return true
        }

        override fun onFling(
            e1: MotionEvent?,
            e2: MotionEvent,
            velocityX: Float,
            velocityY: Float
        ): Boolean {
            if (e1 != null && abs(e2.x - e1.x) > 40f) {
                toggleFocus()
                return true
            }
            return false
        }
    }
}
