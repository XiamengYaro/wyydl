# 更新日志(Changelog)

## 版本规范

自 1.0.0 起,按以下规则递增:

| 变更类型 | 版本变化 | 示例 |
|----------|----------|------|
| 普通修改(bug 修复、文案、小调整) | +0.0.1 | 1.0.0 → 1.0.1 |
| 功能更新(新增功能、体验改进) | +0.1 | 1.0.1 → 1.1.0 |
| 特别更新(大版本重构、重大变化) | +1.0 | 1.x → 2.0.0 |

同步位置:`fpk/manifest` 的 `version` 与 `sync/wyydl/__init__.py` 的 `__version__` 必须一致,并在本文件追加条目、更新 `fpk/manifest` 的 `changelog`。

## 1.1.1 - 2026-08-28

- 修复:安装向导中自定义端口/音乐目录不生效的问题。原实现依赖 compose 的 `.env` 变量替换,但 appcenter 拉起 compose 的方式不受控;现改为生命周期脚本按向导配置直接**渲染最终 docker-compose.yaml**(模板 `docker-compose.yaml.tpl` 兜底默认值),并在 `$TRIM_PKGETC/wyydl.settings` 持久化、`logs/settings.log` 留痕。

## 1.1.0 - 2026-08-28

- 新增桌面/应用中心入口(`app/ui/config`):点击应用卡片即在 fnOS 桌面窗口内打开 Web 面板;入口端口使用 `${wyydl_port}` 模板变量,自动跟随安装向导配置。
- 开发者与发布者均配置为 Xiameng(https://github.com/XiamengYaro )。

## 1.0.1 - 2026-08-28

- 开发者信息:Xiameng(点击跳转 https://github.com/XiamengYaro )。

## 1.0.0 - 2026-08-28

- 首个版本。
- 定时同步网易云歌单:音质协商(jymaster→hires→lossless…逐级降档)、MD5 校验、封面/歌词标签、NFO 元数据(单曲/专辑/歌手)、歌手/专辑归档 + 歌单 m3u8、NCM 兜底转换、增量同步、飞书通知。
- Web 控制面板:扫码登录、账号歌单导入、结构化设置、实时下载进度、日志。
- fnOS 应用包:安装向导支持自定义 Web 面板端口与音乐保存目录,应用中心一键启停/升级。
