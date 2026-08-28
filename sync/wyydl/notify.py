"""通知:飞书自定义机器人 webhook(可选签名),以及通用 JSON webhook。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

import httpx

log = logging.getLogger("wyydl.notify")

# 通知事件默认开关(可在面板设置中逐项勾选)
DEFAULT_EVENTS = {
    "on_start": False,          # 同步开始
    "on_complete": False,       # 每轮完成(检测完成,无变化也推)
    "on_changes": True,         # 有新增/升级/移除变更
    "on_failed": True,          # 有歌曲下载失败
    "on_partial": True,         # 部分未成功(NFO/封面/歌词/标签等)
    "on_login_expired": True,   # 登录失效
    "on_error": True,           # 轮次异常
}


def events_for(cfg: dict) -> dict:
    ev = dict(DEFAULT_EVENTS)
    ev.update(((cfg.get("notify") or {}).get("events") or {}))
    return {k: bool(v) for k, v in ev.items()}


def notify_start(cfg: dict) -> None:
    if events_for(cfg).get("on_start"):
        notify(cfg, "【网易云歌单同步】开始同步…")


def should_notify(cfg: dict, summary: dict) -> bool:
    """按事件开关判断本轮结果是否需要推送。"""
    ev = events_for(cfg)
    st = summary.get("status")
    if st == "login_expired":
        return ev["on_login_expired"]
    if st == "error":
        return ev["on_error"]
    if summary.get("failed") and ev["on_failed"]:
        return True
    if summary.get("partial") and ev["on_partial"]:
        return True
    if (summary.get("added") or summary.get("upgraded") or summary.get("removed")) and ev["on_changes"]:
        return True
    if ev["on_complete"]:
        return True
    return False


def notify_run(cfg: dict, summary: dict) -> None:
    """按事件开关推送本轮同步摘要。"""
    if should_notify(cfg, summary):
        notify(cfg, run_summary_text(summary))


def _feishu_sign(secret: str, ts: int) -> str:
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_feishu(url: str, text: str, secret: str = "", timeout: float = 10.0) -> bool:
    payload: dict = {"msg_type": "text", "content": {"text": text}}
    if secret:
        ts = int(time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = _feishu_sign(secret, ts)
    r = httpx.post(url, json=payload, timeout=timeout)
    ok = r.status_code == 200
    if not ok:
        log.warning("feishu notify failed: %s %s", r.status_code, r.text[:200])
    return ok


def send_generic(url: str, text: str, timeout: float = 10.0) -> bool:
    r = httpx.post(url, json={"text": text}, timeout=timeout)
    ok = r.status_code < 300
    if not ok:
        log.warning("webhook notify failed: %s", r.status_code)
    return ok


def notify(cfg: dict, text: str) -> None:
    n = cfg.get("notify") or {}
    url = (n.get("url") or "").strip()
    if not url:
        return
    try:
        if n.get("type") == "feishu":
            send_feishu(url, text, secret=n.get("secret") or "")
        else:
            send_generic(url, text)
    except Exception as e:  # 通知失败不影响同步
        log.warning("notify error: %s", e)


def run_summary_text(summary: dict) -> str:
    """把一轮同步结果格式化为通知文本。"""
    lines = [f"【网易云歌单同步】{summary.get('finished', '')}  状态: {summary.get('status', 'ok')}"]
    pls = summary.get("playlists") or []
    if pls:
        lines.append("歌单: " + ", ".join(f"{p['name']}({p['total']})" for p in pls))
    lines.append(
        f"新增 {summary.get('added', 0)} / 升级 {summary.get('upgraded', 0)}"
        f" / 失败 {summary.get('failed', 0)} / 移除 {summary.get('removed', 0)}"
    )
    if summary.get("partial"):
        lines.append(f"⚠ 部分未成功(NFO/封面/歌词/标签): {summary['partial']} 首")
        for f in (summary.get("partial_failures") or [])[:5]:
            lines.append(f"  · {f.get('artist', '')} - {f.get('title', '')}: {', '.join(f.get('items') or [])}")
    levels = summary.get("levels") or {}
    if levels:
        detail = ", ".join(f"{k}×{v}" for k, v in sorted(levels.items(), key=lambda x: -x[1]))
        lines.append(f"音质分布: {detail}")
    fails = summary.get("failures") or []
    for f in fails[:10]:
        lines.append(f"✗ {f.get('artist', '')} - {f.get('title', '')} ({f.get('reason', '')})")
    if len(fails) > 10:
        lines.append(f"...等共 {len(fails)} 条失败")
    return "\n".join(lines)
