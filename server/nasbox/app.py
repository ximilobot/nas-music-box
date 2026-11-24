"""aiohttp 应用：API、SSE 推送、静态页面。"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import logging
import os
import random
import time
import zipfile
from urllib.parse import urlparse

from aiohttp import web

from . import __version__
from .icons import ICONS
from .config import Paths, Settings, verify_password
from .library import Library
from .media import Media
from .micloud import CloudError, MiCloud, NeedLogin, NetworkError
from .player import MODES, PlayerManager
from .util import is_private_ip, redact, setup_logging
from .voice import VoiceService

log = logging.getLogger("nasbox.app")
STATIC = os.path.join(os.path.dirname(__file__), "static")
SESSION_DAYS = 90


def jerr(msg: str, status: int = 400):
    return web.json_response({"ok": False, "error": msg}, status=status)


def jok(**kw):
    return web.json_response({"ok": True, **kw})


class Hub:
    """SSE 推送：状态变化后合并 300ms 再发。"""
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.clients: set[asyncio.Queue] = set()
        self._dirty = asyncio.Event()

    def publish(self):
        self._dirty.set()

    async def run(self):
        while True:
            await self._dirty.wait()
            await asyncio.sleep(0.3)
            self._dirty.clear()
            if not self.clients:
                continue
            try:
                data = json.dumps(self.snapshot(), ensure_ascii=False)
            except Exception:
                log.exception("生成状态快照失败")
                continue
            for q in list(self.clients):
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(data)


class App:
    def __init__(self, paths: Paths | None = None):
        self.paths = paths or Paths()
        os.makedirs(self.paths.data, exist_ok=True)
        self.settings = Settings(self.paths)
        self.log_path = setup_logging(self.paths.data, self.settings["log_level"])
        self.lib = Library(self.paths)
        self.cloud = MiCloud(self.paths, self.settings)
        self.media = Media(self.paths, self.settings, self.lib)
        self.players = PlayerManager(self.cloud, self.lib, self.settings, self.media, self.paths)
        self.voice = VoiceService(self.cloud, self.lib, self.players, self.settings)
        self.hub = Hub(self.status)
        self.cloud.on_change = self.hub.publish
        self.players.on_change = self.hub.publish
        self.lib.on_change = self.hub.publish
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------ 状态
    def status(self) -> dict:
        try:
            player = self.players.get().snapshot()
        except ValueError:
            player = None
        base, note = self.media.base_url()
        return {"app": "nas-music-box", "name": "NAS 音乐盒", "need_login": False, "authed": True,
                "version": __version__, "auth": self.cloud.status(), "devices": self.cloud.devices,
                "current_device": self.players.current_device_id(), "player": player,
                "library": {"count": self.lib.count(), "scanning": self.lib.scanning, "progress": self.lib.scan_progress,
                            "music_dir_ok": os.path.isdir(self.paths.music)},
                "voice": self.voice.status(), "base_url": base, "base_url_note": note,
                "password_set": bool(self.settings["password_hash"]), "ts": time.time()}

    # ------------------------------------------------------------------ 访问密码
    def _session_token(self, exp: int) -> str:
        sig = hmac.new(self.settings.secret, f"sess:{exp}:{self.settings['password_hash']}".encode(), hashlib.sha256).hexdigest()
        return f"{exp}.{sig}"

    def _authed(self, request: web.Request) -> bool:
        if not self.settings["password_hash"]:
            return True
        tok = request.cookies.get("nasbox_session", "")
        try:
            exp_s, _ = tok.split(".", 1)
            exp = int(exp_s)
        except ValueError:
            return False
        return exp > time.time() and hmac.compare_digest(tok, self._session_token(exp))

    @web.middleware
    async def middleware(self, request: web.Request, handler):
        path = request.path
        if path.startswith("/api/") and path not in ("/api/login", "/api/auth_info", "/api/status") and not self._authed(request):
            return jerr("需要登录", 401)
        try:
            resp = await handler(request)
        except web.HTTPException:
            raise
        except NeedLogin as e:
            return jerr(str(e), 409)
        except NetworkError as e:
            return jerr(f"连接小米云端失败：{e}", 502)
        except CloudError as e:
            return jerr(f"小米云端返回错误：{e}", 502)
        except ValueError as e:
            return jerr(str(e), 400)
        except (ConnectionResetError, asyncio.CancelledError):
            raise
        except Exception:
            log.exception("处理 %s 出错", path)
            return jerr("服务器内部错误，详见日志", 500)
        if path.startswith("/api/") or path in ("/", "/index.html", "/sw.js"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    # ------------------------------------------------------------------ 路由
    def build(self) -> web.Application:
        app = web.Application(middlewares=[self.middleware], client_max_size=1024 * 1024)
        r = app.router
        r.add_get("/", self.h_index)
        r.add_get("/media/{token}/{name}", self.media.handle)
        r.add_get("/api/auth_info", self.h_auth_info)
        r.add_post("/api/login", self.h_login)
        r.add_post("/api/logout", self.h_logout)
        r.add_get("/api/status", self.h_status)
        r.add_get("/api/events", self.h_events)
        r.add_get("/api/settings", self.h_settings_get)
        r.add_post("/api/settings", self.h_settings_post)
        r.add_post("/api/mi/qr/start", self.h_qr_start)
        r.add_get("/api/mi/qr/status", self.h_qr_status)
        r.add_post("/api/mi/check", self.h_mi_check)
        r.add_post("/api/mi/logout", self.h_mi_logout)
        r.add_post("/api/device/select", self.h_device_select)
        r.add_get("/api/songs", self.h_songs)
        r.add_get("/api/search", self.h_search)
        r.add_get("/api/folders", self.h_folders)
        r.add_get("/api/playlists", self.h_playlists)
        r.add_post("/api/playlists", self.h_playlist_create)
        r.add_get("/api/playlists/{pid}", self.h_playlist_get)
        r.add_post("/api/playlists/{pid}", self.h_playlist_edit)
        r.add_delete("/api/playlists/{pid}", self.h_playlist_delete)
        r.add_get("/api/favorites", self.h_favorites)
        r.add_post("/api/favorites", self.h_favorite_set)
        r.add_post("/api/scan", self.h_scan)
        r.add_post("/api/play", self.h_play)
        r.add_post("/api/control", self.h_control)
        r.add_get("/api/queue", self.h_queue)
        r.add_post("/api/queue", self.h_queue_edit)
        r.add_post("/api/tts", self.h_tts)
        r.add_post("/api/sleep", self.h_sleep)
        r.add_post("/api/voice/test", self.h_voice_test)
        r.add_get("/api/cover/{sid}", self.h_cover)
        r.add_get("/api/logs", self.h_logs)
        r.add_get("/api/logs/download", self.h_logs_download)
        r.add_get("/icon-192.png", self.h_icon)
        r.add_get("/icon-512.png", self.h_icon)
        r.add_static("/", STATIC, show_index=False)
        app.on_startup.append(self.on_startup)
        app.on_cleanup.append(self.on_cleanup)
        return app

    async def on_startup(self, app):
        log.info("NAS 音乐盒 %s 启动，曲库目录 %s，数据目录 %s，端口 %d", __version__, self.paths.music, self.paths.data, self.paths.port)
        base, note = self.media.base_url()
        log.info("音箱拉流基地址：%s（%s）", base, note)
        self._tasks.append(asyncio.create_task(self.hub.run()))
        self._tasks.append(asyncio.create_task(self.lib.scan()))
        self._tasks.append(asyncio.create_task(self._ticker()))
        self.cloud.start_keepalive()
        self.voice.start()

    async def on_cleanup(self, app):
        for t in self._tasks + [self.voice._task]:
            if t:
                t.cancel()
        self.players.save_state()
        await self.cloud.close()

    async def _ticker(self):
        """有界面连接且在播放时，每 5 秒推一次，纠正前端的本地进度推算。"""
        while True:
            await asyncio.sleep(5)
            if self.hub.clients and self.players.any_active():
                self.hub.publish()

    # ------------------------------------------------------------------ 基础 handlers
    async def h_index(self, request):
        return web.FileResponse(os.path.join(STATIC, "index.html"))

    async def h_auth_info(self, request):
        return jok(password_set=bool(self.settings["password_hash"]), authed=self._authed(request), version=__version__)

    async def h_login(self, request):
        body = await self._json(request)
        await asyncio.sleep(0.5)          # 减缓暴力尝试
        if not self.settings["password_hash"] or verify_password(str(body.get("password", "")), self.settings["password_hash"]):
            exp = int(time.time()) + SESSION_DAYS * 86400
            resp = jok()
            resp.set_cookie("nasbox_session", self._session_token(exp), max_age=SESSION_DAYS * 86400, httponly=True, samesite="Lax")
            return resp
        return jerr("密码不正确", 403)

    async def h_logout(self, request):
        resp = jok()
        resp.del_cookie("nasbox_session")
        return resp

    async def _json(self, request) -> dict:
        try:
            d = await request.json()
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    async def h_status(self, request):
        if not self._authed(request):
            # 免登录也返回 200 + JSON（安卓 App 靠它识别服务端），但只给不敏感的字段
            a = self.cloud.status()
            return web.json_response({"app": "nas-music-box", "name": "NAS 音乐盒", "version": __version__,
                                      "need_login": True, "authed": False,
                                      "auth": {"state": a["state"], "label": a["label"]}})
        self._learn_base(request)
        if request.query.get("refresh"):
            try:
                asyncio.create_task(self.players.get().refresh_status())
            except ValueError:
                pass
        return web.json_response(self.status())

    def _learn_base(self, request):
        """浏览器是用局域网 IP 打开页面的 → 这个地址音箱大概率也能访问，记下来当默认拉流地址。"""
        if self.settings["base_url"] or os.environ.get("NASBOX_BASE_URL"):
            return
        origin = request.headers.get("Origin") or request.headers.get("Referer") or f"http://{request.host}"
        try:
            u = urlparse(origin if "://" in origin else "http://" + origin)
        except ValueError:
            return
        if u.scheme == "http" and u.hostname and is_private_ip(u.hostname) and u.port:
            learned = f"http://{u.hostname}:{u.port}"
            # 只认直连本服务端口的访问（经反向代理的端口对音箱没意义）
            if u.port == self.paths.port and learned != self.settings["learned_base_url"]:
                self.settings.data["learned_base_url"] = learned
                self.settings.save()
                log.info("根据浏览器访问地址识别到拉流基地址：%s", learned)

    async def h_events(self, request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                           "X-Accel-Buffering": "no"})
        await resp.prepare(request)
        q: asyncio.Queue = asyncio.Queue(maxsize=5)
        self.hub.clients.add(q)
        try:
            await resp.write(f"data: {json.dumps(self.status(), ensure_ascii=False)}\n\n".encode())
            try:
                asyncio.create_task(self.players.get().refresh_status())
            except ValueError:
                pass
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=20)
                    await resp.write(f"data: {data}\n\n".encode())
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
        except (ConnectionResetError, asyncio.CancelledError, ConnectionError):
            pass
        finally:
            self.hub.clients.discard(q)
        return resp

    async def h_settings_get(self, request):
        base, note = self.media.base_url()
        return jok(settings=self.settings.public(), base_url_effective=base, base_url_note=note,
                   origin_hint=f"http://{request.host}")

    async def h_settings_post(self, request):
        body = await self._json(request)
        changed = self.settings.update(body)
        if "log_level" in changed:
            logging.getLogger().setLevel(getattr(logging, self.settings["log_level"].upper(), logging.INFO))
        if "keepalive_min" in changed or "renew_hours" in changed:
            self.cloud.wake()
        self.hub.publish()
        resp = jok(changed=changed, settings=self.settings.public())
        if "password_hash" in changed and self.settings["password_hash"]:
            exp = int(time.time()) + SESSION_DAYS * 86400     # 设密码的人自动保持登录
            resp.set_cookie("nasbox_session", self._session_token(exp), max_age=SESSION_DAYS * 86400, httponly=True, samesite="Lax")
        return resp

    # ------------------------------------------------------------------ 小米账号
    async def h_qr_start(self, request):
        return jok(qr=await self.cloud.start_qr())

    async def h_qr_status(self, request):
        return jok(qr=self.cloud.qr_public(), auth=self.cloud.status())

    async def h_mi_check(self, request):
        await self.cloud.check_now()
        self.hub.publish()
        return jok(auth=self.cloud.status(), devices=self.cloud.devices)

    async def h_mi_logout(self, request):
        for p in self.players.players.values():
            if p.state != "idle":
                await p.stop(reason="user")
        await self.cloud.logout()
        return jok()

    async def h_device_select(self, request):
        body = await self._json(request)
        did = str(body.get("device_id", ""))
        if not self.cloud.device(did):
            return jerr("找不到这台音箱")
        self.settings.update({"current_device": did})
        self.hub.publish()
        return jok()

    # ------------------------------------------------------------------ 曲库
    async def h_songs(self, request):
        q = request.query
        d = q.get("dir")
        res = self.lib.list_songs(dir=d, offset=int(q.get("offset", 0)), limit=min(int(q.get("limit", 200)), 1000),
                                  sort=q.get("sort", "title"))
        return jok(**res)

    async def h_search(self, request):
        return jok(songs=self.lib.search(request.query.get("q", ""), limit=100))

    async def h_folders(self, request):
        return jok(folders=self.lib.folders())

    async def h_playlists(self, request):
        return jok(playlists=self.lib.playlists(), favorites=len(self.lib.favorite_ids()))

    async def h_playlist_create(self, request):
        body = await self._json(request)
        pid = self.lib.playlist_create(str(body.get("name", "")))
        if body.get("song_ids"):
            self.lib.playlist_add(pid, [int(x) for x in body["song_ids"]])
        return jok(id=pid)

    async def h_playlist_get(self, request):
        pid = int(request.match_info["pid"])
        return jok(songs=self.lib.songs_by_ids(self.lib.playlist_ids(pid)))

    async def h_playlist_edit(self, request):
        pid = int(request.match_info["pid"])
        body = await self._json(request)
        if body.get("name"):
            self.lib.playlist_rename(pid, str(body["name"]))
        if body.get("add"):
            self.lib.playlist_add(pid, [int(x) for x in body["add"]])
        if body.get("remove"):
            self.lib.playlist_remove(pid, [int(x) for x in body["remove"]])
        return jok()

    async def h_playlist_delete(self, request):
        self.lib.playlist_delete(int(request.match_info["pid"]))
        return jok()

    async def h_favorites(self, request):
        return jok(songs=self.lib.songs_by_ids(self.lib.favorite_ids()))

    async def h_favorite_set(self, request):
        body = await self._json(request)
        sid = int(body.get("song_id", 0))
        self.lib.set_favorite(sid, bool(body.get("on", True)))
        for p in self.players.players.values():
            if p.song and p.song["id"] == sid:
                p.song = self.lib.song(sid) or p.song
        self.hub.publish()
        return jok()

    async def h_scan(self, request):
        body = await self._json(request)
        if not self.lib.scanning:
            self._tasks.append(asyncio.create_task(self.lib.scan(full=bool(body.get("full")))))
        return jok()

    async def h_cover(self, request):
        try:
            res = await asyncio.get_running_loop().run_in_executor(None, self.lib.cover, int(request.match_info["sid"]))
        except Exception:
            res = None
        if not res:
            raise web.HTTPNotFound()
        return web.FileResponse(res[0], headers={"Content-Type": res[1], "Cache-Control": "max-age=86400"})

    # ------------------------------------------------------------------ 播放
    def _resolve_source(self, src: dict) -> tuple[list[int], str]:
        kind = src.get("type", "all")
        if kind == "all":
            return self.lib.all_ids(), "全部歌曲"
        if kind == "folder":
            d = str(src.get("id", ""))
            return self.lib.folder_ids(d), f"文件夹：{os.path.basename(d) or '根目录'}"
        if kind == "playlist":
            pid = int(src.get("id", 0))
            name = next((p["name"] for p in self.lib.playlists() if p["id"] == pid), "")
            return self.lib.playlist_ids(pid), f"歌单：{name}"
        if kind == "favorites":
            return self.lib.favorite_ids(), "收藏"
        if kind == "search":
            q = str(src.get("q", ""))
            return [s["id"] for s in self.lib.search(q, 100)], f"搜索：{q}"
        if kind == "ids":
            return [int(x) for x in src.get("ids", [])], str(src.get("label", "自选"))
        raise ValueError("未知的播放来源")

    async def h_play(self, request):
        body = await self._json(request)
        player = self.players.get(body.get("device_id") or None)
        ids, label = self._resolve_source(body.get("source") or {"type": "all"})
        sid = body.get("song_id")
        if sid is not None:
            sid = int(sid)
            if sid not in ids:
                ids = [sid] + ids
            idx = ids.index(sid)
        elif body.get("shuffle") or self.settings["play_mode"] == "random":
            idx = random.randrange(len(ids)) if ids else 0
        else:
            idx = 0
        if not ids:
            return jerr("这里还没有歌曲")
        await player.play_queue(ids, idx, label)
        return jok(player=player.snapshot())

    async def h_control(self, request):
        body = await self._json(request)
        player = self.players.get(body.get("device_id") or None)
        act = body.get("action")
        val = body.get("value")
        if act == "pause":
            await player.pause()
        elif act == "resume":
            await player.resume()
        elif act == "toggle":
            await player.toggle()
        elif act == "stop":
            await player.stop(reason="user")
        elif act == "next":
            await player.next()
        elif act == "prev":
            await player.prev()
        elif act == "seek":
            await player.seek(float(val))
        elif act == "volume":
            await player.set_volume(int(val))
        elif act == "volume_step":
            await player.volume_step(int(val))
        elif act == "mode":
            if val not in MODES:
                return jerr("未知的播放模式")
            self.settings.update({"play_mode": val})
        elif act == "play_index":
            await player.play_index(int(val))
        else:
            return jerr("未知的操作")
        self.hub.publish()
        return jok(player=player.snapshot())

    async def h_queue(self, request):
        player = self.players.get(request.query.get("device_id") or None)
        off = max(0, int(request.query.get("offset", max(0, player.index - 20))))
        ids = player.queue[off:off + 200]
        return jok(index=player.index, total=len(player.queue), offset=off, songs=self.lib.songs_by_ids(ids), source=player.source)

    async def h_queue_edit(self, request):
        body = await self._json(request)
        player = self.players.get(body.get("device_id") or None)
        if body.get("add"):
            ids = [int(x) for x in body["add"]]
            if player.state == "idle" and not player.queue:
                await player.play_queue(ids, 0, "自选")
            else:
                player.enqueue(ids, play_next=bool(body.get("next")))
        if body.get("remove_pos") is not None:
            player.remove_from_queue(int(body["remove_pos"]))
        return jok(total=len(player.queue))

    async def h_tts(self, request):
        body = await self._json(request)
        text = str(body.get("text", "")).strip()[:300]
        if not text:
            return jerr("请输入要播报的文字")
        player = self.players.get(body.get("device_id") or None)
        await self.cloud.tts(player.device_id, text)
        return jok()

    async def h_sleep(self, request):
        body = await self._json(request)
        player = self.players.get(body.get("device_id") or None)
        player.set_sleep(float(body.get("minutes", 0)))
        return jok(player=player.snapshot())

    async def h_voice_test(self, request):
        """在界面里模拟一句语音口令，方便调试关键词。dry=true 只解析不执行。"""
        from .voice import parse_command
        body = await self._json(request)
        text = str(body.get("text", ""))
        parsed = parse_command(text, self.settings["keywords"])
        if body.get("dry", True) or not parsed:
            return jok(parsed=parsed)
        result = await self.voice.handle(self.players.get().device_id, text)
        return jok(parsed=parsed, result=result)

    # ------------------------------------------------------------------ 日志
    def _read_logs(self) -> list[tuple[str, str]]:
        out = []
        d = os.path.dirname(self.log_path)
        for fn in sorted(os.listdir(d)):
            if fn.startswith("nasbox.log"):
                try:
                    with open(os.path.join(d, fn), encoding="utf-8", errors="replace") as f:
                        out.append((fn, redact(f.read())))     # 写入时已脱敏，导出时再脱敏一遍
                except OSError:
                    pass
        return out

    async def h_logs(self, request):
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 60000))
                text = f.read()
        except OSError:
            text = ""
        lines = redact(text).splitlines()[-int(request.query.get("lines", 200)):]
        return jok(lines=lines)

    async def h_icon(self, request):
        return web.Response(body=ICONS[request.path.rsplit("/", 1)[-1]], content_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})

    async def h_logs_download(self, request):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for fn, text in self._read_logs():
                z.writestr(fn + ".txt", text)
            st = self.status()
            st["devices"] = [{"name": d.get("name"), "hardware": d.get("hardware"), "online": d.get("online")} for d in st["devices"]]
            st["current_device"] = "(已隐藏)"
            if st.get("player"):
                st["player"]["device_id"] = "(已隐藏)"
            z.writestr("status.json", redact(json.dumps(st, ensure_ascii=False, indent=2)))
            pub = dict(self.settings.public())
            if pub.get("current_device"):
                pub["current_device"] = "(已隐藏)"
            z.writestr("settings.json", json.dumps(pub, ensure_ascii=False, indent=2))
        name = time.strftime("nasbox-logs-%Y%m%d-%H%M%S.zip")
        return web.Response(body=buf.getvalue(), headers={
            "Content-Type": "application/zip", "Content-Disposition": f'attachment; filename="{name}"'})


def main():
    paths = Paths()
    app = App(paths)
    web.run_app(app.build(), host=paths.host, port=paths.port, access_log=None, print=None)


if __name__ == "__main__":
    main()
