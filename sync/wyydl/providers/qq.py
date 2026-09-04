"""QQ 音乐 Provider:对接自建 QQMusicApi 容器(jsososo/QQMusicApi)。

登录:扫码优先(/login?qrcode=1 + /checkQrcode),失败降级 Cookie 粘贴。
所有歌曲/歌单数据归一化为「网易云形态」中转结构。
注:该第三方 API 的具体端点以容器实测为准,解析均做了防御。
"""
from __future__ import annotations

import httpx

from .base import BaseProvider


class QQProvider(BaseProvider):
    platform = "qq"
    sid_base = 1_000_000_000_000
    level_chain = ["hires", "lossless", "high", "standard"]

    def __init__(self, base_url: str, cookie_getter):
        super().__init__()
        self.base = base_url.rstrip("/")
        self.cookie_getter = cookie_getter
        self.client = httpx.Client(timeout=30)

    def _headers(self) -> dict:
        h = dict(self._default_headers)
        h["Referer"] = "https://y.qq.com/"
        cookie = self.cookie_getter() or ""
        if cookie:
            h["Cookie"] = cookie
        return h

    def _get(self, path: str, **params) -> dict:
        r = self.client.get(f"{self.base}{path}", params=params, headers=self._headers())
        r.raise_for_status()
        return r.json()

    # ---- 登录 ----
    def logged_in(self) -> bool:
        if not (self.cookie_getter() or ""):
            return False
        try:
            d = self._get("/user/detail")
            return int(d.get("code", -1)) in (0, 200)
        except Exception:
            return False

    def login_qr_start(self) -> dict:
        d = self._get("/login", qrcode=1)
        img = str(d.get("qrcode") or d.get("image") or "")
        if not img:
            raise RuntimeError(f"QQ 音乐 API 未返回二维码(code={d.get('code')})")
        return {"img": img,
                "key": str(d.get("qrcode_str") or d.get("key") or "")}

    def login_qr_poll(self, key: str) -> dict:
        d = self._get("/checkQrcode", qrcode=1, qrcode_str=key)
        code = int(d.get("code", -1))
        msg = {0: "登录成功", 1: "等待扫码", 2: "已扫码,请在手机上确认"}.get(code, str(d.get("message") or "等待中"))
        return {"code": code, "message": msg, "saved": code == 0}

    # ---- 目标 ----
    def user_playlists(self) -> list[dict]:
        d = self._get("/user/songlist")
        out = []
        for p in (d.get("data") or []):
            if p.get("dissid") or p.get("tid"):
                out.append({"id": p.get("dissid") or p.get("tid"),
                            "name": p.get("dissname") or p.get("title") or "",
                            "total": p.get("song_count") or p.get("count") or 0})
        return out

    def playlist_detail(self, pid) -> dict:
        d = self._get("/songlist", id=str(pid))
        data = d.get("data") or {}
        return {"name": data.get("dissname") or data.get("title") or str(pid)}

    def playlist_tracks(self, pid, pl: dict | None = None) -> list[dict]:
        d = self._get("/songlist", id=str(pid))
        data = d.get("data") or {}
        out = []
        for t in data.get("songlist") or []:
            mid = t.get("songmid") or t.get("mid") or ""
            sid_num = t.get("songid") or t.get("id") or 0
            if not (mid or sid_num):
                continue
            raw_id = str(mid or sid_num)
            singers = [s.get("name") for s in (t.get("singer") or []) if s.get("name")]
            out.append(self._norm_track(
                raw_id=raw_id, name=t.get("songname") or t.get("name") or "",
                singers=singers, album=t.get("albumname") or "",
                interval=t.get("interval") or 0, songmid=mid))
        return out

    # ---- 歌曲 ----
    def _norm_track(self, *, raw_id, name, singers, album, interval, songmid="",
                    pic_url="", pay=None) -> dict:
        """归一化为网易云形态:dt 毫秒、ar/al 列表、privilege 档位。"""
        return {
            "id": raw_id,
            "_pid": str(songmid or raw_id),
            "name": name or str(raw_id),
            "ar": [{"name": s} for s in singers] or [{"name": "未知歌手"}],
            "al": {"name": album or "未知专辑", "id": 0, "picUrl": pic_url or ""},
            "dt": int(interval) * 1000,
            "no": 0,
            "publishTime": 0,
            "songmid": songmid,
            "privilege": {"maxBrLevel": self.level_chain[0], "dlLevel": None},
        }

    def song_detail(self, raw_ids: list[str]) -> list[dict]:
        out = []
        for raw in raw_ids:
            try:
                d = self._get("/song", songmid=str(raw))
                info = (d.get("info") or {}) if isinstance(d.get("info"), dict) else {}
                track = (info.get("track_info") or {}) if info else (d.get("data") or {})
                if not track:
                    continue
                singers = [s.get("name") for s in (track.get("singer") or []) if s.get("name")]
                album = track.get("album") or {}
                pmid = album.get("pmid") or album.get("mid") or ""
                pic = f"https://y.gtimg.cn/music/photo_new/T002R500x500M000{pmid}.jpg" if pmid else ""
                out.append(self._norm_track(
                    raw_id=track.get("songmid") or raw, name=track.get("name") or "",
                    singers=singers, album=album.get("name") or "",
                    interval=track.get("interval") or 0, songmid=str(track.get("songmid") or raw),
                    pic_url=pic))
            except Exception:
                continue
        return out

    def song_url(self, raw_id, level: str, detail: dict | None = None) -> dict:
        mid = str((detail or {}).get("songmid") or raw_id)
        d = self._get("/song/urls", ids=f"[\"{mid}\"]")
        url = ((d.get("data") or {}).get(mid)) or ""
        if not url:
            return {}
        ext = url.split("?")[0].rsplit(".", 1)[-1].lower() if "." in url.split("?")[0] else "mp3"
        return {"url": url, "type": ext, "size": 0, "md5": "", "br": 0,
                "level": "auto", "freeTrialInfo": None}

    def lyric(self, raw_id) -> str | None:
        try:
            d = self._get("/lyric", songmid=str(raw_id))
        except Exception:
            return None
        return str((d.get("data") or {}).get("lyric") or "")

    def lyric_new(self, raw_id) -> str | None:
        return None  # QQ 暂无逐字歌词来源

    def album_info(self, detail: dict) -> dict:
        return {}  # 第三方接口暂无专辑流派/厂牌字段

    def search(self, keywords: str, limit: int = 15) -> list[dict]:
        d = self._get("/search", keywords=keywords, limit=min(30, max(1, limit)))
        out = []
        for t in ((d.get("data") or {}).get("list") or [])[:limit]:
            singers = [s.get("name") for s in (t.get("singer") or []) if s.get("name")]
            mid = t.get("songmid") or t.get("mid") or ""
            out.append(self._norm_track(
                raw_id=t.get("songid") or mid, name=t.get("songname") or t.get("name") or "",
                singers=singers, album=(t.get("albumname") or ""),
                interval=t.get("interval") or 0, songmid=mid))
        return out
