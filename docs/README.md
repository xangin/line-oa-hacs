# 開發者文件

> **開發用資料。** 一般使用者只需要看專案首頁的 [README](../README.md)（安裝 LINE OA Add-on + 本整合）。以下內容給有興趣了解內部設計、協定或參與開發的人。

| 文件 | 內容 |
| --- | --- |
| [security.md](security.md) | 配對與憑證安全設計、Supervisor 探索的存取邊界、SSRF 防護 |
| [troubleshooting.md](troubleshooting.md) | 連線、登入、探索、配對錯誤的完整對照 |
| [external-connection.md](external-connection.md) | 外部 Gateway（實驗性）：改連 LXC / Docker / Linux 上的 Gateway |
| [api-contract-issues.md](api-contract-issues.md) | Gateway API 契約的待確認事項 |
| [screenshots/](screenshots/) | 開發過程的介面截圖 |

## Gateway API 契約

整合只透過下列 HTTP API 與 Gateway（Add-on 或外部）溝通：

| Method | Path | 說明 |
| --- | --- | --- |
| GET | `/api/v1/health` | 健康檢查（2xx 即視為在線） |
| GET | `/api/v1/status` | 狀態，需 Bearer API Key |
| GET | `/api/v1/aliases` | 已設定的別名 |
| POST | `/api/v1/messages/text` | JSON：`who`、`message`、`request_id` |
| POST | `/api/v1/messages/image` | multipart：`who`、`image`、`request_id`（不含文字） |
| POST | `/api/v1/pairing/exchange` | 僅 Add-on：以一次性 `bootstrap_token` 換取專用 API Key |

`/api/v1/health` 與 `/api/v1/status` 若回報 `api_version`，必須為 `1`；未回報時（舊版 LXC）視為 v1。

Add-on 透過 Supervisor discovery 發布 `line_oa` 服務：`host`（等於 Supervisor 指派的 Add-on 主機名稱）、`port: 8787`、`instance_id`、`api_version: 1`、`pairing_required`，未配對時另含 `bootstrap_token` 與 `bootstrap_expires_at`。

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

測試全部使用本機 Mock Gateway（`tests/mock_gateway.py`，含 Add-on 配對模擬），不會連到真正的 LINE。Mock Gateway 也可獨立啟動：

```bash
.venv/bin/python tests/mock_gateway.py --port 8787 --api-key test-api-key
```

CI（`.github/workflows/`）執行 HACS 驗證、hassfest，以及 Home Assistant 2026.9 / 2026.10 的 Ruff、mypy、pytest。
