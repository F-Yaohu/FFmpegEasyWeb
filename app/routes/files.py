import mimetypes
import re

from flask import Blueprint, request, jsonify, send_file

from config import tasks, tasks_lock
from database import (
    list_assets,
    get_asset,
    get_asset_disk_path,
    rename_asset,
    delete_asset,
    load_all_tasks_from_db,
)
from security import require_auth

files_bp = Blueprint("files", __name__)


def _in_running_task(asset: dict) -> bool:
    aid = asset.get("asset_id", "")
    fid = asset.get("ref_id", "") if asset.get("source") == "upload" else ""

    # 内存中的任务
    with tasks_lock:
        mem_tasks = list(tasks.values())
    for t in mem_tasks:
        if t.get("status") not in ("pending", "running"):
            continue
        if aid and aid in (t.get("input_asset_ids") or []):
            return True
        if fid:
            if t.get("input_file_id") == fid:
                return True
            if fid in (t.get("input_file_ids") or []):
                return True

    # 数据库中的任务
    db_tasks = load_all_tasks_from_db(limit=100000)
    for t in db_tasks:
        if t.get("status") not in ("pending", "running"):
            continue
        if aid and aid in (t.get("input_asset_ids") or []):
            return True
        if fid:
            if t.get("input_file_id") == fid:
                return True
            if fid in (t.get("input_file_ids") or []):
                return True
    return False


@files_bp.route("/api/files", methods=["GET"])
@require_auth
def api_list_files():
    kind = request.args.get("type", "all")
    source = request.args.get("source", "all")
    q = request.args.get("q", "").strip()
    created_from = request.args.get("from", 0, type=float)
    created_to = request.args.get("to", 0, type=float)
    limit = request.args.get("limit", 500, type=int)

    items = list_assets(kind=kind, source=source, q=q, created_from=created_from, created_to=created_to, limit=limit)

    total_size = sum(int(i.get("size") or 0) for i in items)
    return jsonify({"files": items, "total": len(items), "total_size": total_size})


@files_bp.route("/api/files/<asset_id>", methods=["GET"])
@require_auth
def api_get_file(asset_id: str):
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", asset_id):
        return jsonify({"error": "无效的 asset_id"}), 400
    asset = get_asset(asset_id)
    if not asset:
        return jsonify({"error": "文件不存在"}), 404
    path = get_asset_disk_path(asset)
    payload = asset.copy()
    payload["exists"] = path.exists()
    return jsonify(payload)


@files_bp.route("/api/files/<asset_id>/download", methods=["GET"])
@require_auth
def api_download_file(asset_id: str):
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", asset_id):
        return jsonify({"error": "无效的 asset_id"}), 400
    asset = get_asset(asset_id)
    if not asset:
        return jsonify({"error": "文件不存在"}), 404
    path = get_asset_disk_path(asset)
    if not path.exists():
        return jsonify({"error": "文件已不存在"}), 404
    return send_file(str(path), as_attachment=True, download_name=asset.get("name") or path.name)


@files_bp.route("/api/files/<asset_id>/preview", methods=["GET"])
@require_auth
def api_preview_file(asset_id: str):
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", asset_id):
        return jsonify({"error": "无效的 asset_id"}), 400
    asset = get_asset(asset_id)
    if not asset:
        return jsonify({"error": "文件不存在"}), 404
    path = get_asset_disk_path(asset)
    if not path.exists():
        return jsonify({"error": "文件已不存在"}), 404
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return send_file(str(path), as_attachment=False, mimetype=mime)


@files_bp.route("/api/files/<asset_id>/rename", methods=["POST"])
@require_auth
def api_rename_file(asset_id: str):
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", asset_id):
        return jsonify({"error": "无效的 asset_id"}), 400
    data = request.get_json() or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "文件名不能为空"}), 400

    new_name = re.sub(r"[\\/*?:\"<>|]", "", new_name)
    if not new_name:
        return jsonify({"error": "无效文件名"}), 400

    asset = get_asset(asset_id)
    if not asset:
        return jsonify({"error": "文件不存在"}), 404

    ext = asset.get("ext", "")
    if ext and not new_name.lower().endswith(f".{ext.lower()}"):
        new_name += f".{ext}"

    rename_asset(asset_id, new_name)
    return jsonify({"success": True, "name": new_name})


@files_bp.route("/api/files/<asset_id>", methods=["DELETE"])
@require_auth
def api_delete_file(asset_id: str):
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", asset_id):
        return jsonify({"error": "无效的 asset_id"}), 400

    asset = get_asset(asset_id)
    if not asset:
        return jsonify({"error": "文件不存在"}), 404

    if _in_running_task(asset):
        return jsonify({"error": "该文件正被未完成任务使用，暂不可删除"}), 409

    ok = delete_asset(asset_id)
    if not ok:
        return jsonify({"error": "删除失败"}), 500
    return jsonify({"success": True})


@files_bp.route("/api/files", methods=["DELETE"])
@require_auth
def api_delete_files_batch():
    data = request.get_json() or {}
    asset_ids = data.get("asset_ids", [])
    if not isinstance(asset_ids, list) or not asset_ids:
        return jsonify({"error": "未提供 asset_ids"}), 400

    deleted = 0
    failed = []
    for aid in asset_ids:
        if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", str(aid)):
            failed.append({"asset_id": aid, "error": "asset_id 非法"})
            continue
        asset = get_asset(aid)
        if not asset:
            failed.append({"asset_id": aid, "error": "不存在"})
            continue
        if _in_running_task(asset):
            failed.append({"asset_id": aid, "error": "被未完成任务引用"})
            continue
        if delete_asset(aid):
            deleted += 1
        else:
            failed.append({"asset_id": aid, "error": "删除失败"})

    return jsonify({"success": True, "deleted_count": deleted, "failed": failed})
