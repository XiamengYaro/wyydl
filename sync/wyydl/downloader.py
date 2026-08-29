"""流式下载 + MD5/大小校验。返回临时文件路径,由调用方校验后落位。"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import httpx


def download(url: str, tmp_dir: Path, timeout: tuple[float, float] = (20.0, 900.0),
             progress=None, proxy: str | None = None) -> tuple[Path, str, int]:
    """下载到 tmp_dir 下的 .part 文件,返回 (path, md5_hex, size)。
    progress(downloaded, total) 按块回调,total 取 Content-Length(可能为 0);
    proxy 可选,仅作用于本次 CDN 下载。"""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f".dl-{os.getpid()}-{int(time.time() * 1000)}.part"
    h = hashlib.md5()
    size = 0
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                   "Referer": "https://music.163.com/"}
        with httpx.stream("GET", url, timeout=timeout, headers=headers,
                          follow_redirects=True, proxy=proxy) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
                    size += len(chunk)
                    if progress:
                        progress(size, total)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, h.hexdigest(), size


def verify(path: Path, expect_md5: str = "", expect_size: int = 0) -> bool:
    if expect_size and path.stat().st_size != expect_size:
        return False
    if expect_md5:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest() == expect_md5
    return True


def move_into(tmp: Path, final: Path) -> None:
    final.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(tmp, final)
    except OSError:  # 跨文件系统
        shutil_move(tmp, final)


def shutil_move(src: Path, dst: Path) -> None:
    import shutil
    shutil.move(str(src), str(dst))
