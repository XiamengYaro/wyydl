FROM python:3.12-slim

# ncmdump-go 无官方 Docker 镜像,构建时从 release 取对应架构二进制打进镜像。
# 主源:git.taurusxin.com(资产名已验证:ncmdump-go_linux_{arch}_{ver}.tar.gz);API 发现作回退。
ARG TARGETARCH
ARG NCMDUMP_VER=1.7.5
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    arch="${TARGETARCH:-amd64}"; \
    ver="${NCMDUMP_VER}"; \
    direct="https://git.taurusxin.com/taurusxin/ncmdump-go/releases/download/v${ver}/ncmdump-go_linux_${arch}_${ver}.tar.gz"; \
    py='import sys,json,urllib.request;req=urllib.request.Request(sys.argv[1],headers={"User-Agent":"wyydl"});assets=json.load(urllib.request.urlopen(req,timeout=30)).get("assets") or [];print(next(a["browser_download_url"] for a in assets if "linux_"+sys.argv[2] in a.get("name","")))'; \
    if ! curl -fsSL "$direct" -o /tmp/ncm.tar.gz; then \
        url=$(python3 -c "$py" "https://git.taurusxin.com/api/v1/repos/taurusxin/ncmdump-go/releases/latest" "$arch" \
           || python3 -c "$py" "https://api.github.com/repos/taurusxin/ncmdump-go/releases/latest" "$arch"); \
        curl -fsSL "$url" -o /tmp/ncm.tar.gz; \
    fi; \
    tar xzf /tmp/ncm.tar.gz -C /usr/local/bin; \
    chmod +x /usr/local/bin/ncmdump-go; \
    rm -f /tmp/ncm.tar.gz; \
    apt-get purge -y curl; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY wyydl/ ./wyydl/

ENV TZ=Asia/Shanghai
EXPOSE 8286
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8286/api/status',timeout=4)" || exit 1

CMD ["python", "-m", "wyydl.main"]
