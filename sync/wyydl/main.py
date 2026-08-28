"""入口:默认守护模式(Web 面板 + 定时调度);--once 执行一轮同步后退出。"""
from __future__ import annotations

import argparse
import logging
import time
from logging.handlers import RotatingFileHandler

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import __version__, web as webmod
from .api import NcmApi
from .config import DB_DIR, LOG_DIR, Config, load_music_u
from .state import State
from .syncer import SyncEngine

TZ = "Asia/Shanghai"
log = logging.getLogger("wyydl.main")


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh = RotatingFileHandler(LOG_DIR / "wyydl.log", maxBytes=5 * 1024 * 1024,
                             backupCount=3, encoding="utf-8")
    sh = logging.StreamHandler()
    fh.setFormatter(fmt)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)


def build():
    cfg = Config.load()
    state = State(DB_DIR / "state.sqlite3")
    delay = cfg.d["limits"].get("api_delay") or (1.0, 3.0)
    api = NcmApi(cfg.api_base, load_music_u, (float(delay[0]), float(delay[1])))
    engine = SyncEngine(cfg, state, api)
    return cfg, state, api, engine


def main() -> None:
    ap = argparse.ArgumentParser("wyydl 网易云歌单同步")
    ap.add_argument("--once", action="store_true", help="执行一轮同步后退出(供 fnOS 计划任务调用)")
    ap.add_argument("--playlist", action="append", type=int, help="仅同步指定歌单 ID,可重复")
    ap.add_argument("--port", type=int, default=None, help="覆盖 Web 面板端口")
    args = ap.parse_args()

    setup_logging()
    log.info("wyydl %s 启动", __version__)

    cfg, state, api, engine = build()

    if args.once:
        engine.run_once(args.playlist or None, trigger="once")
        return

    sched = BackgroundScheduler(timezone=TZ)
    sched.add_job(lambda: engine.run_once(None, "schedule"),
                  CronTrigger.from_crontab(cfg.schedule, timezone=TZ),
                  id="sync", replace_existing=True, max_instances=1, coalesce=True)
    sched.start()

    def next_run() -> str:
        j = sched.get_job("sync")
        if j and j.next_run_time:
            return j.next_run_time.astimezone().isoformat(timespec="seconds")
        return ""

    def reschedule() -> None:
        try:
            sched.reschedule_job("sync", trigger=CronTrigger.from_crontab(cfg.schedule, timezone=TZ))
            log.info("调度已更新:%s", cfg.schedule)
        except ValueError as e:
            log.warning("schedule 更新失败:%s", e)

    import uvicorn
    ctx = webmod.AppContext(cfg=cfg, state=state, api=api, engine=engine,
                            next_run=next_run, on_reschedule=reschedule)
    web = cfg.d.get("web") or {}
    port = args.port or int(web.get("port") or 8286)
    if web.get("enabled", True):
        log.info("Web 面板: http://0.0.0.0:%s", port)
        uvicorn.run(webmod.create_app(ctx), host="0.0.0.0", port=port, log_level="warning")
    else:
        log.info("Web 面板已关闭,仅保留定时调度")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            sched.shutdown(wait=False)


if __name__ == "__main__":
    main()
