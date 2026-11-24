package com.nasmusicbox.app;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** 本机设置：服务器列表、当前服务器、开关。存在 SharedPreferences 里。 */
final class Store {
    static final class Server {
        String name;
        String url;

        Server(String name, String url) {
            this.name = name;
            this.url = url;
        }
    }

    private final SharedPreferences p;

    Store(Context c) {
        p = c.getApplicationContext().getSharedPreferences("nasbox", Context.MODE_PRIVATE);
    }

    List<Server> servers() {
        List<Server> out = new ArrayList<>();
        try {
            JSONArray a = new JSONArray(p.getString("servers", "[]"));
            for (int i = 0; i < a.length(); i++) {
                JSONObject o = a.getJSONObject(i);
                String url = o.optString("url", "");
                if (!url.isEmpty()) out.add(new Server(o.optString("name", ""), url));
            }
        } catch (Exception ignored) {
        }
        return out;
    }

    void saveServers(List<Server> list) {
        JSONArray a = new JSONArray();
        try {
            for (Server s : list) {
                JSONObject o = new JSONObject();
                o.put("name", s.name == null ? "" : s.name);
                o.put("url", s.url);
                a.put(o);
            }
        } catch (Exception ignored) {
        }
        p.edit().putString("servers", a.toString()).apply();
    }

    /** 当前服务器地址；没有任何服务器时返回 null。 */
    String activeUrl() {
        List<Server> list = servers();
        if (list.isEmpty()) return null;
        String act = p.getString("active", null);
        for (Server s : list) if (s.url.equals(act)) return act;
        return list.get(0).url;
    }

    void setActive(String url) {
        p.edit().putString("active", url).apply();
    }

    boolean keepScreenOn() {
        return p.getBoolean("keep_on", false);
    }

    void setKeepScreenOn(boolean v) {
        p.edit().putBoolean("keep_on", v).apply();
    }

    boolean showFab() {
        return p.getBoolean("fab", true);
    }

    void setShowFab(boolean v) {
        p.edit().putBoolean("fab", v).apply();
    }

    boolean fabOnRight() {
        return p.getBoolean("fab_right", true);
    }

    float fabY() {
        return p.getFloat("fab_y", 0.62f);
    }

    void setFabPos(boolean right, float y) {
        p.edit().putBoolean("fab_right", right).putFloat("fab_y", y).apply();
    }
}
