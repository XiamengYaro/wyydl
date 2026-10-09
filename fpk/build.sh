#!/bin/bash
# 构建 fnOS fpk 包:同步应用源码到打包目录后调用 fnpack。
# 前置:安装 fnpack(https://developer.fnnas.com/docs/cli/fnpack/),确保在 PATH 中。
set -euo pipefail
cd "$(dirname "$0")"

# 1) 读取 manifest 版本,渲染镜像引用(默认 GHCR 官方源;安装向导可换成国内镜像源)
VER=$(grep -E '^version' manifest | head -1 | awk -F= '{print $2}' | tr -d ' ')
IMAGE_DEFAULT="ghcr.io/xiamengyaro/wyydl/wyydl-sync:v${VER}"
for f in app/docker/docker-compose.yaml app/docker/docker-compose.yaml.tpl; do
    sed -i.bak "s|__WYYDL_IMAGE__|${IMAGE_DEFAULT}|g" "$f" && rm -f "$f.bak"
done
echo "镜像引用已渲染:${IMAGE_DEFAULT}"

# 2) 同步源码到打包目录(排除本地运行数据与缓存)
rm -rf app/docker/sync
cp -R ../sync app/docker/sync
find app/docker/sync -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
rm -rf app/docker/sync/data
find . -name '.DS_Store' -delete 2>/dev/null || true

# 3) 打包
fnpack build --directory .
