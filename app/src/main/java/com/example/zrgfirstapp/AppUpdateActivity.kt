package com.example.zrgfirstapp

import android.app.Activity
import android.app.PendingIntent
import android.content.pm.PackageInstaller
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.GestureDetector
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import kotlin.math.abs

class AppUpdateActivity : AppCompatActivity() {
    private val baseUrl by lazy { getString(R.string.glasses_service_base_url_network).trimEnd('/') }
    private val role = BuildConfig.APP_MODE
    private val panels by lazy { listOf(findViewById<View>(R.id.left_eye_panel), findViewById<View>(R.id.right_eye_panel)) }
    private lateinit var gestures: GestureDetector
    private var info: JSONObject? = null
    private var downloaded: File? = null
    private var busy = false
    private var focus = 0
    private var status = "正在检查更新……"
    private val permission = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (Build.VERSION.SDK_INT < 26 || packageManager.canRequestPackageInstalls()) install()
        else show("未获得安装许可，可返回后再更新。")
    }
    private val installer = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        show("安装界面已关闭。完成更新后重新打开助手；未安装可重试。")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.activity_main)
        findViewById<View>(R.id.main).setBackgroundColor(Color.BLACK)
        gestures = GestureDetector(this, object : GestureDetector.SimpleOnGestureListener() {
            override fun onDown(e: MotionEvent) = true
            override fun onSingleTapConfirmed(e: MotionEvent): Boolean { activate(); return true }
            override fun onDoubleTap(e: MotionEvent): Boolean { leave(); return true }
            override fun onFling(a: MotionEvent?, b: MotionEvent, vx: Float, vy: Float): Boolean {
                if (!busy && a != null && abs(b.x - a.x) > 30) { focus = 1 - focus; render(); return true }
                return false
            }
        })
        val error = intent.getStringExtra("install_error")
        if (error != null) show(error) else check()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        intent.getStringExtra("install_error")?.let(::show)
    }

    private fun render() {
        panels.forEach { panel ->
            panel.findViewById<TextView>(R.id.title_text).text = "${if (role == "teacher") "教师" else "学生"}助手更新"
            panel.findViewById<TextView>(R.id.subtitle_text).text = "当前版本 ${BuildConfig.VERSION_NAME}"
            panel.findViewById<TextView>(R.id.result_text).text = status
            panel.findViewById<TextView>(R.id.status_text).text = if (busy) "请稍候" else "左右滑动选择，单击确认；双击返回"
            panel.findViewById<View>(R.id.progress).visibility = if (busy) View.VISIBLE else View.GONE
            listOf(R.id.primary_button, R.id.secondary_button).forEachIndexed { index, id ->
                panel.findViewById<Button>(id).apply {
                    text = if (index == 1) "返回助手" else if (info != null) "立即更新" else "检查更新"
                    isEnabled = !busy
                    isSelected = focus == index
                    alpha = if (focus == index) 1f else 0.6f
                    setOnClickListener { focus = index; activate() }
                }
            }
        }
    }

    private fun show(message: String) { status = message; busy = false; render() }
    private fun onUi(action: () -> Unit) = runOnUiThread { if (!isFinishing && !isDestroyed) action() }

    private fun check() {
        busy = true; status = "正在检查更新……"; render()
        Thread {
            try {
                val connection = URL("$baseUrl/api/glasses/updates/$role").openConnection() as HttpURLConnection
                val release = try {
                    connection.connectTimeout = 3_000; connection.readTimeout = 5_000
                    JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
                } finally { connection.disconnect() }
                check(release.getString("package_name") == packageName) { "此测试安装包不能使用正式版更新。" }
                val newer = release.getInt("version_code") > BuildConfig.VERSION_CODE
                onUi {
                    if (newer) {
                        info = release
                        show("发现新版 ${release.getString("version_name")}\n${release.optString("notes")}\n点击立即更新；支持的系统会直接完成安装。")
                    } else if (intent.getBooleanExtra("startup", false)) leave()
                    else show("已经是最新版本。")
                }
            } catch (error: Exception) {
                onUi {
                    if (intent.getBooleanExtra("startup", false)) leave()
                    else show("暂时无法检查更新，请确认眼镜已联网。\n${error.message.orEmpty().take(100)}")
                }
            }
        }.start()
    }

    private fun activate() {
        if (busy) return
        if (focus == 1) { leave(); return }
        if (info == null) { check(); return }
        if (downloaded != null) install() else download()
    }

    private fun download() {
        val release = info ?: return
        busy = true; status = "正在下载更新，请不要退出……"; render()
        Thread {
            val folder = File(cacheDir, "app_updates").apply { mkdirs() }
            val file = File(folder, "update.apk")
            try {
                val connection = URL(URL(baseUrl), release.getString("download_url")).openConnection() as HttpURLConnection
                try {
                    connection.connectTimeout = 10_000; connection.readTimeout = 30_000
                    connection.inputStream.use { input -> file.outputStream().use { input.copyTo(it) } }
                } finally { connection.disconnect() }
                val digest = MessageDigest.getInstance("SHA-256")
                file.inputStream().use { input ->
                    val bytes = ByteArray(64 * 1024)
                    var n = input.read(bytes)
                    while (n >= 0) { digest.update(bytes, 0, n); n = input.read(bytes) }
                }
                check(digest.digest().joinToString("") { "%02x".format(it) } == release.getString("sha256")) { "下载文件不完整，请重新下载。" }
                val apk = packageManager.getPackageArchiveInfo(file.path, 0)
                check(apk?.packageName == packageName && apk.versionCode == release.getInt("version_code")) { "更新包与当前程序不匹配。" }
                onUi { downloaded = file; busy = false; install() }
            } catch (error: Exception) {
                file.delete()
                onUi { show("更新下载失败：${error.message.orEmpty().take(120)}") }
            }
        }.start()
    }

    private fun install() {
        val file = downloaded ?: return
        try {
            if (Build.VERSION.SDK_INT >= 26 && !packageManager.canRequestPackageInstalls()) {
                show("请在系统页面允许此助手安装更新，然后返回。")
                permission.launch(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
                return
            }
            if (Build.VERSION.SDK_INT >= 31) {
                installSession(file)
                return
            }
            val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
            installer.launch(Intent(Intent.ACTION_INSTALL_PACKAGE, uri)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION).putExtra(Intent.EXTRA_RETURN_RESULT, true))
        } catch (error: Exception) {
            show("无法打开系统安装界面，请先使用 USB 更新。\n${error.message.orEmpty().take(100)}")
        }
    }

    private fun installSession(file: File) {
        busy = true
        status = "正在安装更新，请稍候；完成后重新打开助手。"
        render()
        Thread {
            val manager = packageManager.packageInstaller
            var sessionId: Int? = null
            try {
                val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
                    setAppPackageName(packageName)
                    setSize(file.length())
                    setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
                }
                val id = manager.createSession(params)
                sessionId = id
                manager.openSession(id).use { session ->
                    session.openWrite("base.apk", 0, file.length()).use { output ->
                        file.inputStream().use { it.copyTo(output) }
                        session.fsync(output)
                    }
                    val result = PendingIntent.getBroadcast(this, id, Intent(this, AppInstallReceiver::class.java),
                        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE)
                    session.commit(result.intentSender)
                }
            } catch (error: Exception) {
                sessionId?.let { runCatching { manager.abandonSession(it) } }
                onUi { show("更新安装失败，可重试或使用 USB 更新。\n${error.message.orEmpty().take(100)}") }
            }
        }.start()
    }

    private fun leave() {
        if (busy && downloaded == null) {
            // A back gesture may leave the check/download; it never installs in the background.
            busy = false
        }
        startActivity(Intent(this, MainActivity::class.java).putExtra("skip_update_check", true))
        finish()
    }

    override fun dispatchTouchEvent(event: MotionEvent): Boolean = gestures.onTouchEvent(event) || super.dispatchTouchEvent(event)
    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        return when (keyCode) {
            KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_DPAD_CENTER -> { activate(); true }
            KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_RIGHT, KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_DOWN -> {
                if (!busy) { focus = 1 - focus; render() }; true
            }
            KeyEvent.KEYCODE_BACK, KeyEvent.KEYCODE_ESCAPE -> { leave(); true }
            else -> super.onKeyUp(keyCode, event)
        }
    }

    companion object {
        fun onLaunch(activity: Activity): Boolean {
            if (BuildConfig.LOCAL_USB || activity.intent.getBooleanExtra("skip_update_check", false)) return false
            activity.startActivity(Intent(activity, AppUpdateActivity::class.java).putExtra("startup", true))
            activity.finish()
            return true
        }
    }
}
