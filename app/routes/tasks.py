import re
import time
import sqlite3
from pathlib import Path

from flask import Blueprint, request, jsonify, send_file

from config import OUTPUT_DIR, DB_PATH, tasks, tasks_lock
from database import load_task_from_db, delete_task_from_db, load_all_tasks_from_db
from security import require_auth

tasks_bp = Blueprint("tasks_api", __name__)


@tasks_bp.route("/api/tasks", methods=["GET"])
@require_auth
def get_tasks():
	limit = request.args.get("limit", 100, type=int)
	status = request.args.get("status", "")

	tasks_list = load_all_tasks_from_db(limit)

	with tasks_lock:
		for task in tasks_list:
			if task["task_id"] in tasks:
				mem_task = tasks[task["task_id"]]
				task["status"] = mem_task.get("status", task["status"])
				task["progress"] = mem_task.get("progress", task["progress"])
				task["file_size"] = mem_task.get("file_size", task.get("file_size", 0))

	if status:
		tasks_list = [t for t in tasks_list if t.get("status") == status]

	return jsonify({"tasks": tasks_list, "total": len(tasks_list)})


@tasks_bp.route("/api/task/<task_id>", methods=["DELETE"])
@require_auth
def delete_task(task_id: str):
	if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", task_id):
		return jsonify({"error": "无效的 task_id"}), 400

	task = load_task_from_db(task_id)
	if not task:
		with tasks_lock:
			if task_id in tasks:
				task = tasks[task_id].copy()
	if not task:
		return jsonify({"error": "任务不存在"}), 404

	delete_task_from_db(task_id)
	with tasks_lock:
		tasks.pop(task_id, None)

	return jsonify({"success": True, "message": "任务记录已删除，文件保留在文件管理库"})


@tasks_bp.route("/api/tasks", methods=["DELETE"])
@require_auth
def delete_tasks_batch():
	data = request.get_json() or {}
	task_ids = data.get("task_ids", [])
	if not task_ids:
		return jsonify({"error": "未提供任务ID列表"}), 400

	deleted_count = 0
	failed_ids = []
	for task_id in task_ids:
		if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", str(task_id)):
			failed_ids.append(task_id)
			continue
		ok = delete_task_from_db(task_id)
		if ok:
			deleted_count += 1
		else:
			failed_ids.append(task_id)
		with tasks_lock:
			tasks.pop(task_id, None)

	return jsonify({"success": True, "deleted_count": deleted_count, "failed_ids": failed_ids})


@tasks_bp.route("/api/task/<task_id>/rename", methods=["POST"])
@require_auth
def rename_task(task_id: str):
	if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", task_id):
		return jsonify({"error": "无效的 task_id"}), 400

	data = request.get_json() or {}
	custom_name = data.get("custom_name", "").strip()
	if not custom_name:
		return jsonify({"error": "未提供新文件名"}), 400

	task = load_task_from_db(task_id)
	if not task:
		return jsonify({"error": "任务不存在"}), 404

	safe_name = re.sub(r"[\\/*?:\"<>|]", "", custom_name)
	if not safe_name:
		return jsonify({"error": "无效的文件名"}), 400

	output_file = task.get("output_file", "")
	if output_file:
		ext = Path(output_file).suffix
		if not safe_name.lower().endswith(ext.lower()):
			safe_name += ext

	try:
		with sqlite3.connect(DB_PATH) as conn:
			conn.execute(
				"UPDATE tasks SET custom_name = ?, updated_at = ? WHERE task_id = ?",
				(safe_name, time.time(), task_id),
			)
			conn.commit()
	except Exception as e:
		return jsonify({"error": f"重命名失败: {e}"}), 500

	with tasks_lock:
		if task_id in tasks:
			tasks[task_id]["custom_name"] = safe_name

	return jsonify({"success": True, "custom_name": safe_name})


@tasks_bp.route("/api/task/<task_id>")
@require_auth
def task_status(task_id: str):
	if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", task_id):
		return jsonify({"error": "无效的 task_id"}), 400

	with tasks_lock:
		mem_task = tasks.get(task_id)
	if mem_task:
		return jsonify(mem_task)

	db_task = load_task_from_db(task_id)
	if db_task:
		return jsonify(db_task)

	return jsonify({"error": "任务不存在"}), 404


@tasks_bp.route("/api/download/<task_id>")
@require_auth
def download(task_id: str):
	if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", task_id):
		return jsonify({"error": "无效的 task_id"}), 400

	with tasks_lock:
		task = tasks.get(task_id)
	if not task:
		task = load_task_from_db(task_id)

	if not task or task.get("status") != "done":
		return jsonify({"error": "文件未就绪"}), 404

	output_path = OUTPUT_DIR / task.get("output_file", "")
	if not output_path.exists():
		return jsonify({"error": "文件不存在或已从文件库删除"}), 404

	download_name = task.get("custom_name") or task.get("original_name") or output_path.name
	custom_download_name = request.args.get("filename", "").strip()
	if custom_download_name:
		safe_name = re.sub(r"[\\/*?:\"<>|]", "", custom_download_name)
		if safe_name:
			ext = Path(task.get("output_file", "")).suffix
			if ext and not safe_name.lower().endswith(ext.lower()):
				safe_name += ext
			download_name = safe_name

	return send_file(str(output_path), as_attachment=True, download_name=download_name)
