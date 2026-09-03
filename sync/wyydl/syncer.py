"""核心同步引擎:歌单拉取 → 音质协商 → 下载打标 → m3u8/清理 → 通知。"""
from __future__ import annotations

import datetime as _dt
import logging
import re
import shutil
import threading
import zlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

from . import downloader, nfo, notify, quality, tagger, yrc
from . import config as cfgmod
from .api import ApiError, LoginExpired, NcmApi
from .providers.base import platform_of_sid, raw_id_of
from .config import DB_DIR, MUSIC_DIR, Config
from .state import State

log = logging.getLogger("wyydl.sync")


def stable_pid(raw_id: str) -> int:
    """非数字来源 ID(如 B 站链接)→ 稳定正整数 pid。"""
    raw = str(raw_id)
    if raw.lstrip("-").isdigit():
        return int(raw)
    return zlib.crc32(raw.encode("utf-8")) % 1_000_000_000


class _Fail(Exception):
    """下载链路内的可预期失败,reason 面向用户。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

# 特殊来源的合成歌单 id(负值,避免与真实歌单 id 冲突)
SOURCE_PID = {"cloud": -1, "daily": -2, "fm": -3}
SOURCE_NAME = {"cloud": "云盘", "daily": "每日推荐", "fm": "私人FM"}


def now_str() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SyncEngine:
    def __init__(self, cfg: Config, state: State, api):
        """api 可为 NcmApi 实例(自动补齐 QQ/B 站 Provider),
        或 {platform: Provider} 字典(多平台)。"""
        self.cfg = cfg
        self.state = state
        self.tmp_dir = DB_DIR / "tmp"
        from .providers.netease import NetEaseProvider
        if isinstance(api, NcmApi):
            from .providers.qq import QQProvider
            from .providers.bili import BiliProvider
            api = {
                "netease": NetEaseProvider(api),
                "qq": QQProvider(cfgmod.QQ_API, lambda: cfgmod.load_secret("qq")),
                "bilibili": BiliProvider(lambda: cfgmod.load_secret("bili"),
                                         lambda c: cfgmod.save_secret("bili", c),
                                         self.tmp_dir),
            }
        self.providers: dict = api if isinstance(api, dict) else {"netease": api}
        self.api = self.providers["netease"].api  # 兼容别名(网易 NcmApi)
        self.running = False
        self.progress: dict = {}
        self.recent: list = []
        self.active: dict = {}  # sid -> {title,artist,downloaded,total} 正在下载的曲目
        self._run_lock = threading.Lock()
        self._cover_cache: dict[str, bytes | None] = {}
        self._album_cache: dict[str, dict | None] = {}
        self._cookie_ok = False
        self._cookie_checked_at = 0.0

    # ---- Provider 路由 ----
    def _prov_by_platform(self, platform: str):
        return self.providers.get(platform) or self.providers["netease"]

    def _prov_of_sid(self, sid: int):
        from .providers.base import platform_of_sid
        return self._prov_by_platform(platform_of_sid(sid))

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
            "added": 0, "upgraded": 0, "failed": 0, "removed": 0,
            "partial": 0, "partial_failures": [],
            "failures": [], "started": started,
        }
        notify.notify_start(self.cfg.d)
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
            notify.notify_run(self.cfg.d, summary)
        return summary

    # ================= 主流程 =================
    def _run(self, summary: dict, playlist_ids: list[int] | None) -> None:
        cfg = self.cfg
        if not self.api.logged_in():
            raise LoginExpired("MUSIC_U 无效或已过期")

        targets = list(cfg.d.get("playlists") or [])
        if playlist_ids:
            want = {int(x) for x in playlist_ids}
            if any(w < 0 for w in want):  # 负数 = 特殊源
                targets = [p for p in targets if SOURCE_PID.get(p.get("source")) in want]
            else:
                targets = [p for p in targets if stable_pid(p.get("id", -1)) in want] \
                    or [{"id": i, "name": ""} for i in want]
        if not targets:
            log.info("未配置任何歌单,本轮结束")
            return

        # 1) 拉取目标曲目(平台由条目 platform 决定,默认网易云;先记录旧成员表)
        desired: dict[int, list[dict]] = {}
        prev_members: dict[int, set[int]] = {}
        for p in targets:
            platform = str(p.get("platform") or "netease")
            prov = self._prov_by_platform(platform)
            source = str(p.get("source") or "playlist")
            if source in SOURCE_PID:
                pid = SOURCE_PID[source]
                name = SOURCE_NAME[source]
                self._set_progress("拉取歌单", name)
                try:
                    ids = self._fetch_source(source)
                except ApiError as e:
                    log.warning("特殊源[%s]拉取失败:%s", name, e)
                    ids = []
                tracks = [{"id": i} for i in ids]
            else:
                pid = stable_pid(p["id"])
                self._set_progress("拉取歌单", str(p.get("name") or pid))
                pl = prov.playlist_detail(pid)
                name = str(p.get("name") or pl.get("name") or pid)
                tracks = prov.playlist_tracks(pid, pl=pl)
            for t in tracks:  # 平台内 ID → 全局 sid(分段)
                t["_pid"] = str(t.get("_pid") or t.get("id") or "")
                t["_sid"] = prov.sid(t["_pid"])
            prev_members[pid] = {sid for sid, _ in self.state.playlist_songs(pid)}
            desired[pid] = tracks
            self.state.upsert_playlist(pid, name, len(tracks), last_sync=now_str())
            self.state.set_playlist_songs(pid, [(t["_sid"], i) for i, t in enumerate(tracks) if t.get("_sid")])
            summary["playlists"].append({"id": pid, "name": name, "total": len(tracks),
                                         "platform": platform})
            log.info("歌单[%s](%s) 共 %d 首", name, platform, len(tracks))

        # 2) 详情与音质权限(按平台分组拉取)
        all_ids = sorted({t["_sid"] for ts in desired.values() for t in ts if t.get("_sid")})
        self._set_progress("拉取歌曲详情", f"{len(all_ids)} 首")
        details: dict[int, dict] = {}
        by_platform: dict[str, list[tuple[int, str]]] = {}
        for ts in desired.values():
            for t in ts:
                if t.get("_sid"):
                    plat = t.get("platform") or platform_of_sid(t["_sid"])
                    by_platform.setdefault(plat, []).append((t["_sid"], t["_pid"]))
        for plat, pairs in by_platform.items():
            prov = self._prov_by_platform(plat)
            raws = [raw for _, raw in pairs]
            for (sid, raw), d in zip(pairs, prov.song_detail(raws)):
                if not d:
                    continue
                d.setdefault("_pid", str(raw))
                details[sid] = d

        # 元数据先入库(含 platform):下载完成前面板也能看到歌名/歌手(状态仍为 new)
        for sid in all_ids:
            d = details.get(sid)
            if not d:
                continue
            artists = [a.get("name") for a in (d.get("ar") or d.get("artists") or []) if a.get("name")]
            self.state.upsert_song(sid=sid, platform=platform_of_sid(sid),
                                   title=str(d.get("name") or sid),
                                   artist=" / ".join(artists) or "未知歌手",
                                   album=str((d.get("al") or {}).get("name") or "未知专辑"),
                                   track_no=int(d.get("no") or 0))

        # 3) 计划下载(失败退避:连续失败>=3 且 24h 内不再重试;单轮上限截断)
        upgrade_on = bool(cfg.d["quality"].get("upgrade_existing", True))
        tasks: list[tuple[int, str, str, str]] = []  # (sid, level, kind, platform)
        now = _dt.datetime.now()
        for sid in all_ids:
            row = self.state.song(sid)
            plat = platform_of_sid(sid)
            prov = self._prov_by_platform(plat)
            lvl = prov.pick_level((details.get(sid) or {}).get("privilege"),
                                  cfg.quality_chain if plat == "netease" else prov.level_chain)
            has_file = bool(row and row["status"] == "ok" and row["file_path"]
                            and Path(row["file_path"]).exists())
            if has_file:
                if upgrade_on and quality.rank(lvl) > quality.rank(row["level"]):
                    tasks.append((sid, lvl, "upgrade", plat))
                continue
            if row and int(row.get("fail_count") or 0) >= 3 and row.get("downloaded_at"):
                try:
                    last = _dt.datetime.strptime(row["downloaded_at"], "%Y-%m-%d %H:%M:%S")
                    if (now - last).total_seconds() < 86400:
                        continue  # 退避中,下轮再试
                except ValueError:
                    pass
            tasks.append((sid, lvl, "new", plat))
        limit = int(cfg.d["limits"].get("max_per_run") or 0)
        if limit > 0 and len(tasks) > limit:
            log.info("单轮上限 %d 首,本轮截断 %d 首,余量下轮继续", limit, len(tasks) - limit)
            tasks = tasks[:limit]
        summary["added"] = sum(1 for _, _, k, _ in tasks if k == "new")
        summary["upgraded"] = sum(1 for _, _, k, _ in tasks if k == "upgrade")
        log.info("待下载 %d(新增 %d / 升级 %d)", len(tasks), summary["added"], summary["upgraded"])

        # 4) 磁盘空间预检
        if tasks and not self._disk_ok():
            summary["status"] = "disk_full"
            summary["error"] = "磁盘剩余空间低于 min_free_space 阈值,本轮跳过下载"
            log.warning("%s", summary["error"])
            tasks = []
        if tasks:
            self._download_all(tasks, details, summary)

        # 5) 导出 m3u8、删除处理、回收目录清理
        self._set_progress("整理输出", "")
        self._export_m3u8()
        summary["removed"] = self._cleanup_missing(desired, prev_members)
        self._cleanup_trash()

    def _fetch_source(self, source: str) -> list[int]:
        if source == "cloud":
            return self.api.user_cloud()
        if source == "daily":
            return self.api.recommend_songs()
        if source == "fm":
            return self.api.personal_fm()
        return []

    def _disk_ok(self) -> bool:
        try:
            free_gb = shutil.disk_usage(self._music_root()).free / (1024 ** 3)
            threshold = float(self.cfg.d["limits"].get("min_free_space") or 2)
            return free_gb >= threshold
        except OSError:
            return True

    def _cleanup_trash(self) -> None:
        """清理 _trash 中超过保留期的文件。"""
        days = int(self.cfg.d.get("trash_retention_days") or 30)
        if days <= 0:
            return
        trash = self._music_root() / "_trash"
        if not trash.is_dir():
            return
        cutoff = time.time() - days * 86400
        for p in trash.iterdir():
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    log.info("清理回收文件:%s", p.name)
            except OSError:
                pass

    def _note_fail(self, sid: int, reason: str = "") -> None:
        """整首下载失败:状态置 failed、记录原因与时间、失败计数 +1(供 24h 退避)。"""
        row = self.state.song(sid) or {}
        self.state.upsert_song(sid=sid, status="failed",
                               last_error=str(reason or "未知原因"),
                               fail_count=int(row.get("fail_count") or 0) + 1,
                               downloaded_at=now_str())

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

    def _build_meta(self, sid: int, detail: dict, member: tuple[str, int],
                    platform: str = "netease", platform_id: str | None = None) -> dict:
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
        """并发下载。流地址在任务内即时获取(取到立刻用),
        避免预取排队导致 CDN 签名链接过期(403)。"""
        kind_of = {sid: kind for sid, _, kind, _ in tasks}
        plat_of = {sid: plat for sid, _, _, plat in tasks}
        total = len(tasks)

        conc = max(1, int(self.cfg.d["limits"].get("download_concurrency") or 3))
        self.recent = []
        self.active = {}
        done = 0
        pool = ThreadPoolExecutor(max_workers=conc)
        try:
            futs = {
                pool.submit(self._download_one, sid, lvl, kind_of[sid],
                            details.get(sid) or {}, plat_of[sid]): sid
                for sid, lvl, kind, plat in tasks
            }
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
                    if r.get("warns"):
                        summary["partial"] += 1
                        summary["partial_failures"].append({
                            "title": r.get("title") or "", "artist": r.get("artist") or "",
                            "items": r.get("warns") or []})
                else:
                    summary["failed"] += 1
                    summary["failures"].append(r)
                    self._note_fail(futs[fut], r.get("reason") or "")  # 失败计数 + 原因,供退避与查看
                    log.warning("失败 %s - %s:%s", r.get("artist"), r.get("title"), r.get("reason"))
        finally:
            pool.shutdown(wait=True)

    def _dl_tick(self, sid: int, downloaded: int, total: int) -> None:
        a = self.active.get(sid)
        if a is not None:
            a["downloaded"] = downloaded
            if total:
                a["total"] = total

    def _fetch_lyric(self, meta: dict, sid: int) -> tuple[str, bool]:
        """返回 (歌词文本, 是否成功)。yrc 开启时优先逐字歌词,失败回退普通歌词。"""
        plat = meta.get("platform") or "netease"
        prov = self._prov_by_platform(plat)
        raw = meta.get("platform_id") or str(sid)
        if self.cfg.d["lyrics"].get("yrc") and hasattr(prov, "lyric_new"):
            y = prov.lyric_new(raw)
            if y:
                return yrc.yrc_to_lrc(y), True
        lr = prov.lyric(raw)
        if lr is None:
            return "", False
        return lr, True

    def _download_one(self, sid: int, want_level: str, kind: str, detail: dict,
                      platform: str = "netease") -> dict:
        provider = self._prov_by_platform(platform)
        members = self._membership([sid])
        meta = self._build_meta(sid, detail, members.get(sid, ("未命名歌单", 0)), platform)
        info = self._album_info(meta, detail)
        meta["genre"] = self._genre_of(info)
        meta["label"] = str(info.get("company") or "")
        fail = lambda reason: {"ok": False, "title": meta["title"], "artist": meta["artist"], "reason": reason}  # noqa: E731

        if hasattr(provider, "download_audio"):  # yt-dlp 引擎(B 站)
            self.active[sid] = {"title": meta["title"], "artist": meta["artist"],
                                "downloaded": 0, "total": 0}
            try:
                real, size = provider.download_audio(
                    meta["platform_id"], self.tmp_dir,
                    progress_cb=lambda dn, dt: self._dl_tick(sid, dn, dt))
                ext = real.suffix.lstrip(".").lower() or "m4a"
                md5hex, level, entry_br = "", "bestaudio", 0
            except _Fail as e:
                return fail(e.reason)
            except Exception as e:
                return fail(f"下载失败:{e.__class__.__name__}")
            finally:
                self.active.pop(sid, None)
        else:  # URL 取流引擎(网易云 / QQ)
            chain = self.cfg.quality_chain if platform == "netease" else provider.level_chain
            entry = None
            for lvl in provider.chain_from(want_level, chain):
                try:
                    e = provider.song_url(meta["platform_id"], lvl, detail)
                except ApiError:
                    continue
                if e.get("url") and not e.get("freeTrialInfo"):
                    entry = e
                    break
            if not entry:
                return fail("无可用音源(试听或下架)")

            # 下载;403/410 视为签名链接过期或失效,自动重取一次新链接重试
            for attempt in (1, 2):
                self.active[sid] = {"title": meta["title"], "artist": meta["artist"],
                                    "downloaded": 0, "total": int(entry.get("size") or 0)}
                try:
                    try:
                        tmp, md5hex, size = downloader.download(
                            entry["url"], self.tmp_dir,
                            progress=lambda dn, dt: self._dl_tick(sid, dn, dt),
                            headers=provider.download_headers(),
                            proxy=self.cfg.d["limits"].get("proxy") or None)
                        break
                    finally:
                        self.active.pop(sid, None)
                except httpx.HTTPStatusError as e:
                    code = e.response.status_code if e.response is not None else 0
                    if attempt == 1 and code in (403, 410):
                        try:
                            e2 = provider.song_url(meta["platform_id"],
                                                   str(entry.get("level") or want_level), detail)
                        except ApiError:
                            return fail(f"下载失败:HTTP {code}")
                        if e2.get("url") and not e2.get("freeTrialInfo"):
                            entry = e2
                            continue
                    return fail(f"下载失败:HTTP {code}" if code else f"下载失败:{e.__class__.__name__}")
                except Exception as e:
                    return fail(f"下载失败:{e.__class__.__name__}")
            else:  # 两次尝试均未成功落盘
                return fail("下载失败:重试后仍失败")
            if not downloader.verify(tmp, str(entry.get("md5") or ""), int(entry.get("size") or 0)):
                tmp.unlink(missing_ok=True)
                return fail("校验失败(MD5/大小不符)")
            ext = str(entry.get("type") or "mp3").lower()
            real = tmp.with_suffix(f".{ext}")
            tmp.rename(real)
            level = str(entry.get("level") or want_level)
            entry_br = int(entry.get("br") or 0)

        # 次要失败收集:歌词/封面/标签/NFO 任一失败记为「部分未成功」,不影响下载成功判定
        warns: list[str] = []
        lrc = ""
        if self.cfg.d["lyrics"].get("lrc", True) or self.cfg.d["lyrics"].get("embed", True):
            lrc, lrc_ok = self._fetch_lyric(meta, sid)
            if not lrc_ok:
                warns.append("歌词获取失败")
        al_pic = ((detail or {}).get("al") or {}).get("picUrl")
        cover = self._cover_for(meta, detail) if al_pic else None
        if al_pic and cover is None:
            warns.append("封面获取失败")
        tag_warn = tagger.tag_file(real, meta, cover, lrc if self.cfg.d["lyrics"].get("embed", True) else None)
        if tag_warn:
            warns.append(tag_warn)

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
            level=str(level),
            br=int(entry_br), size=size, md5=md5hex,
            downloaded_at=now_str(), status="ok", fail_count=0, last_error="",
        )
        if self.cfg.d.get("nfo", True):
            try:
                self._write_nfo(final, meta, detail)
            except Exception as e:
                warns.append("NFO 写入失败")
                log.warning("NFO 写入失败 %s: %s", final.parent, e)
        log.info("[%s] %s - %s (%s)", kind, meta["artist"], meta["title"], level)
        return {"ok": True, "kind": kind, "level": str(level),
                "title": meta["title"], "artist": meta["artist"], "warns": warns}

    def _write_nfo(self, final: Path, meta: dict, detail: dict) -> None:
        """单曲 <歌名>.nfo(所有布局都写);专辑/歌手级 NFO 跟随布局目录结构。"""
        d = detail or {}
        info = self._album_info(meta, detail)
        releasedate = ""
        if info.get("publishTime"):
            try:
                releasedate = _dt.datetime.utcfromtimestamp(int(info["publishTime"]) / 1000).strftime("%Y-%m-%d")
            except (ValueError, OSError, OverflowError):
                releasedate = ""
        genre = self._genre_of(info)
        ar = d.get("ar") or []
        ncm_aid = str(ar[0].get("id") or "") if ar else ""

        # 单曲级:<歌名>.nfo
        nfo.write_song_nfo(
            final.with_suffix(".nfo"), title=meta["title"], artist=meta["artist"],
            album=meta["album"], albumartist=meta["album_artist"],
            track=int(meta.get("track") or 0), disc=int(meta.get("disc") or 0),
            year=str(meta.get("date") or ""), duration=int((d.get("dt") or 0) // 1000),
            genre=str(genre), ncm_id=str(meta.get("sid") or ""),
            platform=str(meta.get("platform") or "netease"),
        )
        if self.cfg.layout == "album":
            album_dir = final.parent
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
            nfo.write_artist_nfo(album_dir.parent / "artist.nfo", name=meta["album_artist"],
                                 genre=str(genre), ncm_id=ncm_aid,
                                 platform=str(meta.get("platform") or "netease"))
        elif self.cfg.layout == "artist":
            nfo.write_artist_nfo(final.parent / "artist.nfo", name=meta["album_artist"],
                                 genre=str(genre), ncm_id=ncm_aid,
                                 platform=str(meta.get("platform") or "netease"))
        # flat/playlist 布局无歌手/专辑目录结构,不写这两级 NFO

    def _album_info(self, meta: dict, detail: dict) -> dict:
        """专辑详情(流派/厂牌/发行日/简介),按 平台+专辑 ID 缓存;无该能力的平台返回空。"""
        platform = meta.get("platform") or "netease"
        al = (detail or {}).get("al") or {}
        aid = str(al.get("id") or "")
        if not aid or aid == "0":
            return {}
        key = f"{platform}:{aid}"
        if key not in self._album_cache:
            try:
                self._album_cache[key] = self._prov_by_platform(platform).album_info(detail) or {}
            except Exception:
                self._album_cache[key] = {}
        return self._album_cache[key] or {}

    @staticmethod
    def _genre_of(info: dict) -> str:
        g = info.get("genre") or ""
        if isinstance(g, list):
            return ", ".join(str(x) for x in g)
        return str(g or "")

    def _cover_for(self, meta: dict, detail: dict) -> bytes | None:
        al = (detail or {}).get("al") or {}
        url = al.get("picUrl") or ""
        if not url:
            return None
        key = f"{meta.get('platform')}:{url}"
        if key in self._cover_cache:
            return self._cover_cache[key]
        data = None
        try:
            fetch_url = f"{url}?param=1500y1500" if meta.get("platform") == "netease" else url
            prov = self._prov_by_platform(meta.get("platform"))
            r = prov.client.get(fetch_url, headers=prov.download_headers(), timeout=30)
            if r.status_code == 200 and r.content[:2] in (b"\xff\xd8", b"\x89P"):
                data = r.content
        except Exception:
            data = None
        self._cover_cache[key] = data
        return data

    # ---------- 本地音乐列表 / 刮削 / 编辑 ----------
    _LOCAL_EXTS = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".wav", ".ape"}

    def _music_root(self) -> Path:
        """音乐输出根目录(可由面板配置覆盖)。"""
        return Path(self.cfg.d.get("music_dir") or MUSIC_DIR)

    @staticmethod
    def local_sid(path: Path) -> int:
        """无网易云 ID 的本地文件用路径派生的负数 sid 区分。"""
        import zlib
        return -(zlib.crc32(str(path).encode("utf-8")) % 900_000_000 + 1)

    @staticmethod
    def _read_file_tags(path: Path) -> dict:
        meta = {"title": path.stem, "artist": "", "album": ""}
        try:
            from mutagen import File as _MFile
            mf = _MFile(str(path), easy=True)
            if mf is None:
                return meta
            def first(k: str) -> str:
                v = mf.get(k) or [""]
                return str(v[0]) if v and v[0] is not None else ""
            meta["title"] = first("title") or path.stem
            meta["artist"] = first("artist")
            meta["album"] = first("album")
        except Exception:
            pass
        return meta

    def _path_sid(self, p: Path) -> int | None:
        rp = str(p.resolve())
        for s in self.state.all_songs():
            if s.get("file_path") and str(Path(s["file_path"]).resolve()) == rp:
                return int(s["sid"])
        return None

    def local_files(self) -> list[dict]:
        """列出音乐目录全部音频文件:含当前信息(库内或文件内嵌)、缺失状态、重复标记、失败原因。"""
        root = self._music_root()
        by_path: dict[str, dict] = {}
        for s in self.state.all_songs():
            if s.get("file_path"):
                try:
                    by_path.setdefault(str(Path(s["file_path"]).resolve()), s)
                except Exception:
                    pass
        counts = self.state.membership_counts()
        out: list[dict] = []
        for p in root.rglob("*"):
            if not (p.is_file() and p.suffix.lower() in self._LOCAL_EXTS):
                continue
            rel = p.relative_to(root)
            if rel.parts and rel.parts[0] in ("_trash",):
                continue
            rp = str(p.resolve())
            row = by_path.get(rp)
            st = p.stat()
            if row:
                title = row.get("title") or ""
                artist = row.get("artist") or ""
                album = row.get("album") or ""
                status = row.get("status") or ""
                level = row.get("level") or ""
                last_error = row.get("last_error") or ""
                dup = counts.get(int(row["sid"]), 0) > 1
            else:
                t = self._read_file_tags(p)
                title, artist, album = t["title"], t["artist"], t["album"]
                status, level, last_error, dup = "", "", "", False
            missing = (row is None) or (status != "ok") or (not title and not artist)
            out.append({
                "path": str(p), "name": p.name, "size": st.st_size, "mtime": int(st.st_mtime),
                "title": title, "artist": artist, "album": album,
                "in_db": row is not None, "status": status, "level": level, "missing": missing,
                "dup": dup, "last_error": last_error,
            })
        return sorted(out, key=lambda x: x["path"].casefold())

    def _scrape(self, sid: int, p: Path, detail: dict, platform: str = "netease",
                platform_id: str | None = None) -> dict:
        """用平台元数据补全本地文件:标签/封面/歌词/NFO;按布局整理到规范位置并入库。"""
        meta = self._build_meta(sid, detail, ("未命名歌单", 0), platform,
                                platform_id or str(detail.get("_pid") or ""))
        info = self._album_info(meta, detail)
        meta["genre"] = self._genre_of(info)
        meta["label"] = str(info.get("company") or "")
        warns: list[str] = []
        layout = self.cfg.layout
        final = self._organize_target(p, meta, layout) if layout in ("flat", "artist", "album") else p
        moved = final != p

        lrc = ""
        if self.cfg.d["lyrics"].get("lrc", True) or self.cfg.d["lyrics"].get("embed", True):
            lrc, lrc_ok = self._fetch_lyric(meta, sid)
            if not lrc_ok:
                warns.append("歌词获取失败")
        al_pic = ((detail or {}).get("al") or {}).get("picUrl")
        cover = self._cover_for(meta, detail) if al_pic else None
        if al_pic and cover is None:
            warns.append("封面获取失败")
        tw = tagger.tag_file(p, meta, cover, lrc if self.cfg.d["lyrics"].get("embed", True) else None)
        if tw:
            warns.append(tw)

        if moved:
            try:
                final.parent.mkdir(parents=True, exist_ok=True)
                downloader.shutil_move(p, final)
                log.info("[organize] %s -> %s", p.name, final)
            except Exception as e:
                warns.append(f"移动失败:{e.__class__.__name__}")
                final = p  # 移动失败则留在原位
                moved = False
        if moved and p.with_suffix(".lrc").exists():
            try:
                downloader.shutil_move(p.with_suffix(".lrc"), final.with_suffix(".lrc"))
            except Exception:
                pass

        self.state.upsert_song(
            sid=sid, title=meta["title"], artist=meta["artist"], album=meta["album"],
            file_path=str(final), ext=final.suffix.lstrip(".").lower() or "mp3",
            track_no=int(meta.get("track") or 0), level="manual",
            downloaded_at=now_str(), status="ok", fail_count=0, last_error="",
        )
        if lrc and self.cfg.d["lyrics"].get("lrc", True):
            try:
                final.with_suffix(".lrc").write_text(lrc, encoding="utf-8")
            except Exception:
                warns.append("歌词文件写入失败")
        if self.cfg.d.get("nfo", True):
            try:
                self._write_nfo(final, meta, detail)
            except Exception:
                warns.append("NFO 写入失败")
        try:  # 刮削/匹配/编辑后即时刷新歌单 m3u8
            self._export_m3u8()
        except Exception:
            pass
        log.info("[scrape] %s - %s <- %s", meta["artist"], meta["title"], final.name)
        return {"ok": True, "title": meta["title"], "artist": meta["artist"],
                "path": str(final), "warns": warns}

    def _organize_target(self, src: Path, meta: dict, mode: str) -> Path:
        """刮削分类目标路径;同名冲突自动追加序号,不覆盖已有文件。"""
        root = self._music_root()
        title = tagger.sanitize(meta.get("title") or "未知标题")
        artist = tagger.sanitize(meta.get("artist") or "未知歌手")
        album = tagger.sanitize(meta.get("album") or "未知专辑")
        ext = src.suffix.lower() or ".mp3"
        if mode == "flat":
            base = root
        elif mode == "artist":
            base = root / artist
        else:  # album
            base = root / artist / album
        cand = base / f"{title}{ext}"
        i = 2
        while cand.exists() and cand.resolve() != src.resolve():
            cand = base / f"{title} ({i}){ext}"
            i += 1
        return cand

    def match_local_file(self, sid: int, filepath: str, platform: str = "netease",
                         platform_id: str | None = None) -> dict:
        """手动匹配:搜索选定的平台曲目补全本地文件。"""
        p = Path(filepath)
        if not p.is_file():
            raise ValueError("文件不存在")
        raw = platform_id or (sid if platform == "netease" else raw_id_of(sid))
        prov = self._prov_by_platform(platform)
        d = prov.song_detail([raw])
        if not d:
            raise ValueError("未找到该歌曲")
        return self._scrape(sid, p, d[0], platform, raw)

    def refetch_local(self, filepath: str) -> dict:
        """重新刮削:已入库按原 sid 重新抓取;未入库按文件名/内嵌信息自动搜索匹配。"""
        p = Path(filepath)
        if not p.is_file():
            raise ValueError("文件不存在")
        sid = self._path_sid(p)
        if sid is None:
            t = self._read_file_tags(p)
            kw = f"{t['title']} {t['artist']}".strip() or p.stem
            res = self.api.search(kw, limit=5)
            if not res:
                raise ValueError("未能自动匹配到网易云曲目,请使用手动匹配")
            sid = int(res[0]["id"])
        row = self.state.song(sid) or {}
        platform = row.get("platform") or "netease"
        raw = row.get("platform_id") or (sid if platform == "netease" else raw_id_of(sid))
        prov = self._prov_by_platform(platform)
        d = prov.song_detail([raw])
        if not d:
            raise ValueError("未找到该歌曲")
        return self._scrape(sid, p, d[0], platform, raw)

    def edit_local(self, filepath: str, title: str, artist: str = "", album: str = "",
                   track: int = 0) -> dict:
        """手动修改信息:直接按用户填写写入标签与 NFO 并入库。"""
        p = Path(filepath)
        if not p.is_file():
            raise ValueError("文件不存在")
        sid = self._path_sid(p) or self.local_sid(p)
        platform = (self.state.song(sid) or {}).get("platform") or "netease"
        meta = {"title": title or p.stem, "artist": artist or "未知歌手",
                "album": album or "未知专辑", "album_artist": artist or "未知歌手",
                "track": int(track or 0), "disc": 0, "date": ""}
        warns: list[str] = []
        tw = tagger.tag_file(p, meta, None, None)
        if tw:
            warns.append(tw)
        layout = self.cfg.layout
        final = self._organize_target(p, meta, layout) if layout in ("flat", "artist", "album") else p
        if final != p:
            try:
                final.parent.mkdir(parents=True, exist_ok=True)
                downloader.shutil_move(p, final)
                if p.with_suffix(".lrc").exists():
                    downloader.shutil_move(p.with_suffix(".lrc"), final.with_suffix(".lrc"))
                log.info("[organize] %s -> %s", p.name, final)
            except Exception as e:
                warns.append(f"移动失败:{e.__class__.__name__}")
                final = p
        self.state.upsert_song(
            sid=sid, title=meta["title"], artist=meta["artist"], album=meta["album"],
            file_path=str(final), ext=final.suffix.lstrip(".").lower() or "mp3",
            track_no=int(meta.get("track") or 0), level="manual",
            downloaded_at=now_str(), status="ok",
        )
        if self.cfg.d.get("nfo", True):
            try:
                nfo.write_song_nfo(final.with_suffix(".nfo"), title=meta["title"], artist=meta["artist"],
                                   album=meta["album"], albumartist=meta["artist"],
                                   track=int(meta.get("track") or 0))
            except Exception:
                warns.append("NFO 写入失败")
        log.info("[edit] %s - %s <- %s", meta["artist"], meta["title"], p.name)
        return {"ok": True, "title": meta["title"], "artist": meta["artist"], "warns": warns}

    # ---------- 输出整理 ----------
    def _export_m3u8(self) -> None:
        allowed = set(self.cfg.playlist_ids())
        allowed |= {SOURCE_PID[e["source"]] for e in (self.cfg.d.get("playlists") or [])
                    if e.get("source") in SOURCE_PID}
        for pl in self.state.playlists():
            pid = pl["pid"]
            if pid not in allowed:
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
            if pid < 0:
                continue  # 特殊源(云盘/日推/私人FM)成员是快照,不做删除/镜像处理
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
