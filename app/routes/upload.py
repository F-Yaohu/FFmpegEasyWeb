import re
import uuid

from flask import Blueprint, request, jsonify

from config import UPLOAD_DIR, ALLOWED_EXTENSIONS, MAX_FILE_SIZE
from database import record_upload_file, get_upload_stats
from security import require_auth, allowed_file, safe_filename
from ffmpeg import get_file_info

upload_bp = Blueprint("upload", __name__)


@upload_bp.route("/api/upload", methods=["POST"])
@require_auth
def upload_file():
	if "file" not in request.files:
		return jsonify({"error": "没有上传文件"}), 400
	f = request.files["file"]
	if not f.filename:
		return jsonify({"error": "文件名为空"}), 400
	if not allowed_file(f.filename):
		return jsonify({"error": f"不支持的文件类型，允许: {', '.join(sorted(ALLOWED_EXTENSIONS))}"}), 400

	file_id = str(uuid.uuid4())
	filename = safe_filename(f.filename)
	save_path = UPLOAD_DIR / f"{file_id}_{filename}"
	f.save(str(save_path))

	file_size = save_path.stat().st_size
	info = get_file_info(str(save_path))
	asset_id = record_upload_file(file_id, filename, file_size, info=info)

	return jsonify(
		{
			"file_id": file_id,
			"asset_id": asset_id,
			"filename": filename,
			"size": file_size,
			"source": "upload",
			"info": info,
		}
	)


@upload_bp.route("/api/probe", methods=["POST"])
@require_auth
def probe():
	data = request.get_json() or {}
	file_id = data.get("file_id", "")
	if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", file_id):
		return jsonify({"error": "无效的 file_id"}), 400
	matches = list(UPLOAD_DIR.glob(f"{file_id}_*"))
	if not matches:
		return jsonify({"error": "找不到文件"}), 404
	info = get_file_info(str(matches[0]))
	return jsonify(info)


@upload_bp.route("/api/formats")
def list_formats():
	formats = {
		"video": ["mp4", "avi", "mov", "mkv", "flv", "wmv", "webm", "mpeg", "ts", "gif"],
		"audio": ["mp3", "wav", "aac", "flac", "ogg", "m4a", "opus", "wma"],
		"image": ["png", "jpg", "bmp", "tiff", "webp"],
	}
	return jsonify(formats)


@upload_bp.route("/api/maxFileSize")
def max_file_size():
	return jsonify({"size": MAX_FILE_SIZE})


@upload_bp.route("/api/upload/stats", methods=["GET"])
@require_auth
def upload_stats():
	return jsonify(get_upload_stats())
