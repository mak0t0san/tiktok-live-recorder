import json
import os
import subprocess
import time
from pathlib import Path

import ffmpeg

from utils.logger_manager import logger


class VideoManagement:
    @staticmethod
    def wait_for_file_release(file, timeout=10):
        """
        Wait until the file is released (not locked anymore) or timeout is reached.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                with open(file, "ab"):
                    return True
            except PermissionError:
                time.sleep(0.5)
        return False

    @staticmethod
    def _ffprobe_bin(ffmpeg_path=None):
        if ffmpeg_path:
            return os.path.join(os.path.dirname(ffmpeg_path), "ffprobe")
        return "ffprobe"

    @staticmethod
    def _probe_max_dimensions(file, ffmpeg_path=None):
        """
        Scan the recording for the largest video resolution present.

        TikTok changes resolution mid-stream, so the first frame is not
        necessarily the highest quality (e.g. when recording starts mid-stream
        at a lower resolution). Every distinct resolution is introduced by a
        keyframe (H.264 requires a new SPS + IDR frame for a resolution change),
        so decoding only keyframes (``-skip_frame nokey``) is enough to find the
        maximum while staying cheap.

        Returns (width, height) of the largest frame by area, or None if it
        could not be determined.
        """
        ffprobe = VideoManagement._ffprobe_bin(ffmpeg_path)
        cmd = [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-skip_frame",
            "nokey",
            "-show_entries",
            "frame=width,height",
            "-of",
            "json",
            file,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            detail = getattr(e, "stderr", None) or str(e)
            logger.warning(f"Could not scan {file} for max resolution: {detail}")
            return None

        try:
            frames = json.loads(result.stdout).get("frames", [])
        except (ValueError, AttributeError):
            return None

        best = None
        for frame in frames:
            width = frame.get("width")
            height = frame.get("height")
            if not width or not height:
                continue
            w, h = int(width), int(height)
            if best is None or w * h > best[0] * best[1]:
                best = (w, h)
        return best

    @staticmethod
    def _probe_dimensions(file, ffmpeg_path=None):
        """
        Probe the source's initial video resolution via ffprobe.

        Returns (width, height) of the first video stream, or None if probing
        fails or no video stream is found. Used as a fallback when the
        keyframe scan in :meth:`_probe_max_dimensions` yields nothing.
        """
        ffprobe = VideoManagement._ffprobe_bin(ffmpeg_path)

        try:
            info = ffmpeg.probe(file, cmd=ffprobe)
        except (ffmpeg.Error, OSError) as e:
            stderr = getattr(e, "stderr", None)
            detail = stderr.decode() if isinstance(stderr, bytes) else str(e)
            logger.warning(f"Could not probe {file} for dimensions: {detail}")
            return None

        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                width = stream.get("width")
                height = stream.get("height")
                if width and height:
                    return int(width), int(height)
        logger.warning(f"No video stream with dimensions found in {file}.")
        return None

    @staticmethod
    def _build_output_file(file: str) -> str:
        directory, basename = os.path.split(file)
        if basename.endswith("_flv.mp4"):
            basename = basename.removesuffix("_flv.mp4") + ".mp4"
        else:
            stem, ext = os.path.splitext(basename)
            basename = f"{stem}_converted.mp4"
        return os.path.join(directory, basename)

    @staticmethod
    def convert_flv_to_mp4(file, bitrate=None, ffmpeg_path=None, scale=False):
        """
        Convert the video from flv format to mp4 format.

        When ``scale`` is set, the video is re-encoded onto a single canvas
        (the highest resolution seen anywhere in the recording) so that
        TikTok's mid-stream resolution changes no longer make the picture shrink
        and grow on playback. This is slower and lossy compared to the default
        stream copy.

        Returns the converted file path, or None if conversion was skipped
        or failed.
        """
        logger.info("Converting {} to MP4 format...".format(file))

        if not VideoManagement.wait_for_file_release(file):
            logger.error(
                f"File {file} is still locked after waiting. Skipping conversion."
            )
            return None

        output_file = VideoManagement._build_output_file(file)
        if os.path.abspath(output_file) == os.path.abspath(file):
            logger.error(f"Refusing to convert {file}: output path equals input path.")
            return None

        vf = None
        if scale:
            dimensions = VideoManagement._probe_max_dimensions(file, ffmpeg_path)
            if not dimensions:
                dimensions = VideoManagement._probe_dimensions(file, ffmpeg_path)
            if dimensions:
                w, h = dimensions
                vf = (
                    f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
                )
            else:
                logger.warning(
                    f"Skipping size normalization for {file}: "
                    "could not determine source resolution."
                )

        try:
            output_args = {"y": "-y"}

            if bitrate or vf:
                output_args["c:v"] = "libx264"
                output_args["c:a"] = "copy"
                if bitrate:
                    output_args["b:v"] = bitrate
                if vf:
                    output_args["vf"] = vf
                    output_args["pix_fmt"] = "yuv420p"
            else:
                output_args["c"] = "copy"

            ffmpeg.input(file).output(output_file, **output_args).run(
                quiet=True, cmd=ffmpeg_path or "ffmpeg"
            )

        except ffmpeg.Error as e:
            logger.error(
                f"ffmpeg conversion failed: {e.stderr.decode() if hasattr(e, 'stderr') else str(e)}"
            )
            return None

        os.remove(file)
        logger.info(f"Finished converting {Path(output_file).resolve()}\n")
        return output_file
