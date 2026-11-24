package com.nasmusicbox.app;

import android.app.Activity;
import android.graphics.Color;
import android.os.Build;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;

/** 状态栏 / 导航栏配色：背景跟随给定颜色，图标深浅按亮度自动选。 */
final class Bars {
    private Bars() {}

    @SuppressWarnings("deprecation")
    static void apply(Activity a, int color) {
        color |= 0xFF000000;
        boolean light = luminance(color) > 0.55;
        Window w = a.getWindow();
        w.clearFlags(WindowManager.LayoutParams.FLAG_TRANSLUCENT_STATUS);
        w.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
        w.setStatusBarColor(color);
        View d = w.getDecorView();
        int vis = d.getSystemUiVisibility();
        vis = light ? vis | View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR : vis & ~View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
        if (Build.VERSION.SDK_INT >= 26) {
            w.setNavigationBarColor(color);
            vis = light ? vis | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
                    : vis & ~View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
        } else {
            // Android 7.x 的导航栏图标只能是白色，浅色背景下改用黑底
            w.setNavigationBarColor(light ? Color.BLACK : color);
        }
        d.setSystemUiVisibility(vis);
    }

    private static double luminance(int c) {
        return (0.299 * Color.red(c) + 0.587 * Color.green(c) + 0.114 * Color.blue(c)) / 255.0;
    }

    /** 解析 CSS 颜色（#rgb / #rrggbb / rgb() / rgba()）。透明或无法解析时返回 null。 */
    static Integer parseCss(String s) {
        if (s == null) return null;
        s = s.trim().toLowerCase();
        try {
            if (s.startsWith("#")) {
                String h = s.substring(1);
                if (h.length() == 3) {
                    h = "" + h.charAt(0) + h.charAt(0) + h.charAt(1) + h.charAt(1) + h.charAt(2) + h.charAt(2);
                }
                if (h.length() == 8) h = h.substring(0, 6);
                if (h.length() != 6) return null;
                return 0xFF000000 | Integer.parseInt(h, 16);
            }
            if (s.startsWith("rgb")) {
                int i = s.indexOf('('), j = s.indexOf(')');
                if (i < 0 || j < i) return null;
                String[] p = s.substring(i + 1, j).split("[,/\\s]+");
                if (p.length < 3) return null;
                if (p.length >= 4 && Float.parseFloat(p[3].replace("%", "")) == 0f) return null;
                int r = Math.round(Float.parseFloat(p[0]));
                int g = Math.round(Float.parseFloat(p[1]));
                int b = Math.round(Float.parseFloat(p[2]));
                if (r < 0 || r > 255 || g < 0 || g > 255 || b < 0 || b > 255) return null;
                return Color.rgb(r, g, b);
            }
        } catch (Exception ignored) {
        }
        return null;
    }
}
