import shutil
import tempfile
import os
from pathlib import Path

if not shutil.which("ffmpeg"):
    raise EnvironmentError(
        "ffmpegが見つかりません。インストールしてPATHに追加してください。\n"
        "  winget install Gyan.FFmpeg\n"
        "または https://ffmpeg.org/download.html からダウンロード"
    )

from pydub import AudioSegment


class AudioLoadError(Exception):
    pass


SUPPORTED_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac",
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".ts",
}


class AudioHandler:
    def load_file(self, path: str) -> AudioSegment:
        path = str(path)
        ext = Path(path).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise AudioLoadError(
                f"非対応のフォーマットです: {ext}\n"
                f"対応: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )
        try:
            return AudioSegment.from_file(path)
        except Exception as e:
            raise AudioLoadError(f"ファイルの読み込みに失敗しました: {e}") from e

    def get_metadata(self, segment: AudioSegment) -> dict:
        return {
            "duration_sec": len(segment) / 1000.0,
            "channels": segment.channels,
            "sample_rate": segment.frame_rate,
        }

    def export_wav(self, segment: AudioSegment, dest_dir: str) -> str:
        os.makedirs(dest_dir, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(
            suffix=".wav", dir=dest_dir, delete=False
        )
        tmp.close()
        audio = segment.set_frame_rate(16000).set_channels(1)
        # -20 dBFS に正規化して小音量による誤認識を防ぐ
        target_dBFS = -20.0
        delta = target_dBFS - audio.dBFS
        if abs(delta) > 0.5:
            audio = audio.apply_gain(delta)
        audio.export(tmp.name, format="wav")
        return tmp.name
