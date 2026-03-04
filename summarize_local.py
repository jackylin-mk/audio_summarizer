#!/usr/bin/env python3
"""
腳本一：本地音訊檔 → Whisper 轉錄 → Claude 摘要
使用方式：python summarize_local.py <音訊檔路徑>
"""

import sys
import os
from pathlib import Path
from openai import OpenAI
import anthropic

# ── 設定 ──────────────────────────────────────────────
OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY",  "your_openai_api_key")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "your_anthropic_api_key")

WHISPER_MODEL  = "whisper-1"
CLAUDE_MODEL   = "claude-sonnet-4-20250514"
MAX_TOKENS     = 4096

# Whisper 單檔上限 25MB；超過請先用 ffmpeg 分割
WHISPER_MAX_MB = 25
# ─────────────────────────────────────────────────────


SUMMARY_PROMPT = """你是一位專業會議記錄整理員。
請根據以下逐字稿，輸出兩個區塊：

## 📋 會議記錄
（條列式，含時間脈絡、討論議題、決議事項）

## ✅ 重點摘要
（5–10 點，每點一句話，條列式）

逐字稿：
{transcript}
"""


def check_file_size(filepath: Path) -> None:
    size_mb = filepath.stat().st_size / (1024 * 1024)
    if size_mb > WHISPER_MAX_MB:
        print(f"⚠️  檔案大小 {size_mb:.1f} MB，超過 Whisper {WHISPER_MAX_MB} MB 限制。")
        print("建議先用 ffmpeg 分割：")
        print(f"  ffmpeg -i {filepath} -f segment -segment_time 600 -c copy chunk_%03d.mp3")
        sys.exit(1)


def transcribe(filepath: Path) -> str:
    print(f"🎙️  Whisper 轉錄中：{filepath.name} ...")
    client = OpenAI(api_key=OPENAI_API_KEY)
    with open(filepath, "rb") as f:
        result = client.audio.transcriptions.create(
            model=WHISPER_MODEL,
            file=f,
            response_format="text",
            language="zh",          # 中文優先；若混語可改 "null" 自動偵測
        )
    print("✅  轉錄完成")
    return result


def summarize(transcript: str) -> str:
    print("🤖  Claude 摘要生成中 ...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        messages=[
            {"role": "user", "content": SUMMARY_PROMPT.format(transcript=transcript)}
        ],
    )
    print("✅  摘要完成")
    return message.content[0].text


def save_output(source: Path, transcript: str, summary: str) -> None:
    stem = source.stem
    out_dir = source.parent

    transcript_path = out_dir / f"{stem}_transcript.txt"
    summary_path    = out_dir / f"{stem}_summary.md"

    transcript_path.write_text(transcript, encoding="utf-8")
    summary_path.write_text(summary, encoding="utf-8")

    print(f"\n📄  逐字稿 → {transcript_path}")
    print(f"📝  摘要   → {summary_path}")


def main():
    if len(sys.argv) < 2:
        print("使用方式：python summarize_local.py <音訊檔路徑>")
        print("範例：    python summarize_local.py meeting.mp3")
        sys.exit(1)

    filepath = Path(sys.argv[1]).resolve()
    if not filepath.exists():
        print(f"❌  找不到檔案：{filepath}")
        sys.exit(1)

    check_file_size(filepath)
    transcript = transcribe(filepath)
    summary    = summarize(transcript)
    save_output(filepath, transcript, summary)
    print("\n🎉  全部完成！")


if __name__ == "__main__":
    main()
