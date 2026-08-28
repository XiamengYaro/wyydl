"""核心同步引擎:歌单拉取 → NCM 兜底入库 → 音质协商 → 下载打标 → m3u8/清理 → 通知。"""
from __future__ import annotations

import datetime as _dt
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import downloader, ncm as ncmmod, nfo, notify, quality, tagger
from .api import ApiError, LoginExpired, NcmApi
from .config import DB_DIR, MUSIC_DIR, Config
from .state import State

log = logging.getLogger("wyydl.sync")


def now_str() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SyncEngine:
    def __init__(self, cfg: Config, state: State, api: NcmApi):
        self.cfg = cfg
        self.state = state
        self.api = api
        self.running = False
        self.progress: dict = {}
        self.recent: list = []
        self.active: dict = {}  # sid -> {title,artist,downloaded,total} 正在下载的曲目
        self._run_lock = threading.Lock()
        self._cover_cache: dict[int, bytes | None] = {}
        self._album_cache: dict[int, dict | None] = {}
        self.tmp_dir = DB_DIR / "tmp"
        self._cookie_ok = False
        self._cookie_checked_at = 0.0

    # ================= 对外接口 =================
    def cookie_ok(self) -> bool:
        """带 10 分钟缓存的登录态检查(Web 面板轮询用)。"""
        if time.time() - self._cookie_checked_at > 600:
            self._cookie_ok = self.api.logged_in()
            self._cookie_checked_at = time.time()
        return self._cookie_ok

    def try_run(self, playlist_ids: list[int] | None = None, trigger: str = "manual") -> bool:
        if self.running:
            return False
        threading.Thread(target=self._guarded_run, args=(playlist_ids, trigger), daemon=True).start()
        return True

    def _guarded_run(self, playlist_ids, trigger) -> None:
        try:
            self.run_once(playlist_ids, trigger)
        except Exception:
            log.exception("同步线程异常退出")

    def run_once(self, playlist_ids: list[int] | None = None, trigger: str = "schedule") -> dict:
        with self._run_lock:
            if self.running:
                return {}
            self.running = True
        started = now_str()
        summary: dict = {
            "trigger": trigger, "status": "ok", "playlists": [], "levels": {},
            "added": 0, "upgraded": 0, "failed": 0, "removed": 0, "ncm": 0,
            "failures": [], "started": started,
        }
        try:
            self._run(summary, playlist_ids)
        except LoginExpired:
            summary["status"] = "login_expired"
            log.error("登录态失效,本轮终止")
        except Exception as e:
            summary["status"] = "error"
            summary["error"] = str(e)
            log.exception("同步失败")
        finally:
            self.running = False
            self.progress = {}
            summary["finished"] = now_str()
            self.state.record_run(started, summary["finished"], summary["status"], summary)
            if (summary["added"] or summary["upgraded"] or summary["failed"]
                    or summary["removed"] or summary["ncm"] or summary["status"] != "ok"):
                notify.notify(self.cfg.d, notify.run_summary_text(summary))
        return summary

    # ================= 主流程 =================
    def _run(self, summary: dict, playlist_ids: list[int] | None) -> None:
        cfg = self.cfg
        if not self.api.logged_in():
            raise LoginExpired("MUSIC_U 无效或已过期")

        targets = list(cfg.d.get("playlists") or [])
        if playlist_ids:
            want = {int(x) for x in playlist_ids}
            targets = [p for p in targets if int(p.get("id", -1)) in want] \
                or [{"id": i, "name": ""} for i in want]
        if not targets:
            log.info("未配置任何歌单,本轮结束")
            return

        # 1) 拉取歌单曲目(先记录旧成员表用于删除统计)
        desired: dict[int, list[dict]] = {}
        prev_members: dict[int, set[int]] = {}
        for p in targets:
            pid = int(p["id"])
            self._set_progress("拉取歌单", str(p.get("name") or pid))
            pl = self.api.playlist_detail(pid)
            name = str(p.get("name") or pl.get("name") or pid)
            tracks = self.api.playlist_tracks(pid, pl=pl)
            prev_members[pid] = {sid for sid, _ in self.state.playlist_songs(pid)}
            desired[pid] = tracks
            self.state.upsert_playlist(pid, name, len(tracks), last_sync=now_str())
            self.state.set_playlist_songs(pid, [(int(t["id"]), i) for i, t in enumerate(tracks) if t.get("id")])
            summary["playlists"].append({"id": pid, "name": name, "total": len(tracks)})
            log.info("歌单[%s] 共 %d 首", name, len(tracks))

        # 2) 详情与音质权限
        all_ids = sorted({int(t["id"]) for ts in desired.values() for t in ts if t.get("id")})
        self._set_progress("拉取歌曲详情", f"{len(all_ids)} 首")
        details: dict[int, dict] = {}
        for s in self.api.song_detail(all_ids):
            details[int(s["id"])] = s

        # 元数据先入库:下载完成前面板也能看到歌名/歌手(状态仍为 new)
        for sid in all_ids:
            d = details.get(sid)
            if not d:
                continue
            artists = [a.get("name") for a in (d.get("ar") or d.get("artists") or []) if a.get("name")]
            self.state.upsert_song(sid=sid, title=str(d.get("name") or sid),
                                   artist=" / ".join(artists) or "未知歌手",
                                   album=str((d.get("al") or {}).get("name") or "未知专辑"),
                                   track_no=int(d.get("no") or 0))

        # 3) NCM 投放目录兜底入库
        if cfg.d.get("ncm_inbox", True):
            self._set_progress("NCM 转换", "")
            summary["ncm"] = self._process_inbox()
            self._set_progress("准备下载", "")

        # 4) 计划下载
        chain = cfg.quality_chain
        upgrade_on = bool(cfg.d["quality"].get("upgrade_existing", True))
        tasks: list[tuple[int, str, str]] = []  # (sid, level, kind)
        for sid in all_ids:
            row = self.state.song(sid)
            lvl = quality.pick_level((details.get(sid) or {}).get("privilege"), chain)
            has_file = bool(row and row["status"] == "ok" and row["file_path"]
                            and Path(row["file_path"]).exists())
            if not has_file:
                tasks.append((sid, lvl, "new"))
            elif upgrade_on and quality.rank(lvl) > quality.rank(row["level"]):
                tasks.append((sid, lvl, "upgrade"))
        summary["added"] = sum(1 for _, _, k in tasks if k == "new")
        summary["upgraded"] = sum(1 for _, _, k in tasks if k == "upgrade")
        log.info("待下载 %d(新增 %d / 升级 %d)", len(tasks), summary["added"], summary["upgraded"])

        if tasks:
            self._download_all(tasks, details, summary)

        # 5) 导出 m3u8 与删除处理
        self._set_progress("整理输出", "")
        self._export_m3u8()
        summary["removed"] = self._cleanup_missing(desired, prev_members)

    # ---------- 歌单元数据 ----------
    def _membership(self, all_ids: list[int]) -> dict[int, tuple[str, int]]:
        """sid -> (歌单名, 位置),按配置里的歌单顺序取第一个所属歌单。"""
        out: dict[int, tuple[str, int]] = {}
        pls = {p["pid"]: p["name"] for p in self.state.playlists()}
        order = [int(p.get("id")) for p in (self.cfg.d.get("playlists") or [])]
        for pid in order:
            name = pls.get(pid) or str(pid)
            for sid, pos in self.state.playlist_songs(pid):
                out.setdefault(sid, (name, pos))
        return out

    def _build_meta(self, sid: int, detail: dict, member: tuple[str, int]) -> dict:
        d = detail or {}
        artists = [a.get("name") for a in (d.get("ar") or d.get("artists") or []) if a.get("name")]
        al = d.get("al") or d.get("album") or {}
        year = ""
        if d.get("publishTime"):
            try:
                year = str(_dt.datetime.utcfromtimestamp(int(d["publishTime"]) / 1000).year)
            except (ValueError, OSError, OverflowError):
                year = ""
        disc = 0
        if d.get("cd"):
            m = re.match(r"\d+", str(d["cd"]))
            disc = int(m.group()) if m else 0
        return {
            "sid": sid,
            "title": str(d.get("name") or d.get("title") or sid),
            "artist": " / ".join(artists) or "未知歌手",
            "album_artist": (artists[0] if artists else "") or "未知歌手",
            "album": str(al.get("name") or "未知专辑"),
            "pic_url": str(al.get("picUrl") or ""),
            "album_id": al.get("id"),
            "track": int(d.get("no") or 0),
            "disc": disc,
            "date": year,
            "playlist": member[0],
            "pos": member[1],
        }

    def _set_progress(self, stage: str, detail: str, done: int | None = None, total: int | None = None) -> None:
        self.progress = {"stage": stage, "detail": detail, "ts": now_str()}
        if done is not None:
            self.progress["done"] = done
        if total is not None:
            self.progress["total"] = total

    # ---------- 下载 ----------
    def _download_all(self, tasks, details: dict[int, dict], summary: dict) -> None:
        """取流与下载交错:每取到一批流地址立刻提交下载,不整体阻塞。"""
        kind_of = {sid: kind for sid, _, kind in tasks}
        total = len(tasks)
        urlmap: dict[int, dict] = {}
        by_level: dict[str, list[int]] = {}
        for sid, lvl, _ in tasks:
            by_level.setdefault(lvl, []).append(sid)

        conc = max(1, int(self.cfg.d["limits"].get("download_concurrency") or 3))
        self.recent = []
        self.active = {}
        done = 0
        futs: list = []
        pool = ThreadPoolExecutor(max_workers=conc)
        try:
            for lvl, sids in by_level.items():
                for i in range(0, len(sids), 5):
                    chunk = sids[i:i + 5]
                    self._set_progress("下载", f"取流 {lvl}…", done=done, total=total)
                    try:
                        urlmap.update(self.api.song_url_batch(chunk, lvl))
                    except ApiError as e:
                        log.warning("批量取流失败(level=%s):%s", lvl, e)
                    for sid in chunk:
                        futs.append(pool.submit(self._download_one, sid, lvl, kind_of[sid],
                                                details.get(sid) or {}, urlmap.get(sid)))
            for fut in as_completed(futs):
                done += 1
                try:
                    r = fut.result()
                except Exception as e:
                    sid = futs[fut]
                    r = {"ok": False, "title": str(sid), "artist": "", "reason": f"内部错误:{e}"}
                self.recent.append({"title": str(r.get("title") or ""), "artist": str(r.get("artist") or ""),
                                    "level": str(r.get("level") or ""), "ok": bool(r.get("ok")),
                                    "reason": str(r.get("reason") or "")})
                if len(self.recent) > 10:
                    del self.recent[:len(self.recent) - 10]
                self._set_progress("下载", f"{done}/{total}", done=done, total=total)
                if r["ok"]:
                    summary["levels"][r.get("level") or "?"] = summary["levels"].get(r.get("level") or "?", 0) + 1
                else:
                    summary["failed"] += 1
                    summary["failures"].append(r)
                    log.warning("失败 %s - %s:%s", r.get("artist"), r.get("title"), r.get("reason"))
        finally:
            pool.shutdown(wait=True)

    def _dl_tick(self, sid: int, downloaded: int, total: int) -> None:
        a = self.active.get(sid)
        if a is not None:
            a["downloaded"] = downloaded
            if total:
                a["total"] = total

    def _download_one(self, sid: int, want_level: str, kind: str,
                      detail: dict, pre: dict | None) -> dict:
        members = self._membership([sid])
        meta = self._build_meta(sid, detail, members.get(sid, ("未命名歌单", 0)))
        fail = lambda reason: {"ok": False, "title": meta["title"], "artist": meta["artist"], "reason": reason}  # noqa: E731

        # 取流:预取结果不可用(试听/空链)时逐档降档重试
        entry = pre if (pre and pre.get("url") and not pre.get("freeTrialInfo")) else None
        if entry is None:
            for lvl in quality.chain_from(want_level, self.cfg.quality_chain):
                try:
                    e = self.api.song_url(sid, lvl)
                except ApiError:
                    continue
                if e.get("url") and not e.get("freeTrialInfo"):
                    entry = e
                    break
        if not entry:
            return fail("无可用音源(试听或下架)")

        self.active[sid] = {"title": meta["title"], "artist": meta["artist"],
                            "downloaded": 0, "total": int(entry.get("size") or 0)}
        try:
            try:
                tmp, md5hex, size = downloader.download(
                    entry["url"], self.tmp_dir,
                    progress=lambda dn, dt: self._dl_tick(sid, dn, dt))
            finally:
                self.active.pop(sid, None)
        except Exception as e:
            return fail(f"下载失败:{e.__class__.__name__}")
        if not downloader.verify(tmp, str(entry.get("md5") or ""), int(entry.get("size") or 0)):
            tmp.unlink(missing_ok=True)
            return fail("校验失败(MD5/大小不符)")

        ext = str(entry.get("type") or "mp3").lower()
        real = tmp.with_suffix(f".{ext}")
        tmp.rename(real)

        lrc = ""
        if self.cfg.d["lyrics"].get("lrc", True) or self.cfg.d["lyrics"].get("embed", True):
            lrc = self.api.lyric(sid)
        cover = self._cover_for(detail) if (detail or {}).get("al", {}).get("id") is not None else None
        tagger.tag_file(real, meta, cover, lrc if self.cfg.d["lyrics"].get("embed", True) else None)

        rel = tagger.song_relative_path(meta, self.cfg.layout, self.cfg.naming)
        final = MUSIC_DIR / f"{rel}.{ext}"
        if final.exists():
            final.unlink()
        downloader.move_into(real, final)

        if lrc and self.cfg.d["lyrics"].get("lrc", True):
            final.with_suffix(".lrc").write_text(lrc, encoding="utf-8")

        self.state.upsert_song(
            sid=sid, title=meta["title"], artist=meta["artist"], album=meta["album"],
            file_path=str(final), ext=ext, track_no=int(meta.get("track") or 0),
            level=str(entry.get("level") or want_level),
            br=int(entry.get("br") or 0), size=size, md5=md5hex,
            downloaded_at=now_str(), status="ok",
        )
        if self.cfg.d.get("nfo", True):
            try:
                self._write_nfo(final, meta, detail)
            except Exception as e:
                log.warning("NFO 写入失败 %s: %s", final.parent, e)
        log.info("[%s] %s - %s (%s)", kind, meta["artist"], meta["title"], entry.get("level") or want_level)
        return {"ok": True, "kind": kind, "level": str(entry.get("level") or want_level),
                "title": meta["title"], "artist": meta["artist"]}

    def _write_nfo(self, final: Path, meta: dict, detail: dict) -> None:
        """单曲 <歌名>.nfo(两种布局都写);专辑/艺人级 NFO 仅归档布局有目录结构。"""
        d = detail or {}
        aid = meta.get("album_id")
        info: dict = {}
        if aid is not None:
            if aid not in self._album_cache:
                try:
                    info = self.api.album(aid).get("album") or {}
                except ApiError:
                    info = {}
                self._album_cache[aid] = info
            info = self._album_cache[aid] or {}
        releasedate = ""
        if info.get("publishTime"):
            try:
                releasedate = _dt.datetime.utcfromtimestamp(int(info["publishTime"]) / 1000).strftime("%Y-%m-%d")
            except (ValueError, OSError, OverflowError):
                releasedate = ""
        genre = info.get("genre") or ""
        if isinstance(genre, list):
            genre = ", ".join(str(g) for g in genre)
        ar = d.get("ar") or []
        ncm_aid = str(ar[0].get("id") or "") if ar else ""

        # 单曲级:<歌名>.nfo
        nfo.write_song_nfo(
            final.with_suffix(".nfo"), title=meta["title"], artist=meta["artist"],
            album=meta["album"], albumartist=meta["album_artist"],
            track=int(meta.get("track") or 0), disc=int(meta.get("disc") or 0),
            year=str(meta.get("date") or ""), duration=int((d.get("dt") or 0) // 1000),
            genre=str(genre), ncm_id=str(meta.get("sid") or ""),
        )
        if self.cfg.layout != "archive":
            return
        album_dir = final.parent
        artist_dir = album_dir.parent
        tracks = []
        for s in self.state.all_songs():
            if (s["status"] == "ok" and s["title"] and s["album"] == meta["album"]
                    and s["file_path"] and Path(s["file_path"]).exists()):
                tracks.append((int(s.get("track_no") or 0), s["title"]))
        nfo.write_album_nfo(
            album_dir / "album.nfo", title=meta["album"], artist=meta["artist"],
            year=str(meta.get("date") or ""), genres=str(genre),
            label=str(info.get("company") or ""), releasedate=releasedate,
            plot=str(info.get("description") or ""), tracks=tracks,
        )
        nfo.write_artist_nfo(artist_dir / "artist.nfo", name=meta["album_artist"],
                             genre=str(genre), ncm_id=ncm_aid)

    def _cover_for(self, detail: dict) -> bytes | None:
        al = (detail or {}).get("al") or {}
        aid = al.get("id")
        if aid is None:
            return None
        if aid in self._cover_cache:
            return self._cover_cache[aid]
        url = al.get("picUrl")
        data = None
        if url:
            try:
                r = self.api.client.get(f"{url}?param=1500y1500")
                if r.status_code == 200 and r.content[:2] in (b"\xff\xd8", b"\x89P"):
                    data = r.content
            except Exception:
                data = None
        self._cover_cache[aid] = data
        return data

    # ---------- NCM 兜底 ----------
    def _process_inbox(self) -> int:
        inbox = MUSIC_DIR / "_ncm_inbox"
        out = DB_DIR / "ncm_out"
        done = MUSIC_DIR / "_inbox_done"
        files = sorted(inbox.glob("*.ncm"))
        if not files:
            return 0
        count = 0
        known = {(s["title"].casefold(), s["artist"].split(" / ")[0].casefold()): s["sid"]
                 for s in self.state.all_songs() if s["title"]}
        for f in files:
            self._set_progress("NCM 转换", f.name)
            conv = ncmmod.convert_one(f, out)
            if conv is None:
                continue
            meta = self._tags_from_file(conv)
            rel = tagger.song_relative_path({**meta, "pos": 0}, self.cfg.layout, self.cfg.naming)
            final = MUSIC_DIR / f"{rel}{conv.suffix.lower()}"
            i = 2
            while final.exists():
                final = MUSIC_DIR / f"{rel} ({i}){conv.suffix.lower()}"
                i += 1
            downloader.move_into(conv, final)
            key = (meta["title"].casefold(), meta["artist"].split(" / ")[0].casefold())
            sid = known.get(key) or ncmmod.ncm_sid(f)
            self.state.upsert_song(
                sid=sid, title=meta["title"], artist=meta["artist"], album=meta["album"],
                file_path=str(final), ext=final.suffix.lstrip("."), level="ncm",
                downloaded_at=now_str(), status="ok",
            )
            ncmmod.move_done(f, done)
            count += 1
            log.info("[ncm] %s -> %s", f.name, final.name)
        return count

    @staticmethod
    def _tags_from_file(path: Path) -> dict:
        from mutagen import File as MutaFile
        meta = {"title": path.stem, "artist": "未知歌手", "album": "未知专辑", "track": 0}
        try:
            mf = MutaFile(str(path), easy=True)
            if mf is None:
                return meta
            def first(k, d=""):
                v = (mf.get(k) or [d])[0]
                return str(v) if v is not None else d
            meta["title"] = first("title", path.stem)
            meta["artist"] = first("artist", "未知歌手")
            meta["album"] = first("album", "未知专辑")
            tn = first("tracknumber", "0").split("/")[0]
            meta["track"] = int(tn) if tn.isdigit() else 0
        except Exception:
            pass
        return meta

    # ---------- 输出整理 ----------
    def _export_m3u8(self) -> None:
        for pl in self.state.playlists():
            pid = pl["pid"]
            if pid not in self.cfg.playlist_ids():
                continue
            lines = ["#EXTM3U"]
            for sid, _pos in self.state.playlist_songs(pid):
                s = self.state.song(sid)
                if not (s and s["status"] == "ok" and s["file_path"]):
                    continue
                p = Path(s["file_path"])
                try:
                    rel = p.resolve().relative_to(MUSIC_DIR.resolve()).as_posix()
                except ValueError:
                    continue
                lines.append(f"#EXTINF:-1,{s['artist']} - {s['title']}")
                lines.append(rel)
            name = tagger.sanitize(pl["name"] or str(pid))
            (MUSIC_DIR / f"{name}.m3u8").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    def _cleanup_missing(self, desired: dict[int, list[dict]], prev_members: dict[int, set[int]]) -> int:
        removed = 0
        still_wanted: set[int] = set()
        for ts in desired.values():
            still_wanted |= {int(t["id"]) for t in ts if t.get("id")}
        for pid, prev in prev_members.items():
            now = {int(t["id"]) for t in desired.get(pid, []) if t.get("id")}
            gone = prev - now
            removed += len(gone)
            if not (gone and self.cfg.d.get("mirror")):
                continue
            trash = MUSIC_DIR / "_trash"
            trash.mkdir(parents=True, exist_ok=True)
            for sid in gone:
                if sid in still_wanted:
                    continue  # 其他歌单还在用,文件保留
                s = self.state.song(sid)
                if not (s and s["file_path"]):
                    continue
                src = Path(s["file_path"])
                if src.exists() and MUSIC_DIR.resolve() in src.resolve().parents:
                    dst = trash / f"{_dt.datetime.now().strftime('%Y%m%d%H%M%S')}_{src.name}"
                    downloader.shutil_move(src, dst)
                    self.state.upsert_song(sid=sid, status="removed", file_path=str(dst))
                    log.info("已移入回收目录:%s", src.name)
        return removed
