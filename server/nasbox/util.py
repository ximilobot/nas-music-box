"""通用工具：原子写文件、日志脱敏、字符串归一化。"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import socket
import tempfile
import unicodedata

SENSITIVE_KEYS = (
    "passToken", "serviceToken", "ssecurity", "psecurity", "yetAnotherServiceToken",
    "clientSign", "password", "cookie", "Cookie", "token", "nonce", "_sign", "signature",
)
_KEY_RE = re.compile(
    r"(?i)(passtoken|servicetoken|yetanotherservicetoken|ssecurity|psecurity|clientsign|"
    r"password|set-cookie|cookie|signature|_sign|nonce|token)"
    r"(\"?\s*[:=]\s*\"?)([^\s\"&;,}']+)"
)
_MEDIA_RE = re.compile(r"(/media/)[0-9a-f]{16,}")
_LONG_RE = re.compile(r"(?<![A-Za-z0-9/._\-])[A-Za-z0-9+_\-]{48,}={0,2}")


def redact(text: str) -> str:
    """把文本里可能出现的凭据替换成 ***。即使调用方失误把敏感值写进日志也会被拦下。"""
    text = _KEY_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    text = _MEDIA_RE.sub(r"\1***", text)
    text = _LONG_RE.sub("***", text)
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        record.msg = redact(msg)
        record.args = ()
        if record.exc_info:
            # 异常堆栈同样脱敏
            import traceback
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True


def setup_logging(data_dir: str, level: str = "INFO") -> str:
    os.makedirs(os.path.join(data_dir, "logs"), exist_ok=True)
    path = os.path.join(data_dir, "logs", "nasbox.log")
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    filt = RedactFilter()
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    sh = logging.StreamHandler()
    for h in (fh, sh):
        h.setFormatter(fmt)
        h.addFilter(filt)
        root.addHandler(h)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    return path


def atomic_write(path: str, data: bytes | str, mode: int = 0o600) -> None:
    """临时文件 + fsync + rename，保证任何时刻磁盘上都是完整的旧文件或完整的新文件。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, obj, mode: int = 0o600) -> None:
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2), mode)


def read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        logging.getLogger(__name__).warning("读取 %s 失败，文件可能损坏", path)
        return default


_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def norm(s: str) -> str:
    """搜索用归一化：全角转半角、小写、去掉空白和标点。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).lower()
    return _PUNCT_RE.sub("", s)


def detect_lan_ip() -> str:
    """尽力探测本机局域网 IP（不实际发包）。Docker bridge 模式下得到的是容器 IP，不可用。"""
    for target in ("223.5.5.5", "192.168.1.1", "10.255.255.255"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except OSError:
            pass
        finally:
            s.close()
    return ""


def is_private_ip(host: str) -> bool:
    import ipaddress
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private and not ip.is_loopback


def looks_like_docker_ip(ip: str) -> bool:
    import ipaddress
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("172.16.0.0/12")
    except ValueError:
        return False
