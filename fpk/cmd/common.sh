#!/bin/bash
# 公共函数:校验并渲染 docker compose 使用的 .env。
# 取值优先级:向导环境变量 > $TRIM_PKGETC/wyydl.settings(持久化) > 默认值。
# install/upgrade/config 生命周期脚本 source 本文件后调用 wyydl_apply_settings。

wyydl_apply_settings () {
    local persist="${TRIM_PKGETC}/wyydl.settings"
    mkdir -p "${TRIM_PKGETC}" "${TRIM_APPDEST}/docker"

    # 1) 取值
    local port="${wyydl_port:-}" mode="${wyydl_music_mode:-}" dir="${wyydl_music_dir:-}"
    if [ -f "$persist" ]; then
        # shellcheck disable=SC1090
        . "$persist"
        [ -z "$port" ] && port="${WYYDL_PORT:-}"
        [ -z "$mode" ] && mode="${WYYDL_MUSIC_MODE:-}"
        [ -z "$dir" ] && dir="${WYYDL_MUSIC_DIR:-}"
    fi
    port="${port:-8286}"
    mode="${mode:-share}"
    local default_dir="/var/apps/wyydl/shares/wyydl/music"
    [ "$mode" = "share" ] && dir="$default_dir"
    [ -z "$dir" ] && dir="$default_dir"

    # 2) 校验(向导值按字符串处理,使用前必须再校验)
    case "$port" in
        ''|*[!0-9]*)
            [ -n "$TRIM_TEMP_LOGFILE" ] && echo "端口无效(必须为数字):$port" > "$TRIM_TEMP_LOGFILE"
            return 1 ;;
    esac
    if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        [ -n "$TRIM_TEMP_LOGFILE" ] && echo "端口超出范围(1-65535):$port" > "$TRIM_TEMP_LOGFILE"
        return 1
    fi
    case "$dir" in
        /*) ;;
        *)
            [ -n "$TRIM_TEMP_LOGFILE" ] && echo "音乐目录必须是绝对路径:$dir" > "$TRIM_TEMP_LOGFILE"
            return 1 ;;
    esac
    if [ "$mode" = "custom" ] && [ ! -d "$dir" ]; then
        mkdir -p "$dir" 2>/dev/null || {
            [ -n "$TRIM_TEMP_LOGFILE" ] && echo "无法创建音乐目录:$dir" > "$TRIM_TEMP_LOGFILE"
            return 1
        }
    fi

    # 3) 持久化(应用设置可重复打开)并渲染 compose .env
    cat > "$persist" <<EOF
WYYDL_PORT=$port
WYYDL_MUSIC_MODE=$mode
WYYDL_MUSIC_DIR=$dir
EOF
    cat > "${TRIM_APPDEST}/docker/.env" <<EOF
WYYDL_PORT=$port
WYYDL_MUSIC_DIR=$dir
EOF
    return 0
}
