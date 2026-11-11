---
name: p_to_v
description: 把 PowerPoint 簡報（.pptx）做成有女聲配音、字幕、鏡頭推移與轉場的生動講解影片（.mp4，另附 .srt）。當使用者說「把 xxx.pptx 做成影片」、「簡報轉影片」、「幫投影片配音」、「做成有配音的影片」，或 "pptx to video"、"narrated video from slides"、"slides to mp4" 時使用。需要 Windows + Microsoft PowerPoint、ffmpeg、Python（python-pptx、edge-tts、Pillow）。
---

# p_to_v：簡報 → 配音影片

把 .pptx 轉成 1080p 講解影片，內容包括：
- 片頭標題卡
- 每頁緩動的推近、拉遠或平移，頁與頁之間輪流使用 10 種轉場
- 女聲配音（預設 edge-tts `zh-TW-HsiaoChenNeural`），一句一句合成，句子之間有自然停頓；線上服務失效時，會自動改用離線的 Windows 聲音
- 與配音同步的燒錄字幕，另外輸出 `.srt` 字幕檔
- 底部進度條、結尾淡出、音量標準化

腳本位置（以下指令都用絕對路徑）：

```
SKILL=C:\Users\USER\.claude\skills\p_to_v
python "%SKILL%\scripts\p_to_v.py" ...
```

## 工作流程（給 Claude 的步驟）

### 0. 預檢

```bash
python "C:/Users/USER/.claude/skills/p_to_v/scripts/p_to_v.py" --check
```

全部顯示 `[OK]` 才繼續；缺什麼，畫面上會提示安裝指令。

### 1. 匯出投影片 + 講稿草稿

```bash
python "C:/Users/USER/.claude/skills/p_to_v/scripts/p_to_v.py" "簡報.pptx" --work "<scratchpad>/p_to_v_<簡報名>" --stop-after script
```

- `--work` 建議放在 scratchpad。沒有指定時，預設是 `%TEMP%\p_to_v\<簡報名>`，實際路徑會在開頭印出。
- 匯出時會用簡報的**副本**，使用者正開著的 PowerPoint 不會被關掉。
- **隱藏投影片會跳過**，輸出時會列出跳過了哪幾頁。
- 4:3 或其他比例的簡報會保留原比例，左右補黑邊。
- 產出：
  - `WORK/slides/slide_001.png …`
  - `WORK/slides_text.json`：每頁的投影片文字和備忘稿，`original_index` 是在原簡報中的頁碼
  - `WORK/script.json`：講稿草稿，一頁一個字串。有備忘稿就用備忘稿，沒有就用投影片上的文字。完全沒有文字的頁面會是空字串，變成 2 秒靜音。
  - `WORK/slides/slides.json`：每頁的標題，以及每個圖形和段落的代號與位置（例如 `S3`、`S3.2`，`box` 是 0–1 的 x,y,w,h）
  - `WORK/grid/slide_001.png …`：投影片疊上 0.1 格線和圖形代號（藍框）。挑選要打亮的區域時，用 Read 看這些圖

### 2. 潤飾講稿（Claude 親自做，最重要）

讀 `slides_text.json`，並用 Read 看幾張 `slides/*.png`，然後改寫 `script.json`：
- **一定要維持 JSON 字串陣列，數量和投影片張數相同**。數量不符時，下一步會直接報錯停下。
- 改成口語：第一頁加開場問候，最後一頁加結語。
- 修正備忘稿裡和實際頁序不符的「下一頁…」轉場句。
- 每句結尾用「。！？；」。配音是一句一句合成的，句號越清楚，停頓和字幕時間就越準。
- 字幕會顯示講稿原文，所以講稿用正常寫法即可；讀音問題交給讀音字典處理（見下方）。
- 每頁大約 30–60 秒（150–300 字）。

### 2b. 加上視覺標記（讓畫面跟著講稿動）

在句子**開頭**插入標記，那句開始唸時就會觸發。標記不會被唸出來，也不會出現在字幕上。

| 標記 | 效果 |
|---|---|
| `[[show:S3]]` | 圖形 S3 在這句才出現（在這之前自動隱藏） |
| `[[show:S3.2]]` | S3 的第 2 段才出現，用來做條列逐項出現（版面不會跳動） |
| `[[show:0.48,0.64,0.23,0.16]]` | 一塊區域（x,y,w,h）才出現：用背景色蓋住，適合整頁是一張圖的投影片 |
| `[[focus:S3.2]]` / `[[focus:0.1,0.2,0.3,0.2]]` | 打亮這個圖形或區域：其餘變暗，加上橘色圓角框，持續到下一個 focus |
| `[[focus:off]]` | 取消打亮；放在最後一句之後，會在那句唸完時觸發 |

- 同一個標記可以放多個項目，用 `|` 分隔：`[[show:S3|S4]]`。
- 範例：`今天講三個重點。[[show:S2.1]][[focus:S2.1]]第一點是資料來源。[[show:S2.2]][[focus:S2.2]]第二點是分析方法。[[focus:off]]`
- **怎麼選**：
  - 一般文字方塊的投影片：用 `slides.json` 裡的段落代號（`S2.1`…）。
  - 內容是整張圖片的投影片（`slides.json` 裡只有一個 `(picture)`）：看 `grid/slide_NNN.png`，從格線讀出座標。
- **不要過度使用**：每頁 0–5 個標記就好。只在講到圖上特定位置、或有條列可以逐項出現時才加。
- 區域型 `show` 是用周圍的顏色蓋住，**範圍要框緊**，不要蓋到旁邊不該隱藏的東西；漸層或照片背景的投影片不要用。
- 標記寫錯（例如代號不存在）時，effects 步驟會印出 `WARNING`，該標記會被略過。

### 3. 先試跑 2 頁（建議）

```bash
python ".../p_to_v.py" "簡報.pptx" --work WORK --start-from tts --slides 2 --out "<scratchpad>/trial.mp4" --title "主標題"
```

注意：`--slides 2` 搭配 `--start-from tts` 時，`script.json` 也只能有 2 個元素。比較簡單的做法是用另一個 WORK，從頭跑 `--slides 2`。

### 4. 正式產出

```bash
python ".../p_to_v.py" "簡報.pptx" --work WORK --start-from tts --title "主標題" --subtitle "副標題" --kicker "實驗室會議報告 · 2026-11-11"
```

- 用背景執行（`run_in_background`），進度會印出 `slide i`、`clip i/n`。
- 輸出位置預設是 pptx 旁邊的 `<檔名>_配音影片.mp4`。**已有同名檔案時不會覆蓋**，會自動改名為 `_2`、`_3`。
- 已經合成過的句子會快取在 `WORK/tts_cache`，只改幾句時重跑很快。
- 速度參考：10 頁約 7 分鐘的影片，總共約 3–5 分鐘（每頁片段是平行算的）。

### 5. 驗證（qa 步驟會自動跑）

流程最後會執行 `qa.py`，印出 `QA: PASS` 或列出問題。它會檢查：
- 影片長度和規劃的時間軸是否一致
- 音量是否接近 -16 LUFS
- 字幕是否超出影片長度

另外會產出兩張圖，**Claude 必須用 Read 看過**：
- `WORK/qa_contact.png`：每頁中間的畫面。檢查字幕、裁切、有沒有黑畫面。
- `WORK/qa_effects.png`：每個視覺效果狀態的畫面。檢查逐項出現的順序對不對、打亮框有沒有框到正確位置、區域遮蓋有沒有蓋到不該蓋的東西。

想要更嚴格時，可以截一段音訊，用 faster-whisper 加上 `word_timestamps=True` 轉文字，對照 `.srt` 的時間。

除了 mp4，還會輸出：
- `<輸出>.srt`：字幕檔
- `<輸出>_chapters.txt`：YouTube 章節列表，直接貼到影片說明就能用
- mp4 內嵌的章節，以投影片標題命名

## 配音引擎與備援

兩個引擎都**不需要金鑰或額外設定**，隨時可用。預設 `--engine auto` 會先試 edge；edge 失效就自動改用 Windows 內建聲音。

| 順序 | 引擎 | 需要什麼 | 聲音 | 說明 |
|---|---|---|---|---|
| 1 | `edge` | 網路 | `zh-TW-HsiaoChenNeural`（`--voice`） | 免費、品質好，但不是官方介面，可能突然失效 |
| 2 | `windows` | 不需要網路，Windows 內建 | `Microsoft Hanhan Desktop`（`--win-voice`），台灣華語女聲 | 一定能用；聲音較機械，但辨識度高 |

- 每個引擎會先用一句話測試，壞掉的幾秒內就會被跳過。
- **發生備援時，會印出 `[tts] WARNING: fell back to 'windows'`**，並記錄在 `WORK/audio/engine.txt`。看到這行，Claude 要告訴使用者「這次用的是離線備援聲音」，以及 edge 為什麼失敗。之後想換回 edge，先執行 `pip install -U edge-tts`，再從 `tts` 步驟重跑。
- **同一支影片只會用一種聲音**：只要有一句失敗，就整批換引擎重做，不會混用兩種聲音。
- **快取**依「引擎＋聲音＋語速＋文字」分開存放，切換引擎不會誤用別的聲音。
- **任何引擎失敗時，前一次的配音都會保留**，不會被清空。
- `--engine windows` 可以完全離線產出影片。
- `python ".../p_to_v.py" --check` 會列出兩個引擎目前的狀態，以及電腦上已安裝的 Windows 聲音。

## 讀音字典（lexicon）

只改「念出來的文字」，字幕不受影響。規則依序套用：
1. skill 內建的 `lexicon.json`：千分位、`30–60%` → `30% 到 60%`、`HP1/HP2` → `HP1 和 HP2`、`Mb/kb/Gb`
2. `WORK/lexicon.json`（這份簡報專用）
3. `--lexicon 檔案.json`

格式是 `[{"pattern": "正規表示式", "replace": "替換文字"}]`。例如想讓縮寫逐字母唸：
`{"pattern": "\\bSNV\\b", "replace": "S N V"}`

## 常用參數

| 參數 | 預設 | 說明 |
|---|---|---|
| `--engine` | `auto` | `edge`／`windows`（見「配音引擎與備援」） |
| `--voice` | `zh-TW-HsiaoChenNeural` | edge 的聲音；其他女聲：`zh-TW-HsiaoYuNeural`、`zh-CN-XiaoxiaoNeural`、`en-US-JennyNeural` |
| `--win-voice` | `Microsoft Hanhan Desktop` | 離線 Windows 聲音 |
| `--rate` | `+8%` | 語速，例如 `+0%`、`-5%` |
| `--title` / `--subtitle` / `--kicker` | 檔名 / 空 / 空 | 片頭卡的主標題、副標題，以及主標題上方的小字 |
| `--no-intro` / `--no-subs` | 關 | 不要片頭卡 / 不燒錄字幕（`.srt` 仍會輸出） |
| `--no-effects` | 關 | 忽略 `[[show]]`、`[[focus]]` 標記 |
| `--zoom` | `0.07` | 推近幅度；內容貼邊的投影片可以調小到 `0.03` |
| `--bgm 音樂.mp3` | 無 | 背景音樂，自動循環並壓低音量 |
| `--encoder` | `libx264` | 有 NVIDIA 顯卡可以用 `h264_nvenc`（`--check` 會列出可用的編碼器） |
| `--jobs` | CPU/2（最多 8） | 同時算幾頁片段 |
| `--slides N` | 全部 | 只做前 N 張可見投影片 |
| `--stop-after` / `--start-from` | — | 步驟：`slides`／`script`／`tts`／`effects`／`clips`／`final`／`qa`。改了講稿或標記就從 `tts` 開始（沒改到的句子會用快取）；只改片頭標題就從 `clips` 開始 |

## 腳本一覽

- `scripts/p_to_v.py`：總控制，包含預檢、步驟控制、講稿數量檢查、不覆蓋舊檔
- `scripts/export_slides.ps1`：用 PowerPoint COM 匯出 PNG。在副本上操作、跳過隱藏頁、保留原比例、PowerPoint 原本沒開才會關掉它
- `scripts/extract_text.py`：抽出可見頁的文字和備忘稿，產生 `script.json` 草稿
- `scripts/tts.py`：一句一句合成，自己控制停頓，套用讀音字典，有快取和重試，引擎失效時自動備援
- `scripts/tts_engines.py`：兩個配音引擎（edge、離線 Windows 聲音）
- `scripts/sapi_tts.ps1`：離線 Windows 語音合成
- `scripts/effects.py`：把 `[[show]]`、`[[focus]]` 標記轉成各個狀態的畫面；`--grids` 會畫出格線輔助圖
- `scripts/export_states.ps1`：用 COM 複製投影片、隱藏指定的圖形或段落後匯出（逐項出現用）
- `scripts/qa.py`：自動品質檢查和縮圖總覽
- `scripts/build.py`：
  - 時間軸以影格為單位計算
  - 平行算出每頁片段，再分批串接轉場
  - 配音做成單一音軌，放在每頁的絕對時間點上，頁數再多也不會漂移
  - 每頁的效果狀態用 0.3 秒淡入淡出切換，鏡頭運動在整頁內保持連續
  - 燒錄 ASS 字幕，並輸出 `.srt` 和章節
- `lexicon.json`：內建讀音規則
- `改進建議.md`：調查報告；第一、二輪已完成，剩下原生 PowerPoint 模式、Remotion

## 注意事項

- 投影片內的動畫和嵌入影片不會保留，每頁只會有最終畫面。
- edge-tts 是非官方介面，可能突然出現 403 錯誤。腳本會自動切換到離線的 Windows 聲音；想恢復原本的聲音，執行 `pip install -U edge-tts` 後從 `tts` 步驟重跑。
- 字幕會蓋住投影片最下方約 100px，內容貼底時用 `--no-subs`。
- 在 Git Bash 下執行時，若中文輸出變亂碼，先設 `PYTHONIOENCODING=utf-8`。
