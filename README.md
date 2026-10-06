# US-Session BTC Gemini Quant System Agent

本專案為 BTC/USDT 永續合約量化交易代理的安全、可觀測實作基礎。依照系統架構與技術實施計畫，涵蓋市場資料採集、Gemini 2.5 Flash 多模態推理、Gate.io 交易執行、風險控制、定時清倉與稽核紀錄。

> **安全提醒：目前預設僅提供模擬／乾跑模式，未啟用真實交易。正式上線前必須完成交易所、法規、資金承受與風險審查。**

## 專案目標

建立一套聚焦美股交易時段的 BTC 量化交易系統，主要目標為：

- 以美國 09:30–16:00（台灣時間約 21:30–04:00）作為交易窗口。
- 使用 Gemini 2.5 Flash 綜合 K 線、期貨、宏觀、新聞、Orderbook 與 CVD。
- 透過嚴格的 JSON Schema 驗證，避免模型自由文字直接控制交易。
- 在確認風險預算與交易所狀態後，才執行交易。
- 透過結構化日誌保存決策、下單、成交、清倉與異常事件，支援回放與稽核。

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

## 啟動模擬模式

```bash
python -m pip install -e '.[dev]'
python -m quant_agent.cli --events ./events
```

預設模擬模式不會呼叫 Gemini、Gate.io 或其他外部服務，並將決策與交易操作記錄到本機稽核目錄。

## GitHub Actions 自動化

開盤與清倉工作流位於 `.github/workflows/`：

- 開盤：美國工作日 09:30（America/New_York）執行。
- 清倉：美國收盤前 10 分鐘 15:50（America/New_York）執行。
- 夏令時段對應 UTC 13:30 與 19:50。
- 冬令時段對應 UTC 14:30 與 20:50。
- 每個工作日最多執行兩次，避免每分鐘消耗 GitHub Actions 免費額度。
- CLI 以 `America/New_York` 再次檢查時間，避免固定 UTC cron 造成錯時段執行。
- `workflow_dispatch` 可供人工測試；定時工作流只在 `production` environment 執行。

## 實盤串接要求

```bash
export GEMINI_API_KEY=...
export GATEIO_API_KEY=...
export GATEIO_SECRET=...
export ALLOW_LIVE_TRADING=true
```

```bash
python -m quant_agent.cli --mode live --action open --events ./events
python -m quant_agent.cli --mode live --action close --events ./events
```

實盤模式要求所有金鑰與 `ALLOW_LIVE_TRADING=true`，並在 GitHub 的 `production` environment 中設定必要的 approver。這樣可以避免只有程式碼權限就能啟用實盤交易。

## 執行測試

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

## 環境變數

實際連線前，可透過環境變數提供必要設定：

```bash
export GEMINI_API_KEY=...
export GATEIO_API_KEY=...
export GATEIO_SECRET=...
```

```bash
# 產生可觀測的事件紀錄
python -m quant_agent.cli --events ./events
```

> 預設 CLI 不會使用以上金鑰。實際交易所適配器應只在受控制的部署環境中啟用，並禁止將金鑰寫入原始碼或稽核日誌。

## 主要檔案

- `src/quant_agent/decision.py`：Gemini 決策引擎與 JSON 驗證。
- `src/quant_agent/risk.py`：風險門檻與持倉限制。
- `src/quant_agent/adapters.py`：Gemini 與 Gate.io 介面。
- `src/quant_agent/engine.py`：交易工作流與清倉控制。
- `src/quant_agent/storage.py`：稽核事件持久化。
- `tests/`：單元測試與安全行為驗證。

## 狀態與驗證標準

本專案的**程式實作完成度為 100%**：市場資料、Gemini 決策、風險控制、Gate.io Futures 介面、時區排程、定時開盤／清倉、持倉追蹤、稽核日誌與測試已完成。

> **正式上線狀態：未啟用。** 實盤交易仍需完成 Gate.io Testnet 驗證、GitHub production environment 設定及人工審查。程式內建的安全 gate 不等於交易所 API 的端到端驗證。

技術驗收已完成以下項目：

1. 資料可重現。
2. 決策可驗證。
3. 風險可拒絕。
4. 保護單可回讀。
5. 清倉可證明。
6. 異常可告警。

本專案提供軟體架構與風險控制範例，不構成投資、交易或財務建議。正式使用前，應進行獨立的法規、交易所條款、API 風險及資金承受度審查。
