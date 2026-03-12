import re
import time
from functools import wraps

from flask import request, jsonify
from werkzeug.utils import secure_filename

from config import ACCESS_KEY, ALLOWED_EXTENSIONS


def require_auth(f):
	@wraps(f)
	def wrapper(*args, **kwargs):
		if not ACCESS_KEY:
			return f(*args, **kwargs)

		key = (
			request.headers.get("X-Access-Key", "").strip()
			or request.args.get("access_key", "").strip()
			or (request.get_json(silent=True) or {}).get("access_key", "").strip()
		)
		if key != ACCESS_KEY:
			return jsonify({"error": "未授权"}), 401
		return f(*args, **kwargs)

	return wrapper


DANGEROUS_PATTERNS = [
	r"[;&|`$><]",
	r"\.{2}/",
	r"\.{2}\\",
	r"\b(rm|del|mkfs|shutdown|reboot)\b",
]

SAFE_FFMPEG_FLAGS = {
	"-c:v", "-c:a", "-b:v", "-b:a", "-crf", "-preset", "-r", "-s", "-vf", "-af",
	"-an", "-vn", "-ac", "-ar", "-ab", "-q:v", "-pix_fmt", "-movflags", "-shortest",
	"-map", "-filter_complex", "-ss", "-to", "-t", "-threads", "-metadata", "-f",
	"-profile:v", "-level", "-g", "-maxrate", "-bufsize", "-aq", "-vol", "-aspect",
}


def is_safe_input(text: str) -> bool:
	if not isinstance(text, str):
		return False
	if len(text) > 500:
		return False
	return all(not re.search(pat, text, re.IGNORECASE) for pat in DANGEROUS_PATTERNS)


def validate_ffmpeg_args(extra_args) -> tuple[bool, str]:
	if not isinstance(extra_args, list):
		return False, "extra_args 必须是数组"
	if len(extra_args) > 60:
		return False, "参数过多"

	i = 0
	while i < len(extra_args):
		token = str(extra_args[i]).strip()
		if not token:
			i += 1
			continue
		if not is_safe_input(token):
			return False, f"存在不安全参数: {token}"
		if token.startswith("-"):
			if token not in SAFE_FFMPEG_FLAGS:
				return False, f"不允许的参数: {token}"
			if i + 1 < len(extra_args):
				nxt = str(extra_args[i + 1]).strip()
				if nxt and not nxt.startswith("-"):
					if not is_safe_input(nxt):
						return False, f"存在不安全参数值: {nxt}"
					i += 1
		i += 1
	return True, ""


def allowed_file(filename: str) -> bool:
	if "." not in filename:
		return False
	ext = filename.rsplit(".", 1)[1].lower()
	return ext in ALLOWED_EXTENSIONS


def safe_filename(filename: str) -> str:
	return secure_filename(filename) or f"upload_{int(time.time())}"


def cleanup_old_files(_directory, _max_age_hours=24):
	# 资产库模式下不做自动物理清理
	return 0


def _sanitize_cmd_for_display(cmd: list[str]) -> str:
	rendered = []
	for token in cmd:
		t = str(token)
		t = re.sub(r"/app/(uploads|outputs)/[^\s]+", r"/app/\1/<file>", t)
		rendered.append(t)
	return " ".join(rendered)
