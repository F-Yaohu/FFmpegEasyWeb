import os
import threading
from pathlib import Path

MAX_FILE_SIZE = int(os.environ.get('MAX_FILE_SIZE', 500))  # MB

UPLOAD_DIR = Path('/app/uploads')
OUTPUT_DIR = Path('/app/outputs')
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# 数据库路径
DB_PATH = Path('/app/data/tasks.db')
DB_PATH.parent.mkdir(exist_ok=True)

# ACCESS_KEY 鉴权：若环境变量未设置则不启用
ACCESS_KEY = os.environ.get('ACCESS_KEY', '').strip()

ALLOWED_EXTENSIONS = set(
    os.environ.get('ALLOWED_EXTENSIONS',
        'mp4,avi,mov,mkv,flv,wmv,webm,mp3,wav,aac,flac,ogg,m4a,jpg,jpeg,png,gif,bmp,tiff'
    ).split(',')
)

# 任务状态存储（内存缓存，用于进度追踪）
tasks: dict = {}
tasks_lock = threading.Lock()
