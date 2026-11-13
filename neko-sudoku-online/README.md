# 貓咪數獨多裝置共用版

這個資料夾是可直接部署的 Flask 網站版本。成績由伺服器共用，因此同一網址下的不同手機、平板與電腦會看到同一份排行榜。

支援兩種資料庫模式：

- 本機與 Render：SQLite
- Vercel：Turso 遠端 SQLite

## 本機測試

1. 安裝 Python 3.10 以上。
2. 雙擊 `start.bat`。
3. 瀏覽器會開啟 `http://127.0.0.1:8000/`。

同一區域網路的手機可連到：

```text
http://電腦的區域網路IP:8000/
```

例如 `http://192.168.1.20:8000/`。若手機連不上，請允許 Windows 防火牆的 Python 或連接埠 8000。

## 部署到 Vercel + Turso

Vercel 的 Serverless 檔案系統不持久，不能直接使用本機 SQLite。此版本會在設定 `TURSO_DATABASE_URL` 後改成連線 Turso。

建議使用 Vercel Marketplace 安裝 Turso；Windows 的 Turso Cloud CLI 需要 WSL，Marketplace 可直接建立資料庫並自動帶入環境變數。

1. 從 `neko-sudoku-online` 資料夾登入並建立 Vercel 專案：

```bash
npx vercel login
npx vercel
```

第一次部署若尚未接上 Turso，會暫存到 `/tmp`，重啟後成績會消失；接上 Turso 後重新部署才會永久保存。

2. 到 Vercel Dashboard → 你的專案 → Marketplace / Storage → Turso → Install，建立資料庫並 Connect Project。整合會自動加入：

```text
TURSO_DATABASE_URL
TURSO_AUTH_TOKEN
```

也可手動在 Project Settings → Environment Variables 加入：

```text
APP_TIMEZONE_OFFSET_HOURS=8
TRUST_PROXY=1
```

3. 回到 `neko-sudoku-online` 資料夾重新部署：

```bash
npx vercel --prod
```

4. 開啟 `https://你的網址/api/health`，確認回傳包含 `"storage":"turso"`。`public/index.html` 會由 Vercel CDN 提供，`app.py` 處理 `/api/*`，資料表會在第一次啟動時自動建立。

若 `/api/*` 回傳 404，代表 Vercel 沒有辨識到 Flask。請在 Project Settings → Framework Preset 選 **Flask**，或在專案資料夾執行 `npx vercel project update --framework flask --yes`，再執行 `npx vercel --prod`。

Vercel 不支援直接上傳 ZIP；請使用 CLI 或 GitHub 部署。

注意：Vercel Hobby 方案以供個人非商業使用為主；店內正式營運請確認方案條款。Turso 免費額度與限制請以官方控制台為準。

## 部署到 Render

資料夾已包含 `Dockerfile`、`Procfile`、`render.yaml`。Render 版預設使用本機 SQLite。

1. 將整個 `neko-sudoku-online` 資料夾上傳到 GitHub。
2. 在 Render 建立 Blueprint 或 Web Service，使用此資料夾。
3. 確認已掛載持久化磁碟，且 `DATABASE_PATH=/var/data/neko-sudoku.db`。
4. 部署後以 Render 提供的網址開啟。

`render.yaml` 使用 `starter` 方案與 1GB 磁碟。免費方案沒有持久化磁碟，重新部署、重啟或閒置休眠後成績會消失。

若 Render 也要改用 Turso，設定 `TURSO_DATABASE_URL` 與 `TURSO_AUTH_TOKEN` 即可，不需要掛載 SQLite 磁碟。

## 資料與行為

- 本機與 Render 的成績存在 `data/neko-sudoku.db`；Vercel 的成績存在 Turso。
- 設定頁顯示該題目的全站前 3 名。
- 完成挑戰後顯示該題目的全站前 5 名與總排名。
- 「觀看所有即時挑戰結果」顯示伺服器當日全部成績。
- 結果頁開啟時每 5 秒自動更新，切換回分頁時也會立即更新。
- 玩家名字、主題、盤面大小與當日挑戰次數仍存在各自瀏覽器。
- 舊版存在瀏覽器 localStorage 的成績不會自動上傳到新網站。
- 目前沒有帳號與管理後台，玩家名字會公開顯示；請避免輸入敏感資料。

## API

- `GET /api/health`：健康檢查，回傳 `ok`、`date`、`storage`。
- `GET /api/scores?key=4-1&limit=3`：指定題目的全站排名。
- `GET /api/scores/today`：今日全部題目的成績。
- `POST /api/challenge/start`：開始計時挑戰，JSON 欄位為 `key`、`name`；回傳 `session_id` 與伺服器開始時間。
- `POST /api/scores`：新增成績，JSON 欄位為 `key`、`name`、`session`、`attempt`、`mistakes`。完成時間由伺服器依 `session` 的開始時間計算，不再接受前端傳入的 `time`。

## 備份

本機與 Render 的 SQLite 資料庫檔案：

```text
data/neko-sudoku.db
```

SQLite 使用 WAL 模式時，備份前建議先停止服務，或同時備份 `.db`、`.db-wal`、`.db-shm`。

Vercel 版請使用 Turso 控制台或 Turso CLI 備份雲端資料庫。
