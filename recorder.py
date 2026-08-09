from __future__ import annotations

import argparse
import logging
import signal
import subprocess
import threading
import time
from datetime import datetime

from common import (
    build_video_segment_pattern,
    cleanup_recordings,
    enabled_cameras,
    get_record_root,
    gigabytes_to_bytes,
    load_config,
)


LOGGER = logging.getLogger("recorder")


class CameraRecorder(threading.Thread):
    def __init__(
        self,
        camera: dict,
        record_root,
        recorder_config: dict,
        stop_event: threading.Event,
    ) -> None:
        super().__init__(daemon=True, name=f"camera-{camera['id']}")
        self.camera = camera
        self.record_root = record_root
        self.recorder_config = recorder_config
        self.stop_event = stop_event
        self.ffmpeg_path = str(recorder_config["ffmpeg_path"])
        self.segment_seconds = max(int(recorder_config["segment_seconds"]), 1)
        self.target_fps = max(float(recorder_config["target_fps"]), 1.0)
        self.rtsp_transport = str(recorder_config["rtsp_transport"])
        self.ffmpeg_loglevel = str(recorder_config["ffmpeg_loglevel"])
        self.video_codec = str(recorder_config["video_codec"])
        self.video_extension = str(recorder_config["video_extension"])
        self.encoder_preset = str(recorder_config["encoder_preset"])
        self.crf = int(recorder_config["crf"])
        self.reconnect_delay = float(recorder_config["reconnect_delay_seconds"])

    def run(self) -> None:
        while not self.stop_event.is_set():
            process = self._start_ffmpeg()
            if process is None:
                self.stop_event.wait(self.reconnect_delay)
                continue

            try:
                self._wait_for_process(process)
            finally:
                self._stop_process(process)

            if not self.stop_event.is_set():
                self.stop_event.wait(self.reconnect_delay)

    def _start_ffmpeg(self) -> subprocess.Popen | None:
        if not self._ensure_output_directory():
            return None

        output_pattern = build_video_segment_pattern(
            self.record_root,
            self.camera["id"],
            self.video_extension,
        )
        command = self._build_ffmpeg_command(output_pattern)
        LOGGER.info("Starting ffmpeg recorder for camera %s", self.camera["id"])
        try:
            return subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            LOGGER.error("ffmpeg binary not found: %s", self.ffmpeg_path)
            self.stop_event.set()
            return None
        except OSError as error:
            LOGGER.error(
                "Failed to start ffmpeg for camera %s: %s",
                self.camera["id"],
                error,
            )
            return None

    def _ensure_output_directory(self) -> bool:
        output_directory = (
            self.record_root
            / self.camera["id"]
            / datetime.now().strftime("%Y-%m-%d")
        )
        try:
            output_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            LOGGER.error(
                "Failed to create recording directory for camera %s: %s",
                self.camera["id"],
                error,
            )
            return False
        return True

    def _build_ffmpeg_command(self, output_pattern: str) -> list[str]:
        return [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            self.ffmpeg_loglevel,
            "-rtsp_transport",
            self.rtsp_transport,
            "-i",
            self.camera["rtsp_url"],
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            f"fps={self.target_fps}",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            self.video_codec,
            "-preset",
            self.encoder_preset,
            "-crf",
            str(self.crf),
            "-force_key_frames",
            f"expr:gte(t,n_forced*{self.segment_seconds})",
            "-f",
            "segment",
            "-segment_time",
            str(self.segment_seconds),
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            "-segment_format",
            "mp4",
            "-segment_format_options",
            "movflags=+faststart",
            output_pattern,
        ]

    def _wait_for_process(self, process: subprocess.Popen) -> None:
        while not self.stop_event.is_set():
            # FFmpeg's segment muxer expands the date in the path, but does not
            # create the expanded directory. Keep it present across midnight.
            self._ensure_output_directory()
            return_code = process.poll()
            if return_code is not None:
                if return_code == 0:
                    LOGGER.info(
                        "ffmpeg recorder exited cleanly for camera %s",
                        self.camera["id"],
                    )
                else:
                    LOGGER.warning(
                        "ffmpeg recorder exited for camera %s with code %s",
                        self.camera["id"],
                        return_code,
                    )
                return
            self.stop_event.wait(1)

    def _stop_process(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return

        LOGGER.info("Stopping ffmpeg recorder for camera %s", self.camera["id"])
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            LOGGER.warning(
                "ffmpeg did not stop gracefully for camera %s, terminating",
                self.camera["id"],
            )

        process.terminate()
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            LOGGER.error(
                "ffmpeg did not terminate for camera %s, killing",
                self.camera["id"],
            )
            process.kill()
            process.wait(timeout=5)


def cleanup_worker(config: dict, stop_event: threading.Event) -> None:
    record_root = get_record_root(config)
    storage = config["storage"]
    max_storage_bytes = gigabytes_to_bytes(storage["max_storage_gb"])
    max_age_days = int(storage["max_age_days"])
    interval = int(storage["cleanup_interval_seconds"])

    while not stop_event.is_set():
        cleanup_recordings(record_root, max_storage_bytes, max_age_days)
        stop_event.wait(interval)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RTSP CCTV H.264 clip recorder")
    parser.add_argument(
        "--config",
        default="setting.json",
        help="Path to JSON config file",
    )
    return parser.parse_args()


def main() -> None:
    configure_logging()
    args = parse_args()
    config = load_config(args.config)
    record_root = get_record_root(config)
    record_root.mkdir(parents=True, exist_ok=True)

    stop_event = threading.Event()

    def handle_signal(signum, _frame) -> None:
        LOGGER.info("Received signal %s, stopping", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    workers = [
        CameraRecorder(camera, record_root, config["recorder"], stop_event)
        for camera in enabled_cameras(config)
    ]
    cleaner = threading.Thread(
        target=cleanup_worker,
        args=(config, stop_event),
        daemon=True,
        name="cleanup-worker",
    )

    for worker in workers:
        worker.start()
    cleaner.start()

    LOGGER.info("Recorder started for %s camera(s)", len(workers))

    try:
        while not stop_event.is_set():
            time.sleep(1)
    finally:
        stop_event.set()
        for worker in workers:
            worker.join(timeout=15)
        cleaner.join(timeout=5)
        LOGGER.info("Recorder stopped")


if __name__ == "__main__":
    main()
