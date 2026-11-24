"""播放器状态机。

切歌不靠盲目定时器：
- 下发播放后进入 starting，轮询音箱 player_get_play_status；
- 看到 status=1（播放中）才算真正开始；迟迟不开始会重发，重发仍不行才跳过这首；
- 一曲结束的判定（任一成立）：
    a) 曾经在播、现在不在播，且进度已接近结尾（音箱上报的 position/duration 优先，
       没有上报时用本地累计的播放时长对比文件时长）；
    b) 音箱自己把同一首歌循环回了开头（position 从结尾附近跳回开头）；
    c) 兜底定时器：累计播放时长超过文件时长 + 宽限，且没有来自音箱的进度信息可以反驳
       （比如状态接口一直失败）。
- 曾经在播、现在不在播、但离结尾还远 → 视为被外部暂停/打断（例如用户对小爱说了别的话），
  不切歌；之后音箱恢复播放则继续跟踪。
- 下发播放失败会带退避重试；一直失败则进入 retrying，周期性重试，队列不丢。
"""
from __future__ import annotations

import asyncio
import logging
import random
import time

from .micloud import CloudError, DeviceOffline, NeedLogin, NetworkError
from .util import atomic_write_json, read_json

log = logging.getLogger("nasbox.player")

MODES = ("all", "one", "random", "seq")
MODE_LABEL = {"all": "全部循环", "one": "单曲循环", "random": "随机播放", "seq": "顺序播放"}


class Player:
    def __init__(self, mgr: "PlayerManager", device_id: str):
        self.mgr = mgr
        self.cloud = mgr.cloud
        self.lib = mgr.lib
        self.settings = mgr.settings
        self.device_id = device_id
        self.queue: list[int] = []
        self.index = -1
        self.source = ""
        self.history: list[int] = []
        self.state = "idle"            # idle | starting | playing | paused | retrying
        self.song: dict | None = None
        self.error = ""
        self.volume: int | None = None
        self.sleep_until = 0.0
        self.gen = 0                   # 每次换歌自增，用来作废过期的异步操作
        # 进度跟踪
        self._play_acc = 0.0           # 已累计的播放秒数（不含暂停）
        self._play_since: float | None = None
        self._pos_ms = 0               # 音箱上报的进度
        self._pos_at = 0.0
        self._has_pos = False
        self._spk_dur_ms = 0
        self._saw_playing = False
        self._nonplay = 0
        self._sent_at = 0.0
        self._resend = 0
        self._skip_streak = 0
        # 识别「音箱改播了别的内容」
        self._fetched = False          # 音箱已来拉过当前这首
        self._own_aid = ""             # 播放我们这首歌时音箱上报的 audio_id（自学习）
        self._aid_cand, self._aid_cnt = "", 0
        self._sample_logged = False
        self._yielded = False          # 已让出控制（音箱在播别的内容）
        self._yield_at = 0.0
        # 音箱离线处理
        self._offline_hits = 0         # 连续「音箱没有响应」次数
        self._retry_count = 0          # retrying 状态下已重试次数
        self._probe_task: asyncio.Task | None = None
        # 语音打断处理
        self._intr_until = 0.0         # 这段时间内加快状态检查（刚有人对音箱说话）
        self._intr_task: asyncio.Task | None = None
        self._status_fail = 0
        self._ext_paused = False
        self._paused_at = 0.0
        self._monitor: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._last_status_at = 0.0
        self.fast = 1.0                # 测试用的时间缩放

    # ------------------------------------------------------------------ 计时
    def _clock_start(self):
        if self._play_since is None:
            self._play_since = time.monotonic()

    def _clock_stop(self):
        if self._play_since is not None:
            self._play_acc += time.monotonic() - self._play_since
            self._play_since = None

    def elapsed(self) -> float:
        e = self._play_acc
        if self._play_since is not None:
            e += time.monotonic() - self._play_since
        return e

    def duration(self) -> float:
        if self._spk_dur_ms > 0:
            return self._spk_dur_ms / 1000
        return float((self.song or {}).get("duration") or 0)

    def position(self) -> float:
        if self.state == "idle" or not self.song:
            return 0.0
        if self._has_pos:
            p = self._pos_ms / 1000
            if self.state == "playing":
                p += time.monotonic() - self._pos_at
        else:
            p = self.elapsed()
        d = self.duration()
        return max(0.0, min(p, d) if d > 0 else p)

    # ------------------------------------------------------------------ 对外快照
    def snapshot(self) -> dict:
        dev = self.cloud.device(self.device_id) or {}
        return {"device_id": self.device_id, "device_name": dev.get("name", ""),
                "state": self.state, "song": self.song, "position": round(self.position(), 1),
                "duration": round(self.duration(), 1), "volume": self.volume,
                "mode": self.settings["play_mode"], "mode_label": MODE_LABEL[self.settings["play_mode"]],
                "queue_len": len(self.queue), "index": self.index, "source": self.source,
                "error": self.error, "ext_paused": self._ext_paused,
                "sleep_remaining": max(0, int(self.sleep_until - time.time())) if self.sleep_until else 0}

    def _notify(self):
        self.mgr.notify()

    # ------------------------------------------------------------------ 队列
    async def play_queue(self, ids: list[int], index: int = 0, source: str = ""):
        if not ids:
            raise ValueError("没有可播放的歌曲")
        self.queue = list(ids)
        self.index = max(0, min(index, len(ids) - 1))
        self.source = source
        self.history = []
        self._skip_streak = 0
        self.mgr.save_state()
        log.info("播放新队列（%s）：共 %d 首，从第 %d 首开始", source or "未命名", len(ids), self.index + 1)
        await self._start_current()

    def enqueue(self, ids: list[int], play_next: bool = False):
        if play_next and self.index >= 0:
            self.queue[self.index + 1:self.index + 1] = ids
        else:
            self.queue.extend(ids)
        self.mgr.save_state()
        self._notify()

    def remove_from_queue(self, pos: int):
        if 0 <= pos < len(self.queue) and pos != self.index:
            self.queue.pop(pos)
            if pos < self.index:
                self.index -= 1
            self.mgr.save_state()
            self._notify()

    def _pick_next(self, auto: bool) -> int | None:
        n = len(self.queue)
        if n == 0:
            return None
        mode = self.settings["play_mode"]
        if auto and mode == "one":
            return self.index
        if mode == "random" and n > 1:
            choices = [i for i in range(n) if i != self.index]
            return random.choice(choices)
        nxt = self.index + 1
        if nxt >= n:
            if auto and mode == "seq":
                return None
            nxt = 0
        return nxt

    async def next(self, auto: bool = False):
        nxt = self._pick_next(auto)
        if nxt is None:
            await self.stop(reason="队列播放完毕")
            return
        if self.index >= 0 and nxt != self.index:
            self.history.append(self.index)
            self.history = self.history[-200:]
        self.index = nxt
        self.mgr.save_state()
        if not auto:
            log.info("手动/口令切到下一首")
        await self._start_current()

    async def prev(self):
        if not self.queue:
            return
        if self.history:
            self.index = min(self.history.pop(), len(self.queue) - 1)
        else:
            self.index = (self.index - 1) % len(self.queue)
        self.mgr.save_state()
        log.info("手动/口令切到上一首")
        await self._start_current()

    async def play_index(self, pos: int):
        if 0 <= pos < len(self.queue):
            if self.index >= 0:
                self.history.append(self.index)
            self.index = pos
            self.mgr.save_state()
            log.info("手动点播队列第 %d 首", pos + 1)
            await self._start_current()

    # ------------------------------------------------------------------ 下发播放
    async def _start_current(self):
        self.gen += 1
        gen = self.gen
        # 找到一首文件还在的歌
        song = None
        for _ in range(len(self.queue)):
            song = self.lib.song(self.queue[self.index])
            if song:
                break
            log.warning("队列第 %d 首已不在曲库，跳过", self.index + 1)
            self.index = (self.index + 1) % len(self.queue)
        if not song:
            await self.stop(reason="队列里的歌曲都已不存在")
            return
        self.song = song
        self.error = ""
        self.state = "starting"
        self._play_acc, self._play_since = 0.0, None
        self._pos_ms, self._has_pos, self._spk_dur_ms = 0, False, 0
        self._saw_playing, self._nonplay, self._resend, self._status_fail = False, 0, 0, 0
        self._ext_paused = False
        self._fetched, self._own_aid, self._aid_cand, self._aid_cnt = False, "", "", 0
        self._offline_hits, self._retry_count = 0, 0
        self._sample_logged, self._yielded = False, False
        self._sent_at = 0.0            # 0 = 播放指令尚未成功下发
        self._notify()
        self._ensure_monitor()
        ok = await self._send_play(gen, attempts=4)
        if gen != self.gen:
            return
        if not ok:
            self.state = "retrying"
            self._notify()
        self.mgr.media.prewarm(self._peek_next_song())

    def _peek_next_song(self) -> dict | None:
        if self.settings["play_mode"] in ("random", "one") or not self.queue:
            return None
        return self.lib.song(self.queue[(self.index + 1) % len(self.queue)])

    async def _send_play(self, gen: int, attempts: int = 1) -> bool:
        """把当前歌曲的地址发给音箱。失败带退避重试；返回是否成功。"""
        song = self.song
        url = self.mgr.media.url_for(song)
        for i in range(attempts):
            if gen != self.gen:
                return False
            try:
                res = await self.cloud.play_url(self.device_id, url)
                code = res.get("code", 0) if isinstance(res, dict) else 0
                if code == 0:
                    self._sent_at = time.monotonic()
                    self.error = ""
                    self._offline_hits, self._retry_count = 0, 0
                    log.info("已下发播放：%s（队列 %d/%d）", song["title"], self.index + 1, len(self.queue))
                    # 音箱的循环模式会被小米自有播放改动（如实机日志里 1→2），NAS 从不设它就会一直残留；
                    # 每次下发都固定为列表循环（1，与 micli play 一致），失败也不影响本次播放。
                    try:
                        await self.cloud.set_loop(self.device_id, 1)
                    except Exception:
                        log.warning("设置音箱循环模式失败（不影响播放）", exc_info=True)
                    return True
                self.error = f"音箱拒绝播放指令 (code={code})"
                log.warning("下发播放返回 code=%s", code)
            except NeedLogin as e:
                self.error = str(e)
                return False
            except DeviceOffline as e:
                self._offline_hits += 1
                log.warning("下发播放失败，音箱没有响应（第 %d 次）：%s", self._offline_hits, e)
                if self._offline_hits >= 2:
                    self._go_offline()
                    return False
                self.error = "音箱没有响应，正在重试"
            except NetworkError as e:
                self.error = f"网络异常，正在重试：{e}"
                log.warning("下发播放失败（第 %d 次）：%s", i + 1, e)
            except CloudError as e:
                self.error = f"云端错误，正在重试：{e}"
                log.warning("下发播放失败（第 %d 次）：%s", i + 1, e)
            if i + 1 < attempts:
                self._notify()
                await asyncio.sleep(min(2 ** i, 4) * self.fast)
        return False

    # ------------------------------------------------------------------ 控制
    async def pause(self):
        if self.state not in ("playing", "starting"):
            return
        await self.cloud.player_op(self.device_id, "pause")
        log.info("手动/口令暂停：%s（已播 %d 秒）", (self.song or {}).get("title"), int(self.elapsed()))
        self._clock_stop()
        self._freeze_pos()
        self.state = "paused"
        self._ext_paused = False
        self._paused_at = time.monotonic()
        self._pause_refetch, self._pause_warned = False, False
        self._notify()

    def _freeze_pos(self):
        if self._has_pos and self._pos_at:
            self._pos_ms = int(self.position() * 1000)
            self._pos_at = time.monotonic()

    async def resume(self):
        if self.state == "paused" and not self._has_pos and self.song:
            # 不上报进度的音箱（如小米智能音箱 Pro）暂停后无法从断点续播：直接从头重放这一首，进度归零
            log.info("音箱不支持断点续播，从头重放：%s", self.song["title"])
            await self._start_current()
        elif self.state == "paused":
            await self.cloud.player_op(self.device_id, "play")
            self.state = "playing"
            self._ext_paused = False
            self._saw_playing = False       # 重新确认音箱真的恢复了
            self._nonplay = 0
            self._sent_at = time.monotonic()
            self._pos_at = time.monotonic()
            self._clock_start()
            self._ensure_monitor()
            self._notify()
        elif self.state == "idle" and self.queue:
            if self.index < 0:
                self.index = 0
            await self._start_current()

    async def toggle(self):
        if self.state in ("playing", "starting"):
            await self.pause()
        else:
            await self.resume()

    async def stop(self, reason: str = ""):
        self.gen += 1
        was_active = self.state != "idle"
        self.state = "idle"
        self._clock_stop()
        self.sleep_until = 0
        self.error = reason if reason and reason != "user" else ""
        self._notify()
        if was_active:
            for act in ("pause", "stop"):
                try:
                    await self.cloud.player_op(self.device_id, act)
                except CloudError as e:
                    log.warning("停止播放指令 %s 失败：%s", act, e)
                    break

    async def seek(self, seconds: float):
        if self.state not in ("playing", "paused") or not self.song:
            return
        seconds = max(0.0, min(seconds, max(0.0, self.duration() - 1)))
        await self.cloud.seek(self.device_id, int(seconds * 1000))
        self._play_acc = seconds
        if self._play_since is not None:
            self._play_since = time.monotonic()
        self._pos_ms, self._pos_at = int(seconds * 1000), time.monotonic()
        self._notify()

    async def set_volume(self, v: int):
        v = max(0, min(100, int(v)))
        await self.cloud.set_volume(self.device_id, v)
        self.volume = v
        self._notify()

    async def volume_step(self, delta: int):
        if self.volume is None:
            await self.refresh_status(force=True)
        cur = self.volume if self.volume is not None else 30
        await self.set_volume(cur + int(delta) * int(self.settings["volume_step"]))

    async def refresh_status(self, force: bool = False):
        """空闲时偶尔同步一下音量（有界面打开时才会调用）。"""
        if not force and time.monotonic() - self._last_status_at < 10:
            return
        self._last_status_at = time.monotonic()
        try:
            st = await self.cloud.get_status(self.device_id)
            if self.volume != st["volume"]:
                self.volume = st["volume"]
                self._notify()
        except CloudError:
            pass

    def set_sleep(self, minutes: float):
        self.sleep_until = time.time() + minutes * 60 if minutes > 0 and self.state != "idle" else 0
        self._ensure_monitor()
        self._notify()

    # ------------------------------------------------------------------ 监视循环
    def _ensure_monitor(self):
        if self._monitor is None or self._monitor.done():
            self._monitor = asyncio.create_task(self._monitor_loop())

    async def _monitor_loop(self):
        try:
            retry_at = 0.0
            while self.state != "idle":
                poll = float(self.settings["status_poll_sec"])
                dur = self.duration()
                if self.state == "playing" and dur > 0 and dur - self.position() < 8:
                    poll = min(poll, 1.0)
                if self.state == "starting":
                    poll = min(poll, 1.5)
                if self.state == "paused":
                    poll = max(poll, 5.0)
                if time.monotonic() < self._intr_until:
                    poll = min(poll, 1.0)
                await asyncio.sleep(poll * self.fast)
                if self.state == "idle":
                    break
                if self.sleep_until and time.time() >= self.sleep_until:
                    log.info("定时关闭时间到，停止播放")
                    await self.stop(reason="定时关闭")
                    break
                gen = self.gen
                if self.state == "retrying":
                    if time.monotonic() >= retry_at:
                        self._retry_count += 1
                        if self._retry_count > 30:
                            await self.stop(reason="连续重试约 5 分钟仍无法下发播放，已停止。请检查 NAS 的外网连接后再点播放")
                            break
                        ok = await self._send_play(gen, attempts=1)
                        retry_at = time.monotonic() + 10 * self.fast
                        if ok and gen == self.gen:
                            self.state = "starting"
                            self._notify()
                    continue
                try:
                    st = await self.cloud.get_status(self.device_id)
                    self._status_fail = 0
                    self._offline_hits = 0
                except NeedLogin:
                    self.error = "登录已失效，需要重新扫码"
                    self._notify()
                    continue
                except DeviceOffline as e:
                    self._status_fail += 1
                    self._offline_hits += 1
                    if self._offline_hits == 1:
                        log.warning("读取音箱状态失败，音箱没有响应：%s", e)
                    if self._offline_hits >= 4:
                        self._go_offline()
                        break
                    continue
                except CloudError as e:
                    self._status_fail += 1
                    if self._status_fail in (1, 5) or self._status_fail % 20 == 0:
                        log.warning("读取音箱状态失败（连续 %d 次）：%s", self._status_fail, e)
                    await self._fallback_timer(gen, trusted_pos=False)
                    continue
                if gen != self.gen:
                    continue
                self._last_status_at = time.monotonic()
                await self._on_status(st, gen)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("播放监视循环异常，1 秒后重启")
            await asyncio.sleep(1)
            if self.state != "idle":
                self._monitor = asyncio.create_task(self._monitor_loop())

    async def _fallback_timer(self, gen: int, trusted_pos: bool):
        """兜底：累计播放时长已超过文件时长 + 宽限，而且没有可信的进度反驳，就切歌。"""
        if self.state not in ("playing", "starting") or gen != self.gen:
            return
        dur = float((self.song or {}).get("duration") or 0)
        if dur <= 0 or trusted_pos:
            return
        if self.state == "starting":
            # 状态接口不可用时无法确认开播，按「已下发即开始」计时
            if time.monotonic() - self._sent_at > 3 * self.fast and self._sent_at:
                self.state = "playing"
                self._clock_start()
                self._notify()
            return
        if self.elapsed() >= dur + float(self.settings["end_grace_sec"]):
            log.info("兜底定时器触发切歌（无可用进度信息）：%s", self.song["title"])
            await self._finished(gen)

    def on_stream_fetch(self, sid: int, from_start: bool):
        """音箱又从头拉了一遍当前这首歌。

        小米智能音箱 Pro（OH2P）不上报进度，播完会自己把同一首从头再放；
        这次重新拉流就是最准的「一曲结束」信号，比兜底定时器早几秒，避免听到开头重播。
        只在累计播放时长已接近文件时长时才认，开播阶段的重复请求不算。
        """
        if not self.song or self.song.get("id") != sid:
            return
        self._fetched = True
        if self._yielded and self.state == "idle":
            if from_start and time.monotonic() - self._yield_at < 900:
                # 让出控制后音箱又回来从头拉这首歌：它没有真的改播别的，恢复跟踪
                log.info("音箱重新播放 NAS 歌曲，恢复控制：%s", self.song["title"])
                self._yielded = False
                self.error = ""
                self.state = "playing"
                self._play_acc, self._play_since = 0.0, None
                self._saw_playing, self._nonplay, self._ext_paused = False, 0, False
                self._own_aid, self._aid_cand, self._aid_cnt = "", "", 0
                self._sent_at = time.monotonic()
                self._clock_start()
                self._ensure_monitor()
                self._notify()
            return
        if not from_start or self.state not in ("playing", "paused"):
            return
        dur = float(self.song.get("duration") or 0)
        if dur <= 0 or self._has_pos:
            return
        if self.state == "playing" and self.elapsed() >= max(dur * 0.8, dur - 20):
            log.info("音箱重新从头拉流，判定一曲结束：%s", self.song["title"])
            asyncio.get_running_loop().create_task(self._finished(self.gen))
        elif self.state == "paused" and not self._ext_paused:
            # 手动暂停期间音箱又从头拉流：记下来，配合「播放中」状态才认定它恢复了
            self._pause_refetch = True
            self._play_acc, self._play_since = 0.0, None
            self._notify()
        elif self.elapsed() > 3:
            # 曲中又从头拉流：音箱被打断后从头重放了，进度归零
            log.info("音箱在曲中从头重新拉流，进度归零：%s", self.song["title"])
            self._play_acc = 0.0
            if self._play_since is not None:
                self._play_since = time.monotonic()
            self._notify()

    def _cancel_intr(self):
        if self._intr_task and not self._intr_task.done() and self._intr_task is not asyncio.current_task():
            self._intr_task.cancel()
        self._intr_task = None

    def voice_stopped(self, query: str) -> bool:
        """用户口头叫停（不是我们的口令）：进度条立刻停，按暂停处理，不自动恢复。"""
        self._cancel_intr()
        if self.state not in ("playing", "starting"):
            return False
        log.info("语音叫停（%s）：按暂停处理，点播放可重新播放 %s", query, (self.song or {}).get("title"))
        self._clock_stop()
        self._freeze_pos()
        self.state = "paused"
        self._ext_paused = False
        self._paused_at = time.monotonic()
        self._notify()
        return True

    def voice_interrupted(self, query: str, delay: float) -> bool:
        """播放中有人跟音箱说了别的话（调音量、问天气）：加快检查；等它答完，没自己恢复就重放当前这首。"""
        if self.state not in ("playing", "starting") and not (self.state == "paused" and self._ext_paused):
            return False
        self._cancel_intr()
        self._intr_until = time.monotonic() + (delay + 5) * self.fast
        self._intr_task = asyncio.create_task(self._resume_after_talk(self.gen, query, delay))
        return True

    async def _resume_after_talk(self, gen: int, query: str, delay: float):
        try:
            await asyncio.sleep(delay * self.fast)
            for _ in range(2):
                if gen != self.gen or self.state == "idle" or not self.song:
                    return
                if self.state == "paused" and not self._ext_paused:
                    return                          # 期间用户主动暂停了
                st = await self.cloud.get_status(self.device_id)
                if st["status"] != 1:
                    break
                if self.state != "paused":
                    return                          # 一直在播，没被打断
                await asyncio.sleep(2 * self.fast)  # 音箱报在播、我们还记着停：给监视循环一点时间确认
            else:
                return
            if gen != self.gen or self.state == "idle" or (self.state == "paused" and not self._ext_paused):
                return
            log.info("被语音打断后音箱没有恢复播放（%s），从头重放：%s", query, self.song["title"])
            await self._start_current()
        except asyncio.CancelledError:
            raise
        except CloudError as e:
            log.warning("对话结束后恢复播放失败：%s", e)
        except Exception:
            log.exception("对话结束后恢复播放异常")

    def _go_offline(self):
        """音箱关机/断网：停止播放和重试，保留队列与当前位置；后台低频探测，恢复后只更新在线状态。"""
        if self.state == "idle":
            return
        log.warning("音箱没有响应（可能已关机或断网），停止播放并停止重试；音箱恢复后点播放可从当前这首继续")
        self.gen += 1
        self.state = "idle"
        self._clock_stop()
        self._offline_hits = 0
        self.error = "音箱没有响应（可能已关机或断网），已停止播放。音箱恢复后点播放，可从当前这首继续"
        self.cloud.mark_presence(self.device_id, False)
        self._notify()
        if not self._probe_task or self._probe_task.done():
            self._probe_task = asyncio.create_task(self._probe_loop())

    async def _probe_loop(self):
        """离线后的在线探测：30 秒、1 分钟、2 分钟，之后每 5 分钟一次；探到就停。不会自动恢复播放。"""
        delay = 30.0
        try:
            while self.device_id in self.cloud.offline_local:
                await asyncio.sleep(delay * self.fast)
                delay = min(delay * 2, 300.0)
                if self.device_id not in self.cloud.offline_local:
                    break
                if self.state != "idle":
                    continue                      # 用户已经重新开始播放，由播放流程自己判断
                try:
                    await self.cloud.get_status(self.device_id)      # 成功即自动标回在线
                    log.info("音箱恢复在线")
                    if self.state == "idle" and "没有响应" in (self.error or ""):
                        self.error = ""
                    self._notify()
                    break
                except CloudError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("在线探测异常")

    def yield_control(self, reason: str):
        """音箱改播了别的内容（例如用户用语音点了小米自己的曲库）：停止跟踪，不再发任何指令去抢。"""
        if self.state == "idle" or not self.song:
            return
        log.info("音箱改播了其他内容，NAS 音乐盒让出控制（%s）：原曲 %s", reason, self.song["title"])
        self.gen += 1
        self.state = "idle"
        self._clock_stop()
        self._yielded, self._yield_at = True, time.monotonic()
        self.error = "音箱正在播放其他内容，NAS 音乐盒已停止控制；点播放可重新播放 NAS 歌曲"
        self._notify()

    def _check_takeover(self, st: dict) -> bool:
        """靠音箱上报的 audio_id 判断是否被别的内容接管（自学习：先记住播我们这首时的值）。"""
        if not self._sample_logged:
            self._sample_logged = True
            log.info("音箱状态样本：status=%s audio_id=%s position=%s duration=%s loop_type=%s 字段=%s 详情字段=%s",
                     st.get("status"), st.get("audio_id") or "-", st.get("position"), st.get("duration"),
                     st.get("loop_type"), ",".join(st.get("info_keys") or []), ",".join(st.get("detail_keys") or []))
        aid = st.get("audio_id") or ""
        if not aid or not self._fetched:
            return False
        if aid == self._aid_cand:
            self._aid_cnt += 1
        else:
            if self._own_aid and aid != self._own_aid:
                log.info("音箱上报的 audio_id 变了：%s → %s", self._own_aid, aid)
            self._aid_cand, self._aid_cnt = aid, 1
        if not self._own_aid:
            if self._aid_cnt >= 2:
                self._own_aid = aid
            return False
        return aid != self._own_aid and self._aid_cnt >= 2

    async def _finished(self, gen: int):
        if gen != self.gen:
            return
        self._skip_streak = 0
        await self.next(auto=True)

    async def _on_status(self, st: dict, gen: int):
        changed = False
        if st["volume"] != self.volume:
            self.volume = st["volume"]
            changed = True
        playing = st["status"] == 1
        file_dur = float((self.song or {}).get("duration") or 0)
        poll = float(self.settings["status_poll_sec"])
        near_margin = max(5.0, 2 * poll)

        if self.state == "paused" and playing and not self._ext_paused and not self._has_pos \
                and not getattr(self, "_pause_refetch", False):
            # 手动暂停后，不报进度的音箱（如 OH2P）仍可能上报「播放中」，单凭这个状态不能当作恢复播放，
            # 否则进度条会自己接着走、走完还会切到下一首。只有音箱又从头拉了这首歌才算它真的恢复了。
            if not getattr(self, "_pause_warned", False):
                self._pause_warned = True
                log.info("已手动暂停，音箱仍上报播放中（status=%s），保持暂停", st.get("status"))
            if changed:
                self._notify()
            return
        if self.state == "paused":
            if playing and (self._ext_paused or time.monotonic() - self._paused_at > 6 * self.fast):
                # 音箱自己恢复了（例如用户说「继续播放」，或答完话自动续播）
                if not self._has_pos:
                    # 这类音箱被打断后是从头重放的，进度跟着归零，免得进度条走完歌还没唱完就切歌
                    log.info("音箱被打断后自行恢复播放，按从头重放处理，进度归零：%s", (self.song or {}).get("title"))
                    self._play_acc, self._play_since = 0.0, None
                self.state = "playing"
                self._ext_paused = False
                self._saw_playing = True
                self._pos_at = time.monotonic()
                self._clock_start()
                changed = True
            if changed:
                self._notify()
            return

        if playing and self.state != "paused" and self._check_takeover(st):
            self.yield_control("音箱上报的曲目已不是 NAS 歌曲")
            return
        if playing:
            self._nonplay = 0
            if not self._saw_playing:
                self._saw_playing = True
                self._skip_streak = 0
                if self.state == "starting":
                    self.state = "playing"
                self._clock_start()
                changed = True
            trusted = False
            if st["duration"] > 0:
                # 音箱上报了进度：以它为准
                looped = (self._has_pos and self._pos_ms >= st["duration"] * 0.85
                          and st["position"] + 3000 < self._pos_ms and st["position"] < st["duration"] * 0.2)
                if looped:
                    log.info("检测到音箱自行循环回开头，判定一曲结束：%s", self.song["title"])
                    await self._finished(gen)
                    return
                self._has_pos = True
                self._spk_dur_ms = st["duration"]
                self._pos_ms, self._pos_at = st["position"], time.monotonic()
                trusted = st["position"] < st["duration"] - 1500
                # 进度可信但迟迟不结束的上限：超过文件时长 60 秒仍在播，也按结束处理
                if file_dur > 0 and self.elapsed() > max(file_dur, st["duration"] / 1000) + 60:
                    trusted = False
            if changed:
                self._notify()
            await self._fallback_timer(gen, trusted_pos=trusted)
            return

        # ---- 音箱当前不在播放 ----
        if not self._sent_at:
            if changed:
                self._notify()
            return                      # 指令还在下发/重试中，先不判断
        self._nonplay += 1
        if not self._saw_playing:
            waited = time.monotonic() - self._sent_at
            if waited > 12 * self.fast:
                if self._resend < 2:
                    self._resend += 1
                    log.warning("音箱 %d 秒内没有开始播放，重发（第 %d 次）", int(waited), self._resend)
                    await self._send_play(gen, attempts=2)
                else:
                    self._skip_streak += 1
                    if self._skip_streak >= 3:
                        await self.stop(reason="音箱连续多首无法开始播放，请检查「音箱拉流地址」设置与网络")
                    else:
                        log.warning("《%s》多次下发后仍未开始播放，跳到下一首", self.song["title"])
                        self.error = f"《{self.song['title']}》无法播放，已跳过"
                        streak = self._skip_streak
                        await self.next(auto=False)
                        self._skip_streak = streak
            if changed:
                self._notify()
            return

        # 曾经在播，现在停了：结束 or 被打断？
        if self._has_pos and self._spk_dur_ms > 0:
            pos_now = st["position"] if st["duration"] > 0 else self._pos_ms + (time.monotonic() - self._pos_at) * 1000
            near_end = max(pos_now, self._pos_ms + (time.monotonic() - self._pos_at) * 1000) >= self._spk_dur_ms - near_margin * 1000
        else:
            near_end = file_dur > 0 and self.elapsed() >= file_dur - near_margin
        if near_end:
            log.info("音箱播放结束：%s", self.song["title"])
            await self._finished(gen)
            return
        if self._nonplay >= 2 or time.monotonic() < self._intr_until:
            log.info("音箱在曲中停止（疑似被外部暂停/打断），不切歌，等待恢复")
            self._clock_stop()
            self._freeze_pos()
            self.state = "paused"
            self._ext_paused = True
            changed = True
        if changed:
            self._notify()


class PlayerManager:
    def __init__(self, cloud, lib, settings, media, paths):
        self.cloud, self.lib, self.settings, self.media, self.paths = cloud, lib, settings, media, paths
        self.players: dict[str, Player] = {}
        self.on_change = None
        self._saved = read_json(paths.state_file, {}) or {}
        media.on_fetch = self._on_fetch

    def _on_fetch(self, sid: int, from_start: bool):
        for p in list(self.players.values()):
            p.on_stream_fetch(sid, from_start)

    def notify(self):
        if self.on_change:
            self.on_change()

    def current_device_id(self) -> str:
        cur = self.settings["current_device"]
        ids = [d["device_id"] for d in self.cloud.devices]
        if cur and cur in ids:
            return cur
        for d in self.cloud.devices:
            if d.get("online"):
                return d["device_id"]
        return ids[0] if ids else ""

    def get(self, device_id: str | None = None) -> Player:
        did = device_id or self.current_device_id()
        if not did:
            raise ValueError("还没有可用的音箱，请先扫码登录")
        if device_id and not self.cloud.device(device_id):
            raise ValueError("找不到这台音箱")
        p = self.players.get(did)
        if not p:
            p = self.players[did] = Player(self, did)
            s = self._saved.get(did) or {}
            ids = [i for i in s.get("queue", []) if isinstance(i, int)]
            if ids:
                p.queue, p.index, p.source = ids, min(int(s.get("index", 0)), len(ids) - 1), s.get("source", "")
                p.song = self.lib.song(p.queue[p.index]) if p.index >= 0 else None
        return p

    def save_state(self):
        try:
            data = {did: {"queue": p.queue[:5000], "index": p.index, "source": p.source}
                    for did, p in self.players.items()}
            atomic_write_json(self.paths.state_file, data)
        except OSError as e:
            log.warning("保存播放队列失败：%s", e)

    def any_active(self) -> bool:
        return any(p.state != "idle" for p in self.players.values())
