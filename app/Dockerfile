# 使用更小的 Alpine 基础镜像
FROM python:3.11-alpine

# 安装 FFmpeg 及必需的运行时库 (使用Alpine的apk包管理器)
RUN apk update && \
    apk add --no-cache --virtual .build-deps gcc musl-dev && \
    apk add --no-cache ffmpeg && \
    apk del .build-deps && \
    rm -rf /var/cache/apk/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p uploads outputs

EXPOSE 5000

CMD ["gunicorn", "-b", "0.0.0.0:5000", "app:app"]
