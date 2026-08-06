from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)
VIDEO_EXTENSIONS = {".mp4", ".mpeg", ".mpg", ".avi", ".mov"}
RECORDING_EXTENSIONS = VIDEO_EXTENSIONS | {".jpg", ".jpeg"}


def load_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    if "cameras" not in config or not isinstance(config["cameras"], list):
        raise ValueError("setting.json must contain a 'cameras' list")

    config.setdefault("storage", {})
    config.setdefault("recorder", {})
    config.setdefault("replayer", {})

    config["storage"].setdefault("record_root", "./rec")
    config["storage"].setdefault("max_storage_gb", 20)
    config["storage"].setdefault("max_age_days", 7)
    config["storage"].setdefault("cleanup_interval_seconds", 60)

    config["recorder"].setdefault("segment_seconds", 60)
    config["recorder"].setdefault("target_fps", 5)
    config["recorder"].setdefault("ffmpeg_path", "ffmpeg")
    config["recorder"].setdefault("rtsp_transport", "tcp")
    config["recorder"].setdefault("ffmpeg_loglevel", "warning")
    config["recorder"].setdefault("video_codec", "libx264")
    config["recorder"].setdefault("video_extension", "mp4")
    config["recorder"].setdefault("encoder_preset", "veryfast")
    config["recorder"].setdefault("crf", 23)
    config["recorder"].setdefault("reconnect_delay_seconds", 5)

    config["replayer"].setdefault("host", "0.0.0.0")
    config["replayer"].setdefault("port", 8080)
    config["replayer"].setdefault("debug", False)

    return config


def get_record_root(config: dict[str, Any]) -> Path:
    return Path(config["storage"]["record_root"]).expanduser().resolve()


def gigabytes_to_bytes(value: float | int) -> int:
    return int(float(value) * 1024 * 1024 * 1024)


def build_video_path(
    record_root: Path,
    camera_id: str,
    segment_time: datetime,
    extension: str,
) -> Path:
    day_dir = record_root / camera_id / segment_time.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    normalized_extension = extension.lower().lstrip(".")
    base_name = f"{camera_id}_{segment_time.strftime('%Y-%m-%d_%H-%M-%S')}"
    output_path = day_dir / f"{base_name}.{normalized_extension}"
    if not output_path.exists():
        return output_path

    part_index = 2
    while True:
        candidate = day_dir / f"{base_name}_part{part_index:02d}.{normalized_extension}"
        if not candidate.exists():
            return candidate
        part_index += 1


def build_video_segment_pattern(
    record_root: Path,
    camera_id: str,
    extension: str,
) -> str:
    normalized_extension = extension.lower().lstrip(".")
    camera_dir = record_root / camera_id
    camera_dir.mkdir(parents=True, exist_ok=True)
    return str(
        camera_dir
        / "%Y-%m-%d"
        / f"{camera_id}_%Y-%m-%d_%H-%M-%S.{normalized_extension}"
    )


def list_recording_files(record_root: Path) -> list[Path]:
    if not record_root.exists():
        return []

    return sorted(
        (
            path
            for path in record_root.rglob("*")
            if path.is_file() and path.suffix.lower() in RECORDING_EXTENSIONS
        ),
        key=lambda path: path.stat().st_mtime,
    )


def cleanup_recordings(
    record_root: Path,
    max_storage_bytes: int | None,
    max_age_days: int | None,
) -> None:
    files = list_recording_files(record_root)
    if not files:
        return

    if max_age_days and max_age_days > 0:
        cutoff = datetime.now() - timedelta(days=max_age_days)
        for path in files:
            modified_time = datetime.fromtimestamp(path.stat().st_mtime)
            if modified_time < cutoff:
                try:
                    path.unlink()
                    LOGGER.info("Removed expired recording: %s", path)
                except FileNotFoundError:
                    continue
        files = list_recording_files(record_root)

    if max_storage_bytes and max_storage_bytes > 0:
        total_bytes = sum(path.stat().st_size for path in files)
        for path in files:
            if total_bytes <= max_storage_bytes:
                break
            try:
                file_size = path.stat().st_size
                path.unlink()
                total_bytes -= file_size
                LOGGER.info("Removed old recording for storage limit: %s", path)
            except FileNotFoundError:
                continue

    prune_empty_directories(record_root)


def prune_empty_directories(root: Path) -> None:
    if not root.exists():
        return

    directories = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            next(directory.iterdir())
        except StopIteration:
            directory.rmdir()


def enabled_cameras(config: dict[str, Any]) -> list[dict[str, Any]]:
    cameras = []
    for item in config["cameras"]:
        if item.get("enabled", True):
            cameras.append(item)
    return cameras


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS
