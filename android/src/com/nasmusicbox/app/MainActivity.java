package com.nasmusicbox.app;

import android.annotation.SuppressLint;
import android.annotation.TargetApi;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.media.AudioManager;
import android.net.ConnectivityManager;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewConfiguration;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.SslErrorHandler;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;

/** 主界面：全屏 WebView 加载 NAS 音乐盒的网页界面，外加原生错误页、悬浮菜单、音量键转发。 */
public class MainActivity extends Activity {
    private static final int REQ_FILE = 11;
    private static final String BRIDGE_NAME = "NasBoxApp";

    private final Handler ui = new Handler(Looper.getMainLooper());
    private Store store;
    private FrameLayout root, webHolder;
    private WebView web;
    private ProgressBar progress;
    private View errorView;
    private TextView errorDetail, errorUrl, fab;

    /** 当前 WebView 里加载的服务器地址（规整后的 base），null 表示还没加载 */
    private String loadedBase;
    private String failedUrl;
    private boolean loadFailed;
    private boolean pageReady;
    private boolean clearHistoryOnFinish;
    private boolean settingsLaunched;
    private boolean webDead;
    private long lastVolumeAt;
    private ValueCallback<Uri[]> fileCallback;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        store = new Store(this);
        setContentView(R.layout.activity_main);
        root = findViewById(R.id.root);
        webHolder = findViewById(R.id.web_holder);
        progress = findViewById(R.id.progress);
        errorView = findViewById(R.id.error);
        errorDetail = findViewById(R.id.error_detail);
        errorUrl = findViewById(R.id.error_url);
        fab = findViewById(R.id.fab);

        findViewById(R.id.btn_retry).setOnClickListener(v -> retry());
        findViewById(R.id.btn_change).setOnClickListener(v -> openSettings());
        Bars.apply(this, getColor(R.color.bg));

        createWebView();
        setupFab();

        if (saved != null) {
            String base = saved.getString("base");
            Bundle wb = saved.getBundle("web");
            if (base != null && wb != null && web.restoreState(wb) != null) {
                loadedBase = base;
            }
        }
    }

    // ---------------------------------------------------------------- WebView

    @SuppressLint("SetJavaScriptEnabled")
    private void createWebView() {
        web = new WebView(this);
        web.setBackgroundColor(getColor(R.color.bg));
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        webHolder.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setSupportMultipleWindows(false);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setGeolocationEnabled(false);
        s.setUserAgentString(s.getUserAgentString() + " NasMusicBox/" + versionName());
        CookieManager.getInstance().setAcceptCookie(true);

        web.addJavascriptInterface(new Bridge(), BRIDGE_NAME);
        web.setWebViewClient(new Client());
        web.setWebChromeClient(new Chrome());
        web.setDownloadListener((url, ua, cd, mime, len) -> openExternal(Uri.parse(url)));
    }

    private void loadBase(String base) {
        loadedBase = base;
        clearHistoryOnFinish = true;
        pageReady = false;
        hideError();
        web.loadUrl(base + "/");
    }

    private void reloadPage() {
        if (web == null || loadedBase == null) return;
        if (errorView.getVisibility() == View.VISIBLE) {
            retry();
        } else {
            web.reload();
        }
    }

    private void retry() {
        if (loadedBase == null) return;
        String target = failedUrl != null ? failedUrl : loadedBase + "/";
        errorDetail.setText("正在重试…");
        web.loadUrl(target);
    }

    private void showError(String detail, String url) {
        loadFailed = true;
        pageReady = false;
        failedUrl = url;
        if (!hasNetwork()) detail = "手机当前没有联网。连上 Wi-Fi 后再试。";
        errorDetail.setText(detail);
        errorUrl.setText(url == null ? String.valueOf(loadedBase) : url);
        web.setVisibility(View.INVISIBLE);
        errorView.setVisibility(View.VISIBLE);
        progress.setVisibility(View.GONE);
        Bars.apply(this, getColor(R.color.bg));
    }

    private void hideError() {
        errorView.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
    }

    private boolean hasNetwork() {
        try {
            ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
            return cm == null || cm.getActiveNetwork() != null;
        } catch (Exception e) {
            return true;
        }
    }

    private static String describe(int code, CharSequence desc) {
        switch (code) {
            case WebViewClient.ERROR_HOST_LOOKUP:
                return "找不到这台主机。检查地址是否写对，手机是否和 NAS 在同一个网络。";
            case WebViewClient.ERROR_CONNECT:
                return "连接失败：NAS 上的服务可能没启动，或端口不对；也可能手机和 NAS 不在同一个网络。";
            case WebViewClient.ERROR_TIMEOUT:
                return "连接超时：手机和 NAS 可能不在同一个网络，或端口被防火墙拦住了。";
            case WebViewClient.ERROR_FAILED_SSL_HANDSHAKE:
                return "HTTPS 握手失败。局域网地址一般用 http:// 开头。";
            case WebViewClient.ERROR_IO:
                return "网络中断了，稍后重试。";
            default:
                return "页面打不开" + (desc == null || desc.length() == 0 ? "。" : "（" + desc + "）。");
        }
    }

    private final class Client extends WebViewClient {
        @Override
        public void onPageStarted(WebView view, String url, Bitmap favicon) {
            // WebView 内置的报错页自己也会触发一次加载，别让它把失败标记冲掉
            if (url != null && url.startsWith("chrome-error:")) return;
            loadFailed = false;
            pageReady = false;
            progress.setProgress(5);
            progress.setVisibility(View.VISIBLE);
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            progress.setVisibility(View.GONE);
            if (clearHistoryOnFinish) {
                clearHistoryOnFinish = false;
                view.clearHistory();
            }
            if (loadFailed) return;
            failedUrl = null;
            pageReady = true;
            hideError();
            syncBarColor();
            ui.postDelayed(MainActivity.this::syncBarColor, 900);
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
            if (!req.isForMainFrame()) return;
            showError(describe(err.getErrorCode(), err.getDescription()), req.getUrl().toString());
        }

        @Override
        public void onReceivedHttpError(WebView view, WebResourceRequest req, WebResourceResponse resp) {
            if (!req.isForMainFrame()) return;
            int code = resp.getStatusCode();
            String d = code >= 500
                    ? "服务器出错了（HTTP " + code + "），看一下 NAS 上的服务日志。"
                    : "服务器返回了错误（HTTP " + code + "）。这个地址可能不是 NAS 音乐盒服务。";
            showError(d, req.getUrl().toString());
        }

        @Override
        public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
            handler.cancel();
            if (!pageReady) {
                showError("HTTPS 证书无效（自签名或已过期），已拦截。局域网地址一般用 http:// 开头。", error.getUrl());
            }
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
            Uri u = req.getUrl();
            String scheme = u.getScheme() == null ? "" : u.getScheme().toLowerCase();
            if (scheme.equals("http") || scheme.equals("https")) {
                if (loadedBase != null && ServerUrl.sameOrigin(u.toString(), loadedBase)) return false;
                if (!req.isForMainFrame()) return false;
                openExternal(u);
                return true;
            }
            if (scheme.equals("about") || scheme.equals("data") || scheme.equals("blob")
                    || scheme.equals("javascript")) {
                return false;
            }
            openExternal(u);
            return true;
        }

        @TargetApi(26)
        @Override
        public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
            // 渲染进程被系统回收或崩溃：丢掉这个 WebView，重建界面，避免整个 App 被杀
            webDead = true;
            webHolder.removeView(view);
            view.destroy();
            web = null;
            loadedBase = null;
            ui.post(MainActivity.this::recreate);
            return true;
        }
    }

    private final class Chrome extends WebChromeClient {
        @Override
        public void onProgressChanged(WebView view, int p) {
            progress.setProgress(p);
            if (p >= 100) progress.setVisibility(View.GONE);
        }

        @Override
        public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> cb, FileChooserParams params) {
            if (fileCallback != null) fileCallback.onReceiveValue(null);
            fileCallback = cb;
            try {
                startActivityForResult(params.createIntent(), REQ_FILE);
                return true;
            } catch (Exception e) {
                fileCallback = null;
                toast("这台手机上没有可用的文件选择器");
                return false;
            }
        }
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        super.onActivityResult(req, res, data);
        if (req == REQ_FILE && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(res, data));
            fileCallback = null;
        }
    }

    private void openExternal(Uri u) {
        try {
            Intent i = new Intent(Intent.ACTION_VIEW, u);
            i.addCategory(Intent.CATEGORY_BROWSABLE);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch (ActivityNotFoundException e) {
            toast("没有能打开这个链接的应用");
        } catch (Exception e) {
            toast("打不开这个链接");
        }
    }

    /** 读取网页的 theme-color（没有就取 body 背景色），让状态栏 / 导航栏和页面同色。 */
    private void syncBarColor() {
        if (web == null || !pageReady || isFinishing()) return;
        web.evaluateJavascript("(function(){try{var m=document.querySelector('meta[name=\"theme-color\"]');"
                + "if(m&&m.content)return m.content;"
                + "return getComputedStyle(document.body).backgroundColor}catch(e){return ''}})()", v -> {
            if (v == null || !pageReady || isFinishing()) return;
            Integer c = Bars.parseCss(v.replace("\"", ""));
            if (c != null) Bars.apply(this, c);
        });
    }

    // ---------------------------------------------------------------- JS 桥

    /** 注入到网页里的 window.NasBoxApp。方法都很轻，不暴露任何本机数据。 */
    private final class Bridge {
        @JavascriptInterface
        public String getVersion() {
            return versionName();
        }

        @JavascriptInterface
        public void openSettings() {
            ui.post(MainActivity.this::openSettings);
        }

        @JavascriptInterface
        public void reload() {
            ui.post(MainActivity.this::reloadPage);
        }

        @JavascriptInterface
        public void showMenu() {
            ui.post(MainActivity.this::showMenu);
        }

        /** 网页切换深浅色后可以主动通知 App 改状态栏颜色，例如 NasBoxApp.setBarColor('#12131a') */
        @JavascriptInterface
        public void setBarColor(String css) {
            final Integer c = Bars.parseCss(css);
            if (c != null) ui.post(() -> {
                if (pageReady && !isFinishing()) Bars.apply(MainActivity.this, c);
            });
        }
    }

    private String versionName() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "";
        }
    }

    // ---------------------------------------------------------------- 音量键

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        int kc = e.getKeyCode();
        if ((kc == KeyEvent.KEYCODE_VOLUME_UP || kc == KeyEvent.KEYCODE_VOLUME_DOWN)
                && web != null && pageReady && errorView.getVisibility() != View.VISIBLE) {
            if (e.getAction() == KeyEvent.ACTION_DOWN) {
                long now = SystemClock.uptimeMillis();
                if (e.getRepeatCount() == 0 || now - lastVolumeAt >= 150) {
                    lastVolumeAt = now;
                    sendVolume(kc == KeyEvent.KEYCODE_VOLUME_UP ? 1 : -1);
                }
            }
            return true;
        }
        return super.dispatchKeyEvent(e);
    }

    /**
     * 页面定义了 window.__nasbox_volume 就交给页面（调音箱音量）；
     * 没定义则按系统默认行为调手机音量并弹出系统音量条。
     */
    private void sendVolume(final int delta) {
        String js = "(function(){if(typeof window.__nasbox_volume!=='function')return 0;"
                + "try{window.__nasbox_volume(" + delta + ")}catch(e){}return 1})()";
        web.evaluateJavascript(js, v -> {
            if (!"1".equals(v)) systemVolume(delta);
        });
    }

    private void systemVolume(int delta) {
        try {
            AudioManager am = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
            if (am != null) {
                am.adjustSuggestedStreamVolume(delta > 0 ? AudioManager.ADJUST_RAISE : AudioManager.ADJUST_LOWER,
                        AudioManager.USE_DEFAULT_STREAM_TYPE, AudioManager.FLAG_SHOW_UI);
            }
        } catch (Exception ignored) {
        }
    }

    // ---------------------------------------------------------------- 菜单 / 悬浮按钮

    private void openSettings() {
        startActivity(new Intent(this, SettingsActivity.class));
    }

    private void showMenu() {
        if (isFinishing()) return;
        final List<Store.Server> servers = store.servers();
        final String active = store.activeUrl();
        final List<String> labels = new ArrayList<>();
        final List<Runnable> actions = new ArrayList<>();

        labels.add("刷新");
        actions.add(this::reloadPage);
        for (final Store.Server s : servers) {
            if (s.url.equals(active)) continue;
            labels.add("切换到：" + (s.name == null || s.name.isEmpty() ? ServerUrl.hostOf(s.url) : s.name));
            actions.add(() -> {
                store.setActive(s.url);
                loadBase(s.url);
            });
        }
        labels.add("服务器设置");
        actions.add(this::openSettings);
        labels.add(store.keepScreenOn() ? "屏幕常亮：已开（点击关闭）" : "屏幕常亮：已关（点击打开）");
        actions.add(() -> {
            store.setKeepScreenOn(!store.keepScreenOn());
            applyKeepScreenOn();
            toast(store.keepScreenOn() ? "已打开屏幕常亮" : "已关闭屏幕常亮");
        });
        if (store.showFab()) {
            labels.add("隐藏这个悬浮按钮");
            actions.add(() -> {
                store.setShowFab(false);
                fab.setVisibility(View.GONE);
                toast("已隐藏。长按桌面图标 → 服务器设置，可重新打开");
            });
        }
        new AlertDialog.Builder(this)
                .setItems(labels.toArray(new String[0]), (d, which) -> actions.get(which).run())
                .show();
    }

    private void applyKeepScreenOn() {
        if (store.keepScreenOn()) {
            getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        } else {
            getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        }
    }

    @SuppressLint("ClickableViewAccessibility")
    private void setupFab() {
        final int slop = ViewConfiguration.get(this).getScaledTouchSlop();
        fab.setOnTouchListener(new View.OnTouchListener() {
            float downX, downY, startX, startY;
            boolean dragging;

            @Override
            public boolean onTouch(View v, MotionEvent e) {
                switch (e.getActionMasked()) {
                    case MotionEvent.ACTION_DOWN:
                        downX = e.getRawX();
                        downY = e.getRawY();
                        startX = v.getX();
                        startY = v.getY();
                        dragging = false;
                        v.setAlpha(1f);
                        return true;
                    case MotionEvent.ACTION_MOVE: {
                        float dx = e.getRawX() - downX, dy = e.getRawY() - downY;
                        if (!dragging && Math.hypot(dx, dy) > slop) dragging = true;
                        if (dragging) {
                            v.setX(clamp(startX + dx, 0, root.getWidth() - v.getWidth()));
                            v.setY(clamp(startY + dy, 0, root.getHeight() - v.getHeight()));
                        }
                        return true;
                    }
                    case MotionEvent.ACTION_UP:
                        v.setAlpha(0.8f);
                        if (dragging) {
                            boolean right = v.getX() + v.getWidth() / 2f > root.getWidth() / 2f;
                            int range = Math.max(1, root.getHeight() - v.getHeight());
                            store.setFabPos(right, v.getY() / range);
                            placeFab();
                        } else {
                            showMenu();
                        }
                        return true;
                    case MotionEvent.ACTION_CANCEL:
                        v.setAlpha(0.8f);
                        placeFab();
                        return true;
                    default:
                        return false;
                }
            }
        });
        fab.setAlpha(0.8f);
        root.addOnLayoutChangeListener((v, l, t, r, b, ol, ot, or, ob) -> {
            if (r - l != or - ol || b - t != ob - ot) placeFab();
        });
    }

    private void placeFab() {
        int w = root.getWidth(), h = root.getHeight();
        if (w == 0 || h == 0) return;
        int m = Math.round(8 * getResources().getDisplayMetrics().density);
        int fw = fab.getWidth() > 0 ? fab.getWidth() : Math.round(40 * getResources().getDisplayMetrics().density);
        float y = clamp(store.fabY(), 0f, 1f) * Math.max(1, h - fw);
        fab.setX(store.fabOnRight() ? w - fw - m : m);
        fab.setY(clamp(y, m, Math.max(m, h - fw - m)));
    }

    private static float clamp(float v, float lo, float hi) {
        return Math.max(lo, Math.min(hi, v));
    }

    private void toast(String s) {
        Toast.makeText(this, s, Toast.LENGTH_SHORT).show();
    }

    // ---------------------------------------------------------------- 生命周期

    @Override
    protected void onResume() {
        super.onResume();
        if (web == null) return;
        web.onResume();
        applyKeepScreenOn();
        fab.setVisibility(store.showFab() ? View.VISIBLE : View.GONE);
        root.post(this::placeFab);

        String base = store.activeUrl();
        if (base == null) {
            // 还没有任何服务器：进设置页；从设置页空手返回就退出
            if (settingsLaunched) {
                finish();
            } else {
                settingsLaunched = true;
                openSettings();
            }
            return;
        }
        settingsLaunched = false;
        if (!base.equals(loadedBase)) loadBase(base);
    }

    @Override
    protected void onPause() {
        if (web != null) web.onPause();
        CookieManager.getInstance().flush();
        super.onPause();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putBoolean("settingsLaunched", settingsLaunched);
        if (web != null && !webDead && loadedBase != null && !loadFailed) {
            Bundle wb = new Bundle();
            if (web.saveState(wb) != null) {
                out.putBundle("web", wb);
                out.putString("base", loadedBase);
            }
        }
    }

    @Override
    protected void onRestoreInstanceState(Bundle saved) {
        super.onRestoreInstanceState(saved);
        settingsLaunched = saved.getBoolean("settingsLaunched", false);
    }

    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        if (web != null && errorView.getVisibility() != View.VISIBLE && web.canGoBack()) {
            web.goBack();
        } else {
            // 退到后台而不销毁，下次打开直接回到原来的页面
            moveTaskToBack(true);
        }
    }

    @Override
    protected void onDestroy() {
        ui.removeCallbacksAndMessages(null);
        if (fileCallback != null) {
            fileCallback.onReceiveValue(null);
            fileCallback = null;
        }
        if (web != null) {
            webHolder.removeView(web);
            web.removeJavascriptInterface(BRIDGE_NAME);
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }
}
