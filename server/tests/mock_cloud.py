"""模拟小米云端：账号登录/续期、扫码长轮询、device_list、ubus（含一台虚拟音箱）、对话记录、MIoT。

仅用于测试，不代表真实接口的全部行为。
"""
from __future__ import annotations

import asyncio
import base64
import json
import time

from aiohttp import web


class Speaker:
    """虚拟音箱：收到播放指令后按 sim_duration_ms 「播放」，可配置各种异常行为。"""
    def __init__(self):
        self.status = 0            # 0 空闲 1 播放 2 暂停
        self.volume = 30
        self.url = ""
        self.started = 0.0
        self.pos_base = 0.0        # 暂停时已播放的毫秒
        self.sim_duration_ms = 2000
        self.audio_id = "1582971365183456177"
        self.report_position = True
        self.end_status = 2        # 播完后上报的状态
        self.loop_self = False     # 音箱自行单曲循环
        self.loop_type = 1         # 音箱上报的循环模式：0 单曲循环 / 1 列表循环（micli play 用 1）
        self.never_start = False   # 收到播放指令但不开始
        self.play_log: list[str] = []
        self.ops: list[str] = []   # 所有收到的操作，按顺序
        self.tts: list[str] = []

    def _pos(self) -> float:
        if self.status == 1:
            return self.pos_base + (time.monotonic() - self.started) * 1000
        return self.pos_base

    def tick(self):
        if self.status == 1 and self._pos() >= self.sim_duration_ms:
            if self.loop_self:
                self.pos_base = 0
                self.started = time.monotonic()
            else:
                self.status = self.end_status
                self.pos_base = self.sim_duration_ms

    def play(self, url: str):
        self.url = url
        self.play_log.append(url)
        self.ops.append("play_url")
        if self.never_start:
            return
        self.status = 1
        self.pos_base = 0
        self.started = time.monotonic()

    def info(self) -> dict:
        self.tick()
        d = {"status": self.status, "volume": self.volume, "loop_type": self.loop_type}
        if self.report_position and self.url:
            d["play_song_detail"] = {"audio_id": self.audio_id, "position": int(min(self._pos(), self.sim_duration_ms)),
                                     "duration": self.sim_duration_ms}
        return d

    def op(self, action: str):
        self.tick()
        self.ops.append(action)
        if action == "pause" and self.status == 1:
            self.pos_base = self._pos()
            self.status = 2
        elif action == "play" and self.status == 2 and self.pos_base < self.sim_duration_ms:
            self.status = 1
            self.started = time.monotonic()
        elif action == "stop":
            self.status = 0
            self.pos_base = 0


class MockCloud:
    def __init__(self):
        self.base = ""
        self.pass_token = "PASS-TOKEN-ORIGINAL-0001"
        self.user_id = "123456789"
        self.valid_service_tokens: set[str] = set()
        self.token_seq = 0
        self.renew_count = 0
        self.login_calls = 0
        self.account_503 = False       # 账号服务器 5xx
        self.login_weird_code = None   # serviceLogin 返回奇怪的错误码
        self.mina_503 = 0              # 接下来 N 次 mina 请求返回 503
        self.play_fail = 0             # 接下来 N 次播放指令返回 503
        self.device_off = False        # 音箱关机：ubus 一律返回 code=101
        self.status_down = False       # 状态接口 503
        self.qr_scanned = asyncio.Event()
        self.qr_new_pass_token = "PASS-TOKEN-AFTER-QR-0002"
        self.records: list[dict] = []
        self.speaker = Speaker()
        self.miot_calls: list[dict] = []
        self.seen_ua: set[str] = set()
        self.seen_device_ids: set[str] = set()
        self.conv_calls = 0
        self.runner = None
        self.port = 0

    # ---------------------------------------------------------------- 生命周期
    def app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_get("/pass/serviceLogin", self.service_login)
        r.add_get("/sts", self.sts)
        r.add_get("/longPolling/loginUrl", self.login_url)
        r.add_get("/lp", self.lp)
        r.add_get("/admin/v2/device_list", self.device_list)
        r.add_post("/remote/ubus", self.ubus)
        r.add_get("/device_profile/v2/conversation", self.conversation)
        r.add_post("/miotspec/action", self.miot)
        r.add_get("/__test/{what}", self.test_hook)      # 仅供独立进程联调时从外部操纵模拟云端
        return app

    async def start(self, port: int = 0):
        self.runner = web.AppRunner(self.app(), access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", port)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        return self.base

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    def new_service_token(self) -> str:
        self.token_seq += 1
        t = f"SERVICE-TOKEN-{self.token_seq:04d}-" + "x" * 30
        self.valid_service_tokens.add(t)
        return t

    def say(self, query: str, answer: str = "好的"):
        self.records.insert(0, {"time": int(time.time() * 1000), "query": query,
                                "answers": [{"tts": {"text": answer}}]})

    async def test_hook(self, request: web.Request):
        what = request.match_info["what"]
        q = request.query
        sp = self.speaker
        if what == "scan":
            self.qr_scanned.set()
        elif what == "say":
            self.say(q.get("q", ""))
        elif what == "expire":
            self.valid_service_tokens.clear()
        elif what == "revoke":
            self.valid_service_tokens.clear()
            self.pass_token = "REVOKED"
        elif what == "duration":
            sp.sim_duration_ms = int(q.get("ms", 2000))
        return web.json_response({"status": sp.status, "volume": sp.volume, "play_log": sp.play_log, "ops": sp.ops,
                                  "tts": sp.tts, "miot": self.miot_calls, "renew_count": self.renew_count})

    # ---------------------------------------------------------------- 账号
    async def service_login(self, request: web.Request):
        self.login_calls += 1
        self.seen_ua.add(request.headers.get("User-Agent", ""))
        self.seen_device_ids.add(request.cookies.get("deviceId", ""))
        if self.account_503:
            return web.Response(status=503, text="busy")
        sid = request.query.get("sid", "")
        if self.login_weird_code is not None:
            return web.Response(text="&&&START&&&" + json.dumps({"code": self.login_weird_code, "desc": "系统繁忙"}))
        if request.cookies.get("passToken") == self.pass_token and request.cookies.get("userId") == self.user_id:
            body = {"code": 0, "location": f"{self.base}/sts?sid={sid}&d=1", "nonce": 987654321,
                    "ssecurity": base64.b64encode(b"0123456789abcdef").decode(), "userId": int(self.user_id),
                    "passToken": self.pass_token, "cUserId": "cuser-abc"}
        else:
            body = {"code": 70016, "desc": "登录验证失败", "_sign": "sign-xyz", "qs": "%3Fsid%3D" + sid,
                    "callback": "https://sts.api.io.mi.com/sts", "sid": sid,
                    "location": f"https://account.xiaomi.com/fe/service/login/password?sid={sid}&qs=q1&callback=cb1&_sign=s1&serviceParam=sp1"}
        return web.Response(text="&&&START&&&" + json.dumps(body))

    async def sts(self, request: web.Request):
        if not request.query.get("clientSign"):
            return web.Response(status=400, text="no sign")
        self.renew_count += 1
        resp = web.Response(text="ok")
        resp.set_cookie("serviceToken", self.new_service_token())
        return resp

    async def login_url(self, request: web.Request):
        if "sid" not in request.query or "_sign" not in request.query:
            return web.Response(text="&&&START&&&" + json.dumps({"code": 1, "desc": "bad params"}))
        return web.Response(text="&&&START&&&" + json.dumps({
            "code": 0, "loginUrl": "https://account.xiaomi.com/mock/qr-login?ticket=MOCKTICKET",
            "qr": "https://account.xiaomi.com/mock/qr.png", "lp": f"{self.base}/lp?ticket=MOCKTICKET"}))

    async def lp(self, request: web.Request):
        try:
            await asyncio.wait_for(self.qr_scanned.wait(), timeout=20)
        except asyncio.TimeoutError:
            return web.Response(text="&&&START&&&" + json.dumps({"code": 702, "desc": "timeout"}))
        self.pass_token = self.qr_new_pass_token
        return web.Response(text="&&&START&&&" + json.dumps({
            "code": 0, "passToken": self.pass_token, "userId": int(self.user_id), "cUserId": "cuser-abc",
            "ssecurity": base64.b64encode(b"fedcba9876543210").decode(), "psecurity": "psec", "nonce": 111,
            "location": f"{self.base}/sts?sid=mijia"}))

    # ---------------------------------------------------------------- 业务
    def _auth(self, request: web.Request) -> bool:
        return request.cookies.get("serviceToken") in self.valid_service_tokens and request.cookies.get("userId") == self.user_id

    async def device_list(self, request: web.Request):
        if self.mina_503 > 0:
            self.mina_503 -= 1
            return web.Response(status=503)
        if not self._auth(request):
            return web.Response(status=401, text="Unauthorized")
        return web.json_response({"code": 0, "message": "Success", "data": [
            {"deviceID": "mock-device-0001", "miotDID": "100200300", "hardware": "OH2P", "alias": "客厅音箱",
             "name": "Xiaomi 智能音箱 Pro", "presence": "online"},
            {"deviceID": "mock-device-0002", "miotDID": "100200301", "hardware": "LX06", "alias": "卧室音箱",
             "name": "小爱音箱 Pro", "presence": "offline"}]})

    async def ubus(self, request: web.Request):
        if self.mina_503 > 0:
            self.mina_503 -= 1
            return web.Response(status=503)
        if not self._auth(request):
            return web.Response(status=401, text="Unauthorized")
        if self.device_off:
            return web.json_response({"code": 101, "message": "ubus server or device returned invalid result"})
        form = await request.post()
        method = form.get("method")
        msg = json.loads(form.get("message") or "{}")
        sp = self.speaker
        if method in ("player_play_music", "player_play_url"):
            if self.play_fail > 0:
                self.play_fail -= 1
                return web.Response(status=503)
            if method == "player_play_music":
                url = json.loads(msg["music"])["payload"]["audio_items"][0]["stream"]["url"]
            else:
                url = msg["url"]
            sp.ops.append(method)
            sp.play(url)
            return web.json_response({"code": 0, "message": "Success", "data": {"code": 0}})
        if method == "player_get_play_status":
            if self.status_down:
                return web.Response(status=503)
            return web.json_response({"code": 0, "message": "Success", "data": {"code": 0, "info": json.dumps(sp.info())}})
        if method == "player_play_operation":
            sp.op(msg.get("action", ""))
        elif method == "player_set_loop":
            sp.loop_type = int(msg.get("type", 1))
        elif method == "player_set_volume":
            sp.volume = int(msg["volume"])
        elif method == "player_set_positon":
            sp.pos_base = int(msg["position"])
            sp.started = time.monotonic()
        elif method == "text_to_speech":
            sp.tts.append(msg.get("text", ""))
        return web.json_response({"code": 0, "message": "Success", "data": {"code": 0}})

    async def conversation(self, request: web.Request):
        self.conv_calls += 1
        if not self._auth(request) or not request.cookies.get("deviceId"):
            return web.Response(status=401, text="Unauthorized")
        return web.json_response({"code": 0, "message": "Success",
                                  "data": json.dumps({"records": self.records[:2]})})

    async def miot(self, request: web.Request):
        if not self._auth(request):
            return web.Response(status=401, text="Unauthorized")
        form = await request.post()
        if not form.get("signature") or not form.get("_nonce"):
            return web.json_response({"code": -1, "message": "bad signature"})
        self.miot_calls.append(json.loads(form["data"]))
        return web.json_response({"code": 0, "message": "ok", "result": {"code": 0}})


if __name__ == "__main__":
    import sys

    async def _main():
        m = MockCloud()
        await m.start(int(sys.argv[1]) if len(sys.argv) > 1 else 18999)
        print("mock cloud at", m.base, flush=True)
        await asyncio.Event().wait()
    asyncio.run(_main())
