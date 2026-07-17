import os
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
    def _build_output_file(file: str) -> str:
        directory, basename = os.path.split(file)
        if basename.endswith("_flv.mp4"):
            basename = basename.removesuffix("_flv.mp4") + ".mp4"
        else:
            stem, ext = os.path.splitext(basename)
            basename = f"{stem}_converted.mp4"
        return os.path.join(directory, basename)

    @staticmethod
    def convert_flv_to_mp4(file, bitrate=None, ffmpeg_path=None):
        """
        Convert the video from flv format to mp4 format.

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

        try:
            output_args = {
                "c": "copy",
                "y": "-y",
            }

            if bitrate:
                output_args["b:v"] = bitrate
                del output_args["c"]
                output_args["c:v"] = "libx264"
                output_args["c:a"] = "copy"

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
