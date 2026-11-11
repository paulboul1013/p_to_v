# p_to_v：簡報 → 配音影片

一個 [Claude Code](https://claude.com/claude-code) skill，把 PowerPoint 簡報（.pptx）做成 1080p 講解影片：

- 女聲配音（edge-tts `zh-TW-HsiaoChenNeural`），一句一句合成，句子之間有自然停頓；線上服務失效時，自動改用離線的 Windows 內建聲音
- 與配音同步的燒錄字幕，另外輸出 `.srt` 字幕檔和章節列表
- 每頁緩動的推近、拉遠或平移，頁與頁之間有轉場
- **畫面跟著講稿走**：在講稿裡寫 `[[show:...]]` 讓條列逐項出現，寫 `[[focus:...]]` 打亮正在講的地方（可以指定圖形代號，也可以指定座標區域）
- 片頭標題卡、進度條、音量標準化、自動品質檢查

## 需求

- Windows + Microsoft PowerPoint（用 COM 匯出投影片）
- ffmpeg / ffprobe（需要在 PATH 裡）
- Python 3.10+，套件：`pip install python-pptx edge-tts Pillow`
- 微軟正黑體（Windows 內建）

## 安裝成 Claude Code skill

```bash
git clone https://github.com/paulboul1013/p_to_v.git "%USERPROFILE%\.claude\skills\p_to_v"
```

安裝後，對 Claude 說「把 xxx.pptx 做成影片」，或輸入 `/p_to_v` 就會使用。

## 直接用指令執行

```bash
python scripts/p_to_v.py --check
python scripts/p_to_v.py 簡報.pptx --stop-after script
python scripts/p_to_v.py 簡報.pptx --start-from tts --title "主標題"
```

1. 第一行是事前檢查，確認需要的工具都已安裝。
2. 第二行匯出投影片並產生講稿草稿 `script.json`。完成後，自己（或讓 Claude）潤飾 `script.json`，需要的話加上視覺標記。
3. 第三行合成配音並產出影片。

完整流程、標記語法和參數說明見 [SKILL.md](SKILL.md)。
