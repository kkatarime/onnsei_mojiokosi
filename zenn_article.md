---
title: "ゼロから作る完全ローカル音声文字起こしアプリ【Python + Tkinter + faster-whisper】"
emoji: "🎙️"
type: "tech"
topics: ["python", "tkinter", "whisper", "音声認識", "デスクトップアプリ"]
published: false
---

## はじめに

「会議の録音を文字起こししたい。でも音声データを外部サーバーに送りたくない。」

そんな課題を解決するため、**完全ローカル動作**の音声文字起こしデスクトップアプリを  
Python + Tkinter + faster-whisper でゼロから作りました。

この記事では、設計から段階的な機能追加、精度改善、デスクトップアプリ化まで、  
**実際の開発の流れをそのまま記録**します。

**最終的に実現した機能：**
- mp3 / wav / mp4 / mov など主要な音声・動画フォーマットに対応
- faster-whisper によるローカル推論（GPU/CPU自動切替）
- タイムスタンプ付き文字起こし（ON/OFF切替可）
- 処理中のキャンセル
- 設定の永続化（モデル・言語）
- LM Studio接続インジケーター
- ダブルクリックで起動できるデスクトップアプリ化

---

## 技術スタック

| 役割 | ライブラリ |
|---|---|
| GUI | `tkinter`（Python標準） |
| 音声変換・前処理 | `pydub` + `ffmpeg` |
| 文字起こし | `faster-whisper` |
| LLM接続確認 | `openai`（OpenAI互換クライアント） |

```bash
pip install faster-whisper pydub openai
winget install Gyan.FFmpeg  # ffmpegは別途インストール
```

:::message
`ffmpeg` は Python パッケージではないため `pip` ではインストールできません。  
winget または [公式サイト](https://ffmpeg.org/download.html) からダウンロードして PATH に追加してください。
:::

---

## プロジェクト構成

```
prekore/
├── src/
│   ├── transcribe_app.py   # GUI・状態管理
│   ├── audio_handler.py    # 音声前処理
│   ├── transcriber.py      # Whisper推論
│   └── lm_studio_client.py # LM Studio接続確認
├── config.json             # 設定の永続化
├── launch.pyw              # ダブルクリック起動エントリポイント
├── install_shortcut.py     # デスクトップショートカット作成
└── requirements.txt
```

---

## Step 1：音声前処理（audio_handler.py）

### フォーマット対応

最初は `.mp3` と `.wav` のみ対応していましたが、**動画ファイルからの音声抽出**も必要になりました。

```diff python
- if ext == ".mp3":
-     return AudioSegment.from_mp3(path)
- elif ext == ".wav":
-     return AudioSegment.from_wav(path)
- else:
-     raise AudioLoadError(f"非対応のフォーマットです: {ext}")
+ # ffmpeg が対応する全フォーマットを AudioSegment.from_file() に委ねる
+ return AudioSegment.from_file(path)
```

`AudioSegment.from_file()` は ffmpeg が対応するすべての形式（mp4, mov, mkv, m4a など）を自動処理します。

### 音量正規化

Whisper は**音量が小さい音声で誤認識しやすい**という特性があります。  
pydub で **-20 dBFS に正規化**してから渡すことで精度が向上します。

```python
def export_wav(self, segment: AudioSegment, dest_dir: str) -> str:
    audio = segment.set_frame_rate(16000).set_channels(1)  # Whisper要件

    # -20 dBFS に正規化（小音量による誤認識を防ぐ）
    target_dBFS = -20.0
    delta = target_dBFS - audio.dBFS
    if abs(delta) > 0.5:
        audio = audio.apply_gain(delta)

    audio.export(tmp.name, format="wav")
    return tmp.name
```

:::message
Whisper が要求するフォーマットは **16kHz・モノラル** です。  
`set_frame_rate(16000).set_channels(1)` で事前に変換しておくことが重要です。
:::

---

## Step 2：文字起こしエンジン（transcriber.py）

### 精度チューニング

デフォルト設定のままでは日本語精度が低く出ました。以下のパラメータ調整が効きます。

```python
segments, info = model.transcribe(
    wav_path,
    language="ja",        # ← 最重要：言語を明示して自動検出の誤判定を防ぐ
    beam_size=5,
    temperature=0.0,       # 確定的デコード（ランダム誤認識をゼロに）
    vad_filter=True,       # 無音区間をスキップ
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
`language` を指定しないと、短い発話で Whisper が言語を誤認識することがあります。  
日本語音声には必ず `language="ja"` を指定してください。
:::

**精度改善のポイントまとめ：**

| パラメータ | 効果 |
|---|---|
| `language="ja"` | 自動検出の誤判定を排除 |
| `temperature=0.0` | 確定的デコード。再現性が上がり誤認識が減る |
| `initial_prompt` | 日本語モードを最初から強制 |
| `vad_filter=True` | 無音・ノイズ区間をスキップ |

### キャンセル処理

`threading.Event` をセグメントループ先頭で確認することで、次のセグメント境界で即座に中断できます。

```python
for seg in segments:
    if cancel_event and cancel_event.is_set():
        raise TranscriptionCancelledError()
    segment_callback(seg.text, seg.start)  # seg.start でタイムスタンプ取得
```

---

## Step 3：GUI（transcribe_app.py）

### 状態管理

UIの整合性を保つため、アプリ状態を Enum で管理し `_set_state()` でウィジェットの  
有効・無効を**一元制御**します。

```python
class AppState(Enum):
    IDLE        = auto()
    FILE_LOADED = auto()
    TRANSCRIBING = auto()
    DONE        = auto()
    CANCELLED   = auto()
    ERROR       = auto()
```

```
IDLE → FILE_LOADED → TRANSCRIBING → DONE
                          ↓
                      CANCELLED / ERROR
```

### スレッドセーフなUI更新

Tkinter は**メインスレッドからしかウィジェットを操作できません**。  
バックグラウンドスレッドからの更新はすべて `after(0, ...)` 経由で行います。

```python
# ❌ バックグラウンドスレッドから直接操作（クラッシュの原因）
self._txt_transcript.insert(tk.END, text)

# ✅ after(0, ...) でメインスレッドのイベントループに委譲
self.after(0, self._append_transcript, text, start_sec)
```

### モデル読み込み中の進捗表示

faster-whisper のモデル読み込みは初回数十秒かかります。  
その間プログレスバーが止まって見えるのを避けるため、動的に表示モードを切り替えます。

```python
def _update_progress(self, ratio: float):
    if not self._first_segment_received:
        # 最初のセグメントが届いた = モデル読み込み完了
        self._first_segment_received = True
        self._progressbar.stop()
        self._progressbar.config(mode="determinate")  # パーセント表示に切替
        self._lbl_status.config(text="文字起こし中...", foreground="blue")
    self._progressbar.config(value=int(ratio * 100))
```

**進捗の遷移：**
```
起動直後         → indeterminate（アニメーション）「モデル読み込み中...」
最初のセグメント → determinate（パーセント）「文字起こし中...」
完了             → 100%「完了」
```

### タイムスタンプON/OFFの即時切替

セグメントを `(text, start_sec)` のリストとして保持し、  
チェックボックス変更時にテキストエリアを**全再描画**します。

```python
def _render_transcript(self):
    self._txt_transcript.delete("1.0", tk.END)
    for text, start_sec in self._segments:
        if self._show_ts_var.get():
            m, s = divmod(int(start_sec), 60)
            self._txt_transcript.insert(tk.END, f"[{m:02d}:{s:02d}]{text}\n")
        else:
            self._txt_transcript.insert(tk.END, f"{text}\n")
    self._txt_transcript.see(tk.END)
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

### LM Studio 接続インジケーター

ローカルLLM（LM Studio）の接続状態を常時表示します。  
バックグラウンドスレッドで10秒ごとに自動ポーリングします。

```python
_LM_POLL_INTERVAL_MS = 10_000

def _check_lm_connection(self):
    connected = self._lm.check_connection()
    self.after(0, self._update_lm_indicator, connected)
    self.after(_LM_POLL_INTERVAL_MS, self._schedule_lm_check)

def _update_lm_indicator(self, connected: bool):
    if connected:
        self._lm_dot.itemconfig(self._lm_dot_id, fill="#22c55e")   # 緑
        self._lbl_lm_status.config(text="LM Studio 接続中", foreground="#16a34a")
    else:
        self._lm_dot.itemconfig(self._lm_dot_id, fill="#ef4444")   # 赤
        self._lbl_lm_status.config(text="LM Studio 未接続", foreground="#dc2626")
```

---

## Step 4：デスクトップアプリ化

### launch.pyw でコンソール非表示起動

`.pyw` 拡張子は Windows で `pythonw.exe` に関連付けられており、  
**コンソールウィンドウを表示せずに** GUI を起動できます。

```python
# launch.pyw（プロジェクトルートに配置）
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from transcribe_app import main
main()
```

### デスクトップショートカット作成

PowerShell の COM オブジェクトを Python から呼び出してショートカットを作成します。  
追加ライブラリ不要です。

```python
# install_shortcut.py
import subprocess, sys, os

pyw  = sys.executable.replace("python.exe", "pythonw.exe")
app  = os.path.abspath("launch.pyw")
dest = os.path.join(os.path.expanduser("~"), "Desktop", "音声文字起こし.lnk")

ps = f"""
$ws  = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut('{dest}')
$lnk.TargetPath       = '{pyw}'
$lnk.Arguments        = '"{app}"'
$lnk.WorkingDirectory = '{os.path.dirname(app)}'
$lnk.Save()
"""
subprocess.run(["powershell", "-Command", ps], check=True)
print(f"ショートカットを作成しました: {dest}")
```

```bash
# 初回のみ実行
python install_shortcut.py
```

以降はデスクトップの「音声文字起こし」をダブルクリックするだけで起動します。

---

## まとめ：詰まったポイントと解決策

| 課題 | 対策 |
|---|---|
| 日本語の誤認識が多い | `language="ja"` 明示 + `initial_prompt` + `temperature=0` |
| 小音量で認識精度が落ちる | pydub で -20dBFS 正規化 |
| UIが固まる | daemon スレッド + `after(0, ...)` でスレッドセーフ更新 |
| モデル読み込み中に無反応に見える | indeterminate → determinate へ動的切替 |
| 処理を途中で止められない | `threading.Event` でセグメント間キャンセル |
| mp4などの動画が読み込めない | `from_file()` に統一してffmpegに委ねる |
| 設定が毎回リセットされる | `config.json` に保存して起動時に復元 |
| コマンドラインでしか起動できない | `.pyw` + PowerShellでショートカット作成 |

---

## モデル選択の目安

| モデル | サイズ | 速度 | 精度 |
|---|---|---|---|
| tiny | 75MB | 最速 | 低 |
| base | 145MB | 速い | 普通 |
| **small** | 245MB | 普通 | **推奨** |
| medium | 769MB | 遅い | 高 |

:::message
初回実行時は HuggingFace からモデルがダウンロードされます（`small` で約245MB）。  
2回目以降はキャッシュが使われるのでオフラインでも動作します。
:::

---

## ソースコード

https://github.com/kkatarime/onnsei_mojiokosi
