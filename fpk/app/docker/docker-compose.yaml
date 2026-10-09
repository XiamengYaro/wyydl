name: wyydl

services:
  ncm-api:
    image: moefurina/ncm-api:latest
    container_name: wyydl-ncm-api
    restart: unless-stopped
    environment:
      # 容器内 request 库会读这些代理变量,必须显式清空,否则全部请求失败(上游 README 要求)
      http_proxy: ""
      https_proxy: ""
      HTTP_PROXY: ""
      HTTPS_PROXY: ""
      no_proxy: ""
      NO_PROXY: ""
      ENABLE_FLAC: "true"
      SELECT_MAX_BR: "true"
      ENABLE_GENERAL_UNBLOCK: "true"
    expose:
      - "3000"
    networks: [wyydl]

  # 默认使用 GHCR 预构建镜像,安装时无需本地构建(避开 python 基础镜像拉取问题)。
  # 若你的网络访问 ghcr.io 不通,可在安装向导"容器镜像源"里换成国内镜像源,例如:
  #   ghcr.nju.edu.cn/xiamengyaro/wyydl/wyydl-sync:vX.Y.Z
  # 想改为全新本地构建(不依赖 ghcr):注释 image 行,取消 build 行注释,再执行
  #   docker compose build --build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
  wyydl-sync:
    image: ghcr.io/xiamengyaro/wyydl/wyydl-sync:v2.1.1
    # build: ./sync
    container_name: wyydl-sync
    restart: unless-stopped
    depends_on: [ncm-api]
    environment:
      - TZ=Asia/Shanghai
      - NCM_API=http://ncm-api:3000
    ports:
      - "${wyydl_port:-8286}:8286"
    volumes:
      - ${wyydl_music_dir:-/var/apps/wyydl/shares/wyydl/music}:/music
      - /var/apps/wyydl/var/config:/config
      - /var/apps/wyydl/var/db:/db
      - /var/apps/wyydl/var/logs:/logs
    networks: [wyydl]

networks:
  wyydl:
