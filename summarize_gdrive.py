#!/usr/bin/env python3
"""
腳本二：Google Drive 音訊檔 → Whisper 轉錄 → Claude 摘要
使用方式：python summarize_gdrive.py <Google Drive 檔案ID或分享連結>

前置需求：
  1. Google Cloud Console 建立 OAuth 憑證（credentials.json）
  2. pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib openai anthropic
"""

import sys
import os
import io
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

CREDENTIALS_FILE  = "credentials.json"   # Google OAuth 憑證檔
TOKEN_FILE        = "token.json"          # 首次授權後自動產生

WHISPER_MODEL     = "whisper-1"
CLAUDE_MODEL      = "claude-sonnet-4-20250514"
MAX_TOKENS        = 4096

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
    """接受 Drive 檔案 ID 或完整分享連結，回傳純 ID"""
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
    """OAuth 授權並回傳 Drive service（首次執行會開啟瀏覽器授權）"""
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


def download_from_drive(service, file_id: str) -> tuple[bytes, str]:
    """下載 Drive 檔案，回傳 (bytes, 檔名)"""
    meta = service.files().get(fileId=file_id, fields="name,mimeType,size").execute()
    filename = meta["name"]
    size_mb  = int(meta.get("size", 0)) / (1024 * 1024)
    print(f"📁  找到檔案：{filename}（{size_mb:.1f} MB）")

    if size_mb > 25:
        print(f"⚠️  檔案 {size_mb:.1f} MB 超過 Whisper 25 MB 限制，建議先壓縮或分割")
        sys.exit(1)

    print("⬇️   下載中 ...")
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    print("✅  下載完成")
    return buf.getvalue(), filename


def transcribe(audio_bytes: bytes, filename: str) -> str:
    print("🎙️  Whisper 轉錄中 ...")
    suffix = Path(filename).suffix or ".mp3"
    client = OpenAI(api_key=OPENAI_API_KEY)

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=f,
                response_format="text",
                language="zh",
            )
    finally:
        os.unlink(tmp_path)

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
    audio_bytes, filename = download_from_drive(service, file_id)
    transcript = transcribe(audio_bytes, filename)
    summary    = summarize(transcript)
    save_output(filename, transcript, summary)
    print("\n🎉  全部完成！")


if __name__ == "__main__":
    main()
