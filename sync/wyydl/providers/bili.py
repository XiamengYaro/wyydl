"""哔哩哔哩 Provider:直连 B 站 API(登录二维码 + view/playurl 取流)。

目标类型:BV/av 链接或 BV 号(单视频/多 P)、收藏夹 media_id 或收藏夹链接。
元数据天然缺失流派/厂牌/歌词,对应字段留空(不计部分未成功告警)。
"""
from __future__ import annotations

import base64
import httpx
import io
import logging
import qrcode
import zlib

from .base import BaseProvider

log = logging.getLogger("wyydl.bili")

PASSPORT = "https://passport.bilibili.com"
API = "https://api.bilibili.com"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
_LOCAL_EXTS = (".m4a", ".flac", ".mp3", ".webm", ".ogg")


class BiliProvider(BaseProvider):
    platform = "bilibili"
    sid_base = 2_000_000_000_000
    level_chain = ["bestaudio"]

    def __init__(self, cookie_getter, cookie_saver, tmp_dir):
        super().__init__()
        self.cookie_getter = cookie_getter
        self.cookie_saver = cookie_saver
        self.tmp_dir = tmp_dir
        self.client = httpx.Client(timeout=30, headers={
            "User-Agent": _UA,
            "Referer": "https://www.bilibili.com/",
        })
        self._view_cache: dict[str, dict] = {}
        self._cookies_path = None

    # ---- 登录 ----
    def logged_in(self) -> bool:
        if not (self.cookie_getter() or ""):
            return False
        try:
            r = self.client.get(f"{API}/x/web-interface/nav")
            return ((r.json().get("data") or {}).get("isLogin")) is True
        except Exception:
            return False

    def login_qr_start(self) -> dict:
        """生成 B 站登录二维码(纯 Python PNG 工厂,容器无需 Pillow)。"""
        d = (self.client.get(f"{PASSPORT}/x/passport-login/web/qrcode/generate").json()
             .get("data") or {})
        url = d.get("url") or ""
        if not url:
            raise RuntimeError("B 站未返回登录二维码 URL")
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        buf = io.BytesIO()
        try:
            qr.make_image(image_factory=qrcode.image.pure.PyPNGImage).save(buf)
        except Exception:
            qr.make_image().save(buf, format="PNG")
        img = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        return {"key": d.get("qrcode_key") or "", "img": img, "url": url}

    def login_qr_poll(self, key: str) -> dict:
        r = self.client.get(f"{PASSPORT}/x/passport-login/web/qrcode/poll",
                            params={"qrcode_key": key})
        j = r.json()
        raw_code = (j.get("data") or {}).get("code")
        code = int(raw_code) if raw_code is not None else -1  # 0=成功,不可 falsy 兜底
        msg = {0: "登录成功", 86038: "二维码已过期", 86090: "已扫码,请在手机上确认",
               86101: "等待扫码"}.get(code, "等待中")
        saved, cookie = False, ""
        if code == 0:
            cookie = "; ".join(f"{k}={v}" for k, v in r.cookies.items())
            if cookie:
                self.cookie_saver(cookie)
                saved = True
        return {"code": code, "message": msg, "saved": saved, "cookie": cookie}

    # ---- Cookie ----
    def _write_cookies_file(self):
        cookie = self.cookie_getter() or ""
        if not cookie:
            return None
        if self._cookies_path and self._cookies_path.exists():
            return self._cookies_path
        lines = ["# Netscape HTTP Cookie File"]
        for kv in cookie.split(";"):
            if "=" not in kv:
                continue
            k, v = kv.strip().split("=", 1)
            lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{k}\t{v}")
        self._cookies_path = self.tmp_dir / "bili_cookies.txt"
        self._cookies_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self._cookies_path

    # ---- BVID 提取 ----
    @staticmethod
    def _bvid_of(target: str):
        """从链接/输入提取 BV 号;av 号自动转。返回 None 表示不是单视频。"""
        t = str(target).strip()
        import re
        m = re.search(r"(BV[0-9A-Za-z]{10})", t)
        if m:
            return m.group(1)
        m = re.search(r"(?:av|/av)(\d+)", t, re.I)
        if m:
            r = httpx.get(f"{API}/x/web-interface/archive/stat?aid={m.group(1)}",
                          headers={"User-Agent": _UA}, timeout=15)
            return (r.json().get("data") or {}).get("bvid")
        return None

    # ---- view / 收藏夹 ----
    def _view(self, bvid: str) -> dict:
        if bvid in self._view_cache:
            return self._view_cache[bvid]
        r = self.client.get(f"{API}/x/web-interface/view", params={"bvid": bvid})
        r.raise_for_status()
        d = (r.json().get("data") or {})
        self._view_cache[bvid] = d
        return d

    def _fav_tracks(self, media_id: str) -> tuple[str, list[dict]]:
        name, tracks, pn = "", [], 1
        while True:
            r = self.client.get(f"{API}/x/v3/fav/resource/list",
                                params={"media_id": media_id, "ps": 20, "pn": pn},
                                headers=self._headers())
            d = (r.json().get("data") or {})
            name = name or (d.get("info") or {}).get("title") or media_id
            medias = d.get("medias") or []
            for m in medias:
                if not m.get("bvid"):
                    continue
                tracks.append({
                    "id": m["bvid"], "_pid": m["bvid"],
                    "name": m.get("title") or m["bvid"],
                    "ar": [{"name": ((m.get("upper") or {}).get("name")) or "B站"}],
                    "al": {"name": name, "id": 0, "picUrl": (m.get("cover") or "")},
                    "dt": int(m.get("duration") or 0) * 1000, "no": 0, "publishTime": 0,
                    "privilege": {"maxBrLevel": "bestaudio", "dlLevel": "bestaudio"},
                })
            if len(medias) < 20:
                break
            pn += 1
        return name, tracks

    # ---- 目标 ----
    def playlist_detail(self, target: str) -> dict:
        return {"name": self.playlist_tracks(target)[0] and ""} if False else {"name": str(target)}

    def playlist_tracks(self, target: str, pl: dict | None = None) -> tuple[str, list[dict]]:
        t = str(target).strip()
        bvid = self._bvid_of(t)
        if bvid:  # 单视频/多 P
            v = self._view(bvid)
            name = v.get("title") or bvid
            tracks = []
            for pg in (v.get("pages") or [{"cid": 0, "part": name}]):
                tracks.append({
                    "id": bvid, "_pid": f"{bvid}|{pg.get('cid') or 0}",
                    "name": (pg.get("part") or name) if len(v.get("pages") or []) > 1 else name,
                    "ar": [{"name": ((v.get("owner") or {}).get("name")) or "B站"}],
                    "al": {"name": name, "id": 0, "picUrl": v.get("pic") or ""},
                    "dt": int(pg.get("duration") or (v.get("duration") or 0)) * 1000,
                    "no": int(pg.get("page") or 1), "publishTime": 0,
                    "privilege": {"maxBrLevel": "bestaudio", "dlLevel": "bestaudio"},
                })
            fav_name = tagger_sanitize(name) if False else None
            return name, tracks
        # 收藏夹:数字 media_id 或收藏夹链接
        import re
        m = re.search(r"fid=(\d+)", t) or re.search(r"/(\d+)(?:/|$)", t)
        media_id = m.group(1) if m else (t if t.isdigit() else "")
        if not media_id:
            raise ValueError(f"无法识别 B 站目标:{t}")
        name, tracks = self._fav_tracks(media_id)
        return name, tracks

    def song_detail(self, raw_ids: list[str]) -> list[dict]:
        out = []
        for raw in raw_ids:
            bvid, _, cid = str(raw).partition("|")
            try:
                v = self._view(bvid)
            except Exception as e:
                log.warning("B站详情获取失败 %s: %s", bvid, e)
                continue
            pages = v.get("pages") or [{}]
            pg = next((p for p in pages if str(p.get("cid")) == str(cid)), pages[0] if pages else {})
            out.append({
                "id": raw, "_pid": raw,
                "name": (pg.get("part") if len(pages) > 1 else None) or v.get("title") or raw,
                "ar": [{"name": ((v.get("owner") or {}).get("name")) or "B站"}],
                "al": {"name": v.get("title") or bvid, "id": 0, "picUrl": v.get("pic") or ""},
                "dt": int(pg.get("duration") or (v.get("duration") or 0)) * 1000,
                "no": int(pg.get("page") or 0) if len(pages) > 1 else 0,
                "publishTime": 0,
                "privilege": {"maxBrLevel": "bestaudio", "dlLevel": "bestaudio"},
            })
        return out

    def search(self, keywords: str, limit: int = 15) -> list[dict]:
        r = self.client.get(f"{API}/x/web-interface/search/type",
                            params={"search_type": "video", "keyword": keywords,
                                    "page": 1, "page_size": min(30, max(1, limit))})
        out = []
        for e in ((r.json().get("data") or {}).get("result") or [])[:limit]:
            t = self._norm_entry({"id": e.get("bvid"), "title": e.get("title"),
                                  "uploader": e.get("author"),
                                  "duration": 0, "thumbnails": [{"url": e.get("pic")}]})
            out.append(t)
        return out

    # ---- 取流(yt-dlp 不需要:直连 playurl) ----
    def download_audio(self, platform_id: str, tmp_dir, progress_cb=None) -> tuple:
        bvid, _, cid = str(platform_id).partition("|")
        v = self.client.get(f"{API}/x/web-interface/view", params={"bvid": bvid})
        if not cid:
            pages = (v.json().get("data") or {}).get("pages") or []
            cid = str((pages[0] if pages else {}).get("cid") or "")
        fnval = 4048 if (self.cookie_getter() or "") else 80  # 大会员可拿 flac/dolby
        r = self.client.get(f"{API}/x/player/playurl",
                            params={"fnval": fnval, "qn": 127, "bvid": bvid, "cid": cid})
        data = (r.json().get("data") or {})
        url = ""
        flac = ((data.get("dash") or {}).get("flac") or {}).get("audio") or {}
        if flac.get("baseUrl"):
            url = flac["baseUrl"]
        else:
            best, best_id = "", -1
            for a in (data.get("dash") or {}).get("audio") or []:
                if a.get("baseUrl") and int(a.get("id") or 0) > best_id:
                    best, best_id = a["baseUrl"], int(a["id"])
            url = best or ((data.get("durl") or [{}])[0].get("url") or "")
        if not url:
            raise RuntimeError("B 站未返回音频流")
        ext = ".m4a" if ".m4a" in url or "mp4a" in url else (".flac" if "flac" in url else ".m4a")
        local = tmp_dir / f".bili-{bvid}-{cid}{ext}"
        size, got = 0, 0
        with self.client.stream("GET", url, headers={
                "User-Agent": _UA, "Referer": "https://www.bilibili.com/"}) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            with open(local, "wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    f.write(chunk)
                    size += len(chunk)
                    got += 1
                    if progress_cb:
                        progress_cb(size, total)
        return local, size


def tagger_sanitize(name: str) -> str:
    import re
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", str(name))
    return re.sub(r"\s+", " ", s).strip() or "未知"
