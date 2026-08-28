"""音质协商:配置链 × 歌曲最高档(maxBrLevel) × 账号可用档(dlLevel)。"""
from __future__ import annotations

# 保真度从低到高;空间音频类(杜比/全景声)对存档意义有限,排在 hires 之后仅作占位
_LEVEL_ORDER = [
    "standard", "higher", "exhigh", "lossless", "hires",
    "jyeffect", "sky", "dolby", "vivid", "jymaster",
]

def rank(level: str | None) -> int:
    """未知档位返回 -1(表示无约束),便于兼容新增档位。"""
    if not level:
        return -1
    try:
        return _LEVEL_ORDER.index(str(level))
    except ValueError:
        return -1


def pick_level(privilege: dict | None, chain: list[str]) -> str:
    """chain 按高→低排列,返回第一个同时不超过歌曲上限与账号上限的档位。"""
    song_max = rank((privilege or {}).get("maxBrLevel"))
    acc_max = rank((privilege or {}).get("dlLevel"))
    for lvl in chain:
        r = rank(lvl)
        if r < 0:
            continue
        if song_max >= 0 and r > song_max:
            continue
        if acc_max >= 0 and r > acc_max:
            continue
        return lvl
    return chain[-1] if chain else "standard"


def chain_from(level: str, chain: list[str]) -> list[str]:
    """返回 level 及其以下的降档序列。"""
    r = rank(level)
    out = [x for x in chain if rank(x) >= 0 and rank(x) <= (r if r >= 0 else 10**9)]
    return out or [level]
