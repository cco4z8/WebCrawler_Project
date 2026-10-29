# 桃園市觀音區地方知識爬蟲

本專案將指定來源整理為可重跑的網頁與影片資料擷取流程，保留來源網址、擷取狀態和失敗原因。程式會遵守 `robots.txt`、限制延伸探索範圍，不會嘗試繞過登入、付費牆或影音平台限制。網站結構不同，正文、圖片和影片資料仍需依報告中的人工檢查方式抽查。

## 環境與執行

需要 Python 3.10 或更新版本。先執行 `python --version` 確認版本。若顯示低於 3.10，請改用已安裝的新版 Python 完整路徑。此工作環境的 `python` 目前指向舊版 Anaconda 3.5.2；可在這台電腦使用 Codex 附帶的 Python 3.12：

CMD（此電腦可直接使用）：

```bat
set "PY=C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
"%PY%" --version
"%PY%" -m venv .venv
.\.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python crawler\crawler.py --discover --clean-output
```

PowerShell（此電腦可直接使用）：

```powershell
$Py = 'C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $Py --version
& $Py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python crawler\crawler.py --discover --clean-output
```

請先在命令列切換到專案資料夾；若你的電腦沒有上述 Codex Python 路徑，請安裝 Python 3.10+，再以該版本建立虛擬環境。

依 `urls.csv` 處理 31 個去重後的來源。完整作業執行請使用 `--discover` 啟用同網域一層延伸探索（每個起始頁最多挑 2 個相關連結，總新增上限 20 筆）。`--clean-output` 會先清除 articles、images、videos 目錄中的舊輸出，避免重跑後新舊資料混在一起；只對這些爬蟲輸出目錄使用，請先備份自己另外放進去的檔案。

```powershell
python crawler\crawler.py --discover --clean-output
```

只記錄圖片候選、不下載圖片：

```powershell
python crawler\crawler.py --no-images --clean-output
```

若要單獨示範一筆網址、保留正式輸出，使用 `--urls` 指定小型 CSV，再用 `--output-dir demo_output` 將測試結果寫到獨立目錄。

## 輸出

- `output/index.jsonl`：每筆網頁或影片的統一 JSON 資料，含正文、連結、圖片候選與下載結果、分類、關鍵字、重複線索、狀態和時間。
- `output/crawl_report.csv`：所有起始來源及延伸來源的成功、部分成功或失敗紀錄。
- `output/articles/`、`output/videos/`：逐筆 Markdown 整理稿。
- `output/images/`：成功下載的圖片；JSON 內保留來源 URL、說明和 SHA-256。下載前依 robots 規則檢查，僅保存一般圖片格式且小於 12 MB 的素材。
- `report/screenshots/`：放入至少三種網站類型的原始頁面截圖，與相同來源的清理後 Markdown 對照。

## 資料限制

影片流程只透過 YouTube oEmbed 取得公開基本資料，不下載影片或擷取畫面。字幕與代表畫面欄位保留供合法取得字幕或素材時補充；無法取得時，狀態會清楚說明。網站可能阻擋自動存取；錯誤紀錄即為該次執行的結果，不代表網站內容本身不存在。

分類、關鍵字和相似文章提示由簡單規則產生，屬於整理輔助，應人工檢查。事實查核需在 `report/report.md` 登錄敘述、可信來源、查核結果與日期；參考網址本身不能自動視為權威證據。

