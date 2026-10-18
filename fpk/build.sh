#!/bin/bash
# 构建 fnOS fpk 包:同步应用源码到打包目录后调用 fnpack。
# 前置:安装 fnpack(https://developer.fnnas.com/docs/cli/fnpack/),确保在 PATH 中。
set -euo pipefail
cd "$(dirname "$0")"

# 1) 读取 manifest 版本,渲染镜像引用(默认 GHCR 官方源;安装向导可换成国内镜像源)
VER=$(grep -E '^version' manifest | head -1 | awk -F= '{print $2}' | tr -d ' ')
IMAGE_DEFAULT="ghcr.io/xiamengyaro/wyydl/wyydl-sync:v${VER}"
LOCAL_IMAGE="wyydl-sync:v${VER}"
MODE="${WYYDL_BUILD_MODE:-image}"
case "$MODE" in
    image)
        OUTPUT="wyydl.fpk"
        cp app/docker/docker-compose.image.yaml.tpl app/docker/docker-compose.yaml
        cp app/docker/docker-compose.image.yaml.tpl app/docker/docker-compose.yaml.tpl
        ;;
    local)
        OUTPUT="wyydl-${VER}-local.fpk"
        cp app/docker/docker-compose.local.yaml.tpl app/docker/docker-compose.yaml
        cp app/docker/docker-compose.local.yaml.tpl app/docker/docker-compose.yaml.tpl
        ;;
    *)
        echo "错误:WYYDL_BUILD_MODE 只能是 image 或 local" >&2
        exit 1
        ;;
esac

for f in app/docker/docker-compose.yaml app/docker/docker-compose.yaml.tpl; do
    # 同时兼容首次构建的占位符和已渲染过的旧版本 tag,避免 manifest 升版后
    # fpk 仍引用上一版镜像。
    sed -i.bak -E \
        "s#(__WYYDL_IMAGE__|ghcr\\.(io|nju\\.edu\\.cn)/[^[:space:]]*/wyydl-sync:[^[:space:]]+)#${IMAGE_DEFAULT}#g" \
        "$f"
    sed -i.bak "s|__WYYDL_LOCAL_IMAGE__|${LOCAL_IMAGE}|g" "$f"
    rm -f "$f.bak"
    if ! grep -Fq "image: ${IMAGE_DEFAULT}" "$f" && ! grep -Fq "image: ${LOCAL_IMAGE}" "$f"; then
        echo "错误:$f 未渲染到当前版本镜像" >&2
        exit 1
    fi
done
if [ "$MODE" = "local" ]; then
    echo "构建模式:local;镜像引用:${LOCAL_IMAGE}"
else
    echo "构建模式:image;镜像引用:${IMAGE_DEFAULT}"
fi

# 2) 同步源码到打包目录(排除本地运行数据与缓存)
rm -rf app/docker/sync
cp -R ../sync app/docker/sync
find app/docker/sync -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
rm -rf app/docker/sync/data
find . -name '.DS_Store' -delete 2>/dev/null || true
grep -Fq "__version__ = \"${VER}\"" app/docker/sync/wyydl/__init__.py || {
    echo "错误:打包源码版本与 manifest(${VER})不一致" >&2
    exit 1
}

# 3) 打包
fnpack build --directory .
if [ "$(basename "$OUTPUT")" != "wyydl.fpk" ]; then
    mv -f wyydl.fpk "$OUTPUT"
fi
echo "打包完成:$(pwd)/${OUTPUT}"
