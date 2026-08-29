"""SQLite 状态库:歌单/歌曲/运行记录。单写多读,内部加锁。"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS playlists(
  pid INTEGER PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  track_count INTEGER NOT NULL DEFAULT 0,
  last_sync TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS songs(
  sid INTEGER PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  artist TEXT NOT NULL DEFAULT '',
  album TEXT NOT NULL DEFAULT '',
  file_path TEXT NOT NULL DEFAULT '',
  ext TEXT NOT NULL DEFAULT '',
  level TEXT NOT NULL DEFAULT '',
  br INTEGER NOT NULL DEFAULT 0,
  size INTEGER NOT NULL DEFAULT 0,
  md5 TEXT NOT NULL DEFAULT '',
  downloaded_at TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'new'
);
CREATE TABLE IF NOT EXISTS playlist_songs(
  pid INTEGER NOT NULL,
  sid INTEGER NOT NULL,
  pos INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(pid, sid)
);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started TEXT NOT NULL,
  finished TEXT NOT NULL,
  status TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '{}'
);
"""

_SONG_FIELDS = ("sid", "title", "artist", "album", "file_path", "ext", "track_no",
                "level", "br", "size", "md5", "downloaded_at", "status", "fail_count", "last_error")


def _rows_to_dicts(cur: sqlite3.Cursor) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


class State:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            for col in ("track_no INTEGER NOT NULL DEFAULT 0",
                        "fail_count INTEGER NOT NULL DEFAULT 0",
                        "last_error TEXT NOT NULL DEFAULT ''"):
                try:  # 旧库迁移:补充列
                    self._conn.execute(f"ALTER TABLE songs ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass  # 列已存在
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    # ---- playlists ----
    def upsert_playlist(self, pid: int, name: str, track_count: int, last_sync: str = "") -> None:
        self._exec(
            "INSERT INTO playlists(pid,name,track_count,last_sync) VALUES(?,?,?,?) "
            "ON CONFLICT(pid) DO UPDATE SET name=excluded.name,track_count=excluded.track_count,"
            "last_sync=CASE WHEN excluded.last_sync='' THEN playlists.last_sync ELSE excluded.last_sync END",
            (pid, name, track_count, last_sync),
        )

    def playlists(self) -> list[dict]:
        cur = self._exec("SELECT * FROM playlists ORDER BY pid")
        return _rows_to_dicts(cur)

    def remove_playlist(self, pid: int) -> None:
        self._exec("DELETE FROM playlists WHERE pid=?", (pid,))
        self._exec("DELETE FROM playlist_songs WHERE pid=?", (pid,))

    def set_playlist_songs(self, pid: int, pairs: list[tuple[int, int]]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM playlist_songs WHERE pid=?", (pid,))
            self._conn.executemany(
                "INSERT OR IGNORE INTO playlist_songs(pid,sid,pos) VALUES(?,?,?)",
                [(pid, sid, pos) for sid, pos in pairs],
            )
            self._conn.commit()

    def playlist_songs(self, pid: int) -> list[tuple[int, int]]:
        cur = self._exec("SELECT sid,pos FROM playlist_songs WHERE pid=? ORDER BY pos", (pid,))
        return [(r[0], r[1]) for r in cur.fetchall()]

    def membership_counts(self) -> dict[int, int]:
        """每个 sid 被多少个歌单/来源引用(用于「重复」标记)。"""
        cur = self._exec("SELECT sid, COUNT(*) AS c FROM playlist_songs GROUP BY sid")
        return {r["sid"]: int(r["c"]) for r in _rows_to_dicts(cur)}

    def playlist_stats(self) -> dict[int, dict]:
        """每个歌单的 ok / pending(等待下载) / bad(真实失败) 歌曲数。"""
        cur = self._exec(
            "SELECT ps.pid AS pid, "
            " SUM(CASE WHEN s.status='ok' THEN 1 ELSE 0 END) AS ok, "
            " SUM(CASE WHEN s.sid IS NULL OR IFNULL(s.status,'') IN ('','new') THEN 1 ELSE 0 END) AS pending, "
            " SUM(CASE WHEN s.sid IS NOT NULL AND IFNULL(s.status,'') NOT IN ('','new','ok') THEN 1 ELSE 0 END) AS bad, "
            " COUNT(*) AS total "
            "FROM playlist_songs ps LEFT JOIN songs s ON s.sid=ps.sid GROUP BY ps.pid"
        )
        return {r["pid"]: dict(r) for r in _rows_to_dicts(cur)}

    # ---- songs ----
    def song(self, sid: int) -> dict | None:
        cur = self._exec(f"SELECT {','.join(_SONG_FIELDS)} FROM songs WHERE sid=?", (sid,))
        rows = _rows_to_dicts(cur)
        return rows[0] if rows else None

    def all_songs(self) -> list[dict]:
        cur = self._exec(f"SELECT {','.join(_SONG_FIELDS)} FROM songs")
        return _rows_to_dicts(cur)

    def upsert_song(self, **kw) -> None:
        sid = int(kw["sid"])
        row = self.song(sid) or {f: (0 if f in ("br", "size", "track_no", "fail_count") else "") for f in _SONG_FIELDS}
        row["sid"] = sid
        for f in _SONG_FIELDS:
            v = kw.get(f)
            if v is not None:
                row[f] = v
        self._exec(
            f"INSERT OR REPLACE INTO songs({','.join(_SONG_FIELDS)}) "
            f"VALUES({','.join('?' * len(_SONG_FIELDS))})",
            tuple(row[f] for f in _SONG_FIELDS),
        )

    # ---- runs ----
    def record_run(self, started: str, finished: str, status: str, summary: dict) -> int:
        cur = self._exec(
            "INSERT INTO runs(started,finished,status,summary) VALUES(?,?,?,?)",
            (started, finished, status, json.dumps(summary, ensure_ascii=False)),
        )
        return int(cur.lastrowid or 0)

    def runs(self, limit: int = 20) -> list[dict]:
        cur = self._exec("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        out = _rows_to_dicts(cur)
        for r in out:
            try:
                r["summary"] = json.loads(r.get("summary") or "{}")
            except json.JSONDecodeError:
                r["summary"] = {}
        return out

    def last_run(self) -> dict | None:
        rs = self.runs(1)
        return rs[0] if rs else None
