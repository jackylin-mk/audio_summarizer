#!/usr/bin/env python3
"""
腳本三：自動監控 Google Drive 資料夾 → 偵測新音訊 → 轉錄 → 摘要 → 存回 Drive
設計用於 GitHub Actions 每小時排程執行

流程：
  1. 讀取 processed_files.json（已處理清單，存在 Drive 上）
  2. 掃描指定 Drive 資料夾，找出未處理的音訊檔
  3. 每個新檔：下載 → 自動分割（>25MB）→ Whisper 轉錄 → GPT 摘要
  4. 將逐字稿與摘要上傳回 Drive 同資料夾
  5. 更新 processed_files.json

環境變數（GitHub Actions Secrets）：
  OPENAI_API_KEY        - OpenAI API Key
  GDRIVE_TOKEN_JSON     - Google OAuth token.json 的完整內容（JSON 字串）
  GDRIVE_FOLDER_ID      - 要監控的 Drive 資料夾 ID
"""

import os
import io
import json
import subprocess
import shutil
import tempfile
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaInMemoryUpload

from openai import OpenAI

# ── 設定 ──────────────────────────────────────────────
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
GDRIVE_TOKEN_JSON = os.environ["GDRIVE_TOKEN_JSON"]   # token.json 的 JSON 字串
FOLDER_ID         = os.environ["GDRIVE_FOLDER_ID"]    # 監控的資料夾 ID

WHISPER_MODEL  = "whisper-1"
GPT_MODEL      = "gpt-4o-mini"
MAX_TOKENS     = 4096
WHISPER_MAX_MB = 25

PROCESSED_FILENAME = "processed_files.json"   # 存在 Drive 上的已處理清單

AUDIO_MIME_TYPES = {
    "audio/mpeg", "audio/mp4", "audio/x-m4a",
    "audio/wav", "audio/webm", "audio/ogg",
    "video/mp4",   # 有些錄音會被識別成 video/mp4
}
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


# ── Google Drive 授權 ─────────────────────────────────

def get_drive_service():
    """從環境變數中的 token JSON 字串建立 Drive service"""
    token_data = json.loads(GDRIVE_TOKEN_JSON)
    creds = Credentials.from_authorized_user_info(token_data)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("drive", "v3", credentials=creds)


# ── 已處理清單（存在 Drive 上） ───────────────────────

def load_processed(service) -> dict:
    """從 Drive 資料夾讀取 processed_files.json，回傳 {file_id: filename}"""
    results = service.files().list(
        q=f"'{FOLDER_ID}' in parents and name='{PROCESSED_FILENAME}' and trashed=false",
        fields="files(id, name)"
    ).execute()
    files = results.get("files", [])
    if not files:
        return {}
    file_id = files[0]["id"]
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return json.loads(buf.getvalue().decode("utf-8"))


def save_processed(service, processed: dict) -> None:
    """將 processed_files.json 更新回 Drive"""
    content = json.dumps(processed, ensure_ascii=False, indent=2).encode("utf-8")
    media   = MediaInMemoryUpload(content, mimetype="application/json")

    # 找現有檔案 ID（更新用）
    results = service.files().list(
        q=f"'{FOLDER_ID}' in parents and name='{PROCESSED_FILENAME}' and trashed=false",
        fields="files(id)"
    ).execute()
    files = results.get("files", [])

    if files:
        service.files().update(fileId=files[0]["id"], media_body=media).execute()
    else:
        service.files().create(
            body={"name": PROCESSED_FILENAME, "parents": [FOLDER_ID]},
            media_body=media
        ).execute()
    print(f"✅  已更新 {PROCESSED_FILENAME}")


# ── 掃描新檔 ──────────────────────────────────────────

def list_new_audio_files(service, processed: dict) -> list[dict]:
    """列出資料夾內未處理的音訊檔"""
    results = service.files().list(
        q=f"'{FOLDER_ID}' in parents and trashed=false",
        fields="files(id, name, mimeType, size)",
        orderBy="createdTime desc",
        pageSize=50
    ).execute()
    all_files = results.get("files", [])
    new_files = [
        f for f in all_files
        if f["id"] not in processed
        and f.get("mimeType", "") in AUDIO_MIME_TYPES
    ]
    print(f"📂  資料夾共 {len(all_files)} 個檔案，發現 {len(new_files)} 個未處理音訊")
    return new_files


# ── 下載 ─────────────────────────────────────────────

def download_file(service, file_meta: dict) -> tuple[bytes, str, float]:
    """下載 Drive 檔案，回傳 (bytes, 檔名, 大小MB)"""
    file_id  = file_meta["id"]
    filename = file_meta["name"]
    size_mb  = int(file_meta.get("size", 0)) / (1024 * 1024)
    print(f"⬇️   下載：{filename}（{size_mb:.1f} MB）")
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    print("✅  下載完成")
    return buf.getvalue(), filename, size_mb


# ── 分割 + 轉錄 ───────────────────────────────────────

def split_audio(input_path: str, suffix: str, tmp_dir: str) -> list[str]:
    print("✂️   自動分割（每段 10 分鐘）...")
    pattern = os.path.join(tmp_dir, f"chunk_%03d{suffix}")
    cmd = [
        "ffmpeg", "-i", input_path,
        "-f", "segment", "-segment_time", "600",
        "-c", "copy", "-y", pattern
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 分割失敗：{result.stderr}")
    chunks = sorted(Path(tmp_dir).glob(f"chunk_*{suffix}"))
    print(f"✅  分割完成，共 {len(chunks)} 段")
    return [str(c) for c in chunks]


def transcribe(audio_bytes: bytes, filename: str, size_mb: float) -> str:
    suffix  = Path(filename).suffix or ".mp3"
    client  = OpenAI(api_key=OPENAI_API_KEY)
    tmp_dir = tempfile.mkdtemp()

    try:
        tmp_path = os.path.join(tmp_dir, f"original{suffix}")
        with open(tmp_path, "wb") as f:
            f.write(audio_bytes)

        chunk_paths = split_audio(tmp_path, suffix, tmp_dir) if size_mb > WHISPER_MAX_MB else [tmp_path]

        transcripts = []
        for i, chunk_path in enumerate(chunk_paths, 1):
            chunk_mb = os.path.getsize(chunk_path) / (1024 * 1024)
            print(f"🎙️  Whisper 轉錄第 {i}/{len(chunk_paths)} 段（{chunk_mb:.1f} MB）...")
            with open(chunk_path, "rb") as f:
                result = client.audio.transcriptions.create(
                    model=WHISPER_MODEL,
                    file=f,
                    response_format="text",
                    language="zh",
                )
            transcripts.append(result)

        print("✅  轉錄完成")
        return "\n".join(transcripts)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── 摘要 ─────────────────────────────────────────────

def summarize(transcript: str) -> str:
    print("🤖  GPT 摘要生成中 ...")
    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=GPT_MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": SUMMARY_PROMPT.format(transcript=transcript)}],
    )
    print("✅  摘要完成")
    return response.choices[0].message.content


# ── 上傳回 Drive ──────────────────────────────────────

def upload_text(service, content: str, filename: str, mimetype: str) -> None:
    """上傳文字內容到 Drive 資料夾"""
    media = MediaInMemoryUpload(content.encode("utf-8"), mimetype=mimetype)
    service.files().create(
        body={"name": filename, "parents": [FOLDER_ID]},
        media_body=media
    ).execute()
    print(f"☁️   已上傳：{filename}")


# ── 主流程 ────────────────────────────────────────────

def main():
    print("=" * 50)
    print("🔍  開始掃描 Google Drive 資料夾...")
    service   = get_drive_service()
    processed = load_processed(service)
    new_files = list_new_audio_files(service, processed)

    if not new_files:
        print("✨  沒有新檔案，本次結束。")
        return

    for file_meta in new_files:
        filename = file_meta["name"]
        file_id  = file_meta["id"]
        stem     = Path(filename).stem
        print(f"\n{'─'*40}")
        print(f"🎬  處理：{filename}")

        try:
            audio_bytes, _, size_mb = download_file(service, file_meta)
            transcript = transcribe(audio_bytes, filename, size_mb)
            summary    = summarize(transcript)

            upload_text(service, transcript, f"{stem}_transcript.txt", "text/plain")
            upload_text(service, summary,    f"{stem}_summary.md",     "text/markdown")

            processed[file_id] = filename
            save_processed(service, processed)
            print(f"🎉  {filename} 處理完成！")

        except Exception as e:
            print(f"❌  處理 {filename} 時發生錯誤：{e}")
            # 不中斷，繼續處理下一個檔案

    print("\n" + "=" * 50)
    print("✅  本次掃描完成。")


if __name__ == "__main__":
    main()
