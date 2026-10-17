# 錯誤排除（開發者參考）

> 開發用資料。一般使用者請看 [README 的錯誤排除](../README.md#錯誤排除)。

## 先分清楚是哪一種問題

| 狀態 | 代表意義 | 處理方式 |
| --- | --- | --- |
| `binary_sensor.line_oa_connected = off` | HA 連不到 Gateway | 檢查 Add-on 是否啟動 / LXC 是否在線、網址與網路 |
| 跳出「API Key 失效」或「重新配對」 | HA 與 Gateway 的驗證失敗 | Add-on：在 Add-on 按「允許 Home Assistant 配對」後回 HA 提交；外部：輸入新 API Key |
| `logged_in = off`、`session_status = auth_required / expired` | Gateway 與 LINE 的登入失效 | 在 Gateway / Add-on 的 Web UI 重新登入 LINE |
| 動作回報「發送結果不明」 | 無法判定訊息是否已送出 | **不要立即重試**，先到 LINE 確認 |

## 連線

| 訊息 | 原因 / 處理 |
| --- | --- |
| Gateway 無法連線 | Gateway 未啟動、網址或埠錯誤、防火牆 |
| 此 Gateway 的 API 版本不相容 | Gateway 回報的 `api_version` 不是 1；請更新 HACS 或 Gateway |
| instance_id 與設定不符 | 這個網址上換成了另一台（或重新安裝的）Gateway；用「重新設定」 |
| 此網址屬於另一台 Gateway | 外部模式重新設定時換了 Gateway；要換 Gateway 請刪除後重新新增，或透過切換流程 |

## 探索

| 訊息 | 原因 / 處理 |
| --- | --- |
| 沒有出現「已發現 LINE OA」 | Add-on 未啟動、HA 不是 HA OS / Supervised、或已經設定 LINE OA（只會連接一台） |
| 找不到執行中的 LINE OA Add-on | 先安裝並啟動 Add-on |
| 此安裝方式沒有 Supervisor | HA Container / Core 無法使用 Add-on |
| 無法驗證 Add-on 的探索資訊 | 探索內容不符合規則（主機名稱、埠、instance_id）；檢查 Add-on 版本 |
| 你已選擇保留目前的 Gateway | 之前選了「保留目前連線」；要切換請用「重新設定 → LINE OA Add-on」 |

## 配對

| 訊息 | 原因 / 處理 |
| --- | --- |
| Add-on 已經配對過 | Add-on 已核發 Key；先在 Add-on Web UI 按「允許 Home Assistant 配對」再重試 |
| 配對碼已過期 | 重試即可，HA 會自動讀取新的配對碼 |
| 配對失敗：Add-on 拒絕了配對碼 | 配對碼無效、過期或已使用；重試 |
| 配對嘗試次數過多 | 等一分鐘後再試 |
| Add-on 不支援配對或無法連線 | Add-on 未啟動或版本太舊 |
| 配對失敗，未儲存任何憑證 | 查看 Add-on 日誌；若配對請求逾時，Add-on 可能已核發 Key，請按「允許 Home Assistant 配對」後重試 |

配對失敗時不會建立或修改任何設定。

## 切換 Gateway

- 切換失敗：原本的連線完全不變。
- 切換後別名不存在：別名是各 Gateway 自己的資料，請在新 Gateway 內設定。
- 想切回外部 Gateway（實驗性）：見 [external-connection.md](external-connection.md)。

## 診斷資料

**設定 → 裝置與服務 → LINE OA → ⋮ → 下載診斷資料**：包含連線模式、Gateway 狀態與版本，API Key 會被遮蔽。

除錯日誌：

```yaml
logger:
  logs:
    custom_components.line_oa: debug
```

日誌不包含 API Key、配對碼或訊息內容。
