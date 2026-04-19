---
title: "クラウド不要・完全ローカルで動く音声文字起こしアプリをPython+Tkinterで作る"
emoji: "🎙️"
type: "tech"
topics: ["python", "tkinter", "whisper", "音声認識", "デスクトップアプリ"]
published: false
---

## なぜこの記事を書くのか

会議の録音を文字起こししたいとき、多くのサービスは音声データをクラウドへ送信します。機密情報を含む会議では、それは避けたい場合があります。

この記事では **faster-whisper**（ローカルで動くWhisperの高速実装）と **Python + Tkinter** を使い、外部APIへの通信を一切行わないデスクトップアプリを段階的に実装します。

**解決する課題：**
- 音声データを外部に送らず文字起こししたい
- mp3/mp4など複数フォーマットをまとめて処理したい
- GUIで使いやすくしたい

---

## 技術スタック

| 役割 | ライブラリ | 用途 |
|---|---|---|
| GUI | `tkinter`（標準） | ウィンドウ・ウィジェット |
| 音声変換 | `pydub` + `ffmpeg` | フォーマット統一・音量正規化 |
| 文字起こし | `faster-whisper` | Whisperのローカル高速推論 |
| LLM接続確認 | `openai` | LM Studioとの疎通チェック（任意） |

```bash
pip install faster-whisper pydub openai
winget install Gyan.FFmpeg  # ffmpegはPythonパッケージではないので別途
```

---

## プロジェクト構成

```
prekore/
├── src/
│   ├── transcribe_app.py   # GUI・状態管理
│   ├── audio_handler.py    # 音声前処理
│   ├── transcriber.py      # Whisper推論
│   └── lm_studio_client.py # LM Studio接続確認
├── config.json             # 設定永続化
└── requirements.txt
```

---

## 実装：音声前処理（audio_handler.py）

Whisperに渡す前に**16kHz・モノラルへの変換**と**音量正規化**を行います。音量が小さいまま渡すと誤認識が増えるため、-20dBFSを目標値に正規化します。

```python
def export_wav(self, segment: AudioSegment, dest_dir: str) -> str:
    audio = segment.set_frame_rate(16000).set_channels(1)

    # -20 dBFS に正規化（小音量による誤認識を防ぐ）
    target_dBFS = -20.0
    delta = target_dBFS - audio.dBFS
    if abs(delta) > 0.5:
        audio = audio.apply_gain(delta)

    audio.export(tmp.name, format="wav")
    return tmp.name
```

:::message
対応フォーマットは `AudioSegment.from_file()` に委ねることで、mp3/wav/m4a/mp4/movなど **ffmpegが対応するすべての形式**を自動サポートできます。
:::

---

## 実装：文字起こしエンジン（transcriber.py）

### 精度チューニングのポイント

デフォルト設定のままでは日本語精度が低いです。以下のパラメータ設定が効きます。

```python
segments, info = model.transcribe(
    wav_path,
    language="ja",        # 言語を明示（自動検出は誤判定の原因）
    beam_size=5,
    temperature=0.0,       # 確定的デコード。ランダム誤認識をゼロにする
    vad_filter=True,       # 無音区間をスキップしてノイズ誤認識を防ぐ
    vad_parameters={"min_silence_duration_ms": 500},
    condition_on_previous_text=True,
    no_speech_threshold=0.6,
    initial_prompt=(
        "以下は日本語の音声を文字起こしした内容です。"
        "句読点を適切に使用し、正確に書き起こしてください。"
    ),
)
```

:::message alert
`language` を指定しないと、短い発話でWhisperが言語を誤認識することがあります。日本語音声には必ず `language="ja"` を指定してください。
:::

### キャンセル処理

`threading.Event` をセグメントループ先頭で確認することで、次のセグメント境界で即座に中断できます。

```python
for seg in segments:
    if cancel_event and cancel_event.is_set():
        raise TranscriptionCancelledError()
    segment_callback(seg.text, seg.start)
```

---

## 実装：GUI（transcribe_app.py）

### スレッドセーフなUI更新

Tkinterは**メインスレッドからしかウィジェットを操作できません**。バックグラウンドスレッドからの更新はすべて `after(0, ...)` 経由で行います。

```python
# ❌ バックグラウンドスレッドから直接操作（クラッシュの原因）
self._txt_transcript.insert(tk.END, text)

# ✅ after(0, ...) 経由でメインスレッドに委譲
self.after(0, self._append_transcript, text, start_sec)
```

### モデル読み込み中の進捗表示

faster-whisperのモデル読み込みは初回数十秒かかります。プログレスバーが止まって見えるのを避けるため、モデル読み込み中は **indeterminate（アニメーション）**、最初のセグメントが届いた時点で **determinate（パーセント）** に切り替えます。

```python
def _update_progress(self, ratio: float):
    if not self._first_segment_received:
        self._first_segment_received = True
        self._progressbar.stop()
        self._progressbar.config(mode="determinate")
        self._lbl_status.config(text="文字起こし中...", foreground="blue")
    self._progressbar.config(value=int(ratio * 100))
```

### タイムスタンプのON/OFF切り替え

セグメントを `(text, start_sec)` のリストとして保持し、チェックボックス変更時に全再描画します。これにより文字起こし中・完了後を問わずリアルタイムに表示が切り替わります。

```python
def _render_transcript(self):
    self._txt_transcript.delete("1.0", tk.END)
    for text, start_sec in self._segments:
        if self._show_ts_var.get():
            m, s = divmod(int(start_sec), 60)
            self._txt_transcript.insert(tk.END, f"[{m:02d}:{s:02d}]{text}\n")
        else:
            self._txt_transcript.insert(tk.END, f"{text}\n")
```

出力例（タイムスタンプON）：
```
[00:03] 本日はお集まりいただきありがとうございます。
[00:07] まず最初の議題ですが、Q3の売上について共有します。
[00:15] 前四半期比で15%増という結果になりました。
```

### 設定の永続化

モデル・言語の選択を `config.json` に保存し、次回起動時に復元します。

```python
CONFIG_PATH = Path(__file__).parent.parent / "config.json"

def _save_config(self):
    cfg = {"model": self._model_var.get(), "language": self._lang_var.get()}
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def _load_config(self):
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self._model_var.set(cfg.get("model", "small"))
        self._lang_var.set(cfg.get("language", "ja"))
```

---

## 状態管理

UIの整合性を保つため、状態を Enum で管理し `_set_state()` でウィジェットの有効・無効を一元制御しています。

```
IDLE → FILE_LOADED → TRANSCRIBING → DONE
                          ↓              ↓
                      CANCELLED       （再実行可）
                          ↓
                        ERROR
```

---

## モデル選択の目安

| モデル | サイズ | 速度 | 精度 |
|---|---|---|---|
| tiny | 75MB | 最速 | 低 |
| base | 145MB | 速い | 普通 |
| **small** | 245MB | 普通 | **推奨** |
| medium | 769MB | 遅い | 高 |

:::message
初回実行時はHuggingFaceからモデルがダウンロードされます（`small`で約245MB）。2回目以降はキャッシュが使われます。
:::

---

## 起動方法

```bash
python src/transcribe_app.py
```

---

## まとめ

| 課題 | 対策 |
|---|---|
| 日本語の誤認識 | `language="ja"` + `initial_prompt` + `temperature=0` |
| 小音量による誤認識 | pydubで-20dBFS正規化 |
| GUIの固まり | daemon スレッド + `after(0, ...)` |
| 読み込み中の無反応 | indeterminate → determinate へ動的切り替え |
| 処理を途中で止められない | `threading.Event` でセグメント間キャンセル |

完全ローカル動作のため、機密性の高い会議録音にも安心して使えます。LM Studio（ローカルLLM）との連携で議事録整形まで自動化することも可能です。
