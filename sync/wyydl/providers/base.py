"""Provider 公共基类:sid 分段、音质档位协商、通用下载头。

sid 分段(64 位整数内,避免重建表):
  netease  = 0 ~ 1e12-1(原样)
  qq       = 1e12 ~ 2e12-1(1e12 + QQ 歌曲数字 ID)
  bilibili = 2e12 ~ 3e12-1(2e12 + B 站 aid)
  local    = 负数(路径哈希,本地文件/手动编辑)
"""
from __future__ import annotations

import time

SID_BASE = {"netease": 0, "qq": 1_000_000_000_000, "bilibili": 2_000_000_000_000}


def platform_of_sid(sid: int) -> str:
    if sid >= SID_BASE["bilibili"]:
        return "bilibili"
    if sid >= SID_BASE["qq"]:
        return "qq"
    if sid >= 0:
        return "netease"
    return "local"


def raw_id_of(sid: int) -> int:
    """由全局 sid 还原平台内原始数字 ID。"""
    for name, base in sorted(SID_BASE.items(), key=lambda kv: -kv[1]):
        if sid >= base:
            return sid - base
    return sid


class BaseProvider:
    """平台 Provider 统一接口。方法语义与原 NcmApi 对齐,
    但 song/track 相关返回值统一为「网易云形态」的中性 dict,
    由各平台自行归一化(如 B 站 UP 主 → ar、时长秒 → dt 毫秒)。"""

    platform = ""
    sid_base = 0
    level_chain: list[str] = ["standard"]

    def __init__(self):
        self._default_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        self._li_ts = 0.0
        self._li_val = False

    def logged_in_cached(self, ttl: float = 300.0) -> bool:
        """带缓存的登录态检查(面板轮询用)。"""
        if time.time() - self._li_ts > ttl:
            try:
                self._li_val = bool(self.logged_in())
            except Exception:
                self._li_val = False
            self._li_ts = time.time()
        return self._li_val

    def invalidate_login_cache(self) -> None:
        self._li_ts = 0.0

    # ---- 身份 ----
    def sid(self, raw_id: int | str) -> int:
        return self.sid_base + int(raw_id)

    def download_headers(self) -> dict:
        return dict(self._default_headers)

    # ---- 音质 ----
    def pick_level(self, privilege: dict | None, chain: list[str] | None = None) -> str:
        """按平台权限与档位链取目标档;默认取链顶(子类按需覆盖)。"""
        chain = chain or self.level_chain
        return chain[0]

    def chain_from(self, level: str, chain: list[str] | None = None) -> list[str]:
        chain = chain or self.level_chain
        try:
            return chain[chain.index(level):]
        except ValueError:
            return [chain[0]]
