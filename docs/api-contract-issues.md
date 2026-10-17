# LINE OA Gateway API 契約待確認事項

本文件記錄 HACS 整合依「Gateway API 契約」開發時發現的不明確或有風險之處。
HACS 端**沒有**另外發明 API；對於契約未定義的部分，下表列出 HACS 目前採用的**暫定假設**，需要 Gateway 端確認或修改。

狀態欄：🔴 影響發送安全性（可能重複發送或誤報）　🟠 影響錯誤判斷　🟡 改善項目

---

## 1. 🔴 發送 API 成功回應格式未定義

**目前問題**：契約只說 Gateway 會回傳 `submitted / confirmed / failed / unknown`，沒有定義 JSON 結構。

**HACS 暫定假設**（`api.py` `_parse_send_success`）：

```json
{ "status": "submitted", "request_id": "<同 request>", "message_id": "選填" }
```

- 2xx 但不是 JSON、或 `status` 不是四個值之一 → 視為 **unknown**（不重送）。

**需要 Gateway 修改**：固定成功回應結構，並保證 2xx 一定帶 `status`。

**對 HACS 影響**：若 Gateway 回傳其他欄位名（例如 `result`），所有發送都會被判為「結果不明」。

---

## 2. 🔴 錯誤回應格式與標準錯誤碼未定義

**目前問題**：契約只舉例 `ALIAS_NOT_FOUND`、`SESSION_EXPIRED`、`SEND_UNKNOWN`，沒有定義錯誤 JSON 結構與完整錯誤碼清單、對應 HTTP 狀態碼。

**HACS 暫定假設**：可解析以下三種格式（優先順序由上至下）：

```json
{ "status": "failed", "error": { "code": "ALIAS_NOT_FOUND", "message": "..." } }
{ "error": "ALIAS_NOT_FOUND" }
{ "code": "ALIAS_NOT_FOUND", "message": "..." }
```

**建議修正**：統一使用第一種，並固定下列錯誤碼：

| 錯誤碼 | 建議 HTTP | 是否已送出 | HACS 對應 |
| --- | --- | --- | --- |
| `UNAUTHORIZED` / `INVALID_API_KEY` | 401 | 否 | API Key 錯誤 → 啟動重新驗證 |
| `FORBIDDEN` | 403 | 否 | 同上 |
| `ALIAS_NOT_FOUND` | 404 | 否 | 找不到接收者別名 |
| `SESSION_EXPIRED` | 409 | 否 | LINE 登入已過期 |
| `AUTH_REQUIRED` / `NOT_LOGGED_IN` | 409 | 否 | Gateway 尚未登入 LINE |
| `INVALID_REQUEST` / `VALIDATION_ERROR` / `INVALID_IMAGE` | 400 / 422 | 否 | Gateway 拒絕請求 |
| `MESSAGE_NOT_SUPPORTED` | 400 | 否 | 不支援圖片附加文字 |
| `PAYLOAD_TOO_LARGE` / `IMAGE_TOO_LARGE` | 413 | 否 | 圖片過大 |
| `RATE_LIMITED` | 429（附 `Retry-After`） | 否 | 請求過於頻繁 |
| `LINE_REJECTED` / `SEND_FAILED` | 200（`status: failed`）或 4xx | 否（LINE 明確拒絕） | LINE 明確拒絕 |
| `GATEWAY_UNAVAILABLE` | 503 | 否 | Gateway 暫時無法服務 |
| `SEND_UNKNOWN` | 200（`status: unknown`）或 5xx | **不確定** | 發送結果不明 |

**對 HACS 影響**：沒有錯誤碼時，HACS 只能依 HTTP 狀態碼保守判斷（見第 3 點）。

---

## 3. 🔴 POST 遇到 5xx 時是否已送出不明

**目前問題**：500/502/504 可能發生在 Gateway 已經呼叫 LINE 之後（例如反向代理逾時）。

**HACS 目前行為**：

- 500 / 502 / 504 且**沒有**明確錯誤碼 → **發送結果不明**（不重送）。
- 503 且沒有錯誤碼 → 視為「Gateway 暫時無法服務、未送出」。

**建議修正**：

- Gateway 在**確定尚未呼叫 LINE** 時，所有錯誤都應帶明確錯誤碼（例如 500 + `INTERNAL_ERROR` 並附 `"sent": false`）。
- 已呼叫 LINE 但無法確認結果時，回 `status: unknown` + `SEND_UNKNOWN`。
- 503 只用於「尚未處理請求」的情況。

**對 HACS 影響**：若 Gateway 對「已送出」的情況回 503，HACS 會誤報「沒有送出」，使用者可能手動重送造成重複訊息。

---

## 4. ✅ 圖片不附加文字（已決定）

**決定**：`send_image` 與 `send_screen_shot` **不提供** `message` 欄位，圖片一律不附加文字；需要文字時由使用者另外呼叫 `line_oa.send_text`。

**HACS 目前行為**：

- 兩個圖片 Action 的 Schema 不接受 `message`，填了會在送出前直接報錯（提示改用 `send_text`）。
- `POST /api/v1/messages/image` 的 multipart 只送 `who`、`image`、`request_id`。

**需要 Gateway 配合**：圖片端點只需處理單張圖片，不需要支援 `message`；也因此不會有「圖片送出但文字失敗」的部分成功情況。若 Gateway 收到未知欄位，建議回 400 `UNSUPPORTED_FIELD` 而不是忽略。

**對 HACS 影響**：無。若未來要恢復圖片附加文字，需要先在契約中定義部分成功（圖片已送、文字失敗）的回應方式。

## 5. 🟠 `request_id` 的去重語意

**目前問題**：HACS 每次呼叫產生新的 UUID `request_id`，但契約沒有說 Gateway 收到重複 `request_id` 時的行為。

**建議修正**：Gateway 以 `request_id` 做去重（例如保留 24 小時），重複請求直接回傳第一次的結果，並在回應中帶回 `request_id`。

**對 HACS 影響**：HACS 本身不重送；但若使用者或其他工具重送同一個 `request_id`，有去重才能避免重複訊息。HACS 會把 `request_id` 放在「結果不明」錯誤中，方便使用者到 Gateway 查詢。建議 Gateway Web UI 提供依 `request_id` 查詢結果的功能。

---

## 6. 🟠 `GET /api/v1/aliases` 回應格式未定義

**HACS 暫定假設**：接受下列任一格式：

```json
{ "aliases": [ { "alias": "simon", "type": "user" }, { "alias": "family", "type": "group" } ] }
["simon", "family"]
```

**其他待確認**：

- 別名是否區分大小寫？HACS 目前做**完全相同**比對（`Simon` ≠ `simon`）。
- 別名是否允許空白、中文、Emoji？是否做 Unicode 正規化？

**對 HACS 影響**：HACS 在讀取圖片 / 擷取攝影機畫面**之前**先用此 API 驗證 `who`；若回應格式不同，所有 Action 都會失敗。若此端點回 404（尚未實作），HACS 會略過預先檢查，改由發送 API 的 `ALIAS_NOT_FOUND` 判斷。

---

## 7. 🟠 `GET /api/v1/health` 是否需要驗證

**目前問題**：契約未定義 health 是否需要 API Key 與回應格式。

**HACS 目前行為**：一律帶 `Authorization`；任何 2xx 即視為 Gateway 在線；401/403 視為「Gateway 在線但 API Key 錯誤」。

**建議修正**：health 不需驗證，固定回 `200 {"status": "ok"}`；所有 `/api/v1/*` 其他端點需要 Bearer API Key。

---

## 8. 🟠 `GET /api/v1/status` 欄位語意

**待確認**：

- `line_logged_in` 與 `session_status` 不一致時（例如 `true` + `expired`）以何者為準？HACS 兩者分別顯示，不互相推導。
- `session_status` 可能值是否就是 `ready / auth_required / expired / disconnected / error / unknown`？HACS 將其他值顯示為 `unknown`。
- `last_error` 型別：字串、`null` 或物件（`{code, message, at}`）？HACS 三種都接受，取 `message` → `code`。
- `last_error` 必須由 Gateway 先去除 Cookie、Token、訊息內容與聊天內容；HACS 會再做一次遮蔽並截斷到 255 字元，但無法辨識一般文字中的私人內容。

---

## 9. 🟠 `instance_id` 穩定性

HACS 以 `instance_id` 作為 Config Entry 唯一識別，用來防止重複加入、並在「重新設定」時拒絕換成另一台 Gateway。

**需要 Gateway 保證**：`instance_id` 在 Gateway 重啟、升級、換 IP 後都不變；只有重新安裝（清除資料）才改變。

---

## 10. 🟡 大小與長度限制

- HACS 限制圖片 ≤ 10 MiB、僅 JPEG/PNG；文字 ≤ 5000 字元（LINE 文字訊息上限）。
- 請確認 Gateway / LINE OA 後台實際允許的圖片大小與格式，以及 Gateway 是否會自動縮圖。若 Gateway 上限較小，請回 413 `PAYLOAD_TOO_LARGE`。

---

## 11. 🟡 自簽 HTTPS 憑證

HACS 目前沒有「略過憑證驗證」選項。若 Gateway 預設使用自簽憑證，需在 HACS 新增選項，或建議 Gateway 在內網使用 HTTP / 正式憑證。

---

## 12. 🟡 Gateway 自動發現（mDNS 廣播）

**HACS 目前行為**：manifest 宣告 zeroconf 類型 `_line-oa-gw._tcp.local.`。HA 收到廣播後顯示「已發現」，網址由廣播的 IP 與埠組成（未提供埠時預設 8790），使用者只需輸入 API Key。手動新增時另會探測 HA 本機 IP 的 8790 埠並自動填入網址。

**需要 Gateway 修改（建議）**：

- 由 Gateway 自行（或在 LXC 以 Avahi）廣播 `_line-oa-gw._tcp`，埠為實際服務埠。
- TXT 記錄建議包含 `instance_id`（與 `/api/v1/status` 相同）、`version`；如在反向代理後方，加上 `scheme`、`path`。
- `GET /api/v1/health` 建議不需 API Key（HACS 用它在未取得 API Key 前確認對方是 Gateway；回 401/403 也會被視為 Gateway 存在）。

**對 HACS 影響**：Gateway 不廣播時，「已發現」不會出現，但手動新增仍可使用（Gateway 在 HA 本機時網址會自動填入）。API Key 不會透過廣播傳送，仍需使用者輸入。

