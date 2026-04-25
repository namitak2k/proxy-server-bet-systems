# proxy_purchase_systems

`purchase_server.py` 単体で FastAPI サーバーを起動できます。

## APIs

- `POST /api/gui/purchase-requests`
  GUIで入力した購入申請情報を受け付けます。
- `POST /api/gui/purchase-pdfs`
  GUIでアップロードされたPDFを受け付けて保存します。
- `POST /webhooks/rakuraku/completed`
  楽々販売の更新完了イベントを受けて、GUIの webhook に通知します。
- `GET /health`
  ヘルスチェックです。

## Run

```powershell
cd C:\job\proxy_purchase_systems
copy .env.example .env
python -m pip install -r requirements.txt
python purchase_server.py
```

または:

```powershell
uvicorn purchase_server:app --reload
```

## Relay server env

中継サーバーの外部連携先は Docker の環境変数で管理します。

```powershell
docker run --env-file .env ...
```

主に以下を設定してください。

- `RAKURAKU_REGISTER_URL`
- `RAKURAKU_UPDATE_URL`
- `RAKURAKU_TOKEN`
- `BAKURAKU_APPLICATION_URL`
- `BAKURAKU_STATUS_URL`
- `BAKURAKU_TOKEN`
- `POLLING_INTERVAL_SECONDS`
