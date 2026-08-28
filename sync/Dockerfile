FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY wyydl/ ./wyydl/

ENV TZ=Asia/Shanghai
EXPOSE 8286
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8286/api/status',timeout=4)" || exit 1

CMD ["python", "-m", "wyydl.main"]
