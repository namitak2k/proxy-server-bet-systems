import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import requests


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
ENV_FILE = BASE_DIR / ".env"


def _load_local_env() -> None:
    if not ENV_FILE.exists():
        return

    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_local_env()


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable '{name}' is required.")
    return value


def _rakuraku_headers(token: str) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-HD-apitoken": token,
    }


def _bakuraku_headers(token: str) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "x-bakuraku-api-key": token,
    }


def _bakuraku_multipart_headers(token: str) -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "x-bakuraku-api-key": token,
    }


def _load_template(template_name: str) -> Dict[str, Any]:
    path = TEMPLATES_DIR / template_name
    return json.loads(path.read_text(encoding="utf-8"))


class RelayClient:
    def __init__(self) -> None:
        self.rakuraku_register_url = _required_env("RAKURAKU_REGISTER_URL")
        self.rakuraku_update_url = _required_env("RAKURAKU_UPDATE_URL")
        self.rakuraku_token = _required_env("RAKURAKU_TOKEN")
        self.bakuraku_file_upload_url = _required_env("BAKURAKU_FILE_UPLOAD_URL")
        self.bakuraku_application_url = _required_env("BAKURAKU_APPLICATION_URL")
        self.bakuraku_status_url = _required_env("BAKURAKU_STATUS_URL")
        self.bakuraku_token = _required_env("BAKURAKU_TOKEN")

    def _post_json(self, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise requests.HTTPError(
                f"{exc}; response_body={response.text}; request_payload={json.dumps(payload, ensure_ascii=False)}"
            ) from exc
        return response.json()

    def _get_json(self, url: str, headers: Dict[str, str]) -> Dict[str, Any]:
        response = requests.get(url, headers=headers, timeout=30)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise requests.HTTPError(f"{exc}; response_body={response.text}") from exc
        return response.json()

    def _post_multipart(
        self,
        url: str,
        file_name: str,
        file_bytes: bytes,
        headers: Dict[str, str],
    ) -> Dict[str, Any]:
        response = requests.post(
            url,
            data={"name": file_name},
            files={"file": (file_name, file_bytes)},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def build_rakuraku_record_payload(
        self,
        db_schema_id: str,
        request_status: str,
        request_number: str,
        product_code: str,
        attachment_url: str,
        approval_token: str,
        desired_date: str,
        details: list[Dict[str, str]],
        record_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = _load_template("rakuraku_record_create.json")
        payload["dbSchemaId"] = db_schema_id
        payload["values"]["109935"] = request_status
        payload["values"]["109918"] = request_number
        payload["values"]["109921"] = product_code
        payload["values"]["109956"] = attachment_url
        payload["values"]["109959"] = approval_token
        payload["values"]["109931"] = desired_date
        payload["values"]["details"] = details

        if record_id:
            payload["recordId"] = record_id

        return payload

    def build_bakuraku_application_payload(
        self,
        title: str,
        status: str,
        form_id: str,
        client_name: str,
        payment_amount: int,
        approval_reminds_date: str,
        purchase_close_scheduled_date: str,
        trading_date: str,
        form_field_values: list[Dict[str, Any]],
        file_ids: list[str],
    ) -> Dict[str, Any]:
        payload = _load_template("bakuraku_application_create.json")
        payload["title"] = title
        payload["status"] = status
        payload["formId"] = form_id
        payload["defaultFieldValue"] = {
            "clientName": client_name,
            "paymentAmount": payment_amount,
            "approvalRemindsDate": approval_reminds_date,
            "purchaseCloseScheduledDate": purchase_close_scheduled_date,
            "tradingDate": trading_date,
        }
        payload["formFieldValues"] = form_field_values
        payload["fileIds"] = file_ids
        return payload

    def post_rakuraku_record_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._post_json(self.rakuraku_register_url, payload, _rakuraku_headers(self.rakuraku_token))

    def post_rakuraku_record_update(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._post_json(self.rakuraku_update_url, payload, _rakuraku_headers(self.rakuraku_token))

    def post_bakuraku_application_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._post_json(
            self.bakuraku_application_url,
            payload,
            _bakuraku_headers(self.bakuraku_token),
        )

    def post_bakuraku_file_upload(self, file_name: str, file_bytes: bytes) -> Dict[str, Any]:
        return self._post_multipart(
            self.bakuraku_file_upload_url,
            file_name,
            file_bytes,
            _bakuraku_multipart_headers(self.bakuraku_token),
        )

    def get_bakuraku_application_status(self, application_id: str) -> Dict[str, Any]:
        url = f"{self.bakuraku_status_url.rstrip('/')}/{application_id}"
        return self._get_json(url, _bakuraku_headers(self.bakuraku_token))


relay_client = RelayClient()
