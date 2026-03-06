import os
import re
import uuid
import json
import time
import subprocess
import threading
from fractions import Fraction
from pathlib import Path
from functools import wraps
from flask import Flask, request, jsonify, send_file, g
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key')
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_FILE_SIZE', 500)) * 1024 * 1024  # MB

UPLOAD_DIR = Path('/app/uploads')
OUTPUT_DIR = Path('/app/outputs')
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ACCESS_KEY 鉴权：若环境变量未设置则不启用
ACCESS_KEY = os.environ.get('ACCESS_KEY', '').strip()

ALLOWED_EXTENSIONS = set(
    os.environ.get('ALLOWED_EXTENSIONS',
        'mp4,avi,mov,mkv,flv,wmv,webm,mp3,wav,aac,flac,ogg,m4a,jpg,jpeg,png,gif,bmp,tiff'
    ).split(',')
)

# 任务状态存储（生产环境建议用 Redis）
tasks = {}
tasks_lock = threading.Lock()

# ──────────────────────────────────────────────
# 鉴权
# ──────────────────────────────────────────────

def require_auth(f):
    """装饰器：若 ACCESS_KEY 已设置，则要求请求携带正确的 X-Access-Key 头或 access_key 查询参数"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if ACCESS_KEY:
            key = (
                request.headers.get('X-Access-Key', '')
                or request.args.get('access_key', '')
                or (request.get_json(silent=True) or {}).get('access_key', '')
            )
            if key != ACCESS_KEY:
                return jsonify({'error': '未授权：请提供正确的 access_key'}), 401
        return f(*args, **kwargs)
    return decorated


# ──────────────────────────────────────────────
# 安全工具
# ──────────────────────────────────────────────

DANGEROUS_PATTERNS = [
    r'[;&|`$]',           # Shell 操作符
    r'\.\.',              # 路径穿越
    r'>(>?)\s*/',         # 重定向到根路径
    r'<\s*/',             # 从根路径读取
    r'/etc/', r'/proc/',  # 敏感路径
    r'rm\s', r'dd\s',     # 危险命令
]

SAFE_FFMPEG_FLAGS = {
    '-i', '-vf', '-af', '-b:v', '-b:a', '-r', '-s', '-t', '-ss', '-to',
    '-c:v', '-c:a', '-codec:v', '-codec:a', '-vcodec', '-acodec',
    '-f', '-movflags', '-preset', '-crf', '-tune', '-profile:v',
    '-pix_fmt', '-aspect', '-vn', '-an', '-map', '-metadata',
    '-threads', '-y', '-n', '-loglevel', '-progress', '-stats',
    '-loop', '-shortest', '-filter_complex', '-ac', '-ar',
    '-vol', '-atempo', '-setpts', '-scale', '-crop', '-pad',
    '-rotate', '-transpose', '-hflip', '-vflip', '-deinterlace',
    '-fps_mode', '-vsync', '-async', '-c',
    # 混流 / 封面相关
    '-disposition', '-map_metadata', '-map_chapters',
    '-attach', '-dump_attachment', '-frames:v', '-frames',
    '-update', '-q:v', '-q:a', '-bitexact',
    '-id3v2_version', '-write_id3v1', '-write_apetag',
    '-tag:v', '-tag:a',
    # 合并 concat
    '-safe', '-segment_list', '-segment_format',
}

def is_safe_input(text: str) -> bool:
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return False
    return True

def validate_ffmpeg_args(args: list) -> tuple[bool, str]:
    """验证 FFmpeg 参数列表是否安全"""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith('-'):
            flag = arg.split(':')[0] if ':' in arg else arg
            if flag not in SAFE_FFMPEG_FLAGS:
                return False, f"不支持的参数: {arg}"
            if i + 1 < len(args) and not args[i + 1].startswith('-'):
                val = args[i + 1]
                if not is_safe_input(val):
                    return False, f"参数值包含危险字符: {val}"
                i += 2
                continue
        i += 1
    return True, ""

def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def safe_filename(original: str) -> str:
    name = secure_filename(original)
    if not name:
        name = 'upload'
    return name

def cleanup_old_files(directory: Path, max_age_hours: int = 24):
    """清理超过指定时间的文件"""
    now = time.time()
    for f in directory.iterdir():
        if f.is_file() and (now - f.stat().st_mtime) > max_age_hours * 3600:
            f.unlink(missing_ok=True)

def _sanitize_cmd_for_display(cmd: list) -> str:
    """将命令列表转为可读字符串（隐藏真实路径，用占位符替代）"""
    parts = []
    i = 0
    while i < len(cmd):
        arg = cmd[i]
        if arg in ('-i',) and i + 1 < len(cmd):
            parts.append(arg)
            p = Path(cmd[i + 1])
            parts.append(f'<{p.name}>')
            i += 2
            continue
        # 输出文件（最后一个不以 - 开头的参数）
        if i == len(cmd) - 1 and not arg.startswith('-') and arg not in ('pipe:1',):
            p = Path(arg)
            parts.append(f'<output.{p.suffix.lstrip(".")}>')
            i += 1
            continue
        parts.append(arg)
        i += 1
    return ' '.join(parts)

# ──────────────────────────────────────────────
# 路由 - 基础
# ──────────────────────────────────────────────

@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'ffmpeg': _check_ffmpeg(),
        'auth_enabled': bool(ACCESS_KEY),
    })

def _check_ffmpeg() -> bool:
    try:
        r = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False

@app.route('/api/upload', methods=['POST'])
@require_auth
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': '没有上传文件'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': '文件名为空'}), 400
    if not allowed_file(f.filename):
        return jsonify({'error': f'不支持的文件类型，允许: {", ".join(sorted(ALLOWED_EXTENSIONS))}'}), 400

    file_id = str(uuid.uuid4())
    filename = safe_filename(f.filename)
    save_path = UPLOAD_DIR / f"{file_id}_{filename}"
    f.save(str(save_path))

    info = get_file_info(str(save_path))
    return jsonify({
        'file_id': file_id,
        'filename': filename,
        'size': save_path.stat().st_size,
        'info': info
    })

def get_file_info(path: str) -> dict:
    """用 ffprobe 获取媒体信息"""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_format', '-show_streams',
            path
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            data = json.loads(r.stdout)
            fmt = data.get('format', {})
            streams = data.get('streams', [])
            video = next((s for s in streams if s.get('codec_type') == 'video' and s.get('disposition', {}).get('attached_pic', 0) == 0), None)
            audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
            cover = next((s for s in streams if s.get('codec_type') == 'video' and s.get('disposition', {}).get('attached_pic', 0) == 1), None)
            result = {
                'duration': float(fmt.get('duration', 0)),
                'size': int(fmt.get('size', 0)),
                'bitrate': int(fmt.get('bit_rate', 0)),
                'format': fmt.get('format_long_name', ''),
                'has_cover': cover is not None,
            }
            if video:
                result['video'] = {
                    'codec': video.get('codec_name', ''),
                    'width': video.get('width', 0),
                    'height': video.get('height', 0),
                    'fps': float(Fraction(video.get('r_frame_rate', '0/1'))),
                }
            if audio:
                result['audio'] = {
                    'codec': audio.get('codec_name', ''),
                    'sample_rate': int(audio.get('sample_rate', 0)),
                    'channels': audio.get('channels', 0),
                }
            return result
    except Exception:
        pass
    return {}

def _find_upload(file_id: str) -> Path | None:
    if not re.match(r'^[a-zA-Z0-9_-]{1,36}$', file_id):
        return None
    matches = list(UPLOAD_DIR.glob(f"{file_id}_*"))
    return matches[0] if matches else None

# ──────────────────────────────────────────────
# 命令构建器
# ──────────────────────────────────────────────

def _build_convert_cmd(input_path: Path, output_path: Path, extra_args: list) -> list:
    cmd = ['ffmpeg', '-y', '-i', str(input_path)]
    cmd.extend(extra_args)
    cmd += ['-progress', 'pipe:1', '-loglevel', 'warning', str(output_path)]
    return cmd

def _build_cut_cmd(input_path: Path, output_path: Path, extra_args: list,
                   ss='', to='', t='', copy=True) -> list:
    cmd = ['ffmpeg', '-y']
    if ss:
        cmd += ['-ss', ss]
    cmd += ['-i', str(input_path)]
    if to:
        cmd += ['-to', to]
    elif t:
        cmd += ['-t', t]
    if copy:
        cmd += ['-c', 'copy']
    cmd.extend(extra_args)
    cmd += ['-progress', 'pipe:1', '-loglevel', 'warning', str(output_path)]
    return cmd


def _build_mux_cmd(input_path: Path, output_path: Path, extra_args: list,
                   audio_path=None, mux_mode='replace',
                   video_vol=1.0, audio_vol=1.0) -> list:
    cmd = ['ffmpeg', '-y', '-i', str(input_path)]
    if audio_path:
        cmd += ['-i', str(audio_path)]
    if mux_mode == 'replace':
        if audio_path:
            cmd += ['-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-shortest']
        else:
            cmd += ['-map', '0:v', '-an', '-c:v', 'copy']
    else:  # mix
        if audio_path:
            fc = f'[0:a]volume={video_vol}[a0];[1:a]volume={audio_vol}[a1];[a0][a1]amix=inputs=2:duration=first[aout]'
            cmd += ['-filter_complex', fc, '-map', '0:v', '-map', '[aout]', '-c:v', 'copy']
        else:
            cmd += ['-map', '0:v', '-map', '0:a', '-c', 'copy']
    cmd.extend(extra_args)
    cmd += ['-progress', 'pipe:1', '-loglevel', 'warning', str(output_path)]
    return cmd


def _build_cover_cmd(input_path: Path, output_path: Path, extra_args: list,
                     cover_action='set', cover_path=None, keep_cover=False) -> list:
    if cover_action == 'set':
        cmd = ['ffmpeg', '-y', '-i', str(input_path)]
        if cover_path:
            cmd += ['-i', str(cover_path)]
            cmd += ['-map', '0:a', '-map', '1:v',
                    '-c', 'copy',
                    '-disposition:v', 'attached_pic',
                    '-id3v2_version', '3']
        else:
            cmd += ['-map', '0', '-c', 'copy']
    elif cover_action == 'extract':
        cmd = ['ffmpeg', '-y', '-i', str(input_path),
               '-an', '-frames:v', '1']
    else:  # extract_audio
        cmd = ['ffmpeg', '-y', '-i', str(input_path)]
        if keep_cover:
            # 保留封面必须用支持 attached_pic 的容器（m4a/mp4）
            # 输出格式由调用方保证为 m4a
            cmd += ['-map', '0:a', '-map', '0:v?',
                    '-c:a', 'copy', '-c:v', 'copy',
                    '-disposition:v:0', 'attached_pic']
        else:
            cmd += ['-vn', '-c:a', 'copy']

    cmd.extend(extra_args)
    cmd += ['-progress', 'pipe:1', '-loglevel', 'warning', str(output_path)]
    return cmd

# ──────────────────────────────────────────────
# /api/preview — 返回将执行的 FFmpeg 命令（不实际执行）
# ──────────────────────────────────────────────

@app.route('/api/preview', methods=['POST'])
@require_auth
def preview_command():
    """根据请求参数构建 FFmpeg 命令并返回，不执行"""
    data = request.get_json()
    if not data:
        return jsonify({'error': '请求体为空'}), 400

    endpoint = data.get('endpoint', 'convert')  # convert | merge

    if endpoint == 'merge':
        file_ids = data.get('file_ids', [])
        output_format = data.get('output_format', 'mp4').strip().lower()
        extra_args = data.get('extra_args', [])
        if not file_ids:
            return jsonify({'error': '缺少 file_ids'}), 400
        # 构造占位路径
        paths = [Path(f'/app/uploads/<file_{i+1}>') for i in range(len(file_ids))]
        fake_output = Path(f'/app/outputs/<output.{output_format}>')
        concat_file = Path('/app/outputs/<concat_list.txt>')
        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_file),
            '-c', 'copy',
        ]
        cmd.extend(extra_args)
        cmd += ['-progress', 'pipe:1', '-loglevel', 'warning', str(fake_output)]
        concat_content = '\n'.join([f"file '<uploaded_file_{i+1}.{output_format}>'" for i in range(len(file_ids))])
        return jsonify({
            'command': ' '.join(cmd),
            'concat_list': concat_content,
            'note': 'concat 列表文件内容如上，实际路径由服务器生成',
        })

    # convert / cut / mux / cover
    mode = data.get('mode', 'convert')
    file_id = data.get('file_id', '')
    filename = data.get('filename', 'input')
    output_format = data.get('output_format', '').strip().lower()
    extra_args = data.get('extra_args', [])

    if not re.match(r'^[a-zA-Z0-9]{1,10}$', output_format):
        return jsonify({'error': '无效的输出格式'}), 400

    # 使用占位路径
    fake_input = Path(f'/app/uploads/<{filename}>')
    fake_output = Path(f'/app/outputs/<output.{output_format}>')

    if mode == 'cut':
        cmd = _build_cut_cmd(
            fake_input, fake_output, extra_args,
            ss=data.get('ss', ''), to=data.get('to', ''),
            t=data.get('t', ''), copy=data.get('copy', True)
        )
    elif mode == 'mux':
        audio_filename = data.get('audio_filename', 'audio')
        fake_audio = Path(f'/app/uploads/<{audio_filename}>') if data.get('audio_file_id') else None
        cmd = _build_mux_cmd(
            fake_input, fake_output, extra_args,
            audio_path=fake_audio,
            mux_mode=data.get('mux_mode', 'replace'),
            video_vol=float(data.get('video_vol', 1.0)),
            audio_vol=float(data.get('audio_vol', 1.0)),
        )
    elif mode == 'cover':
        cover_filename = data.get('cover_filename', 'cover')
        fake_cover = Path(f'/app/uploads/<{cover_filename}>') if data.get('cover_file_id') else None
        cmd = _build_cover_cmd(
            fake_input, fake_output, extra_args,
            cover_action=data.get('cover_action', 'set'),
            cover_path=fake_cover,
            keep_cover=data.get('keep_cover', False),
        )
    else:  # convert
        cmd = _build_convert_cmd(fake_input, fake_output, extra_args)

    return jsonify({'command': ' '.join(cmd)})


# ──────────────────────────────────────────────
# /api/convert
# ──────────────────────────────────────────────

@app.route('/api/convert', methods=['POST'])
@require_auth
def convert():
    data = request.get_json()
    if not data:
        return jsonify({'error': '请求体为空'}), 400

    mode = data.get('mode', 'convert')
    file_id = data.get('file_id', '')
    filename = data.get('filename', '')
    output_format = data.get('output_format', '').strip().lower()
    extra_args = data.get('extra_args', [])

    if not re.match(r'^[a-zA-Z0-9_-]{1,36}$', file_id):
        return jsonify({'error': '无效的 file_id'}), 400
    if not re.match(r'^[a-zA-Z0-9]{1,10}$', output_format):
        return jsonify({'error': '无效的输出格式'}), 400

    input_path = _find_upload(file_id)
    if not input_path:
        return jsonify({'error': '找不到上传的文件'}), 404

    if extra_args:
        ok, msg = validate_ffmpeg_args(extra_args)
        if not ok:
            return jsonify({'error': msg}), 400

    task_id = str(uuid.uuid4())
    output_filename = f"{task_id}.{output_format}"
    output_path = OUTPUT_DIR / output_filename
    original_name = (Path(filename).stem or 'output') + '.' + output_format

    with tasks_lock:
        tasks[task_id] = {
            'status': 'pending',
            'progress': 0,
            'output_file': output_filename,
            'original_name': original_name,
            'log': [],
            'created_at': time.time(),
            'duration': data.get('duration', 0),
        }

    if mode == 'cut':
        cmd_builder = _build_cut_cmd
        cmd_kwargs = {
            'ss': data.get('ss', ''),
            'to': data.get('to', ''),
            't': data.get('t', ''),
            'copy': data.get('copy', True),
        }
    elif mode == 'mux':
        audio_id = data.get('audio_file_id', '')
        audio_path = _find_upload(audio_id) if audio_id else None
        cmd_builder = _build_mux_cmd
        cmd_kwargs = {
            'audio_path': audio_path,
            'mux_mode': data.get('mux_mode', 'replace'),
            'video_vol': float(data.get('video_vol', 1.0)),
            'audio_vol': float(data.get('audio_vol', 1.0)),
        }
    elif mode == 'cover':
        cover_action = data.get('cover_action', 'set')
        cover_id = data.get('cover_file_id', '')
        cover_path = _find_upload(cover_id) if cover_id else None
        # fix: extract_audio + keep_cover 必须输出 m4a
        if cover_action == 'extract_audio' and data.get('keep_cover', False):
            if output_format == 'mp3':
                output_format = 'm4a'
                output_filename = f"{task_id}.{output_format}"
                output_path = OUTPUT_DIR / output_filename
                original_name = (Path(filename).stem or 'output') + '.' + output_format
                with tasks_lock:
                    tasks[task_id]['output_file'] = output_filename
                    tasks[task_id]['original_name'] = original_name
        cmd_builder = _build_cover_cmd
        cmd_kwargs = {
            'cover_action': cover_action,
            'cover_path': cover_path,
            'keep_cover': data.get('keep_cover', False),
        }
    else:  # convert
        cmd_builder = None
        cmd_kwargs = {}

    t = threading.Thread(
        target=run_ffmpeg,
        args=(task_id, input_path, output_path, extra_args, cmd_builder, cmd_kwargs),
        daemon=True
    )
    t.start()

    return jsonify({'task_id': task_id})


# ──────────────────────────────────────────────
# /api/merge
# ──────────────────────────────────────────────

@app.route('/api/merge', methods=['POST'])
@require_auth
def merge():
    data = request.get_json()
    if not data:
        return jsonify({'error': '请求体为空'}), 400

    file_ids = data.get('file_ids', [])
    output_format = data.get('output_format', '').strip().lower()
    extra_args = data.get('extra_args', [])

    if not file_ids or len(file_ids) < 2:
        return jsonify({'error': '至少需要 2 个文件'}), 400
    if len(file_ids) > 10:
        return jsonify({'error': '最多支持 10 个文件'}), 400
    if not re.match(r'^[a-zA-Z0-9]{1,10}$', output_format):
        return jsonify({'error': '无效的输出格式'}), 400

    paths = []
    for fid in file_ids:
        p = _find_upload(fid)
        if not p:
            return jsonify({'error': f'找不到文件: {fid}'}), 404
        paths.append(p)

    if extra_args:
        ok, msg = validate_ffmpeg_args(extra_args)
        if not ok:
            return jsonify({'error': msg}), 400

    task_id = str(uuid.uuid4())
    output_filename = f"{task_id}.{output_format}"
    output_path = OUTPUT_DIR / output_filename
    original_name = f"merged.{output_format}"

    with tasks_lock:
        tasks[task_id] = {
            'status': 'pending',
            'progress': 0,
            'output_file': output_filename,
            'original_name': original_name,
            'log': [],
            'created_at': time.time(),
            'duration': 0,
        }

    t = threading.Thread(
        target=run_merge_task,
        args=(task_id, paths, output_path, output_format, extra_args),
        daemon=True
    )
    t.start()

    return jsonify({'task_id': task_id})


def run_merge_task(task_id: str, paths: list, output_path: Path,
                   _output_format: str, extra_args: list):
    def update(status=None, progress=None, log=None):
        with tasks_lock:
            task = tasks.get(task_id, {})
            if status:
                task['status'] = status
            if progress is not None:
                task['progress'] = progress
            if log:
                task['log'].append(log)

    update(status='running', progress=0)

    concat_file = OUTPUT_DIR / f"{task_id}_concat.txt"
    try:
        with open(str(concat_file), 'w', encoding='utf-8') as cf:
            for p in paths:
                cf.write(f"file '{str(p)}'\n")

        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_file),
            '-c', 'copy',
        ]
        cmd.extend(extra_args)
        cmd += ['-progress', 'pipe:1', '-loglevel', 'warning', str(output_path)]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        if proc.stdout:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith('progress=end'):
                    update(progress=99)

        proc.wait(timeout=3600)
        stderr_output = proc.stderr.read() if proc.stderr else ''

        if proc.returncode == 0:
            update(status='done', progress=100)
        else:
            update(status='error', log=stderr_output[:2000])
    except subprocess.TimeoutExpired:
        update(status='error', log='合并超时（1小时限制）')
    except Exception as e:
        update(status='error', log=str(e))
    finally:
        concat_file.unlink(missing_ok=True)


def run_ffmpeg(task_id: str, input_path: Path, output_path: Path,
               extra_args: list, cmd_builder=None, cmd_kwargs: dict = None):
    def update(status=None, progress=None, log=None):
        with tasks_lock:
            task = tasks.get(task_id, {})
            if status:
                task['status'] = status
            if progress is not None:
                task['progress'] = progress
            if log:
                task['log'].append(log)

    update(status='running', progress=0)

    if cmd_builder:
        cmd = cmd_builder(input_path, output_path, extra_args, **(cmd_kwargs or {}))
    else:
        cmd = ['ffmpeg', '-y', '-i', str(input_path)]
        cmd.extend(extra_args)
        cmd += ['-progress', 'pipe:1', '-loglevel', 'warning', str(output_path)]

    proc: 'subprocess.Popen[str] | None' = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        with tasks_lock:
            duration = tasks[task_id].get('duration') or 0.0

        current_time = 0.0
        if proc.stdout:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith('out_time_ms='):
                    try:
                        ms = int(line.split('=')[1])
                        current_time = ms / 1_000_000
                        if duration and float(duration) > 0:
                            pct = min(int(current_time / float(duration) * 100), 99)
                            update(progress=pct)
                    except ValueError:
                        pass
                elif line.startswith('progress=end'):
                    update(progress=99)

        proc.wait(timeout=3600)
        stderr_output = proc.stderr.read() if proc.stderr else ''

        if proc.returncode == 0:
            update(status='done', progress=100)
        else:
            update(status='error', log=stderr_output[:2000])
    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
        update(status='error', log='处理超时（1小时限制）')
    except Exception as e:
        update(status='error', log=str(e))


# ──────────────────────────────────────────────
# 其他路由
# ──────────────────────────────────────────────

@app.route('/api/task/<task_id>')
@require_auth
def task_status(task_id: str):
    if not re.match(r'^[a-zA-Z0-9_-]{1,36}$', task_id):
        return jsonify({'error': '无效的 task_id'}), 400
    with tasks_lock:
        task = tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    return jsonify(task)

@app.route('/api/download/<task_id>')
@require_auth
def download(task_id: str):
    if not re.match(r'^[a-zA-Z0-9_-]{1,36}$', task_id):
        return jsonify({'error': '无效的 task_id'}), 400
    with tasks_lock:
        task = tasks.get(task_id)
    if not task or task['status'] != 'done':
        return jsonify({'error': '文件未就绪'}), 404

    output_path = OUTPUT_DIR / task['output_file']
    if not output_path.exists():
        return jsonify({'error': '文件不存在'}), 404

    return send_file(
        str(output_path),
        as_attachment=True,
        download_name=task['original_name']
    )

@app.route('/api/formats')
def list_formats():
    formats = {
        'video': ['mp4', 'avi', 'mov', 'mkv', 'flv', 'wmv', 'webm', 'mpeg', 'ts', 'gif'],
        'audio': ['mp3', 'wav', 'aac', 'flac', 'ogg', 'm4a', 'opus', 'wma'],
        'image': ['png', 'jpg', 'bmp', 'tiff', 'webp'],
    }
    return jsonify(formats)

@app.route('/api/probe', methods=['POST'])
@require_auth
def probe():
    data = request.get_json()
    file_id = data.get('file_id', '')
    if not re.match(r'^[a-zA-Z0-9_-]{1,36}$', file_id):
        return jsonify({'error': '无效的 file_id'}), 400
    matches = list(UPLOAD_DIR.glob(f"{file_id}_*"))
    if not matches:
        return jsonify({'error': '找不到文件'}), 404
    info = get_file_info(str(matches[0]))
    return jsonify(info)

@app.route('/api/cleanup', methods=['POST'])
@require_auth
def cleanup():
    cleanup_old_files(UPLOAD_DIR)
    cleanup_old_files(OUTPUT_DIR)
    now = time.time()
    with tasks_lock:
        to_remove = [
            tid for tid, t in tasks.items()
            if t['status'] in ('done', 'error') and now - t['created_at'] > 3600
        ]
        for tid in to_remove:
            tasks.pop(tid, None)
    return jsonify({'removed_tasks': len(to_remove)})

@app.route('/')
def index():
    return send_file('static/index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
