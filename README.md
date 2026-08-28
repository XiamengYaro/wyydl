# wyydl · 网易云歌单定时同步(fnOS NAS)

> 🤖 **本项目为纯 AI Vibe Coding 生成**——全部代码、文档与打包配置均由 AI(编码代理)编写。

定时把指定的网易云音乐歌单同步到 NAS 音乐目录:以**当前账号可用的最高音质**下载,自动写入完整歌曲信息(标签/封面/歌词/流派/厂牌),生成 Jellyfin/Emby 友好的 NFO 元数据,支持四种下载文件布局,并为每个歌单生成 `.m3u8` 播放列表。自带 **Web 控制面板**(扫码登录、歌单管理、本地音乐刮削、实时进度)与**飞书/Webhook 通知**。

> ⚠️ **免责声明**:本项目仅供个人学习与备份使用。请尊重版权,勿用于商业用途,勿分享下载的内容。使用本项目产生的一切后果由使用者自行承担。

## ✨ 功能特性

- **音质协商**:按 `歌曲上限 × 账号权限 × 配置链` 三重协商,自动在 超清母带(jymaster)/ Hi-Res / 无损 / 极高 / 标准之间降档;检测并跳过试听片段;下载后校验大小与 MD5。
- **全量歌曲信息写入**:标题、歌手、专辑、专辑歌手、音轨号、碟号、年份、**流派**、**厂牌**、网易云歌曲 ID、**内嵌封面**(1500px)、**内嵌歌词**;FLAC / MP3 / M4A 分别按标准写法,MP3 增量更新不抹掉已有内容。
- **NFO 元数据**:单曲 `<歌名>.nfo` + 专辑目录 `album.nfo`(流派/厂牌/发行日期/简介/曲目列表)+ 歌手目录 `artist.nfo`,Jellyfin/Emby/Kodi 可读。
- **四种下载布局**:按专辑分类(默认)/ 按歌手分类 / 歌曲平铺 / 按歌单分文件夹;每个歌单生成 `.m3u8`。
- **增量同步**:SQLite 记录每首歌的状态;只下载新增与可升级曲目;歌单移除默认保留本地文件。
- **本地音乐管理**:列出音乐库全部文件,支持**重新刮削**(按已匹配 ID 重抓全部信息)、**手动匹配**(搜索网易云指定曲目)、**手动编辑**(直接修改标题/歌手/专辑/音轨),缺失判定基于实际信息。
- **Web 控制面板**:扫码登录、账号歌单一键导入、结构化设置、实时下载进度(含每首的百分比/速度)、运行日志、通知测试。
- **飞书/Webhook 通知**:7 种事件逐项开关(开始/完成/变更/失败/部分未成功/登录失效/异常)。
- **fnOS 应用包**:一键安装、桌面入口、安装向导自定义端口与音乐目录、升级自动重建镜像。

## 🏗️ 工作原理

```
                 ┌────────────────────────┐
   手机 App       │  wyydl-sync (Python)   │       ┌──────────────────────┐
   扫码 ────────► │  · Web 面板 :8286      │──────►│ SQLite 状态库         │
                 │  · 定时调度(cron)      │       │ 曲目/文件/音质/校验值 │
   网易云歌单 ──► │  · 音质协商 → 下载      │       └──────────────────────┘
                 │  · 打标签/歌词/NFO      │       ┌──────────────────────┐
                 │  · 本地刮削/匹配        │──────►│ 音乐目录(四种布局)  │
                 └───────────┬────────────┘       │ + m3u8 + cover.jpg   │
                             │ 全部网易云请求       └──────────────────────┘
                             ▼
                 ┌────────────────────────┐
                 │ ncm-api (api-enhanced) │────► music.163.com
                 │ 自建 API 服务,带登录态 │
                 └────────────────────────┘
```

所有网易云请求经自建 [api-enhanced](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced) 服务转发(带登录 Cookie、限速 1–3s 随机间隔),NAS 只与自家容器和网易 CDN 通信。

## 📦 安装

### 方式一:fnOS 应用包(推荐)

1. 从 [Releases](https://github.com/XiamengYaro/wyydl/releases) 下载最新的 `wyydl.fpk`;
2. fnOS 应用中心安装 **Docker** 应用;
3. 应用中心 → 手动安装(或 `appcenter-cli install-fpk wyydl.fpk`),安装向导中设置 **Web 面板端口**(默认 8286)与**音乐保存目录**(默认共享目录 `wyydl/music`,或自定义绝对路径);
4. 安装后桌面出现应用卡片,点击直达 Web 面板;首次启动会构建镜像(需访问 pypi,约 1–3 分钟)。

fnOS 打包细节见 [fpk/README.md](fpk/README.md)。

### 方式二:Docker Compose(任意 NAS/Linux)

```bash
git clone https://github.com/XiamengYaro/wyydl.git
cd wyydl
# 按需修改 docker-compose.yml 里的挂载路径
docker compose up -d --build
```

`docker-compose.yml` 需要的挂载:

| 容器路径 | 用途 |
|----------|------|
| `/music` | 音乐输出目录 |
| `/config` | config.yaml + secret.yaml(登录凭证) |
| `/db` | SQLite 状态库 |
| `/logs` | 运行日志 |

### 首次使用(三步)

1. **扫码登录**:面板右上角「扫码登录」→ 手机网易云 App 扫码并确认,凭证(MUSIC_U)自动保存到 `config/secret.yaml`;
2. **添加歌单**:「账号歌单」一键列出账号内全部歌单点选添加,或直接输入歌单 ID(网页版歌单 URL 中的数字);添加后立即同步一轮;
3. **调设置**:音质链、布局、通知等在「设置」页修改,保存即生效(含 cron 热更新)。

之后按 `schedule`(默认每天 04:00)自动增量同步。

### 命令行(可选)

```bash
docker exec -it wyydl-sync python -m wyydl.qrlogin              # 终端扫码登录(ASCII 二维码)
docker exec wyydl-sync python -m wyydl.main --once              # 手动执行一轮同步
docker exec wyydl-sync python -m wyydl.main --once --playlist 24381616   # 只同步指定歌单
```

## 🖥️ Web 面板

| 区域 | 功能 |
|------|------|
| 顶栏 | 登录状态灯、下次运行时间、cron、布局;**立即同步**、**扫码登录** |
| 同步进行中 | 当前阶段、已完成/总数、进度条、**正在下载列表**(每首:百分比 + 已下载/总大小)、最近完成(✓/✗ 与原因) |
| 歌单 | 曲目/已入库/等待/失败四态统计;单歌单同步、移除;点歌单名查看每首歌的入库状态与音质 |
| 本地音乐 | 音乐库全量列表(标题/歌手/专辑/状态),`仅显示缺失` 过滤;每行:**重刮削**(重抓标签/封面/歌词/NFO)、**匹配**(搜索网易云手动指定)、**编辑**(直接改标题/歌手/专辑/音轨) |
| 最近运行 | 每轮摘要(新增/升级/失败/移除)+ 状态 |
| 运行日志 | 实时尾迹(2s 刷新) |
| 设置 | 结构化表单:计划、布局、刮削整理、音质链、歌词/NFO、通知类型与**推送事件**、并发/限速、面板令牌;「高级」折叠区可直接编辑完整 config.yaml |

### 扫码登录

面板内展示二维码(或终端 `python -m wyydl.qrlogin` 打印 ASCII 二维码),手机网易云扫码 → 手机确认 → 凭证自动保存。二维码 5 分钟过期可重新生成;凭证过期后面板登录态变红并按事件通知。

## ⚙️ 配置参考(config.yaml)

```yaml
schedule: "0 4 * * *"          # 运行计划(cron,5 段)
layout: album                  # 下载文件布局:album=按专辑 | artist=按歌手 | flat=歌曲平铺 | playlist=按歌单
                               # (旧值 archive 自动兼容为 album)
naming: ""                     # 文件名模板,可用 {track} {pos} {title} {artist} {album};留空用布局默认
mirror: false                  # 歌单移除歌曲时: false=保留本地文件, true=移入 _trash

quality:
  chain: [jymaster, hires, lossless, exhigh, standard]   # 音质降档链(从高到低)
  upgrade_existing: true       # 歌曲出现更高音质时自动重新下载

lyrics:
  lrc: true                    # 生成同名 .lrc 文件
  embed: true                  # 歌词内嵌到音频标签

nfo: true                      # 生成 NFO 元数据(单曲 nfo + album.nfo/artist.nfo 跟随布局)

notify:
  type: feishu                 # feishu | webhook(通用 JSON {"text": ...})
  url: "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
  secret: ""                   # 飞书机器人开启「签名校验」时填写
  events:                      # 推送事件,面板「推送事件」可逐项勾选
    on_start: false            #   同步开始
    on_complete: false         #   每轮完成(检测完成,无变化也推)
    on_changes: true           #   有新增/升级/移除变更
    on_failed: true            #   有歌曲下载失败
    on_partial: true           #   部分未成功(歌曲已入库但 NFO/封面/歌词/标签某项失败)
    on_login_expired: true     #   登录失效需重新扫码
    on_error: true             #   轮次异常

web:
  enabled: true                # Web 面板开关
  port: 8286                   # 面板端口
  token: ""                    # 访问令牌;设置后面板首次访问需输入(建议局域网不可信时启用)

limits:
  download_concurrency: 3      # 下载并发数(1-8)
  api_delay: [1.0, 3.0]        # 网易云 API 请求随机间隔(秒)

playlists:                     # 同步的歌单(面板管理;id 必填,name 可选自定义显示名)
  - { id: 24381616 }
```

所有配置均可在 Web 面板「设置」中修改;「高级」折叠区可编辑完整 YAML(含 `api_base` 等)。

## 🎵 刮削规则(歌曲信息写入)

**数据源**:全部来自自建 ncm-api(登录态),`/song/detail`(批量)、`/album`(按专辑缓存,每专辑仅请求一次)、`/lyric`。

**标签字段**(FLAC / MP3 / M4A 按标准写法):

| 信息 | FLAC | MP3 | M4A |
|------|------|-----|-----|
| 标题/歌手/专辑/专辑歌手 | TITLE/ARTIST/ALBUM/ALBUMARTIST | TIT2/TPE1/TPE2/TALB | ©nam/©ART/aART/©alb |
| 音轨号/碟号 | TRACKNUMBER/DISCNUMBER | TRCK/TPOS | trkn |
| 年份 | DATE | TDRC | ©day |
| 流派 | GENRE | TCON | ©gen |
| 厂牌 | PUBLISHER | TPUB | — |
| 网易云歌曲 ID | NETEASE_ID | TXXX:NETEASE_SONG_ID | freeform |
| 歌词 | LYRICS 帧 | USLT | ©lyr |
| 封面(1500px) | Picture | APIC | covr |

- MP3/M4A/FLAC 均为**增量更新**:只覆盖本次提供的字段,已有封面/歌词保留;
- 标签写入失败不影响歌曲入库,计入「部分未成功」。

**封面**:专辑封面 `?param=1500y1500`,按专辑 ID 缓存;JPEG/PNG 嗅探校验;请求失败计为部分未成功。

**歌词**:写入同名 `.lrc`(UTF-8)+ 按开关内嵌;接口失败计为部分未成功,歌曲本身无歌词不告警。

**NFO**:单曲 nfo 含标题/歌手/专辑/音轨/年份/流派/时长/`uniqueid netease`;album.nfo 另含厂牌/发行日期/简介/曲目列表。

**失败分级**:整首未入库(无音源/试听/校验失败)→「下载失败」;已入库但次要项目失败 →「部分未成功」;两者分别统计并按通知事件推送。

## 📁 音乐目录产出示例

```
音乐目录/
├── 周杰伦/叶惠美/
│   ├── 01. 以父之名.flac
│   ├── 01. 以父之名.lrc
│   ├── 01. 以父之名.nfo
│   ├── album.nfo                # 专辑元数据(流派/厂牌/简介/曲目)
│   └── cover 除外——封面内嵌在音频里
├── 周杰伦/artist.nfo            # 歌手元数据
├── 我的喜欢的音乐.m3u8           # 每个歌单一个播放列表(相对路径)
└── _trash/                      # mirror 开启时被移除歌曲的暂存
```

四种布局的落位规则见面板「设置 → 下载文件布局」;手动匹配/重新刮削/编辑后的文件**跟随同一布局**自动整理。

## 🔔 通知事件

| 事件 | 触发 | 默认 |
|------|------|------|
| on_start | 每轮同步开始 | 关 |
| on_complete | 每轮完成(检测完成,无变化也推) | 关 |
| on_changes | 有新增/升级/移除变更 | 开 |
| on_failed | 有歌曲下载失败(整首未入库) | 开 |
| on_partial | 部分未成功(已入库但 NFO/封面/歌词/标签某项失败) | 开 |
| on_login_expired | 登录失效需重新扫码 | 开 |
| on_error | 轮次内部异常 | 开 |

飞书机器人示例:群设置 → 机器人 → 自定义机器人 → 获取 webhook 地址(可选开启签名校验,密钥填 `secret`)。面板「测试飞书通知」会先保存当前配置再发送。

## ⏱️ 定时与增量

- 内置 APScheduler 按 cron 调度;面板保存计划即时生效;
- 每轮:登录预检 → 拉取歌单(完整 trackIds)→ 详情与音质权限 → 与状态库 diff → 音质协商 → 批量取流+并发下载(交错执行)→ 打标签/NFO → 重建 m3u8 → 清理 → 按事件通知;
- 已入库且音质无升级的曲目跳过;下载中断的曲目下轮自动重试。

## 🗂️ 数据与文件位置

| 内容 | Docker Compose | fnOS fpk |
|------|----------------|----------|
| 音乐输出 | `/music` 挂载目录 | 共享目录 `wyydl/music` 或安装时自定义目录 |
| 配置/凭证 | `/config`(config.yaml、secret.yaml) | `/vol/@appdata/wyydl/config` |
| 状态库 | `/db/state.sqlite3` | `/vol/@appdata/wyydl/db` |
| 日志 | `/logs/wyydl.log` | `/vol/@appdata/wyydl/logs` |

升级/重装不动数据;卸载前自动 `docker compose down`,数据保留需手动删除。

## 🧰 常见问题(FAQ)

**启动报 `Pulling ncm-api ... registry-1.docker.io` 解析/超时**
Docker Hub 国内不可达,`ncm-api` 镜像拉取失败。配置镜像加速或手动导入:
```bash
sudo tee /etc/docker/daemon.json <<'EOF'
{ "registry-mirrors": ["https://docker.1ms.run", "https://docker.m.daocloud.io"] }
EOF
sudo systemctl restart docker
# 或在可访问 Docker Hub 的机器上: docker save → 传到 NAS → docker load
```

**登录后歌单/取流 301 或 301 报错** — Cookie 失效,重新扫码;避免频繁调用登录接口。

**想同步的歌搜不到/下载失败** — 灰色或下架歌曲无可用音源;确认账号会员状态与该曲音质权限;失败明细在面板「本地音乐/最近运行」与通知中可见。

**端口被占用** — 修改设置中的面板端口(fpk 需同步修改 compose 端口映射)或停用占用程序。

**网络到 GitHub/Docker Hub 不稳定** — fpk 可从 Releases 下载;镜像可配加速;Git 推送可配置代理(`git config --global http.proxy ...`)。

**测试**:`cd sync && python -m tests.smoke`(不依赖网络,mock 全部接口)。

## 🧪 项目结构

```
├── docker-compose.yml          # 通用 Docker 部署
├── fpk/                        # fnOS 应用包(manifest/向导/生命周期脚本/build.sh)
├── sync/
│   ├── Dockerfile              # 同步容器镜像(x86_64/arm64 自适应)
│   ├── tests/smoke.py          # 冒烟测试(72 项,mock 全部外部接口)
│   └── wyydl/
│       ├── main.py             # 入口:守护(Web+调度)/ --once
│       ├── syncer.py           # 同步引擎(增量/音质协商/并发下载/刮削/整理)
│       ├── api.py              # ncm-api 客户端(限速/重试/扫码/搜索)
│       ├── quality.py          # 音质协商
│       ├── downloader.py       # 流式下载 + MD5 校验
│       ├── tagger.py           # mutagen 标签/封面/歌词
│       ├── nfo.py              # NFO 元数据生成
│       ├── state.py            # SQLite 状态库
│       ├── notify.py           # 飞书/Webhook 通知(事件开关)
│       ├── qrlogin.py          # CLI 扫码登录
│       ├── config.py           # 配置与凭证
│       └── web.py + static/    # Web 面板(FastAPI + 单页)
```

## 🏷️ 版本规范

当前版本见 [CHANGELOG.md](CHANGELOG.md):普通修改 +0.0.1,功能更新 +0.1,特别更新 +1.0。发版时同步 `fpk/manifest` 与 `sync/wyydl/__init__.py` 的版本号,推 `v*` 标签自动构建发布。

## 📄 许可证与致谢

[MIT](LICENSE) © 2026 Xiameng

- [NeteaseCloudMusicApiEnhanced/api-enhanced](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced) — 网易云 API 服务
- [mutagen](https://github.com/quodlibet/mutagen) — 音频标签库
- [FastAPI](https://github.com/tiangolo/fastapi) / [APScheduler](https://github.com/agronholm/apscheduler) / [httpx](https://github.com/encode/httpx)
