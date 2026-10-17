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
| 架構 | LINE OA Add-on 目前只提供 **amd64（x86-64）** 映像 |
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
| `image_path` | ✔ | **Home Assistant 系統上**的圖片絕對路徑（例如 `/media/...`） |

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

圖片由 Home Assistant 讀取後上傳給 Add-on；Add-on 不會直接讀取 HA 的檔案。

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

| 實體 | 狀態 | 說明 |
| --- | --- | --- |
| `binary_sensor.line_oa_connected`（Gateway 連線狀態） | `on` / `off` | HA 是否連得到 LINE OA Add-on。在線**不代表** LINE 已登入 |
| `binary_sensor.line_oa_logged_in`（LINE 登入狀態） | `on` / `off` / `unavailable` | Add-on 的 LINE 登入是否有效。Add-on 連不上時為 `unavailable`，**不會**誤判為 `off` |
| `sensor.line_oa_session_status`（LINE Session 狀態） | `ready`、`auth_required`、`expired`、`disconnected`、`error`、`unknown` | Add-on 連不上時為 `unavailable`，不會殘留舊的 `ready`。屬性 `account_name` 為官方帳號名稱 |
| `sensor.line_oa_last_error`（最近錯誤） | 錯誤摘要或 `none` | 限制 255 字元；會遮蔽 Token、Cookie、Authorization 等字樣 |

- 每 **60 秒**更新一次（DataUpdateCoordinator）。Add-on 恢復後自動更新，離線期間錯誤日誌只記一次。
- 發送遇到連線錯誤或 LINE Session 錯誤時，會向 Add-on 重新查詢狀態；其他發送失敗（例如 LINE 拒絕、別名不存在）**不會**改變登入狀態。

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
| 不是 JPEG 或 PNG 圖片 / 超過 10 MiB | 轉檔或縮小圖片 |
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
