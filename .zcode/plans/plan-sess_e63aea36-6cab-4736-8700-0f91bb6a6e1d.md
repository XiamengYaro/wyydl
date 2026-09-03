## 多平台打通计划:QQ 音乐 + 哔哩哔哩(登录/歌单/下载/刮削)

**可行性结论**:可以。B 站走 **yt-dlp**(原生支持 B 站视频/音频/收藏夹/合集/UP主/搜索,扫码登录走 B 站 passport 二维码 API);QQ 音乐走 **QQMusicApi 容器**(jsososo/QQMusicApi,Express 服务,自带 Dockerfile,支持 Cookie/扫码登录、歌单、vkey 取流、歌词、搜索)。两者均以**扫码优先、Cookie 兜底**。

单版本交付 **1.12.0**(SemVer 次版本:向后兼容的新功能),内含三个阶段。

---

### 阶段 0:平台抽象重构(前置,行为不变)

当前代码为网易云硬编码(已核实:NcmApi 被 main/web/qrlogin/syncer 四处引用;音质档位/privilege/al-ar 字段/QR 登录端点/sid 全局主键均为网易专属;songs 表无 platform 列)。

1. 新增 `providers/base.py`:Provider 接口(登录状态/扫码流/列举目标/拉曲目/歌曲详情/取流/歌词/专辑信息/搜索),统一中性 TrackMeta(标题/歌手/专辑/封面/时长/可用音质档/扩展字段)。
2. `providers/netease.py`:现有 NcmApi 逻辑包装为 NetEaseProvider(ncm-api 容器不变)。
3. `quality.py`:档位表按平台提供(网易 10 档不变;QQ:128/320/flac/hires;B 站:bestaudio 单档)。
4. **sid 分段避免跨平台冲突**(不重建表):网易 sid 原样;QQ = 1e12 + qmid;B 站 = 2e12 + aid;本地文件负数 sid 不变。`songs` 表加 `platform` 列(默认 netease,迁移复用现有 ALTER 模式)。
5. `tagger.py`/`nfo.py`:ID 字段通用化(按平台写 `<uniqueid type="netease|qq|bilibili">`,MP3 TXXX 改 `SOURCE_SONG_ID` 并兼容旧值);`downloader` 的 Referer/UA 由 Provider 提供。
6. `web.py` **修复既有 bug:重复路由 `remove_playlist`(web.py:178 与 227 同名覆盖,特殊源删除分支实际失效)**。
7. 登录凭证改为按平台存 secret.yaml:`music_u`(现有)、`bili`、`qq`。验收:smoke 全绿、现有行为回归不变。

### 阶段 1:哔哩哔哩 Provider(yt-dlp + 扫码)

1. Dockerfile 增装 `ffmpeg` 与 `yt-dlp`。
2. `providers/bilibili.py`:
   - **扫码登录**:B 站 passport 二维码 API(web/qrcode/generate + poll),面板展示二维码轮询,成功后保存 SESSDATA 等 Cookie;登录校验用 nav 接口。
   - **来源类型**:收藏夹(fid/链接)、合集、UP 主空间、单个 BV/av 链接——`yt-dlp --flat-playlist -J` 列举(带 Cookie)。
   - **下载**:`yt-dlp -f bestaudio`(带 Cookie)输出到 tmp,后续走统一打标/NFO/布局管线。
   - 元数据:标题/UP 主(→歌手)/封面/时长/B 站 ID;流派厂牌天然缺失留空(不计部分未成功告警)。
3. 面板:添加来源支持粘贴 B 站链接/收藏夹;登录区 B 站二维码;歌单行平台徽标。

### 阶段 2:QQ 音乐 Provider(QQMusicApi 容器)

1. Compose/fpk 新增 `qq-music-api` 容器(jsososo/QQMusicApi 构建),CI 同步发布该镜像到 GHCR。
2. `providers/qq.py`:
   - **扫码登录**(QQMusicApi QR 端点,实施时验证;不可用降级 Cookie 粘贴),Cookie 存 secret.yaml `qq`。
   - 用户歌单列表/曲目/取流(vkey;128/320/flac/hires 按绿钻权限映射统一档位)/歌词/搜索/封面。
3. 面板:QQ 登录状态、扫码/Cookie 双入口。

### 统一能力(对三个平台全部生效)

四种下载布局、m3u8(按平台+来源命名)、通知事件、失败原因/重复标记、部分未成功、本地列表刮削(匹配源=当前文件平台;跨平台用聚合搜索)——QQ/B 站缺失流派厂牌时 NFO 字段留空(平台信息天然缺失,不告警)。同曲多平台各存一份(按 平台+sid)。

### 验证与发布

- smoke:网易全量回归不变 + Provider 映射、sid 分段、B 站扫码/列举 mock、QQ 取流 mock、路由修复;预计 84+ 项。
- 版本 **1.12.0**(SemVer 次版本);CHANGELOG/README(多平台说明、登录方式与会员音质限制);fpk 与 Compose 增 qq-music-api 容器;推 `v1.12.0` → CI 构建 fpk + 双镜像(多架构)发布。

**风险**:QQ 第三方 API 活跃度一般、风控较严(扫码可能降级 Cookie);B 站接口变动频繁(yt-dlp 社区跟进快);高音质依赖会员(QQ 绿钻/B 站大会员);同曲多平台各存一份占空间。