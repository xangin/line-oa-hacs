# 外部 Gateway 連線（LXC / Docker / Linux）

> 只需要安裝**同一份** LINE OA HACS 整合；它同時支援外部 Gateway 與 Add-on。

## 準備

1. 在 LXC 上啟動 LINE OA Gateway（預設埠 `8790`），開啟 Gateway Web UI。
2. 完成 LINE 登入、設定聯絡人別名。
3. 在 Gateway Web UI **建立 API Key**。

## 新增整合

1. **設定 → 裝置與服務 → 新增整合 → LINE OA**。
2. 選擇「**外部 Gateway（LXC / Docker / Linux）**」。
3. 輸入：
   - **Gateway 網址**：例如 `http://192.168.1.50:8790`
   - **API Key**：Gateway Web UI 建立的 Key
4. 送出後檢查 `GET /api/v1/health` → `GET /api/v1/status`（帶 API Key）→ API 版本 → `instance_id`，成功即建立設定。

Gateway 在 HA 本機的 8790 埠時，網址會自動帶入。Gateway 用 mDNS 廣播 `_line-oa-gw._tcp` 時，HA 會顯示「已發現 LINE OA Gateway」，只需輸入 API Key（見 README）。

## 版本相容

| Gateway 回報 | 結果 |
| --- | --- |
| 沒有 `api_version`（目前的 LXC Gateway） | 視為 v1，可使用，不需升級 |
| `api_version: 1` | 可使用 |
| 其他版本 | 顯示「API 版本不相容」，不建立設定 |

## 重新設定與 API Key

- **重新設定**：修改網址或 API Key（Key 留空 = 沿用）。換成另一台 Gateway（`instance_id` 不同）會被拒絕。
- **API Key 失效（401/403）**：HA 跳出「API Key 失效」，輸入新 Key 即可。
- **LINE 登入過期**：在 Gateway Web UI 重新登入，HA 不需要改設定。

## 一次只連接一台 Gateway

已設定外部 Gateway 時，「新增整合」與 mDNS 探索都不會建立第二份設定。之後若安裝 Add-on，請見 [migration.md](migration.md)。
