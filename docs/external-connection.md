# 外部 Gateway（實驗性）

> **開發用 / 實驗性功能。** 一般使用請依 [README](../README.md) 安裝 LINE OA Add-on。
> 本頁說明如何讓已設定好的 LINE OA 整合改連到 HA 以外的 Gateway（Ubuntu LXC / Docker / Linux）。

## 限制

- **新增整合一定要先透過 LINE OA Add-on 完成**；外部 Gateway 只能在之後的「重新設定」中切換，介面標示為「外部 Gateway（實驗性）」。
- 沒有 Supervisor 的安裝（HA Container / Core）無法新增本整合。
- 外部 Gateway 不會被自動探索（沒有 mDNS / zeroconf 發現）。
- 一次只連接一台 Gateway（Add-on 或外部擇一）。
- Gateway 使用自簽 HTTPS 憑證時無法連線。

## 準備外部 Gateway

1. 在 LXC 上啟動 LINE OA Gateway（例如埠 `8790`），開啟 Gateway Web UI。
2. 完成 LINE 登入、設定聯絡人別名。
3. 在 Gateway Web UI **建立 API Key**。

## 切換到外部 Gateway

1. **設定 → 裝置與服務 → LINE OA → ⋮ → 重新設定**。
2. 選「**外部 Gateway（實驗性）**」。
3. 輸入 **Gateway 網址**（例如 `http://192.168.1.50:8790`）與 **API Key**（從 Add-on 切換時必填）。
4. 整合會檢查 `GET /api/v1/health` → `GET /api/v1/status`（帶 API Key）→ API 版本 → `instance_id`，成功後重新載入。

切換時：

- 四個實體的 Entity ID / Unique ID、自訂名稱、歷史與既有自動化都保留（Unique ID 以第一次設定時的 Gateway `instance_id` 為基礎，存在 `entity_instance_id`）。
- 原本的 Add-on 連線存在 `previous_connection`，可切回。
- 任何驗證失敗都不會改動目前連線。
- 聯絡人別名與 LINE 登入是各 Gateway 自己的資料，需在外部 Gateway 內設定。

## 切回 Add-on

**重新設定 → LINE OA Add-on**：HA 重新讀取 Add-on 探索資料並配對（Add-on 若已配對過，需先在 Add-on Web UI 按「允許 Home Assistant 配對」）。實體同樣保留。

已連接外部 Gateway 時，若 Add-on 重新發布探索，HA 會詢問「保留目前連線」或「遷移至 Add-on」，不會自動切換；選「保留」後同一個 Add-on 不會再詢問。

## 外部模式的設定細節

- **重新設定**：可修改網址或 API Key（Key 留空 = 沿用）。換成另一台 Gateway（`instance_id` 不同）會被拒絕。
- **API Key 失效（401/403）**：HA 跳出「API Key 失效」，輸入新 Key。
- **LINE 登入過期**：在 Gateway Web UI 重新登入，HA 不需要改設定。

## 版本相容

| Gateway 回報 | 結果 |
| --- | --- |
| 沒有 `api_version`（目前的 LXC Gateway） | 視為 v1，可使用 |
| `api_version: 1` | 可使用 |
| 其他版本 | 顯示「API 版本不相容」，不切換 |

## Add-on 移除後

HA 移除 Add-on 探索時，只刪除 unique_id 等於 Discovery UUID 的 hassio 設定；LINE OA 使用 Gateway `instance_id`，因此已切換為外部 Gateway 的設定**不會被刪除**。
