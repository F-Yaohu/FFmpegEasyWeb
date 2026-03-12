import re
import uuid
import time
import threading
from pathlib import Path

from flask import Blueprint, request, jsonify

from config import UPLOAD_DIR, OUTPUT_DIR, tasks, tasks_lock
from database import save_task_to_db, mark_upload_file_used, get_asset, get_asset_disk_path
from security import require_auth, validate_ffmpeg_args
from ffmpeg import (
    _find_upload,
    _build_convert_cmd, _build_cut_cmd, _build_mux_cmd, _build_cover_cmd,
    run_ffmpeg, run_merge_task,
)

convert_bp = Blueprint('convert', __name__)


def _resolve_input(file_id: str, asset_id: str):
    if asset_id:
        asset = get_asset(asset_id)
        if not asset:
            return None, None, None, "找不到资产文件"
        path = get_asset_disk_path(asset)
        if not path.exists():
            return None, None, None, "资产文件不存在"
        return path, asset.get("name") or path.name, asset.get("asset_id"), ""

    if file_id:
        path = _find_upload(file_id)
        if not path:
            return None, None, None, "找不到上传的文件"
        return path, path.name.split("_", 1)[-1], None, ""

    return None, None, None, "缺少 file_id 或 asset_id"


@convert_bp.route('/api/preview', methods=['POST'])
@require_auth
def preview_command():
    """根据请求参数构建 FFmpeg 命令并返回，不执行"""
    data = request.get_json()
    if not data:
        return jsonify({'error': '请求体为空'}), 400

    endpoint = data.get('endpoint', 'convert')  # convert | merge

    if endpoint == 'merge':
        file_ids = data.get('file_ids', [])
        inputs = data.get('inputs', [])
        if inputs and isinstance(inputs, list):
            merge_count = len(inputs)
        else:
            merge_count = len(file_ids)
        output_format = data.get('output_format', 'mp4').strip().lower()
        extra_args = data.get('extra_args', [])
        if merge_count < 1:
            return jsonify({'error': '缺少 file_ids'}), 400
        concat_file = Path('/app/outputs/<concat_list.txt>')
        fake_output = Path(f'/app/outputs/<output.{output_format}>')
        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_file),
            '-c', 'copy',
        ]
        cmd.extend(extra_args)
        cmd += ['-progress', 'pipe:1', '-loglevel', 'warning', str(fake_output)]
        concat_content = '\n'.join([f"file '<uploaded_file_{i + 1}.{output_format}>'" for i in range(merge_count)])
        return jsonify({
            'command': ' '.join(cmd),
            'concat_list': concat_content,
            'note': 'concat 列表文件内容如上，实际路径由服务器生成',
        })

    # convert / cut / mux / cover
    mode = data.get('mode', 'convert')
    filename = data.get('filename', 'input')
    output_format = data.get('output_format', '').strip().lower()
    extra_args = data.get('extra_args', [])

    if not re.match(r'^[a-zA-Z0-9]{1,10}$', output_format):
        return jsonify({'error': '无效的输出格式'}), 400

    fake_input = Path(f'/app/uploads/<{filename}>')
    fake_output = Path(f'/app/outputs/<output.{output_format}>')

    if mode == 'cut':
        cmd = _build_cut_cmd(
            fake_input, fake_output, extra_args,
            ss=data.get('ss', ''), to=data.get('to', ''),
            t=data.get('t', ''), copy=data.get('copy', True),
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


@convert_bp.route('/api/convert', methods=['POST'])
@require_auth
def convert():
    data = request.get_json()
    if not data:
        return jsonify({'error': '请求体为空'}), 400

    mode = data.get('mode', 'convert')
    file_id = data.get('file_id', '')
    asset_id = data.get('asset_id', '')
    filename = data.get('filename', '')
    output_format = data.get('output_format', '').strip().lower()
    extra_args = data.get('extra_args', [])
    custom_name = data.get('custom_name', '').strip()

    if file_id and not re.match(r'^[a-zA-Z0-9_-]{1,64}$', file_id):
        return jsonify({'error': '无效的 file_id'}), 400
    if asset_id and not re.match(r'^[a-zA-Z0-9_-]{1,64}$', asset_id):
        return jsonify({'error': '无效的 asset_id'}), 400
    if not re.match(r'^[a-zA-Z0-9]{1,10}$', output_format):
        return jsonify({'error': '无效的输出格式'}), 400

    input_path, resolved_name, resolved_asset_id, err = _resolve_input(file_id, asset_id)
    if err:
        return jsonify({'error': err}), 404
    if not filename:
        filename = resolved_name or 'input'

    if extra_args:
        ok, msg = validate_ffmpeg_args(extra_args)
        if not ok:
            return jsonify({'error': msg}), 400

    task_id = str(uuid.uuid4())
    output_filename = f"{task_id}.{output_format}"
    output_path = OUTPUT_DIR / output_filename
    original_name = (Path(filename).stem or 'output') + '.' + output_format

    final_custom_name = None
    if custom_name:
        safe_custom = re.sub(r'[\\/*?:"<>|]', '', custom_name)
        if safe_custom:
            if not safe_custom.lower().endswith(f'.{output_format}'):
                safe_custom += f'.{output_format}'
            final_custom_name = safe_custom

    task_data = {
        'status': 'pending',
        'progress': 0,
        'output_file': output_filename,
        'original_name': original_name,
        'custom_name': final_custom_name,
        'log': [],
        'created_at': time.time(),
        'duration': data.get('duration', 0),
        'mode': mode,
        'input_filename': filename,
        'input_file_id': file_id,
        'input_asset_ids': [resolved_asset_id] if resolved_asset_id else [],
    }

    with tasks_lock:
        tasks[task_id] = task_data.copy()

    save_task_to_db(task_id, task_data)
    if file_id:
        mark_upload_file_used(file_id)

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
        audio_asset_id = data.get('audio_asset_id', '')
        audio_path = None
        if audio_id or audio_asset_id:
            audio_path, _audio_name, audio_resolved_asset_id, a_err = _resolve_input(audio_id, audio_asset_id)
            if a_err:
                return jsonify({'error': f'音频文件无效: {a_err}'}), 404
            if audio_resolved_asset_id:
                with tasks_lock:
                    tasks[task_id].setdefault('input_asset_ids', [])
                    tasks[task_id]['input_asset_ids'].append(audio_resolved_asset_id)
        else:
            audio_resolved_asset_id = None
        cmd_builder = _build_mux_cmd
        cmd_kwargs = {
            'audio_path': audio_path,
            'mux_mode': data.get('mux_mode', 'replace'),
            'video_vol': float(data.get('video_vol', 1.0)),
            'audio_vol': float(data.get('audio_vol', 1.0)),
        }
        if audio_id:
            mark_upload_file_used(audio_id)
    elif mode == 'cover':
        cover_action = data.get('cover_action', 'set')
        cover_id = data.get('cover_file_id', '')
        cover_asset_id = data.get('cover_asset_id', '')
        cover_path = None
        if cover_id or cover_asset_id:
            cover_path, _cover_name, cover_resolved_asset_id, c_err = _resolve_input(cover_id, cover_asset_id)
            if c_err:
                return jsonify({'error': f'封面文件无效: {c_err}'}), 404
            if cover_resolved_asset_id:
                with tasks_lock:
                    tasks[task_id].setdefault('input_asset_ids', [])
                    tasks[task_id]['input_asset_ids'].append(cover_resolved_asset_id)
        if cover_id:
            mark_upload_file_used(cover_id)
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
                if final_custom_name:
                    final_custom_name = re.sub(r'\.[^.]+$', '.m4a', final_custom_name)
                    with tasks_lock:
                        tasks[task_id]['custom_name'] = final_custom_name
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
        daemon=True,
    )
    t.start()

    return jsonify({'task_id': task_id})


@convert_bp.route('/api/merge', methods=['POST'])
@require_auth
def merge():
    data = request.get_json()
    if not data:
        return jsonify({'error': '请求体为空'}), 400

    file_ids = data.get('file_ids', [])
    asset_ids = data.get('asset_ids', [])
    inputs = data.get('inputs', [])
    output_format = data.get('output_format', '').strip().lower()
    extra_args = data.get('extra_args', [])
    custom_name = data.get('custom_name', '').strip()

    if inputs:
        normalized = []
        for item in inputs:
            if isinstance(item, dict):
                normalized.append({
                    'file_id': item.get('file_id', ''),
                    'asset_id': item.get('asset_id', ''),
                })
        inputs = normalized
    else:
        inputs = [{'file_id': fid, 'asset_id': ''} for fid in file_ids] + [{'file_id': '', 'asset_id': aid} for aid in asset_ids]

    if not inputs or len(inputs) < 2:
        return jsonify({'error': '至少需要 2 个文件'}), 400
    if len(inputs) > 10:
        return jsonify({'error': '最多支持 10 个文件'}), 400
    if not re.match(r'^[a-zA-Z0-9]{1,10}$', output_format):
        return jsonify({'error': '无效的输出格式'}), 400

    paths = []
    used_file_ids = []
    used_asset_ids = []
    for item in inputs:
        p, _name, resolved_asset_id, err = _resolve_input(item.get('file_id', ''), item.get('asset_id', ''))
        if err:
            return jsonify({'error': err}), 404
        paths.append(p)
        if item.get('file_id'):
            used_file_ids.append(item.get('file_id'))
        if resolved_asset_id:
            used_asset_ids.append(resolved_asset_id)

    if extra_args:
        ok, msg = validate_ffmpeg_args(extra_args)
        if not ok:
            return jsonify({'error': msg}), 400

    task_id = str(uuid.uuid4())
    output_filename = f"{task_id}.{output_format}"
    output_path = OUTPUT_DIR / output_filename
    original_name = f"merged.{output_format}"

    final_custom_name = None
    if custom_name:
        safe_custom = re.sub(r'[\\/*?:"<>|]', '', custom_name)
        if safe_custom:
            if not safe_custom.lower().endswith(f'.{output_format}'):
                safe_custom += f'.{output_format}'
            final_custom_name = safe_custom

    task_data = {
        'status': 'pending',
        'progress': 0,
        'output_file': output_filename,
        'original_name': original_name,
        'custom_name': final_custom_name,
        'log': [],
        'created_at': time.time(),
        'duration': 0,
        'mode': 'merge',
        'input_filename': f"{len(inputs)} files",
        'input_file_ids': used_file_ids,
        'input_asset_ids': used_asset_ids,
    }

    with tasks_lock:
        tasks[task_id] = task_data.copy()

    save_task_to_db(task_id, task_data)

    for fid in used_file_ids:
        mark_upload_file_used(fid)

    t = threading.Thread(
        target=run_merge_task,
        args=(task_id, paths, output_path, output_format, extra_args),
        daemon=True,
    )
    t.start()

    return jsonify({'task_id': task_id})
