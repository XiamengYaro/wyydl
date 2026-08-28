"""文件命名与标签写入(mutagen):flac / mp3 / m4a,封面与歌词。"""
from __future__ import annotations

import re
from pathlib import Path

from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, APIC, TALB, TDRC, TIT2, TPE1, TPOS, TRCK, USLT
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover

_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_MULTI_SPACE = re.compile(r"\s+")


def sanitize(name: str, maxlen: int = 120) -> str:
    s = _ILLEGAL.sub(" ", str(name))
    s = _MULTI_SPACE.sub(" ", s).strip().strip(".")
    s = s[:maxlen].strip()
    return s or "未知"


def safe_format(template: str, **vars: object) -> str:
    class _D(dict):
        def __missing__(self, key: str) -> str:
            return ""
    return sanitize(template.format_map(_D(vars)))


def song_relative_path(meta: dict, layout: str, naming: str) -> Path:
    """返回相对 music_dir 的最终路径(不含扩展名之外的目录创建)。"""
    artist = sanitize(meta.get("artist") or "未知歌手")
    album = sanitize(meta.get("album") or "未知专辑")
    track = int(meta.get("track") or 0)
    pos = int(meta.get("pos") or 0)
    fname = safe_format(naming, track=track, pos=pos,
                        title=meta.get("title") or "未知标题",
                        artist=artist, album=album)
    if layout in ("archive", "album"):      # 按专辑分类
        return Path(artist) / album / fname
    if layout == "artist":                  # 按歌手分类
        return Path(artist) / fname
    if layout == "flat":                    # 歌曲平铺
        return Path(fname)
    return Path(sanitize(meta.get("playlist") or "未命名歌单")) / fname  # 按歌单


def _cover_mime(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def tag_file(path: Path, meta: dict, cover: bytes | None, lrc: str | None) -> str | None:
    """写入标签;返回 None 表示成功,否则返回失败说明(供部分失败统计)。"""
    ext = path.suffix.lower()
    try:
        if ext == ".flac":
            _tag_flac(path, meta, cover, lrc)
        elif ext == ".mp3":
            _tag_mp3(path, meta, cover, lrc)
        elif ext == ".m4a":
            _tag_mp4(path, meta, cover, lrc)
        # 其他容器(aac/ape 等)不做标签写入,保留原始流
        return None
    except Exception as e:
        return f"标签写入失败:{e.__class__.__name__}"


def _tag_flac(path: Path, meta: dict, cover: bytes | None, lrc: str | None) -> None:
    f = FLAC(str(path))
    f["title"] = meta.get("title") or ""
    f["artist"] = meta.get("artist") or ""
    f["album"] = meta.get("album") or ""
    if meta.get("album_artist"):
        f["albumartist"] = meta["album_artist"]
    if track := int(meta.get("track") or 0):
        f["tracknumber"] = str(track)
    if disc := int(meta.get("disc") or 0):
        f["discnumber"] = str(disc)
    if meta.get("date"):
        f["date"] = str(meta["date"])
    if lrc:
        f["LYRICS"] = lrc
    if cover:
        pic = Picture()
        pic.type = 3
        pic.mime = _cover_mime(cover)
        pic.desc = "Cover"
        pic.data = cover
        f.clear_pictures()
        f.add_picture(pic)
    f.save()


def _tag_mp3(path: Path, meta: dict, cover: bytes | None, lrc: str | None) -> None:
    tags = ID3()
    tags.add(TIT2(encoding=3, text=meta.get("title") or ""))
    tags.add(TPE1(encoding=3, text=meta.get("artist") or ""))
    tags.add(TALB(encoding=3, text=meta.get("album") or ""))
    if meta.get("album_artist"):
        tags.add(TPE2(encoding=3, text=meta["album_artist"]))
    if track := int(meta.get("track") or 0):
        tags.add(TRCK(encoding=3, text=str(track)))
    if disc := int(meta.get("disc") or 0):
        tags.add(TPOS(encoding=3, text=str(disc)))
    if meta.get("date"):
        tags.add(TDRC(encoding=3, text=str(meta["date"])))
    if lrc:
        tags.add(USLT(encoding=3, lang="chi", desc="", text=lrc))
    if cover:
        tags.add(APIC(encoding=3, mime=_cover_mime(cover), type=3, desc="Cover", data=cover))
    audio = MP3(str(path))
    audio.tags = tags
    audio.save()


def _tag_mp4(path: Path, meta: dict, cover: bytes | None, lrc: str | None) -> None:
    m = MP4(str(path))
    m["\xa9nam"] = [meta.get("title") or ""]
    m["\xa9ART"] = [meta.get("artist") or ""]
    m["aART"] = [meta.get("album_artist") or meta.get("artist") or ""]
    m["\xa9alb"] = [meta.get("album") or ""]
    if track := int(meta.get("track") or 0):
        m["trkn"] = [(track, 0)]
    if meta.get("date"):
        m["\xa9day"] = [str(meta["date"])]
    if lrc:
        m["\xa9lyr"] = [lrc]
    if cover:
        mime = _cover_mime(cover)
        kind = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        m["covr"] = [MP4Cover(cover, imageformat=kind)]
    m.save()
