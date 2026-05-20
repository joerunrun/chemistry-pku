package com.example.zrgfirstapp

import android.app.Application
import android.util.Log

class ChemApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        initMercurySdkIfAvailable()
    }

    private fun initMercurySdkIfAvailable() {
        try {
            val sdkClass = Class.forName("com.ffalcon.mercury.android.sdk.MercurySDK")
            val initMethod = sdkClass.getMethod("init", Application::class.java)
            try {
                initMethod.invoke(null, this)
            } catch (_: NullPointerException) {
                val instance = sdkClass.getField("INSTANCE").get(null)
                initMethod.invoke(instance, this)
            }
            Log.i(TAG, "MercurySDK initialized.")
        } catch (exc: ClassNotFoundException) {
            Log.i(TAG, "MercurySDK is not bundled on this build; RayNeo metadata remains enabled.")
        } catch (exc: Throwable) {
            Log.w(TAG, "MercurySDK initialization skipped: ${exc.message}")
        }
    }

    private companion object {
        const val TAG = "ChemApplication"
    }
}
