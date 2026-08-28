"""ncm-api(api-enhanced)客户端。统一限速、重试、cookie 注入。"""
from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable

import httpx

log = logging.getLogger("wyydl.api")


class ApiError(Exception):
    pass


class LoginExpired(Exception):
    pass


class NcmApi:
    def __init__(self, base: str, token_getter: Callable[[], str | None],
                 api_delay: tuple[float, float] = (1.0, 3.0)):
        self.base = base.rstrip("/")
        self.token_getter = token_getter
        self._delay = (max(0.0, api_delay[0]), max(api_delay[0], api_delay[1]))
        self._lock = threading.Lock()
        self._last = 0.0
        self.client = httpx.Client(timeout=httpx.Timeout(30.0, read=60.0))

    # ---- 底层 ----
    def _cookie(self) -> str:
        t = self.token_getter() or ""
        return f"MUSIC_U={t}; os=pc" if t else "os=pc"

    def _pace(self) -> None:
        """串行化并保持随机间隔,规避高频风控。"""
        with self._lock:
            wait = random.uniform(*self._delay)
            now = time.monotonic()
            delta = self._last + wait - now
            if delta > 0:
                time.sleep(delta)
            self._last = time.monotonic()

    def get(self, path: str, **params) -> dict:
        # 恒带时间戳,绕过 ncm-api 的 2 分钟缓存(文档要求;登录/动作类接口必须)
        params.setdefault("timestamp", int(time.time() * 1000))
        params["cookie"] = self._cookie()
        url = f"{self.base}{path}"
        last_err: Exception | None = None
        for attempt in range(3):
            self._pace()
            try:
                r = self.client.get(url, params=params)
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, dict):
                    raise ApiError(f"{path}: unexpected payload")
                return data
            except Exception as e:
                last_err = e
                log.debug("GET %s attempt %s failed: %s", path, attempt + 1, e)
                time.sleep(2.0 * (attempt + 1))
        raise ApiError(f"{path}: {last_err}")

    # ---- 登录 ----
    def login_status(self) -> dict:
        return self.get("/login/status")

    def logged_in(self) -> bool:
        try:
            d = self.login_status()
        except ApiError:
            return False
        data = d.get("data") or {}
        return data.get("code") == 200

    def qr_key(self) -> str:
        d = self.get("/login/qr/key")
        key = (d.get("data") or {}).get("unikey")
        if not key:
            raise ApiError(f"qr key failed: {d}")
        return str(key)

    def qr_create(self, key: str) -> dict:
        d = self.get("/login/qr/create", key=key, qrimg="true")
        data = d.get("data") or {}
        if not data.get("qrimg"):
            raise ApiError(f"qr create failed: {d}")
        return data  # {qrurl, qrimg(dataURL)}

    def qr_check(self, key: str) -> dict:
        """轮询扫码状态。注意:该接口的 800/801/802/803 在响应顶层,
        与 qr/key、qr/create 的 data 嵌套结构不同;这里做兼容解析。
        502(个别部署无 cookie 调用会报)时退化为 noCookie=true,但那样拿不到 cookie。"""
        def _resolve(d: dict) -> dict:
            data = d.get("data") if isinstance(d.get("data"), dict) else {}
            code = 0
            for v in (d.get("code"), data.get("code")):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    continue
                if v in (800, 801, 802, 803, 502):  # 只认扫码状态码,忽略外层包装 code=200
                    code = v
                    break
            return {
                "code": code,
                "message": str(d.get("message") or data.get("message") or ""),
                "cookie": str(d.get("cookie") or data.get("cookie") or ""),
            }

        r = _resolve(self.get("/login/qr/check", key=key))
        if r["code"] == 502:
            r = _resolve(self.get("/login/qr/check", key=key, noCookie="true"))
        return r

    # ---- 歌单 ----
    def playlist_detail(self, pid: int) -> dict:
        d = self.get("/playlist/detail", id=pid)
        pl = d.get("playlist") or {}
        if not pl:
            raise ApiError(f"playlist {pid} not found: code={d.get('code')}")
        return pl

    def playlist_tracks(self, pid: int, pl: dict | None = None) -> list[dict]:
        """歌单全部曲目(顺序保持歌单原始顺序)。使用 /playlist/detail 的 trackIds
        ——它是完整列表,而 /playlist/track/all 会被截断(如上千首只返回 499)。
        曲目详情与音质权限随后由 song_detail 批量补齐。"""
        if pl is None:
            pl = self.playlist_detail(pid)
        ids: list[dict] = []
        for t in pl.get("trackIds") or []:
            try:
                ids.append({"id": int(t["id"])})
            except (KeyError, TypeError, ValueError):
                continue
        if ids:
            return ids
        # 兜底:trackIds 为空时退回 track/all 分页
        first = self.get("/playlist/track/all", id=pid, limit=500, offset=0)
        songs: list[dict] = list(first.get("songs") or [])
        total = int(first.get("total") or len(songs))
        offset = len(songs)
        while offset < total and songs:
            page = self.get("/playlist/track/all", id=pid, limit=500, offset=offset)
            batch = page.get("songs") or []
            if not batch:
                break
            songs += batch
            offset += len(batch)
        return songs

    def song_detail(self, ids: list[int]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(ids), 100):
            batch = ",".join(str(x) for x in ids[i:i + 100])
            d = self.get("/song/detail", ids=batch)
            privs = {}
            for p in d.get("privileges") or []:
                if p.get("id") is not None:
                    privs[int(p["id"])] = p
            for s in d.get("songs") or []:
                sid = s.get("id")
                if sid is not None and int(sid) in privs and not s.get("privilege"):
                    s["privilege"] = privs[int(sid)]  # 音质协商依赖此字段
                out.append(s)
        return out

    def user_account(self) -> dict:
        """当前登录账号的 profile(含 userId)。"""
        return self.get("/user/account").get("profile") or {}

    def user_playlists(self, uid: int) -> list[dict]:
        """账号内全部歌单(含『我喜欢的音乐』),自动翻页。"""
        out: list[dict] = []
        offset = 0
        while True:
            d = self.get("/user/playlist", uid=uid, limit=50, offset=offset)
            batch = d.get("playlist") or []
            out += batch
            offset += len(batch)
            if not batch or (len(batch) < 50 and not d.get("more")):
                break
        return out

    @staticmethod
    def extract_privs(resp: dict) -> dict[int, dict]:
        """从 song/detail 或 playlist/track/all 的响应提取 {sid: privilege}。"""
        privs: dict[int, dict] = {}
        for p in resp.get("privileges") or []:
            pid = p.get("id")
            if pid is not None:
                privs[int(pid)] = p
        for s in resp.get("songs") or []:
            p = s.get("privilege")
            sid = s.get("id")
            if p and sid is not None:
                privs[int(sid)] = p
        return privs

    # ---- 取流 / 歌词 ----
    def song_url_batch(self, ids: list[int], level: str) -> dict[int, dict]:
        d = self.get("/song/url/v1", id=",".join(str(x) for x in ids), level=level)
        if d.get("code") != 200:
            raise ApiError(f"song/url/v1 code={d.get('code')}")
        out: dict[int, dict] = {}
        for e in d.get("data") or []:
            if e and e.get("id") is not None:
                out[int(e["id"])] = e
        return out

    def song_url(self, sid: int, level: str) -> dict:
        return self.song_url_batch([sid], level).get(sid, {})

    def lyric(self, sid: int) -> str | None:
        """歌词文本;None 表示获取失败(接口异常),空串表示无歌词。"""
        try:
            d = self.get("/lyric", id=sid)
        except ApiError:
            return None
        return str((d.get("lrc") or {}).get("lyric") or "")

    def album(self, aid: int) -> dict:
        """专辑详情(流派/厂牌/发行时间/简介),供 NFO 使用。"""
        return self.get("/album", id=aid)

    def close(self) -> None:
        self.client.close()
