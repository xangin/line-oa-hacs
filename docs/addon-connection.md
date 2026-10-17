# Add-on 連線：自動探索與安全配對

> 只需要安裝**同一份** LINE OA HACS 整合；它同時支援 Add-on 與外部 Gateway。

## 適用情況

- Home Assistant 是 **HA OS 或 Supervised**（有 Supervisor）。
- 已安裝並啟動 **LINE OA Add-on**（`line-oa-ha-addon`）。

HA Container / Core 沒有 Supervisor，無法使用 Add-on，請改用[外部 Gateway](external-connection.md)。

## 使用者流程

1. 安裝並啟動 LINE OA Add-on，在 Add-on 的 Web UI（側邊欄「LINE OA」）登入 LINE、設定聯絡人別名。
2. Add-on 透過 Supervisor 探索通知 HA。**設定 → 裝置與服務** 會出現「**已發現：LINE OA**」。
3. 點「設定」→ 畫面詢問「是否將 LINE OA Add-on 連接到 Home Assistant？」。
4. 按「提交」後才開始配對：
   1. HA 向 Supervisor 重新讀取最新的探索資料（配對碼會定期更換）。
   2. 以一次性配對碼呼叫 Add-on 的 `POST /api/v1/pairing/exchange`。
   3. Add-on 核發一把**專用 API Key**，HA 存入 Config Entry。
   4. HA 用新 Key 驗證 `health`、`status`、API 版本與 `instance_id`。
5. 建立四個狀態實體，三個動作可以使用。

整個過程不需要輸入 IP、埠、API Key、LINE Cookie、Bot ID 或 Chat ID，也**不會發送任何 LINE 訊息**。

只收到探索通知時**不會**建立任何設定；一定要使用者確認。

## 手動從「新增整合」開始

**新增整合 → LINE OA → Home Assistant Add-on**：

- 有找到 Add-on：直接進入同一個確認與配對畫面。
- 沒找到：顯示「找不到執行中的 LINE OA Add-on」與安裝指引，**不會假裝已找到**。
- 沒有 Supervisor：顯示此安裝方式不支援 Add-on。

## 探索資料（由 Add-on 發布）

```json
{
  "host": "local-line-oa",
  "port": 8787,
  "instance_id": "STABLE_UUID",
  "api_version": 1,
  "pairing_required": true,
  "bootstrap_token": "（一次性，未配對時才有）",
  "bootstrap_expires_at": "2026-10-17T10:10:00Z"
}
```

HA 端的驗證：

| 欄位 | 規則 |
| --- | --- |
| `host` | 必須等於 Supervisor 提供的 Add-on slug 換成連字號（例如 `local_line_oa` → `local-line-oa`）；不接受 IP、外部網域、路徑或埠 |
| `port` | 必須是 `8787` |
| `instance_id` | 必須是 UUID；作為 Config Entry 的唯一識別 |
| `api_version` | 必須是 `1`，否則顯示版本不相容 |
| `bootstrap_token` | 43 字元 base64url；已過期時不送出，重新讀取 |

## 識別值

| 值 | 來源 | 用途 |
| --- | --- | --- |
| Gateway `instance_id` | Add-on / Gateway 永久產生 | Config Entry 的 unique_id；判斷是不是同一台 Gateway |
| Discovery UUID | Supervisor 產生 | 只記錄在 `discovery_uuid`，**不當作身分** |

因為兩者不同，HA 在 Add-on 移除時（只刪除 unique_id 等於 Discovery UUID 的 hassio 設定）**不會刪掉 LINE OA 整合**。Add-on 移除後，整合保留、感測器顯示離線，你可以用「重新設定」切到外部 Gateway 或手動刪除。

## Add-on 重啟、更新、主機名稱變更

- Add-on 每 30 秒重新發布探索資料；HA 收到時，若已設定同一個 `instance_id`，只會更新經驗證的內部網址（例如從 `local-line-oa` 變成 `a1b2c3d4-line-oa`），**保留原 API Key、不重新配對、不新增設定**。
- Add-on 停止時：`connected = off`、其他三個實體 `unavailable`；API Key 不清除，也不會自動改用其他 Gateway。
- Add-on 重新安裝（產生新的 `instance_id`）時，舊 Key 失效且身分不同，HA 不會把新 Gateway 的狀態當成原本的顯示；請用「重新設定 → 與 Add-on 配對」。

## 重新配對

API Key 失效（例如在 Add-on 內重設配對）時，HA 會跳出「重新配對」：

1. 開啟 LINE OA Add-on 的 Web UI，按「**允許 Home Assistant 配對**」（重設配對）。
2. 回到 HA，在「重新配對」畫面按提交。

不需要找或複製隱藏在 Add-on 內的 API Key。也可以從 **重新設定 → 與 Add-on 配對** 主動執行。

LINE 登入過期**不會**觸發重新配對；請在 Add-on 的 Web UI 重新登入 LINE。
