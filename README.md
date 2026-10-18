# LINE OA for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Release](https://img.shields.io/github/v/release/xangin/line-oa-hacs)](https://github.com/xangin/line-oa-hacs/releases)
[![Validate](https://github.com/xangin/line-oa-hacs/actions/workflows/validate.yml/badge.svg)](https://github.com/xangin/line-oa-hacs/actions/workflows/validate.yml)
[![Tests](https://github.com/xangin/line-oa-hacs/actions/workflows/tests.yml/badge.svg)](https://github.com/xangin/line-oa-hacs/actions/workflows/tests.yml)

讓 Home Assistant 透過 LINE 官方帳號發送**文字、圖片與攝影機截圖**通知。

> **必須同時安裝 [LINE OA Add-on](https://github.com/xangin/line-oa-ha-addon)。** 本儲存庫只是 Home Assistant 整合（動作與狀態實體）；登入 LINE、發送訊息由 Add-on 負責。只裝其中一個無法使用。

| 元件 | 儲存庫 | 用途 |
| --- | --- | --- |
| **LINE OA Add-on**（必要） | [`https://github.com/xangin/line-oa-ha-addon`](https://github.com/xangin/line-oa-ha-addon) | LINE 登入、收件者別名、發送 |
| **LINE OA 整合**（本儲存庫，必要） | `https://github.com/xangin/line-oa-hacs` | `line_oa.*` 動作、四個狀態實體、自動探索與安全配對 |

> **非官方 LINE 專案。** Add-on 使用 LINE 官方帳號管理後台的未公開聊天介面，並非 LINE Messaging API，也未獲 LINE 背書。LINE 服務變更或登入限制可能影響使用；請遵守 LINE 使用規範，不適合大量群發或必須保證送達的緊急通知。

提供：

| 類型 | 名稱 |
| --- | --- |
| Action | `line_oa.send_text`、`line_oa.send_image`、`line_oa.send_screen_shot` |
| 狀態實體 | `binary_sensor.line_oa_connected`、`binary_sensor.line_oa_logged_in`、`sensor.line_oa_session_status`、`sensor.line_oa_last_error` |

> 本整合**不**建立 `notify.line_oa`，也不會依聯絡人建立任何實體。

---

## 需求

| 項目 | 需求 |
| --- | --- |
| Home Assistant | **Home Assistant OS**（需要 Supervisor／Add-on 商店），版本 **2026.9.0** 以上 |
| 架構 | LINE OA Add-on 提供 **amd64（x86-64）** 與 **aarch64（64 位元 ARM／Raspberry Pi）** 映像 |
| HACS | 2.x |
| LINE | 可管理該官方帳號的 LINE 帳號，且官方帳號已啟用「聊天」（見 [Add-on 說明](https://github.com/xangin/line-oa-ha-addon#2-必須啟用聊天並建立對話)） |

沒有 Supervisor 的 Home Assistant Container／Core 無法安裝 Add-on，因此無法使用本整合。

---

## 安裝

### 步驟 1：安裝 LINE OA Add-on

[![新增 Add-on 儲存庫到 Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fxangin%2Fline-oa-ha-addon)

或手動：

1. **設定 → 應用程式／附加元件 → 商店**（名稱依 HA 版本而不同）→ 右上角 ⋮ → **儲存庫（Repositories）**，加入：

   ```text
   https://github.com/xangin/line-oa-ha-addon
   ```

2. 找到 **LINE OA** → **安裝** → **啟動**（建議開啟「開機啟動」）。
3. 按 **開啟 Web UI**：用手機 LINE 掃描登入官方帳號，再到「聯絡人」為收件者設定**別名**（例如 `home`；小寫英數、底線或連字號，1–32 字元）。

詳細步驟見 [Add-on 說明文件](https://github.com/xangin/line-oa-ha-addon#readme)。

### 步驟 2：用 HACS 安裝本整合

[![在 HACS 開啟本儲存庫](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=xangin&repository=line-oa-hacs&category=integration)

或手動：

1. 開啟 **HACS** → 右上角 ⋮ → **自訂儲存庫（Custom repositories）**。
2. Repository 填入下列網址，類別選 **Integration**（整合），按「新增」：

   ```text
   https://github.com/xangin/line-oa-hacs
   ```

3. 在 HACS 搜尋 **LINE OA** → **下載**。
4. **重新啟動 Home Assistant**（設定 → 系統 → 右上角電源 → 重新啟動）。

### 步驟 3：確認自動探索並配對

1. 重新啟動後，**設定 → 裝置與服務** 會出現「**已發現：LINE OA**」，點 **新增／設定**。
2. 畫面詢問「是否將 LINE OA Add-on 連接到 Home Assistant？」→ **提交**。
3. HA 會與 Add-on 安全配對並自動取得專用憑證，完成後建立四個狀態實體，三個動作即可使用。

全程**不需要輸入 IP、埠或 API Key**，也不會發送任何 LINE 訊息。沒有看到「已發現」時，可以手動新增：

[![新增 LINE OA 整合](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=line_oa)

若顯示「找不到執行中的 LINE OA Add-on」，請先完成步驟 1 並確認 Add-on 已啟動。

### 步驟 4：測試

在 **開發者工具 → 動作** 執行（`who` 填 Add-on 裡設定的別名）：

```yaml
action: line_oa.send_text
data:
  who: home
  message: "LINE OA 測試通知"
```

### 不使用 HACS（手動安裝）

下載 [最新版本](https://github.com/xangin/line-oa-hacs/releases/latest)，把 `custom_components/line_oa/` 整個資料夾複製到 HA 的 `/config/custom_components/line_oa/`，重新啟動 Home Assistant 後依步驟 3 配對。

### 更新

- 整合：在 HACS 更新後重新啟動 Home Assistant。
- Add-on：在 Add-on 商店更新。**不要以解除安裝後重裝代替更新**（會改變 Add-on 身分，需要重新設定）。

---

## Action 範例

三個 Action 都可在**自動化圖形化編輯器**中選擇（搜尋「LINE OA」），欄位會顯示中文名稱與說明。

### `line_oa.send_text`：傳送文字

| 欄位 | 必要 | 說明 |
| --- | --- | --- |
| `who` | ✔ | 接收者別名，請填寫 LINE OA Add-on 中已設定的別名（**只能一個字串**） |
| `message` | ✔ | 文字訊息，支援 Template |

```yaml
actions:
  - action: line_oa.send_text
    data:
      who: simon
      message: "測試通知"
```

使用 Template：

```yaml
actions:
  - action: line_oa.send_text
    data:
      who: simon
      message: >
        浴室溫度：
        {{ states('sensor.bathroom_temperature') }}°C
```

### `line_oa.send_image`：傳送圖片

| 欄位 | 必要 | 說明 |
| --- | --- | --- |
| `who` | ✔ | 接收者別名 |
| `image_path` | ✔ | HA 本機圖片絕對路徑（例如 `/media/...`），或公開 HTTP／HTTPS 圖片直連網址 |

圖片**不附加文字**，沒有 `message` 欄位（填了會直接報錯）。需要說明文字時，請另外用 `line_oa.send_text`：

```yaml
actions:
  - action: line_oa.send_image
    data:
      who: simon
      image_path: "/media/test.jpg"
```

```yaml
actions:
  - action: line_oa.send_image
    data:
      who: simon
      image_path: "/media/snapshot.jpg"
  - action: line_oa.send_text
    data:
      who: simon
      message: "設備檢查結果"
```

從 **0.3.3** 起，同一個 `image_path` 欄位也接受公開圖片網址：

```yaml
actions:
  - action: line_oa.send_image
    data:
      who: simon
      image_path: "https://example.com/notification.jpg"
```

請將範例換成實際可直接下載的 JPEG／PNG 圖片網址。也可以在 `image_path` 使用 HA Template 組合網址。

圖片由 Home Assistant 讀取或下載後，以 multipart 圖片內容上傳給 Add-on；Add-on 不需要知道網址，也不會直接讀取 HA 的檔案。外部圖片**只在 HACS 記憶體短暫保存**，發送結束後釋放，不建立暫存檔或永久快取，不增加備份內容。

- 網址下載總逾時 **20 秒**，最多 **3 次**重新導向；沒有 Content-Length 的下載也會逐塊檢查 **10 MiB** 上限。
- 請使用圖片直連網址。一般網頁、登入頁、GIF、WebP、SVG 等格式會明確報錯，不會當作圖片發送。
- 支援帶簽名參數的圖片網址；下載不帶 HA／Add-on 的 API Key 或 Cookie。需要登入或特殊認證 Header 的網站不包含在此功能內。
- 外部網址需指向公開網路位址；不接受內網、localhost、link-local 位址，重新導向也套用此規則。HTTPS 不會降級到 HTTP，TLS 憑證須有效。
- 同一整合一次只處理一張外部圖片的下載及上傳；其他外部圖片請求依序執行。下載失敗時不會呼叫 Add-on 發送；上傳結果不明仍不自動重送。

本機檔案的限制維持如下：

- 支援 JPEG、PNG（以檔案內容判斷，不只看副檔名），上限 **10 MiB**。
- 只能讀取 HA `allowlist_external_dirs` 內的檔案。預設包含媒體資料夾（HA OS / Container 為 `/media`，Core 安裝為 `config/media`）與 `config/www`。
- 會先解析 symlink 與 `..` 再檢查實際位置；即使把整個 `/config` 加進 allowlist，`secrets.yaml`、`.storage/` 及任何隱藏檔仍一律拒絕。

### `line_oa.send_screen_shot`：傳送攝影機截圖

| 欄位 | 必要 | 說明 |
| --- | --- | --- |
| `who` | ✔ | 接收者別名 |
| `camera_entity` | ✔ | 攝影機實體（圖形化編輯器中為攝影機選擇器） |

和 `send_image` 一樣**不附加文字**；需要文字請另外用 `line_oa.send_text`：

```yaml
actions:
  - action: line_oa.send_screen_shot
    data:
      who: simon
      camera_entity: camera.front_door
  - action: line_oa.send_text
    data:
      who: simon
      message: "門口有人"
```

- 透過 HA 官方 Camera API（`camera.async_get_image`）即時取得靜態畫面；只提供串流的攝影機，由 HA 官方機制從串流擷取。
- 圖片**只存在記憶體**，不會存檔，不需要事先執行 `camera.snapshot`，也不需要共享資料夾。
- 攝影機無法提供畫面時會明確報錯，**不會**用舊圖代替。

### 回應資料（選用）

三個 Action 都支援 Response Data：

```yaml
actions:
  - action: line_oa.send_text
    data:
      who: simon
      message: "洗衣機已完成"
    response_variable: line_result
  - action: system_log.write
    data:
      message: "LINE 發送狀態：{{ line_result.status }}（{{ line_result.request_id }}）"
```

| 欄位 | 說明 |
| --- | --- |
| `status` | `submitted`（已送交 LINE）或 `confirmed`（已確認送達 LINE）。**都不代表收件者已讀** |
| `request_id` | 本次請求 ID（由 HA 產生） |
| `message_id` | Add-on 回傳的訊息 ID（若有） |
| `who` | 接收者別名 |

### 單一接收者規則

`who` 永遠只能是**一個字串**，不支援陣列，也不會自動展開。別名不存在時直接報錯，**不會**改傳預設對象或其他人。需要通知多人時，請在 LINE 建立群組並在 Add-on 設定成一個別名。

---

## 狀態感測器

整合 **0.3.4** 起，先看「LINE OA 服務狀態」即可辨識問題發生在 HA 配對、Add-on 連線，還是 LINE 登入。三個實體的 ID 與 unique ID 保持不變；既有自動化與自行設定的名稱、ID 都會保留。

| 顯示名稱 | 實體 ID | 說明 |
| --- | --- | --- |
| LINE OA 服務狀態 | `sensor.line_oa_session_status` | 彙整連線與登入狀態，詳見下表。屬性 `account_name` 為官方帳號名稱；讀取失敗時清空，避免顯示舊資料 |
| HA 與 Add-on 連線 | `binary_sensor.line_oa_connected` | `on`：Add-on 健康檢查有回應；`off`：無法連線或健康檢查失敗。另看服務狀態確認配對及 LINE 登入 |
| LINE 帳號登入有效 | `binary_sensor.line_oa_logged_in` | `on`：Add-on 回報登入有效；`off`：Add-on 回報未登入；`unavailable`：目前無法確認，例如 Add-on 離線、配對失效或回應缺少登入資訊 |
| 最近錯誤 | `sensor.line_oa_last_error` | 錯誤摘要或 `none`，限制 255 字元，遮蔽 Token、Cookie、Authorization 等字樣。狀態讀取失敗時為 `unavailable`；完整資訊請看 Add-on 日誌 |

### 服務狀態與處理方式

| 中文顯示狀態 | 自動化使用的狀態值 | 意義與處理方式 |
| --- | --- | --- |
| 連線與登入正常 | `ready` | HA 能讀取正確 Add-on 的狀態，且 LINE 登入有效。無須處理；發送前仍須設定接收者別名 |
| Add-on 無法連線 | `gateway_unreachable` | 檢查 Add-on 是否啟動、健康檢查及日誌是否正常；外部 Gateway 則檢查網址、連接埠與網路。恢復後自動更新 |
| HA 需要重新配對 | `pairing_required` | Add-on 拒絕 HA 的 API Key。在 Add-on Web UI 按「允許 Home Assistant 配對」，再回 HA 完成重新驗證；外部 Gateway 請更新 API Key |
| LINE 尚未登入 | `auth_required` | 到 Add-on Web UI 掃描 QR Code 登入，再按「手動測試連線」。舊版 Gateway 登入失效也可能回報此狀態 |
| LINE 登入已過期 | `expired` | 到 Add-on Web UI 重新掃碼登入，再按「手動測試連線」。無須重新配對 HA |
| LINE 暫時無法連線 | `disconnected` | 檢查 Add-on 對外網路及 LINE 服務，稍候再測試連線；若持續發生，查看 Add-on 日誌 |
| LINE 驗證或權限異常 | `error` | Add-on 回報 LINE 錯誤，可能是驗證、權限或其他請求異常；查看「最近錯誤」及 Add-on 日誌確認原因，再依錯誤處理 |
| 無法讀取 Add-on 狀態 | `status_unavailable` | 健康檢查有回應，但狀態 API 回應錯誤或格式不符。查看 Add-on 日誌，確認整合與 Add-on 版本相容 |
| 連到不同的 Add-on | `instance_mismatch` | 此網址回應的是另一個 Gateway 實例，例如重裝後識別碼改變。先確認目標正確，再從整合「重新設定」明確切換連線 |
| 狀態尚未確認 | `unknown` | 資料缺漏、出現未支援狀態，或 `ready` 與登入資訊不一致。稍候更新或手動測試連線，持續發生則查看日誌與版本 |

中文介面的列舉狀態會依 HA 使用者的介面語言顯示；開發者工具、範本與自動化請使用表中的英文狀態值。實體的預設名稱依 **HA 系統語言**建立，因此系統英文、介面中文時，名稱仍可能是英文，但服務狀態會顯示中文。已有自訂名稱的實體會保留自訂名稱。

**升級注意：** Add-on 離線時，服務狀態現在為 `gateway_unreachable`，方便直接判斷原因。原本以 `sensor.line_oa_session_status = unavailable` 判斷斷線的自動化，請改用此狀態或 `binary_sensor.line_oa_connected = off`。停用或卸載整合時，實體仍由 HA 標為 `unavailable`。

- 每 **60 秒**更新一次（DataUpdateCoordinator）。Add-on 恢復後自動更新，離線期間錯誤日誌只記一次。
- 發送遇到連線錯誤或 LINE Session 錯誤時，會向 Add-on 重新查詢狀態；其他發送失敗（例如 LINE 拒絕、別名不存在）**不會**改變登入狀態。

---

## LINE 登入失效提醒

HACS 整合 **0.3.2** 起，確認 LINE 登入失效時，會建立一則 HA 持續通知及「設定 → 系統 → 修復」提示，附上管理頁面入口。Add-on 模式連到 HA 的 Add-on 頁面，再按「開啟 Web UI」重新掃碼登入、按「手動測試連線」。驗證恢復為 `ready` 後，通知與修復提示自動清除，**不必重新配對 Home Assistant**。

- 同一次失效只建立一則通知，輪詢不會反覆新增；使用者關閉通知後，也不會每分鐘重新彈出。
- 暫時斷線、一般錯誤或未知狀態不會誤報登入失效；若先前已確認失效，提醒保留到驗證成功。
- 首次尚未登入不提醒。Add-on 0.1.6 起可辨識主動登出，也不會誤報為失效；主動登出會清除既有提醒。
- 重啟 HA 時若狀態已是 `expired`，仍會顯示提醒；卸載或停用整合時清除它。
- **Add-on 0.1.6 起每 5 分鐘主動驗證**，HACS 每 60 秒讀取結果。正常閒置且檢查未因發送忙碌而略過時，發現失效到 HA 提示約需最多 6 分鐘，加上請求處理時間。手動測試或發送前驗證可提早發現。
- 舊版 Add-on／外部 Gateway 仍相容，但發現失效的時機取決於該 Gateway；它們可能只在發送時驗證，也無法可靠區分主動登出。

這些是 HA 內的通知與修復提示，不會嘗試使用已失效的 LINE 登入來發送提醒。Gateway API Key 被拒絕仍走既有重新配對流程，與 LINE 重新登入分開。

---

## 發送結果與「結果不明」

| 發送結果 | HA 行為 |
| --- | --- |
| `submitted` / `confirmed` | Action 成功，Response Data 帶回原狀態 |
| `failed` | Action 失敗：「LINE 明確拒絕此訊息」 |
| `unknown`、逾時、傳送中斷線、伺服器錯誤（500/502/504） | Action 失敗：「**LINE 訊息發送結果不明，可能已經送出，請勿立即重試。**」並附 `request_id` |

本整合**絕不自動重送**。看到「結果不明」時，請先到 LINE 確認是否已收到，再決定是否手動重送。

---

## 錯誤排除

| 狀況 | 原因 / 處理 |
| --- | --- |
| 沒有出現「已發現：LINE OA」 | 確認 LINE OA Add-on 已安裝並啟動、整合已安裝並重新啟動 HA；或用上方按鈕手動新增 |
| 找不到執行中的 LINE OA Add-on | 先安裝並啟動 Add-on（步驟 1） |
| 此安裝方式沒有 Supervisor | 需要 Home Assistant OS；Container／Core 無法使用 |
| `connected = off` | Add-on 停止或重新啟動中；到 Add-on 頁面確認狀態與日誌 |
| `logged_in = off`、`session_status = auth_required / expired` | LINE 登入失效：開啟 Add-on Web UI 重新登入 LINE，**不需要**改 HA 設定 |
| 跳出「重新配對」 | Add-on 的配對被重設：在 Add-on Web UI 按「允許 Home Assistant 配對」，再回到 HA 按提交 |
| 配對失敗／配對碼已過期 | 直接重試；Add-on 已配對過時，請先在 Add-on Web UI 按「允許 Home Assistant 配對」 |
| 找不到 LINE 接收者別名「xxx」 | 到 Add-on 的「聯絡人」確認別名拼字 |
| 不允許讀取「/path」 | 圖片需放在 `/media`（或 `allowlist_external_dirs` 內），且不能是隱藏檔或敏感檔 |
| 不是 JPEG 或 PNG 圖片 / 超過 10 MiB | 改用圖片直連網址，或轉檔、縮小圖片 |
| 外部圖片下載失敗／逾時／HTTP 錯誤 | 確認 HA 能在 20 秒內下載圖片，不需要登入，且 TLS 憑證有效 |
| 圖片網址不允許／重新導向過多 | 使用公開網路的最終圖片直連網址；內網來源請用既有攝影機截圖或本機檔案方式 |
| 攝影機無法提供目前的靜態畫面 | 攝影機離線、關閉或不支援靜態畫面；先在 HA 攝影機畫面確認能看到即時影像 |
| message is not supported for images | `send_image` / `send_screen_shot` 不接受 `message`；文字請另外用 `line_oa.send_text` |
| 發送結果不明 | 見上一節；**不要立即重試** |

需要更多診斷資訊時：**設定 → 裝置與服務 → LINE OA → ⋮ → 下載診斷資料**（憑證會被遮蔽），或暫時開啟除錯日誌：

```yaml
logger:
  logs:
    custom_components.line_oa: debug
```

日誌不包含憑證、配對碼或訊息內容。

---

## 安全

- HA 與 Add-on 透過 Supervisor 探索與**一次性、短效**的配對碼完成配對，取得專用憑證；不使用固定密碼、LINE Cookie 或 Supervisor Token。
- 憑證只保存在 Home Assistant 的整合設定中，不會出現在日誌、實體屬性、動作回應或診斷資料。
- `send_image` 只讀取 HA 允許的目錄，並阻擋 symlink 跳脫、`..`、`secrets.yaml`、`.storage/` 與隱藏檔。
- 攝影機截圖只在記憶體中處理，不存檔。

---

## 已知限制

- 一次只連接一個 LINE OA Add-on。
- `who` 是文字輸入欄位（HA 沒有穩定的遠端動態下拉選單）；送出前會向 Add-on 驗證別名。
- 少數攝影機整合本身會回傳快取畫面；本整合只能使用 HA 官方 Camera API 提供的畫面。
- 移除 Add-on 不會自動刪除本整合；整合會顯示離線，可自行刪除。

---

## 開發者文件

協定、安全設計與測試方式等開發用資料放在 [docs/](docs/README.md)，一般使用者不需要閱讀。

## 授權

MIT License
