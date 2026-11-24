"""媒体流：签名 URL、HTTP Range、按需 ffmpeg 转 mp3（带缓存，可边转边播）。"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time

from aiohttp import web

from .util import detect_lan_ip, looks_like_docker_ip

log = logging.getLogger("nasbox.media")

MIME = {"mp3": "audio/mpeg", "flac": "audio/flac", "wav": "audio/wav", "ape": "audio/x-ape",
        "ogg": "audio/ogg", "m4a": "audio/mp4", "wma": "audio/x-ms-wma"}


class Job:
    def __init__(self, tmp: str, final: str):
        self.tmp, self.final = tmp, final
        self.done = asyncio.Event()
        self.ok = False


class Media:
    def __init__(self, paths, settings, lib):
        self.paths, self.settings, self.lib = paths, settings, lib
        self.tdir = os.path.join(paths.cache_dir, "transcode")
        os.makedirs(self.tdir, exist_ok=True)
        for f in os.listdir(self.tdir):           # 清理上次异常退出留下的半成品
            if f.endswith(".part"):
                try:
                    os.unlink(os.path.join(self.tdir, f))
                except OSError:
                    pass
        self.jobs: dict[str, Job] = {}
        self._detected = None
        self.on_fetch = None          # 回调(song_id, 是否从头拉流)，播放器用它识别音箱自行循环

    # ---------------------------------------------------------------- 地址
    def base_url(self) -> tuple[str, str]:
        """返回 (基地址, 来源说明)。"""
        s = self.settings
        if s["base_url"]:
            return s["base_url"], "手动设置"
        env = os.environ.get("NASBOX_BASE_URL", "").strip().rstrip("/")
        if env:
            return env, "环境变量 NASBOX_BASE_URL"
        if s["learned_base_url"]:
            return s["learned_base_url"], "根据浏览器访问地址自动识别"
        if self._detected is None:
            self._detected = detect_lan_ip()
        if self._detected:
            note = "自动探测"
            if looks_like_docker_ip(self._detected):
                note = "自动探测（疑似 Docker 内部地址，音箱可能访问不到，请手动填写 NAS 的局域网地址）"
            return f"http://{self._detected}:{self.paths.port}", note
        return f"http://127.0.0.1:{self.paths.port}", "探测失败，请手动填写"

    def sign(self, sid: int) -> str:
        return hmac.new(self.settings.secret, f"media:{sid}".encode(), hashlib.sha256).hexdigest()[:32]

    def need_transcode(self, song: dict) -> bool:
        mode = self.settings["transcode"]
        ext = (song.get("ext") or "").lower()
        if mode == "off" or ext == "mp3":
            return False
        if mode == "all":
            return True
        return ext in [x.lower() for x in self.settings["transcode_formats"]]

    def url_for(self, song: dict) -> str:
        ext = "mp3" if self.need_transcode(song) else song["ext"]
        return f"{self.base_url()[0]}/media/{self.sign(song['id'])}/{song['id']}.{ext}"

    # ---------------------------------------------------------------- 转码
    def _cache_path(self, song: dict) -> str:
        full = self.lib.full_path(song["path"])
        try:
            mt = int(os.stat(full).st_mtime)
        except OSError:
            mt = 0
        key = hashlib.sha1(f"{song['path']}|{mt}|{self.settings['transcode_bitrate']}".encode()).hexdigest()[:24]
        return os.path.join(self.tdir, key + ".mp3")

    def _start_job(self, song: dict) -> Job:
        final = self._cache_path(song)
        job = self.jobs.get(final)
        if job:
            return job
        job = Job(final + ".part", final)
        self.jobs[final] = job
        asyncio.create_task(self._run_job(job, self.lib.full_path(song["path"]), song.get("title", "")))
        return job

    async def _run_job(self, job: Job, src: str, title: str):
        t0 = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                self.paths.ffmpeg, "-v", "error", "-nostdin", "-y", "-i", src, "-vn", "-map_metadata", "-1",
                "-c:a", "libmp3lame", "-b:a", str(self.settings["transcode_bitrate"]), "-f", "mp3", job.tmp,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            try:
                _, err = await asyncio.wait_for(proc.communicate(), timeout=1800)
            except asyncio.TimeoutError:
                proc.kill()
                err = b"timeout"
            if proc.returncode == 0 and os.path.getsize(job.tmp) > 0:
                os.replace(job.tmp, job.final)
                job.ok = True
                log.info("转码完成：%s（%.1fs）", title, time.time() - t0)
            else:
                log.warning("转码失败：%s：%s", title, err.decode(errors="replace")[-300:])
        except FileNotFoundError:
            log.error("找不到 ffmpeg，无法转码")
        except Exception:
            log.exception("转码异常")
        finally:
            if not job.ok:
                try:
                    os.unlink(job.tmp)
                except OSError:
                    pass
            job.done.set()
            self.jobs.pop(job.final, None)
            self._evict()

    def _evict(self):
        limit = int(self.settings["cache_max_mb"]) * 1024 * 1024
        try:
            files = [(e.stat().st_atime, e.stat().st_size, e.path) for e in os.scandir(self.tdir) if e.name.endswith(".mp3")]
        except OSError:
            return
        total = sum(f[1] for f in files)
        for _, size, path in sorted(files):
            if total <= limit:
                break
            try:
                os.unlink(path)
                total -= size
            except OSError:
                pass

    def prewarm(self, song: dict | None):
        """提前把下一首转好，切歌时音箱直接拿到带 Range 的缓存文件。"""
        if song and self.need_transcode(song) and not os.path.isfile(self._cache_path(song)):
            try:
                self._start_job(song)
            except Exception:
                log.exception("预转码启动失败")

    # ---------------------------------------------------------------- HTTP
    async def handle(self, request: web.Request) -> web.StreamResponse:
        token = request.match_info["token"]
        name = request.match_info["name"]
        try:
            sid_s, ext = name.rsplit(".", 1)
            sid = int(sid_s)
        except ValueError:
            raise web.HTTPNotFound()
        if not hmac.compare_digest(token, self.sign(sid)):
            raise web.HTTPForbidden()
        song = self.lib.song(sid)
        if not song:
            raise web.HTTPNotFound()
        full = self.lib.full_path(song["path"])
        if not os.path.isfile(full):
            raise web.HTTPNotFound()
        log.info("拉流 %s %s《%s》Range=%s 来自 %s", request.method, ext, song["title"],
                 request.headers.get("Range", "-"), request.remote)
        if self.on_fetch and request.method == "GET":
            rng = request.headers.get("Range", "").replace(" ", "")
            try:
                self.on_fetch(sid, rng in ("", "bytes=0-") or rng.startswith("bytes=0-"))
            except Exception:
                log.exception("拉流回调异常")
        if ext.lower() == "mp3" and song["ext"] != "mp3":
            return await self._serve_transcoded(request, song)
        # aiohttp 的 FileResponse 自带 Range / If-Range / 206 / 416 处理
        return web.FileResponse(full, headers={"Content-Type": MIME.get(song["ext"], "application/octet-stream"),
                                               "Accept-Ranges": "bytes", "Cache-Control": "no-cache"})

    async def _serve_transcoded(self, request: web.Request, song: dict) -> web.StreamResponse:
        hdr = {"Content-Type": "audio/mpeg", "Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
        final = self._cache_path(song)
        if os.path.isfile(final):
            os.utime(final)
            return web.FileResponse(final, headers=hdr)
        job = self._start_job(song)
        rng = request.headers.get("Range", "")
        if request.method == "HEAD" or (rng and not rng.replace(" ", "").startswith("bytes=0-")):
            # 需要总长度或中间位置：等转完再按普通文件处理
            await job.done.wait()
            if not job.ok:
                raise web.HTTPInternalServerError(text="transcode failed")
            return web.FileResponse(final, headers=hdr)
        # 边转边发：追着正在增长的临时文件读
        resp: web.StreamResponse | None = None
        f = None
        try:
            while True:
                if f is None:
                    if os.path.isfile(job.tmp):
                        f = open(job.tmp, "rb")
                    elif os.path.isfile(final):
                        f = open(final, "rb")
                    elif job.done.is_set():
                        raise web.HTTPInternalServerError(text="transcode failed")
                    else:
                        await asyncio.sleep(0.05)
                        continue
                chunk = f.read(65536)
                if chunk:
                    if resp is None:
                        resp = web.StreamResponse(status=200, headers={"Content-Type": "audio/mpeg", "Cache-Control": "no-cache"})
                        await resp.prepare(request)
                    await resp.write(chunk)
                    continue
                if job.done.is_set():
                    chunk = f.read(65536)      # 收尾：rename 之后同一 fd 仍可读完
                    if chunk:
                        if resp is None:
                            resp = web.StreamResponse(status=200, headers={"Content-Type": "audio/mpeg"})
                            await resp.prepare(request)
                        await resp.write(chunk)
                        continue
                    break
                await asyncio.sleep(0.1)
            if resp is None:
                raise web.HTTPInternalServerError(text="transcode failed")
            await resp.write_eof()
            return resp
        except (ConnectionResetError, asyncio.CancelledError):
            raise
        finally:
            if f:
                f.close()
