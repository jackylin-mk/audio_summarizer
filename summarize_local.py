#!/usr/bin/env python3
"""
腳本一：本地音訊檔 → Whisper 轉錄 → Claude 摘要
支援自動分割超過 25MB 的音訊檔（需安裝 ffmpeg）

使用方式：python summarize_local.py <音訊檔路徑>

前置需求：
  1. pip install openai anthropic
  2. 安裝 ffmpeg（超過 25MB 時自動分割用）
     - macOS:   brew install ffmpeg
     - Windows: https://ffmpeg.org/download.html 並加入 PATH
     - Ubuntu:  sudo apt install ffmpeg
"""

import sys
import os
import subprocess
import shutil
import tempfile
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


def check_ffmpeg() -> bool:
    """檢查 ffmpeg 是否已安裝"""
    return shutil.which("ffmpeg") is not None


def split_audio(filepath: Path, tmp_dir: str) -> list[str]:
    """用 ffmpeg 將音訊分割成 10 分鐘一段，回傳分割檔路徑清單"""
    print("✂️   檔案超過 25MB，使用 ffmpeg 自動分割（每段 10 分鐘）...")
    suffix  = filepath.suffix or ".mp3"
    pattern = os.path.join(tmp_dir, f"chunk_%03d{suffix}")
    cmd = [
        "ffmpeg", "-i", str(filepath),
        "-f", "segment",
        "-segment_time", "600",
        "-c", "copy",
        "-y",
        pattern
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌  ffmpeg 分割失敗：{result.stderr}")
        sys.exit(1)

    chunks = sorted(Path(tmp_dir).glob(f"chunk_*{suffix}"))
    print(f"✅  分割完成，共 {len(chunks)} 段")
    return [str(c) for c in chunks]


def transcribe(filepath: Path) -> str:
    """轉錄音訊；超過 25MB 自動分割後逐段轉錄並合併"""
    size_mb = filepath.stat().st_size / (1024 * 1024)
    client  = OpenAI(api_key=OPENAI_API_KEY)
    tmp_dir = None

    try:
        if size_mb > WHISPER_MAX_MB:
            if not check_ffmpeg():
                print(f"⚠️  檔案 {size_mb:.1f} MB 超過 {WHISPER_MAX_MB} MB 限制")
                print("請安裝 ffmpeg 以啟用自動分割功能：")
                print("  macOS:   brew install ffmpeg")
                print("  Windows: https://ffmpeg.org/download.html")
                print("  Ubuntu:  sudo apt install ffmpeg")
                sys.exit(1)
            tmp_dir     = tempfile.mkdtemp()
            chunk_paths = split_audio(filepath, tmp_dir)
        else:
            chunk_paths = [str(filepath)]

        transcripts = []
        for i, chunk_path in enumerate(chunk_paths, 1):
            chunk_mb = os.path.getsize(chunk_path) / (1024 * 1024)
            print(f"🎙️  Whisper 轉錄第 {i}/{len(chunk_paths)} 段（{chunk_mb:.1f} MB）...")
            with open(chunk_path, "rb") as f:
                result = client.audio.transcriptions.create(
                    model=WHISPER_MODEL,
                    file=f,
                    response_format="text",
                    language="zh",   # 中文優先；若混語可改 None 自動偵測
                )
            transcripts.append(result)

        print("✅  轉錄完成")
        return "\n".join(transcripts)

    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


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

    transcript = transcribe(filepath)
    summary    = summarize(transcript)
    save_output(filepath, transcript, summary)
    print("\n🎉  全部完成！")


if __name__ == "__main__":
    main()
