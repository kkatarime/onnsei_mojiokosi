import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import os
import json
from enum import Enum, auto
from pathlib import Path

_LM_POLL_INTERVAL_MS = 10_000
CONFIG_PATH = Path(__file__).parent.parent / "config.json"
_DEFAULT_CONFIG = {"model": "small", "language": "ja"}


class AppState(Enum):
    IDLE = auto()
    FILE_LOADED = auto()
    TRANSCRIBING = auto()
    DONE = auto()
    CANCELLED = auto()
    ERROR = auto()


class TranscribeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ローカル音声文字起こし")
        self.resizable(True, True)
        self.minsize(700, 480)

        self._audio_segment = None
        self._wav_path = None
        self._state = AppState.IDLE
        self._cancel_event = threading.Event()
        self._lm_connected: bool | None = None
        self._segments: list[tuple[str, float]] = []

        self._init_backends()
        self._build_ui()
        self._load_config()
        self._set_state(AppState.IDLE)
        self._schedule_lm_check()

    def _init_backends(self):
        try:
            from audio_handler import AudioHandler
            self._audio = AudioHandler()
        except EnvironmentError as e:
            messagebox.showerror("ffmpegエラー", str(e))
            self.destroy()
            return

        from transcriber import Transcriber
        self._transcriber = Transcriber()

        from lm_studio_client import LMStudioClient
        self._lm = LMStudioClient()

    def _build_ui(self):
        self._build_menu()

        top = ttk.Frame(self, padding=8)
        top.pack(fill=tk.X)
        self._btn_select = ttk.Button(top, text="ファイル選択", command=self._on_select_file)
        self._btn_select.pack(side=tk.LEFT)
        self._lbl_file = ttk.Label(top, text="ファイルを選択してください", foreground="gray")
        self._lbl_file.pack(side=tk.LEFT, padx=8)

        ctrl = ttk.Frame(self, padding=(8, 0))
        ctrl.pack(fill=tk.X)

        self._btn_exec = ttk.Button(ctrl, text="文字起こし実行", command=self._on_execute)
        self._btn_exec.pack(side=tk.LEFT)

        self._btn_cancel = ttk.Button(ctrl, text="キャンセル", command=self._on_cancel)
        self._btn_cancel.pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(ctrl, text="モデル:").pack(side=tk.LEFT, padx=(12, 2))
        self._model_var = tk.StringVar(value="small")
        self._model_cb = ttk.Combobox(
            ctrl, textvariable=self._model_var,
            values=["tiny", "base", "small", "medium"],
            state="readonly", width=7
        )
        self._model_cb.pack(side=tk.LEFT)

        ttk.Label(ctrl, text="言語:").pack(side=tk.LEFT, padx=(8, 2))
        self._lang_var = tk.StringVar(value="ja")
        self._lang_cb = ttk.Combobox(
            ctrl, textvariable=self._lang_var,
            values=["ja", "en", "auto"],
            state="readonly", width=5
        )
        self._lang_cb.pack(side=tk.LEFT)

        self._show_ts_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            ctrl, text="タイムスタンプ", variable=self._show_ts_var,
            command=self._render_transcript
        ).pack(side=tk.LEFT, padx=(10, 0))

        lm_frame = ttk.Frame(ctrl)
        lm_frame.pack(side=tk.RIGHT, padx=(0, 4))
        self._lm_dot = tk.Canvas(
            lm_frame, width=12, height=12,
            highlightthickness=0, bg=self.cget("bg")
        )
        self._lm_dot.pack(side=tk.LEFT, padx=(0, 4))
        self._lm_dot_id = self._lm_dot.create_oval(1, 1, 11, 11, fill="gray", outline="")
        self._lbl_lm_status = ttk.Label(lm_frame, text="確認中...", foreground="gray")
        self._lbl_lm_status.pack(side=tk.LEFT)
        self._btn_lm_retry = ttk.Button(
            lm_frame, text="再確認", width=5, command=self._on_lm_retry
        )
        self._btn_lm_retry.pack(side=tk.LEFT, padx=(6, 0))

        prog_frame = ttk.Frame(self, padding=(8, 6))
        prog_frame.pack(fill=tk.X)
        self._progressbar = ttk.Progressbar(prog_frame, mode="determinate", maximum=100)
        self._progressbar.pack(fill=tk.X)
        self._lbl_status = ttk.Label(prog_frame, text="待機中", foreground="gray")
        self._lbl_status.pack(anchor=tk.W)

        txt_frame = ttk.Frame(self, padding=(8, 0, 8, 8))
        txt_frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(txt_frame, text="文字起こし結果").pack(anchor=tk.W)
        self._txt_transcript = scrolledtext.ScrolledText(
            txt_frame, wrap=tk.WORD, font=("Yu Gothic UI", 10)
        )
        self._txt_transcript.pack(fill=tk.BOTH, expand=True)

    def _build_menu(self):
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="文字起こしを保存", command=self._save_transcript)
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=self.destroy)
        menubar.add_cascade(label="ファイル", menu=file_menu)
        self.config(menu=menubar)

    def _load_config(self):
        cfg = _DEFAULT_CONFIG.copy()
        if CONFIG_PATH.exists():
            try:
                cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
            except Exception:
                pass
        self._model_var.set(cfg.get("model", "small"))
        self._lang_var.set(cfg.get("language", "ja"))

    def _save_config(self):
        cfg = {"model": self._model_var.get(), "language": self._lang_var.get()}
        try:
            CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _set_state(self, state: AppState):
        self._state = state
        idle = state in (AppState.IDLE, AppState.FILE_LOADED, AppState.DONE,
                         AppState.CANCELLED, AppState.ERROR)
        running = state == AppState.TRANSCRIBING

        self._btn_select.config(state=tk.NORMAL if idle else tk.DISABLED)
        self._btn_exec.config(
            state=tk.NORMAL if state in (AppState.FILE_LOADED, AppState.DONE, AppState.CANCELLED) else tk.DISABLED
        )
        self._btn_cancel.config(state=tk.NORMAL if running else tk.DISABLED)
        self._model_cb.config(state="readonly" if idle else tk.DISABLED)
        self._lang_cb.config(state="readonly" if idle else tk.DISABLED)

        if state == AppState.TRANSCRIBING:
            self._progressbar.config(mode="indeterminate", value=0)
            self._progressbar.start(12)
            self._lbl_status.config(text="モデル読み込み中...", foreground="blue")
        elif state == AppState.DONE:
            self._progressbar.stop()
            self._progressbar.config(mode="determinate", value=100)
            self._lbl_status.config(text="完了", foreground="green")
            self._cleanup_wav()
        elif state == AppState.CANCELLED:
            self._progressbar.stop()
            self._progressbar.config(mode="determinate", value=0)
            self._lbl_status.config(text="キャンセルしました", foreground="gray")
            self._cleanup_wav()
        elif state == AppState.ERROR:
            self._progressbar.stop()
            self._progressbar.config(mode="determinate", value=0)
            self._lbl_status.config(text="エラーが発生しました", foreground="red")
            self._cleanup_wav()
        elif state == AppState.FILE_LOADED:
            self._lbl_status.config(text="実行ボタンを押してください", foreground="gray")
        elif state == AppState.IDLE:
            self._lbl_status.config(text="待機中", foreground="gray")

    def _cleanup_wav(self):
        if self._wav_path and os.path.exists(self._wav_path):
            try:
                os.remove(self._wav_path)
            except Exception:
                pass
        self._wav_path = None

    # --- LM Studio接続インジケーター ---

    def _schedule_lm_check(self):
        threading.Thread(target=self._check_lm_connection, daemon=True).start()

    def _check_lm_connection(self):
        connected = self._lm.check_connection()
        self.after(0, self._update_lm_indicator, connected)
        self.after(_LM_POLL_INTERVAL_MS, self._schedule_lm_check)

    def _update_lm_indicator(self, connected: bool):
        self._lm_connected = connected
        if connected:
            self._lm_dot.itemconfig(self._lm_dot_id, fill="#22c55e")
            self._lbl_lm_status.config(text="LM Studio 接続中", foreground="#16a34a")
        else:
            self._lm_dot.itemconfig(self._lm_dot_id, fill="#ef4444")
            self._lbl_lm_status.config(text="LM Studio 未接続", foreground="#dc2626")

    def _on_lm_retry(self):
        self._lm_dot.itemconfig(self._lm_dot_id, fill="gray")
        self._lbl_lm_status.config(text="確認中...", foreground="gray")
        threading.Thread(
            target=lambda: self.after(0, self._update_lm_indicator, self._lm.check_connection()),
            daemon=True
        ).start()

    # --- ファイル選択 ---

    def _on_select_file(self):
        path = filedialog.askopenfilename(
            title="音声・動画ファイルを選択",
            filetypes=[
                ("音声・動画ファイル",
                 "*.mp3 *.wav *.m4a *.aac *.ogg *.flac "
                 "*.mp4 *.mov *.avi *.mkv *.webm *.ts"),
                ("音声ファイル", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac"),
                ("動画ファイル", "*.mp4 *.mov *.avi *.mkv *.webm *.ts"),
                ("すべてのファイル", "*.*"),
            ]
        )
        if not path:
            return
        try:
            self._audio_segment = self._audio.load_file(path)
            meta = self._audio.get_metadata(self._audio_segment)
            dur = meta["duration_sec"]
            m, s = divmod(int(dur), 60)
            self._lbl_file.config(
                text=f"{Path(path).name}  [{m:02d}:{s:02d}]", foreground="black"
            )
            self._set_state(AppState.FILE_LOADED)
        except Exception as e:
            messagebox.showerror("読み込みエラー", str(e))

    # --- 実行・キャンセル ---

    def _on_execute(self):
        if self._audio_segment is None:
            return
        self._txt_transcript.delete("1.0", tk.END)
        self._segments.clear()
        self._save_config()

        output_dir = os.path.join(os.path.dirname(__file__), "..", "output")
        try:
            self._wav_path = self._audio.export_wav(self._audio_segment, output_dir)
        except Exception as e:
            messagebox.showerror("WAV変換エラー", str(e))
            return

        self._transcriber.model_size = self._model_var.get()
        self._transcriber.unload()
        self._cancel_event.clear()
        self._first_segment_received = False

        self._set_state(AppState.TRANSCRIBING)
        threading.Thread(target=self._transcription_thread, daemon=True).start()

    def _on_cancel(self):
        self._cancel_event.set()
        self._btn_cancel.config(state=tk.DISABLED)
        self._lbl_status.config(text="キャンセル中...", foreground="orange")

    def _transcription_thread(self):
        from transcriber import TranscriptionCancelledError
        try:
            self._transcriber.transcribe(
                self._wav_path,
                language=self._lang_var.get(),
                progress_callback=lambda r: self.after(0, self._update_progress, r),
                segment_callback=lambda s, ts: self.after(0, self._append_transcript, s, ts),
                cancel_event=self._cancel_event,
            )
            self.after(0, self._set_state, AppState.DONE)
        except TranscriptionCancelledError:
            self.after(0, self._set_state, AppState.CANCELLED)
        except RuntimeError as e:
            msg = (
                f"GPU メモリ不足です。モデルサイズを小さくするか言語をCPUに切り替えてください。\n{e}"
                if "CUDA" in str(e) or "out of memory" in str(e).lower()
                else str(e)
            )
            self.after(0, self._on_error, Exception(msg))
        except Exception as e:
            self.after(0, self._on_error, e)

    # --- コールバック・更新 ---

    def _update_progress(self, ratio: float):
        if not self._first_segment_received:
            self._first_segment_received = True
            self._progressbar.stop()
            self._progressbar.config(mode="determinate")
            self._lbl_status.config(text="文字起こし中...", foreground="blue")
        self._progressbar.config(value=int(ratio * 100))

    def _append_transcript(self, text: str, start_sec: float):
        self._segments.append((text, start_sec))
        self._render_transcript()

    def _render_transcript(self):
        self._txt_transcript.delete("1.0", tk.END)
        for text, start_sec in self._segments:
            if self._show_ts_var.get():
                m, s = divmod(int(start_sec), 60)
                self._txt_transcript.insert(tk.END, f"[{m:02d}:{s:02d}]{text}\n")
            else:
                self._txt_transcript.insert(tk.END, f"{text}\n")
        self._txt_transcript.see(tk.END)

    def _on_error(self, exc: Exception):
        self._set_state(AppState.ERROR)
        messagebox.showerror("エラー", str(exc))

    # --- 保存 ---

    def _save_transcript(self):
        content = self._txt_transcript.get("1.0", tk.END)
        if not content.strip():
            messagebox.showinfo("保存", "保存する文字起こし結果がありません。")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("テキストファイル", "*.txt"), ("すべてのファイル", "*.*")],
            title="文字起こしを保存"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)


def main():
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    app = TranscribeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
