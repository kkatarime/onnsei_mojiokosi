from typing import Callable

MINUTES_SYSTEM_PROMPT = """あなたはプロの議事録作成者です。
音声文字起こしのテキストを受け取り、以下の構成で読みやすく整形してください。
文字起こしに存在しない情報は追加しないでください。
言語は文字起こしの言語に合わせてください（日本語なら日本語で）。

## 概要
会議の目的と結論を1段落で要約。

## 出席者
名前が言及されていれば列挙。不明な場合は「不明」と記載。

## 議題と議論
議論されたトピックをトピック毎に箇条書き。

## アクションアイテム
| 担当者 | タスク | 期限 |
形式で記載。不明な場合は「-」。

## 次のステップ
フォローアップ事項を箇条書き。
"""

MAX_CONTEXT_CHARS = 12000


class LMStudioConnectionError(Exception):
    pass


class LMStudioTimeoutError(Exception):
    pass


class LMStudioClient:
    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model: str = "deepseek-r1",
        timeout: int = 120,
    ):
        self.base_url = base_url
        self.model = model
        self.timeout = timeout

    def _client(self):
        from openai import OpenAI
        return OpenAI(base_url=self.base_url, api_key="lm-studio")

    def check_connection(self) -> bool:
        try:
            client = self._client()
            client.models.list()
            return True
        except Exception:
            return False

    def format_as_minutes(
        self,
        transcript: str,
        stream_callback: Callable[[str], None] | None = None,
    ) -> str:
        from openai import APIConnectionError, APITimeoutError

        chunks = self._split_transcript(transcript)
        all_results = []

        client = self._client()

        for i, chunk in enumerate(chunks):
            user_content = chunk
            if len(chunks) > 1:
                user_content = f"[パート {i+1}/{len(chunks)}]\n{chunk}"

            try:
                stream = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": MINUTES_SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    stream=True,
                    timeout=self.timeout,
                )
                part = []
                for delta in stream:
                    text = delta.choices[0].delta.content or ""
                    if text:
                        part.append(text)
                        if stream_callback:
                            stream_callback(text)
                all_results.append("".join(part))

            except APIConnectionError as e:
                raise LMStudioConnectionError(
                    f"LM Studioに接続できません ({self.base_url}): {e}"
                ) from e
            except APITimeoutError as e:
                raise LMStudioTimeoutError(
                    f"LM Studioの応答がタイムアウトしました ({self.timeout}秒): {e}"
                ) from e

        return "\n\n---\n\n".join(all_results)

    def _split_transcript(self, transcript: str) -> list[str]:
        if len(transcript) <= MAX_CONTEXT_CHARS:
            return [transcript]
        chunks = []
        while transcript:
            chunks.append(transcript[:MAX_CONTEXT_CHARS])
            transcript = transcript[MAX_CONTEXT_CHARS:]
        return chunks
