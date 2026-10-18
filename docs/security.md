# 安全性：API Key、Discovery 與配對（開發者參考）

> 開發用資料，說明內部安全設計。

## 兩種認證

| 認證 | 對象 | 誰負責 |
| --- | --- | --- |
| Gateway API Key | Home Assistant ↔ Gateway | HACS 整合（配對 / 重新設定 / 重新驗證） |
| LINE Session | Gateway ↔ LINE | Gateway / Add-on Web UI |

LINE Session 過期不會被當成 API Key 失效，反之亦然。

## API Key 保存

- 只存在 Home Assistant 的 Config Entry（`.storage/core.config_entries`），不另建檔案或資料庫。
- 只放在 `Authorization: Bearer` Header；**不放在網址或 Query String**。
- 不寫入一般日誌、不出現在實體狀態或屬性、不出現在動作回應資料。
- 診斷資料（Diagnostics）會遮蔽目前與切換前（`previous_connection`）的 API Key。
- `PairingResult` 的 `repr` 不含 Key；配對錯誤訊息不含配對碼或 Key。

## Supervisor 探索的存取邊界（已查證 Supervisor 原始碼）

- 讀取探索內容的 `GET /discovery`、`GET /discovery/<uuid>` 只允許 **HA Core** 呼叫（`require_home_assistant`）。其他 Add-on 只能發布或刪除**自己的**訊息。
- Supervisor 推送給 HA 的通知**不含** config，HA 需再透過 API 讀取；Supervisor 日誌只記錄 UUID 與服務名稱。
- Supervisor 會把探索訊息存到主機磁碟（`discovery.json`）。因此配對碼必須短效且單次有效。
- HA Core 的 hassio 探索不會把 config 寫入日誌；探索資料只存在設定流程的記憶體中，不會傳給前端。

所以探索**不被當成可傳送永久秘密的通道**：探索裡只有一次性配對碼，永久 API Key 只在配對 API 的回應中傳給 HA。

## 一次性配對碼（bootstrap token）

由 Add-on 產生（`line-oa-ha-addon` 的 `pairing.ts`）：

| 性質 | 行為 |
| --- | --- |
| 長度 | 32 bytes 隨機（43 字元 base64url） |
| 有效期 | 10 分鐘，只存在 Add-on 記憶體 |
| 使用次數 | 一次；成功後立即作廢，並重新發布不含配對碼的探索 |
| 已配對時 | 不發出配對碼；配對 API 一律拒絕 |
| 重放 | 已用過的配對碼回 401 |
| 頻率限制 | 每分鐘最多 5 次嘗試 |
| 比對 | 常數時間比較，並同時檢查 `instance_id` |
| 瀏覽器請求 | 帶 `Origin` / `Cookie` / `Sec-Fetch-Site` 的請求拒絕 |

HACS 端：

- 收到探索時**不配對**；使用者按確認後才重新讀取最新探索並配對。
- 配對碼已過期時不送出。
- 配對 POST 只送一次，不重試、不跟隨重新導向。
- 回應必須是 201、`instance_id` 相符、`api_version` 為 1、API Key 格式正確，否則不儲存任何東西。
- 取得 Key 後再用它驗證 Gateway，確認 `instance_id` 一致才建立設定。

## 防止被誘導連到其他主機（SSRF）

探索資料中的 `host` 不直接採用：必須等於 Supervisor 提供的 Add-on slug 換成連字號，`port` 固定 `8787`。IP、外部網域、路徑、其他埠、其他 slug 的 Add-on 都會被拒絕。更新已設定 Add-on 的網址時也套用同樣規則。

Coordinator 每次輪詢會比對 Gateway 回報的 `instance_id`；不一致（另一台或重新安裝的 Gateway）時不顯示其狀態，需使用者重新設定。

## 不使用的認證方式

- 不使用固定或預設 API Key。
- 不使用 LINE Session Cookie 或 Supervisor Token 當作 Gateway API Key。
- 不以 IP、Hostname、`instance_id` 或 User-Agent 取代認證。
- 不提供免驗證的配對 API。

## 重新配對的授權

Add-on 已配對時不會發出配對碼。要重新配對，必須由管理員在 Add-on 的 Web UI（Ingress，僅 HA 管理員可開啟）按「允許 Home Assistant 配對」，這會撤銷舊 Key 並產生新的配對碼。

## 發送可靠性

兩種模式都一樣：結果 `unknown`、逾時、斷線、500/502/504 都回報「結果不明」，**不自動重送**，也不會改用另一台 Gateway 重送。

## 公開圖片下載（0.3.3）

外部圖片使用獨立的 aiohttp session、DummyCookieJar，不沿用 Gateway client、HA Cookie、認證 Header 或環境 Proxy。公開網址可帶簽名 Query，但錯誤訊息不包含來源網址或原始 aiohttp 例外；Add-on 只收到圖片內容與固定檔名。TLS 保持驗證。

每次連線的自訂 DNS resolver 先檢查所有回覆都是公開 unicast 位址，再將同一組結果交給 connector 建立連線，避免先檢查、再重新查 DNS 的落差。literal IP、每一步重新導向都檢查；拒絕私有、loopback、link-local、multicast 位址與 HTTPS→HTTP 降級。簽名 URL 保留已編碼路徑與 Query。

同一 config entry 的 lock 涵蓋下載至上傳完成，限制遠端圖片同時佔用的記憶體。20 秒涵蓋 DNS、連線、重新導向與下載；串流實際內容上限 10 MiB，拒絕壓縮傳輸，避免依壓縮大小誤判。檔案格式沿用 JPEG／PNG magic 檢查，沒有新增圖片轉碼器或外部下載相依。內容只留 RAM，沒有磁碟暫存或快取。
