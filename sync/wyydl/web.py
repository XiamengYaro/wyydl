"""Web 控制面板(FastAPI):状态总览、手动同步、歌单管理、扫码登录、配置与日志。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import __version__, config as cfgmod, notify
from .api import NcmApi
from .config import LOG_DIR, Config
from .state import State
from .syncer import SyncEngine

log = logging.getLogger("wyydl.web")

_INDEX = Path(__file__).parent / "static" / "index.html"

_QR_MSG = {800: "二维码已过期", 801: "等待扫码", 802: "已扫码,请在手机上确认", 803: "登录成功"}


@dataclass
class AppContext:
    cfg: Config
    state: State
    api: NcmApi
    engine: SyncEngine
    next_run: Callable[[], str] = lambda: ""  # noqa: E731
    on_reschedule: Callable[[], None] = lambda: None  # noqa: E731


def _guard(ctx: AppContext):
    async def dep(x_token: Optional[str] = Header(default=None, alias="X-Token"),
                  token: Optional[str] = None) -> None:
        want = str((ctx.cfg.d.get("web") or {}).get("token") or "")
        if want and x_token != want and token != want:
            raise HTTPException(status_code=401, detail="需要访问令牌")
    return dep


def create_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="wyydl", docs_url=None, redoc_url=None, openapi_url=None)
    guard = _guard(ctx)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX.read_text(encoding="utf-8")

    # ---------- 状态 ----------
    @app.get("/api/status", dependencies=[Depends(guard)])
    def status() -> dict:
        stats = ctx.state.playlist_stats()
        pls = []
        for p in ctx.state.playlists():
            st = stats.get(p["pid"], {})
            pls.append({
                "id": p["pid"], "name": p["name"], "total": p["track_count"],
                "ok": st.get("ok") or 0, "pending": st.get("pending") or 0,
                "failed": st.get("bad") or 0,
                "configured": p["pid"] in ctx.cfg.playlist_ids(),
                "last_sync": p["last_sync"],
            })
        prog = dict(ctx.engine.progress or {})
        prog["recent"] = list(getattr(ctx.engine, "recent", []))[-8:]
        prog["active"] = list(getattr(ctx.engine, "active", {}).values())[:10]
        return {
            "version": __version__,
            "cookie_ok": ctx.engine.cookie_ok(),
            "running": ctx.engine.running,
            "progress": prog,
            "next_run": ctx.next_run(),
            "last_run": ctx.state.last_run(),
            "playlists": pls,
            "layout": ctx.cfg.layout,
            "schedule": ctx.cfg.schedule,
        }

    @app.post("/api/sync", dependencies=[Depends(guard)])
    def sync(body: Optional[SyncBody] = None) -> dict:
        ids = body.playlists if body else None
        if not ctx.engine.try_run(ids, trigger="manual"):
            raise HTTPException(status_code=409, detail="已有同步任务在运行")
        return {"started": True}

    @app.get("/api/runs", dependencies=[Depends(guard)])
    def runs(limit: int = 15) -> dict:
        return {"runs": ctx.state.runs(max(1, min(limit, 50)))}

    @app.get("/api/tracks/{pid}", dependencies=[Depends(guard)])
    def tracks(pid: int) -> dict:
        out = []
        for sid, pos in ctx.state.playlist_songs(pid):
            s = ctx.state.song(sid) or {}
            out.append({
                "sid": sid, "pos": pos, "title": s.get("title") or str(sid),
                "artist": s.get("artist") or "", "album": s.get("album") or "",
                "status": s.get("status") or "new", "level": s.get("level") or "",
            })
        out.sort(key=lambda x: x["pos"])
        return {"tracks": out}

    @app.get("/api/logs", dependencies=[Depends(guard)])
    def logs(lines: int = 300) -> dict:
        f = LOG_DIR / "wyydl.log"
        if not f.exists():
            return {"text": ""}
        blob = f.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"text": "\n".join(blob[-max(10, min(lines, 2000)):])}

    # ---------- 歌单管理 ----------
    @app.post("/api/playlists", dependencies=[Depends(guard)])
    async def add_playlist(payload: dict) -> dict:
        try:
            pid = int(str(payload.get("id")).strip())
        except (TypeError, ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="歌单 ID 必须是数字")
        pls = ctx.cfg.d.setdefault("playlists", [])
        if any(int(p.get("id", -1)) == pid for p in pls):
            raise HTTPException(status_code=409, detail="该歌单已存在")
        name = str(payload.get("name") or "").strip()
        pls.append({"id": pid, **({"name": name} if name else {})})
        ctx.cfg.save()
        started = ctx.engine.try_run([pid], trigger="add")  # 加入后立即同步一次
        return {"added": pid, "sync_started": bool(started)}

    @app.get("/api/account/playlists", dependencies=[Depends(guard)])
    def account_playlists() -> dict:
        uid = (ctx.api.user_account() or {}).get("userId")
        if not uid:
            raise HTTPException(status_code=401, detail="未登录或凭证失效,请先扫码")
        configured = set(ctx.cfg.playlist_ids())
        out = []
        for p in ctx.api.user_playlists(int(uid)):
            try:
                pid = int(p.get("id"))
            except (TypeError, ValueError):
                continue
            out.append({"id": pid, "name": p.get("name") or str(pid),
                        "total": p.get("trackCount") or 0, "configured": pid in configured})
        return {"uid": uid, "playlists": out}

    @app.delete("/api/playlists/{pid}", dependencies=[Depends(guard)])
    def remove_playlist(pid: int) -> dict:
        pls = ctx.cfg.d.get("playlists") or []
        if not any(int(p.get("id", -1)) == pid for p in pls):
            raise HTTPException(status_code=404, detail="歌单不存在")
        ctx.cfg.d["playlists"] = [p for p in pls if int(p.get("id", -1)) != pid]
        ctx.cfg.save()
        ctx.state.remove_playlist(pid)  # 本地文件保留,仅解除关联
        return {"removed": pid}

    # ---------- 扫码登录 ----------
    @app.post("/api/login/qr", dependencies=[Depends(guard)])
    def qr_start() -> dict:
        key = ctx.api.qr_key()
        data = ctx.api.qr_create(key)
        return {"key": key, "img": data.get("qrimg"), "url": data.get("qrurl")}

    @app.get("/api/login/qr/poll/{key}", dependencies=[Depends(guard)])
    def qr_poll(key: str) -> dict:
        r = ctx.api.qr_check(key)
        code = int(r.get("code") or 0)
        saved = False
        msg = _QR_MSG.get(code) or str(r.get("message") or "")
        if code == 0:
            msg = "接口响应异常,请查看日志"
        if code == 803:
            m = re.search(r"MUSIC_U=([^;\s]+)", r.get("cookie") or "")
            if m:
                cfgmod.save_music_u(m.group(1))
                ctx.engine._cookie_checked_at = 0.0  # 强制下次立即复查
                saved = True
            else:
                msg = "已确认但未返回凭证,请重新扫码"
        return {"code": code, "message": msg, "saved": saved}

    # ---------- 结构化设置 ----------
    @app.get("/api/settings", dependencies=[Depends(guard)])
    def get_settings() -> dict:
        d = ctx.cfg.d
        delay = d["limits"].get("api_delay") or [1.0, 3.0]
        web = d.get("web") or {}
        nf = d.get("notify") or {}
        return {
            "schedule": d["schedule"], "layout": d["layout"], "naming": d.get("naming") or "",
            "chain": d["quality"].get("chain") or [],
            "upgrade_existing": d["quality"].get("upgrade_existing", True),
            "lrc": d["lyrics"].get("lrc", True), "embed": d["lyrics"].get("embed", True),
            "nfo": d.get("nfo", True),
            "mirror": d.get("mirror", False),
            "notify_type": nf.get("type") or "feishu", "notify_url": nf.get("url") or "",
            "notify_secret": nf.get("secret") or "",
            "notify_events": notify.events_for(d),
            "web_enabled": web.get("enabled", True), "web_port": web.get("port") or 8286,
            "web_token": web.get("token") or "",
            "concurrency": d["limits"].get("download_concurrency") or 3,
            "delay_min": delay[0], "delay_max": delay[1],
        }

    @app.put("/api/settings", dependencies=[Depends(guard)])
    async def put_settings(payload: dict) -> dict:
        from apscheduler.triggers.cron import CronTrigger
        schedule = str(payload.get("schedule") or "").strip()
        try:
            CronTrigger.from_crontab(schedule or "0 4 * * *")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"运行计划不是合法 cron:{e}")
        layout = payload.get("layout") if payload.get("layout") in ("archive", "playlist") else ctx.cfg.layout
        chain = [str(x) for x in (payload.get("chain") or []) if str(x)]
        if not chain:
            raise HTTPException(status_code=400, detail="音质链不能为空")
        try:
            conc = max(1, min(8, int(payload.get("concurrency") or 3)))
            lo = max(0.0, float(payload.get("delay_min") or 1.0))
            hi = max(float(payload.get("delay_max") or lo), lo)
            port = max(1, min(65535, int(payload.get("web_port") or 8286)))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="并发/间隔/端口必须是数字")
        events_in = payload.get("events") or {}
        events = dict(notify.DEFAULT_EVENTS)
        for k in events:
            if k in events_in:
                events[k] = bool(events_in[k])
        new_cfg = Config({
            "schedule": schedule, "layout": layout,
            "naming": str(payload.get("naming") or "").strip(),
            "quality": {"chain": chain, "upgrade_existing": bool(payload.get("upgrade_existing"))},
            "lyrics": {"lrc": bool(payload.get("lrc")), "embed": bool(payload.get("embed"))},
            "nfo": bool(payload.get("nfo")),
            "mirror": bool(payload.get("mirror")),
            "notify": {
                "type": "feishu" if payload.get("notify_type") == "feishu" else "webhook",
                "url": str(payload.get("notify_url") or "").strip(),
                "secret": str(payload.get("notify_secret") or "").strip(),
                "events": events,
            },
            "web": {
                "enabled": bool(payload.get("web_enabled")), "port": port,
                "token": str(payload.get("web_token") or "").strip(),
            },
            "limits": {"download_concurrency": conc, "api_delay": [lo, hi]},
        })
        new_cfg.d["playlists"] = ctx.cfg.d.get("playlists") or []  # 歌单在别处管理,保持不变
        new_cfg.d["api_base"] = ctx.cfg.d.get("api_base") or new_cfg.d["api_base"]  # 高级字段不经表单,保持原值
        ctx.cfg.d.clear()
        ctx.cfg.d.update(new_cfg.d)
        ctx.cfg.save()
        ctx.api.base = str(ctx.cfg.d["api_base"]).rstrip("/")
        ctx.on_reschedule()
        return {"saved": True}

    # ---------- 配置 ----------
    @app.get("/api/config", dependencies=[Depends(guard)])
    def get_config() -> dict:
        return {"content": yaml.safe_dump(ctx.cfg.d, allow_unicode=True, sort_keys=False)}

    @app.put("/api/config", dependencies=[Depends(guard)])
    async def put_config(payload: dict) -> dict:
        from apscheduler.triggers.cron import CronTrigger
        try:
            data = yaml.safe_load(payload.get("content") or "")
        except yaml.YAMLError as e:
            raise HTTPException(status_code=400, detail=f"YAML 解析失败:{e}")
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="配置必须是 YAML 映射")
        try:
            CronTrigger.from_crontab(str(data.get("schedule") or "0 4 * * *"))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"schedule 不是合法 cron:{e}")
        new_cfg = Config(data)
        ctx.cfg.d.clear()
        ctx.cfg.d.update(new_cfg.d)
        ctx.cfg.save()
        ctx.api.base = str(ctx.cfg.d["api_base"]).rstrip("/")
        ctx.on_reschedule()
        return {"saved": True}

    @app.post("/api/notify/test", dependencies=[Depends(guard)])
    def test_notify() -> dict:
        notify.notify(ctx.cfg.d, "【wyydl】测试通知:通道配置成功")
        return {"sent": True}

    # ---------- 本地信息缺失识别与手动匹配 ----------
    @app.get("/api/local/missing", dependencies=[Depends(guard)])
    def local_missing() -> dict:
        return {"files": ctx.engine.local_missing()}

    @app.get("/api/search", dependencies=[Depends(guard)])
    def search_songs(keywords: str = "", limit: int = 15) -> dict:
        kw = keywords.strip()
        if not kw:
            return {"songs": []}
        out = []
        for s in ctx.api.search(kw, min(30, max(1, limit))):
            artists = " / ".join(a.get("name") for a in (s.get("ar") or []) if a.get("name"))
            out.append({
                "id": s.get("id"), "name": s.get("name") or "", "artists": artists,
                "album": (s.get("al") or {}).get("name") or "",
                "duration": int((s.get("dt") or 0) // 1000),
            })
        return {"songs": out}

    @app.post("/api/local/match", dependencies=[Depends(guard)])
    async def match_local(body: MatchBody) -> dict:
        try:
            return ctx.engine.match_local_file(body.sid, body.path)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"匹配失败:{e}")

    return app


class SyncBody(BaseModel):
    playlists: Optional[List[int]] = None


class MatchBody(BaseModel):
    sid: int
    path: str
