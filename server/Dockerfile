# NAS 音乐盒 服务端镜像（amd64）
# 国内网络可通过构建参数换源（在 docker-compose.yml 的 build.args 里填）：
#   BASE_IMAGE   基础镜像（Docker Hub 拉不动时换成镜像站地址）
#   APT_MIRROR   Debian 软件源主机名，如 mirrors.ustc.edu.cn
#   PIP_INDEX_URL pip 源，如 https://pypi.tuna.tsinghua.edu.cn/simple
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ARG APT_MIRROR=""
ARG PIP_INDEX_URL=""

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    NASBOX_MUSIC_DIR=/music \
    NASBOX_DATA_DIR=/data \
    NASBOX_PORT=8090

RUN set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
      sed -i "s@deb.debian.org@${APT_MIRROR}@g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
      sed -i "s@deb.debian.org@${APT_MIRROR}@g" /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends ffmpeg tzdata; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN if [ -n "$PIP_INDEX_URL" ]; then pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt; \
    else pip install --no-cache-dir -r requirements.txt; fi

COPY nasbox ./nasbox
# 允许以任意 UID 运行（compose 里的 user: PUID:PGID）
RUN chmod -R a+rX /app && mkdir -p /data /music && chmod 777 /data

VOLUME ["/data"]
EXPOSE 8090
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,json;d=json.load(urllib.request.urlopen('http://127.0.0.1:%s/api/status'%os.environ.get('NASBOX_PORT','8090'),timeout=4));assert d['app']=='nas-music-box'"
CMD ["python", "-m", "nasbox"]
