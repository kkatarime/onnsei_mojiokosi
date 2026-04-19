import gc
import threading
from typing import Callable

JAPANESE_INITIAL_PROMPT = (
    "以下は日本語の音声を文字起こしした内容です。"
    "句読点を適切に使用し、正確に書き起こしてください。"
)


class TranscriptionCancelledError(Exception):
    pass


class Transcriber:
    def __init__(self, model_size: str = "small", device: str = "auto", compute_type: str = "auto"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _resolve_device(self) -> tuple[str, str]:
        if self.device != "auto":
            ct = self.compute_type if self.compute_type != "auto" else (
                "float16" if self.device == "cuda" else "int8"
            )
            return self.device, ct
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda", "float16"
        except ImportError:
            pass
        return "cpu", "int8"

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            device, compute_type = self._resolve_device()
            self._model = WhisperModel(
                self.model_size,
                device=device,
                compute_type=compute_type,
            )
        return self._model

    def transcribe(
        self,
        wav_path: str,
        language: str = "ja",
        progress_callback: Callable[[float], None] | None = None,
        segment_callback: Callable[[str, float], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        model = self._get_model()

        initial_prompt = JAPANESE_INITIAL_PROMPT if language == "ja" else None

        segments, info = model.transcribe(
            wav_path,
            language=language if language != "auto" else None,
            beam_size=5,
            temperature=0.0,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=True,
            no_speech_threshold=0.6,
            initial_prompt=initial_prompt,
            word_timestamps=False,
        )
        total_duration = info.duration if info.duration else None

        full_text_parts = []
        for seg in segments:
            if cancel_event and cancel_event.is_set():
                raise TranscriptionCancelledError()
            full_text_parts.append(seg.text)
            if segment_callback:
                segment_callback(seg.text, seg.start)
            if progress_callback and total_duration and total_duration > 0:
                progress_callback(min(seg.end / total_duration, 1.0))

        if progress_callback:
            progress_callback(1.0)

        return "".join(full_text_parts)

    def unload(self):
        self._model = None
        gc.collect()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("使い方: python transcriber.py <wavファイルパス> [言語(ja/en/auto)]")
        sys.exit(1)
    lang = sys.argv[2] if len(sys.argv) > 2 else "ja"
    t = Transcriber(model_size="small")
    result = t.transcribe(
        sys.argv[1],
        language=lang,
        progress_callback=lambda r: print(f"進捗: {r:.0%}"),
        segment_callback=lambda s, ts: print(f"[{int(ts)//60:02d}:{int(ts)%60:02d}] {s}"),
    )
    print("\n=== 全文 ===")
    print(result)
