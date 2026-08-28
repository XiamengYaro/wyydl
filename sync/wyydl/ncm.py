"""NCM 兜底通道:调用 ncmdump-go 逐个解密 _ncm_inbox 下的文件。"""
from __future__ import annotations

import logging
import shutil
import subprocess
import zlib
from pathlib import Path

log = logging.getLogger("wyydl.ncm")

AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".ogg", ".ape", ".wav"}


def ncm_sid(path: Path) -> int:
    """NCM 文件不含歌曲 id,用内容路径派生负数 sid 与 API 通道的正数 id 区分开。"""
    return -(zlib.crc32(str(path).encode("utf-8")) % 900_000_000 + 1)


def convert_one(ncm: Path, out_dir: Path, timeout: int = 600) -> Path | None:
    """转换单个 ncm,返回转换产物路径。"""
    before = {p.name: p.stat().st_mtime for p in out_dir.rglob("*") if p.is_file()} if out_dir.exists() else {}
    r = subprocess.run(["ncmdump-go", str(ncm), "-o", str(out_dir)],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        log.warning("ncmdump-go %s failed: %s", ncm.name, (r.stderr or r.stdout).strip()[:200])
        return None
    candidates = [p for p in out_dir.rglob("*")
                  if p.is_file() and p.suffix.lower() in AUDIO_EXTS
                  and before.get(p.name) != p.stat().st_mtime]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def move_done(ncm: Path, done_dir: Path) -> None:
    done_dir.mkdir(parents=True, exist_ok=True)
    dst = done_dir / ncm.name
    i = 1
    while dst.exists():
        dst = done_dir / f"{ncm.stem}_{i}{ncm.suffix}"
        i += 1
    shutil.move(str(ncm), str(dst))
