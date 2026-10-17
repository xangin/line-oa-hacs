# 從外部 LXC 切換到 Add-on

> 這是使用者明確操作的「切換 Gateway」，不是自動遷移。原有自動化不需要重寫。

## 何時會出現

已用外部 Gateway（LXC）設定 LINE OA，之後安裝並啟動 LINE OA Add-on 時，HA 會顯示：

> 偵測到 LINE OA Add-on。是否將現有 LINE OA 整合切換至 Add-on 連線？

選項：

| 選項 | 結果 |
| --- | --- |
| **保留目前連線** | 不做任何改變；之後同一個 Add-on 重新發布探索也不會再詢問 |
| **遷移至 Add-on** | 進入確認畫面，顯示舊 Gateway 網址與兩者的 `instance_id` |

HA **不會**自動替你切換，也不會建立第二份 LINE OA 設定。

也可以在任何時候從 **LINE OA → ⋮ → 重新設定 → Home Assistant Add-on** 切換。

## 切換時會做什麼

按下確認後依序：

1. 重新讀取 Add-on 最新的探索資料。
2. 以一次性配對碼完成安全配對，取得新的專用 API Key。
3. 用新 Key 驗證 Gateway `health`、API 版本、`status`（含 LINE Session 狀態）與 `instance_id`。
4. 全部成功後才更新設定並重新載入整合。

**任何一步失敗，原本的 LXC 設定完全不變**（網址、API Key、unique_id 都不動），感測器繼續使用 LXC。

## 保留了什麼

| 項目 | 保留方式 |
| --- | --- |
| 四個實體的 Entity ID | 不變 |
| Entity Unique ID | 不變：以第一次設定時的 Gateway `instance_id`（`entity_instance_id`）為基礎，不含 IP 或 API Key |
| 自訂名稱、自訂 Entity ID、歷史資料 | 隨 Entity Registry 保留 |
| 三個動作與既有自動化 | 不需修改，切換後改送到 Add-on |
| 原本的 LXC 設定 | 存在 `previous_connection`，供切回使用 |

Config Entry 的 unique_id 會改成 Add-on 的 `instance_id`，所以之後 Add-on 的探索會被認成「已設定」，不會重複詢問。

## 兩台是不同的 Gateway

LXC 與 Add-on 通常是**不同的 Gateway 實例**（不同 `instance_id`）。切換保留的是「HA 這邊的 LINE OA 連線實體」，不是宣稱兩台伺服器相同：

- 聯絡人別名、LINE 登入是各 Gateway 自己的資料，需在 Add-on 內重新設定。
- 確認畫面會列出兩個 `instance_id`，讓你明確確認。

## 切回 LXC

**LINE OA → ⋮ → 重新設定 → 外部 Gateway**：網址會自動帶入切換前的 LXC 網址；為了安全，API Key 需要重新輸入一次（或貼上原本的 Key）。實體同樣保留。

## Add-on 移除後

HA 移除 Add-on 探索時，只刪除 unique_id 等於 Discovery UUID 的 hassio 設定；LINE OA 使用 Gateway `instance_id`，因此**不會被刪除**。若你已切回 LXC，移除 Add-on 不影響 LXC 連線。

## 沒有設定資料遷移

本版本沒有 Config Entry 資料遷移程式；沒有 `connection_mode` 的設定一律視為外部 Gateway 運作。
