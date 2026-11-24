package com.nasmusicbox.app;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.ConnectException;
import java.net.HttpURLConnection;
import java.net.NoRouteToHostException;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.net.UnknownHostException;

import javax.net.ssl.SSLException;

/** 「测试连接」：GET {base}/api/status，返回 200 且内容是 JSON 才算成功。纯 Java，须在后台线程调用。 */
public final class ConnTester {
    private ConnTester() {}

    public static final String STATUS_PATH = "/api/status";

    public static final class Result {
        public final boolean ok;
        public final String message;

        Result(boolean ok, String message) {
            this.ok = ok;
            this.message = message;
        }
    }

    public static Result test(String base) {
        return test(base, 6000);
    }

    public static Result test(String base, int timeoutMs) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(base + STATUS_PATH).openConnection();
            c.setConnectTimeout(timeoutMs);
            c.setReadTimeout(timeoutMs);
            c.setUseCaches(false);
            c.setRequestMethod("GET");
            c.setRequestProperty("Accept", "application/json");
            c.setRequestProperty("User-Agent", "NasMusicBox-Android");
            int code = c.getResponseCode();
            if (code == 200) {
                String body = read(c.getInputStream()).trim();
                if (body.startsWith("{") || body.startsWith("[")) {
                    return new Result(true, "连接成功");
                }
                return new Result(false, "这个地址有响应，但不像 NAS 音乐盒服务（" + STATUS_PATH
                        + " 返回的不是 JSON）。检查端口是否写对。");
            }
            if (code == 401 || code == 403) {
                return new Result(false, "服务器拒绝访问（HTTP " + code + "）。");
            }
            if (code == 404) {
                return new Result(false, "这个地址有响应，但不是 NAS 音乐盒服务（" + STATUS_PATH
                        + " 返回 404）。检查端口是否写对。");
            }
            if (code >= 500) {
                return new Result(false, "服务器出错了（HTTP " + code + "），看一下 NAS 上的服务日志。");
            }
            return new Result(false, "服务器返回了意外的状态（HTTP " + code + "）。");
        } catch (UnknownHostException e) {
            return new Result(false, "找不到这台主机，检查地址是否写对、手机是否联网。");
        } catch (SocketTimeoutException e) {
            return new Result(false, "连接超时：手机和 NAS 可能不在同一个网络，或端口被防火墙拦住了。");
        } catch (NoRouteToHostException e) {
            return new Result(false, "到不了这台主机：手机和 NAS 可能不在同一个网络。");
        } catch (ConnectException e) {
            return new Result(false, "连接被拒绝：服务没启动，或端口不对。");
        } catch (SSLException e) {
            return new Result(false, "HTTPS 证书有问题。局域网地址一般用 http:// 开头。");
        } catch (Exception e) {
            String m = e.getMessage();
            return new Result(false, "连接失败" + (m == null ? "" : "：" + m));
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private static String read(InputStream in) throws Exception {
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while (out.size() < 65536 && (n = in.read(buf)) > 0) out.write(buf, 0, n);
            return out.toString("UTF-8");
        } finally {
            in.close();
        }
    }
}
