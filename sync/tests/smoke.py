"""冒烟测试:不依赖网络,验证核心逻辑与 Web 面板可用。运行: python -m tests.smoke"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="wyydl-smoke-"))
os.environ["WYYDL_CONFIG_DIR"] = str(_tmp / "config")
os.environ["WYYDL_DB_DIR"] = str(_tmp / "db")
os.environ["WYYDL_LOG_DIR"] = str(_tmp / "logs")
os.environ["WYYDL_MUSIC_DIR"] = str(_tmp / "music")

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wyydl import config, quality  # noqa: E402
from wyydl.api import NcmApi  # noqa: E402
from wyydl.qrlogin import _extract_music_u  # noqa: E402
from wyydl.state import State  # noqa: E402
from wyydl.tagger import safe_format, sanitize, song_relative_path  # noqa: E402

passed = 0


def ok(name: str, cond: bool) -> None:
    global passed
    assert cond, name
    passed += 1
    print(f"  ✓ {name}")


# ---- 音质协商 ----
CHAIN = ["jymaster", "hires", "lossless", "exhigh", "standard"]
ok("VIP 吃满母带", quality.pick_level({"maxBrLevel": "jymaster", "dlLevel": "jymaster"}, CHAIN) == "jymaster")
ok("歌曲只有 hires", quality.pick_level({"maxBrLevel": "hires", "dlLevel": "jymaster"}, CHAIN) == "hires")
ok("账号仅 lossless", quality.pick_level({"maxBrLevel": "jymaster", "dlLevel": "lossless"}, CHAIN) == "lossless")
ok("无特权字段回退链顶", quality.pick_level({}, CHAIN) == "jymaster")
ok("空权限回退链尾", quality.pick_level(None, ["standard"]) == "standard")
ok("未知档位不设限", quality.pick_level({"maxBrLevel": "未来档位"}, CHAIN) == "jymaster")
ok("降档链", quality.chain_from("lossless", CHAIN) == ["lossless", "exhigh", "standard"])
ok("降档链(sky 以下)", quality.chain_from("sky", CHAIN) == ["hires", "lossless", "exhigh", "standard"])
ok("降档链(未知档=全链)", quality.chain_from("未知档", CHAIN) == CHAIN)

# ---- 命名清洗 ----
ok("非法字符", sanitize('A/B:*c?"<>|') == "A B c")
ok("空名兜底", sanitize("   ") == "未知")
p = song_relative_path({"artist": "周杰伦 / 费玉清", "album": "叶惠美", "title": "晴天",
                        "track": 3, "pos": 7, "playlist": "我的最爱"}, "archive", "{track:02d}. {title}")
ok("archive 路径", str(p) == str(Path("周杰伦 费玉清") / "叶惠美" / "03. 晴天"))
ok("格式化缺失变量", safe_format("{pos:02d}. {artist} - {title}", pos=1, title="T") == "01. - T")

# ---- 配置与凭证 ----
cfg = config.Config.load()
ok("默认配置生成", cfg.schedule == "0 4 * * *" and cfg.layout == "album")
cfg.d["schedule"] = "30 5 * * 1"
cfg.save()
ok("配置保存回读", config.Config.load().schedule == "30 5 * * 1")
config.save_music_u("abc123")
ok("凭证保存回读", config.load_music_u() == "abc123")

# ---- 状态库 ----
st = State(config.DB_DIR / "t.sqlite3")
st.upsert_playlist(1, "测试歌单", 2)
st.set_playlist_songs(1, [(101, 0), (102, 1)])
st.upsert_song(sid=101, title="晴天", artist="周杰伦", status="ok", level="hires", file_path="/m/a.flac")
st.upsert_song(sid=102, title="七里香", status="failed:no_source")
ok("歌单成员", st.playlist_songs(1) == [(101, 0), (102, 1)])
ok("歌曲回读", st.song(101)["level"] == "hires")
ok("部分更新保留旧值", st.song(101)["artist"] == "周杰伦" and st.song(102)["artist"] == "")
ok("统计", st.playlist_stats()[1]["ok"] == 1 and st.playlist_stats()[1]["bad"] == 1)
st.set_playlist_songs(1, [(101, 0), (102, 1), (103, 2)])
st.upsert_song(sid=103, title="等待中的歌")
ok("三态统计(等待不计失败)", st.playlist_stats()[1]["pending"] == 1
   and st.playlist_stats()[1]["ok"] == 1 and st.playlist_stats()[1]["bad"] == 1)
st.record_run("2026-08-28 04:00:00", "2026-08-28 04:10:00", "ok", {"added": 1})
ok("运行记录", st.last_run()["summary"]["added"] == 1)

# ---- cookie 解析 ----
ok("MUSIC_U 提取", _extract_music_u("MUSIC_U=xyz; os=pc; __csrf=x") == "xyz")
ok("MUSIC_U 缺失", _extract_music_u("os=pc") is None)

# ---- 扫码状态解析(check 接口状态码在顶层,与 key/create 的嵌套结构不同)----
import httpx as _hx  # noqa: E402
from wyydl.api import NcmApi as _NcmApi  # noqa: E402


def _mock_api(responses):
    seq = list(responses)

    def handler(request):
        return _hx.Response(200, json=seq.pop(0))

    a = _NcmApi("http://mock", lambda: None, (0.0, 0.0))
    a.client = _hx.Client(transport=_hx.MockTransport(handler))
    return a


ok("扁平结构 801", _mock_api([{"code": 801, "message": "等待扫码"}]).qr_check("k")["code"] == 801)
ok("嵌套结构 802", _mock_api([{"code": 200, "data": {"code": 802}}]).qr_check("k")["code"] == 802)
r = _mock_api([{"code": 803, "cookie": "MUSIC_U=xyz; Path=/"}]).qr_check("k")
ok("803 返回 cookie", r["code"] == 803 and r["cookie"] == "MUSIC_U=xyz; Path=/")
r = _mock_api([{"code": 200, "data": {"code": 803, "cookie": "MUSIC_U=q"}}]).qr_check("k")
ok("嵌套结构 803+cookie", r["code"] == 803 and r["cookie"] == "MUSIC_U=q")
ok("502 自动 noCookie 重试", _mock_api([{"code": 502}, {"code": 801}]).qr_check("k")["code"] == 801)
ok("未知结构 code=0", _mock_api([{"foo": 1}]).qr_check("k")["code"] == 0)

# ---- 歌单 trackIds / 权限合并 / 账号歌单分页 ----
a = _mock_api([{"playlist": {"name": "X", "trackIds": [{"id": 1}, {"id": 2}, {"id": 3}]}}])
ok("trackIds 全量获取", [t["id"] for t in a.playlist_tracks(1)] == [1, 2, 3])
a = _mock_api([{"playlist": {"trackIds": []}},
               {"songs": [{"id": 1}, {"id": 2}], "total": 2}])
ok("trackIds 空时兜底分页", [t["id"] for t in a.playlist_tracks(1)] == [1, 2])
a = _mock_api([{"songs": [{"id": 1, "name": "S"}], "privileges": [{"id": 1, "maxBrLevel": "hires"}]}])
ok("详情合并音质权限", a.song_detail([1])[0]["privilege"]["maxBrLevel"] == "hires")
a = _mock_api([{"profile": {"userId": 9}},
               {"playlist": [{"id": 1}, {"id": 2}], "more": True},
               {"playlist": [{"id": 3}], "more": False}])
ok("账号 uid", a.user_account()["userId"] == 9)
ok("账号歌单分页", [p["id"] for p in a.user_playlists(9)] == [1, 2, 3])

# ---- NFO 生成 ----
import xml.etree.ElementTree as _ET  # noqa: E402

from wyydl import nfo as _nfo  # noqa: E402

_p = _tmp / "song_test.nfo"
_nfo.write_song_nfo(_p, title="A & B <Live>", artist="歌手", album="专辑", track=3, disc=1,
                    year="2020", duration=245, genre="Pop", ncm_id=12345)
_r = _ET.parse(_p).getroot()
ok("单曲 NFO 根元素", _r.tag == "song")
ok("单曲 NFO 转义与字段", _r.findtext("title") == "A & B <Live>" and _r.findtext("track") == "3"
   and _r.findtext("duration") == "245" and _r.find("uniqueid").get("type") == "netease")
_p2 = _tmp / "album_test.nfo"
_nfo.write_album_nfo(_p2, title="专辑", artist="歌手", tracks=[(2, "B"), (1, "A")])
_r2 = _ET.parse(_p2).getroot()
ok("专辑 NFO 曲目排序", [t.findtext("title") for t in _r2.findall("track")] == ["A", "B"])

# ---- 下载进度回调与活动列表 ----
from wyydl.syncer import SyncEngine  # noqa: E402

eng = SyncEngine(cfg, st, _NcmApi("http://127.0.0.1:1", config.load_music_u, (0.0, 0.0)))
eng.active[1] = {"title": "T", "artist": "A", "downloaded": 0, "total": 100}
eng._dl_tick(1, 50, 200)
ok("下载进度回调", eng.active[1]["downloaded"] == 50 and eng.active[1]["total"] == 200)
eng.active.pop(1)
ok("活动列表清理", not eng.active)

# ---- 通知事件 ----
from wyydl import notify as _notify  # noqa: E402

cfg_ev = {"notify": {"type": "feishu", "url": "x", "events": {"on_changes": True}}}
ok("事件:有变更推送", _notify.should_notify(cfg_ev, {"status": "ok", "added": 1, "failed": 0, "partial": 0}))
ok("事件:无变更不推", not _notify.should_notify(cfg_ev, {"status": "ok", "added": 0, "failed": 0, "partial": 0}))
ok("事件:检测完成全推", _notify.should_notify(
    {"notify": {"events": {"on_complete": True}}}, {"status": "ok", "added": 0}))
ok("事件:失败推送", _notify.should_notify(
    {"notify": {"events": {"on_failed": True}}}, {"status": "ok", "failed": 2, "added": 0}))
ok("事件:登录失效推送", _notify.should_notify(
    {"notify": {"events": {"on_login_expired": True}}}, {"status": "login_expired"}))

# ---- 本地信息缺失识别与手动匹配 ----
_mflac = config.MUSIC_DIR / "手动测试.flac"
import struct as _st  # noqa: E402
_si = _st.pack(">HHHH", 4096, 4096, 0, 0) \
    + _st.pack(">Q", (44100 << 12) | (1 << 9) | (15 << 4)) + b"\x00" * 16 + b"\x00\x00"
_mflac.write_bytes(b"fLaC" + b"\x80" + _st.pack(">I", 34)[1:] + _si)  # 最小合法 flac(仅 STREAMINFO)

_cfg2 = config.Config.load()
_cfg2.d["layout"] = "artist"
_eng2 = SyncEngine(_cfg2, st, _mock_api([
    {"songs": [{"id": 7, "name": "测试歌",
                "ar": [{"id": 1, "name": "歌手A"}, {"id": 2, "name": "歌手B"}],
                "al": {"id": 5, "name": "专辑X", "picUrl": ""}, "no": 3,
                "publishTime": 1072800000000, "dt": 250000}]},
    {"lrc": {"lyric": "[00:00]测试歌词"}},
    {"album": {"name": "专辑X", "genre": ["Pop"], "company": "厂牌",
               "publishTime": 1072800000000, "description": ""}},
]))
_lf = _eng2.local_files()
ok("本地列表含未入库文件", any(x["name"] == "手动测试.flac" and not x["in_db"] and x["missing"] for x in _lf))
_r = _eng2.match_local_file(7, str(_mflac))
ok("手动匹配补全", _r["ok"] and _r["title"] == "测试歌" and _r["artist"] == "歌手A / 歌手B")
ok("按歌手布局移动", _r["path"].endswith("歌手A 歌手B/测试歌.flac") and not _mflac.exists())
_newp = st.song(7)["file_path"]
# 合成文件无真实音频帧,mutagen 拒绝写标签属预期;验证「部分未成功」被如实上报
ok("合成文件标签失败被上报", any("标签写入失败" in w for w in (_r.get("warns") or [])))
ok("移动后生成 NFO", (Path(_newp).parent / "测试歌.nfo").exists()
   and (Path(_newp).parent / "artist.nfo").exists())
ok("移动后不再缺失", not any(x["path"] == _newp and x["missing"] for x in _eng2.local_files()))

_eng3 = SyncEngine(config.Config.load(), st, _mock_api([
    {"songs": [{"id": 7, "name": "测试歌",
                "ar": [{"id": 1, "name": "歌手A"}],
                "al": {"id": 5, "name": "专辑X", "picUrl": ""}, "no": 3,
                "publishTime": 1072800000000, "dt": 250000}]},
    {"lrc": {"lyric": "[00:00]测试歌词"}},
    {"album": {"name": "专辑X", "genre": ["Pop"], "company": "厂牌",
               "publishTime": 1072800000000, "description": ""}},
]))
_r3 = _eng3.refetch_local(_newp)
ok("重新刮削", _r3["ok"] and _r3["title"] == "测试歌")
_newp = st.song(7)["file_path"]  # refetch 按默认 album 布局移动了文件
_r4 = _eng3.edit_local(_newp, "改标题", "改歌手", "改专辑", 5)
ok("手动修改信息", _r4["ok"] and st.song(7)["title"] == "改标题" and st.song(7)["artist"] == "改歌手")
_newp = st.song(7)["file_path"]
ok("手动修改后不再缺失", not any(x["path"] == _newp and x["missing"] for x in _eng3.local_files()))
_a = _mock_api([{"result": {"songs": [{"id": 9, "name": "搜索曲", "ar": [{"name": "甲"}],
                                       "al": {"name": "辑"}, "dt": 180000}]}}])
ok("搜索接口", _a.search("关键词")[0]["name"] == "搜索曲")

# ---- Web 面板 ----
from wyydl.web import AppContext, create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

api = NcmApi("http://127.0.0.1:1", config.load_music_u, (0.0, 0.0))
eng = type("E", (), {"cfg": cfg, "cookie_ok": lambda self: False, "running": False,
                     "progress": {}, "recent": [], "active": {},
                     "try_run": lambda self, ids=None, trigger="m": False})()
client = TestClient(create_app(AppContext(cfg=cfg, state=st, api=api, engine=eng)))
r = client.get("/api/status")
ok("面板 /api/status", r.status_code == 200 and r.json()["layout"] == "album")
r = client.get("/")
ok("面板首页", r.status_code == 200 and "网易云歌单同步" in r.text)
r = client.get("/api/tracks/1")
ok("面板曲目列表", r.status_code == 200 and r.json()["tracks"][0]["title"] == "晴天")

# ---- 结构化设置 ----
cfg.d["playlists"] = [{"id": 999}]
r = client.get("/api/settings")
ok("读取设置", r.status_code == 200 and r.json()["schedule"] == "30 5 * * 1")
ok("设置含音质链", r.json()["chain"] == ["jymaster", "hires", "lossless", "exhigh", "standard"])
bad = client.put("/api/settings", json={"schedule": "not-cron", "chain": ["hires"]})
ok("非法 cron 被拒", bad.status_code == 400)
bad = client.put("/api/settings", json={"schedule": "0 4 * * *", "chain": []})
ok("空音质链被拒", bad.status_code == 400)
good = client.put("/api/settings", json={
    "schedule": "30 5 * * *", "layout": "flat", "chain": ["hires", "lossless"],
    "upgrade_existing": True, "lrc": True, "embed": False, "mirror": False,
    "nfo": True,
    "notify_type": "feishu", "notify_url": "https://example.com/hook", "notify_secret": "",
    "events": {"on_failed": False, "on_start": True},
    "web_enabled": True, "web_port": 8286, "web_token": "t1",
    "concurrency": 2, "delay_min": 1, "delay_max": 2})
ok("保存结构化设置", good.status_code == 200)
ok("设置生效", cfg.d["schedule"] == "30 5 * * *" and cfg.d["web"]["token"] == "t1"
   and cfg.d["lyrics"]["embed"] is False and cfg.d["limits"]["download_concurrency"] == 2
   and cfg.d["nfo"] is True and cfg.layout == "flat"
   and cfg.quality_chain == ["hires", "lossless"])
ok("通知事件生效", cfg.d["notify"]["events"]["on_failed"] is False
   and cfg.d["notify"]["events"]["on_start"] is True
   and cfg.d["notify"]["events"]["on_changes"] is True)
ok("表单保存不影响歌单与 api_base", cfg.playlist_ids() == [999] and cfg.d["api_base"].startswith("http"))

print(f"\n全部 {passed} 项通过 ✅")
