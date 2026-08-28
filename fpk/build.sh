#!/bin/bash
# 构建 fnOS fpk 包:同步应用源码到打包目录后调用 fnpack。
# 前置:安装 fnpack(https://developer.fnnas.com/docs/cli/fnpack/),确保在 PATH 中。
set -euo pipefail
cd "$(dirname "$0")"

# 1) 同步源码到打包目录(排除本地运行数据与缓存)
rm -rf app/docker/sync
cp -R ../sync app/docker/sync
find app/docker/sync -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
rm -rf app/docker/sync/data
find . -name '.DS_Store' -delete 2>/dev/null || true

# 2) 打包
fnpack build --directory .
