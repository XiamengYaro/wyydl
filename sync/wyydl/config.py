"""配置与凭证管理。所有路径可被环境变量覆盖,容器内由 compose 挂载决定。"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import yaml

ENV_MAP = {
    "config": "WYYDL_CONFIG_DIR",
    "db": "WYYDL_DB_DIR",
    "logs": "WYYDL_LOG_DIR",
    "music": "WYYDL_MUSIC_DIR",
}

DEFAULTS: dict = {
    "schedule": "0 4 * * *",          # cron: 每天凌晨 4 点
    "api_base": os.environ.get("NCM_API", "http://ncm-api:3000"),
    "layout": "archive",              # archive=按歌手/专辑归档+m3u8 | playlist=按歌单分文件夹
    "naming": "",                     # 留空用 layout 对应默认模板
    "playlists": [],                  # [{"id": 123, "name": "可选自定义名"}]
    "quality": {
        "chain": ["jymaster", "hires", "lossless", "exhigh", "standard"],
        "upgrade_existing": True,     # 发现更高音质时重新下载
    },
    "lyrics": {"lrc": True, "embed": True},
    "nfo": True,                      # 生成 album.nfo / artist.nfo(Jellyfin/Emby/Kodi)
    "mirror": False,                  # playlist 布局下,歌单移除的歌曲是否移入 _trash
    "notify": {"type": "feishu", "url": "", "secret": ""},
    "web": {"enabled": True, "port": 8286, "token": ""},
    "limits": {"download_concurrency": 3, "api_delay": [1.0, 3.0]},
}


def _dir(kind: str) -> Path:
    v = os.environ.get(ENV_MAP[kind])
    if v:
        p = Path(v)
    else:
        p = Path(f"/{kind}")
        if not p.exists():  # 本地开发:落到包外 data/
            p = Path(__file__).resolve().parent.parent / "data" / kind
    p.mkdir(parents=True, exist_ok=True)
    return p


CONFIG_DIR = _dir("config")
DB_DIR = _dir("db")
LOG_DIR = _dir("logs")
MUSIC_DIR = _dir("music")

CONFIG_FILE = CONFIG_DIR / "config.yaml"
SECRET_FILE = CONFIG_DIR / "secret.yaml"


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, data: dict | None = None):
        self.d: dict = _merge(DEFAULTS, data or {})

    # ---- 常用属性 ----
    @property
    def schedule(self) -> str:
        return str(self.d["schedule"])

    @property
    def api_base(self) -> str:
        return str(self.d["api_base"]).rstrip("/")

    @property
    def layout(self) -> str:
        return self.d["layout"] if self.d["layout"] in ("archive", "playlist") else "archive"

    @property
    def quality_chain(self) -> list[str]:
        chain = self.d["quality"].get("chain") or DEFAULTS["quality"]["chain"]
        return [str(x) for x in chain]

    @property
    def naming(self) -> str:
        if self.d.get("naming"):
            return str(self.d["naming"])
        return "{track:02d}. {title}" if self.layout == "archive" else "{pos:02d}. {artist} - {title}"

    def playlist_ids(self) -> list[int]:
        out = []
        for p in self.d.get("playlists") or []:
            try:
                out.append(int(p["id"]))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(yaml.safe_dump(self.d, allow_unicode=True, sort_keys=False), encoding="utf-8")

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_FILE.exists():
            try:
                data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                data = {}
            return cls(data if isinstance(data, dict) else {})
        cfg = cls()
        cfg.save()
        return cfg


def load_music_u() -> str | None:
    if not SECRET_FILE.exists():
        return None
    try:
        data = yaml.safe_load(SECRET_FILE.read_text(encoding="utf-8")) or {}
        t = data.get("music_u")
        return str(t) if t else None
    except yaml.YAMLError:
        return None


def save_music_u(token: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SECRET_FILE.write_text(yaml.safe_dump({"music_u": token}), encoding="utf-8")
    os.chmod(SECRET_FILE, stat.S_IRUSR | stat.S_IWUSR)
