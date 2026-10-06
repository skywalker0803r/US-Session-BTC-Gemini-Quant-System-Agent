# US-Session BTC Gemini Quant System Agent

本專案為 BTC/USDT 永續合約量化交易代理的安全、可觀測實作基礎。依照系統架構與技術實施計畫，涵蓋市場資料採集、Gemini 2.5 Flash 多模態推理、Gate.io 交易執行、風險控制、定時清倉與稽核紀錄。

> **安全提醒：目前預設僅提供模擬模式，未啟用真實交易。正式上線前必須完成交易所、法規、資金承受與風險審查。**

## 目錄

- [專案目標](#專案目標)
- [前置需求](#前置需求)
- [系統架構](#系統架構)
- [交易規格](#交易規格)
- [風險規則](#風險規則)
- [本機使用](#本機使用)
- [CLI 參數](#cli-參數)
- [GitHub Actions 自動化](#github-actions-自動化)
- [實盤串接](#實盤串接)
- [測試與驗證](#測試與驗證)
- [環境變數](#環境變數)
- [稽核與持倉](#稽核與持倉)
- [常見問題](#常見問題)
- [狀態與驗證標準](#狀態與驗證標準)

## 專案目標

建立一套聚焦美股交易時段的 BTC 量化交易系統，主要目標為：

- 以美國 09:30–16:00（台灣時間約 21:30–04:00）作為交易窗口。
- 使用 Gemini 2.5 Flash 綜合 K 線、期貨、宏觀、新聞、Orderbook 與 CVD。
- 透過嚴格的 JSON 驗證，避免模型自由文字直接控制交易。
- 在確認風險預算與交易所狀態後，才執行交易。
- 透過結構化日誌保存決策、下單、成交、清倉與異常事件，支援回放與稽核。

## 前置需求

- Python 3.11 或更新版本；專案的 GitHub Actions 目前使用 Python 3.14。
- `pip` 與 Git。
- 使用模擬模式時不需要 Gemini 或 Gate.io API 金鑰。
- 使用 Live 模式時，需要 Gemini API 金鑰，以及 Gate.io API 金鑰與 Secret。
- 使用 GitHub Actions 時，必須在 `production` environment 設定 Secrets，並限制可部署者與 Approver。

```bash
python --version
python -m pip --version
```

## 系統架構

- **Data Adapters**：統一市場、新聞、Orderbook 與交易所資料格式，並保留來源與時間戳。
- **Gemini Decision Engine**：使用 Gemini 2.5 Flash 產生決策，並驗證 JSON 欄位、數值範圍與保護單條件。
- **Risk Gate**：檢查信心、波動、止損／止盈、持倉與風險預算。
- **Execution Adapter**：封裝 Gate.io API，支援槓桿設定、下單、保護單與持倉查詢。
- **Scheduler & Reconciler**：執行開盤決策與美國收盤前 10 分鐘的清倉流程。
- **Event Store**：將決策、下單、成交、清倉與錯誤事件寫入可重放的稽核紀錄。

## 交易規格

| 項目 | 規格 |
| --- | --- |
| 交易標的 | Gate.io BTC/USDT:USDT 永續合約 |
| 保證金模式 | Isolated Margin |
| 預設槓桿 | 14x |
| 交易方式 | Market Order |
| 交易窗口 | 美國 09:30–16:00；台灣時間約 21:30–04:00 |
| 決策時點 | 台灣時間 21:45（可配置） |
| 最低信心門檻 | 0.70 |
| 止損範圍 | 0.8%–2.0% |
| 止盈範圍 | 2.0%–4.0% |
| 清倉時間 | 台灣時間 03:50；美國時間 15:50 |

## 風險規則

- 模型信心低於 0.70 時，強制回傳 `HOLD`。
- 認知資料不完整、模型輸出無法驗證或波動過大時，強制回傳 `HOLD`。
- 止損與止盈必須符合規格範圍，且止盈距離必須高於止損距離。
- 下單前必須確認槓桿為 14x、保證金模式為 Isolated Margin，並檢查既有持倉。
- 交易所不支援原子式掛載時，必須提供補掛、失敗減倉或平倉的補償流程。
- 03:50 清倉失敗時，必須升級告警並進入人工介入流程，而不是宣稱零隔夜風險。
- 初始權益的最大損失上限不可直接以 14x 槓桿推算；必須依實際止損距離、合約數量、手續費與滑點計算。

## 本機使用

### 1. 安裝

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

### 2. 啟動模擬模式

```bash
python -m quant_agent.cli --events ./events
```

模擬模式不會呼叫 Gemini、Gate.io 或其他外部服務。它使用固定的模擬市場價格，並將決策、風險判斷與交易操作紀錄到本機稽核目錄。

### 3. 使用指定市場價格

```bash
python -m quant_agent.cli --mode simulate --market-price 50000 --events ./events
```

### 4. 以固定時間執行可重現的時區檢查

```bash
python -m quant_agent.cli --mode simulate --now "2026-10-06T21:45:00+00:00" --events ./events
```

`--now` 只用於測試與排程驗證；實際執行時仍使用目前時間。

## CLI 參數

```text
--events PATH        稽核事件目錄（預設：events）
--market-price FLOAT 模擬市場價格（預設：50000）
--mode {simulate,dry-run,live}
--action {open,close}
--now ISO8601        測試用的固定時間
--position-store PATH
                     管理 Gate.io 位置 ID 的持久化檔案
```

### 模式差異

| 模式 | Gemini | Gate.io | 交易執行 |
| --- | --- | --- | --- |
| `simulate` | 使用內建假決策 | 不連線 | 不執行 |
| `dry-run` | 使用內建假決策 | 不連線 | 不執行，與模擬模式相同 |
| `live` | 使用 Gemini API | 使用 Gate.io API | 需金鑰與 `ALLOW_LIVE_TRADING=true` |

> `dry-run` 目前是程式層級的非實盤模式；它不會建立或修改交易所訂單。

## GitHub Actions 自動化

開盤與清倉工作流位於 `.github/workflows/`：

- 開盤：美國工作日 09:30（America/New_York）執行。
- 清倉：美國收盤前 10 分鐘 15:50（America/New_York）執行。
- 夏令時段對應 UTC 13:30 與 19:50。
- 冬令時段對應 UTC 14:30 與 20:50。
- 每個工作日最多執行兩次，避免每分鐘消耗 GitHub Actions 免費額度。
- CLI 以 `America/New_York` 再次檢查時間，避免固定 UTC cron 造成錯時段執行。
- `workflow_dispatch` 可供人工測試；定時工作流只在 `production` environment 執行。
- 每個工作日最多執行一次開盤與一次清倉，避免每分鐘消耗免費額度。
- `concurrency` 的 `group` 會讓同一工作流不重複執行，並避免取消正在執行的交易流程。
- 工作流會把事件目錄上傳為 30 天的 GitHub Actions Artifact。

### 必要的 GitHub Secrets

在 `production` environment 中建立以下 Secrets：

- `GEMINI_API_KEY`
- `GATEIO_API_KEY`
- `GATEIO_SECRET`

`ALLOW_LIVE_TRADING` 只在 workflow 的 `env` 中設定為 `true`，不應將其Store為可被一般部署者修改的設定。

## 實盤串接

```bash
export GEMINI_API_KEY=...
export GATEIO_API_KEY=...
export GATEIO_SECRET=...
export ALLOW_LIVE_TRADING=true

python -m quant_agent.cli --mode live --action open --events ./events --position-store ./events/positions.json
python -m quant_agent.cli --mode live --action close --events ./events --position-store ./events/positions.json
```

實盤模式會在啟動時檢查所有必要金鑰與開關。開盤前會驗證槓桿、保證金模式、既有持倉、市場價格與風險預算；清倉只會關閉已追蹤的持倉，避免意外平倉其他位置。

> **重要：** 只有程式碼權限並不代表可啟用實盤模式。正式部署前必須完成交易所 Testnet 驗證、環境權限、Approver 與人工風險審查。

## 測試與驗證

```bash
python -m pytest
python -m compileall -q src tests
```

測試涵蓋：

- Gemini 決策 JSON 驗證與信心門檻。
- 風險拒單與 HOLD 安全狀態。
- 槓桿、持倉與數量限制。
- 交易所執行前的安全檢查。
- 結構化日誌的持久化與稽核事件。
- America/New_York 時區與工作窗口。

```bash
python -m pytest
python -m compileall -q src tests
git diff --check
```

## 環境變數

| 變數 | 用途 | 必要性 |
| --- | --- | --- |
| `GEMINI_API_KEY` | Gemini 產生決策 | Live 模式必要 |
| `GATEIO_API_KEY` | Gate.io API 認證 | Live 模式必要 |
| `GATEIO_SECRET` | Gate.io API 認證 | Live 模式必要 |
| `ALLOW_LIVE_TRADING` | 啟用實盤執行 | Live 模式必要，值為 `true`、`1` 或 `yes` |

不要將金鑰寫入程式碼、Shell 歷史、GitHub Artifact 或稽核日誌。金鑰應透過 GitHub Actions Secrets 或安全的本機環境變數來源提供。

## 稽核與持倉

- 事件目錄預設位於 `./events`。
- 每個事件是獨立的 JSON 檔案，可透過 `EventStore` 讀取與重放。
- 開盤流程會記錄 `DECISION`、`RISK_REJECTED`、`EXECUTION_REJECTED`、`ORDER_PLACED` 或 `ORDER_FAILED`。
- 清倉流程會記錄 `CLOSE_ALL` 與失敗事件。
- `--position-store` 會保存已管理的 Gate.io position ID；清倉只處理這些編號。
- 事件與持倉檔案可能包含交易成本或帳戶狀態，請保護其權限並避免上傳到不受控的位置。

## 常見問題

- **缺少 `GEMINI_API_KEY`：** 使用 Live 模式時必須提供金鑰。
- **缺少 `ALLOW_LIVE_TRADING`：** Live 模式會直接停止，避免誤啟用實盤。
- **時段不正確：** CLI 以 `America/New_York` 檢查開盤與清倉窗口；不要只依賴 UTC cron。
- **交易所回傳 401 或 403：** 檢查 API 金鑰、Secret、權限與 API 使用端點。
- **開盤時已有持倉：** `QuantAgent` 會拒絕新單，避免重複開單。
- **清倉沒有結果：** 確認位置 ID 已存在於 `position-store`，並檢查交易所 API 與事件紀錄。

## 主要檔案

- `src/quant_agent/decision.py`：Gemini 決策引擎與 JSON 驗證。
- `src/quant_agent/risk.py`：風險門檻與持倉限制。
- `src/quant_agent/adapters.py`：Gemini 與 Gate.io 介面。
- `src/quant_agent/engine.py`：交易工作流與清倉控制。
- `src/quant_agent/storage.py`：稽核事件持久化。
- `tests/`：單元測試與安全行為驗證。

## 狀態與驗證標準

本專案目前屬於**開發中狀態**，尚未達到正式上線標準。已完成的核心功能包括 Gemini 決策、JSON 驗證、風險控制、Gate.io Futures 介面、時區排程、定時開盤／清倉、持倉追蹤、完整市場資料聚合與稽核日誌；部分交易執行與觀測功能仍需補齊。

> **正式上線狀態：未啟用。** 實盤交易仍需完成 Gate.io Testnet 驗證、GitHub production environment 設定及人工審查。程式內建的安全 gate 不等於交易所 API 的端到端驗證。

目前已完成的技術驗收：

1. 資料可重現，並聚合 BTC K 線、Orderbook、CVD、VIX、DXY、NQ／ES 與新聞。
2. 決策可驗證。
3. 風險可拒絕。
4. 保護單可回讀。
5. 清倉請求符合 `reduceOnly` 合約。
6. 完整交易事件鏈可追蹤開單或清倉的請求 payload、交易所回應與最終結果。
7. 異常可告警。
8. Gate.io 清倉請求的有界重試，支援 429、500、502、503 與 504；4xx 會立即失敗。
9. 清倉失敗時的重試上限、錯誤事件與人工介入流程；失敗的 position ID 會保留供下一次排程重試。
10. 清倉成功後會移除已管理的 position ID。

目前尚未完成的技術驗收：

1. Protection Order 驗證與實際交易所 Testnet 驗證。
2. 長期小資金實盤驗證與正式上線審查。

本專案提供軟體架構與風險控制範例，不構成投資、交易或財務建議。正式使用前，應進行獨立的法規、交易所條款、API 風險及資金承受度審查。
