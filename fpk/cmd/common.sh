#!/bin/bash
# 公共函数:校验向导配置并渲染最终 docker-compose.yaml。
# 取值优先级:向导环境变量 > $TRIM_PKGETC/wyydl.settings(持久化) > 默认值。
# 渲染而非 .env 替换:appcenter 拉起 compose 的方式不受控,必须让 compose 文件本身携带最终值。

WYYDL_TPL="${TRIM_APPDEST}/docker/docker-compose.yaml.tpl"
WYYDL_COMPOSE="${TRIM_APPDEST}/docker/docker-compose.yaml"
WYYDL_DEFAULT_DIR="/var/apps/wyydl/shares/wyydl/music"

wyydl_fail () {
    [ -n "$TRIM_TEMP_LOGFILE" ] && printf '%s\n' "$1" > "$TRIM_TEMP_LOGFILE"
    echo "$1"
    return 1
}

# $1=端口 $2=音乐目录 → 由模板渲染出最终 compose(幂等)。
# 模板中的 ${wyydl_port:-8286} / ${wyydl_music_dir:-默认目录} 与向导字段同名,
# appcenter 若支持向导变量插值则安装时即正确;脚本渲染作为确定性兜底。
wyydl_render_compose () {
    local port="$1" dir="$2" image="$3"
    [ -f "$WYYDL_TPL" ] || return 0  # 无模板时使用包内默认 compose
    if [ -n "$image" ]; then
        # 仅替换含 wyydl-sync 的镜像行,避免误伤 ncm-api
        awk -v img="$image" '
            /^[[:space:]]*image:[[:space:]]/ && /wyydl-sync/ { sub(/image:[[:space:]]*.*/, "image: " img) }
            { print }
        ' "$WYYDL_TPL" | sed -e "s|\${wyydl_port:-8286}|$port|" \
            -e "s|\${wyydl_music_dir:-$WYYDL_DEFAULT_DIR}|$dir|" > "$WYYDL_COMPOSE"
    else
        sed -e "s|\${wyydl_port:-8286}|$port|" \
            -e "s|\${wyydl_music_dir:-$WYYDL_DEFAULT_DIR}|$dir|" \
            "$WYYDL_TPL" > "$WYYDL_COMPOSE"
    fi
}

wyydl_apply_settings () {
    mkdir -p "${TRIM_PKGETC}" "${TRIM_APPDEST}/docker" "${TRIM_PKGVAR}/logs"

    # 1) 取值
    local port="${wyydl_port:-}" mode="${wyydl_music_mode:-}" dir="${wyydl_music_dir:-}"
    local img_choice="${wyydl_image:-}"
    local persist="${TRIM_PKGETC}/wyydl.settings"
    if [ -f "$persist" ]; then
        # shellcheck disable=SC1090
        . "$persist"
        [ -z "$port" ] && port="${WYYDL_PORT:-}"
        [ -z "$mode" ] && mode="${WYYDL_MUSIC_MODE:-}"
        [ -z "$dir" ] && dir="${WYYDL_MUSIC_DIR:-}"
        [ -z "$img_choice" ] && img_choice="${WYYDL_IMAGE_SOURCE:-}"
    fi
    port="${port:-8286}"
    mode="${mode:-share}"
    img_choice="${img_choice:-official}"
    [ "$mode" = "share" ] && dir="$WYYDL_DEFAULT_DIR"
    [ -z "$dir" ] && dir="$WYYDL_DEFAULT_DIR"

    # 2) 校验(向导值按字符串处理,使用前必须再校验)
    case "$port" in
        ''|*[!0-9]*) wyydl_fail "端口无效(必须为数字):$port"; return 1 ;;
    esac
    if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        wyydl_fail "端口超出范围(1-65535):$port"
        return 1
    fi
    case "$dir" in
        /*) ;;
        *) wyydl_fail "音乐目录必须是绝对路径:$dir"; return 1 ;;
    esac
    if [ "$mode" = "custom" ] && [ ! -d "$dir" ]; then
        mkdir -p "$dir" 2>/dev/null || { wyydl_fail "无法创建音乐目录:$dir"; return 1; }
    fi

    # 3) 持久化 + 渲染最终 compose + 留痕
    cat > "$persist" <<EOF
WYYDL_PORT=$port
WYYDL_MUSIC_MODE=$mode
WYYDL_MUSIC_DIR=$dir
WYYDL_IMAGE_SOURCE=$img_choice
EOF
    # 镜像源:official=包内默认(ghcr.io),mirror=国内镜像源
    local image=""
    if [ "$img_choice" = "mirror" ]; then
        local base
        base=$(grep -E '^[[:space:]]*image:[[:space:]].*wyydl-sync' "$WYYDL_TPL" | head -1 | awk '{print $2}')
        image=$(printf '%s' "$base" | sed 's|^ghcr\.io/|ghcr.nju.edu.cn/|')
    fi
    wyydl_render_compose "$port" "$dir" "$image" || return 1
    echo "$(date '+%F %T') port=$port music_mode=$mode music_dir=$dir image_source=$img_choice image=${image:-default}" >> "${TRIM_PKGVAR}/logs/settings.log" 2>/dev/null || true
    return 0
}
