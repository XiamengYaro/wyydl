"""通知:飞书自定义机器人 webhook(可选签名),以及通用 JSON webhook。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

import httpx

log = logging.getLogger("wyydl.notify")


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
        f" / 失败 {summary.get('failed', 0)} / NCM入库 {summary.get('ncm', 0)}"
        f" / 移除 {summary.get('removed', 0)}"
    )
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
