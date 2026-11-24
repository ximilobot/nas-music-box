import com.nasmusicbox.app.ConnTester;
import com.nasmusicbox.app.ServerUrl;

/** 在电脑的 JVM 上跑的逻辑测试（地址校验 + 测试连接），不需要 Android。用法见 run-tests.sh。 */
public class LogicTest {
    static int fail = 0, pass = 0;

    static void eq(String in, String want) {
        String got;
        try {
            got = ServerUrl.normalize(in);
        } catch (IllegalArgumentException e) {
            got = "ERR";
        }
        check("normalize(" + in + ")", want, got);
    }

    static void check(String what, Object want, Object got) {
        if (String.valueOf(want).equals(String.valueOf(got))) {
            pass++;
        } else {
            fail++;
            System.out.println("FAIL " + what + ": want=" + want + " got=" + got);
        }
    }

    public static void main(String[] a) {
        eq("http://192.168.1.10:8090", "http://192.168.1.10:8090");
        eq("  192.168.1.10:8090/ ", "http://192.168.1.10:8090");
        eq("192.168.1.10：8090", "http://192.168.1.10:8090");
        eq("HTTP://NAS.Local:8090//", "http://nas.local:8090");
        eq("https://music.example.com", "https://music.example.com");
        eq("https://music.example.com:443/box/", "https://music.example.com/box");
        eq("http://nas:80", "http://nas");
        eq("http://[fe80::1]:8090", "http://[fe80::1]:8090");
        eq("", "ERR");
        eq("ftp://nas:21", "ERR");
        eq("http://", "ERR");
        eq("http://192.168.1:8090", "ERR");
        eq("192.168.1.300:8090", "ERR");
        eq("http://nas:99999", "ERR");
        eq("http://nas:0", "ERR");
        eq("http://nas:", "ERR");
        eq("http://nas:abc", "ERR");
        eq("http://user:pw@nas:8090", "ERR");
        eq("http://nas:8090/?a=1", "ERR");
        eq("http://nas:8090/#x", "ERR");
        eq("http://na s:8090", "ERR");

        check("sameOrigin same", true, ServerUrl.sameOrigin("http://nas:8090/a/b?x=1", "http://NAS:8090"));
        check("sameOrigin port", false, ServerUrl.sameOrigin("http://nas:8091/", "http://nas:8090"));
        check("sameOrigin scheme", false, ServerUrl.sameOrigin("https://nas:8090/", "http://nas:8090"));
        check("sameOrigin default port", true, ServerUrl.sameOrigin("http://nas/", "http://nas:80"));
        check("sameOrigin other host", false, ServerUrl.sameOrigin("https://github.com/x", "http://nas:8090"));

        if (a.length > 0) {
            String base = a[0];
            check("test ok", true, ConnTester.test(base + "/good").ok);
            check("test html", false, ConnTester.test(base + "/html").ok);
            check("test 404", false, ConnTester.test(base + "/missing").ok);
            check("test 500", false, ConnTester.test(base + "/boom").ok);
            check("test refused", false, ConnTester.test("http://127.0.0.1:9", 2000).ok);
            check("test unknown host", false, ConnTester.test("http://no-such-host.invalid:8090", 3000).ok);
            System.out.println("  refused -> " + ConnTester.test("http://127.0.0.1:9", 2000).message);
            System.out.println("  404     -> " + ConnTester.test(base + "/missing").message);
            System.out.println("  html    -> " + ConnTester.test(base + "/html").message);
        }
        System.out.println("pass=" + pass + " fail=" + fail);
        System.exit(fail == 0 ? 0 : 1);
    }
}
