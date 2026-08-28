# wyydl 的 fnOS 应用包(fpk)

把网易云歌单同步打成飞牛 fnOS 应用:在应用中心一键安装/启动/停止/升级,自动创建音乐共享目录,Web 面板走应用端口 8286。

## 包结构

```
fpk/
├── manifest                    # 应用描述(appname/version/service_port=8286 等)
├── ICON.PNG / ICON_256.PNG     # 图标(当前为程序生成的占位图,可替换)
├── config/
│   ├── privilege               # run-as root(升级脚本需操作 docker)
│   └── resource                # docker-project + data-share(wyydl/music)
├── cmd/                        # 生命周期脚本
│   ├── common.sh               # 校验并渲染向导配置到 docker/.env(端口/音乐目录)
│   ├── main                    # start/stop 由 appcenter 自动处理,status 查 wyydl-sync 容器
│   ├── install_init            # 安装前:检查 Docker 已安装
│   ├── install_callback        # 安装后:应用向导配置 + 初始化目录 + 预构建镜像
│   ├── config_callback         # 配置变更后:应用新端口/目录并重建容器
│   ├── upgrade_callback        # 升级后:强制 docker compose build(否则旧镜像不更新)
│   └── uninstall_init          # 卸载前:docker compose down(保留数据)
├── wizard/
│   ├── install                 # 安装向导:Web 面板端口、音乐保存位置(共享目录/自定义)
│   └── config                  # 配置向导:安装后在应用设置中可随时修改这两项
├── app/
│   ├── docker/
│   │   ├── docker-compose.yaml # ncm-api + wyydl-sync 双容器(由 appcenter 托管)
│   │   └── sync/               # 构建时由 build.sh 从仓库 sync/ 复制
│   └── ui/                     # 桌面入口:点击应用卡片直达 Web 面板
│       ├── config              # .url 入口,port=${wyydl_port} 跟随安装向导
│       └── images/             # icon_64.png / icon_256.png
└── build.sh                    # 一键打包
```

## 安装向导(自定义目录与端口)

安装时会弹出「基础配置」步骤,安装后也可从**应用设置 → 配置**随时修改(自动重建容器生效,已下载数据保留):

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| Web 面板端口 | 8286 | 数字,1-65535 |
| 音乐保存位置 | 应用共享目录 `wyydl/music` | 可切换为自定义绝对路径(如 `/vol1/1000/media/music`),目录不存在会自动创建 |

实现方式:向导值作为环境变量(`wyydl_port`/`wyydl_music_mode`/`wyydl_music_dir`)传入生命周期脚本,`cmd/common.sh` 校验后持久化到 `$TRIM_PKGETC/wyydl.settings`,并渲染成 `app/docker/.env` 供 compose 变量替换(`${WYYDL_PORT}` / `${WYYDL_MUSIC_DIR}`)。

## 版本规范

当前版本 **1.0.0**。普通修改 +0.0.1,功能更新 +0.1(→1.1.0),特别更新 +1.0(→2.0.0)。发版时同步修改 `manifest` 的 `version` 与 `sync/wyydl/__init__.py` 的 `__version__`,并更新根目录 `CHANGELOG.md` 与 manifest 的 `changelog` 字段。

## 构建(开发机)

```bash
# 1. 安装 fnpack(https://developer.fnnas.com/docs/cli/fnpack/)
curl -fsSL -o /usr/local/bin/fnpack https://static2.fnnas.com/fnpack/fnpack-1.2.3-linux-amd64  # 按平台选择
chmod +x /usr/local/bin/fnpack

# 2. 打包
cd fpk && ./build.sh
# 产物:wyydl.fpk
```

`build.sh` 会先把 `../sync` 源码复制进 `app/docker/sync/`(排除缓存与本地数据),再调用 `fnpack build`。

## 安装到 fnOS

1. 把 `wyydl.fpk` 上传到 fnOS 设备;
2. 通过应用中心的**手动/本地安装入口**安装,或使用 [appcenter-cli](https://developer.fnnas.com/docs/cli/appcentercli/)(需管理员权限);
3. 安装前确保应用中心已安装 **Docker** 应用(`install_init` 会检查);
4. 安装后启动应用——appcenter 自动执行 `docker compose up`,**首次启动会构建镜像**,需设备可访问 pypi 与 git.taurusxin.com,耗时几分钟属正常。

## 安装后的东西在哪

| 内容 | 位置 |
|------|------|
| Web 控制面板 | `http://<NAS_IP>:8286`(扫码登录、歌单管理、设置、进度) |
| 音乐输出 | 共享目录 `wyydl/music`(文件管理器可见,布局:歌手/专辑 + m3u8 + NFO) |
| 配置/凭证 | `/vol/@appdata/wyydl/config/`(config.yaml、secret.yaml) |
| 状态库/日志 | `/vol/@appdata/wyydl/db`、`/vol/@appdata/wyydl/logs` |

## 升级与卸载

- **升级**:应用中心升级时 `upgrade_callback` 会自动重建镜像,代码改动即生效;用户数据(配置/状态库/音乐)全部保留。
- **停止/启动**:应用中心对应按钮即可(stop 只停容器,数据不动)。
- **卸载**:卸载前自动 `docker compose down` 清理容器;配置与音乐文件保留在上述路径,确认不要后手动删除。

## 备注

- `platform=all`:包内不含预编译二进制,Docker 构建按设备架构自适应(x86_64/arm64 均可)。
- 图标 `ICON*.PNG` 当前是构建脚本生成的占位图(圆角底 + 播放三角),要换官方风格直接覆盖这两个文件。
- 端口 8286 被占用时启动会失败,可改 `app/docker/docker-compose.yaml` 的端口映射并重新打包。
