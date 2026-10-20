package com.example.zrgfirstapp

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.util.Log
import android.widget.Toast

/** Receives the system's install result even when a self-update replaces the running app. */
class AppInstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        Log.i("AppUpdateInstall", "package=${context.packageName} status=$status")
        when (status) {
            PackageInstaller.STATUS_SUCCESS -> Toast.makeText(context, "更新完成，请重新打开助手", Toast.LENGTH_LONG).show()
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirmation = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                if (confirmation != null) context.startActivity(confirmation.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                else showError(context, "系统未提供安装确认页面，请使用 USB 更新。")
            }
            else -> showError(context, "更新未完成，可重试或返回助手。\n" +
                intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE).orEmpty().take(100))
        }
    }

    private fun showError(context: Context, message: String) {
        context.startActivity(Intent(context, AppUpdateActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            .putExtra("install_error", message))
    }
}
