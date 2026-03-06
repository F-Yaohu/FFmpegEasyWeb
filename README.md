# FFmpeg Easy Web 工具

基于 Flask + FFmpeg 的多功能媒体处理 Web 应用，提供格式转换、视频剪切、文件合并、音视频混流和封面管理五大功能，支持 Docker 一键部署。

---

## 功能一览

| 功能 | 说明 |
|------|------|
| 🔄 格式转换 | 视频、音频、图片之间互相转换，支持高级 FFmpeg 参数与常用预设 |
| ✂️ 剪切 | 按开始/结束时间或时长裁剪音视频，支持流复制（极速）或重新编码 |
| 🔗 合并 | 多文件顺序拼接（concat），支持拖拽排序，最多 10 个 |
| 🎚️ 混流 | 视频替换音轨或混合叠加音频，支持独立音量调节 |
| 🖼️ 封面 | 给音频设置封面图 / 提取封面图片 / 从视频提取音频（可保留封面） |

---

## 快速开始

### 前置要求

- Docker & Docker Compose

### 启动

1. 使用仓库中提供的 `docker-compose.yml`（推荐）或自行创建 `docker-compose.yml` 文件：

    ```yaml
    version: '3.8'
    services:
      web:
        image: zy1234567/ffmpeg-easy-web:latest
        ports:
          - "5000:5000"
        volumes:
          - ./uploads:/app/uploads
          - ./outputs:/app/outputs
        environment:
          # 访问密钥：设置后所有接口及上传均需携带此密钥（留空则不启用鉴权）
          - ACCESS_KEY=
          - MAX_FILE_SIZE=500
          - ALLOWED_EXTENSIONS=mp4,avi,mov,mkv,flv,wmv,webm,mp3,wav,aac,flac,ogg,m4a,jpg,jpeg,png,gif,bmp,tiff
          - SECRET_KEY=change-me-in-production
        restart: unless-stopped
        networks:
          - ffmpeg-net
    networks:
      ffmpeg-net:
        driver: bridge
    ```
2. 运行 `docker-compose up -d` 启动容器

访问 `http://localhost:5000`

---

## 环境变量配置

在 `docker-compose.yml` 的 `environment` 中按需修改：

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `ACCESS_KEY` | *(空，不启用)* | **访问密钥**。设置后，所有 API 及上传均需携带该密钥。留空则不启用鉴权。 |
| `MAX_FILE_SIZE` | `500` | 单文件上传大小上限（MB） |
| `SECRET_KEY` | `dev-key` | Flask Session 密钥，生产环境务必修改 |
| `ALLOWED_EXTENSIONS` | `mp4,avi,...` | 允许上传的文件扩展名，逗号分隔 |

### 启用访问鉴权示例

```yaml
environment:
  - ACCESS_KEY=your-secret-key-here   # 设置后工具将要求输入密钥才能使用
  - SECRET_KEY=change-me-in-production
  - MAX_FILE_SIZE=500
```

设置 `ACCESS_KEY` 后：
- 浏览器访问页面时会弹出密钥输入框
- 所有 API 请求需在 Header 中携带 `X-Access-Key: <key>` 或在请求体中包含 `"access_key": "<key>"`

---

## API 接口

### `GET /api/health`
返回服务状态和 FFmpeg 可用性。

```json
{"status": "ok", "ffmpeg": true, "auth_enabled": false}
```

### `POST /api/upload`
上传文件，返回 `file_id` 和媒体信息。

### `POST /api/convert`
提交格式转换/剪切/混流/封面处理任务。

| 参数 | 说明 |
|------|------|
| `mode` | `convert` / `cut` / `mux` / `cover` |
| `file_id` | 上传后返回的文件 ID |
| `output_format` | 输出格式（如 `mp4`、`mp3`） |
| `extra_args` | 额外 FFmpeg 参数数组（安全白名单校验） |

### `POST /api/merge`
提交多文件合并任务，`file_ids` 为文件 ID 数组（2~10 个）。

### `POST /api/preview`
预览将执行的 FFmpeg 命令（不实际执行，路径已脱敏）。

### `GET /api/task/<task_id>`
轮询任务状态，返回 `status`（pending/running/done/error）和 `progress`（0~100）。

### `GET /api/download/<task_id>`
下载处理完成的文件。

### `GET /api/formats`
返回支持的输出格式列表。

---

## 注意事项

1. **封面管理 - 提取音频并保留封面**：MP3 容器不支持附加图片流，选择「保留封面」时输出格式会自动改为 **M4A**。
2. **合并**：要求所有文件的编码格式、分辨率一致（concat 流复制），否则请先用格式转换统一参数。
3. **文件自动清理**：上传和输出文件超过 24 小时后可通过 `POST /api/cleanup` 清理；已完成/失败任务记录超过 1 小时也会被清理。
4. **安全**：`extra_args` 中的 FFmpeg 参数经过白名单校验，不允许 Shell 操作符和路径穿越字符。

---

## 项目结构

```
.
├── docker-compose.yml
└── app/
    ├── Dockerfile
    ├── requirements.txt
    ├── app.py              # Flask 后端
    └── static/
        └── index.html      # 前端单页应用
```
