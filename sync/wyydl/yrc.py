"""yrc(网易云逐字歌词)→ LRC 转换(行级时间戳 + 合并逐字文本)。"""
from __future__ import annotations

import re

_LINE = re.compile(r"^\[(\d+),\d+\](.*)$")
_CHAR_TS = re.compile(r"\(\d+(?:,\d+)*(?:,\d*)?\)")  # (ts,dur) 或 (ts,dur,idx)
_TAG = re.compile(r"^\[[A-Za-z]+:.*\]$")


def yrc_to_lrc(yrc: str) -> str:
    lines: list[str] = []
    for raw in yrc.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _LINE.match(line)
        if not m:
            if not _TAG.match(line):  # 跳过 [by:xxx] 等元数据行,保留普通文本行
                lines.append(line)
            continue
        start_ms = int(m.group(1))
        body = _CHAR_TS.sub("", m.group(2))  # 去掉逐字时间戳
        mm, ss = divmod(start_ms // 1000, 60)
        lines.append(f"[{mm:02d}:{ss:02d}]{body}")
    return "\n".join(lines)
