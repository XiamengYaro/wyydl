# 基础镜像可通过构建参数覆盖(国内网络可换成可达镜像源):
#   docker compose build --build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
# CI(GitHub 托管)使用默认 python:3.12-slim。
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

# 可选 pip 源(国内构建可传镜像源,如 https://pypi.tuna.tsinghua.edu.cn/simple)
ARG PIP_INDEX_URL=
WORKDIR /app
COPY requirements.txt .
RUN if [ -n "$PIP_INDEX_URL" ]; then \
        pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi
COPY wyydl/ ./wyydl/

ENV TZ=Asia/Shanghai
EXPOSE 8286
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8286/api/status',timeout=4)" || exit 1

CMD ["python", "-m", "wyydl.main"]
