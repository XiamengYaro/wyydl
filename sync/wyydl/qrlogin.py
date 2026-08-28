"""命令行扫码登录:打印 ASCII 二维码,轮询并保存 MUSIC_U。"""
from __future__ import annotations

import re
import time

import qrcode

from . import config
from .api import NcmApi
from .config import Config


def _extract_music_u(cookie: str) -> str | None:
    m = re.search(r"MUSIC_U=([^;\s]+)", cookie or "")
    return m.group(1) if m else None


def run_login(api: NcmApi) -> bool:
    key = api.qr_key()
    data = api.qr_create(key)
    print("请使用网易云音乐 App 扫码登录:")
    uri = data.get("qrurl") or ""
    if uri:
        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        qr.print_ascii(invert=True)
    else:
        print(data.get("qrimg", "(无二维码)"))
    while True:
        time.sleep(2)
        r = api.qr_check(key)
        code = r.get("code")
        if code == 800:
            print("二维码已过期,请重新运行。")
            return False
        if code == 802:
            print("已扫码,请在手机上确认…")
        if code == 803:
            token = _extract_music_u(r.get("cookie") or "")
            if not token:
                print("登录成功但未取到 MUSIC_U,请重试。")
                return False
            config.save_music_u(token)
            print("登录成功,凭证已保存。")
            return True


def main() -> None:
    cfg = Config.load()
    api = NcmApi(cfg.api_base, config.load_music_u, (0.5, 1.5))
    try:
        run_login(api)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
