#!/usr/bin/env bash
# 在电脑上跑逻辑测试（地址校验、测试连接），不需要手机。会临时起一个本地假服务器。
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_HOME="${NASBOX_BUILD_HOME:-$HOME/workspace/work/xiaoai-nas/android-build}"
export PATH="$BUILD_HOME/jdk/bin:$PATH"
T="$(mktemp -d)"; trap 'kill $SRV 2>/dev/null || true; rm -rf "$T"' EXIT
javac -encoding UTF-8 -d "$T" "$SRC/src/com/nasmusicbox/app/ServerUrl.java" \
  "$SRC/src/com/nasmusicbox/app/ConnTester.java" "$SRC/test/LogicTest.java"
python3 - <<'PY' &
import http.server
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        m={'/good/api/status':(200,'application/json',b'{"ok":true}'),
           '/html/api/status':(200,'text/html',b'<html>router</html>'),
           '/boom/api/status':(500,'text/plain',b'err')}
        c,t,b=m.get(self.path,(404,'text/plain',b'nf'))
        self.send_response(c); self.send_header('Content-Type',t); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
http.server.HTTPServer(('127.0.0.1',18990),H).serve_forever()
PY
SRV=$!
sleep 1
# preferIPv4Stack：某些沙箱环境里 Java 的双栈套接字连 127.0.0.1 会异常，强制走 IPv4
java -Dstdout.encoding=UTF-8 -Djava.net.preferIPv4Stack=true -cp "$T" LogicTest http://127.0.0.1:18990
