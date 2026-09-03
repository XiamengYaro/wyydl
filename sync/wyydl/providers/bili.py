"""哔哩哔哩 Provider:扫码登录(passport 二维码)+ yt-dlp 列举/下载。

元数据天然缺失流派/厂牌/歌词,对应字段留空(不计部分未成功告警)。
"""
from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

import httpx

from .base import BaseProvider

log = logging.getLogger("wyydl.bili")

PASSPORT = "https://passport.bilibili.com"
API = "https://api.bilibili.com"


class BiliProvider(BaseProvider):
    platform = "bilibili"
    sid_base = 2_000_000_000_000
    level_chain = ["bestaudio"]

    def __init__(self, cookie_getter, cookie_saver, tmp_dir: Path):
        super().__init__()
        self.cookie_getter = cookie_getter
        self.cookie_saver = cookie_saver  # 回调:保存 cookie 字符串
        self.tmp_dir = tmp_dir
        # B 站有 UA 反爬(缺浏览器 UA 会 412),客户端级挂浏览器 UA + Referer
        self.client = httpx.Client(timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
        })
        self._flat_cache: dict[str, dict] = {}   # 视频/音频 ID → 详情
        self._cookies_file: Path | None = None

    # ---- Cookie / 登录 ----
    def _headers(self) -> dict:
        h = dict(self._default_headers)
        h["Referer"] = "https://www.bilibili.com/"
        cookie = self.cookie_getter() or ""
        if cookie:
            h["Cookie"] = cookie
        return h

    def logged_in(self) -> bool:
        if not (self.cookie_getter() or ""):
            return False
        try:
            r = self.client.get(f"{API}/x/web-interface/nav", headers=self._headers())
            return ((r.json().get("data") or {}).get("isLogin")) is True
        except Exception:
            return False

    def login_qr_start(self) -> dict:
        """生成 B 站登录二维码(本地渲染,返回 dataURL)。"""
        import qrcode
        d = (self.client.get(f"{PASSPORT}/x/passport-login/web/qrcode/generate").json()
             .get("data") or {})
        url = d.get("url") or ""
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        buf = io.BytesIO()
        qr.make(fit=True)
        qr.make_image().save(buf, format="PNG")
        img = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        return {"key": d.get("qrcode_key") or "", "img": img, "url": url}

    def login_qr_poll(self, key: str) -> dict:
        r = self.client.get(f"{PASSPORT}/x/passport-login/web/qrcode/poll",
                            params={"qrcode_key": key})
        j = r.json()
        code = int((j.get("data") or {}).get("code") or -1)
        msg = {0: "登录成功", 86038: "二维码已过期", 86090: "已扫码,请在手机上确认",
               86101: "等待扫码"}.get(code, "等待中")
        saved = False
        if code == 0:
            cookie = "; ".join(f"{k}={v}" for k, v in r.cookies.items())
            if cookie:
                self.cookie_saver(cookie)
                saved = True
        return {"code": code, "message": msg, "saved": saved}

    def _cookies_file(self) -> Path | None:
        """把 Cookie 串写成 yt-dlp 需要的 Netscape cookies.txt。"""
        cookie = self.cookie_getter() or ""
        if not cookie:
            return None
        if self._cookies_file and self._cookies_file.exists():
            return self._cookies_file
        lines = ["# Netscape HTTP Cookie File"]
        for kv in cookie.split(";"):
            if "=" not in kv:
                continue
            k, v = kv.strip().split("=", 1)
            lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{k}\t{v}")
        self._cookies_file = self.tmp_dir / "bili_cookies.txt"
        self._cookies_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self._cookies_file

    def _ydl_opts(self, extra: dict | None = None) -> dict:
        opts = {"quiet": True, "no_warnings": True, "socket_timeout": 30,
                "retries": 3, "noplaylist": True,
                "http_headers": {"User-Agent": self.client.headers.get("user-agent"),
                                 "Referer": "https://www.bilibili.com/"}}
        cf = self._cookies_file()
        if cf:
            opts["cookiefile"] = str(cf)
        opts.update(extra or {})
        return opts

    # ---- yt-dlp 列举/详情 ----
    def _flat(self, url: str) -> dict:
        import yt_dlp
        with yt_dlp.YoutubeDL(self._ydl_opts({"extract_flat": "in_playlist"})) as y:
            return y.extract_info(url, download=False) or {}

    @staticmethod
    def _norm_entry(e: dict) -> dict:
        rid = str(e.get("id") or "")
        uploader = e.get("uploader") or e.get("channel") or "B站"
        thumbs = e.get("thumbnails") or []
        pic = (thumbs[-1].get("url") if thumbs else "") or e.get("thumbnail") or ""
        return {
            "id": rid,
            "_pid": rid,
            "name": e.get("title") or rid,
            "ar": [{"name": uploader}],
            "al": {"name": e.get("playlist_title") or uploader or "B站", "id": 0, "picUrl": pic},
            "dt": int((e.get("duration") or 0) * 1000),
            "no": 0,
            "publishTime": 0,
            "privilege": {"maxBrLevel": "bestaudio", "dlLevel": "bestaudio"},
        }

    @staticmethod
    def _url_of(rid: str) -> str:
        if rid.upper().startswith("BV"):
            return f"https://www.bilibili.com/video/{rid}"
        return f"https://www.bilibili.com/video/av{rid}"

    def playlist_detail(self, target: str) -> dict:
        info = self._flat(target)
        return {"name": info.get("title") or target}

    def playlist_tracks(self, target: str, pl: dict | None = None) -> list[dict]:
        info = self._flat(target)
        out = []
        for e in info.get("entries") or []:
            t = self._norm_entry(e)
            self._flat_cache[t["id"]] = t
            out.append(t)
        return out

    def song_detail(self, raw_ids: list[str]) -> list[dict]:
        out = []
        for rid in raw_ids:
            key = str(rid)
            t = self._flat_cache.get(key)
            if t is None:
                try:
                    t = self._norm_entry(self._flat(self._url_of(key)))
                    self._flat_cache[key] = t
                except Exception as e:
                    log.warning("B站详情获取失败 %s: %s", key, e)
                    continue
            out.append(t)
        return out

    def search(self, keywords: str, limit: int = 15) -> list[dict]:
        info = self._flat(f"bilisearch:{keywords}")
        out = [self._norm_entry(e) for e in (info.get("entries") or [])[:limit]]
        return out

    # ---- 下载(yt-dlp 音频) ----
    def download_audio(self, raw_id: str, tmp_dir: Path, progress_cb=None) -> tuple[Path, int]:
        """用 yt-dlp 下载 bestaudio 到 tmp_dir,返回 (文件路径, 大小)。"""
        import yt_dlp
        key = str(raw_id)
        hooks = []
        if progress_cb:
            hooks.append(lambda d: progress_cb(int(d.get("downloaded_bytes") or 0),
                                               int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)))
        opts = self._ydl_opts({
            "format": "bestaudio/best",
            "outtmpl": str(tmp_dir / f".bili-{key}.%(ext)s"),
            "progress_hooks": hooks,
        })
        with yt_dlp.YoutubeDL(opts) as y:
            y.extract_info(self._url_of(key), download=True)
        for p in tmp_dir.glob(f".bili-{key}.*"):
            if p.suffix.lower() != ".part":
                return p, p.stat().st_size
        raise RuntimeError("yt-dlp 未产出音频文件")
