"""QQ 音乐 Provider:直连腾讯接口(参考 guohuiyuan/music-lib 的实现)。

登录:扫码(ptqrshow 出码 → ptqrlogin 轮询,ptqrtoken=hash33(qrsig)),
     Cookie 粘贴兜底。取流:musicu.fcg GetVkey,音质前缀从高到低取第一个可用 purl。
"""
from __future__ import annotations

import base64
import httpx
import json
import random
import re
import time
import zlib

from .base import BaseProvider

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
_PTQR_SHOW = "https://ssl.ptlogin2.qq.com/ptqrshow"
_PTQR_LOGIN = "https://ssl.ptlogin2.qq.com/ptqrlogin"
_MUSICU = "https://u.y.qq.com/cgi-bin/musicu.fcg"
_SEARCH = "http://c.y.qq.com/soso/fcgi-bin/search_for_qq_cp?"
_SONG = "https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg?"
_PLAYLIST = "https://i.y.qq.com/qzone-music/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg?"

# 音质前缀从高到低(VIP 表全试,取第一个有 purl 的;无 VIP 自动落到 M800/M500)
_PREFIXES = [("AI00", "flac", "hires"), ("Q001", "flac", "hires"), ("Q000", "flac", "lossless"),
             ("F000", "flac", "lossless"), ("O801", "ogg", "high"),
             ("M800", "mp3", "standard"), ("M500", "mp3", "standard")]


def _hash33(s: str) -> int:
    h = 0
    for c in s:
        h += (h << 5) + ord(c)
    return h & 0x7FFFFFFF


def _sanitize(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", str(name))
    return re.sub(r"\s+", " ", s).strip() or "未知"


class QQProvider(BaseProvider):
    platform = "qq"
    sid_base = 1_000_000_000_000
    level_chain = ["hires", "lossless", "high", "standard"]

    def __init__(self, base_url_unused, cookie_getter):
        # base_url 兼容旧签名(QQMusicApi 容器时代);直连实现不使用
        super().__init__()
        self.cookie_getter = cookie_getter
        self.client = httpx.Client(timeout=30, headers={"User-Agent": _UA})

    # ---- 底层 ----
    def _headers(self, referer: str, cookie: str | None = None) -> dict:
        h = {"User-Agent": _UA, "Referer": referer}
        ck = cookie or self.cookie_getter() or ""
        if ck:
            h["Cookie"] = ck
        return h

    def _json_post(self, payload: dict, referer: str) -> dict:
        r = self.client.post(_MUSICU, json=payload, headers=self._headers(referer))
        r.raise_for_status()
        return r.json()

    # ---- 登录(扫码) ----
    def login_qr_start(self) -> dict:
        params = {"appid": "716027609", "e": "2", "l": "M", "s": "3", "d": "72", "v": "4",
                  "t": f"{time.time_ns() / 1e18:.17f}", "daid": "383", "pt_3rd_aid": "100497308"}
        r = self.client.get(_PTQR_SHOW, params=params, headers={"Referer": "https://y.qq.com/"})
        r.raise_for_status()
        qrsig = ""
        for k, v in r.cookies.items():
            if k == "qrsig":
                qrsig = v
        if not qrsig:
            raise RuntimeError("QQ 未返回 qrsig")
        buf = io.BytesIO()
        buf.write(r.content)  # ptqrshow 直接返回 PNG 二维码
        img = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        return {"img": img, "key": qrsig}

    def login_qr_poll(self, key: str) -> dict:
        params = {"u1": "https://graph.qq.com/oauth2.0/login_jump",
                  "ptqrtoken": _hash33(key), "ptredirect": "100", "h": "1", "t": "1", "g": "1",
                  "from_ui": "1", "ptlang": "2052", "action": f"0-0-{time.time_ns() // 1000000}",
                  "js_ver": "21072115", "js_type": "1", "login_sig": "", "pt_uistyle": "40",
                  "aid": "716027609", "daid": "383", "pt_3rd_aid": "100497308",
                  "has_onekey": "1", "pttype": "1", "service": "ptqrlogin", "nodirect": "0"}
        r = self.client.get(_PTQR_LOGIN, params=params,
                            headers=self._headers("https://xui.ptlogin2.qq.com/", cookie=f"qrsig={key}"))
        m = re.findall(r"'([^']*)'", r.text)
        code = m[0] if len(m) >= 1 else ""
        message = m[4] if len(m) >= 5 else (m[2] if len(m) >= 3 else "等待中")
        redirect = m[2] if len(m) >= 3 else ""
        saved = False
        cookie = self.cookie_getter() or ""
        if code == "0" and redirect:
            cookies: dict[str, str] = {}
            for k, v in r.cookies.items():
                cookies[k] = v
            try:  # check_sig 域名换取音乐侧 cookie
                rr = self.client.get(redirect, headers=self._headers(
                    "https://xui.ptlogin2.qq.com/", cookie="; ".join(f"{k}={v}" for k, v in cookies.items())))
                for k, v in rr.cookies.items():
                    cookies[k] = v
            except Exception:
                pass
            if cookies.get("uin") is None:
                for k in ("ptui_loginuin", "luin"):
                    if cookies.get(k):
                        cookies["uin"] = cookies[k]
                        break
            cookie = "; ".join(f"{k}={v}" for k, v in cookies.items() if k and v)
            self.cookie_getter  # 兼容签名;实际保存由 web 层完成
            saved = True
        saved_msg = {"0": "登录成功", "65": "二维码已过期", "66": "等待扫码",
                     "67": "已扫码,请在手机上确认"}.get(code, message or "等待中")
        return {"code": int(code) if code.isdigit() else -1, "message": saved_msg,
                "saved": saved, "cookie": cookie if saved else ""}

    def logged_in(self) -> bool:
        return bool(self.cookie_getter() or "")

    def set_cookie_hint(self):  # 兼容占位
        pass

    # ---- 目标 ----
    def user_playlists(self) -> list[dict]:
        return []  # QQ 登录态经扫码/Cookie 后,歌单通过链接方式添加

    def playlist_detail(self, pid) -> dict:
        d = self.playlist_detail_raw(pid)
        return {"name": d.get("dissname") or d.get("title") or str(pid)}

    def playlist_detail_raw(self, pid) -> dict:
        params = {"type": "1", "json": "1", "utf8": "1", "onlysong": "0", "disstid": str(pid),
                  "format": "json", "g_tk": "5381", "loginUin": "0", "hostUin": "0",
                  "inCharset": "utf8", "outCharset": "utf-8", "notice": "0",
                  "platform": "yqq", "needNewCode": "0"}
        last = None
        for ep in ("https://i.y.qq.com/qzone-music/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg?",
                   "http://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg?"):
            try:
                r = self.client.get(ep + params_str(params), headers=self._headers("https://y.qq.com/"))
                body = r.text
                if body.startswith("(") and body.endswith(")"):
                    body = body[1:-1]
                d = json.loads(body)
                cd = (d.get("cdlist") or [{}])[0]
                if d.get("code") == 0 and cd.get("songlist") is not None:
                    return cd
                last = f"code={d.get('code')}"
            except Exception as e:
                last = e
        raise RuntimeError(f"QQ 歌单拉取失败:{last}")

    def playlist_tracks(self, pid, pl: dict | None = None) -> list[dict]:
        cd = pl if pl and pl.get("songlist") is not None else self.playlist_detail_raw(pid)
        out = []
        for t in cd.get("songlist") or []:
            mid = t.get("songmid") or ""
            if not mid:
                continue
            singers = [s.get("name") for s in (t.get("singer") or []) if s.get("name")]
            pic = (f"https://y.gtimg.cn/music/photo_new/T002R500x500M000{t['albummid']}.jpg"
                   if t.get("albummid") else "")
            out.append({
                "id": mid, "_pid": mid,
                "name": t.get("songname") or mid,
                "ar": [{"name": s} for s in singers] or [{"name": "未知歌手"}],
                "al": {"name": t.get("albumname") or "未知专辑", "id": 0, "picUrl": pic},
                "dt": int(t.get("interval") or 0) * 1000,
                "no": 0, "publishTime": 0, "songid": str(t.get("songid") or ""),
                "privilege": {"maxBrLevel": "hires", "dlLevel": None},
            })
        return out

    # ---- 歌曲 ----
    def song_detail(self, raw_ids: list[str]) -> list[dict]:
        out = []
        for raw in raw_ids:
            try:
                r = self.client.get(_SONG, params={"songmid": str(raw), "format": "json"},
                                    headers=self._headers("https://y.qq.com/"))
                items = (r.json().get("data") or [])
                item = items[0] if items else {}
                singers = [sg.get("name") for sg in (item.get("singer") or []) if sg.get("name")]
                album = item.get("album") or {}
                pic = (f"https://y.gtimg.cn/music/photo_new/T002R500x500M000{album.get('mid')}.jpg"
                       if album.get("mid") else "")
                out.append({
                    "id": item.get("mid") or str(raw), "_pid": str(item.get("mid") or raw),
                    "name": item.get("name") or str(raw),
                    "ar": [{"name": s} for s in singers] or [{"name": "未知歌手"}],
                    "al": {"name": album.get("name") or "未知专辑", "id": 0, "picUrl": pic},
                    "dt": int(item.get("interval") or 0) * 1000,
                    "no": 0, "publishTime": 0, "songid": str(item.get("id") or ""),
                    "privilege": {"maxBrLevel": "hires", "dlLevel": None},
                })
            except Exception:
                continue
        return out

    def song_url(self, raw_id, level: str, detail: dict | None = None) -> dict:
        mid = str((detail or {}).get("_pid") or (detail or {}).get("songmid") or raw_id)
        guid = str(random.randint(1000000000, 9999999999))
        filenames, songmids, songtypes = [], [], []
        for prefix, ext in _PREFIXES:
            filenames.append(f"{prefix}{mid}{mid}.{ext}")
            songmids.append(mid)
            songtypes.append(0)
        payload = {
            "comm": {"cv": 4747474, "ct": 24, "format": "json", "inCharset": "utf-8",
                     "outCharset": "utf-8", "notice": 0, "platform": "yqq.json",
                     "needNewCode": 1, "uin": 0},
            "req_1": {"module": "music.vkey.GetVkey", "method": "UrlGetVkey",
                      "param": {"guid": guid, "songmid": songmids, "songtype": songtypes,
                                "uin": "0", "loginflag": 1, "platform": "20",
                                "filename": filenames}},
        }
        r = self.client.post(_MUSICU, json=payload, headers=self._headers("https://y.qq.com/"))
        r.raise_for_status()
        infos = (((r.json().get("req_1") or {}).get("data") or {}).get("midurlinfo")) or []
        purl = ""
        for expected in filenames:
            for info in infos:
                if info.get("filename") == expected and info.get("purl"):
                    purl = "https://ws.stream.qqmusic.qq.com/" + info["purl"]
                    ext = expected.rsplit(".", 1)[-1]
                    lv = _LEVEL_BY_PREFIX.get(expected.split(mid)[0], "standard")
                    return {"url": purl, "type": ext, "size": 0, "md5": "", "br": 0,
                            "level": lv, "freeTrialInfo": None}
        return {}

    def lyric(self, detail: dict) -> str | None:
        mid = str((detail or {}).get("_pid") or (detail or {}).get("id") or "")
        if not mid:
            return None
        try:
            song_id = int((detail or {}).get("songid") or 0)
        except (TypeError, ValueError):
            song_id = 0
        if song_id == 0:
            try:
                d = self.client.get(_SONG, params={"songmid": mid, "format": "json"},
                                    headers=self._headers("https://y.qq.com/"))
                song_id = int(((d.json().get("data") or [{}])[0]).get("id") or 0)
            except Exception:
                song_id = 0
        payload = {
            "comm": {"ct": 11, "cv": "1003006", "v": "1003006", "os_ver": "15",
                     "tmeAppID": "qqmusiclight", "nettype": "NETWORK_WIFI", "uid": "0",
                     "platform": "yqq.json", "needNewCode": 0},
            "request": {"method": "GetPlayLyricInfo", "module": "music.musichallSong.PlayLyricInfo",
                        "param": {"crypt": 1, "ct": 19, "cv": 2111, "interval": 0, "lrc_t": 0,
                                  "qrc": 1, "qrc_t": 0, "roma": 1, "roma_t": 0, "trans": 1,
                                  "trans_t": 0, "type": 0, "songID": song_id}},
        }
        try:
            r = self.client.post(_MUSICU, json=payload, headers=self._headers("https://y.qq.com/"))
            d = ((r.json().get("request") or {}).get("data") or {})
            return str(d.get("lyric") or "")
        except Exception:
            return None

    def album_info(self, detail: dict) -> dict:
        return {}  # 第三方直连接口暂无专辑流派/厂牌字段

    def search(self, keywords: str, limit: int = 15) -> list[dict]:
        r = self.client.get(_SEARCH, params={"w": keywords, "format": "json", "p": "1",
                                             "n": str(min(30, max(1, limit)))},
                            headers=self._headers("https://y.qq.com/"))
        out = []
        for t in ((r.json().get("data") or {}).get("song") or {}).get("list") or []:
            singers = [s.get("name") for s in (t.get("singer") or []) if s.get("name")]
            mid = t.get("songmid") or ""
            out.append({
                "id": mid, "_pid": mid,
                "name": t.get("songname") or "",
                "ar": [{"name": s} for s in singers] or [{"name": "未知歌手"}],
                "al": {"name": t.get("albumname") or "未知专辑", "id": 0, "picUrl": ""},
                "dt": int(t.get("interval") or 0) * 1000, "no": 0, "publishTime": 0,
                "songid": str(t.get("songid") or ""),
                "privilege": {"maxBrLevel": "hires", "dlLevel": None},
            })
        return out

    def download_headers(self) -> dict:
        h = dict(self._default_headers)
        h["Referer"] = "https://y.qq.com/"
        cookie = self.cookie_getter() or ""
        if cookie:
            h["Cookie"] = cookie
        return h


def params_str(params: dict) -> str:
    from urllib.parse import quote
    return "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
