import time

from flask import Blueprint, request, jsonify, send_file, send_from_directory

from config import tasks, tasks_lock, ACCESS_KEY
from security import require_auth
from ffmpeg import _check_ffmpeg

misc_bp = Blueprint("misc", __name__)


@misc_bp.route("/api/health")
def health():
	return jsonify({"status": "ok", "ffmpeg": _check_ffmpeg(), "auth_enabled": bool(ACCESS_KEY)})


@misc_bp.route("/api/cleanup", methods=["POST"])
@require_auth
def cleanup():
	# 资产库模式：仅清理内存中的过期任务状态，不自动删除任何文件。
	now = time.time()
	with tasks_lock:
		to_remove = [
			tid
			for tid, t in tasks.items()
			if t.get("status") in ("done", "error") and now - float(t.get("created_at") or now) > 3600
		]
		for tid in to_remove:
			tasks.pop(tid, None)

	return jsonify(
		{
			"removed_tasks": len(to_remove),
			"removed_orphan_uploads": 0,
			"upload_stats": {"note": "文件留存改为手动删除"},
		}
	)


@misc_bp.route("/img/<path:filename>")
def serve_img(filename):
	return send_from_directory("static/img", filename)


@misc_bp.route("/")
def index():
	return send_file("static/index.html")
