"""网易云 Provider:直接委托既有 NcmApi(行为与 1.11.x 完全一致)。"""
from __future__ import annotations

from .base import BaseProvider


class NetEaseProvider(BaseProvider):
    platform = "netease"
    sid_base = 0
    level_chain = ["standard", "higher", "exhigh", "lossless", "hires",
                   "jyeffect", "sky", "dolby", "vivid", "jymaster"]

    def __init__(self, api):
        super().__init__()
        self.api = api  # NcmApi 实例

    def __getattr__(self, name: str):
        # 除基类定义的方法外,其余(歌单/详情/取流/歌词/专辑/搜索/扫码/特殊源…)
        # 与 NcmApi 同名同语义,直接委托。
        return getattr(self.api, name)

    def pick_level(self, privilege: dict | None, chain: list[str] | None = None) -> str:
        from .. import quality
        return quality.pick_level(privilege, chain or self.level_chain)

    def chain_from(self, level: str, chain: list[str] | None = None) -> list[str]:
        from .. import quality
        return quality.chain_from(level, chain or self.level_chain)

    def download_headers(self) -> dict:
        h = super().download_headers()
        h["Referer"] = "https://music.163.com/"
        return h

    def album_info(self, detail: dict) -> dict:
        al = (detail or {}).get("al") or {}
        aid = al.get("id")
        if aid is None:
            return {}
        return self.api.album(aid).get("album") or {}
