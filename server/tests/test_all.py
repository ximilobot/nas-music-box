"""端到端测试：全部对模拟云端运行，不接触任何真实小米账号。

运行： python -m unittest tests.test_all -v
依赖： ffmpeg（生成测试音频、测转码）
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile

import aiohttp
from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from mock_cloud import MockCloud  # noqa: E402
from nasbox.app import App  # noqa: E402
from nasbox.config import Paths  # noqa: E402
from nasbox.micloud import CloudError, NeedLogin, NetworkError  # noqa: E402
from nasbox.util import redact  # noqa: E402
from nasbox.voice import parse_command  # noqa: E402
from nasbox.config import DEFAULT_KEYWORDS  # noqa: E402

MUSIC = None


def setUpModule():
    global MUSIC
    MUSIC = tempfile.mkdtemp(prefix="nasbox-music-")
    subprocess.run(["sh", os.path.join(HERE, "make_audio.sh"), MUSIC], check=True, stdout=subprocess.DEVNULL)


def tearDownModule():
    shutil.rmtree(MUSIC, ignore_errors=True)


async def wait_for(cond, timeout=10.0, step=0.05, msg=""):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return
        await asyncio.sleep(step)
    raise AssertionError(f"等待超时：{msg}")


class Base(unittest.IsolatedAsyncioTestCase):
    logged_in = True

    async def asyncSetUp(self):
        self.mock = MockCloud()
        base = await self.mock.start()
        self.data = tempfile.mkdtemp(prefix="nasbox-data-")
        for k in ("ACCOUNT", "MINA", "PROFILE", "MIIO"):
            os.environ[f"NASBOX_{k}_BASE"] = base
        os.environ["NASBOX_MUSIC_DIR"] = MUSIC
        os.environ["NASBOX_DATA_DIR"] = self.data
        os.environ["NASBOX_PORT"] = "0"
        self.app = App(Paths())
        self.cloud = self.app.cloud
        self.cloud.confirm_delay = 0.05
        self.cloud.retry_delay = 0.05
        if self.logged_in:
            self.cloud.creds.update(passToken=self.mock.pass_token, userId=self.mock.user_id)
            self.cloud._save()
        await self.app.lib.scan()

    async def asyncTearDown(self):
        for p in self.app.players.players.values():
            p.gen += 1
            if p._monitor:
                p._monitor.cancel()
        await wait_for(lambda: not self.app.media.jobs, msg="转码任务收尾")
        await self.cloud.close()
        await self.mock.stop()
        self.app.lib.db.close()
        shutil.rmtree(self.data, ignore_errors=True)

    def cred_file(self) -> dict:
        with open(self.app.paths.cred_file, encoding="utf-8") as f:
            return json.load(f)

    def sid(self, title: str) -> int:
        return self.app.lib.search(title)[0]["id"]


# ============================================================ 1. 凭据与续期
class TestAuth(Base):
    async def test_01_service_token_expired_auto_renew(self):
        devs = await self.cloud.refresh_devices()
        self.assertEqual(devs[0]["hardware"], "OH2P")
        self.assertEqual(self.mock.renew_count, 1)
        tok1 = self.cred_file()["services"]["micoapi"]["serviceToken"]
        self.mock.valid_service_tokens.clear()          # 模拟 serviceToken 过期
        devs = await self.cloud.refresh_devices()       # 应当静默续期后成功
        self.assertEqual(len(devs), 2)
        self.assertEqual(self.mock.renew_count, 2)
        tok2 = self.cred_file()["services"]["micoapi"]["serviceToken"]
        self.assertNotEqual(tok1, tok2)
        self.assertEqual(self.cloud.state(), "ok")

    async def test_02_concurrent_requests_renew_once(self):
        await self.cloud.refresh_devices()
        self.mock.valid_service_tokens.clear()
        n0 = self.mock.renew_count
        res = await asyncio.gather(*[self.cloud.get_status("mock-device-0001") for _ in range(8)])
        self.assertEqual(len(res), 8)
        self.assertEqual(self.mock.renew_count - n0, 1, "并发失效只应续期一次")

    async def test_03_network_flap_keeps_credentials(self):
        await self.cloud.refresh_devices()
        before = open(self.app.paths.cred_file, "rb").read()
        port = self.mock.port
        await self.mock.stop()                           # 断网：连接被拒绝
        for _ in range(3):
            with self.assertRaises(NetworkError):
                await self.cloud.refresh_devices()
        self.assertEqual(self.cloud.state(), "network_error")
        self.assertEqual(open(self.app.paths.cred_file, "rb").read(), before, "网络错误不得改动凭据文件")
        self.assertTrue(self.cloud.logged_in)
        tokens = set(self.mock.valid_service_tokens)
        await self.mock.start(port)                      # 网络恢复
        self.mock.valid_service_tokens = tokens
        await self.cloud.refresh_devices()
        self.assertEqual(self.cloud.state(), "ok")
        self.assertEqual(self.mock.renew_count, 1, "网络恢复后不需要重新登录")

    async def test_04_account_server_5xx_during_renew(self):
        await self.cloud.refresh_devices()
        self.mock.valid_service_tokens.clear()
        self.mock.account_503 = True                     # token 过期 + 账号服务器故障
        with self.assertRaises(NetworkError):
            await self.cloud.refresh_devices()
        self.assertFalse(self.cloud.need_qr)
        self.assertEqual(self.cred_file()["passToken"], "PASS-TOKEN-ORIGINAL-0001")
        self.assertIn("micoapi", self.cred_file()["services"], "续期失败时旧 serviceToken 也要保留")
        # 退避期内不再打账号服务器
        calls = self.mock.login_calls
        with self.assertRaises(NetworkError):
            await self.cloud.refresh_devices()
        self.assertEqual(self.mock.login_calls, calls)
        self.mock.account_503 = False
        self.cloud._renew_next_allowed = 0
        await self.cloud.refresh_devices()
        self.assertEqual(self.cloud.state(), "ok")

    async def test_05_unknown_login_error_is_not_need_qr(self):
        await self.cloud.refresh_devices()
        self.mock.valid_service_tokens.clear()
        self.mock.login_weird_code = 10031
        with self.assertRaises(CloudError) as cm:
            await self.cloud.refresh_devices()
        self.assertNotIsInstance(cm.exception, NeedLogin)
        self.assertFalse(self.cloud.need_qr)
        self.assertEqual(self.cred_file()["passToken"], "PASS-TOKEN-ORIGINAL-0001")

    async def test_06_passtoken_invalid_then_qr_relogin(self):
        await self.cloud.refresh_devices()
        self.mock.valid_service_tokens.clear()
        self.mock.pass_token = "REVOKED-ON-SERVER"       # passToken 在服务端失效
        calls = self.mock.login_calls
        with self.assertRaises(NeedLogin):
            await self.cloud.refresh_devices()
        self.assertEqual(self.mock.login_calls - calls, 2, "应当两次确认后才判定失效")
        self.assertEqual(self.cloud.state(), "need_qr")
        self.assertEqual(self.cloud.status()["label"], "需要重新扫码")
        cf = self.cred_file()
        self.assertEqual(cf["passToken"], "PASS-TOKEN-ORIGINAL-0001", "旧凭据不删除")
        baks = [f for f in os.listdir(self.data) if ".bak-needqr" in f]
        self.assertEqual(len(baks), 1, "应当留有备份")
        # 重启后仍然是 need_qr（而不是变回未登录）
        from nasbox.micloud import MiCloud
        c2 = MiCloud(self.app.paths, self.app.settings)
        self.assertEqual(c2.state(), "need_qr")
        # 扫码重新登录
        dev_id, ua = cf["deviceId"], cf["ua"]
        qr = await self.cloud.start_qr()
        self.assertEqual(qr["state"], "pending")
        self.assertTrue(qr["png"].startswith("data:image/png;base64,"))
        self.mock.qr_scanned.set()
        await wait_for(lambda: self.cloud.qr["state"] == "ok", msg="扫码登录完成")
        self.assertEqual(self.cloud.state(), "ok")
        cf = self.cred_file()
        self.assertEqual(cf["passToken"], "PASS-TOKEN-AFTER-QR-0002")
        self.assertEqual((cf["deviceId"], cf["ua"]), (dev_id, ua), "deviceId / UA 不变")
        self.assertTrue(any(".bak-relogin" in f for f in os.listdir(self.data)))
        self.assertEqual(len(self.cloud.devices), 2)

    async def test_07_fixed_identity_and_file_mode(self):
        await self.cloud.refresh_devices()
        self.mock.valid_service_tokens.clear()
        await self.cloud.refresh_devices()
        self.assertEqual(len(self.mock.seen_ua), 1, "UA 必须固定")
        self.assertEqual(len(self.mock.seen_device_ids), 1, "deviceId 必须固定")
        mode = stat.S_IMODE(os.stat(self.app.paths.cred_file).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertFalse([f for f in os.listdir(self.data) if f.startswith(".tmp-")], "不留临时文件")

    async def test_08_tts_uses_miot_action_for_oh2p(self):
        await self.cloud.refresh_devices()
        await self.cloud.tts("mock-device-0001", "你好 世界")
        self.assertEqual(self.mock.miot_calls[0]["params"]["siid"], 7)
        self.assertEqual(self.mock.miot_calls[0]["params"]["aiid"], 3)
        self.assertEqual(self.mock.miot_calls[0]["params"]["did"], "100200300")


class TestQrFirstLogin(Base):
    logged_in = False

    async def test_first_login_by_qr(self):
        self.assertEqual(self.cloud.state(), "no_login")
        with self.assertRaises(NeedLogin):
            await self.cloud.refresh_devices()
        qr = await self.cloud.start_qr()
        self.assertEqual(qr["state"], "pending")
        self.assertNotIn("lp", qr)
        self.mock.qr_scanned.set()
        await wait_for(lambda: self.cloud.qr["state"] == "ok", msg="扫码")
        self.assertEqual(self.cloud.state(), "ok")
        self.assertEqual(self.cloud.devices[0]["name"], "客厅音箱")


# ============================================================ 2. 切歌状态机
class TestPlayer(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.cloud.refresh_devices()
        self.app.settings.data["status_poll_sec"] = 0.3
        self.app.settings.data["end_grace_sec"] = 0.5
        self.app.settings.data["base_url"] = "http://192.168.1.10:8090"
        self.player = self.app.players.get()
        self.sp = self.mock.speaker
        self.ids = [self.sid("晴天"), self.sid("十年"), self.sid("带封面")]   # 都是 2 秒的 mp3/ogg

    def played(self):
        return [int(u.rsplit("/", 1)[1].split(".")[0]) for u in self.sp.play_log]

    async def test_01b_loop_mode_pinned_to_list_loop(self):
        """小米自有播放改动循环模式后，NAS 下发播放应把它固定回列表循环（1）。"""
        self.sp.loop_type = 2
        await self.player.play_queue(self.ids, 0, "测试")
        await wait_for(lambda: self.player.state == "playing", msg="开始播放")
        self.assertEqual(self.sp.loop_type, 1)

    async def test_01_advance_by_speaker_status(self):
        self.app.settings.data["play_mode"] = "seq"
        t0 = time.monotonic()
        await self.player.play_queue(self.ids, 0, "测试")
        self.assertIn("player_play_music", self.sp.ops, "OH2P 应使用 player_play_music")
        await wait_for(lambda: self.player.state == "playing", msg="开始播放")
        await wait_for(lambda: len(self.sp.play_log) >= 3, timeout=12, msg="依次播到第三首")
        self.assertEqual(self.played()[:3], self.ids)
        await wait_for(lambda: self.player.state == "idle", timeout=8, msg="顺序模式播完停止")
        el = time.monotonic() - t0
        self.assertTrue(5.5 < el < 11, f"三首 2 秒的歌总用时 {el:.1f}s")
        self.assertEqual(len(self.sp.play_log), 3)

    async def test_02_no_position_reported(self):
        self.sp.report_position = False
        self.sp.end_status = 0
        await self.player.play_queue(self.ids, 0, "测试")
        await wait_for(lambda: len(self.sp.play_log) >= 2, timeout=8, msg="无进度上报时也能切歌")
        self.assertEqual(self.played()[:2], self.ids[:2])

    async def test_02b_refetch_from_start_means_finished(self):
        """音箱不报进度、播完自己从头重放：重新拉流应立即触发切歌，开播阶段的拉流不算。"""
        self.sp.report_position = False
        self.sp.loop_self = True
        self.app.settings.data["end_grace_sec"] = 30      # 把兜底定时器推远，确保是拉流信号起的作用
        await self.player.play_queue(self.ids, 0, "测试")
        await wait_for(lambda: self.player.state == "playing", msg="开始播放")
        self.app.media.on_fetch(self.ids[0], True)        # 刚开播：不应切歌
        await asyncio.sleep(0.3)
        self.assertEqual(len(self.sp.play_log), 1)
        self.player._play_acc += 1.75                     # 快进到接近结尾
        self.app.media.on_fetch(self.ids[1], True)        # 别的歌：不算
        self.app.media.on_fetch(self.ids[0], False)       # 断点续传：不算
        await asyncio.sleep(0.2)
        self.assertEqual(len(self.sp.play_log), 1)
        self.app.media.on_fetch(self.ids[0], True)
        await wait_for(lambda: len(self.sp.play_log) >= 2, timeout=2, msg="重新拉流触发切歌")
        self.assertEqual(self.played()[:2], self.ids[:2])

    async def test_02c_no_position_resume_restarts_from_zero(self):
        """不报进度的音箱：点暂停再点播放 = 从头重放并进度归零；被打断后自行恢复、曲中重新拉流也归零。"""
        self.sp.report_position = False
        self.sp.sim_duration_ms = 60000
        for i in self.ids:
            self.app.lib.song(i)
        await self.player.play_queue(self.ids, 0, "测试")
        self.player.song["duration"] = 60
        await wait_for(lambda: self.player.state == "playing", msg="开始播放")
        self.player._play_acc += 20
        await self.player.pause()
        self.assertEqual(self.player.state, "paused")
        await self.player.resume()
        self.assertEqual(len(self.sp.play_log), 2, "应重新下发播放而不是发 play 操作")
        self.assertEqual(self.played()[:2], [self.ids[0], self.ids[0]])
        self.player.song["duration"] = 60
        await wait_for(lambda: self.player.state == "playing", msg="重新开始播放")
        self.assertLess(self.player.position(), 5, "进度应归零")
        # 被打断（外部暂停）后音箱自行恢复：进度归零
        self.player._play_acc += 25
        self.sp.op("pause")
        await wait_for(lambda: self.player.state == "paused", timeout=5, msg="识别为外部暂停")
        self.sp.op("play")
        await wait_for(lambda: self.player.state == "playing", timeout=9, msg="识别到恢复")
        self.assertLess(self.player.position(), 5, "打断恢复后进度应归零")
        # 曲中从头重新拉流：进度归零，不切歌
        self.player._play_acc += 25
        self.app.media.on_fetch(self.ids[0], True)
        self.assertLess(self.player.position(), 5)
        self.assertEqual(len(self.sp.play_log), 2)

    async def test_02c2_manual_pause_stays_paused(self):
        """手动暂停后音箱仍上报播放中：保持暂停，进度不走、不切歌；从头重新拉流 + 播放中才算恢复。"""
        self.sp.report_position = False
        self.sp.sim_duration_ms = 60000
        await self.player.play_queue(self.ids, 0, "测试")
        self.player.song["duration"] = 60
        await wait_for(lambda: self.player.state == "playing", msg="开始播放")
        self.player._play_acc += 20
        self.player.fast = 0.05                 # 暂停状态下也快速轮询
        await self.player.pause()
        self.sp.status = 1                      # 音箱谎报「播放中」
        pos = self.player.position()
        await asyncio.sleep(1.5)
        self.assertEqual(self.player.state, "paused", "手动暂停后不应自行恢复")
        self.assertAlmostEqual(self.player.position(), pos, delta=0.5)
        self.assertEqual(len(self.sp.play_log), 1, "不应切歌")
        self.app.media.on_fetch(self.ids[0], True)
        await wait_for(lambda: self.player.state == "playing", timeout=9, msg="重新拉流后识别到恢复")
        self.assertLess(self.player.position(), 5, "恢复后进度应归零")

    async def test_02d_yield_when_speaker_plays_other_content(self):
        """音箱改播别的内容：让出控制，不再发指令；音箱回头重新拉我们的歌则恢复。"""
        self.sp.sim_duration_ms = 60000
        await self.player.play_queue(self.ids, 0, "测试")
        self.player.song["duration"] = 60
        self.app.media.on_fetch(self.ids[0], True)
        await wait_for(lambda: self.player.state == "playing" and self.player._own_aid, timeout=6, msg="记住自己的 audio_id")
        ops_before = len(self.sp.ops)
        self.sp.audio_id = "9900112233"                   # 小爱自己的曲库接管了
        await wait_for(lambda: self.player.state == "idle" and self.player._yielded, timeout=6, msg="识别被接管")
        await asyncio.sleep(1.0)
        self.assertEqual(len(self.sp.ops), ops_before, "让出后不应再给音箱发任何指令")
        self.assertEqual(len(self.sp.play_log), 1)
        self.assertIn("其他内容", self.player.snapshot().get("error", "") or self.player.error)
        # 语音触发的让出 + 音箱回头重新拉流 → 恢复
        self.sp.audio_id = "1582971365183456177"
        await self.player.play_queue(self.ids, 0, "测试")
        self.player.song["duration"] = 60
        await wait_for(lambda: self.player.state == "playing", msg="重新播放")
        from nasbox.voice import parse_command
        self.assertIsNone(parse_command("播放周杰伦的歌", self.app.settings["keywords"]) or None)
        await self.app.voice.handle(self.player.device_id, "今天天气怎么样")
        self.assertEqual(self.player.state, "playing", "普通问答不应让出")
        await self.app.voice.handle(self.player.device_id, "播放林俊杰的歌")
        self.assertTrue(self.player._yielded and self.player.state == "idle")
        self.app.media.on_fetch(self.ids[0], True)
        self.assertEqual(self.player.state, "playing", "音箱回头重新拉流应恢复控制")
        self.assertLess(self.player.position(), 3)

    async def test_02e_speaker_powered_off(self):
        """音箱关机：很快判定离线并停止（不无限重试、不切歌），在线状态变离线；开机后探测恢复，点播放从当前这首继续。"""
        self.sp.sim_duration_ms = 60000
        await self.player.play_queue(self.ids, 1, "测试")
        self.player.song["duration"] = 60
        await wait_for(lambda: self.player.state == "playing", msg="开始播放")
        self.player.fast = 0.02
        self.mock.device_off = True
        await wait_for(lambda: self.player.state == "idle", timeout=8, msg="判定离线并停止")
        self.assertIn("没有响应", self.player.error)
        self.assertFalse(self.cloud.devices[0]["online"], "在线状态应变为离线")
        self.assertEqual(len(self.sp.play_log), 1, "离线后不应切歌或重发")
        self.assertEqual(self.player.index, 1, "队列位置保留")
        # 离线时点播放：很快失败并停下，不进入无限重试
        await self.player.resume()
        await wait_for(lambda: self.player.state == "idle", timeout=8, msg="离线时点播放应很快停下")
        self.assertEqual(len(self.sp.play_log), 1)
        await self.cloud.refresh_devices()
        self.assertFalse(self.cloud.devices[0]["online"], "云端列表仍报在线时也保持离线判定")
        # 开机：后台探测恢复在线，但不自动播放
        self.mock.device_off = False
        await wait_for(lambda: self.cloud.devices[0]["online"], timeout=8, msg="探测到恢复在线")
        self.assertEqual(self.player.state, "idle")
        self.assertEqual(self.player.error, "")
        self.player.fast = 1.0
        await self.player.resume()
        await wait_for(lambda: len(self.sp.play_log) == 2, timeout=5, msg="点播放后从当前这首继续")
        self.assertEqual(self.played()[1], self.ids[1])

    async def test_02f_talk_interrupt_then_replay(self):
        """播放中调音量把歌打断：小爱答完后从头重放当前这首；口头叫停则不重放；没被打断的闲聊不重放。"""
        self.app.settings.data["voice_resume_wait_sec"] = 0.6
        self.sp.sim_duration_ms = 60000
        await self.player.play_queue(self.ids, 1, "测试")
        self.player.song["duration"] = 60
        await wait_for(lambda: self.player.state == "playing", msg="开始播放")
        dev = self.player.device_id
        # 闲聊但音箱没停：不重放
        await self.app.voice.handle(dev, "今天天气怎么样", "晴")
        await asyncio.sleep(2.0)
        self.assertEqual(len(self.sp.play_log), 1)
        self.assertEqual(self.player.state, "playing")
        # 调音量把歌打断了
        self.sp.op("pause")
        await self.app.voice.handle(dev, "声音小一点", "好的")
        await wait_for(lambda: self.player.state == "paused", timeout=3, msg="进度条应很快停住")
        await wait_for(lambda: len(self.sp.play_log) == 2, timeout=6, msg="答完后重放当前这首")
        self.assertEqual(self.played()[1], self.ids[1])
        await wait_for(lambda: self.player.state == "playing", msg="重放开始")
        self.assertLess(self.player.position(), 3)
        # 口头叫停：立刻按暂停处理，不重放
        self.sp.op("pause")
        await self.app.voice.handle(dev, "别放了")
        self.assertEqual(self.player.state, "paused")
        await asyncio.sleep(2.5)
        self.assertEqual(len(self.sp.play_log), 2)
        self.assertEqual(self.player.state, "paused")
        # 关掉开关后不重放
        await self.player.resume()
        await wait_for(lambda: self.player.state == "playing" and self.sp.status == 1, msg="重新播放")
        n = len(self.sp.play_log)
        self.app.settings.data["voice_resume_after_talk"] = False
        self.sp.op("pause")
        await self.app.voice.handle(dev, "音量调到三十")
        await asyncio.sleep(2.5)
        self.assertEqual(len(self.sp.play_log), n)

    async def test_02g_voice_queue(self):
        lib = self.app.lib
        titles = lambda ids: [lib.song(i)["title"] for i in ids]
        ids, idx, src, _ = lib.voice_queue("周杰伦的歌")
        self.assertEqual((sorted(titles(ids)), src), (sorted(["晴天", "东风破", "以父之名"]), "歌手：周杰伦"))
        ids, idx, src, _ = lib.voice_queue("陈奕迅")
        self.assertEqual((sorted(titles(ids)), src), (sorted(["十年", "浮夸"]), "歌手：陈奕迅"))
        ids, idx, src, _ = lib.voice_queue("叶惠美")
        self.assertEqual((titles(ids), src), (["晴天", "东风破", "以父之名"], "专辑：叶惠美"))
        ids, idx, src, _ = lib.voice_queue("周杰伦的东风破")
        self.assertEqual((titles(ids)[idx], src), ("东风破", "搜索：周杰伦的东风破"))
        self.assertEqual(len(ids), 3)
        self.assertIsNone(lib.voice_queue("完全不存在的东西啊啊"))
        await self.app.voice.handle(self.player.device_id, "播放歌曲陈奕迅的歌")
        self.assertEqual(self.player.source, "歌手：陈奕迅")
        self.assertEqual(len(self.player.queue), 2)

    async def test_02h_nas_first(self):
        """没说口令关键词：曲库里完全一致就由 NAS 播，否则交给小爱。"""
        v, dev, lib = self.app.voice, self.player.device_id, self.app.lib
        title = lambda: self.player.song["title"]
        self.assertTrue(await v.handle(dev, "播放周杰伦的歌"))
        self.assertEqual((self.player.source, len(self.player.queue)), ("歌手：周杰伦", 3))
        self.assertTrue(await v.handle(dev, "我想听陈奕迅的十年"))
        self.assertEqual(title(), "十年")
        self.assertTrue(await v.handle(dev, "来一首东风破吧"))
        self.assertEqual(title(), "东风破")
        self.assertTrue(await v.handle(dev, "播放叶惠美"))
        self.assertEqual(self.player.source, "专辑：叶惠美")
        self.assertTrue(await v.handle(dev, "播放轻音乐"))
        self.assertEqual(self.player.source, "歌单：轻音乐")
        n = len(self.sp.play_log)
        # 曲库里没有 / 只是相似 / 泛指 / 调音量：不接管
        for q in ("播放波西和皮普", "播放林俊杰的歌", "播放晴", "播放音乐", "放小声一点", "播放周杰伦的稻香"):
            self.assertEqual(await v.handle(dev, q), "", q)
        self.assertEqual(len(self.sp.play_log), n)
        self.assertTrue(self.player._yielded, "没有的内容交给小爱并让出控制")
        # 空闲时也接管
        self.assertEqual(self.player.state, "idle")
        self.assertTrue(await v.handle(dev, "播放晴天"))
        self.assertEqual(title(), "晴天")
        # 关掉开关
        self.app.settings.data["voice_nas_first"] = False
        n = len(self.sp.play_log)
        self.assertEqual(await v.handle(dev, "播放陈奕迅的歌"), "")
        self.assertEqual(len(self.sp.play_log), n)
        # 「播放歌曲 X歌」不再把名字末尾的「歌」吃掉
        self.assertIsNone(lib.voice_queue("儿歌", strict=True))

    async def test_03_status_api_down_fallback_timer(self):
        self.mock.status_down = True
        t0 = time.monotonic()
        await self.player.play_queue(self.ids, 0, "测试")
        await wait_for(lambda: len(self.sp.play_log) >= 2, timeout=10, msg="兜底定时器切歌")
        self.assertGreater(time.monotonic() - t0, 2.0, "不应早于歌曲时长切歌")

    async def test_04_external_pause_does_not_skip(self):
        long_id = self.sid("长曲")                       # 12 秒
        self.sp.sim_duration_ms = 12000
        await self.player.play_queue([long_id, self.ids[0]], 0, "测试")
        await wait_for(lambda: self.player.state == "playing", msg="开始播放")
        await asyncio.sleep(1.0)
        self.sp.op("pause")                              # 用户直接对小爱说了暂停 / 被打断
        await wait_for(lambda: self.player.state == "paused", timeout=5, msg="识别为外部暂停")
        self.assertTrue(self.player._ext_paused)
        await asyncio.sleep(1.5)
        self.assertEqual(len(self.sp.play_log), 1, "曲中停止不应切歌")
        self.sp.op("play")                               # 音箱恢复
        await wait_for(lambda: self.player.state == "playing", timeout=8, msg="恢复后继续跟踪")
        self.assertEqual(len(self.sp.play_log), 1)

    async def test_05_play_command_retry(self):
        self.mock.play_fail = 2
        await self.player.play_queue(self.ids, 0, "测试")
        await wait_for(lambda: self.player.state == "playing", timeout=10, msg="重试后播放成功")
        self.assertEqual(len(self.sp.play_log), 1)

    async def test_06_play_keeps_retrying_and_queue(self):
        self.player.fast = 0.05
        self.mock.play_fail = 7
        await self.player.play_queue(self.ids, 0, "测试")
        self.assertEqual(self.player.state, "retrying")
        self.assertEqual(self.player.queue, self.ids, "失败不丢队列")
        await wait_for(lambda: self.player.state == "playing", timeout=10, msg="周期重试最终成功")

    async def test_07_speaker_self_loop_detected(self):
        self.sp.loop_self = True
        await self.player.play_queue(self.ids, 0, "测试")
        await wait_for(lambda: len(self.sp.play_log) >= 2, timeout=8, msg="音箱自循环时仍然切歌")
        self.assertEqual(self.played()[:2], self.ids[:2])

    async def test_08_single_loop_mode(self):
        self.app.settings.data["play_mode"] = "one"
        await self.player.play_queue(self.ids, 0, "测试")
        await wait_for(lambda: len(self.sp.play_log) >= 2, timeout=8, msg="单曲循环重播")
        self.assertEqual(self.played()[:2], [self.ids[0], self.ids[0]])
        await self.player.next()                         # 手动下一首仍然换歌
        self.assertEqual(self.played()[-1], self.ids[1])

    async def test_09_speaker_never_starts_resend_then_skip(self):
        self.player.fast = 0.05
        self.sp.never_start = True
        await self.player.play_queue(self.ids, 0, "测试")
        await wait_for(lambda: self.player.state == "idle", timeout=20, msg="多首无法开始后停止并给出原因")
        self.assertIn("拉流地址", self.player.error)
        self.assertGreaterEqual(self.sp.play_log.count(self.sp.play_log[0]), 3, "跳过前应重发")

    async def test_10_token_expiry_mid_playback(self):
        await self.player.play_queue(self.ids, 0, "测试")
        await wait_for(lambda: self.player.state == "playing", msg="开始播放")
        self.mock.valid_service_tokens.clear()           # 播放中途 token 过期
        await wait_for(lambda: len(self.sp.play_log) >= 2, timeout=10, msg="过期后自动续期并继续切歌")
        self.assertEqual(self.cloud.state(), "ok")

    async def test_11_controls(self):
        self.sp.sim_duration_ms = 12000
        await self.player.play_queue([self.sid("长曲")] + self.ids, 0, "测试")
        await wait_for(lambda: self.player.state == "playing", msg="开始播放")
        await self.player.pause()
        self.assertEqual(self.sp.status, 2)
        await self.player.resume()
        self.assertEqual(self.sp.status, 1)
        await self.player.set_volume(55)
        self.assertEqual(self.sp.volume, 55)
        await self.player.volume_step(-1)
        self.assertEqual(self.sp.volume, 50)
        self.player.set_sleep(0.01)                      # 0.6 秒后定时关闭
        await wait_for(lambda: self.player.state == "idle", timeout=6, msg="定时关闭")
        self.assertEqual(self.sp.status, 0)


# ============================================================ 3. 语音口令
class TestVoice(Base):
    def test_parse(self):
        kw = DEFAULT_KEYWORDS
        cases = {
            "播放歌曲": ("play_song", ""),
            "播放歌曲晴天": ("play_song", "晴天"),
            "播放歌曲，周杰伦的晴天。": ("play_song", "周杰伦的晴天"),
            "请播放歌曲十年": ("play_song", "十年"),
            "播放歌单叶惠美": ("play_list", "叶惠美"),
            "播放歌单 收藏": ("play_list", "收藏"),
            "上一首": ("prev", ""), "下一首": ("next", ""), "下一首歌": ("next", ""),
            "暂停": ("pause", ""), "暂停播放": ("pause", ""), "继续": ("resume", ""), "继续播放": ("resume", ""),
            "停止": ("stop", ""), "停止播放": ("stop", ""),
            "单曲循环": ("mode_one", ""), "全部循环": ("mode_all", ""), "随机播放": ("mode_random", ""),
            "加入收藏": ("fav_add", ""), "取消收藏": ("fav_del", ""),
            "今天天气怎么样": None, "播放周杰伦的歌": None, "明天下一首歌发布会是几点": None, "": None,
        }
        for q, want in cases.items():
            self.assertEqual(parse_command(q, kw), want, q)
        self.assertEqual(parse_command("来一首晴天", dict(kw, play_song=["来一首"])), ("play_song", "晴天"))

    async def test_poll_and_execute(self):
        await self.cloud.refresh_devices()
        self.app.settings.data["base_url"] = "http://192.168.1.10:8090"
        self.app.settings.data["status_poll_sec"] = 0.3
        v, sp = self.app.voice, self.mock.speaker
        sp.sim_duration_ms = 12000
        dev = "mock-device-0001"
        self.mock.say("播放歌曲东风破")                     # 启动之前的历史记录不应被执行
        await v.poll_once(dev)
        self.assertEqual(sp.play_log, [])
        await asyncio.sleep(0.01)
        self.mock.say("播放歌曲周杰伦的晴天", "为你播放周杰伦的晴天")
        await v.poll_once(dev)
        self.assertEqual(len(sp.play_log), 1)
        self.assertIn(f"/{self.sid('晴天')}.mp3", sp.play_log[0])
        self.assertLess(sp.ops.index("pause"), sp.ops.index("player_play_music"), "应先打断小爱再播放")
        player = self.app.players.get()
        self.assertEqual([self.app.lib.song(i)["title"] for i in player.queue], ["晴天", "东风破", "以父之名"])
        await v.poll_once(dev)                           # 同一条记录不重复执行
        self.assertEqual(len(sp.play_log), 1)
        for q, check in [("下一首", lambda: player.song["title"] == "东风破"),
                         ("上一首", lambda: player.song["title"] == "晴天"),
                         ("加入收藏", lambda: self.sid("晴天") in self.app.lib.favorite_ids()),
                         ("取消收藏", lambda: self.sid("晴天") not in self.app.lib.favorite_ids()),
                         ("单曲循环", lambda: self.app.settings["play_mode"] == "one"),
                         ("全部循环", lambda: self.app.settings["play_mode"] == "all"),
                         ("暂停", lambda: player.state == "paused"),
                         ("继续", lambda: player.state in ("playing", "starting")),
                         ("播放歌单陈奕迅", lambda: player.source == "歌单：陈奕迅" and len(player.queue) == 2),
                         ("停止", lambda: player.state == "idle")]:
            await asyncio.sleep(0.01)
            self.mock.say(q)
            await v.poll_once(dev)
            self.assertTrue(check(), f"口令「{q}」未生效")
        # 空闲时不抢小爱自己的「下一首 / 暂停」
        n = len(sp.ops)
        await asyncio.sleep(0.01)
        self.mock.say("下一首")
        await v.poll_once(dev)
        self.assertEqual(len(sp.ops), n)
        # 非口令不处理
        self.mock.say("今天天气怎么样")
        await v.poll_once(dev)
        self.assertEqual(len(sp.ops), n)

    async def test_adaptive_interval_and_disable(self):
        v = self.app.voice
        self.assertEqual(v._interval(), self.app.settings["voice_poll_idle_sec"])
        v.last_hit = time.time()
        self.assertEqual(v._interval(), self.app.settings["voice_poll_sec"])
        await self.cloud.refresh_devices()
        self.app.settings.data["voice_enabled"] = False
        v.fast = 0.01
        v.start()
        await asyncio.sleep(0.5)
        self.assertEqual(self.mock.conv_calls, 0, "关闭后不应再轮询对话记录")
        self.app.settings.data["voice_enabled"] = True
        await wait_for(lambda: self.mock.conv_calls > 0, msg="开启后恢复轮询")
        v._task.cancel()


# ============================================================ 4. 曲库
class TestLibrary(Base):
    async def test_scan_tags_search(self):
        lib = self.app.lib
        self.assertEqual(lib.count(), 8)
        s = lib.song(self.sid("东风破"))
        self.assertEqual((s["artist"], s["album"], s["ext"], s["track"]), ("周杰伦", "叶惠美", "flac", 2))
        self.assertAlmostEqual(s["duration"], 2.0, delta=0.3)
        for title in ("晴天", "东风破", "以父之名", "十年", "浮夸"):
            self.assertEqual(lib.search(title)[0]["title"], title)
        self.assertEqual(lib.search("无标签的歌")[0]["title"], "无标签的歌")      # 退回文件名
        self.assertEqual(lib.search("周杰伦的晴天")[0]["title"], "晴天")
        self.assertEqual(lib.search("周杰伦 东风破")[0]["title"], "东风破")
        self.assertEqual({x["title"] for x in lib.search("陈奕迅")}, {"十年", "浮夸"})
        self.assertEqual(lib.search("叶惠美")[0]["album"], "叶惠美")
        self.assertEqual(lib.search("东风坡")[0]["title"], "东风破")              # 错一个字
        self.assertEqual(lib.search("ＱＩＮＧ"), [])
        self.assertEqual(lib.search("完全不存在的歌名xyz"), [])
        self.assertEqual({f["name"] for f in lib.folders()}, {"叶惠美", "陈奕迅", "轻音乐"})
        self.assertIsNotNone(lib.cover(self.sid("带封面")))
        self.assertIsNotNone(lib.cover(self.sid("晴天")))                         # 目录 cover.png
        self.assertIsNone(lib.cover(self.sid("十年")))

    async def test_incremental_rescan_and_lists(self):
        lib = self.app.lib
        pid = lib.playlist_create("我的最爱")
        lib.playlist_add(pid, [self.sid("晴天"), self.sid("十年")])
        lib.set_favorite(self.sid("晴天"), True)
        id_before = self.sid("晴天")
        await lib.scan()
        self.assertEqual(lib.scan_progress["total"], 0, "没有变化时不应重读任何文件")
        self.assertEqual(self.sid("晴天"), id_before)
        new = os.path.join(MUSIC, "轻音乐", "新歌.wav")
        shutil.copy(os.path.join(MUSIC, "轻音乐", "无标签的歌.wav"), new)
        try:
            await lib.scan()
            self.assertEqual((lib.scan_progress["added"], lib.count()), (1, 9))
        finally:
            os.unlink(new)
        await lib.scan()
        self.assertEqual((lib.scan_progress["removed"], lib.count()), (1, 8))
        self.assertEqual(len(lib.playlist_ids(pid)), 2)
        self.assertEqual(lib.favorite_ids(), [id_before])
        self.assertEqual(lib.find_list("我的最爱")[0], "playlist")
        self.assertEqual(lib.find_list("收藏")[0], "favorites")
        self.assertEqual(lib.find_list("陈奕迅")[0], "folder")
        with self.assertRaises(ValueError):
            lib.full_path("../../etc/passwd")


# ============================================================ 5. HTTP：媒体流 / API / 密码 / 日志
class TestHttp(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.cloud.refresh_devices()
        self.runner = web.AppRunner(self.app.build(), access_log=None)
        # 不触发 on_startup 里的后台任务以外的东西：正常 setup 即可
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.app.settings.data["base_url"] = self.url
        self.http = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.http.close()
        await wait_for(lambda: not self.app.media.jobs, msg="转码任务收尾")
        await self.runner.cleanup()
        await self.mock.stop()
        self.app.lib.db.close()
        shutil.rmtree(self.data, ignore_errors=True)

    async def test_range_requests(self):
        song = self.app.lib.song(self.sid("晴天"))
        url = self.app.media.url_for(song)
        raw = open(os.path.join(MUSIC, song["path"]), "rb").read()
        async with self.http.get(url) as r:               # 不带登录态
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Content-Type"], "audio/mpeg")
            self.assertEqual(r.headers["Accept-Ranges"], "bytes")
            self.assertEqual(await r.read(), raw)
        async with self.http.get(url, headers={"Range": "bytes=100-299"}) as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(r.headers["Content-Range"], f"bytes 100-299/{len(raw)}")
            self.assertEqual(await r.read(), raw[100:300])
        async with self.http.get(url, headers={"Range": "bytes=1000-"}) as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(await r.read(), raw[1000:])
        async with self.http.get(url, headers={"Range": "bytes=-50"}) as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(await r.read(), raw[-50:])
        async with self.http.get(url, headers={"Range": f"bytes={len(raw) + 10}-"}) as r:
            self.assertEqual(r.status, 416)
        async with self.http.head(url) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(int(r.headers["Content-Length"]), len(raw))
        bad = url.replace("/media/" + self.app.media.sign(song["id"]), "/media/" + "0" * 32)
        async with self.http.get(bad) as r:
            self.assertEqual(r.status, 403)
        other = url.replace(f"/{song['id']}.mp3", f"/{self.sid('十年')}.mp3")     # 签名与歌曲绑定
        async with self.http.get(other) as r:
            self.assertEqual(r.status, 403)

    async def test_transcode(self):
        m = self.app.media
        flac, wma, ogg = (self.app.lib.song(self.sid(t)) for t in ("东风破", "浮夸", "十年"))
        self.assertTrue(m.url_for(flac).endswith(".flac"), "默认 flac 直出")
        self.assertTrue(m.url_for(wma).endswith(".mp3"), "默认 wma 转 mp3")
        self.app.settings.data["transcode"] = "all"
        url = m.url_for(flac)
        self.assertTrue(url.endswith(".mp3"))
        async with self.http.get(url) as r:               # 第一次：边转边发
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Content-Type"], "audio/mpeg")
            body = await r.read()
        self.assertGreater(len(body), 5000)
        self.assertTrue(body[:3] == b"ID3" or body[0] == 0xFF, "应为 mp3 数据")
        await wait_for(lambda: os.path.isfile(m._cache_path(flac)), msg="缓存落盘")
        cached = open(m._cache_path(flac), "rb").read()
        self.assertEqual(len(body), len(cached))
        async with self.http.get(url, headers={"Range": "bytes=1000-1999"}) as r:   # 第二次：走缓存，支持 Range
            self.assertEqual(r.status, 206)
            self.assertEqual(await r.read(), cached[1000:2000])
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=format_name,duration",
                                "-of", "json", m._cache_path(flac)], capture_output=True, text=True)
        fmt = json.loads(probe.stdout)["format"]
        self.assertEqual(fmt["format_name"], "mp3")
        self.assertAlmostEqual(float(fmt["duration"]), 2.0, delta=0.3)
        # 未缓存时直接请求中间位置：等转完再给 206
        async with self.http.get(m.url_for(wma), headers={"Range": "bytes=500-"}) as r:
            self.assertEqual(r.status, 206)
        # 并发请求同一首只起一个转码任务
        u = m.url_for(ogg)
        rs = await asyncio.gather(*[self.http.get(u) for _ in range(3)])
        sizes = [len(await r.read()) for r in rs]
        self.assertEqual(len(set(sizes)), 1)
        self.assertFalse([f for f in os.listdir(m.tdir) if f.endswith(".part")])

    async def test_api_flow(self):
        async with self.http.get(self.url + "/") as r:
            self.assertEqual(r.status, 200)
            self.assertIn("NAS 音乐盒", await r.text())
        for path in ("/app.js", "/style.css", "/manifest.webmanifest", "/sw.js", "/icon.svg", "/icon-192.png", "/icon-512.png"):
            async with self.http.get(self.url + path) as r:
                self.assertEqual(r.status, 200, path)
        async with self.http.get(self.url + "/api/status") as r:
            st = await r.json()
        self.assertEqual((st["app"], st["auth"]["state"], st["library"]["count"]), ("nas-music-box", "ok", 8))
        async with self.http.get(self.url + "/api/search", params={"q": "晴天"}) as r:
            sid = (await r.json())["songs"][0]["id"]
        async with self.http.post(self.url + "/api/play", json={"song_id": sid, "source": {"type": "folder", "id": "周杰伦/叶惠美"}}) as r:
            d = await r.json()
            self.assertTrue(d["ok"], d)
        self.assertEqual(d["player"]["song"]["title"], "晴天")
        self.assertEqual(d["player"]["queue_len"], 3)
        self.assertTrue(self.mock.speaker.play_log[0].startswith(self.url + "/media/"))
        async with self.http.get(self.mock.speaker.play_log[0]) as r:      # 音箱视角：地址真的能拉到
            self.assertEqual(r.status, 200)
        for body in ({"action": "volume", "value": 40}, {"action": "next"}, {"action": "mode", "value": "random"},
                     {"action": "pause"}, {"action": "resume"}, {"action": "stop"}):
            async with self.http.post(self.url + "/api/control", json=body) as r:
                self.assertTrue((await r.json())["ok"], body)
        self.assertEqual(self.mock.speaker.volume, 40)
        async with self.http.post(self.url + "/api/tts", json={"text": "开饭了"}) as r:
            self.assertTrue((await r.json())["ok"])
        self.assertEqual(self.mock.miot_calls[-1]["params"]["in"], ["开饭了"])
        async with self.http.post(self.url + "/api/playlists", json={"name": "跑步", "song_ids": [sid]}) as r:
            pid = (await r.json())["id"]
        async with self.http.get(self.url + f"/api/playlists/{pid}") as r:
            self.assertEqual(len((await r.json())["songs"]), 1)
        async with self.http.post(self.url + "/api/favorites", json={"song_id": sid, "on": True}) as r:
            pass
        async with self.http.get(self.url + "/api/favorites") as r:
            self.assertEqual((await r.json())["songs"][0]["id"], sid)
        async with self.http.get(self.url + f"/api/cover/{sid}") as r:
            self.assertEqual(r.status, 200)
        async with self.http.get(self.url + "/api/events") as r:           # SSE 首包
            line = await asyncio.wait_for(r.content.readline(), 3)
            self.assertTrue(line.startswith(b"data: {"))
        async with self.http.post(self.url + "/api/mi/qr/start") as r:
            self.assertEqual((await r.json())["qr"]["state"], "pending")

    async def test_cloud_down_returns_clear_error(self):
        await self.mock.stop()
        async with self.http.post(self.url + "/api/control", json={"action": "volume", "value": 10}) as r:
            self.assertEqual(r.status, 502)
            self.assertIn("连接小米云端失败", (await r.json())["error"])
        async with self.http.get(self.url + "/api/status") as r:
            st = await r.json()
        self.assertEqual(st["auth"]["label"], "网络异常")

    async def test_password_protection(self):
        async with self.http.post(self.url + "/api/settings", json={"password": "s3cret-pw"}) as r:
            self.assertTrue((await r.json())["ok"])
        anon = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        try:
            async with anon.get(self.url + "/") as r:
                self.assertEqual(r.status, 200, "首页始终 200（页面内登录）")
            async with anon.get(self.url + "/api/status") as r:
                self.assertEqual(r.status, 200, "/api/status 免登录")
                st = await r.json()
            self.assertEqual((st["app"], st["need_login"], st["authed"]), ("nas-music-box", True, False))
            self.assertFalse({"devices", "player", "library", "base_url", "current_device"} & set(st), "免登录不返回敏感字段")
            self.assertNotIn("user_id", st["auth"])
            for path in ("/api/songs", "/api/settings", "/api/logs/download", "/api/events"):
                async with anon.get(self.url + path) as r:
                    self.assertEqual(r.status, 401, path)
            async with anon.post(self.url + "/api/login", json={"password": "wrong"}) as r:
                self.assertEqual(r.status, 403)
            url = self.app.media.url_for(self.app.lib.song(self.sid("晴天")))
            async with anon.get(url) as r:
                self.assertEqual(r.status, 200, "音箱拉流不需要登录态")
            async with anon.post(self.url + "/api/login", json={"password": "s3cret-pw"}) as r:
                self.assertEqual(r.status, 200)
            async with anon.get(self.url + "/api/songs") as r:
                self.assertEqual(r.status, 200)
            async with anon.get(self.url + "/api/status") as r:
                self.assertIn("player", await r.json())
        finally:
            await anon.close()
        stored = json.load(open(self.app.paths.settings_file))
        self.assertNotIn("s3cret-pw", json.dumps(stored))

    async def test_logs_never_contain_secrets(self):
        import logging
        self.mock.valid_service_tokens.clear()
        await self.cloud.refresh_devices()               # 触发一次续期，产生日志
        creds = self.cred_file()
        secrets_ = [creds["passToken"], creds["services"]["micoapi"]["serviceToken"],
                    creds["services"]["micoapi"]["ssecurity"]]
        lg = logging.getLogger("nasbox.test")
        # 即使有人误把凭据写进日志，也要被过滤器拦下
        lg.warning("调试: passToken=%s; serviceToken=%s", secrets_[0], secrets_[1])
        lg.warning("Cookie: userId=1; serviceToken=%s", secrets_[1])
        lg.warning('{"ssecurity": "%s", "passToken":"%s"}', secrets_[2], secrets_[0])
        lg.warning("播放地址 %s", self.app.media.url_for(self.app.lib.song(self.sid("晴天"))))
        try:
            raise RuntimeError(f"boom serviceToken={secrets_[1]}")
        except RuntimeError:
            lg.exception("带堆栈的异常")
        for h in logging.getLogger().handlers:
            h.flush()
        text = open(self.app.log_path, encoding="utf-8").read()
        self.assertIn("serviceToken 续期成功", text)
        for s in secrets_:
            self.assertNotIn(s, text)
        self.assertNotIn(self.app.media.sign(self.sid("晴天")), text)
        async with self.http.get(self.url + "/api/logs/download") as r:
            self.assertEqual(r.status, 200)
            z = zipfile.ZipFile(io.BytesIO(await r.read()))
        blob = b"".join(z.read(n) for n in z.namelist()).decode("utf-8", "replace")
        self.assertIn("status.json", z.namelist())
        for s in secrets_ + ["mock-device-0001", "100200300", self.mock.user_id]:
            self.assertNotIn(s, blob)
        self.assertEqual(redact("x passToken=abc123 y"), "x passToken=*** y")


if __name__ == "__main__":
    unittest.main(verbosity=2)
