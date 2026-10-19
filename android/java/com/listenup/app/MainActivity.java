package com.listenup.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.JsPromptResult;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

/**
 * 听力训练 · 安卓客户端
 *
 * 这是一个「瘦客户端」：界面完全复用电脑端那套网页，语音识别、
 * 切句、媒体下载全都跑在电脑上，手机只负责显示和播放。
 *
 * 为什么不在手机上跑识别：
 *   Whisper 模型 + 解码在这台 18 核电脑上处理 16 分钟音频要 100 秒左右，
 *   手机 CPU 会慢一个数量级、发热、耗电，而且模型要占几百 MB。
 *   做成瘦客户端既快又省电，电脑上的库还能被多台设备共用。
 */
public class MainActivity extends Activity {

    private static final String PREFS = "listenup";
    private static final String KEY_URL = "server_url";

    private WebView web;
    private ProgressBar bar;
    private SharedPreferences prefs;

    private static final int REQ_FILE = 1001;
    private ValueCallback<Uri[]> fileCallback;
    private JsPromptResult jsResult;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);

        // 状态栏跟着深色主题走
        getWindow().setStatusBarColor(Color.parseColor("#08090D"));
        getWindow().setNavigationBarColor(Color.parseColor("#08090D"));

        String saved = prefs.getString(KEY_URL, "");
        if (saved.isEmpty()) {
            showSetup();
        } else {
            showBrowser(saved);
        }
    }

    // ---------------------------------------------------------------- 首次配置

    private void showSetup() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setBackgroundColor(Color.parseColor("#08090D"));
        int pad = dp(28);
        root.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText(R.string.setup_title);
        title.setTextColor(Color.parseColor("#F2F5FA"));
        title.setTextSize(24);
        title.setGravity(Gravity.CENTER);
        root.addView(title);

        TextView tip = new TextView(this);
        tip.setText(R.string.setup_tip);
        tip.setTextColor(Color.parseColor("#8B94A8"));
        tip.setTextSize(13);
        tip.setGravity(Gravity.CENTER);
        tip.setPadding(0, dp(14), 0, dp(22));
        root.addView(tip);

        final EditText input = new EditText(this);
        input.setHint(R.string.setup_hint);
        input.setHintTextColor(Color.parseColor("#5B6376"));
        input.setTextColor(Color.parseColor("#F2F5FA"));
        input.setTextSize(15);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setBackground(roundRect("#161922", 14));
        input.setPadding(dp(16), dp(14), dp(16), dp(14));
        root.addView(input, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        Button ok = new Button(this);
        ok.setText(R.string.btn_ok);
        ok.setTextColor(Color.WHITE);
        ok.setTextSize(16);
        ok.setAllCaps(false);
        ok.setBackground(roundRect("#0A84FF", 14));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(52));
        lp.topMargin = dp(20);
        root.addView(ok, lp);

        ok.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                String url = normalize(input.getText().toString());
                if (url == null) {
                    Toast.makeText(MainActivity.this, "地址格式不对", Toast.LENGTH_SHORT).show();
                    return;
                }
                prefs.edit().putString(KEY_URL, url).apply();
                showBrowser(url);
            }
        });

        setContentView(root);
    }

    private void showUrlDialog() {
        final EditText input = new EditText(this);
        input.setText(prefs.getString(KEY_URL, ""));
        input.setSingleLine(true);
        input.setHint(R.string.setup_hint);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setPadding(dp(16), dp(12), dp(16), dp(12));

        new AlertDialog.Builder(this)
                .setTitle("服务器地址")
                .setView(input)
                .setPositiveButton("保存", new android.content.DialogInterface.OnClickListener() {
                    @Override public void onClick(android.content.DialogInterface d, int w) {
                        String url = normalize(input.getText().toString());
                        if (url == null) {
                            Toast.makeText(MainActivity.this, "地址格式不对",
                                    Toast.LENGTH_SHORT).show();
                            return;
                        }
                        prefs.edit().putString(KEY_URL, url).apply();
                        web.loadUrl(url);
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    /** 补全协议、去掉多余空格；不合法返回 null */
    private String normalize(String raw) {
        if (raw == null) return null;
        String s = raw.trim();
        if (s.isEmpty()) return null;
        if (!s.startsWith("http://") && !s.startsWith("https://")) {
            s = "http://" + s;
        }
        if (!s.endsWith("/")) s = s + "/";
        // 简单合法性：协议后面至少得有个字符
        int idx = s.indexOf("://");
        if (idx < 0 || s.length() <= idx + 3) return null;
        return s;
    }

    // ---------------------------------------------------------------- 主界面

    private void showBrowser(String url) {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#08090D"));

        web = new WebView(this);
        WebSettings st = web.getSettings();
        st.setJavaScriptEnabled(true);
        st.setDomStorageEnabled(true);          // 登录状态用得到
        st.setDatabaseEnabled(true);
        st.setLoadWithOverviewMode(true);
        st.setUseWideViewPort(true);
        st.setSupportZoom(false);
        st.setBuiltInZoomControls(false);
        st.setMediaPlaybackRequiresUserGesture(false);   // 允许自动播放句子音频
        st.setCacheMode(WebSettings.LOAD_DEFAULT);
        st.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true);

        web.setBackgroundColor(Color.parseColor("#08090D"));
                // 网页里的 <input type="file"> 和 prompt() 都需要原生支持，
        // 否则在手机上完全没反应（这是 Android WebView 的默认行为，不是 bug）。
        web.setWebChromeClient(new WebChromeClient() {

            @Override
            public boolean onShowFileChooser(WebView view,
                                             ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                // 上一次的没走完，先取消掉，避免回调泄漏
                if (fileCallback != null) {
                    fileCallback.onReceiveValue(null);
                }
                fileCallback = callback;

                Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("image/*");
                // 允许多选（背景和头像虽然都是单张，但保持通用）
                intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, false);

                try {
                    startActivityForResult(
                            Intent.createChooser(intent, "选择图片"),
                            REQ_FILE);
                } catch (Exception e) {
                    fileCallback = null;
                    Toast.makeText(MainActivity.this,
                            "没有可用的文件选择器", Toast.LENGTH_SHORT).show();
                    return false;
                }
                return true;
            }

            @Override
            public boolean onJsPrompt(WebView view, String url, String message,
                                      String defaultValue, JsPromptResult result) {
                // 网页用 prompt() 改昵称之类；不实现的话会静默返回 null
                final EditText input = new EditText(MainActivity.this);
                input.setText(defaultValue == null ? "" : defaultValue);
                input.setSingleLine(true);
                input.setPadding(dp(16), dp(12), dp(16), dp(12));

                // 先记下来再弹窗：否则用户秒点按钮时回调里还是旧值
                final JsPromptResult pending = result;
                jsResult = result;

                new AlertDialog.Builder(MainActivity.this)
                        .setTitle(message)
                        .setView(input)
                        .setPositiveButton("确定", new DialogInterface.OnClickListener() {
                            @Override public void onClick(DialogInterface d, int w) {
                                pending.confirm(input.getText().toString());
                            }
                        })
                        .setNegativeButton("取消", new DialogInterface.OnClickListener() {
                            @Override public void onClick(DialogInterface d, int w) {
                                jsResult.cancel();
                            }
                        })
                        .setOnCancelListener(new DialogInterface.OnCancelListener() {
                            @Override public void onCancel(DialogInterface d) {
                                jsResult.cancel();
                            }
                        })
                        .show();
                return true;
            }
        });
        web.setWebViewClient(new WebViewClient() {

            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
                String u = req.getUrl().toString();
                // 内置的错误页用自定义协议回调，不跳出应用
                if (u.startsWith("listenup://change")) {
                    showUrlDialog();
                    return true;
                }
                if (u.startsWith("listenup://retry")) {
                    v.reload();
                    return true;
                }
                return false;
            }

            @Override
            public void onPageStarted(WebView v, String u, android.graphics.Bitmap f) {
                bar.setVisibility(View.VISIBLE);
            }

            @Override
            public void onPageFinished(WebView v, String u) {
                bar.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest req,
                                        WebResourceError err) {
                if (req.isForMainFrame()) {
                    bar.setVisibility(View.GONE);
                    showErrorPage(err.getDescription().toString());
                }
            }
        });

        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(100);
        FrameLayout.LayoutParams bl = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(3));
        bl.gravity = Gravity.TOP;
        root.addView(bar, bl);

        setContentView(root);
        web.loadUrl(url);
    }

    /** 连接失败时给一个能自救的页面，而不是让用户对着白屏 */
    private void showErrorPage(String detail) {
        String current = prefs.getString(KEY_URL, "");
        String safe = current.replace("&", "&amp;").replace("<", "&lt;");
        String d = detail == null ? "" : detail.replace("&", "&amp;")
                .replace("<", "&lt;").replace(">", "&gt;");
        String html =
            "<!DOCTYPE html><html><head><meta charset='utf-8'>" +
            "<meta name='viewport' content='width=device-width,initial-scale=1'>" +
            "<style>" +
            "html,body{height:100%;margin:0;background:#08090D;color:#C8CFDC;" +
            "font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif}" +
            ".w{display:flex;flex-direction:column;align-items:center;justify-content:center;" +
            "height:100%;padding:32px;box-sizing:border-box;text-align:center}" +
            ".i{font-size:56px;margin-bottom:18px}" +
            "h1{font-size:19px;color:#F2F5FA;margin:0 0 10px}" +
            "p{font-size:13.5px;color:#8B94A8;line-height:1.8;margin:0 0 6px}" +
            "code{background:#161922;padding:3px 8px;border-radius:6px;font-size:12px;" +
            "color:#C8CFDC;word-break:break-all}" +
            ".b{display:block;width:100%;max-width:320px;margin-top:14px;padding:14px;" +
            "border-radius:12px;font-size:15px;text-decoration:none}" +
            ".p{background:#0A84FF;color:#fff;font-weight:600}" +
            ".s{background:#1D212B;color:#C8CFDC;border:1px solid #ffffff1a}" +
            "</style></head><body><div class='w'>" +
            "<div class='i'>📡</div>" +
            "<h1>连不上电脑</h1>" +
            "<p>当前地址：<code>" + safe + "</code></p>" +
            "<p>请确认电脑开着、服务在运行</p>" +
            "<p>" + d + "</p>" +
            "<a class='b p' href='listenup://retry'>重试</a>" +
            "<a class='b s' href='listenup://change'>修改地址</a>" +
            "</div></body></html>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }


    // ---------------------------------------------------------------- 文件选择回调

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req != REQ_FILE) {
            super.onActivityResult(req, res, data);
            return;
        }
        Uri[] out = null;
        if (res == RESULT_OK && data != null) {
            if (data.getData() != null) {
                out = new Uri[]{ data.getData() };
            } else if (data.getClipData() != null) {
                int n = data.getClipData().getItemCount();
                out = new Uri[n];
                for (int i = 0; i < n; i++) {
                    out[i] = data.getClipData().getItemAt(i).getUri();
                }
            }
        }
        if (fileCallback != null) {
            fileCallback.onReceiveValue(out);
            fileCallback = null;
        }
    }

    // ---------------------------------------------------------------- 交互

    @Override
    public boolean onKeyDown(int code, KeyEvent event) {
        if (code == KeyEvent.KEYCODE_BACK && web != null && web.canGoBack()) {
            web.goBack();
            return true;
        }
        return super.onKeyDown(code, event);
    }

    @Override
    protected void onDestroy() {
        if (web != null) web.destroy();
        super.onDestroy();
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private GradientDrawable roundRect(String color, int radiusDp) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(Color.parseColor(color));
        d.setCornerRadius(dp(radiusDp));
        return d;
    }
}
