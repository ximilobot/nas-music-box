"""设置：/data/settings.json，原子写入。环境变量只提供路径与测试用的云端地址覆盖。"""
from __future__ import annotations

import copy
import hashlib
import hmac
import os
import secrets

from .util import atomic_write, atomic_write_json, read_json

DEFAULT_KEYWORDS = {
    "play_song": ["播放歌曲", "放歌曲", "播放本地歌曲"],
    "play_list": ["播放歌单", "播放列表", "播放文件夹"],
    "next": ["下一首", "切歌"],
    "prev": ["上一首"],
    "pause": ["暂停"],
    "resume": ["继续"],
    "stop": ["停止", "停止播放"],
    "mode_one": ["单曲循环"],
    "mode_all": ["全部循环", "列表循环"],
    "mode_random": ["随机播放"],
    "fav_add": ["加入收藏", "收藏歌曲"],
    "fav_del": ["取消收藏"],
}

DEFAULTS = {
    "base_url": "",              # 音箱拉流基地址，如 http://192.168.1.10:8090；留空自动探测
    "learned_base_url": "",      # 由浏览器访问地址学到的局域网地址（自动）
    "current_device": "",        # 当前选中的音箱 deviceID
    "play_api": "auto",          # auto | music | url
    "tts_mode": "auto",          # auto | miot | mina
    "transcode": "auto",         # auto(仅列表内格式) | all(全部转 mp3) | off
    "transcode_formats": ["ape", "wma", "ogg"],
    "transcode_bitrate": "320k",
    "cache_max_mb": 2048,
    "voice_enabled": True,
    "voice_poll_sec": 1.5,       # 活跃时的对话轮询间隔
    "voice_poll_idle_sec": 5.0,  # 空闲较久后的间隔
    "voice_idle_after_min": 30,  # 多久没有播放/口令算空闲
    "voice_tts_feedback": False, # 口令未命中歌曲时是否播报提示
    "voice_nas_first": True,           # 没说口令关键词的点播，曲库里有（完全一致）就由 NAS 播
    "voice_resume_after_talk": True,   # 播放中被普通对话（调音量、问天气）打断后，自动从头重放当前这首
    "voice_resume_wait_sec": 5,        # 小爱答话之外再多等几秒才检查
    "keywords": DEFAULT_KEYWORDS,
    "status_poll_sec": 3.0,      # 播放时轮询音箱状态的间隔
    "keepalive_min": 10,         # 会话校验间隔（分钟）
    "renew_hours": 24,           # serviceToken 提前续期周期（小时）
    "volume_step": 5,
    "play_mode": "all",          # all | one | random | seq
    "end_grace_sec": 3,          # 兜底定时器宽限
    "password_hash": "",         # 访问密码（scrypt），空=不启用
    "theme": "auto",
    "log_level": "INFO",
}

NUM_LIMITS = {
    "voice_poll_sec": (1.0, 30.0), "voice_poll_idle_sec": (1.0, 120.0),
    "voice_idle_after_min": (1, 1440), "status_poll_sec": (1.0, 30.0),
    "keepalive_min": (1, 720), "renew_hours": (1, 720), "volume_step": (1, 20),
    "end_grace_sec": (0, 30), "voice_resume_wait_sec": (2, 60), "cache_max_mb": (64, 1048576),
}


class Paths:
    def __init__(self):
        self.music = os.environ.get("NASBOX_MUSIC_DIR", "/music")
        self.data = os.environ.get("NASBOX_DATA_DIR", "/data")
        self.port = int(os.environ.get("NASBOX_PORT", "8090"))
        self.host = os.environ.get("NASBOX_HOST", "0.0.0.0")
        # 以下仅供测试指向模拟云端
        self.account_base = os.environ.get("NASBOX_ACCOUNT_BASE", "https://account.xiaomi.com")
        self.mina_base = os.environ.get("NASBOX_MINA_BASE", "https://api2.mina.mi.com")
        self.profile_base = os.environ.get("NASBOX_PROFILE_BASE", "https://userprofile.mina.mi.com")
        self.miio_base = os.environ.get("NASBOX_MIIO_BASE", "https://api.io.mi.com/app")
        self.ffmpeg = os.environ.get("NASBOX_FFMPEG", "ffmpeg")

    @property
    def settings_file(self): return os.path.join(self.data, "settings.json")
    @property
    def cred_file(self): return os.path.join(self.data, "credentials.json")
    @property
    def db_file(self): return os.path.join(self.data, "library.db")
    @property
    def secret_file(self): return os.path.join(self.data, "secret.key")
    @property
    def cache_dir(self): return os.path.join(self.data, "cache")
    @property
    def state_file(self): return os.path.join(self.data, "player_state.json")


class Settings:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.data = copy.deepcopy(DEFAULTS)
        stored = read_json(paths.settings_file, {}) or {}
        for k, v in stored.items():
            if k in DEFAULTS:
                self.data[k] = v
        # 新版本新增的口令类别补默认值
        kw = self.data.get("keywords") or {}
        for k, v in DEFAULT_KEYWORDS.items():
            kw.setdefault(k, list(v))
        self.data["keywords"] = kw
        self.secret = self._load_secret()

    def _load_secret(self) -> bytes:
        try:
            with open(self.paths.secret_file, "rb") as f:
                s = f.read()
            if len(s) >= 32:
                return s
        except FileNotFoundError:
            pass
        s = secrets.token_bytes(32)
        atomic_write(self.paths.secret_file, s)
        return s

    def __getitem__(self, k):
        return self.data[k]

    def get(self, k, d=None):
        return self.data.get(k, d)

    def save(self):
        atomic_write_json(self.paths.settings_file, self.data)

    def public(self) -> dict:
        d = copy.deepcopy(self.data)
        d["password_set"] = bool(d.pop("password_hash"))
        return d

    def update(self, patch: dict) -> list[str]:
        """校验并合并设置，返回被修改的键。"""
        changed = []
        for k, v in patch.items():
            if k == "password":
                self.data["password_hash"] = hash_password(v) if v else ""
                changed.append("password_hash")
                continue
            if k not in DEFAULTS or k in ("password_hash", "learned_base_url"):
                continue
            d = DEFAULTS[k]
            if isinstance(d, bool):
                v = bool(v)
            elif isinstance(d, (int, float)):
                try:
                    v = type(d)(v)
                except (TypeError, ValueError):
                    continue
                lo, hi = NUM_LIMITS.get(k, (None, None))
                if lo is not None:
                    v = max(lo, min(hi, v))
            elif isinstance(d, str):
                v = str(v).strip()
                if k == "base_url":
                    v = v.rstrip("/")
                    if v and not v.startswith(("http://", "https://")):
                        v = "http://" + v
                if k == "play_api" and v not in ("auto", "music", "url"): continue
                if k == "tts_mode" and v not in ("auto", "miot", "mina"): continue
                if k == "transcode" and v not in ("auto", "all", "off"): continue
                if k == "play_mode" and v not in ("all", "one", "random", "seq"): continue
            elif isinstance(d, list):
                if isinstance(v, str):
                    v = [x for x in v.replace("，", ",").split(",")]
                v = [str(x).strip().lower().lstrip(".") for x in v if str(x).strip()]
            elif isinstance(d, dict):
                if not isinstance(v, dict):
                    continue
                clean = {}
                for kk in DEFAULT_KEYWORDS:
                    words = v.get(kk, self.data["keywords"].get(kk, []))
                    if isinstance(words, str):
                        words = words.replace("，", ",").split(",")
                    clean[kk] = [str(w).strip() for w in words if str(w).strip()]
                v = clean
            if self.data.get(k) != v:
                self.data[k] = v
                changed.append(k)
        if changed:
            self.save()
        return changed


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return salt.hex() + "$" + h.hex()


def verify_password(pw: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False
