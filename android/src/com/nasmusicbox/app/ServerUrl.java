package com.nasmusicbox.app;

import java.net.URI;
import java.util.Locale;

/** 服务器地址的校验与规整。纯 Java，不依赖 Android，可在电脑上直接跑单元测试。 */
public final class ServerUrl {
    private ServerUrl() {}

    public static final String EXAMPLE = "http://192.168.1.10:8090";

    /**
     * 把用户输入规整成 scheme://host[:port][/path]（无结尾斜杠）。
     * 不合法时抛 IllegalArgumentException，message 是可直接给用户看的中文。
     */
    public static String normalize(String raw) {
        String s = raw == null ? "" : raw.trim();
        // 中文输入法下常见的全角符号
        s = s.replace('：', ':').replace('。', '.').replace('／', '/');
        if (s.isEmpty()) throw new IllegalArgumentException("请输入服务器地址，例如 " + EXAMPLE);
        for (int i = 0; i < s.length(); i++) {
            if (Character.isWhitespace(s.charAt(i))) {
                throw new IllegalArgumentException("地址里不能有空格");
            }
        }
        if (!s.matches("^[A-Za-z][A-Za-z0-9+.-]*://.*")) s = "http://" + s;

        URI u;
        try {
            u = new URI(s);
        } catch (Exception e) {
            throw new IllegalArgumentException("地址格式不对，例如 " + EXAMPLE);
        }
        String scheme = u.getScheme() == null ? "" : u.getScheme().toLowerCase(Locale.ROOT);
        if (!scheme.equals("http") && !scheme.equals("https")) {
            throw new IllegalArgumentException("只支持 http:// 或 https:// 开头的地址");
        }
        String host = u.getHost();
        if (host == null || host.isEmpty()) {
            throw new IllegalArgumentException("地址格式不对，例如 " + EXAMPLE);
        }
        if (u.getUserInfo() != null) {
            throw new IllegalArgumentException("地址里不要带用户名或密码");
        }
        if (u.getRawQuery() != null || u.getRawFragment() != null) {
            throw new IllegalArgumentException("地址里不要带 ? 或 # 后面的内容");
        }
        host = host.toLowerCase(Locale.ROOT);
        if (host.matches("^[0-9.]+$")) {
            String[] parts = host.split("\\.", -1);
            boolean ok = parts.length == 4;
            for (int i = 0; ok && i < parts.length; i++) {
                String p = parts[i];
                if (p.isEmpty() || p.length() > 3 || Integer.parseInt(p) > 255) ok = false;
            }
            if (!ok) throw new IllegalArgumentException("IP 地址不对，应为 4 段数字，例如 192.168.1.10");
        }
        int port = u.getPort();
        if (port == 0 || port > 65535) {
            throw new IllegalArgumentException("端口号应在 1–65535 之间");
        }
        if (port == -1 && s.matches("^[A-Za-z][A-Za-z0-9+.-]*://[^/]*:$")) {
            throw new IllegalArgumentException("冒号后面缺端口号，例如 :8090");
        }
        if ((scheme.equals("http") && port == 80) || (scheme.equals("https") && port == 443)) port = -1;

        String path = u.getRawPath() == null ? "" : u.getRawPath();
        while (path.endsWith("/")) path = path.substring(0, path.length() - 1);

        StringBuilder sb = new StringBuilder();
        sb.append(scheme).append("://").append(host);
        if (port != -1) sb.append(':').append(port);
        sb.append(path);
        return sb.toString();
    }

    /** 两个地址是否同源（协议、主机、端口都相同）。解析失败按不同源处理。 */
    public static boolean sameOrigin(String a, String b) {
        try {
            URI x = new URI(a), y = new URI(b);
            if (x.getScheme() == null || y.getScheme() == null) return false;
            if (x.getHost() == null || y.getHost() == null) return false;
            return x.getScheme().equalsIgnoreCase(y.getScheme())
                    && x.getHost().equalsIgnoreCase(y.getHost())
                    && port(x) == port(y);
        } catch (Exception e) {
            return false;
        }
    }

    private static int port(URI u) {
        if (u.getPort() != -1) return u.getPort();
        return "https".equalsIgnoreCase(u.getScheme()) ? 443 : 80;
    }

    /** 取主机名，用作默认显示名。 */
    public static String hostOf(String url) {
        try {
            String h = new URI(url).getHost();
            return h == null ? url : h;
        } catch (Exception e) {
            return url;
        }
    }
}
