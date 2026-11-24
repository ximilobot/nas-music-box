package com.nasmusicbox.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.Switch;
import android.widget.TextView;

import java.util.List;

/** 原生设置页：添加 / 测试 / 切换 / 修改 / 删除服务器地址，以及两个开关。首次启动时也是它。 */
public class SettingsActivity extends Activity {
    private Store store;
    private LinearLayout listView;
    private TextView intro, savedTitle, formTitle, status;
    private EditText inputUrl, inputName;
    private Button btnTest, btnSave, btnDone, btnCancelEdit;
    private View busy;

    /** 正在修改的服务器地址；null 表示表单是「添加」 */
    private String editingUrl;
    private boolean testing;
    private boolean destroyed;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        store = new Store(this);
        setContentView(R.layout.activity_settings);
        Bars.apply(this, getColor(R.color.bg));

        listView = findViewById(R.id.server_list);
        intro = findViewById(R.id.intro);
        savedTitle = findViewById(R.id.saved_title);
        formTitle = findViewById(R.id.form_title);
        status = findViewById(R.id.status);
        inputUrl = findViewById(R.id.input_url);
        inputName = findViewById(R.id.input_name);
        btnTest = findViewById(R.id.btn_test);
        btnSave = findViewById(R.id.btn_save);
        btnDone = findViewById(R.id.btn_done);
        btnCancelEdit = findViewById(R.id.btn_cancel_edit);
        busy = findViewById(R.id.busy);

        btnTest.setOnClickListener(v -> run(false));
        btnSave.setOnClickListener(v -> run(true));
        btnDone.setOnClickListener(v -> finishToMain());
        btnCancelEdit.setOnClickListener(v -> setEditing(null));
        inputName.setOnEditorActionListener((v, id, e) -> {
            if (id == EditorInfo.IME_ACTION_DONE) {
                run(true);
                return true;
            }
            return false;
        });

        Switch keepOn = findViewById(R.id.sw_keep_on);
        keepOn.setChecked(store.keepScreenOn());
        keepOn.setOnCheckedChangeListener((b, on) -> store.setKeepScreenOn(on));
        Switch fab = findViewById(R.id.sw_fab);
        fab.setChecked(store.showFab());
        fab.setOnCheckedChangeListener((b, on) -> store.setShowFab(on));

        String ver = "";
        try {
            ver = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception ignored) {
        }
        ((TextView) findViewById(R.id.version)).setText("NAS音乐盒 App  v" + ver);

        if (saved != null) editingUrl = saved.getString("editing");
        render();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putString("editing", editingUrl);
    }

    @Override
    protected void onDestroy() {
        destroyed = true;
        super.onDestroy();
    }

    private void render() {
        final List<Store.Server> servers = store.servers();
        final String active = store.activeUrl();
        boolean empty = servers.isEmpty();

        intro.setText(empty
                ? "先填 NAS 上「NAS 音乐盒」服务的地址。手机要和 NAS 连在同一个网络里。地址能连通才会保存。"
                : "音乐在小爱音箱上播放，手机只是遥控器。可以存多个地址（比如家里内网、外网各一个）随时切换。");
        savedTitle.setVisibility(empty ? View.GONE : View.VISIBLE);
        btnDone.setVisibility(empty ? View.GONE : View.VISIBLE);
        formTitle.setText(editingUrl != null ? "修改服务器" : (empty ? "服务器地址" : "添加服务器"));
        btnCancelEdit.setVisibility(editingUrl != null ? View.VISIBLE : View.GONE);

        listView.removeAllViews();
        LayoutInflater inf = LayoutInflater.from(this);
        for (final Store.Server s : servers) {
            View row = inf.inflate(R.layout.item_server, listView, false);
            ((RadioButton) row.findViewById(R.id.radio)).setChecked(s.url.equals(active));
            ((TextView) row.findViewById(R.id.name))
                    .setText(s.name == null || s.name.isEmpty() ? ServerUrl.hostOf(s.url) : s.name);
            ((TextView) row.findViewById(R.id.url)).setText(s.url);
            row.setOnClickListener(v -> {
                store.setActive(s.url);
                finishToMain();
            });
            row.findViewById(R.id.edit).setOnClickListener(v -> setEditing(s));
            row.findViewById(R.id.del).setOnClickListener(v -> confirmDelete(s));
            listView.addView(row);
        }
    }

    private void setEditing(Store.Server s) {
        if (testing) return;
        editingUrl = s == null ? null : s.url;
        inputUrl.setText(s == null ? "" : s.url);
        inputName.setText(s == null ? "" : s.name);
        setStatus("", 0);
        render();
        if (s != null) {
            inputUrl.requestFocus();
            inputUrl.setSelection(inputUrl.getText().length());
        }
    }

    private void confirmDelete(final Store.Server s) {
        if (testing) return;
        new AlertDialog.Builder(this)
                .setMessage("删除这个服务器地址？\n" + s.url)
                .setNegativeButton("取消", null)
                .setPositiveButton("删除", (d, w) -> {
                    List<Store.Server> list = store.servers();
                    for (int i = list.size() - 1; i >= 0; i--) {
                        if (list.get(i).url.equals(s.url)) list.remove(i);
                    }
                    store.saveServers(list);
                    if (s.url.equals(editingUrl)) {
                        editingUrl = null;
                        inputUrl.setText("");
                        inputName.setText("");
                    }
                    render();
                })
                .show();
    }

    /** 校验 → 测试连接 →（save 时）成功才保存。 */
    private void run(final boolean save) {
        if (testing) return;
        final String url;
        try {
            url = ServerUrl.normalize(inputUrl.getText().toString());
        } catch (IllegalArgumentException e) {
            setStatus(e.getMessage(), R.color.err);
            inputUrl.requestFocus();
            return;
        }
        inputUrl.setText(url);
        inputUrl.setSelection(url.length());
        final String name = inputName.getText().toString().trim();
        hideKeyboard();
        setBusy(true);
        setStatus("正在连接 " + url + " …", 0);

        new Thread(() -> {
            final ConnTester.Result r = ConnTester.test(url);
            runOnUiThread(() -> {
                if (destroyed) return;
                setBusy(false);
                if (!r.ok) {
                    setStatus(r.message + (save ? "\n没有保存。" : ""), R.color.err);
                    return;
                }
                if (!save) {
                    setStatus("连接成功，可以保存。", R.color.ok);
                    return;
                }
                saveServer(name, url);
            });
        }, "nasbox-test").start();
    }

    private void saveServer(String name, String url) {
        List<Store.Server> list = store.servers();
        boolean first = list.isEmpty();
        // 修改：替换原条目；地址和已有条目重复时合并成一条
        int at = -1;
        for (int i = 0; i < list.size(); i++) {
            if (list.get(i).url.equals(editingUrl)) at = i;
        }
        for (int i = list.size() - 1; i >= 0; i--) {
            if (i != at && list.get(i).url.equals(url)) {
                if (name.isEmpty()) name = list.get(i).name;
                list.remove(i);
                if (i < at) at--;
            }
        }
        if (at >= 0) {
            list.set(at, new Store.Server(name, url));
        } else {
            list.add(new Store.Server(name, url));
        }
        store.saveServers(list);
        store.setActive(url);
        editingUrl = null;
        inputUrl.setText("");
        inputName.setText("");
        render();
        setStatus("已保存，并设为当前服务器。", R.color.ok);
        if (first) finishToMain();
    }

    private void setBusy(boolean b) {
        testing = b;
        busy.setVisibility(b ? View.VISIBLE : View.INVISIBLE);
        btnTest.setEnabled(!b);
        btnSave.setEnabled(!b);
        inputUrl.setEnabled(!b);
        inputName.setEnabled(!b);
    }

    private void setStatus(String s, int colorRes) {
        status.setText(s);
        status.setTextColor(getColor(colorRes == 0 ? R.color.sub : colorRes));
    }

    private void hideKeyboard() {
        try {
            InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
            if (imm != null) imm.hideSoftInputFromWindow(inputUrl.getWindowToken(), 0);
        } catch (Exception ignored) {
        }
    }

    /** 回到播放器。若设置页是从桌面快捷方式单独打开的，则顺便把播放器启动起来。 */
    private void finishToMain() {
        if (store.activeUrl() != null && isTaskRoot()) {
            startActivity(new Intent(this, MainActivity.class));
        }
        finish();
    }
}
