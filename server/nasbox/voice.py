"""语音口令：轮询小爱对话记录，命中口令后先打断小爱自己的回复，再执行。"""
from __future__ import annotations

import re
import asyncio
import logging
import time

from .micloud import CloudError, NeedLogin, NetworkError
from .util import norm

log = logging.getLogger("nasbox.voice")

PREFIX_CMDS = ("play_list", "play_song")
CONTROL_ORDER = ("fav_del", "fav_add", "mode_one", "mode_all", "mode_random", "next", "prev",
                 "pause", "resume", "stop")
# 这些口令只有在本程序正在控制播放时才响应，避免抢走小爱自己的「暂停/下一首」
NEED_ACTIVE = {"next", "prev", "pause", "resume", "stop", "fav_add", "fav_del"}
FILLERS = ("请", "帮我", "给我", "我要", "我想听", "我想")


def parse_command(query: str, keywords: dict) -> tuple[str, str] | None:
    """把一句话解析成 (口令, 参数)。未命中返回 None。"""
    q = norm(query)
    if not q:
        return None
    for f in FILLERS:
        if q.startswith(f) and len(q) > len(f):
            q = q[len(f):]
            break
    # 控制类：整句基本等于关键词（允许多 3 个字，如「下一首歌」「暂停播放」）
    for cmd in CONTROL_ORDER:
        for kw in sorted((norm(k) for k in keywords.get(cmd, [])), key=len, reverse=True):
            if kw and kw in q and len(q) - len(kw) <= 3:
                return cmd, ""
    # 前缀类：关键词 + 名称；关键词长的优先
    cands = []
    for cmd in PREFIX_CMDS:
        for kw in keywords.get(cmd, []):
            k = norm(kw)
            if k and q.startswith(k):
                cands.append((len(k), cmd, q[len(k):]))
    if cands:
        cands.sort(reverse=True)
        _, cmd, arg = cands[0]
        return cmd, arg
    return None


# 没命中我们的口令、但像是让小爱自己播放内容的说法
OTHER_PLAY_RE = re.compile(r"播放|来一?[首个点曲段]|放一?[首个点曲段]|[想要]听|听一?[首个下点段]|唱一?[首个]|讲个?.{0,4}故事|放.{0,8}(歌|音乐|曲)")
# 没说口令关键词的自然点播说法：取出后面的名字（输入已归一化，无空格标点）
NATURAL_PLAY_RE = re.compile(r"^(?:请|帮我|给我|麻烦)?(?:我想听|我要听|想听|播放|播|放|来|听|唱)(?:一下|一首|一个|一曲|一点|首|点)?(.+?)(?:吧|呗|好吗|可以吗)?$")
GENERIC_ARGS = {"歌", "歌曲", "音乐", "首歌", "一首歌", "点歌", "歌吧", "的歌", "故事", "新闻", "相声", "电台", "广播"}

# 调音量：不算点播，也不算叫停
VOLUME_RE = re.compile(r"音量|声音|大声|小声|[大小]一?点")
# 没用我们的口令、但意思是别再放了
STOP_RE = re.compile(r"停|别[放唱播吵说]|不要[放唱播]|不[想要]?听|关[掉闭机]|闭嘴|安静|静音|晚安|睡觉|退下")


class VoiceService:
    def __init__(self, cloud, lib, players, settings):
        self.cloud, self.lib, self.players, self.settings = cloud, lib, players, settings
        self.last_ts: dict[str, int] = {}
        self.last_hit = 0.0
        self.last_query = ""
        self.last_result = ""
        self.last_poll_ok = 0.0
        self.poll_error = ""
        self._task: asyncio.Task | None = None
        self.fast = 1.0

    def start(self):
        self._task = asyncio.create_task(self._loop())

    def status(self) -> dict:
        return {"enabled": bool(self.settings["voice_enabled"]), "interval": self._interval(),
                "last_query": self.last_query, "last_result": self.last_result,
                "last_poll_ok": int(self.last_poll_ok), "error": self.poll_error}

    def _interval(self) -> float:
        active = self.players.any_active() or (time.time() - self.last_hit) < float(self.settings["voice_idle_after_min"]) * 60
        return float(self.settings["voice_poll_sec"] if active else self.settings["voice_poll_idle_sec"])

    async def _loop(self):
        backoff = 0.0
        while True:
            try:
                await asyncio.sleep((backoff or self._interval()) * self.fast)
                if not self.settings["voice_enabled"] or self.cloud.state() in ("no_login", "need_qr"):
                    await asyncio.sleep(3 * self.fast)
                    continue
                did = self.players.current_device_id()
                if not did:
                    await asyncio.sleep(3 * self.fast)
                    continue
                await self.poll_once(did)
                backoff = 0.0
                self.poll_error = ""
                self.last_poll_ok = time.time()
            except asyncio.CancelledError:
                raise
            except NeedLogin:
                backoff = 30
            except NetworkError as e:
                backoff = min(max(backoff * 2, 3), 30)
                self.poll_error = f"网络异常：{e}"
            except CloudError as e:
                backoff = min(max(backoff * 2, 5), 60)
                self.poll_error = str(e)
                log.warning("拉取对话记录失败：%s", e)
            except Exception:
                backoff = 10
                log.exception("语音口令循环异常")

    async def poll_once(self, device_id: str):
        records = await self.cloud.conversation(device_id)
        if device_id not in self.last_ts:
            # 首次：只记下最新时间，不执行历史口令
            self.last_ts[device_id] = max([r["time"] for r in records] + [int(time.time() * 1000) - 5000])
            return
        fresh = sorted((r for r in records if r["time"] > self.last_ts[device_id]), key=lambda r: r["time"])
        if not fresh:
            return
        self.last_ts[device_id] = fresh[-1]["time"]
        await self.handle(device_id, fresh[-1]["query"], fresh[-1].get("answer", ""))     # 只处理最新一句

    async def handle(self, device_id: str, query: str, answer: str = "") -> str:
        """解析并执行一句话，返回结果说明（便于界面显示与测试）。"""
        parsed = parse_command(query, self.settings["keywords"])
        if not parsed:
            # 不是我们的口令、但听起来是让小爱自己播内容（「播放周杰伦的歌」「我想听相声」）：让出控制
            player = self.players.get(device_id)
            q = query or ""
            if self.settings["voice_nas_first"] and not VOLUME_RE.search(q):
                # 没说口令关键词的点播（「播放周杰伦的歌」）：曲库里有就由 NAS 播，没有才留给小爱
                res = await self._nas_first(player, device_id, q)
                if res:
                    return res
            if player.state == "idle" or not q.strip():
                return ""
            if STOP_RE.search(q) and not VOLUME_RE.search(q):
                # 没用我们的口令叫停（「别放了」「关掉音乐」）：进度条立刻停，不自动恢复
                if player.voice_stopped(q):
                    self.last_query, self.last_result = query, "已按暂停处理"
            elif not VOLUME_RE.search(q) and OTHER_PLAY_RE.search(q):
                player.yield_control(f"语音：{query}")
                self.last_query, self.last_result = query, "小爱自己播放，已让出控制"
            elif self.settings["voice_resume_after_talk"]:
                # 调音量、问天气这类对话会把歌打断：等小爱答完，没有自己恢复就从头重放当前这首
                delay = float(self.settings["voice_resume_wait_sec"]) + min(len(answer or "") / 4.5, 40)
                if player.voice_interrupted(q, delay):
                    self.last_query, self.last_result = query, "对话结束后恢复播放"
            self.players.notify()
            return ""
        cmd, arg = parsed
        player = self.players.get(device_id)
        if cmd in NEED_ACTIVE and player.state == "idle":
            return ""
        self.last_hit = time.time()
        self.last_query = query
        log.info("命中语音口令：%s（%s %s）", query, cmd, arg)
        # 先打断小爱自己的回复 / 它自己找来的内容
        try:
            await self.cloud.player_op(device_id, "pause")
        except CloudError as e:
            log.warning("打断小爱失败（继续执行口令）：%s", e)
        try:
            result = await self._execute(player, device_id, cmd, arg)
        except CloudError as e:
            result = f"执行失败：{e}"
            log.warning("口令执行失败：%s", e)
        except ValueError as e:
            result = str(e)
        self.last_result = result
        self.players.notify()
        return result

    async def _nas_first(self, player, device_id: str, query: str) -> str:
        m = NATURAL_PLAY_RE.match(norm(query))
        if not m:
            return ""
        arg = m.group(1)
        if not arg or arg in GENERIC_ARGS:
            return ""
        found = self.lib.voice_queue(arg, strict=True)
        if not found:
            lst = self.lib.find_list_exact(arg)
            if lst:
                kind, key, name = lst
                ids = self.lib.playlist_ids(key) if kind == "playlist" else self.lib.folder_ids(key)
                if ids:
                    found = (ids, 0, f"歌单：{name}", f"播放歌单「{name}」")
        if not found:
            return ""
        ids, idx, source, result = found
        if not source.startswith("搜索") and self.settings["play_mode"] == "random":
            import random
            idx = random.randrange(len(ids))
        self.last_hit = time.time()
        self.last_query = query
        log.info("曲库里有，NAS 优先播放：%s（%s）", query, source)
        try:
            await self.cloud.player_op(device_id, "pause")     # 先打断小爱自己找来的内容
        except CloudError as e:
            log.warning("打断小爱失败（继续执行）：%s", e)
        try:
            await player.play_queue(ids, idx, source)
        except CloudError as e:
            result = f"执行失败：{e}"
            log.warning("NAS 优先播放失败：%s", e)
        self.last_result = result
        self.players.notify()
        return result

    async def _say(self, device_id: str, text: str):
        if self.settings["voice_tts_feedback"]:
            try:
                await self.cloud.tts(device_id, text)
            except CloudError as e:
                log.warning("TTS 失败：%s", e)

    async def _execute(self, player, device_id: str, cmd: str, arg: str) -> str:
        s = self.settings
        if cmd == "play_song":
            if not arg:
                if player.queue:
                    if player.state == "paused":
                        await player.resume()
                    else:
                        await player.play_queue(player.queue, max(player.index, 0), player.source)
                    return "继续播放队列"
                ids = self.lib.all_ids()
                if not ids:
                    await self._say(device_id, "曲库里还没有歌曲")
                    return "曲库为空"
                import random
                start = random.randrange(len(ids)) if s["play_mode"] == "random" else 0
                await player.play_queue(ids, start, "全部歌曲")
                return "播放全部歌曲"
            found = self.lib.voice_queue(arg)
            if not found:
                await self._say(device_id, f"曲库里没有找到{arg}")
                return f"没有找到「{arg}」"
            # 临时队列：歌手 → 这位歌手的全部歌；专辑 → 整张专辑；歌名 → 这首在前，后接同文件夹其余歌曲
            ids, idx, source, result = found
            if not source.startswith("搜索") and s["play_mode"] == "random":
                import random
                idx = random.randrange(len(ids))
            await player.play_queue(ids, idx, source)
            return result
        if cmd == "play_list":
            found = self.lib.find_list(arg) if arg else ("favorites", None, "收藏")
            if not found:
                await self._say(device_id, f"没有找到歌单{arg}")
                return f"没有找到歌单「{arg}」"
            kind, key, name = found
            ids = (self.lib.favorite_ids() if kind == "favorites" else
                   self.lib.playlist_ids(key) if kind == "playlist" else self.lib.folder_ids(key))
            if not ids:
                await self._say(device_id, f"歌单{name}是空的")
                return f"歌单「{name}」是空的"
            import random
            start = random.randrange(len(ids)) if s["play_mode"] == "random" else 0
            await player.play_queue(ids, start, f"歌单：{name}")
            return f"播放歌单「{name}」"
        if cmd == "next":
            await player.next()
            return "下一首"
        if cmd == "prev":
            await player.prev()
            return "上一首"
        if cmd == "pause":
            if player.state in ("playing", "starting"):
                await player.pause()
            return "已暂停"
        if cmd == "resume":
            await player.resume()
            return "继续播放"
        if cmd == "stop":
            await player.stop(reason="user")
            return "已停止"
        if cmd.startswith("mode_"):
            mode = cmd[5:]
            s.update({"play_mode": mode})
            if player.state == "paused" and player._ext_paused:
                await player.resume()        # 刚才为了打断小爱暂停了，恢复原播放
            elif player.state in ("playing", "starting"):
                try:
                    await self.cloud.player_op(device_id, "play")
                except CloudError:
                    pass
            if mode == "random" and player.state == "idle":
                ids = player.queue or self.lib.all_ids()
                if ids:
                    import random
                    await player.play_queue(ids, random.randrange(len(ids)), player.source or "全部歌曲")
            return {"one": "单曲循环", "all": "全部循环", "random": "随机播放"}[mode]
        if cmd in ("fav_add", "fav_del"):
            if player.song:
                self.lib.set_favorite(player.song["id"], cmd == "fav_add")
                player.song = self.lib.song(player.song["id"]) or player.song
            try:
                await self.cloud.player_op(device_id, "play")   # 恢复被打断的播放
            except CloudError:
                pass
            return "已加入收藏" if cmd == "fav_add" else "已取消收藏"
        return ""
