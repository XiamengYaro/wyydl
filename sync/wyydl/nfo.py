"""NFO 元数据生成(Jellyfin/Emby/Kodi 可读):单曲 <歌名>.nfo、album.nfo、artist.nfo。"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

_HEAD = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>'


def write_song_nfo(path: Path, *, title: str, artist: str, album: str = "",
                   albumartist: str = "", track: int = 0, disc: int = 0,
                   year: str = "", duration: int = 0, genre: str = "",
                   ncm_id: str = "") -> None:
    lines = [_HEAD, "<song>"]

    def tag(key: str, val: object) -> None:
        if val not in (None, "", 0):
            lines.append(f"  <{key}>{escape(str(val))}</{key}>")

    tag("title", title)
    tag("artist", artist)
    tag("album", album)
    tag("albumartist", albumartist)
    tag("track", int(track or 0))
    tag("disc", int(disc or 0))
    tag("year", year)
    tag("genre", genre)
    tag("duration", int(duration or 0))  # 秒
    if ncm_id:
        lines.append(f'  <uniqueid type="netease">{escape(str(ncm_id))}</uniqueid>')
    lines.append("</song>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_album_nfo(path: Path, *, title: str, artist: str, year: str = "",
                    genres: str = "", label: str = "", releasedate: str = "",
                    plot: str = "", tracks: list[tuple[int, str]] | None = None) -> None:
    lines = [_HEAD, "<album>"]

    def tag(key: str, val: object) -> None:
        if val not in (None, ""):
            lines.append(f"  <{key}>{escape(str(val))}</{key}>")

    tag("title", title)
    tag("artist", artist)
    tag("year", year)
    tag("genre", genres)
    tag("label", label)
    tag("releasedate", releasedate)
    tag("plot", plot)
    for pos, t in sorted(tracks or []):
        lines.append("  <track>")
        if pos:
            lines.append(f"    <position>{int(pos)}</position>")
        lines.append(f"    <title>{escape(t)}</title>")
        lines.append("  </track>")
    lines.append("</album>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_artist_nfo(path: Path, *, name: str, genre: str = "", ncm_id: str = "") -> None:
    lines = [_HEAD, "<artist>"]
    lines.append(f"  <name>{escape(name)}</name>")
    if genre:
        lines.append(f"  <genre>{escape(genre)}</genre>")
    if ncm_id:
        lines.append(f'  <uniqueid type="netease">{escape(ncm_id)}</uniqueid>')
    lines.append("</artist>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
