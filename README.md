# 🎙️ 錄音摘要自動化工具

透過 OpenAI Whisper 轉錄音訊、Claude API 生成結構化摘要，支援本地檔案與 Google Drive 兩種來源。

---

## 📁 檔案說明

| 檔案 | 說明 |
|------|------|
| `summarize_local.py` | 本地音訊檔 → 轉錄 → 摘要 |
| `summarize_gdrive.py` | Google Drive 音訊檔 → 轉錄 → 摘要 |

---

## ⚙️ 環境需求

- Python 3.8+
- OpenAI API Key（用於 Whisper 轉錄 + GPT-4o-mini 摘要）

---

## 🚀 安裝步驟

### 1. 安裝共用套件

```bash
pip install openai
```

### 2. 設定 API Key

**macOS / Linux**
```bash
export OPENAI_API_KEY=your_openai_api_key

```

**Windows CMD**
```cmd
set OPENAI_API_KEY=your_openai_api_key

```

**Windows PowerShell**
```powershell
$env:OPENAI_API_KEY="your_openai_api_key"

```

**`.env` 檔案方式（推薦，永久生效）**

1. 安裝 `python-dotenv`：
```bash
pip install python-dotenv
```

2. 在腳本同目錄建立 `.env` 檔案：
```env
OPENAI_API_KEY=your_openai_api_key
```

3. 在腳本開頭加入（兩個腳本皆適用）：
```python
from dotenv import load_dotenv
load_dotenv()
```

> ⚠️ 請勿將 `.env` 檔案上傳至 Git，建議加入 `.gitignore`

---

## 🗂️ 腳本一：本地音訊檔

### 額外安裝
無需額外套件。

### 使用方式

```bash
python summarize_local.py <音訊檔路徑>
```

**範例：**
```bash
python summarize_local.py meeting.mp3
```

### 支援格式
`mp3` / `mp4` / `m4a` / `wav` / `webm`（Whisper 支援的所有格式）

### 注意事項
- 單檔上限 **25MB**（Whisper API 限制）
- 超過 25MB 請先用 ffmpeg 分割：
```bash
ffmpeg -i input.mp3 -f segment -segment_time 600 -c copy chunk_%03d.mp3
```

---

## ☁️ 腳本二：Google Drive 音訊檔

### 額外安裝

```bash
pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib openai
```

### 前置設定：Google OAuth 憑證

1. 前往 [Google Cloud Console](https://console.cloud.google.com/)
2. 建立新專案（或選擇現有專案）
3. 啟用 **Google Drive API**
4. 前往「憑證」→「建立憑證」→「OAuth 用戶端 ID」
5. 應用程式類型選「桌面應用程式」
6. 下載 `credentials.json`，放在腳本同目錄

> 首次執行時會自動開啟瀏覽器進行授權，授權後產生 `token.json` 供後續使用，無需重複授權。

### 使用方式

```bash
python summarize_gdrive.py <Drive 檔案 ID 或分享連結>
```

**範例（分享連結）：**
```bash
python summarize_gdrive.py "https://drive.google.com/file/d/1aBcDeFgHiJkL/view"
```

**範例（純檔案 ID）：**
```bash
python summarize_gdrive.py 1aBcDeFgHiJkL
```

### 注意事項
- 同樣受 Whisper **25MB** 限制，超過會提示並中止
- Drive 檔案需有「知道連結的人可以查看」權限，或已授權帳號有存取權

---

## 📄 輸出結果

兩個腳本執行後都會在**相同目錄**產生以下兩個檔案：

| 檔案 | 內容 |
|------|------|
| `檔名_transcript.txt` | Whisper 完整逐字稿 |
| `檔名_summary.md` | Claude 結構化摘要 |

### 摘要格式範例

```markdown
## 📋 會議記錄
- 討論 Q2 行銷預算分配，決議增加數位廣告比例
- 確認產品上線時程為 5 月底
- ...

## ✅ 重點摘要
- 數位廣告預算提升至總預算 40%
- 產品預計 5 月底上線，需提前兩週完成 QA
- ...
```

---

## 🌐 語言設定

預設轉錄語言為**中文**（`language="zh"`）。如需調整，修改腳本中的以下參數：

```python
# 自動偵測語言
language=None

# 英文
language="en"
```

---

## 🔑 API Key 取得方式

| 服務 | 申請連結 |
|------|---------|
| OpenAI（Whisper） | https://platform.openai.com/api-keys |




---

## 💰 費用估算

Whisper 為**雲端 API**，無需在本機安裝任何模型，`pip install openai` 即可使用。

以一段 **60 分鐘會議錄音**為例：

| 項目 | 用量 | 單價 | 費用 |
|------|------|------|------|
| Whisper API | 60 分鐘 | $0.006 / 分鐘 | $0.36 |
| GPT-4o-mini（輸入） | 約 10,000 tokens | $0.15 / 1M tokens | $0.0015 |
| GPT-4o-mini（輸出） | 約 1,000 tokens | $0.60 / 1M tokens | $0.0006 |
| **合計** | | | **≈ $0.36（約台幣 12 元）** |

> Whisper 轉錄費用佔大宗，Claude 摘要部分費用極低。

---

## 🤖 腳本三：自動監控 Google Drive（GitHub Actions）

### 流程

```
每小時 GitHub Actions 觸發
  → 掃描指定 Drive 資料夾
  → 比對 processed_files.json（已處理清單）
  → 發現新音訊 → 下載 → 自動分割（>25MB）→ Whisper 轉錄 → Claude 摘要
  → 逐字稿 + 摘要上傳回同資料夾
  → 更新 processed_files.json
```

### 步驟一：取得 Google OAuth Token

在**本機**執行一次 `summarize_gdrive.py` 完成授權，產生 `token.json`，然後將其內容複製備用：

```bash
cat token.json
```

### 步驟二：設定 GitHub Secrets

前往 GitHub Repo → **Settings → Secrets and variables → Actions**，新增以下三個 Secret：

| Secret 名稱 | 內容 |
|-------------|------|
| `OPENAI_API_KEY` | OpenAI API Key |
| `GDRIVE_TOKEN_JSON` | `token.json` 的完整 JSON 內容（貼上整段） |
| `GDRIVE_FOLDER_ID` | 要監控的 Drive 資料夾 ID |

> **如何取得資料夾 ID？**
> 在 Google Drive 開啟目標資料夾，網址列中 `folders/` 後面的字串即為資料夾 ID。
> 例如：`https://drive.google.com/drive/folders/1aBcDeFgHiJkL` → ID 為 `1aBcDeFgHiJkL`

### 步驟三：上傳檔案到 GitHub Repo

確保 Repo 中包含：
```
watch_gdrive.py
.github/workflows/watch.yml
```

### 步驟四：手動測試觸發

前往 GitHub Repo → **Actions → 🎙️ Google Drive 錄音自動摘要 → Run workflow**，確認流程正常運作後，排程會每小時自動執行。

### 輸出結果

處理完成後，Drive 資料夾內會新增：

| 檔案 | 內容 |
|------|------|
| `檔名_transcript.txt` | Whisper 完整逐字稿 |
| `檔名_summary.md` | Claude 結構化摘要 |
| `processed_files.json` | 已處理清單（自動維護，請勿手動刪除） |

### GitHub Actions 免費額度

| 方案 | 每月免費分鐘數 |
|------|--------------|
| 免費帳號 | 2,000 分鐘 |
| Pro | 3,000 分鐘 |

> 每次執行約 5–15 分鐘（視音訊長度），每小時觸發但無新檔時約 1 分鐘即結束，實際用量很低。
