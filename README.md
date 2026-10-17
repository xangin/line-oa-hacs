# LINE OA for Home Assistant

讓 Home Assistant 透過外部的 **LINE OA Gateway**（例如架在 Ubuntu LXC 上）發送 LINE 官方帳號通知。

```
Home Assistant ── LINE OA 整合（本專案） ──HTTP/HTTPS──▶ LINE OA Gateway（LXC） ──▶ LINE 官方帳號
```

本整合**只負責 Home Assistant 端**：不實作 LINE 登入或通訊協定，不需要 LINE Cookie、LINE 密碼、Session、資料庫、Node.js、Chromium 或 HA Add-on。只需要兩個設定值：

- Gateway URL
- Gateway API Key

提供：

| 類型 | 名稱 |
| --- | --- |
| Action | `line_oa.send_text`、`line_oa.send_image`、`line_oa.send_screen_shot` |
| 狀態實體 | `binary_sensor.line_oa_connected`、`binary_sensor.line_oa_logged_in`、`sensor.line_oa_session_status`、`sensor.line_oa_last_error` |

> 本整合刻意**不**建立 `notify.line_oa`，也不會依聯絡人建立任何實體。

---

## 版本需求

| 項目 | 需求 |
| --- | --- |
| Home Assistant | **2026.9.0** 以上（已用 2026.9.0、2026.9.2、2026.10.1 測試） |
| HACS | 2.x |
| LINE OA Gateway | 實作 [API 契約](#gateway-api-契約) `/api/v1/*` 的版本 |

---

## 安裝方式

### 方法一：HACS 自訂 Repository（建議）

1. 開啟 HACS → 右上角 ⋮ → **自訂儲存庫（Custom repositories）**。
2. Repository 填入 `https://github.com/xangin/line-oa-hacs`，類別選 **Integration**。
3. 在 HACS 搜尋 **LINE OA** → 下載。
4. **重新啟動 Home Assistant**。

### 方法二：手動安裝

將 `custom_components/line_oa/` 整個資料夾複製到 HA 的 `/config/custom_components/line_oa/`，然後重新啟動 Home Assistant。

---

## Gateway 設定方式與 API Key

1. 在 LXC 上啟動 LINE OA Gateway，並從瀏覽器開啟 Gateway Web UI。
2. 在 Gateway Web UI 完成 **LINE 登入**並選擇官方帳號。
3. 在 Gateway Web UI 設定**聯絡人別名**（例如 `simon`、`family`）。多人通知請在 LINE 建立群組，再將群組設定為一個別名。
4. 在 Gateway Web UI **建立 API Key**，複製下來給 Home Assistant 使用。

兩種認證要分清楚：

| 認證 | 用途 | 在哪裡處理 |
| --- | --- | --- |
| Gateway API Key | Home Assistant ↔ Gateway | HA 整合設定（可重新設定 / 重新驗證） |
| LINE Session | Gateway ↔ LINE | **Gateway Web UI** 重新登入；HA 不需要改任何設定 |

---

## Config Flow 操作（圖形化設定）

不需要修改 `configuration.yaml`。

1. **設定 → 裝置與服務 → 新增整合 → LINE OA**（若已出現「已發現：LINE OA Gateway」，直接點它即可，見下方「自動發現」）
2. 輸入：
   - **Gateway 網址**：例如 `http://192.168.1.50:8787`
   - **API Key**：Gateway Web UI 建立的 Key
3. 送出後整合會依序：檢查 `GET /api/v1/health` → 以 API Key 呼叫 `GET /api/v1/status` → 讀取 `instance_id` → 檢查是否已加入過同一台 Gateway → 建立設定。

說明：

- Gateway 可連線但 LINE Session 已過期時，**仍可完成設定**，LINE 登入問題會顯示在狀態實體上。
- 以 Gateway 的 `instance_id` 作為唯一識別，**不是 IP**。Gateway 換 IP 時用「重新設定」即可。
- 目前只支援**一台** Gateway。

畫面（HA 2026.10.1 + Mock Gateway 實機截圖）：

| 設定表單 | API Key 錯誤 | 設定完成 |
| --- | --- | --- |
| ![設定表單](docs/screenshots/01-config-flow-form.jpg) | ![API Key 錯誤](docs/screenshots/02-config-flow-invalid-api-key.jpg) | ![設定完成](docs/screenshots/03-config-flow-success.jpg) |

| 自動化編輯器中的三個 Action |
| --- |
| ![Action 清單](docs/screenshots/05-automation-action-picker.jpg) |

### 自動發現 Gateway（免輸入網址）

HA 只會在下列兩種情況自動帶出 Gateway 網址，使用者只需要貼上 API Key：

**1. 區網自動發現（zeroconf / mDNS，建議）**

Gateway 在區網廣播 `_line-oa-gw._tcp` 服務後，HA 重新啟動（或幾分鐘內）會在 **設定 → 裝置與服務** 顯示「**已發現：LINE OA Gateway（IP:8790）**」，點「新增」→ 只輸入 API Key 即可。Gateway 在 Ubuntu LXC 或 HA 本機都適用（需在同一個區網網段）。

在 Gateway 所在的 Ubuntu LXC 上，用 Avahi 廣播即可（**不需要修改 Gateway 程式**）：

```bash
sudo apt install -y avahi-daemon
```

建立 `/etc/avahi/services/line-oa-gateway.service`：

```xml
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">LINE OA Gateway on %h</name>
  <service>
    <type>_line-oa-gw._tcp</type>
    <port>8790</port>
  </service>
</service-group>
```

```bash
sudo systemctl restart avahi-daemon
```

可選的 TXT 記錄（加在 `<service>` 內，例如 `<txt-record>instance_id=...</txt-record>`）：

| TXT | 說明 |
| --- | --- |
| `instance_id` | Gateway 的 `instance_id`；有的話 HA 可在輸入 API Key 前就辨識是否已加入過 |
| `scheme` | `https` 時改用 HTTPS（預設 `http`） |
| `path` | Gateway 位於反向代理子路徑時的基本路徑，例如 `/line` |

**2. Gateway 在 HA 本機**

手動「新增整合 → LINE OA」時，整合會先探測 **HA 本機 IP 的 8790 埠**，找到 Gateway 就自動填入網址。

說明：

- 自訂整合在尚未設定前不會執行任何程式，所以無法「自己掃描」網路；HA 官方的自動發現必須由 Gateway 端廣播（上面的 mDNS）。
- 已經設定好 LINE OA，或在「已發現」卡片按了「忽略」後，就不會再跳出發現通知。
- API Key 仍需手動輸入：它是 HA 與 Gateway 之間的驗證，不會透過廣播傳送。

### 重新設定（Reconfigure）

**設定 → 裝置與服務 → LINE OA → ⋮ → 重新設定**，可變更 Gateway URL 與 API Key（API Key 留空 = 沿用原本的 Key）。變更後會重新驗證；如果新網址是另一台 Gateway（`instance_id` 不同）會被拒絕。

### 重新驗證（Reauth）

只有 **Gateway 拒絕 API Key（401/403）** 時，HA 才會跳出重新驗證通知，要求輸入新的 API Key。LINE Session 過期**不會**觸發這個流程。

---

## Action 範例

三個 Action 都可在**自動化圖形化編輯器**中選擇（搜尋「LINE OA」），欄位會顯示中文名稱與說明。

### `line_oa.send_text`：傳送文字

| 欄位 | 必要 | 說明 |
| --- | --- | --- |
| `who` | ✔ | 接收者別名，請填寫 LINE OA Gateway 中已設定的別名（**只能一個字串**） |
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
| `image_path` | ✔ | **Home Assistant 系統上**的圖片絕對路徑（不是 Gateway LXC 的路徑） |

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

圖片由 Home Assistant 讀取後以 `multipart/form-data` 上傳給 Gateway；不會把 HA 的路徑交給 Gateway。

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
| `status` | `submitted`（Gateway 已送交 LINE）或 `confirmed`（Gateway 已確認送達 LINE）。**都不代表收件者已讀** |
| `request_id` | 本次請求 ID（由 HA 產生） |
| `message_id` | Gateway 回傳的訊息 ID（若有） |
| `who` | 接收者別名 |

### 單一接收者規則

`who` 永遠只能是**一個字串**，不支援陣列，也不會自動展開。別名不存在時直接報錯，**不會**改傳預設對象或其他人。需要通知多人時，請在 LINE 建立群組並在 Gateway 設定成一個別名。

---

## 狀態感測器

| 實體 | 狀態 | 說明 |
| --- | --- | --- |
| `binary_sensor.line_oa_connected`（Gateway 連線狀態） | `on` / `off` | 依 `GET /api/v1/health`。Gateway 在線**不代表** LINE 已登入 |
| `binary_sensor.line_oa_logged_in`（LINE 登入狀態） | `on` / `off` / `unavailable` | 依 `/api/v1/status` 的 `line_logged_in`。Gateway 連不上時為 `unavailable`，**不會**誤判為 `off` |
| `sensor.line_oa_session_status`（LINE Session 狀態） | `ready`、`auth_required`、`expired`、`disconnected`、`error`、`unknown` | Gateway 連不上時為 `unavailable`，不會殘留舊的 `ready`。屬性 `account_name` 為官方帳號名稱 |
| `sensor.line_oa_last_error`（最近錯誤） | 錯誤摘要或 `none` | 限制 255 字元；會遮蔽 Token、Cookie、Authorization 等字樣 |

- 每 **60 秒**更新一次（DataUpdateCoordinator）。Gateway 恢復後自動更新，離線期間錯誤日誌只記一次。
- 發送遇到連線錯誤或 LINE Session 錯誤時，會向 Gateway 重新查詢狀態；其他發送失敗（例如 LINE 拒絕、別名不存在）**不會**改變登入狀態。

---

## 發送結果與「結果不明」

| Gateway 結果 | HA 行為 |
| --- | --- |
| `submitted` / `confirmed` | Action 成功，Response Data 帶回原狀態 |
| `failed` | Action 失敗：「LINE 明確拒絕此訊息」 |
| `unknown`、`SEND_UNKNOWN`、POST 逾時、傳送中斷線、Gateway/Proxy 回 500/502/504 | Action 失敗：「**LINE 訊息發送結果不明，可能已經送出，請勿立即重試。**」並附 `request_id` |

本整合**絕不自動重送**：HTTP Client 不重試 POST、不跟隨重新導向（避免重新送出內容）。看到「結果不明」時，請先到 LINE 確認是否已收到，再決定是否手動重送。

---

## 錯誤排除

| 訊息 | 原因 / 處理 |
| --- | --- |
| 無法連線至 LINE OA Gateway | 確認 Gateway 有在執行、URL/Port 正確、HA 與 LXC 網路互通。訊息**沒有**送出 |
| Gateway 拒絕 Home Assistant 的 API Key | 到 Gateway Web UI 確認/重建 API Key，再依 HA 通知重新驗證，或使用「重新設定」 |
| LINE 登入已過期 / Gateway 尚未登入 LINE | 到 **Gateway Web UI** 重新登入 LINE。不需要改 HA 設定 |
| 找不到 LINE 接收者別名「xxx」 | 到 Gateway Web UI 確認別名拼字（大小寫需一致） |
| 不允許讀取「/path」 | 圖片需放在 `/media`（或 `allowlist_external_dirs` 內），且不能是隱藏檔或敏感檔 |
| 不是 JPEG 或 PNG 圖片 / 超過 10 MiB | 轉檔或縮小圖片 |
| 攝影機無法提供目前的靜態畫面 | 攝影機離線、關閉或不支援靜態畫面；請先在 HA 攝影機畫面確認能看到即時影像 |
| message is not supported for images | `send_image` / `send_screen_shot` 不接受 `message`；文字請另外用 `line_oa.send_text` |
| 發送結果不明 | 見上一節；**不要立即重試** |

需要更多診斷資訊時，可在 `configuration.yaml` 暫時開啟除錯日誌（日誌不會包含 API Key 與訊息內容）：

```yaml
logger:
  logs:
    custom_components.line_oa: debug
```

---

## 安全注意事項

- API Key 只放在 `Authorization: Bearer` Header，不會寫入日誌或錯誤訊息。
- 建議 Gateway 只在內網開放；跨網路請使用 HTTPS（目前不支援自簽憑證，見已知限制）。
- `send_image` 只讀取 HA 允許的目錄，並阻擋 symlink 跳脫、`..`、`secrets.yaml`、`.storage/` 與隱藏檔。
- 攝影機截圖只在記憶體中處理。
- 日誌只記錄 `request_id`、別名與結果狀態，不記錄訊息內容。

---

## 已知限制

- 只支援一台 Gateway（單一 Config Entry）。
- `who` 是文字輸入欄位；HA 的 Action Selector 目前沒有穩定的方式提供遠端動態下拉選單，因此未使用私有前端 API。後端會向 Gateway 驗證別名。
- Gateway 使用自簽 HTTPS 憑證時無法連線（尚無「略過憑證驗證」選項）。
- 少數攝影機整合本身會回傳快取畫面；本整合只能使用 HA 官方 Camera API 提供的畫面。
- 錯誤回應格式、`aliases` 回應格式等 Gateway 契約細節尚待確認，詳見 [docs/api-contract-issues.md](docs/api-contract-issues.md)。

---

## Gateway API 契約

| Method | Path | 說明 |
| --- | --- | --- |
| GET | `/api/v1/health` | 健康檢查（2xx 即視為在線） |
| GET | `/api/v1/status` | 狀態，需 Bearer API Key |
| GET | `/api/v1/aliases` | 已設定的別名 |
| POST | `/api/v1/messages/text` | JSON：`who`、`message`、`request_id` |
| POST | `/api/v1/messages/image` | multipart：`who`、`image`、`request_id`（不含文字） |

---

## 開發與測試

```bash
python3.14 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements_test.txt
```

```bash
.venv/bin/pytest
```

測試全部使用本機 Mock Gateway（`tests/mock_gateway.py`），不會連到真正的 LINE。Mock Gateway 也可獨立啟動，供手動測試 HA：

```bash
.venv/bin/python tests/mock_gateway.py --port 8787 --api-key test-api-key
```

## 授權

MIT License
