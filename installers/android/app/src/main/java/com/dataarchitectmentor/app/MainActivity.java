package com.dataarchitectmentor.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.NotificationManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.app.AlarmManager;
import android.graphics.Color;
import android.graphics.Insets;
import android.view.WindowInsets;
import android.widget.FrameLayout;
import android.window.OnBackInvokedDispatcher;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.os.Handler;
import android.os.Looper;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/** The whole app: the mentor site in a full-screen view, plus a small bridge so the site can set the alarms. */
public class MainActivity extends Activity {
    /** Main address (Render) and a backup (the ngrok link). The app switches to the other one by itself if one fails. */
    static final String PRIMARY = BuildConfig.SITE_URL;
    static final String BACKUP = BuildConfig.BACKUP_URL;
    static volatile String SITE = PRIMARY;
    private WebView web;

    static boolean isOurs(Uri u) {
        String h = u.getHost();
        return h != null && (h.equalsIgnoreCase(Uri.parse(PRIMARY).getHost())
            || (!BACKUP.isEmpty() && h.equalsIgnoreCase(Uri.parse(BACKUP).getHost())));
    }

    /** The same page on whichever address is in use now. */
    static String onCurrentSite(String url) {
        if (url == null) return null;
        for (String base : new String[]{PRIMARY, BACKUP}) {
            if (!base.isEmpty() && url.startsWith(base)) return SITE + url.substring(base.length());
        }
        return url;
    }

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#16202B"));
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#16202B"));   // shows behind the status and navigation bars
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        if (Build.VERSION.SDK_INT >= 30) {
            // newer Android draws the app edge to edge: keep the page clear of the bars and the keyboard
            root.setOnApplyWindowInsetsListener((v, insets) -> {
                Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                Insets ime = insets.getInsets(WindowInsets.Type.ime());
                v.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, ime.bottom));
                return WindowInsets.CONSUMED;
            });
        }
        if (Build.VERSION.SDK_INT >= 33) {
            // back gesture: go back through the app's pages, close the app on the first page
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT, () -> {
                if (web.canGoBack()) web.goBack();
                else finish();
            });
        }

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setTextZoom(100);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        // identify as the app, not as a browser: the site knows it's the app, and ngrok's free plan skips
        // its "You are about to visit" notice for non-browser clients
        s.setUserAgentString(Alarms.USER_AGENT);

        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true);

        web.addJavascriptInterface(new Bridge(), "MentorApp");
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                return openOutside(req.getUrl());
            }

            @SuppressWarnings("deprecation")
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return openOutside(Uri.parse(url));
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                CookieManager.getInstance().flush();   // keep the learner signed in
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) siteFailed(req.getUrl().toString());
            }

            /** The tunnel's own error page (mentor's PC asleep / reconnecting) must never be shown: use ours. */
            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest req, WebResourceResponse res) {
                if (!req.isForMainFrame()) return;
                boolean tunnel = res.getResponseHeaders() != null
                    && (res.getResponseHeaders().containsKey("ngrok-error-code")
                        || res.getResponseHeaders().containsKey("Ngrok-Error-Code"));
                int code = res.getStatusCode();
                if (tunnel || code == 404 || code >= 500) siteFailed(req.getUrl().toString());
            }
        });

        if (state != null) web.restoreState(state);
        else web.loadUrl(startUrl(getIntent()));

        askForNotificationPermission();
        Alarms.scheduleNext(this);
    }

    /** Pages of the mentor site stay in the app; everything else (articles, Telegram...) opens outside. */
    private boolean openOutside(Uri uri) {
        if (isOurs(uri) && !uri.getPath().startsWith("/download/")) return false;
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (Exception ignored) { }
        return true;
    }

    private String startUrl(Intent intent) {
        String path = intent == null ? null : intent.getStringExtra(Alarms.EXTRA_PATH);
        return SITE + (path != null ? "/?source=android-app" + path.replaceFirst("^/", "").replaceFirst("^\\?[^#]*", "")
                                    : "/?source=android-app#home");
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (intent.getStringExtra(Alarms.EXTRA_PATH) != null) web.loadUrl(startUrl(intent));
    }

    // ---- "right back" screen: shown instead of any error page, checks quietly until the mentor answers again ----
    private final Handler handler = new Handler(Looper.getMainLooper());
    private String resumeUrl = null;
    private boolean offline = false;

    private boolean triedOther = false;

    /** A page failed: first quietly try the other address; only if that fails too, show the "right back" screen. */
    private void siteFailed(String url) {
        if (!offline && !triedOther && !BACKUP.isEmpty() && url != null && isOurs(Uri.parse(url))) {
            triedOther = true;
            SITE = SITE.equals(PRIMARY) ? BACKUP : PRIMARY;
            web.loadUrl(onCurrentSite(url));
            return;
        }
        showOffline(url);
    }

    private void showOffline(String failedUrl) {
        if (failedUrl != null && isOurs(Uri.parse(failedUrl))) resumeUrl = failedUrl;
        if (offline) return;
        offline = true;
        String html = "<html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head>"
            + "<body style='margin:0;background:#16202B;color:#E6EBF0;font-family:sans-serif;display:flex;"
            + "align-items:center;justify-content:center;height:100vh;text-align:center'><div style='padding:28px;max-width:380px'>"
            + "<svg width='56' height='56' viewBox='0 0 32 32'><rect x='5' y='7' width='13' height='18' rx='2' fill='none' "
            + "stroke='#7FB2DD' stroke-width='2'/><path d='M18 16h7M25 11l-4 5 4 5' fill='none' stroke='#F6D365' "
            + "stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/></svg>"
            + "<h2 style='font-weight:600;margin:14px 0 8px'>Mentor will be right back</h2>"
            + "<p style='color:#A3B0BD;line-height:1.55;margin:0'>We can't reach the mentor just now. Your progress is safe "
            + "and your reminders still ring. This screen opens Mentor by itself as soon as it's back.</p>"
            + "<button onclick='MentorApp.retryNow()' style='margin-top:20px;padding:12px 22px;border:0;border-radius:8px;"
            + "background:#7FB2DD;color:#0C1620;font-size:16px;font-weight:600'>Try again now</button></div></body></html>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
        handler.postDelayed(this::probe, 8000);
    }

    /** Asks the mentor (off the main thread) whether it's back; if so, return to where the learner was. */
    private void probe() {
        if (!offline) return;
        new Thread(() -> {
            String up = Alarms.firstWorkingSite();   // main address first, then the backup
            handler.post(() -> {
                if (!offline) return;
                if (up != null) { SITE = up; reconnect(); }
                else handler.postDelayed(this::probe, 15000);
            });
        }).start();
    }

    private void reconnect() {
        offline = false;
        triedOther = false;
        handler.removeCallbacksAndMessages(null);
        web.loadUrl(resumeUrl != null ? onCurrentSite(resumeUrl) : SITE + "/?source=android-app#home");
    }

    private void askForNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        Alarms.scheduleNext(this);  // e.g. after the learner allowed exact alarms in Settings
        if (web != null) web.evaluateJavascript("window.onAppResume && window.onAppResume()", null);
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    /** Called from the site's JavaScript as window.MentorApp.* */
    class Bridge {
        @JavascriptInterface
        public void setSchedule(String json) {
            Alarms.saveSchedule(MainActivity.this, json);
            Alarms.scheduleNext(MainActivity.this);
        }

        @JavascriptInterface
        public void retryNow() {
            handler.post(MainActivity.this::probe);
        }

        @JavascriptInterface
        public void testAlarm() {
            Alarms.scheduleTest(MainActivity.this);
        }

        @JavascriptInterface
        public boolean notificationsAllowed() {
            NotificationManager nm = getSystemService(NotificationManager.class);
            return nm != null && nm.areNotificationsEnabled();
        }

        /** True when reminders can ring on the exact minute ("Alarms & reminders" allowed). */
        @JavascriptInterface
        public boolean exactAlarmsAllowed() {
            return Build.VERSION.SDK_INT < 31 || getSystemService(AlarmManager.class).canScheduleExactAlarms();
        }

        @JavascriptInterface
        public void openExactAlarmSettings() {
            if (Build.VERSION.SDK_INT >= 31) {
                startActivity(new Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                    Uri.parse("package:" + getPackageName())));
            }
        }

        @JavascriptInterface
        public void openNotificationSettings() {
            Intent i = new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName());
            startActivity(i);
        }
    }
}
