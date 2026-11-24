"""小米云端客户端（自行实现，不依赖第三方小米库）。

设计要点（针对常见的掉线原因）：
1. deviceId / User-Agent 只生成一次并持久化，之后永不变化。
2. 凭据文件原子写入（临时文件 + rename，权限 600）；任何失败都不会清空或删除凭据。
3. 错误严格分类：
   - NetworkError   网络/超时/5xx —— 凭据原样保留，退避重试；
   - TokenExpired   serviceToken 失效（401 等）—— 用 passToken 静默续期；
   - PassTokenInvalid passToken 被服务端明确拒绝（连续两次确认）—— 才进入「需要重新扫码」，
     且旧凭据会备份保留。
4. 续期有并发锁、指数退避；续期失败时旧 serviceToken 继续保留使用。
5. 所有云端调用都有超时。
6. 日志只记录接口路径与结果码，绝不记录 token / cookie。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import secrets
import shutil
import string
import time
from urllib import parse

import aiohttp

from .util import atomic_write_json, read_json

log = logging.getLogger("nasbox.cloud")

MINA_UA = ("MiHome/6.0.103 (com.xiaomi.mihome; build:6.0.103.1; iOS 14.4.0) "
           "Alamofire/6.0.103 MICO/iOSApp/appStore/6.0.103")
MIIO_UA = "iOS-14.4-6.0.103-iPhone12,3--D7744744F7AF32F0544445285880DD63E47D9BE9-8816080-84A3F44E137B71AE-iPhone"

# 需要用 player_play_music 接口播放的型号（其余用 player_play_url）
USE_PLAY_MUSIC = {"LX04", "LX05", "L05B", "L05C", "L06", "L06A", "X08A", "X10A", "X08C",
                  "X08E", "X8F", "X4B", "OH2", "OH2P", "X6A"}
# MIoT TTS 动作 (siid, aiid)
TTS_ACTION = {"OH2": (5, 3), "OH2P": (7, 3), "LX06": (5, 1), "S12": (5, 1), "L15A": (7, 3),
              "LX5A": (5, 1), "LX01": (5, 1), "LX05": (5, 1), "X10A": (7, 3), "L17A": (7, 3),
              "ASX4B": (5, 3), "L06A": (5, 1), "L05B": (5, 3), "L05C": (5, 3), "X6A": (7, 3),
              "X08E": (7, 3), "L09A": (3, 1), "LX04": (5, 1)}

QR_SID = "mijia"
MINA_SID = "micoapi"
MIIO_SID = "xiaomiio"


class CloudError(Exception):
    """云端返回了业务错误（非网络、非鉴权）。"""


class NetworkError(CloudError):
    """网络不可达 / 超时 / 服务端 5xx。凭据不受影响。"""


class TokenExpired(CloudError):
    """serviceToken 失效，需要用 passToken 续期。"""


class PassTokenInvalid(CloudError):
    """passToken 被服务端明确拒绝。"""


class DeviceOffline(CloudError):
    """云端是通的，但音箱没有响应（关机、断电、断网）。实测音箱关机时 ubus 返回 code=101 或 code=2 超时。"""


class NeedLogin(CloudError):
    """尚未登录或已确认需要重新扫码。"""


def _rand(n: int, chars: str = string.ascii_letters + string.digits) -> str:
    return "".join(random.choices(chars, k=n))


def _strip_start(text: str) -> dict:
    text = text.strip()
    if text.startswith("&&&START&&&"):
        text = text[11:]
    return json.loads(text)


class MiCloud:
    def __init__(self, paths, settings):
        self.paths = paths
        self.settings = settings
        self.creds: dict = read_json(paths.cred_file, None) or {}
        self._ensure_identity()
        self._session: aiohttp.ClientSession | None = None
        self.timeout = aiohttp.ClientTimeout(total=12, connect=6)
        self._renew_locks: dict[str, asyncio.Lock] = {}
        self._renew_fail = 0
        self._renew_next_allowed = 0.0
        self.renewing = False
        self.need_qr = bool(self.creds.get("need_qr"))
        self.net_error = False
        self.last_ok = 0.0
        self.last_error = ""
        self.last_error_ts = 0.0
        self.last_renew = 0.0
        self.confirm_delay = 3.0       # 两次确认 passToken 失效之间的间隔
        self.retry_delay = 1.0         # 续期遇网络错误时的重试基准间隔
        self.devices: list[dict] = self.creds.get("devices_cache", []) if isinstance(self.creds.get("devices_cache"), list) else []
        self.on_change = None          # 状态变化回调
        self.on_login = None           # 登录成功回调
        self.qr: dict = {"state": "idle"}
        self._qr_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self.offline_local: set[str] = set()      # 实际通信判定为离线的音箱
        self._wake = asyncio.Event()

    # ------------------------------------------------------------------ 身份与凭据
    def _ensure_identity(self):
        changed = False
        if not self.creds.get("deviceId"):
            self.creds["deviceId"] = _rand(16).upper()
            changed = True
        if not self.creds.get("pass_o"):
            self.creds["pass_o"] = _rand(16, "0123456789abcdef")
            changed = True
        if not self.creds.get("ua"):
            h = "0123456789ABCDEF"
            id1, id2, id3, id4 = _rand(40, h), _rand(32, h), _rand(32, h), _rand(40, h)
            self.creds["ua"] = (
                "Android-15-11.0.701-Xiaomi-23046RP50C-OS2.0.212.0.VMYCNXM-"
                f"{id1}-CN-{id3}-{id2}-SmartHome-MI_APP_STORE-{id1}|{id4}|{self.creds['pass_o']}-64")
            changed = True
        self.creds.setdefault("services", {})
        if changed:
            self._save()

    def _save(self):
        self.creds["saved_at"] = int(time.time())
        atomic_write_json(self.paths.cred_file, self.creds, 0o600)

    def _backup(self, tag: str):
        """把当前凭据文件另存一份备份（最多保留 5 份）。"""
        src = self.paths.cred_file
        if not os.path.isfile(src):
            return
        dst = f"{src}.bak-{tag}-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            shutil.copy2(src, dst)
            os.chmod(dst, 0o600)
            d = os.path.dirname(src)
            baks = sorted(f for f in os.listdir(d) if f.startswith(os.path.basename(src) + ".bak-"))
            for old in baks[:-5]:
                os.unlink(os.path.join(d, old))
        except OSError as e:
            log.warning("备份凭据失败: %s", e)

    @property
    def logged_in(self) -> bool:
        return bool(self.creds.get("passToken") and self.creds.get("userId"))

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # DummyCookieJar：不让会话自己攒 cookie，每次请求显式带，避免脏 cookie
            self._session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), timeout=self.timeout)
        return self._session

    async def close(self):
        for t in (self._qr_task, self._keepalive_task):
            if t:
                t.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------ 状态
    def state(self) -> str:
        if not self.logged_in:
            return "no_login"
        if self.need_qr:
            return "need_qr"
        if self.renewing:
            return "renewing"
        if self.net_error:
            return "network_error"
        if not self.last_ok:
            return "checking"
        return "ok"

    def status(self) -> dict:
        labels = {"no_login": "未登录", "need_qr": "需要重新扫码", "renewing": "续期中",
                  "network_error": "网络异常", "checking": "校验中", "ok": "正常"}
        st = self.state()
        return {"state": st, "label": labels[st], "last_ok": int(self.last_ok),
                "last_error": self.last_error, "last_error_ts": int(self.last_error_ts),
                "last_renew": int(self.last_renew), "user_id": _mask(str(self.creds.get("userId", ""))),
                "renew_fail_count": self._renew_fail}

    def _notify(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                log.exception("on_change 回调异常")

    def _mark_ok(self):
        changed = self.net_error or not self.last_ok
        self.net_error = False
        self.last_ok = time.time()
        if changed:
            self._notify()

    def _mark_error(self, msg: str, network: bool = False):
        prev = (self.net_error, self.last_error)
        self.last_error = msg
        self.last_error_ts = time.time()
        if network:
            self.net_error = True
        if prev != (self.net_error, self.last_error):
            self._notify()

    # ------------------------------------------------------------------ 底层 HTTP
    async def _http(self, method: str, url: str, *, cookies=None, headers=None, data=None,
                    params=None, timeout: float | None = None, want_cookies=False):
        """返回 (status, text[, cookies])。网络问题统一抛 NetworkError。"""
        to = aiohttp.ClientTimeout(total=timeout, connect=min(6, timeout)) if timeout else self.timeout
        try:
            async with self.session.request(method, url, cookies=cookies, headers=headers, data=data,
                                            params=params, timeout=to) as r:
                text = await r.text(errors="replace")
                if r.status >= 500:
                    raise NetworkError(f"服务端 {r.status}")
                if want_cookies:
                    ck = {}
                    for resp in list(r.history) + [r]:
                        for k, m in resp.cookies.items():
                            ck[k] = m.value
                    return r.status, text, ck
                return r.status, text
        except NetworkError:
            raise
        except asyncio.TimeoutError:
            raise NetworkError("请求超时") from None
        except aiohttp.ClientError as e:
            raise NetworkError(f"网络错误 {type(e).__name__}") from None
        except OSError as e:
            raise NetworkError(f"网络错误 {type(e).__name__}") from None

    # ------------------------------------------------------------------ 续期
    async def _renew_once(self, sid: str):
        """用 passToken 换取 sid 的 serviceToken。成功才替换旧值。"""
        c = self.creds
        cookies = {"sdkVersion": "3.9", "deviceId": c["deviceId"], "pass_o": c["pass_o"],
                   "userId": str(c["userId"]), "passToken": c["passToken"]}
        if c.get("cUserId"):
            cookies["cUserId"] = c["cUserId"]
        status, text = await self._http(
            "GET", f"{self.paths.account_base}/pass/serviceLogin", params={"sid": sid, "_json": "true"},
            cookies=cookies, headers={"User-Agent": c["ua"]})
        try:
            resp = _strip_start(text)
        except Exception:
            raise CloudError(f"serviceLogin 返回无法解析 (HTTP {status})") from None
        code = resp.get("code")
        if code != 0:
            # 登录页形态（要求重新输入凭据）= passToken 已失效
            if code == 70016 or ("_sign" in resp and "callback" in resp) or status == 401:
                raise PassTokenInvalid(f"passToken 被拒绝 (code={code})")
            raise CloudError(f"serviceLogin 失败 code={code}")
        try:
            location, nonce, ssecurity = resp["location"], resp["nonce"], resp["ssecurity"]
        except KeyError as e:
            raise CloudError(f"serviceLogin 响应缺少字段 {e}") from None
        nsec = f"nonce={nonce}&{ssecurity}"
        sign = base64.b64encode(hashlib.sha1(nsec.encode()).digest()).decode()
        sep = "&" if "?" in location else "?"
        status, text, ck = await self._http(
            "GET", location + sep + "clientSign=" + parse.quote(sign),
            headers={"User-Agent": c["ua"]}, want_cookies=True)
        token = ck.get("serviceToken")
        if not token:
            raise CloudError(f"未取得 serviceToken (HTTP {status})")
        c["services"][sid] = {"ssecurity": ssecurity, "serviceToken": token, "ts": int(time.time()), "gen": time.time_ns()}
        if resp.get("passToken"):       # 服务端滚动更新 passToken 时跟随保存
            c["passToken"] = resp["passToken"]
        if resp.get("cUserId"):
            c["cUserId"] = resp["cUserId"]
        c.pop("need_qr", None)
        self._save()

    async def renew(self, sid: str = MINA_SID, *, seen_ts: int | None = None, force: bool = False):
        """续期 serviceToken。带并发锁、退避、passToken 失效二次确认。"""
        if not self.logged_in:
            raise NeedLogin("尚未登录")
        lock = self._renew_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            cur = self.creds["services"].get(sid)
            if seen_ts is not None and cur and cur.get("gen", 0) != seen_ts:
                return                      # 等锁期间别的协程已经续好了
            if self.need_qr and not force:
                raise NeedLogin("需要重新扫码登录")
            now = time.time()
            if not force and now < self._renew_next_allowed:
                raise NetworkError(f"续期退避中，{int(self._renew_next_allowed - now)} 秒后再试（{self.last_error}）")
            self.renewing = True
            self._notify()
            try:
                invalid = 0
                last_exc: Exception | None = None
                for attempt in range(3):
                    try:
                        await self._renew_once(sid)
                        self._renew_fail = 0
                        self._renew_next_allowed = 0
                        self.last_renew = time.time()
                        if self.need_qr:
                            self.need_qr = False
                        self.net_error = False
                        self.last_ok = time.time()
                        log.info("serviceToken 续期成功 sid=%s", sid)
                        return
                    except PassTokenInvalid as e:
                        invalid += 1
                        last_exc = e
                        if invalid >= 2:
                            break
                        log.warning("续期被拒绝（第 1 次），%.0f 秒后复核: %s", self.confirm_delay, e)
                        await asyncio.sleep(self.confirm_delay)
                    except NetworkError as e:
                        last_exc = e
                        log.warning("续期遇到网络问题（第 %d 次）: %s", attempt + 1, e)
                        await asyncio.sleep(min(2 ** attempt, 4) * self.retry_delay)
                    except CloudError as e:
                        last_exc = e
                        log.warning("续期失败: %s", e)
                        break
                # 失败收尾 —— 注意：无论哪种失败，凭据文件都不删除、不清空
                self._renew_fail += 1
                self._renew_next_allowed = time.time() + min(5 * 2 ** (self._renew_fail - 1), 600)
                if isinstance(last_exc, PassTokenInvalid) and invalid >= 2:
                    if not self.need_qr:
                        self._backup("needqr")
                        self.need_qr = True
                        self.creds["need_qr"] = True
                        self._save()
                    self._mark_error("登录已失效，需要重新扫码")
                    log.error("passToken 经两次确认已失效，状态置为「需要重新扫码」（旧凭据已备份保留）")
                    raise NeedLogin("需要重新扫码登录")
                if isinstance(last_exc, NetworkError):
                    self._mark_error(f"续期时网络异常: {last_exc}", network=True)
                else:
                    self._mark_error(f"续期失败: {last_exc}")
                raise last_exc  # type: ignore[misc]
            finally:
                self.renewing = False
                self._notify()

    # ------------------------------------------------------------------ 带鉴权的业务请求
    async def _service_request(self, sid: str, method: str, url: str, *, data=None, headers=None,
                               extra_cookies=None, timeout: float | None = None, label: str = "") -> dict:
        if not self.logged_in:
            raise NeedLogin("尚未登录")
        if self.need_qr:
            raise NeedLogin("需要重新扫码登录")
        for attempt in (0, 1):
            svc = self.creds["services"].get(sid)
            if not svc:
                await self.renew(sid)
                svc = self.creds["services"][sid]
            seen_ts = svc.get("gen", 0)
            cookies = {"userId": str(self.creds["userId"]), "serviceToken": svc["serviceToken"]}
            if extra_cookies:
                cookies.update(extra_cookies)
            body = data(svc) if callable(data) else data
            try:
                status, text = await self._http(method, url, cookies=cookies, headers=headers,
                                                data=body, timeout=timeout)
            except NetworkError as e:
                self._mark_error(f"{label or '请求'}: {e}", network=True)
                raise
            expired = status in (401, 403)
            resp = None
            if not expired:
                try:
                    resp = json.loads(text)
                except Exception:
                    if status == 200:
                        raise CloudError(f"{label} 返回无法解析") from None
                    raise CloudError(f"{label} HTTP {status}") from None
                code = resp.get("code", 0)
                if code == 0:
                    self._mark_ok()
                    return resp
                msg = str(resp.get("message", resp.get("desc", "")))
                low = msg.lower()
                if code in (401, 70016) or "auth" in low or "unauthorized" in low or "登录" in msg:
                    expired = True
                else:
                    self._mark_ok()       # 鉴权是通的，只是业务错误
                    raise CloudError(f"{label} 业务错误 code={code} {msg[:80]}")
            if attempt == 0:
                log.info("%s 鉴权失效，尝试静默续期", label or sid)
                await self.renew(sid, seen_ts=seen_ts)
                continue
            self._mark_error(f"{label} 续期后仍被拒绝")
            raise CloudError(f"{label} 续期后仍被拒绝")
        raise CloudError("unreachable")

    async def mina(self, uri: str, data: dict | None = None, timeout: float | None = None) -> dict:
        rid = "app_ios_" + _rand(30)
        if data is not None:
            data = dict(data, requestId=rid)
            method = "POST"
        else:
            uri += ("&" if "?" in uri else "?") + "requestId=" + rid
            method = "GET"
        return await self._service_request(
            MINA_SID, method, self.paths.mina_base + uri, data=data,
            headers={"User-Agent": MINA_UA}, timeout=timeout, label="mina" + uri.split("?")[0])

    async def miio(self, uri: str, data: dict) -> dict:
        def sign(svc):
            body = json.dumps(data, separators=(",", ":"))
            nonce = base64.b64encode(os.urandom(8) + int(time.time() / 60).to_bytes(4, "big")).decode()
            m = hashlib.sha256()
            m.update(base64.b64decode(svc["ssecurity"]))
            m.update(base64.b64decode(nonce))
            snonce = base64.b64encode(m.digest()).decode()
            msg = "&".join([uri, snonce, nonce, "data=" + body])
            sig = hmac.new(base64.b64decode(snonce), msg.encode(), hashlib.sha256).digest()
            return {"_nonce": nonce, "data": body, "signature": base64.b64encode(sig).decode()}
        resp = await self._service_request(
            MIIO_SID, "POST", self.paths.miio_base + uri, data=sign,
            headers={"User-Agent": MIIO_UA, "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2"},
            extra_cookies={"PassportDeviceId": self.creds["deviceId"]}, label="miio" + uri)
        return resp.get("result", {})

    # ------------------------------------------------------------------ 设备与播放器接口
    async def refresh_devices(self) -> list[dict]:
        resp = await self.mina("/admin/v2/device_list?master=0")
        out = []
        for h in resp.get("data") or []:
            if not h.get("deviceID"):
                continue
            out.append({"device_id": h["deviceID"], "did": str(h.get("miotDID", "")),
                        "name": h.get("alias") or h.get("name") or "小爱音箱",
                        "hardware": h.get("hardware", ""),
                        "online": h.get("presence", "online") == "online" and h["deviceID"] not in self.offline_local})
        self.devices = out
        if self.creds.get("devices_cache") != out:
            self.creds["devices_cache"] = out
            self._save()
        return out

    def device(self, device_id: str) -> dict | None:
        for d in self.devices:
            if d["device_id"] == device_id:
                return d
        return None

    async def ubus(self, device_id: str, method: str, path: str, message: dict, timeout: float | None = None) -> dict:
        try:
            resp = await self.mina("/remote/ubus", {"deviceId": device_id, "message": json.dumps(message),
                                                    "method": method, "path": path}, timeout=timeout)
        except (NetworkError, NeedLogin, TokenExpired, PassTokenInvalid):
            raise
        except CloudError as e:
            s = str(e)
            if "code=101 " in s or "code=2 " in s or "timed out" in s or "invalid result" in s:
                raise DeviceOffline(s) from None
            raise
        self.mark_presence(device_id, True)
        return resp.get("data") or {}

    def mark_presence(self, device_id: str, online: bool):
        """按实际通信结果修正在线状态（云端设备列表里的在线标记更新很慢）。"""
        if online:
            if device_id not in self.offline_local:
                return
            self.offline_local.discard(device_id)
        else:
            if device_id in self.offline_local:
                return
            self.offline_local.add(device_id)
        d = self.device(device_id)
        if d:
            d["online"] = online

    async def play_url(self, device_id: str, url: str) -> dict:
        hw = (self.device(device_id) or {}).get("hardware", "")
        api = self.settings["play_api"]
        use_music = api == "music" or (api == "auto" and hw in USE_PLAY_MUSIC)
        if use_music:
            audio_id = "1582971365183456177"
            music = {"payload": {"audio_type": "", "audio_items": [{
                "item_id": {"audio_id": audio_id, "cp": {"album_id": "-1", "episode_index": 0,
                                                         "id": "355454500", "name": "xiaowei"}},
                "stream": {"url": url}}],
                "list_params": {"listId": "-1", "loadmore_offset": 0, "origin": "xiaowei", "type": "MUSIC"}},
                "play_behavior": "REPLACE_ALL"}
            return await self.ubus(device_id, "player_play_music", "mediaplayer",
                                   {"startaudioid": audio_id, "music": json.dumps(music)})
        return await self.ubus(device_id, "player_play_url", "mediaplayer",
                               {"url": url, "type": 2, "media": "app_ios"})

    async def player_op(self, device_id: str, action: str) -> dict:
        return await self.ubus(device_id, "player_play_operation", "mediaplayer",
                               {"action": action, "media": "app_ios"})

    async def set_volume(self, device_id: str, volume: int) -> dict:
        return await self.ubus(device_id, "player_set_volume", "mediaplayer",
                               {"volume": int(volume), "media": "app_ios"})

    async def seek(self, device_id: str, position_ms: int) -> dict:
        # 接口名里的 positon 是小米原样的拼写
        return await self.ubus(device_id, "player_set_positon", "mediaplayer",
                               {"position": int(position_ms), "media": "app_ios"})

    async def set_loop(self, device_id: str, loop_type: int) -> dict:
        return await self.ubus(device_id, "player_set_loop", "mediaplayer",
                               {"media": "common", "type": loop_type})

    async def get_status(self, device_id: str, timeout: float = 8) -> dict:
        """返回 {status(0空闲/1播放/2暂停), volume, position(ms), duration(ms), loop_type}。"""
        data = await self.ubus(device_id, "player_get_play_status", "mediaplayer",
                               {"media": "app_ios"}, timeout=timeout)
        try:
            info = json.loads(data.get("info") or "{}")
        except Exception:
            info = {}
        detail = info.get("play_song_detail") or {}
        return {"status": int(info.get("status", 0) or 0), "volume": int(info.get("volume", 0) or 0),
                "position": int(detail.get("position", 0) or 0), "duration": int(detail.get("duration", 0) or 0),
                "loop_type": info.get("loop_type"), "has_detail": bool(detail),
                "audio_id": str(detail.get("audio_id") or ""), "detail_keys": sorted(detail.keys()),
                "info_keys": sorted(info.keys())}

    async def tts(self, device_id: str, text: str):
        dev = self.device(device_id) or {}
        mode = self.settings["tts_mode"]
        act = TTS_ACTION.get(dev.get("hardware", ""))
        if mode != "mina" and act and dev.get("did"):
            try:
                res = await self.miio("/miotspec/action", {"params": {
                    "did": dev["did"], "siid": act[0], "aiid": act[1], "in": [text.replace(" ", ",")]}})
                if isinstance(res, dict) and res.get("code", 0) == 0:
                    return
                log.warning("MIoT TTS 返回 code=%s，改用 mina TTS", res.get("code") if isinstance(res, dict) else res)
            except NeedLogin:
                raise
            except CloudError as e:
                log.warning("MIoT TTS 失败（%s），改用 mina TTS", e)
            if mode == "miot":
                raise CloudError("MIoT TTS 失败")
        await self.ubus(device_id, "text_to_speech", "mibrain", {"text": text})

    async def conversation(self, device_id: str, limit: int = 2) -> list[dict]:
        """拉取最近的对话记录，返回 [{time(ms), query, answer}]，新的在前。"""
        hw = (self.device(device_id) or {}).get("hardware", "")
        url = (f"{self.paths.profile_base}/device_profile/v2/conversation?source=dialogu"
               f"&hardware={parse.quote(hw)}&timestamp={int(time.time() * 1000)}&limit={limit}")
        resp = await self._service_request(
            MINA_SID, "GET", url, headers={"User-Agent": MINA_UA},
            extra_cookies={"deviceId": device_id}, timeout=8, label="conversation")
        d = resp.get("data")
        if not d:
            return []
        try:
            records = (json.loads(d) if isinstance(d, str) else d).get("records") or []
        except Exception:
            return []
        out = []
        for r in records:
            ans = ""
            answers = r.get("answers") or []
            if answers:
                ans = ((answers[0] or {}).get("tts") or {}).get("text", "") or ""
            out.append({"time": int(r.get("time", 0) or 0), "query": (r.get("query") or "").strip(), "answer": ans.strip()})
        return out

    # ------------------------------------------------------------------ 扫码登录
    async def start_qr(self) -> dict:
        if self._qr_task and not self._qr_task.done():
            self._qr_task.cancel()
        c = self.creds
        headers = {"User-Agent": c["ua"]}
        cookies = {"deviceId": c["deviceId"], "pass_o": c["pass_o"], "uLocale": "zh_CN"}
        try:
            status, text = await self._http(
                "GET", f"{self.paths.account_base}/pass/serviceLogin",
                params={"_json": "true", "sid": QR_SID, "_locale": "zh_CN"}, cookies=cookies, headers=headers)
            loc = _strip_start(text).get("location", "")
            q = {k: v[0] for k, v in parse.parse_qs(parse.urlparse(loc).query).items()}
            q.update({"theme": "", "bizDeviceType": "", "_hasLogo": "false", "_qrsize": "240",
                      "_dc": str(int(time.time() * 1000))})
            status, text = await self._http("GET", f"{self.paths.account_base}/longPolling/loginUrl",
                                            params=q, headers=headers)
            d = _strip_start(text)
            if d.get("code", 0) != 0 or not d.get("loginUrl") or not d.get("lp"):
                raise CloudError(f"获取二维码失败 code={d.get('code')}")
        except NetworkError as e:
            self.qr = {"state": "error", "error": f"网络异常：{e}"}
            return self.qr_public()
        except Exception as e:
            self.qr = {"state": "error", "error": f"获取二维码失败：{type(e).__name__}"}
            log.warning("获取二维码失败: %s", e)
            return self.qr_public()
        import io
        import segno
        buf = io.BytesIO()
        # 用 PNG（data URI）而不是内嵌 SVG：内嵌 SVG 会继承页面图标的描边样式，二维码会糊成一团
        segno.make(d["loginUrl"], error="m").save(buf, kind="png", scale=8, border=4, dark="#000", light="#fff")
        import base64
        self.qr = {"state": "pending", "png": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
                   "expires_at": int(time.time()) + 170}
        self._qr_task = asyncio.create_task(self._qr_wait(d["lp"]))
        return self.qr_public()

    def qr_public(self) -> dict:
        return {k: v for k, v in self.qr.items() if k in ("state", "png", "error", "expires_at")}

    async def _qr_wait(self, lp_url: str):
        deadline = time.time() + 170
        headers = {"User-Agent": self.creds["ua"]}
        data = None
        while time.time() < deadline:
            try:
                status, text = await self._http("GET", lp_url, headers=headers,
                                                timeout=max(5, deadline - time.time()))
                d = _strip_start(text)
                if d.get("code", 0) == 0 and d.get("passToken"):
                    data = d
                    break
                if d.get("code") in (700, 702):     # 仍在等待/超时，继续
                    await asyncio.sleep(1)
                    continue
                self.qr = {"state": "error", "error": f"扫码未完成 code={d.get('code')}"}
                self._notify()
                return
            except NetworkError:
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.qr = {"state": "error", "error": f"登录结果解析失败：{type(e).__name__}"}
                self._notify()
                return
        if not data:
            self.qr = {"state": "expired"}
            self._notify()
            return
        # 新凭据写入前，先备份旧凭据
        if self.creds.get("passToken"):
            self._backup("relogin")
        self.creds["passToken"] = data["passToken"]
        self.creds["userId"] = str(data["userId"])
        if data.get("cUserId"):
            self.creds["cUserId"] = data["cUserId"]
        self.creds["services"] = {}
        self.creds["login_at"] = int(time.time())
        self.creds.pop("need_qr", None)
        self.need_qr = False
        self._renew_fail = 0
        self._renew_next_allowed = 0
        self._save()
        log.info("扫码登录成功，开始换取服务凭据")
        try:
            await self.renew(MINA_SID, force=True)
            await self.refresh_devices()
            self.qr = {"state": "ok"}
        except Exception as e:
            self.qr = {"state": "ok", "error": f"已登录，但初始化未完成：{e}"}
            log.warning("扫码后初始化未完成: %s", e)
        self._notify()
        if self.on_login:
            try:
                self.on_login()
            except Exception:
                log.exception("on_login 回调异常")
        self._wake.set()

    async def logout(self):
        """用户主动退出：凭据备份后移除登录字段（deviceId/UA 保留）。"""
        self._backup("logout")
        for k in ("passToken", "userId", "cUserId", "need_qr", "devices_cache", "login_at"):
            self.creds.pop(k, None)
        self.creds["services"] = {}
        self.devices = []
        self.need_qr = False
        self.last_ok = 0
        self._save()
        self._notify()

    # ------------------------------------------------------------------ 保活
    def start_keepalive(self):
        self._keepalive_task = asyncio.create_task(self._keepalive())

    def wake(self):
        self._wake.set()

    async def check_now(self) -> None:
        """立即校验一次会话（用户点「重试」时调用）。"""
        self._renew_next_allowed = 0
        if self.need_qr:
            await self.renew(MINA_SID, force=True)
        await self.refresh_devices()

    async def _keepalive(self):
        fail_wait = 5.0
        last_probe = 0.0
        first = True
        while True:
            try:
                interval = float(self.settings["keepalive_min"]) * 60
                if first:
                    wait, first = 1.0, False
                elif self.logged_in and not self.need_qr and (self.net_error or not self.last_ok):
                    wait = fail_wait          # 尚未连通：退避重试（5s 起，最长 5 分钟）
                else:
                    wait = interval
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=wait)
                except asyncio.TimeoutError:
                    pass
                if not self.logged_in:
                    continue
                if self.need_qr:
                    # 已判定失效：每 6 小时低频复核一次，防止误判后永远卡住
                    if time.time() - last_probe > 6 * 3600:
                        last_probe = time.time()
                        try:
                            await self.renew(MINA_SID, force=True)
                        except CloudError:
                            pass
                    continue
                svc = self.creds["services"].get(MINA_SID)
                age_h = (time.time() - svc.get("ts", 0)) / 3600 if svc else 1e9
                if svc and age_h >= float(self.settings["renew_hours"]):
                    # 提前续期；失败不影响仍然有效的旧 token
                    try:
                        await self.renew(MINA_SID, seen_ts=svc.get("gen", 0))
                    except CloudError as e:
                        log.warning("提前续期未成功（旧凭据继续使用）: %s", e)
                await self.refresh_devices()      # 校验会话；失效会自动静默续期
                fail_wait = 5.0
            except asyncio.CancelledError:
                raise
            except NetworkError as e:
                fail_wait = min(fail_wait * 2, 300)
                log.warning("保活检测：网络异常（凭据保留，%d 秒后重试）: %s", int(fail_wait), e)
            except NeedLogin:
                pass
            except Exception as e:
                fail_wait = min(fail_wait * 2, 300)
                log.warning("保活检测失败: %s", e)


def _mask(s: str) -> str:
    return s[:2] + "***" + s[-2:] if len(s) > 4 else ("***" if s else "")
