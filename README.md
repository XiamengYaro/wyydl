# wyydl · 网易云歌单定时同步(fnOS NAS)

定时把指定网易云歌单以**当前账号可用的最高音质**同步到 NAS 音乐目录:自动协商音质(jymaster/hires/lossless…逐级降档)、下载校验(MD5)、写入完整标签(封面/歌词)、生成 NFO、按「歌手/专辑」归档并生成每个歌单的 `.m3u8`。自带 Web 控制面板与飞书 webhook 通知,并内置 [ncmdump-go](https://git.taurusxin.com/taurusxin/ncmdump-go) 兜底转换 `.ncm` 文件。

API 服务:[NeteaseCloudMusicApiEnhanced/api-enhanced](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced)(Docker 镜像 `moefurina/ncm-api`)。

> ⚠️ 仅供个人学习与备份,请尊重版权,勿商用、勿分享下载内容。
>
> 🤖 **AI 声明:本项目为纯 AI Vibe Coding 生成**——全部代码、文档与打包配置均由 AI(编码代理)编写,人类仅负责提出需求、验收与点击"授权"。欢迎围观 AI 的工程能力。

**两种部署方式**:
- **fnOS 应用包(推荐)**:见 [fpk/README.md](fpk/README.md),`./build.sh` 产出 `wyydl.fpk`,应用中心安装即用;也可直接从 [Releases](https://github.com/XiamengYaro/wyydl/releases) 下载打好的 `wyydl.fpk`;
- **Docker Compose**:按下述步骤手动部署。

推送到 GitHub 的 `v*` 标签会自动跑冒烟测试、打包 fpk 并发布 Release(见 [.github/workflows/release.yml](.github/workflows/release.yml))。

## 开源许可

[MIT](LICENSE) © 2026 Xiameng

## 目录结构

```
docker-compose.yml        # 部署入口(在 NAS 上放到 /vol1/appdata/wyydl/)
sync/                     # 同步程序(Python)
├── Dockerfile            # 内含 ncmdump-go 二进制(x86_64/arm64 自适应)
└── wyydl/
    ├── main.py           # 入口:守护(Web+调度)/ --once
    ├── syncer.py         # 同步引擎
    ├── api.py            # ncm-api 客户端(限速/重试/cookie)
    ├── quality.py        # 音质协商
    ├── downloader.py     # 下载与 MD5 校验
    ├── tagger.py         # mutagen 标签/封面/歌词
    ├── ncm.py            # ncmdump-go 调用
    ├── state.py          # SQLite 状态库
    ├── notify.py         # 飞书 webhook
    ├── qrlogin.py        # CLI 扫码登录
    └── web.py + static/  # Web 面板
```

## 部署(fnOS)

1. 应用中心安装 **Docker**;建两个共享文件夹:`/vol1/media/music`(音乐库)、`/vol1/appdata/wyydl`(程序数据)。
2. 把本目录(至少 `docker-compose.yml` 和 `sync/`)上传到 `/vol1/appdata/wyydl/`。
3. fnOS Docker → 项目/Compose → 新建,指向该目录;或 SSH 执行:
   ```bash
   cd /vol1/appdata/wyydl && docker compose up -d --build
   ```
4. 浏览器打开 `http://<NAS_IP>:8286` 进入 Web 面板。

### 首次使用

1. 面板右上角「**扫码登录**」→ 手机网易云 App 扫码确认,凭证自动保存。
2. 输入歌单 ID(网页版歌单 URL 中的数字)「添加」,会立即同步一轮;之后按 `schedule`(默认每天 04:00)自动运行。
3. 「设置」里按需修改:音质链、命名模板、飞书 webhook(支持签名 secret)、cron 计划等,保存即生效。

### 命令行(可选)

```bash
docker exec -it wyydl-sync python -m wyydl.qrlogin          # 终端扫码登录
docker exec wyydl-sync python -m wyydl.main --once          # 手动跑一轮
docker exec wyydl-sync python -m wyydl.main --once --playlist 24381616
```

## 音乐目录产出

```
/vol1/media/music/
├── 周杰伦/叶惠美/03. 晴天.flac      # 按歌手/专辑归档(layout: archive)
├── 03. 晴天.nfo                     # 单曲元数据;专辑/歌手目录另有 album.nfo、artist.nfo
├── .lrc 歌词与内嵌封面随文件生成
├── 我的喜欢的音乐.m3u8              # 每个歌单一个播放列表
├── _ncm_inbox/                      # 丢入 .ncm 自动转换入库(官方客户端下载兜底)
└── _trash/                          # mirror 开启时被移除歌曲的暂存
```

## 关键配置(config.yaml,可在面板改)

```yaml
schedule: "0 4 * * *"        # cron
layout: archive              # archive=歌手/专辑 + m3u8 | playlist=按歌单分文件夹
quality:
  chain: [jymaster, hires, lossless, exhigh, standard]
  upgrade_existing: true     # 出现更高音质自动重下
lyrics: {lrc: true, embed: true}
nfo: true                    # 生成单曲 <歌名>.nfo + album.nfo + artist.nfo(Jellyfin/Emby/Kodi)
mirror: false                # 歌单移除的歌曲是否移入 _trash
notify: {type: feishu, url: "https://open.feishu.cn/open-apis/bot/v2/hook/xxx", secret: "签名密钥(可选)"}
web: {enabled: true, port: 8286, token: ""}   # token 建议在局域网不可信时设置
```

## 逻辑说明

- **音质**:先按 `privilege.maxBrLevel`(歌曲上限)× `dlLevel`(账号上限)× 配置链取目标档;取流返回试听片段(`freeTrialInfo`)或空链时逐档降档;下载后校验大小与 MD5。
- **增量**:SQLite 记录每首歌的文件/音质/校验值;只处理新增与可升级曲目;文件改动不会重复下载。
- **NFO**:下载完成后自动写单曲 `<歌名>.nfo`(标题/歌手/专辑/音轨号/时长/流派/网易云 ID);归档布局下还会在专辑、歌手目录写 `album.nfo` 与 `artist.nfo`(流派/厂牌/发行日期/简介/曲目列表,来自 `/album` 接口,按专辑缓存请求)。
- **删除**:默认只从 m3u8 移除不删文件;`mirror: true` 时移入 `_trash`。
- **风控**:全部请求经自建 ncm-api,带 1–3s 随机间隔;不自动化登录;`MUSIC_U` 过期时飞书通知提醒重扫。
- **NCM 兜底**:把 PC 客户端下载目录指向 SMB 共享的 `_ncm_inbox`,每轮同步前自动 `ncmdump-go` 解密入库。
