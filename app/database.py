import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path

from config import DB_PATH, OUTPUT_DIR, UPLOAD_DIR


def _conn() -> sqlite3.Connection:
	conn = sqlite3.connect(DB_PATH)
	conn.row_factory = sqlite3.Row
	return conn


def _json_loads(value, fallback):
	if not value:
		return fallback
	try:
		return json.loads(value)
	except Exception:
		return fallback


def init_db():
	with _conn() as conn:
		conn.execute(
			"""
			CREATE TABLE IF NOT EXISTS tasks (
				task_id TEXT PRIMARY KEY,
				status TEXT,
				progress INTEGER DEFAULT 0,
				output_file TEXT,
				original_name TEXT,
				custom_name TEXT,
				log_json TEXT,
				created_at REAL,
				updated_at REAL,
				duration REAL DEFAULT 0,
				mode TEXT,
				input_filename TEXT,
				input_file_id TEXT,
				input_file_ids_json TEXT,
				input_asset_ids_json TEXT,
				file_size INTEGER DEFAULT 0
			)
			"""
		)
		conn.execute(
			"""
			CREATE TABLE IF NOT EXISTS upload_files (
				file_id TEXT PRIMARY KEY,
				filename TEXT,
				file_size INTEGER DEFAULT 0,
				used_count INTEGER DEFAULT 0,
				created_at REAL,
				updated_at REAL
			)
			"""
		)
		conn.execute(
			"""
			CREATE TABLE IF NOT EXISTS assets (
				asset_id TEXT PRIMARY KEY,
				name TEXT,
				ext TEXT,
				kind TEXT,
				size INTEGER DEFAULT 0,
				duration REAL DEFAULT 0,
				source TEXT,
				path_type TEXT,
				stored_name TEXT,
				ref_id TEXT,
				info_json TEXT,
				created_at REAL,
				updated_at REAL
			)
			"""
		)
		conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_created ON assets(created_at DESC)")
		conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind)")
		conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
		conn.commit()


def _row_to_task(row: sqlite3.Row) -> dict:
	return {
		"task_id": row["task_id"],
		"status": row["status"],
		"progress": row["progress"] or 0,
		"output_file": row["output_file"] or "",
		"original_name": row["original_name"] or "",
		"custom_name": row["custom_name"] or "",
		"log": _json_loads(row["log_json"], []),
		"created_at": row["created_at"] or 0,
		"updated_at": row["updated_at"] or 0,
		"duration": row["duration"] or 0,
		"mode": row["mode"] or "",
		"input_filename": row["input_filename"] or "",
		"input_file_id": row["input_file_id"] or "",
		"input_file_ids": _json_loads(row["input_file_ids_json"], []),
		"input_asset_ids": _json_loads(row["input_asset_ids_json"], []),
		"file_size": row["file_size"] or 0,
	}


def save_task_to_db(task_id: str, task_data: dict):
	now = time.time()
	with _conn() as conn:
		conn.execute(
			"""
			INSERT OR REPLACE INTO tasks (
				task_id, status, progress, output_file, original_name, custom_name,
				log_json, created_at, updated_at, duration, mode,
				input_filename, input_file_id, input_file_ids_json, input_asset_ids_json, file_size
			) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
			""",
			(
				task_id,
				task_data.get("status", "pending"),
				int(task_data.get("progress", 0) or 0),
				task_data.get("output_file", ""),
				task_data.get("original_name", ""),
				task_data.get("custom_name", ""),
				json.dumps(task_data.get("log", []), ensure_ascii=False),
				float(task_data.get("created_at") or now),
				now,
				float(task_data.get("duration") or 0),
				task_data.get("mode", ""),
				task_data.get("input_filename", ""),
				task_data.get("input_file_id", ""),
				json.dumps(task_data.get("input_file_ids", []), ensure_ascii=False),
				json.dumps(task_data.get("input_asset_ids", []), ensure_ascii=False),
				int(task_data.get("file_size", 0) or 0),
			),
		)
		conn.commit()


def update_task_status_in_db(task_id: str, status=None, progress=None, file_size=None, log=None):
	parts = []
	args = []
	if status is not None:
		parts.append("status = ?")
		args.append(status)
	if progress is not None:
		parts.append("progress = ?")
		args.append(int(progress))
	if file_size is not None:
		parts.append("file_size = ?")
		args.append(int(file_size))
	if log is not None:
		parts.append("log_json = ?")
		args.append(json.dumps(log, ensure_ascii=False))
	parts.append("updated_at = ?")
	args.append(time.time())
	args.append(task_id)
	with _conn() as conn:
		conn.execute(f"UPDATE tasks SET {', '.join(parts)} WHERE task_id = ?", tuple(args))
		conn.commit()


def load_task_from_db(task_id: str):
	with _conn() as conn:
		row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
	if not row:
		return None
	return _row_to_task(row)


def load_all_tasks_from_db(limit=100):
	with _conn() as conn:
		rows = conn.execute(
			"SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
			(int(limit),),
		).fetchall()
	return [_row_to_task(r) for r in rows]


def delete_task_from_db(task_id: str) -> bool:
	with _conn() as conn:
		cur = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
		conn.commit()
		return cur.rowcount > 0


def _file_kind_by_ext(filename: str) -> str:
	ext = Path(filename).suffix.lower().lstrip(".")
	video = {"mp4", "avi", "mov", "mkv", "flv", "wmv", "webm", "mpeg", "ts", "gif"}
	audio = {"mp3", "wav", "aac", "flac", "ogg", "m4a", "opus", "wma"}
	image = {"png", "jpg", "jpeg", "bmp", "tiff", "webp"}
	if ext in video:
		return "video"
	if ext in audio:
		return "audio"
	if ext in image:
		return "image"
	return "other"


def _asset_row_to_dict(row: sqlite3.Row) -> dict:
	info = _json_loads(row["info_json"], {})
	return {
		"asset_id": row["asset_id"],
		"name": row["name"],
		"ext": row["ext"],
		"kind": row["kind"],
		"size": row["size"] or 0,
		"duration": row["duration"] or 0,
		"source": row["source"],
		"path_type": row["path_type"],
		"stored_name": row["stored_name"],
		"ref_id": row["ref_id"] or "",
		"info": info,
		"created_at": row["created_at"] or 0,
		"updated_at": row["updated_at"] or 0,
	}


def record_upload_file(file_id: str, filename: str, file_size: int, info=None):
	now = time.time()
	with _conn() as conn:
		conn.execute(
			"""
			INSERT OR REPLACE INTO upload_files(file_id, filename, file_size, used_count, created_at, updated_at)
			VALUES(?, ?, ?, COALESCE((SELECT used_count FROM upload_files WHERE file_id = ?), 0),
				   COALESCE((SELECT created_at FROM upload_files WHERE file_id = ?), ?), ?)
			""",
			(file_id, filename, int(file_size), file_id, file_id, now, now),
		)

		row = conn.execute("SELECT asset_id FROM assets WHERE source = 'upload' AND ref_id = ?", (file_id,)).fetchone()
		asset_id = row["asset_id"] if row else str(uuid.uuid4())
		ext = Path(filename).suffix.lower().lstrip(".")
		duration = 0
		if isinstance(info, dict):
			duration = float(info.get("duration") or 0)

		conn.execute(
			"""
			INSERT OR REPLACE INTO assets(
				asset_id, name, ext, kind, size, duration, source, path_type, stored_name,
				ref_id, info_json, created_at, updated_at
			) VALUES(?, ?, ?, ?, ?, ?, 'upload', 'upload', ?, ?, ?,
					 COALESCE((SELECT created_at FROM assets WHERE asset_id = ?), ?), ?)
			""",
			(
				asset_id,
				filename,
				ext,
				_file_kind_by_ext(filename),
				int(file_size),
				duration,
				f"{file_id}_{filename}",
				file_id,
				json.dumps(info or {}, ensure_ascii=False),
				asset_id,
				now,
				now,
			),
		)
		conn.commit()
	return asset_id


def mark_upload_file_used(file_id: str):
	with _conn() as conn:
		conn.execute(
			"UPDATE upload_files SET used_count = used_count + 1, updated_at = ? WHERE file_id = ?",
			(time.time(), file_id),
		)
		conn.commit()


def delete_upload_file(file_id: str):
	with _conn() as conn:
		row = conn.execute("SELECT filename FROM upload_files WHERE file_id = ?", (file_id,)).fetchone()
		if row:
			target = UPLOAD_DIR / f"{file_id}_{row['filename']}"
			try:
				target.unlink(missing_ok=True)
			except Exception:
				logging.getLogger(__name__).exception("delete upload file failed: %s", target)

		conn.execute("DELETE FROM upload_files WHERE file_id = ?", (file_id,))
		conn.execute("DELETE FROM assets WHERE source = 'upload' AND ref_id = ?", (file_id,))
		conn.commit()


def is_upload_file_in_use(file_id: str, exclude_task_id: str = "") -> bool:
	tasks = load_all_tasks_from_db(limit=100000)
	for task in tasks:
		if exclude_task_id and task.get("task_id") == exclude_task_id:
			continue
		if task.get("status") in ("done", "error", "pending", "running"):
			if task.get("input_file_id") == file_id:
				return True
			if file_id in (task.get("input_file_ids") or []):
				return True
	return False


def add_generated_asset(task_id: str, output_file: str, display_name: str, file_size: int, info=None):
	now = time.time()
	asset_id = str(uuid.uuid4())
	ext = Path(output_file).suffix.lower().lstrip(".")
	duration = 0
	if isinstance(info, dict):
		duration = float(info.get("duration") or 0)

	with _conn() as conn:
		exists = conn.execute(
			"SELECT asset_id FROM assets WHERE source = 'generated' AND ref_id = ?",
			(task_id,),
		).fetchone()
		if exists:
			asset_id = exists["asset_id"]

		conn.execute(
			"""
			INSERT OR REPLACE INTO assets(
				asset_id, name, ext, kind, size, duration, source, path_type,
				stored_name, ref_id, info_json, created_at, updated_at
			) VALUES(?, ?, ?, ?, ?, ?, 'generated', 'output', ?, ?, ?,
					 COALESCE((SELECT created_at FROM assets WHERE asset_id = ?), ?), ?)
			""",
			(
				asset_id,
				display_name,
				ext,
				_file_kind_by_ext(output_file),
				int(file_size or 0),
				duration,
				output_file,
				task_id,
				json.dumps(info or {}, ensure_ascii=False),
				asset_id,
				now,
				now,
			),
		)
		conn.commit()
	return asset_id


def list_assets(kind="", source="", q="", created_from=0, created_to=0, limit=200):
	sql = "SELECT * FROM assets WHERE 1=1"
	args = []
	if kind and kind != "all":
		sql += " AND kind = ?"
		args.append(kind)
	if source and source != "all":
		sql += " AND source = ?"
		args.append(source)
	if q:
		sql += " AND name LIKE ?"
		args.append(f"%{q}%")
	if created_from:
		sql += " AND created_at >= ?"
		args.append(float(created_from))
	if created_to:
		sql += " AND created_at <= ?"
		args.append(float(created_to))
	sql += " ORDER BY created_at DESC LIMIT ?"
	args.append(int(limit))

	with _conn() as conn:
		rows = conn.execute(sql, tuple(args)).fetchall()
	return [_asset_row_to_dict(r) for r in rows]


def get_asset(asset_id: str):
	with _conn() as conn:
		row = conn.execute("SELECT * FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
	if not row:
		return None
	return _asset_row_to_dict(row)


def get_asset_by_file_id(file_id: str):
	with _conn() as conn:
		row = conn.execute("SELECT * FROM assets WHERE source = 'upload' AND ref_id = ?", (file_id,)).fetchone()
	if not row:
		return None
	return _asset_row_to_dict(row)


def get_asset_disk_path(asset: dict) -> Path:
	base = UPLOAD_DIR if asset.get("path_type") == "upload" else OUTPUT_DIR
	return base / asset.get("stored_name", "")


def rename_asset(asset_id: str, new_name: str):
	now = time.time()
	with _conn() as conn:
		conn.execute(
			"UPDATE assets SET name = ?, updated_at = ? WHERE asset_id = ?",
			(new_name, now, asset_id),
		)
		conn.commit()


def delete_asset(asset_id: str):
	asset = get_asset(asset_id)
	if not asset:
		return False

	disk_path = get_asset_disk_path(asset)
	try:
		disk_path.unlink(missing_ok=True)
	except Exception:
		logging.getLogger(__name__).exception("delete asset file failed: %s", disk_path)

	with _conn() as conn:
		if asset.get("source") == "upload" and asset.get("ref_id"):
			conn.execute("DELETE FROM upload_files WHERE file_id = ?", (asset["ref_id"],))
		conn.execute("DELETE FROM assets WHERE asset_id = ?", (asset_id,))
		conn.commit()
	return True


def get_upload_stats():
	with _conn() as conn:
		row = conn.execute(
			"SELECT COUNT(*) AS c, COALESCE(SUM(file_size), 0) AS s FROM upload_files"
		).fetchone()
	return {
		"total_files": int(row["c"] if row else 0),
		"total_size": int(row["s"] if row else 0),
	}


def cleanup_orphan_uploads(_max_age_hours=24):
	# 资产库模式下仅允许手动删除文件，此接口保留但不执行物理清理。
	return 0
