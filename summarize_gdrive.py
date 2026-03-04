#!/usr/bin/env python3
"""
腳本二：Google Drive 音訊檔 → Whisper 轉錄 → Claude 摘要
使用方式：python summarize_gdrive.py <Google Drive 檔案ID或分享連結>

前置需求：
  1. Google Cloud Console 建立 OAuth 憑證（credentials.json）
  2. pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib openai anthropic
  3. 本機安裝 ffmpeg（檔案超過 25MB 時自動分割用）
     - Windows：https://ffmpeg.org/download.html
     - macOS：  brew install ffmpeg
     - Linux：  sudo apt install ffmpeg
"""

import sys
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from openai import OpenAI
import anthropic

# ── 設定 ──────────────────────────────────────────────
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY",    "your_openai_api_key")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "your_anthropic_api_key")

CREDENTIALS_FILE  = "credentials.json"
TOKEN_FILE        = "token.json"

WHISPER_MODEL     = "whisper-1"
CLAUDE_MODEL      = "claude-sonnet-4-20250514"
MAX_TOKENS        = 4096

WHISPER_MAX_MB    = 24          # 保留 1MB 緩衝（Whisper 上限 25MB）
CHUNK_SECONDS     = 600         # 每段 10 分鐘（可自行調整）

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
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


def extract_file_id(input_str: str) -> str:
    if "drive.google.com" in input_str:
        import re
        m = re.search(r"/d/([a-zA-Z0-9_-]+)", input_str)
        if m:
            return m.group(1)
        m = re.search(r"id=([a-zA-Z0-9_-]+)", input_str)
        if m:
            return m.group(1)
        raise ValueError("無法從連結解析檔案 ID，請直接貼上 ID")
    return input_str.strip()


def get_drive_service():
    creds = None
    if Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path(CREDENTIALS_FILE).exists():
                print(f"❌  找不到 {CREDENTIALS_FILE}")
                print("請至 Google Cloud Console 下載 OAuth 憑證並放在同目錄")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        Path(TOKEN_FILE).write_text(creds.to_json())
    return build("drive", "v3", credentials=creds)


def download_from_drive(service, file_id: str, tmp_dir: str) -> tuple[str, str]:
    meta = service.files().get(fileId=file_id, fields="name,mimeType,size").execute()
    filename = meta["name"]
    size_mb  = int(meta.get("size", 0)) / (1024 * 1024)
    print(f"📁  找到檔案：{filename}（{size_mb:.1f} MB）")

    local_path = os.path.join(tmp_dir, filename)
    print("⬇️   下載中 ...")
    request = service.files().get_media(fileId=file_id)
    with open(local_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    print("✅  下載完成")
    return local_path, filename


def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def split_audio(input_path: str, tmp_dir: str, suffix: str) -> list[str]:
    print(f"✂️   檔案超過 {WHISPER_MAX_MB}MB，使用 ffmpeg 自動分割（每段 {CHUNK_SECONDS//60} 分鐘）...")

    if not check_ffmpeg():
        print("❌  找不到 ffmpeg，請先安裝：")
        print("    Windows：https://ffmpeg.org/download.html")
        print("    macOS：  brew install ffmpeg")
        print("    Linux：  sudo apt install ffmpeg")
        sys.exit(1)

    pattern = os.path.join(tmp_dir, f"chunk_%03d{suffix}")
    cmd = [
        "ffmpeg", "-i", input_path,
        "-f", "segment",
        "-segment_time", str(CHUNK_SECONDS),
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


def transcribe_file(filepath: str, client: OpenAI) -> str:
    with open(filepath, "rb") as f:
        result = client.audio.transcriptions.create(
            model=WHISPER_MODEL,
            file=f,
            response_format="text",
            language="zh",
        )
    return result


def transcribe(local_path: str, filename: str) -> str:
    client = OpenAI(api_key=OPENAI_API_KEY)
    suffix = Path(filename).suffix or ".mp3"
    size_mb = os.path.getsize(local_path) / (1024 * 1024)

    if size_mb <= WHISPER_MAX_MB:
        print(f"🎙️  Whisper 轉錄中（單檔 {size_mb:.1f} MB）...")
        transcript = transcribe_file(local_path, client)
        print("✅  轉錄完成")
        return transcript

    # 超過上限 → 自動分割
    split_tmp = tempfile.mkdtemp()
    try:
        chunks = split_audio(local_path, split_tmp, suffix)
        transcripts = []
        for i, chunk in enumerate(chunks, 1):
            chunk_mb = os.path.getsize(chunk) / (1024 * 1024)
            print(f"🎙️  轉錄第 {i}/{len(chunks)} 段（{chunk_mb:.1f} MB）...")
            transcripts.append(transcribe_file(chunk, client))
        print("✅  全部分段轉錄完成")
        return "\n".join(transcripts)
    finally:
        shutil.rmtree(split_tmp, ignore_errors=True)


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


def save_output(filename: str, transcript: str, summary: str) -> None:
    stem = Path(filename).stem
    out_dir = Path(".")

    transcript_path = out_dir / f"{stem}_transcript.txt"
    summary_path    = out_dir / f"{stem}_summary.md"

    transcript_path.write_text(transcript, encoding="utf-8")
    summary_path.write_text(summary, encoding="utf-8")

    print(f"\n📄  逐字稿 → {transcript_path}")
    print(f"📝  摘要   → {summary_path}")


def main():
    if len(sys.argv) < 2:
        print("使用方式：python summarize_gdrive.py <Drive檔案ID或分享連結>")
        print("範例：    python summarize_gdrive.py 1aBcDeFgHiJkLmNoPqRsTuVwXyZ")
        sys.exit(1)

    file_id = extract_file_id(sys.argv[1])
    service = get_drive_service()

    tmp_dir = tempfile.mkdtemp()
    try:
        local_path, filename = download_from_drive(service, file_id, tmp_dir)
        transcript = transcribe(local_path, filename)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    summary = summarize(transcript)
    save_output(filename, transcript, summary)
    print("\n🎉  全部完成！")


if __name__ == "__main__":
    main()
