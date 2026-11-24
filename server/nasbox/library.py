"""本地曲库：扫描 /music，SQLite 索引，搜索、歌单、收藏。"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import logging
import os
import re
import sqlite3
import threading
import time

from .util import norm

log = logging.getLogger("nasbox.library")

AUDIO_EXT = {".mp3", ".flac", ".wav", ".ape", ".ogg", ".m4a", ".wma"}
COVER_NAMES = ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "cover.jpeg")

SCHEMA = """
CREATE TABLE IF NOT EXISTS songs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT UNIQUE NOT NULL, dir TEXT NOT NULL, filename TEXT NOT NULL,
  title TEXT, artist TEXT, album TEXT, track INTEGER DEFAULT 0,
  duration REAL DEFAULT 0, size INTEGER, mtime REAL, ext TEXT,
  has_cover INTEGER DEFAULT 0, skey TEXT, added_at INTEGER);
CREATE INDEX IF NOT EXISTS idx_songs_dir ON songs(dir);
CREATE TABLE IF NOT EXISTS playlists(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, created_at INTEGER);
CREATE TABLE IF NOT EXISTS playlist_items(playlist_id INTEGER, song_id INTEGER, pos INTEGER,
  PRIMARY KEY(playlist_id, song_id));
CREATE TABLE IF NOT EXISTS favorites(song_id INTEGER PRIMARY KEY, ts INTEGER);
"""

COLS = "id,path,dir,filename,title,artist,album,track,duration,size,ext,has_cover"


def read_tags(full: str) -> dict:
    """用 mutagen 读标签；任何异常都退回到文件名。"""
    info = {"title": "", "artist": "", "album": "", "track": 0, "duration": 0.0, "has_cover": 0}
    try:
        import mutagen
        f = mutagen.File(full, easy=False)
        if f is None:
            return info
        if getattr(f, "info", None) is not None:
            info["duration"] = float(getattr(f.info, "length", 0) or 0)
        tags = f.tags
        has_cover = False
        ez = None
        try:
            ez = mutagen.File(full, easy=True)
        except Exception:
            ez = None
        def first(key):
            try:
                v = ez.get(key) if ez is not None and ez.tags is not None else None
                if v:
                    return str(v[0]).strip()
            except Exception:
                pass
            return ""
        info["title"], info["artist"], info["album"] = first("title"), first("artist"), first("album")
        tr = first("tracknumber")
        try:
            info["track"] = int(tr.split("/")[0]) if tr else 0
        except ValueError:
            pass
        name = type(f).__name__
        if name in ("ASF",) and tags is not None:        # wma
            def asf(k):
                v = tags.get(k)
                return str(v[0]).strip() if v else ""
            info["title"] = info["title"] or asf("Title")
            info["artist"] = info["artist"] or asf("Author")
            info["album"] = info["album"] or asf("WM/AlbumTitle")
            has_cover = bool(tags.get("WM/Picture"))
        elif name == "FLAC":
            has_cover = bool(f.pictures)
        elif name in ("MP4",):
            has_cover = bool(tags and tags.get("covr"))
        elif tags is not None:
            keys = list(tags.keys()) if hasattr(tags, "keys") else []
            has_cover = any(str(k).startswith("APIC") or "cover art" in str(k).lower()
                            or str(k) == "metadata_block_picture" for k in keys)
        info["has_cover"] = 1 if has_cover else 0
    except Exception as e:
        log.debug("读标签失败 %s: %s", os.path.basename(full), e)
    return info


def extract_cover(full: str) -> tuple[bytes, str] | None:
    """取内嵌封面，没有则找同目录 cover.jpg 等。"""
    try:
        import mutagen
        f = mutagen.File(full, easy=False)
        if f is not None:
            name = type(f).__name__
            tags = f.tags
            if name == "FLAC" and f.pictures:
                p = f.pictures[0]
                return p.data, p.mime or "image/jpeg"
            if name == "MP4" and tags and tags.get("covr"):
                c = tags["covr"][0]
                return bytes(c), "image/png" if getattr(c, "imageformat", 13) == 14 else "image/jpeg"
            if name == "ASF" and tags and tags.get("WM/Picture"):
                raw = bytes(tags["WM/Picture"][0].value)
                # 1 字节类型 + 4 字节长度 + UTF-16 mime\0 + UTF-16 描述\0 + 数据
                size = int.from_bytes(raw[1:5], "little")
                return raw[-size:], "image/jpeg"
            if tags is not None and hasattr(tags, "keys"):
                for k in tags.keys():
                    ks = str(k)
                    if ks.startswith("APIC"):
                        a = tags[k]
                        return a.data, a.mime or "image/jpeg"
                    if "cover art" in ks.lower():          # APEv2
                        raw = bytes(tags[k].value)
                        i = raw.find(b"\x00")
                        return raw[i + 1:], "image/jpeg"
                    if ks == "metadata_block_picture":      # ogg
                        import base64
                        from mutagen.flac import Picture
                        p = Picture(base64.b64decode(tags[k][0]))
                        return p.data, p.mime or "image/jpeg"
    except Exception as e:
        log.debug("取封面失败 %s: %s", os.path.basename(full), e)
    d = os.path.dirname(full)
    for n in COVER_NAMES:
        p = os.path.join(d, n)
        if os.path.isfile(p):
            try:
                with open(p, "rb") as fh:
                    return fh.read(), "image/png" if n.endswith(".png") else "image/jpeg"
            except OSError:
                pass
    return None


class Library:
    def __init__(self, paths):
        self.paths = paths
        self.root = os.path.abspath(paths.music)
        os.makedirs(paths.data, exist_ok=True)
        self.db = sqlite3.connect(paths.db_file, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()
        self.scanning = False
        self.scan_progress = {"done": 0, "total": 0, "added": 0, "updated": 0, "removed": 0, "finished_at": 0}
        self.on_change = None

    # ---------------------------------------------------------------- 基础
    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def x(self, sql: str, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def full_path(self, rel: str) -> str:
        p = os.path.abspath(os.path.join(self.root, rel))
        if p != self.root and not p.startswith(self.root + os.sep):
            raise ValueError("非法路径")
        return p

    @staticmethod
    def row(r, fav: set | None = None) -> dict:
        d = dict(r)
        d["title"] = d.get("title") or os.path.splitext(d["filename"])[0]
        d["artist"] = d.get("artist") or ""
        d["album"] = d.get("album") or ""
        if fav is not None:
            d["fav"] = d["id"] in fav
        return d

    def fav_ids(self) -> set:
        return {r[0] for r in self.q("SELECT song_id FROM favorites")}

    def count(self) -> int:
        return self.q("SELECT COUNT(*) FROM songs")[0][0]

    def song(self, sid: int) -> dict | None:
        rows = self.q(f"SELECT {COLS} FROM songs WHERE id=?", (sid,))
        return self.row(rows[0], self.fav_ids()) if rows else None

    def songs_by_ids(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        fav = self.fav_ids()
        out = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in self.q(f"SELECT {COLS} FROM songs WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                out[r["id"]] = self.row(r, fav)
        return [out[i] for i in ids if i in out]

    # ---------------------------------------------------------------- 扫描
    async def scan(self, full: bool = False):
        if self.scanning:
            return
        self.scanning = True
        self._notify()
        try:
            await asyncio.get_running_loop().run_in_executor(None, self._scan_sync, full)
        except Exception:
            log.exception("扫描曲库出错")
        finally:
            self.scanning = False
            self._notify()

    def _notify(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    def _scan_sync(self, full: bool):
        t0 = time.time()
        prog = self.scan_progress
        prog.update(done=0, total=0, added=0, updated=0, removed=0)
        if not os.path.isdir(self.root):
            log.warning("曲库目录不存在: %s", self.root)
            return
        found: dict[str, tuple[int, float]] = {}
        for dp, dns, fns in os.walk(self.root, followlinks=True):
            dns[:] = sorted(d for d in dns if not d.startswith((".", "@", "#")))
            for fn in fns:
                if fn.startswith("."):
                    continue
                if os.path.splitext(fn)[1].lower() not in AUDIO_EXT:
                    continue
                fp = os.path.join(dp, fn)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                found[os.path.relpath(fp, self.root)] = (st.st_size, st.st_mtime)
        if not found and self.count() > 0 and not full:
            # 目录暂时为空（例如 NAS 卷未挂载）时不清空索引，避免歌单/收藏被连带清掉
            log.warning("曲库目录为空，疑似未挂载，本次不删除已有索引")
            return
        existing = {r["path"]: (r["id"], r["size"], r["mtime"]) for r in self.q("SELECT id,path,size,mtime FROM songs")}
        todo = [p for p, (sz, mt) in found.items()
                if full or p not in existing or existing[p][1] != sz or abs((existing[p][2] or 0) - mt) > 1]
        prog["total"] = len(todo)
        for p in todo:
            fullp = os.path.join(self.root, p)
            t = read_tags(fullp)
            fn = os.path.basename(p)
            d = os.path.dirname(p)
            if not t["has_cover"] and any(os.path.isfile(os.path.join(os.path.dirname(fullp), n)) for n in COVER_NAMES):
                t["has_cover"] = 1
            skey = "|".join(norm(x) for x in (t["title"], t["artist"], t["album"], os.path.splitext(fn)[0]))
            sz, mt = found[p]
            ext = os.path.splitext(fn)[1].lower().lstrip(".")
            with self.lock:
                if p in existing:
                    self.db.execute("UPDATE songs SET title=?,artist=?,album=?,track=?,duration=?,size=?,mtime=?,ext=?,has_cover=?,skey=? WHERE id=?",
                                    (t["title"], t["artist"], t["album"], t["track"], t["duration"], sz, mt, ext, t["has_cover"], skey, existing[p][0]))
                    prog["updated"] += 1
                else:
                    self.db.execute("INSERT INTO songs(path,dir,filename,title,artist,album,track,duration,size,mtime,ext,has_cover,skey,added_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (p, d, fn, t["title"], t["artist"], t["album"], t["track"], t["duration"], sz, mt, ext, t["has_cover"], skey, int(time.time())))
                    prog["added"] += 1
                prog["done"] += 1
                if prog["done"] % 200 == 0:
                    self.db.commit()
        gone = [existing[p][0] for p in existing if p not in found]
        with self.lock:
            for i in range(0, len(gone), 500):
                chunk = gone[i:i + 500]
                ph = ",".join("?" * len(chunk))
                self.db.execute(f"DELETE FROM songs WHERE id IN ({ph})", chunk)
                self.db.execute(f"DELETE FROM playlist_items WHERE song_id IN ({ph})", chunk)
                self.db.execute(f"DELETE FROM favorites WHERE song_id IN ({ph})", chunk)
            self.db.commit()
        prog["removed"] = len(gone)
        prog["finished_at"] = int(time.time())
        log.info("扫描完成：共 %d 首，新增 %d，更新 %d，移除 %d，用时 %.1fs",
                 len(found), prog["added"], prog["updated"], prog["removed"], time.time() - t0)

    # ---------------------------------------------------------------- 查询
    def list_songs(self, *, dir: str | None = None, offset: int = 0, limit: int = 200, sort: str = "title") -> dict:
        fav = self.fav_ids()
        order = {"title": "COALESCE(NULLIF(title,''),filename) COLLATE NOCASE", "added": "added_at DESC, id DESC",
                 "path": "dir, track, filename"}.get(sort, "dir, track, filename")
        if dir is not None:
            rows = self.q(f"SELECT {COLS} FROM songs WHERE dir=? ORDER BY track, filename LIMIT ? OFFSET ?", (dir, limit, offset))
            total = self.q("SELECT COUNT(*) FROM songs WHERE dir=?", (dir,))[0][0]
        else:
            rows = self.q(f"SELECT {COLS} FROM songs ORDER BY {order} LIMIT ? OFFSET ?", (limit, offset))
            total = self.count()
        return {"total": total, "songs": [self.row(r, fav) for r in rows]}

    def all_ids(self) -> list[int]:
        return [r[0] for r in self.q("SELECT id FROM songs ORDER BY dir, track, filename")]

    def folder_ids(self, d: str) -> list[int]:
        return [r[0] for r in self.q("SELECT id FROM songs WHERE dir=? ORDER BY track, filename", (d,))]

    def folders(self) -> list[dict]:
        return [{"dir": r["dir"], "name": os.path.basename(r["dir"]) or "根目录", "count": r["c"]}
                for r in self.q("SELECT dir, COUNT(*) c FROM songs GROUP BY dir ORDER BY dir")]

    def search(self, query: str, limit: int = 100) -> list[dict]:
        """容错搜索：歌名/歌手/专辑/文件名；支持「歌手的歌名」「歌手 歌名」；找不到时用相似度兜底。"""
        raw = (query or "").strip()
        nq = norm(raw)
        if not nq:
            return []
        parts = [norm(x) for x in raw.replace("的", " ").split() if norm(x)]
        fav = self.fav_ids()
        scored = []
        for r in self.q(f"SELECT {COLS},skey FROM songs"):
            sk = r["skey"] or ""
            t, a, al, fn = (sk.split("|") + ["", "", "", ""])[:4]
            name = t or fn
            s = 0.0
            if nq == name:
                s = 100
            elif nq == fn:
                s = 98
            elif name and nq.startswith(a) and a and nq[len(a):].lstrip("的") == name:
                s = 99          # 歌手+歌名
            elif nq in name:
                s = 85 + 10 * len(nq) / max(len(name), 1)
            elif nq in fn:
                s = 80 + 10 * len(nq) / max(len(fn), 1)
            elif nq == a:
                s = 78
            elif nq in a:
                s = 70
            elif nq in al:
                s = 65
            elif len(parts) > 1 and all(any(p in f for f in (name, a, al, fn)) for p in parts):
                s = 75
            elif name and name in nq and len(name) >= 2:
                s = 60 + 20 * len(name) / len(nq)     # 口令里夹了多余的字
            else:
                ratio = max(difflib.SequenceMatcher(None, nq, name).ratio() if name else 0,
                            difflib.SequenceMatcher(None, nq, fn).ratio() if fn else 0)
                if ratio >= 0.6:
                    s = ratio * 60
            if s > 0:
                scored.append((s, r))
        scored.sort(key=lambda x: (-x[0], x[1]["dir"], x[1]["filename"]))
        return [dict(self.row({k: r[k] for k in r.keys() if k != "skey"}, fav), score=round(s, 1)) for s, r in scored[:limit]]

    def voice_queue(self, query: str, strict: bool = False):
        """语音点播的临时队列。返回 (ids, 起始下标, 来源说明, 结果说明)，找不到返回 None。

        说的是歌手 → 这位歌手的全部歌曲；说的是专辑 → 整张专辑；
        说的是歌名 → 这首歌在前，后面接其他命中的版本，再接它所在文件夹的其余歌曲。
        """
        raw = (query or "").strip()
        m = re.search(r"(的(歌曲|歌|音乐|专辑)|歌曲|专辑)$", raw)
        core = raw[:m.start()].strip() if m and raw[:m.start()].strip() else raw
        nraw = norm(raw)
        if core != raw and nraw and self.q("SELECT 1 FROM songs WHERE skey LIKE ? OR skey LIKE ? LIMIT 1",
                                            (nraw + "|%", "%|" + nraw)):
            core = raw                 # 整句本身就是一首歌的名字（如《最好的歌》）
        want_artist = core != raw
        nq = norm(core)
        if not nq:
            return None
        rows = self.q("SELECT id,dir,filename,track,title,artist,album,skey FROM songs ORDER BY album, track, dir, filename")
        art_exact, art_part, alb_exact, alb_part, title_exact = [], [], [], [], False
        for r in rows:
            sk = (r["skey"] or "").split("|") + ["", "", "", ""]
            t, a, al, fn = sk[:4]
            names = [norm(x) for x in re.split(r"[;；/、,，&]|\s+feat\.?\s+|\s+ft\.?\s+", r["artist"] or "") if norm(x)]
            if nq == a or nq in names:
                art_exact.append(r["id"])
            elif len(nq) >= 2 and nq in a:
                art_part.append(r["id"])
            if al and nq == al:
                alb_exact.append(r["id"])
            elif al and len(nq) >= 2 and nq in al:
                alb_part.append(r["id"])
            if nq == (t or fn) or nq == fn:
                title_exact = True
        hits = self.search(core, limit=30)
        best = hits[0] if hits else None

        def artist(ids):
            return ids, 0, f"歌手：{core}", f"播放歌手「{core}」的 {len(ids)} 首歌"

        def album(ids):
            return ids, 0, f"专辑：{core}", f"播放专辑「{core}」（{len(ids)} 首）"

        def title():
            ids = [best["id"]] + [h["id"] for h in hits[1:] if h["score"] >= 85]
            seen = set(ids)
            ids += [i for i in self.folder_ids(best["dir"]) if i not in seen]
            return ids, 0, f"搜索：{core}", f"播放《{best['title']}》"

        if strict:
            # 严格模式（没说口令关键词时用）：只认歌手、专辑、歌名的完全一致，或「歌手的歌名」
            if want_artist:
                return artist(art_exact) if art_exact else (album(alb_exact) if alb_exact else None)
            if title_exact and best and best["score"] >= 98:
                return title()
            if art_exact:
                return artist(art_exact)
            if alb_exact:
                return album(alb_exact)
            return title() if best and best["score"] >= 98 else None
        if want_artist and (art_exact or art_part):
            return artist(art_exact or art_part)
        if title_exact and best:
            return title()
        if art_exact:
            return artist(art_exact)
        if alb_exact:
            return album(alb_exact)
        if best and best["score"] >= 79:
            return title()
        if art_part:
            return artist(art_part)
        if alb_part:
            return album(alb_part)
        return title() if best else None

    # ---------------------------------------------------------------- 歌单 / 收藏
    def playlists(self) -> list[dict]:
        return [dict(r) for r in self.q(
            "SELECT p.id, p.name, (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id=p.id) count "
            "FROM playlists p ORDER BY p.name COLLATE NOCASE")]

    def playlist_create(self, name: str) -> int:
        name = name.strip()
        if not name:
            raise ValueError("歌单名不能为空")
        if self.q("SELECT 1 FROM playlists WHERE name=?", (name,)):
            raise ValueError("已有同名歌单")
        return self.x("INSERT INTO playlists(name,created_at) VALUES(?,?)", (name, int(time.time()))).lastrowid

    def playlist_rename(self, pid: int, name: str):
        name = name.strip()
        if not name or self.q("SELECT 1 FROM playlists WHERE name=? AND id<>?", (name, pid)):
            raise ValueError("歌单名为空或重复")
        self.x("UPDATE playlists SET name=? WHERE id=?", (name, pid))

    def playlist_delete(self, pid: int):
        self.x("DELETE FROM playlist_items WHERE playlist_id=?", (pid,))
        self.x("DELETE FROM playlists WHERE id=?", (pid,))

    def playlist_ids(self, pid: int) -> list[int]:
        return [r[0] for r in self.q(
            "SELECT i.song_id FROM playlist_items i JOIN songs s ON s.id=i.song_id WHERE i.playlist_id=? ORDER BY i.pos", (pid,))]

    def playlist_add(self, pid: int, song_ids: list[int]):
        if not self.q("SELECT 1 FROM playlists WHERE id=?", (pid,)):
            raise ValueError("歌单不存在")
        pos = self.q("SELECT COALESCE(MAX(pos),0) FROM playlist_items WHERE playlist_id=?", (pid,))[0][0]
        with self.lock:
            for sid in song_ids:
                pos += 1
                self.db.execute("INSERT OR IGNORE INTO playlist_items(playlist_id,song_id,pos) VALUES(?,?,?)", (pid, sid, pos))
            self.db.commit()

    def playlist_remove(self, pid: int, song_ids: list[int]):
        with self.lock:
            for sid in song_ids:
                self.db.execute("DELETE FROM playlist_items WHERE playlist_id=? AND song_id=?", (pid, sid))
            self.db.commit()

    def favorite_ids(self) -> list[int]:
        return [r[0] for r in self.q("SELECT f.song_id FROM favorites f JOIN songs s ON s.id=f.song_id ORDER BY f.ts DESC")]

    def set_favorite(self, sid: int, on: bool):
        if on:
            self.x("INSERT OR IGNORE INTO favorites(song_id,ts) VALUES(?,?)", (sid, int(time.time())))
        else:
            self.x("DELETE FROM favorites WHERE song_id=?", (sid,))

    def find_list_exact(self, name: str) -> tuple[str, object, str] | None:
        """名字完全一致的自建歌单或文件夹。"""
        n = norm(name)
        if not n:
            return None
        for p in self.playlists():
            if norm(p["name"]) == n:
                return ("playlist", p["id"], p["name"])
        for f in self.folders():
            if norm(f["name"]) == n and f["dir"]:
                return ("folder", f["dir"], f["name"])
        return None

    def find_list(self, name: str) -> tuple[str, object, str] | None:
        """按名字找歌单：自建歌单 > 收藏 > 文件夹。返回 (类型, 标识, 显示名)。"""
        n = norm(name)
        if not n:
            return None
        if n in ("收藏", "我的收藏", "收藏夹", "我喜欢"):
            return ("favorites", None, "收藏")
        best = None
        for p in self.playlists():
            pn = norm(p["name"])
            sc = 100 if pn == n else (80 if n in pn or pn in n else difflib.SequenceMatcher(None, n, pn).ratio() * 70)
            if sc >= 50 and (best is None or sc > best[0]):
                best = (sc, ("playlist", p["id"], p["name"]))
        for f in self.folders():
            fnm = norm(f["name"])
            sc = 95 if fnm == n else (75 if n in fnm or (fnm in n and len(fnm) >= 2) else difflib.SequenceMatcher(None, n, fnm).ratio() * 65)
            if sc >= 50 and (best is None or sc > best[0]):
                best = (sc, ("folder", f["dir"], f["name"]))
        return best[1] if best else None

    # ---------------------------------------------------------------- 封面
    def cover(self, sid: int) -> tuple[str, str] | None:
        """返回 (缓存文件路径, mime)。首次访问时提取并缓存。"""
        rows = self.q("SELECT path,mtime,has_cover FROM songs WHERE id=?", (sid,))
        if not rows or not rows[0]["has_cover"]:
            return None
        cdir = os.path.join(self.paths.cache_dir, "covers")
        os.makedirs(cdir, exist_ok=True)
        key = hashlib.sha1(f"{rows[0]['path']}|{rows[0]['mtime']}".encode()).hexdigest()[:20]
        for ext, mime in ((".jpg", "image/jpeg"), (".png", "image/png")):
            p = os.path.join(cdir, key + ext)
            if os.path.isfile(p):
                return p, mime
        res = extract_cover(self.full_path(rows[0]["path"]))
        if not res:
            return None
        data, mime = res
        ext = ".png" if "png" in mime else ".jpg"
        p = os.path.join(cdir, key + ext)
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
        return p, "image/png" if ext == ".png" else "image/jpeg"
