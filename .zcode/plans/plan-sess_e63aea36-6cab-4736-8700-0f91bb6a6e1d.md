## wyydl 1.10.0 改造计划(四方向全选)

版本按规范升 **1.9.1 → 1.10.0**(功能更新),同步 `fpk/manifest`、`sync/wyydl/__init__.py`、CHANGELOG,重建 fpk,推 `v1.10.0` 触发 CI。

### A. 工程健壮性
1. **`.dockerignore`**(新增 `sync/.dockerignore`):排除 `data/`、`tests/`、`__pycache__/`、`*.pyc`、`*.log`,缩小 build context。
2. **锁定依赖**:查当前 venv `pip freeze` 得到实测版本,将 `requirements.txt` 改为 `==` 固定(7 个依赖)。
3. **CI 自动发布 Docker 镜像到 GHCR**:
   - `release.yml` 增加 buildx 步骤(登录 `ghcr.io` 用 GITHUB_TOKEN,`docker/setup-buildx-action` + `docker/build-push-action`),多架构 `linux/amd64,linux/arm64`,标签 `v*` 与 `latest`;镜像 `ghcr.io/xiamengyaro/wyydl-sync`。
   - 通用 `docker-compose.yml`:wyydl-sync 增加 `image: ghcr.io/xiamengyaro/wyydl-sync:latest` 并保留 `build: ./sync`(注释说明二选一,pull 模式免本地构建)。
   - fpk 保持本地 build(内含源码上下文,不依赖镜像发布)。
4. **单轮下载上限**:`config.limits.max_per_run`(默认 0=不限);`_run` 规划任务时按上限截断,未处理部分下轮继续(增量天然续跑)。
5. **失败退避**:`songs` 表加 `fail_count` 列(沿用现有 ALTER TABLE 迁移模式);每次整首失败 +1、成功清零;`fail_count>=3` 且距上次尝试 <24h 的曲目本轮跳过(下轮再试),避免对同一批坏歌反复请求。

### B. 面板体验
6. **本地音乐搜索/筛选**:`renderLocal()` 加搜索框,按 标题/歌手/专辑/文件名 包含过滤(前端过滤,数据已全量);保留“仅显示缺失”开关。
7. **刮削后 m3u8 即时刷新**:`_scrape`(match/refetch/edit 共用)成功后调用 `_export_m3u8()`(幂等)。
8. **`_trash` 自动清理**:新增 `config.trash_retention_days`(默认 30);每轮同步时清理 `_trash` 下超过保留期的文件,并同步 DB 置 `removed` 状态不变。
9. **磁盘空间提示**:
   - 下载前 `shutil.disk_usage(music_root)`;剩余 < `config.limits.min_free_space`(GB,默认 2)时跳过该轮下载并记入通知;
   - `/api/status` 增加 `disk: {free_gb, total_gb, free_pct}`,面板歌单卡片上方显示可用空间。
10. **面板新版本检测**:`web.py` 新增 `GET /api/upgrade`(调 GitHub latest release API,失败静默);前端顶栏在有新版本时显示“新版本 vX 可用”链接(指向 Releases)。

### C. 数据来源扩展(特殊“歌单”源)
11. `api.py` 新增:`user_cloud()`(/user/cloud 分页)、`recommend_songs()`(/recommend/songs)、`personal_fm()`(/personal_fm)。
12. `config.playlists` 支持条目 `{"source": "cloud" | "daily" | "fm"}`(id 可省略);面板“添加歌单”旁加「添加特殊源」下拉(云盘/每日推荐/私人 FM)。
13. `syncer._run` 按条目的 `source` 字段分派拉取(云盘需再经 song_detail 补详情),曲目 id 汇入同一去重下载流程;特殊源同样生成 m3u8(名称如“云盘/每日推荐/私人FM”),不参与 mirror 删除。
14. **逐字歌词**:`config.lyrics.yrc`(默认 false);开启时用 `/lyric/new` 的 `yrc` 字段做 yrc→LRC 简单转换(按行时间戳+全文),替代普通 `/lyric`,失败回退普通歌词。
15. **代理下载**:`config.limits.proxy`(可选);`downloader.download` 透传 `proxy=` 给 httpx(仅 CDN 下载请求使用,API 请求仍直连)。

### D. 清理
16. 保留 `song_url_batch`(仍有单元素调用方,不做激进删除);仅在代码注释标注批量能力已不再使用。

### 验证与发布
- smoke 新增用例:特殊源分派、失败退避(fail_count 迁移/跳过逻辑)、max_per_run 截断、yrc 转换、代理透传(仅参数)。
- 全量跑 `compileall` + `tests.smoke`(预计 72+ 项);重建 `wyydl.fpk`;提交、rebase 远端网页提交后推送 main、打 `v1.10.0` 标签,确认 CI 构建成功、Release 挂 fpk、GHCR 镜像推送成功。
- README 更新:新配置项(limits/trash/yrc/proxy)、特殊源说明、Compose 镜像二选一。

风险提示:GHCR 多架构推送与 fpk 本地构建互不影响;DB 迁移(新增列)对既有部署自动生效;特殊源依赖网易接口稳定性,失败自动降级为普通歌单逻辑。