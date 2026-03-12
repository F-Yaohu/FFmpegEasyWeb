import subprocess
from fractions import Fraction
from pathlib import Path

from config import UPLOAD_DIR, tasks, tasks_lock
from database import update_task_status_in_db, add_generated_asset


def _check_ffmpeg() -> bool:
	try:
		subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
		subprocess.run(["ffprobe", "-version"], capture_output=True, text=True, timeout=5)
		return True
	except Exception:
		return False


def get_file_info(file_path: str) -> dict:
	try:
		cmd = [
			"ffprobe", "-v", "error",
			"-show_entries", "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate",
			"-of", "json",
			file_path,
		]
		res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
		if res.returncode != 0:
			return {}

		import json

		data = json.loads(res.stdout or "{}")
		streams = data.get("streams", [])
		fmt = data.get("format", {})

		info = {"duration": float(fmt.get("duration") or 0)}
		for s in streams:
			ctype = s.get("codec_type")
			if ctype == "video" and "video" not in info:
				fps_text = s.get("r_frame_rate") or "0/1"
				try:
					fps = float(Fraction(fps_text))
				except Exception:
					fps = 0
				info["video"] = {
					"codec": s.get("codec_name", ""),
					"width": int(s.get("width") or 0),
					"height": int(s.get("height") or 0),
					"fps": fps,
				}
			elif ctype == "audio" and "audio" not in info:
				info["audio"] = {"codec": s.get("codec_name", "")}
		info["has_cover"] = any(s.get("codec_type") == "video" for s in streams) and "audio" in info
		return info
	except Exception:
		return {}


def _find_upload(file_id: str):
	matches = list(UPLOAD_DIR.glob(f"{file_id}_*"))
	if not matches:
		return None
	return matches[0]


def _build_convert_cmd(input_path: Path, output_path: Path, extra_args: list) -> list:
	cmd = ["ffmpeg", "-y", "-i", str(input_path)]
	cmd.extend(extra_args)
	cmd += ["-progress", "pipe:1", "-loglevel", "warning", str(output_path)]
	return cmd


def _build_cut_cmd(input_path: Path, output_path: Path, extra_args: list, ss="", to="", t="", copy=True) -> list:
	cmd = ["ffmpeg", "-y"]
	if ss:
		cmd += ["-ss", ss]
	cmd += ["-i", str(input_path)]
	if to:
		cmd += ["-to", to]
	elif t:
		cmd += ["-t", t]
	if copy:
		cmd += ["-c", "copy"]
	cmd.extend(extra_args)
	cmd += ["-progress", "pipe:1", "-loglevel", "warning", str(output_path)]
	return cmd


def _build_mux_cmd(
	input_path: Path,
	output_path: Path,
	extra_args: list,
	audio_path=None,
	mux_mode="replace",
	video_vol=1.0,
	audio_vol=1.0,
) -> list:
	cmd = ["ffmpeg", "-y", "-i", str(input_path)]
	if audio_path:
		cmd += ["-i", str(audio_path)]
	if mux_mode == "replace":
		if audio_path:
			cmd += ["-map", "0:v", "-map", "1:a", "-c:v", "copy", "-shortest"]
		else:
			cmd += ["-map", "0:v", "-an", "-c:v", "copy"]
	else:
		if audio_path:
			fc = f"[0:a]volume={video_vol}[a0];[1:a]volume={audio_vol}[a1];[a0][a1]amix=inputs=2:duration=first[aout]"
			cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[aout]", "-c:v", "copy"]
		else:
			cmd += ["-map", "0:v", "-map", "0:a", "-c", "copy"]
	cmd.extend(extra_args)
	cmd += ["-progress", "pipe:1", "-loglevel", "warning", str(output_path)]
	return cmd


def _build_cover_cmd(
	input_path: Path,
	output_path: Path,
	extra_args: list,
	cover_action="set",
	cover_path=None,
	keep_cover=False,
) -> list:
	if cover_action == "set":
		cmd = ["ffmpeg", "-y", "-i", str(input_path)]
		if cover_path:
			cmd += ["-i", str(cover_path)]
			cmd += ["-map", "0:a", "-map", "1:v", "-c", "copy", "-disposition:v", "attached_pic", "-id3v2_version", "3"]
		else:
			cmd += ["-map", "0", "-c", "copy"]
	elif cover_action == "extract":
		cmd = ["ffmpeg", "-y", "-i", str(input_path), "-an", "-frames:v", "1"]
	else:
		cmd = ["ffmpeg", "-y", "-i", str(input_path)]
		if keep_cover:
			cmd += ["-map", "0:a", "-map", "0:v?", "-c:a", "copy", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
		else:
			cmd += ["-vn", "-c:a", "copy"]
	cmd.extend(extra_args)
	cmd += ["-progress", "pipe:1", "-loglevel", "warning", str(output_path)]
	return cmd


def _update_task(task_id: str, status=None, progress=None, log=None, file_size=None):
	with tasks_lock:
		task = tasks.get(task_id, {})
		if status is not None:
			task["status"] = status
		if progress is not None:
			task["progress"] = int(progress)
		if log:
			task.setdefault("log", []).append(log)
		if file_size is not None:
			task["file_size"] = int(file_size)
	update_task_status_in_db(task_id, status=status, progress=progress, file_size=file_size, log=task.get("log") if task else None)


def run_merge_task(task_id: str, paths: list, output_path: Path, _output_format: str, extra_args: list):
	_update_task(task_id, status="running", progress=0)
	concat_file = output_path.parent / f"{task_id}_concat.txt"
	proc = None
	try:
		with open(concat_file, "w", encoding="utf-8") as cf:
			for p in paths:
				cf.write(f"file '{str(p)}'\n")

		cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy"]
		cmd.extend(extra_args)
		cmd += ["-progress", "pipe:1", "-loglevel", "warning", str(output_path)]

		proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
		if proc.stdout:
			for line in proc.stdout:
				if line.strip().startswith("progress=end"):
					_update_task(task_id, progress=99)

		proc.wait(timeout=3600)
		stderr_output = proc.stderr.read() if proc.stderr else ""
		if proc.returncode == 0:
			size = output_path.stat().st_size if output_path.exists() else 0
			_update_task(task_id, status="done", progress=100, file_size=size)
			task = tasks.get(task_id, {})
			info = get_file_info(str(output_path))
			add_generated_asset(
				task_id,
				task.get("output_file", output_path.name),
				task.get("custom_name") or task.get("original_name") or output_path.name,
				size,
				info=info,
			)
		else:
			_update_task(task_id, status="error", log=stderr_output[:2000])
	except subprocess.TimeoutExpired:
		if proc:
			proc.kill()
		_update_task(task_id, status="error", log="合并超时（1小时限制）")
	except Exception as e:
		_update_task(task_id, status="error", log=str(e))
	finally:
		concat_file.unlink(missing_ok=True)


def run_ffmpeg(task_id: str, input_path: Path, output_path: Path, extra_args: list, cmd_builder=None, cmd_kwargs=None):
	_update_task(task_id, status="running", progress=0)
	cmd_kwargs = cmd_kwargs or {}

	if cmd_builder:
		cmd = cmd_builder(input_path, output_path, extra_args, **cmd_kwargs)
	else:
		cmd = ["ffmpeg", "-y", "-i", str(input_path)] + list(extra_args) + ["-progress", "pipe:1", "-loglevel", "warning", str(output_path)]

	proc = None
	try:
		proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
		with tasks_lock:
			duration = float(tasks.get(task_id, {}).get("duration") or 0)

		if proc.stdout:
			for line in proc.stdout:
				line = line.strip()
				if line.startswith("out_time_ms=") and duration > 0:
					try:
						ms = int(line.split("=")[1])
						pct = min(int((ms / 1_000_000) / duration * 100), 99)
						_update_task(task_id, progress=pct)
					except Exception:
						pass
				elif line.startswith("progress=end"):
					_update_task(task_id, progress=99)

		proc.wait(timeout=3600)
		stderr_output = proc.stderr.read() if proc.stderr else ""
		if proc.returncode == 0:
			size = output_path.stat().st_size if output_path.exists() else 0
			_update_task(task_id, status="done", progress=100, file_size=size)
			task = tasks.get(task_id, {})
			info = get_file_info(str(output_path))
			add_generated_asset(
				task_id,
				task.get("output_file", output_path.name),
				task.get("custom_name") or task.get("original_name") or output_path.name,
				size,
				info=info,
			)
		else:
			_update_task(task_id, status="error", log=stderr_output[:2000])
	except subprocess.TimeoutExpired:
		if proc:
			proc.kill()
		_update_task(task_id, status="error", log="处理超时（1小时限制）")
	except Exception as e:
		_update_task(task_id, status="error", log=str(e))
