# Apple Music 接入与下载可行性报告

- 评估对象：WYYDL
- 评估日期：2026-10-22
- 调研范围：Apple 官方 API、MusicKit、Apple 服务条款，以及 GitHub 上仍在维护的 Apple Music 下载项目
- 结论状态：可行性研究，不包含代码改动

> 本报告是技术、产品和工程风险评估，不构成法律意见。Apple Music 内容受版权、订阅协议和技术保护措施约束，最终是否允许使用应由使用者结合所在地法律和 Apple 条款自行判断。

## 1. 执行摘要

Apple Music 的**歌单、曲目、专辑、艺人、封面、歌词元数据和试听片段**可以通过官方 Apple Music API / MusicKit 获取，技术上适合接入 WYYDL。

Apple Music 的**完整曲目文件**没有官方公开的“下载为普通 MP3/FLAC/M4A”接口。官方 API 返回的是资源信息、播放参数和试听预览；MusicKit 提供受 DRM 控制的播放能力。GitHub 上的完整下载器普遍需要以下至少一项：

- Apple Music 订阅和用户登录凭证；
- Music User Token 或浏览器 Cookie；
- Widevine / PlayReady 设备密钥；
- FairPlay 解密 wrapper；
- 修改 Apple 客户端、Frida、Android 模拟器或设备认证服务。

因此：

| 能力 | 技术可行性 | 合规风险 | 建议 |
|---|---:|---:|---|
| Apple Music 歌单元数据同步 | 高 | 中 | 可以做 |
| 曲目/专辑/艺人元数据匹配 | 高 | 中 | 可以做 |
| 下载试听片段 | 高 | 中高 | 不建议作为主打功能 |
| 下载完整 AAC | 技术上高 | 很高 | 不建议内置 |
| 下载 ALAC / 无损 | 技术上中高 | 很高 | 不建议内置 |
| 下载 Dolby Atmos | 技术上中 | 很高 | 不建议内置 |
| 作为外部下载器适配层 | 中 | 很高 | 仅在明确隔离、用户自担责任时评估 |

**推荐结论：**

WYYDL 适合做“Apple Music 歌单导入 + 元数据匹配 + 本地/网易云音源补全”，不适合把 DRM 解密下载作为内置核心能力。

## 2. 官方能力边界

### 2.1 Apple Music API 能做什么

Apple 官方文档将 Apple Music API 定位为访问 Apple Music Catalog 和用户 iCloud Music Library 的接口，可获取：

- 歌曲、专辑、艺人、歌单、音乐视频、电台等资源；
- 用户资料库中的歌单和曲目；
- 封面、时长、ISRC、流派、发行日期、作曲等信息；
- 资源关系和分页结果；
- 歌曲的 `playParams` 和 `previews`；
- 经过用户授权后创建或修改歌单、收藏资源。

用户的私人资料库接口需要 **Music User Token**。公共 Catalog 资源可以使用开发者 Token 访问。

### 2.2 官方 API 不提供什么

官方文档中的资源示例包含：

- `playParams`：给 MusicKit 播放器使用的播放参数；
- `previews`：通常是短试听片段的 URL；
- `url`：Apple Music 页面地址。

官方接口没有提供普通音频文件下载地址，也没有提供已解密文件下载能力。`playParams` 不能当作静态 MP3/FLAC 链接使用。

### 2.3 官方播放与下载的区别

Apple Music 会员可以在 Apple 客户端中“下载”歌曲用于离线播放，但这些文件：

- 仍受 DRM 管理；
- 与账号和授权状态相关；
- 通常不能直接作为普通文件继续使用；
- 订阅失效后可能无法访问；
- 不能通过官方 API 导出为 Jellyfin/Navidrome 可扫描的文件。

这是“离线缓存”与“可移植音乐文件”的本质区别。

## 3. GitHub 开源项目调研

### 3.1 项目对比

| 项目 | 主要能力 | 技术路线 | 许可证 | 最近提交 | 评估 |
|---|---|---|---|---|---|
| [gamdl](https://github.com/glomatico/gamdl) | AAC、音乐视频、歌词、元数据 | Python、Widevine/PlayReady、可选 wrapper | MIT | 2026-09-22 | 工程完成度较高，但仍是 DRM 下载器 |
| [zhaarey/apple-music-downloader](https://github.com/zhaarey/apple-music-downloader) | ALAC、AAC、Atmos、MV | Go、wrapper-lite | 仓库未见 LICENSE 文件 | 2026-09-28 | 能力强，但不能直接视为可复用开源代码 |
| [AppleMusicDecrypt](https://github.com/WorldObservationLog/AppleMusicDecrypt) | ALAC、EC3、AAC、MV、TUI | Python、wrapper/lite、Temari | AGPL-3.0 | 2026-10-17 | 功能完整，AGPL 与 DRM 依赖较重 |
| [Manzana](https://github.com/dropcreations/Manzana-Apple-Music-Downloader) | AAC、MV、歌词 | Python、Widevine、Bento4 | GPL-3.0 | 2025-09-06 | AAC 方案明确，但不支持 ALAC/Atmos |
| [alacarte](https://github.com/norifm/alacarte) | ALAC→FLAC、Web UI、播放列表 | Docker、wrapper、外部下载器 | AGPL-3.0 | 2026-10-22 | 现有产品形态最接近 WYYDL，但仅支持 amd64 |

### 3.2 gamdl

优点：

- MIT 许可证，代码复用边界相对清晰；
- 支持歌曲、专辑、歌单、艺人、MV；
- 支持 AAC、歌词、元数据和模板化目录；
- 可配置 Widevine/PlayReady；
- 支持通过外部 wrapper 处理部分 ALAC/FairPlay 场景；
- 当前仍在维护。

限制：

- 需要有效 Apple Music 订阅；
- 需要浏览器 Cookie 或 wrapper 账号；
- 默认依赖 Widevine 设备或外部 wrapper；
- ALAC 往往需要 wrapper，且官方说明中明确存在 API 限制；
- Apple 服务端或 CDM 变化会导致项目快速失效；
- 代码使用许可不等于内容使用许可。

### 3.3 zhaarey/apple-music-downloader

优点：

- 支持 ALAC、AAC、Atmos 和音乐视频；
- 提供多平台预编译二进制；
- 对目录、歌词、封面和标签支持较完整；
- 有 wrapper-lite 架构参考价值。

限制：

- 必须在本地或局域网运行 wrapper-lite；
- 需要 Apple 订阅和媒体用户 Token；
- 仓库当前没有明确 LICENSE 文件，不能按 MIT/GPL 那样直接复用；
- 依赖非官方解密服务；
- 上游变化时维护成本高。

### 3.4 AppleMusicDecrypt

优点：

- 活跃维护，2026-10 仍在更新；
- 支持 ALAC、EC3、AAC 和 MV；
- 流式下载、本地解密、断点续传；
- 有批量任务、失败重试和进度界面；
- 工程上比简单脚本成熟。

限制：

- AGPL-3.0；
- 必须连接 wrapper/lite；
- 依赖 Temari、Widevine/PlayReady、Apple 鉴权和 DRM 解密；
- 将代码嵌入 WYYDL 或作为网络服务提供可能触发 AGPL 义务；
- 仍需要 Apple 账号、订阅和授权。

### 3.5 Manzana

优点：

- 实现路径相对清晰：Widevine + Bento4；
- 支持 AAC、MV、歌词和元数据；
- GPL-3.0 许可明确。

限制：

- 只支持 AAC，不支持 ALAC 和 Atmos；
- 需要 Widevine 设备文件；
- 需要 `mediaUserToken`；
- 还需要 MP4Box、mp4decrypt、ccextractor；
- 最近维护活跃度明显低于其他项目。

### 3.6 alacarte

它最接近 WYYDL 的形态：

- Docker Compose；
- Web UI；
- Apple Music 搜索；
- 专辑、歌单、艺人下载；
- ALAC 转 FLAC；
- 封面、歌词、标签；
- m3u8、Jellyfin/Navidrome 集成；
- 任务队列和状态面板。

但它的限制也很明确：

- 依赖 FairPlay wrapper；
- 官方 README 说明 wrapper 和上游下载器为 `linux/amd64`，原生 ARM Linux 需 QEMU；
- 下载速度受 Apple 限速影响；
- 大规模抓取可能触发数小时到数天的 IP 软限制；
- AGPL-3.0；
- 它会与 WYYDL 在功能、账号、任务队列和 UI 上高度重叠。

## 4. 适合 WYYDL 的接入方案

### 方案 A：只做 Apple Music 歌单元数据导入

流程：

1. 用户提供公共歌单 URL，或通过 Music User Token 授权读取私人资料库；
2. 使用 Apple Music API 拉取歌单、曲目、专辑、艺人、ISRC、时长和封面；
3. 将 Apple Music 曲目与网易云搜索结果、本地音乐库进行匹配；
4. 已有本地文件直接写入 m3u8/NFO；
5. 未匹配曲目进入待确认或失败队列。

优点：

- 技术上最稳；
- 不依赖 DRM 解密；
- 不扩大当前的版权绕过范围；
- 对现有 WYYDL 数据模型改动相对小；
- 后续可扩展到 Spotify、QQ 音乐等其他元数据源。

缺点：

- 不能直接得到 Apple Music 音频；
- 需要解决匹配准确率；
- 私人歌单需要用户 Token；
- 只能处理“用户已在别处拥有对应音频”的场景。

可行性评级：**高，建议优先。**

### 方案 B：Apple Music 元数据 + 30 秒试听

流程：

- 同步 Apple Music 歌单元数据；
- 下载 `previews` 中的短试听片段；
- 生成试听播放列表。

优点：

- 官方 API 直接支持；
- 工程简单；
- 可做 Apple Music 歌单预览或缺失歌曲识别。

缺点：

- 试听片段不能替代完整音乐库；
- 大量下载试听仍可能触发条款和频率问题；
- 对 Jellyfin/Navidrome 的长期归档价值有限。

可行性评级：**技术上高，产品价值中，建议只作为辅助能力。**

### 方案 C：内置完整 Apple Music 下载

需要以下组合：

- Apple Developer Token；
- 用户登录和 Music User Token / Cookie；
- 订阅校验；
- Widevine/PlayReady 或 FairPlay wrapper；
- m3u8/fMP4 解析；
- 解密、重封装；
- ALAC/AAC 标签与封面写入；
- 反限速、重试和异常恢复；
- DRM 依赖持续跟进。

这不是“接入一个 API”，而是在 WYYDL 内维护一套 DRM 下载基础设施。

主要问题：

- 高合规风险；
- 高维护成本；
- 需要分发或引导用户获取 DRM 设备密钥；
- ARM 飞牛兼容性不稳定；
- Apple 侧接口变化会造成整条链路失效；
- 与“个人备份工具”的产品定位冲突。

可行性评级：**技术可行，不建议内置。**

### 方案 D：外部下载器适配层

如果必须保留完整下载能力，建议把 Apple Music 下载器作为**用户自行安装的外部服务**：

```text
WYYDL
  ├─ Apple Music 元数据同步
  ├─ 本地音乐库匹配
  └─ 可选外部适配器
       └─ 用户自行运行的 gamdl / AppleMusicDecrypt / alacarte
```

边界要求：

- WYYDL 不打包 DRM 设备文件；
- WYYDL 不保存 Apple 密码；
- WYYDL 不代理或分发解密密钥；
- 外部服务由用户自己部署和承担合规责任；
- 适配器默认关闭；
- 不把外部下载器作为 WYYDL 的主要卖点；
- 需要明确提示订阅、条款、版权和账号风险。

如果 WYYDL 只通过普通命令行或 HTTP 调用独立进程，法律和许可证风险仍需逐项评估。AGPL 项目不应直接复制代码进 WYYDL，除非项目整体接受相应开源义务。

## 5. 推荐的产品路线

### 第一阶段：元数据优先

- 支持 Apple Music 公共歌单 URL；
- 支持 MusicKit 用户授权后读取私人歌单；
- 获取曲目元数据、ISRC、封面和歌词；
- 与网易云曲库进行匹配；
- 生成“已存在 / 可匹配 / 需人工确认 / 无音源”四态报告。

### 第二阶段：本地库匹配

- 基于标题、艺人、专辑、时长和 ISRC 的综合匹配；
- 允许手动指定本地文件；
- 复用现有 NFO、M3U8、标签和刮削能力；
- 生成类似网易云歌单的 Apple Music 镜像歌单。

### 第三阶段：可选外部下载器

只有在用户明确要求并且接受风险后，才考虑：

- 外部命令白名单；
- 独立容器；
- 手动导入凭证；
- 明确的许可和免责声明；
- 默认关闭；
- 不随 WYYDL 镜像分发 DRM 工具和密钥。

## 6. 技术依赖与账号要求

### 官方元数据路线

- Apple Developer Program 账号；
- MusicKit Identifier 和私钥；
- 生成的 Developer Token；
- 私人资料库额外需要 Music User Token；
- 对应地区的 Storefront；
- 网络可访问 `api.music.apple.com`。

### 完整下载路线

在官方基础上还可能增加：

- Apple Music 订阅；
- 浏览器 Cookie 或 `media-user-token`；
- `.wvd` / `.prd` 设备文件；
- wrapper/lite 服务；
- FFmpeg、MP4Box、Bento4；
- AMD64 容器或 QEMU；
- 足够的临时磁盘空间；
- 稳定的 Apple CDN 网络。

## 7. 风险矩阵

| 风险 | 影响 | 概率 | 缓解方式 |
|---|---|---|---|
| Apple 条款限制 | 账号受限、功能被下架 | 高 | 不做内置 DRM 下载 |
| DRM 接口变更 | 下载全面失效 | 高 | 仅元数据路线；外部适配器 |
| AGPL/GPL 许可冲突 | 发布义务、代码隔离问题 | 中高 | 不复制代码；独立进程仍需评估 |
| ARM 飞牛不兼容 | 无法安装或性能差 | 中高 | 优先纯 Python 元数据实现 |
| IP 限速 | 大批量同步失败 | 中高 | 分页、缓存、退避、分时执行 |
| 凭证泄露 | Apple 账号风险 | 中高 | Token 加密存储、日志脱敏、只读权限 |
| 匹配错误 | 下载错误歌曲 | 中 | ISRC + 时长 + 人工确认 |
| 存储压力 | NAS 空间不足 | 中 | 下载前预检、按需同步 |
| 上游项目停更 | 维护中断 | 高 | 不把外部项目作为核心依赖 |

## 8. 结论与决策建议

### 建议立项

1. **Apple Music 歌单导入**
2. **Apple Music 元数据与封面读取**
3. **Apple Music 曲目匹配网易云/本地音乐**
4. **Apple Music 镜像歌单与 m3u8 生成**
5. **试听片段可选下载，默认关闭**

### 不建议直接立项

1. 在 WYYDL 主镜像中集成 Widevine/FairPlay 解密；
2. 分发 DRM 设备文件；
3. 把完整 Apple Music 下载作为默认功能宣传；
4. 直接复制 AGPL/GPL 项目代码进 WYYDL；
5. 在没有明确合规边界前把 Apple 密码交给 NAS 服务。

### 最终判断

Apple Music 接入对 WYYDL **有产品价值**，但价值最高的不是“再增加一个下载源”，而是：

> 将 Apple Music 作为高质量元数据源，与用户已有本地音乐和网易云音源进行匹配，形成跨平台歌单索引。

完整下载器可以作为用户自行部署的外部能力研究，但不应成为 WYYDL 的内置核心功能。

## 9. 参考资料

Apple 官方：

- [Apple Music API](https://developer.apple.com/documentation/applemusicapi)
- [MusicKit](https://developer.apple.com/documentation/musickit)
- [Get a Library Playlist](https://developer.apple.com/documentation/applemusicapi/get-a-library-playlist)
- [Get a Catalog Song](https://developer.apple.com/documentation/applemusicapi/get-a-catalog-song)
- [Apple Media Services Terms and Conditions](https://www.apple.com/legal/internet-services/itunes/us/terms.html)

GitHub 项目：

- [glomatico/gamdl](https://github.com/glomatico/gamdl)
- [zhaarey/apple-music-downloader](https://github.com/zhaarey/apple-music-downloader)
- [WorldObservationLog/AppleMusicDecrypt](https://github.com/WorldObservationLog/AppleMusicDecrypt)
- [dropcreations/Manzana-Apple-Music-Downloader](https://github.com/dropcreations/Manzana-Apple-Music-Downloader)
- [norifm/alacarte](https://github.com/norifm/alacarte)
- [WorldObservationLog/wrapper](https://github.com/WorldObservationLog/wrapper)

项目状态与许可证按 2026-10-22 可见仓库内容核对。上游 README、许可证和维护状态可能继续变化，正式立项前应再次审查。
