package com.pcbridge.app

import android.Manifest
import android.app.Activity
import android.app.DownloadManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.View
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Toast
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.biometric.BiometricPrompt
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * PC Bridge (Android wrapper)
 *
 * This app does not reimplement the UI. It's a thin native shell around the
 * exact same web app served by pcbridge/server.py on your PC: a WebView
 * points at "http://<your PC address>/" and everything else (PIN entry,
 * file browsing, upload, download, rename, delete, the multi-PC switcher)
 * is the same HTML/JS/CSS you already tested in a normal mobile browser.
 *
 * The native pieces on top of that:
 *  - remembering the PC's address so you don't have to type a URL every time
 *  - wiring file uploads (<input type=file>) to Android's picker
 *  - wiring file downloads to Android's DownloadManager
 *  - a biometric/device-credential lock screen in front of everything
 *  - a small JS bridge so the web page can trigger an upload-complete
 *    notification and keep the home-screen widget's data up to date
 */
class MainActivity : AppCompatActivity() {

    companion object {
        private const val SPLASH_DURATION_MS = 2000L
    }

    private lateinit var webView: WebView
    private var filePathCallback: ValueCallback<Array<Uri>>? = null

    private lateinit var fileChooserLauncher: ActivityResultLauncher<Intent>
    private lateinit var notificationPermissionLauncher: ActivityResultLauncher<String>

    private val prefs by lazy { getSharedPreferences("pcbridge", Context.MODE_PRIVATE) }

    // ---------- lock screen state ----------
    private var isUnlocked = false
    private var awaitingExternalResult = false
    private lateinit var lockOverlay: LinearLayout

    // ---------- splash screen state ----------
    private lateinit var splashOverlay: LinearLayout
    private var isSplashDone = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        createNotificationChannel()

        webView = findViewById(R.id.webview)
        setupWebView()

        lockOverlay = findViewById(R.id.lock_overlay)
        splashOverlay = findViewById(R.id.splash_overlay)
        findViewById<Button>(R.id.unlock_button).setOnClickListener { authenticateAndProceed() }

        fileChooserLauncher = registerForActivityResult(
            ActivityResultContracts.StartActivityForResult()
        ) { result ->
            awaitingExternalResult = false
            val data = result.data
            val results: Array<Uri>? = when {
                result.resultCode != Activity.RESULT_OK || data == null -> null
                data.clipData != null -> {
                    val clip = data.clipData!!
                    Array(clip.itemCount) { i -> clip.getItemAt(i).uri }
                }
                data.data != null -> arrayOf(data.data!!)
                else -> null
            }
            filePathCallback?.onReceiveValue(results)
            filePathCallback = null
        }

        notificationPermissionLauncher = registerForActivityResult(
            ActivityResultContracts.RequestPermission()
        ) { /* no-op either way -- notification just won't show without it */ }
        maybeRequestNotificationPermission()

        // Splash stays up for a beat on every cold start; everything that
        // used to happen immediately here (lock screen / first-run address
        // prompt) now happens once it's dismissed, in finishSplash().
        Handler(Looper.getMainLooper()).postDelayed({ finishSplash() }, SPLASH_DURATION_MS)
    }

    private fun finishSplash() {
        if (isFinishing || isDestroyed) return
        isSplashDone = true
        splashOverlay.visibility = View.GONE

        val savedAddress = prefs.getString("server_address", null)
        if (savedAddress.isNullOrBlank()) {
            // Nothing to protect yet -- skip straight to setup, no lock screen.
            promptForServerAddress(firstRun = true)
        } else {
            loadServer(savedAddress)
            lockOverlay.visibility = View.VISIBLE
            authenticateAndProceed()
        }
    }

    override fun onResume() {
        super.onResume()
        // On cold start, the splash handler (not onResume) decides what
        // happens next -- skip until it's run so we don't show the lock
        // screen underneath the splash or double-trigger biometrics.
        if (!isSplashDone) return

        // Nothing to protect yet on a brand-new install before a PC address
        // is even set up -- skip the lock so it doesn't stack on top of the
        // "enter PC address" dialog on first launch.
        val hasServer = !prefs.getString("server_address", null).isNullOrBlank()
        if (!hasServer) {
            lockOverlay.visibility = View.GONE
            return
        }
        if (!isUnlocked && !awaitingExternalResult) {
            lockOverlay.visibility = View.VISIBLE
            authenticateAndProceed()
        }
    }

    override fun onStop() {
        super.onStop()
        // Re-lock once the app is genuinely backgrounded -- but not when we're
        // just briefly obscured by our own file picker/share sheet.
        if (!awaitingExternalResult) {
            isUnlocked = false
        }
    }

    // ---------- lock screen ----------

    private fun authenticateAndProceed() {
        val executor = ContextCompat.getMainExecutor(this)
        val prompt = BiometricPrompt(this, executor, object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                awaitingExternalResult = false
                unlock()
            }

            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                awaitingExternalResult = false
                if (errorCode == BiometricPrompt.ERROR_NO_BIOMETRICS ||
                    errorCode == BiometricPrompt.ERROR_NO_DEVICE_CREDENTIAL ||
                    errorCode == BiometricPrompt.ERROR_HW_NOT_PRESENT ||
                    errorCode == BiometricPrompt.ERROR_HW_UNAVAILABLE
                ) {
                    // Nothing set up on this phone to lock with -- don't block usage.
                    unlock()
                }
                // Otherwise (user cancelled, etc.) leave the lock screen up;
                // the "Unlock" button lets them try again.
            }
        })

        @Suppress("DEPRECATION")
        val promptInfo = BiometricPrompt.PromptInfo.Builder()
            .setTitle("Unlock PC Bridge")
            .setSubtitle("Use your fingerprint, face, or device PIN")
            .setDeviceCredentialAllowed(true)
            .build()

        // The device-credential (PIN/pattern) fallback launches a separate
        // system activity, which triggers onStop() on this one. Mark this as
        // an "external result" so onStop doesn't re-lock and onResume doesn't
        // fire a second prompt on top of the system's own confirmation UI.
        awaitingExternalResult = true
        prompt.authenticate(promptInfo)
    }

    private fun unlock() {
        isUnlocked = true
        lockOverlay.visibility = View.GONE
    }

    // ---------- notifications ----------

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                "pcbridge_uploads", "Uploads", NotificationManager.IMPORTANCE_DEFAULT
            )
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(channel)
        }
    }

    private fun maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ActivityCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
            ) {
                notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
    }

    fun showUploadNotification(count: Int) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ActivityCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        val builder = NotificationCompat.Builder(this, "pcbridge_uploads")
            .setSmallIcon(android.R.drawable.stat_sys_upload_done)
            .setContentTitle("PC Bridge")
            .setContentText(if (count == 1) "Uploaded 1 file" else "Uploaded $count files")
            .setAutoCancel(true)
        NotificationManagerCompat.from(this).notify(1001, builder.build())
    }

    // ---------- JS <-> native bridge ----------

    /** Exposed to the web page as window.AndroidBridge. */
    inner class WebAppInterface {
        @JavascriptInterface
        fun notifyUploadComplete(count: Int) {
            runOnUiThread { showUploadNotification(count) }
        }

        @JavascriptInterface
        fun syncActiveDevice(name: String, address: String, pin: String) {
            runOnUiThread { saveWidgetDevice(name, address, pin) }
        }

        // Both used to be a separately-positioned floating wrench button;
        // now triggered from the web UI's "⋯" menu instead (see app.js).
        @JavascriptInterface
        fun changeServerAddress() {
            runOnUiThread { promptForServerAddress(firstRun = false) }
        }

        @JavascriptInterface
        fun reloadPage() {
            runOnUiThread { webView.reload() }
        }
    }

    private fun widgetPrefs() = try {
        val masterKey = MasterKey.Builder(this)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            this,
            "pcbridge_widget",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    } catch (e: Exception) {
        // Fall back to a plain store rather than crashing the app over the widget.
        getSharedPreferences("pcbridge_widget_fallback", Context.MODE_PRIVATE)
    }

    private fun saveWidgetDevice(name: String, address: String, pin: String) {
        widgetPrefs().edit()
            .putString("device_name", name)
            .putString("device_address", address)
            .putString("device_pin", pin)
            .apply()

        val mgr = AppWidgetManager.getInstance(this)
        val ids = mgr.getAppWidgetIds(ComponentName(this, PcBridgeWidgetProvider::class.java))
        if (ids.isNotEmpty()) {
            val intent = Intent(this, PcBridgeWidgetProvider::class.java).apply {
                action = AppWidgetManager.ACTION_APPWIDGET_UPDATE
                putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, ids)
            }
            sendBroadcast(intent)
        }
    }

    // ---------- WebView setup ----------

    private fun setupWebView() {
        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.databaseEnabled = true
        settings.allowFileAccess = true
        settings.useWideViewPort = true
        settings.loadWithOverviewMode = true
        settings.cacheMode = android.webkit.WebSettings.LOAD_DEFAULT

        webView.addJavascriptInterface(WebAppInterface(), "AndroidBridge")

        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                // Keep everything inside the app; only the PC's own address is ever loaded.
                return false
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams
            ): Boolean {
                filePathCallback = callback
                val intent = params.createIntent()
                intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                return try {
                    awaitingExternalResult = true
                    fileChooserLauncher.launch(intent)
                    true
                } catch (e: Exception) {
                    awaitingExternalResult = false
                    filePathCallback = null
                    Toast.makeText(this@MainActivity, "Couldn't open file picker", Toast.LENGTH_SHORT).show()
                    false
                }
            }
        }

        webView.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            try {
                val fileName = URLUtil.guessFileName(url, contentDisposition, mimeType)
                val request = DownloadManager.Request(Uri.parse(url))
                request.setMimeType(mimeType)
                request.addRequestHeader("User-Agent", userAgent)
                request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, fileName)
                request.setTitle(fileName)
                request.setDescription("Downloading from PC Bridge")

                val dm = getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
                dm.enqueue(request)
                Toast.makeText(this, "Downloading $fileName", Toast.LENGTH_SHORT).show()
            } catch (e: Exception) {
                Toast.makeText(this, "Download failed: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun loadServer(address: String) {
        val normalized = normalizeAddress(address)
        prefs.edit().putString("server_address", normalized).apply()
        val url = "http://$normalized/"
        webView.loadUrl(url)
    }

    private fun normalizeAddress(input: String): String {
        var addr = input.trim()
        addr = addr.removePrefix("http://").removePrefix("https://")
        addr = addr.trimEnd('/')
        return addr
    }

    private fun promptForServerAddress(firstRun: Boolean) {
        val input = EditText(this)
        input.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        input.hint = "192.168.1.42:8000"
        prefs.getString("server_address", null)?.let { input.setText(it) }

        val dialog = AlertDialog.Builder(this)
            .setTitle("PC address")
            .setMessage(
                "Enter your PC's address as shown in the server console " +
                    "(LAN IP or Tailscale IP), including the port."
            )
            .setView(input)
            .setCancelable(!firstRun)
            .setPositiveButton("Connect") { _, _ ->
                val value = input.text.toString().trim()
                if (value.isNotEmpty()) {
                    loadServer(value)
                } else if (firstRun) {
                    promptForServerAddress(true)
                }
            }

        if (!firstRun) {
            dialog.setNegativeButton("Cancel", null)
        }
        dialog.show()
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }
}
