name: wyydl

services:
  ncm-api:
    image: moefurina/ncm-api:latest
    container_name: wyydl-ncm-api
    restart: unless-stopped
    environment:
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

  wyydl-sync:
    image: __WYYDL_IMAGE__
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
