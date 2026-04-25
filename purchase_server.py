import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import requests
import uvicorn
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl

from relay_server import relay_client


app = FastAPI(
    title="Proxy Purchase Systems",
    description="Purchase relay server APIs for GUI intake, PDF intake, and Rakuraku webhook forwarding.",
    version="0.1.0",
)

DEFAULT_ATTACHMENT_URL = "https://drive.google.com/file/d/1X-j1SMF6YJdpnlIWARq_zZdIKtMEjfa5/view?usp=drive_link"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploaded_pdfs"
UPLOAD_DIR.mkdir(exist_ok=True)
RAKURAKU_TO_BAKURAKU_MAPPING_FILE = BASE_DIR / "rakuraku_to_bakuraku_mapping.json"

PURCHASE_REQUESTS: Dict[str, Dict[str, Any]] = {}
RELAY_PACKAGES: Dict[str, Dict[str, Any]] = {}
WEBHOOK_HISTORY: List[Dict[str, Any]] = []
RAKURAKU_RECEIVED_REQUESTS: Dict[str, Dict[str, Any]] = {}
BAKURAKU_APPLICATION_LINKS: Dict[str, Dict[str, Any]] = {}

BAKURAKU_FORM_ID = os.getenv("BAKURAKU_FORM_ID", "").strip()
BAKURAKU_POLLING_INTERVAL_SECONDS = int(os.getenv("BAKURAKU_POLLING_INTERVAL_SECONDS", "60"))

_polling_started = False


class PurchaseItem(BaseModel):
    item_code: str = Field(..., description="Item code from the GUI.")
    item_name: str = Field(..., description="Item name from the GUI.")
    quantity: int = Field(..., ge=1)
    unit_price: int = Field(..., ge=0)
    department: Optional[str] = Field(default=None, description="Department or cost center.")


class PurchaseRequestPayload(BaseModel):
    request_id: Optional[str] = Field(default=None, description="Client-side request ID.")
    client_id: str = Field(..., description="Client or applicant ID from the GUI.")
    project_id: str = Field(..., description="Project ID from the GUI.")
    applicant_name: str = Field(..., description="Applicant name.")
    title: str = Field(..., description="Purchase request title.")
    currency: str = Field(default="JPY")
    memo: Optional[str] = None
    items: List[PurchaseItem] = Field(default_factory=list)


class GuiNotificationPayload(BaseModel):
    request_id: str = Field(..., description="Request ID managed by the purchase server.")
    rakuraku_record_id: str = Field(..., description="Record ID returned from Rakuraku Hanbai.")
    status: str = Field(..., description="Updated Rakuraku status.")
    callback_url: HttpUrl = Field(..., description="GUI webhook endpoint URL.")
    message: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_rakuraku_to_bakuraku_mapping() -> List[Dict[str, str]]:
    return json.loads(RAKURAKU_TO_BAKURAKU_MAPPING_FILE.read_text(encoding="utf-8"))


def _set_nested_value(target: Dict[str, Any], path: str, value: Any) -> None:
    keys = path.split(".")
    current = target

    for key in keys[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child

    current[keys[-1]] = value


def _parse_json_array(raw_value: Any, field_name: str) -> List[Any]:
    if raw_value in (None, "", []):
        return []

    if isinstance(raw_value, list):
        return raw_value

    try:
        parsed = json.loads(str(raw_value))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {exc}") from exc

    if not isinstance(parsed, list):
        raise HTTPException(status_code=400, detail=f"{field_name} must be a JSON array.")

    return parsed


def _coerce_mapped_value(target_path: str, value: Any) -> Any:
    if target_path == "defaultFieldValue.paymentAmount" and value not in (None, ""):
        return int(value)
    return value


def _build_relay_package(record: Dict[str, Any], bakuraku_file_id: str) -> Dict[str, Any]:
    rakuraku_details = [
        {
            "109926": item["item_code"],
            "109927": item["item_name"],
            "109945": item["department"] or "",
            "109928": str(item["quantity"]),
            "109933": str(item["unit_price"]),
        }
        for item in record["items"]
    ]

    rakuraku_payload = relay_client.build_rakuraku_record_payload(
        db_schema_id="101251",
        request_status="購買申請中",
        request_number=record["client_id"],
        product_code=record["project_id"],
        attachment_url=DEFAULT_ATTACHMENT_URL,
        approval_token=bakuraku_file_id,
        desired_date="",
        details=rakuraku_details,
    )

    bakuraku_form_field_values = [
        {
            "formFieldTitle": item["item_name"],
            "rawValue": item["item_code"],
        }
        for item in record["items"]
    ]

    return {
        "request_id": record["request_id"],
        "normalized_request": record,
        "rakuraku_record_payload": rakuraku_payload,
        "bakuraku_file_upload_input": {
            "file_name": record["pdf"]["original_filename"],
            "saved_path": record["pdf"]["saved_path"],
        },
        "bakuraku_file_id": bakuraku_file_id,
        "bakuraku_application_input": {
            "title": record["title"],
            "client_name": record["applicant_name"],
            "payment_amount": record["total_amount"],
            "form_field_values": bakuraku_form_field_values,
            "file_ids": [],
        },
        "prepared_at": _now_iso(),
    }


def _build_bakuraku_application_from_rakuraku(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not BAKURAKU_FORM_ID:
        raise HTTPException(status_code=500, detail="BAKURAKU_FORM_ID is required.")

    rakuraku_record_id = str(payload.get("rakurakuRecordId", "")).strip()
    if not rakuraku_record_id:
        raise HTTPException(status_code=400, detail="rakurakuRecordId is required.")

    bakuraku_payload: Dict[str, Any] = {
        "formId": BAKURAKU_FORM_ID,
        "defaultFieldValue": {},
        "formFieldValues": _parse_json_array(payload.get("formFieldValuesJson"), "formFieldValuesJson"),
        "fileIds": [str(file_id) for file_id in _parse_json_array(payload.get("fileIdsJson"), "fileIdsJson")],
    }

    for mapping in _load_rakuraku_to_bakuraku_mapping():
        source_key = mapping["source"]
        target_path = mapping["target"]
        if source_key not in payload:
            continue
        mapped_value = _coerce_mapped_value(target_path, payload[source_key])
        _set_nested_value(bakuraku_payload, target_path, mapped_value)

    return bakuraku_payload


def _store_bakuraku_application_link(
    application_id: str,
    rakuraku_record_id: str,
    request_payload: Dict[str, Any],
    bakuraku_response: Dict[str, Any],
) -> Dict[str, Any]:
    link = {
        "application_id": application_id,
        "rakuraku_record_id": rakuraku_record_id,
        "bakuraku_status": bakuraku_response.get("status") or "IN_PROGRESS",
        "bakuraku_response": bakuraku_response,
        "bakuraku_request_payload": request_payload,
        "linked_at": _now_iso(),
        "last_checked_at": None,
    }
    BAKURAKU_APPLICATION_LINKS[application_id] = link
    return link


def _poll_bakuraku_application_links() -> None:
    terminal_statuses = {"APPROVED", "REJECTED"}

    while True:
        for application_id, link in list(BAKURAKU_APPLICATION_LINKS.items()):
            if link["bakuraku_status"] in terminal_statuses:
                continue

            try:
                status_response = relay_client.get_bakuraku_application_status(application_id)
            except Exception:
                continue

            link["last_checked_at"] = _now_iso()
            link["last_status_response"] = status_response
            new_status = status_response.get("status") or link["bakuraku_status"]

            if new_status in terminal_statuses:
                link["bakuraku_status"] = new_status
                link["status_updated_at"] = _now_iso()

        time.sleep(BAKURAKU_POLLING_INTERVAL_SECONDS)


@app.on_event("startup")
def _start_polling_worker() -> None:
    global _polling_started

    if _polling_started:
        return

    worker = threading.Thread(target=_poll_bakuraku_application_links, daemon=True)
    worker.start()
    _polling_started = True


@app.get("/health")
def health_check() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/gui/purchase-submissions", status_code=201)
async def receive_purchase_submission(
    request_json: str = Form(...),
    file: UploadFile = File(...),
) -> Dict[str, Any]:
    if file.content_type not in {"application/pdf", "application/x-pdf"}:
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    try:
        payload = PurchaseRequestPayload.model_validate_json(request_json)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid request_json: {exc}") from exc

    server_request_id = payload.request_id or f"req-{uuid4().hex[:12]}"
    normalized_items = []
    total_amount = 0

    for item in payload.items:
        line_total = item.quantity * item.unit_price
        total_amount += line_total
        normalized_items.append(
            {
                "item_code": item.item_code,
                "item_name": item.item_name,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "department": item.department,
                "line_total": line_total,
            }
        )

    pdf_id = f"pdf-{uuid4().hex[:12]}"
    destination = UPLOAD_DIR / f"{pdf_id}.pdf"
    content = await file.read()
    destination.write_bytes(content)

    pdf_record = {
        "pdf_id": pdf_id,
        "request_id": server_request_id,
        "original_filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": len(content),
        "saved_path": str(destination),
        "uploaded_at": _now_iso(),
        "bakuraku_upload_status": "pending",
    }

    record = {
        "request_id": server_request_id,
        "client_id": payload.client_id,
        "project_id": payload.project_id,
        "applicant_name": payload.applicant_name,
        "title": payload.title,
        "currency": payload.currency,
        "memo": payload.memo,
        "items": normalized_items,
        "total_amount": total_amount,
        "pdf": pdf_record,
        "received_at": _now_iso(),
    }
    PURCHASE_REQUESTS[server_request_id] = record

    try:
        bakuraku_file_response = relay_client.post_bakuraku_file_upload(file.filename, content)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to upload file to Bakuraku: {exc}") from exc

    bakuraku_file_id = (
        bakuraku_file_response.get("id")
        or bakuraku_file_response.get("fileId")
        or bakuraku_file_response.get("file_id")
    )
    if not bakuraku_file_id:
        raise HTTPException(
            status_code=502,
            detail="Bakuraku file upload response did not include a file ID.",
        )

    record["bakuraku_file_response"] = bakuraku_file_response
    record["bakuraku_file_id"] = bakuraku_file_id

    relay_package = _build_relay_package(record, bakuraku_file_id)
    relay_package["bakuraku_file_response"] = bakuraku_file_response
    RELAY_PACKAGES[server_request_id] = relay_package

    try:
        rakuraku_response = relay_client.post_rakuraku_record_create(
            relay_package["rakuraku_record_payload"]
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Failed to post record to Rakuraku.",
                "error": str(exc),
                "bakuraku_file_response": bakuraku_file_response,
                "bakuraku_file_id": bakuraku_file_id,
                "rakuraku_record_payload": relay_package["rakuraku_record_payload"],
            },
        ) from exc

    record["rakuraku_response"] = rakuraku_response
    relay_package["rakuraku_response"] = rakuraku_response

    return {
        "message": "Purchase submission accepted and posted to Rakuraku.",
        "request_id": server_request_id,
        "purchase_request": record,
        "relay_package": relay_package,
        "bakuraku_file_response": bakuraku_file_response,
        "rakuraku_response": rakuraku_response,
    }


@app.post("/webhooks/rakuraku/completed")
def notify_gui_after_rakuraku_update(payload: GuiNotificationPayload) -> Dict[str, Any]:
    if payload.request_id not in PURCHASE_REQUESTS:
        raise HTTPException(status_code=404, detail="request_id was not found.")

    gui_payload = {
        "event": "rakuraku_record_updated",
        "request_id": payload.request_id,
        "rakuraku_record_id": payload.rakuraku_record_id,
        "status": payload.status,
        "message": payload.message,
        "notified_at": _now_iso(),
    }

    try:
        response = requests.post(str(payload.callback_url), json=gui_payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Failed to notify GUI webhook: {exc}") from exc

    history = {
        "request_id": payload.request_id,
        "callback_url": str(payload.callback_url),
        "rakuraku_record_id": payload.rakuraku_record_id,
        "status": payload.status,
        "forwarded_at": _now_iso(),
        "gui_status_code": response.status_code,
    }
    WEBHOOK_HISTORY.append(history)

    return {
        "message": "Webhook forwarded to GUI.",
        "notification": gui_payload,
        "delivery_result": history,
    }


@app.post("/api/rakuraku/records", status_code=202)
def receive_rakuraku_record(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    received_id = f"rakuraku-{uuid4().hex[:12]}"
    rakuraku_record_id = str(payload.get("rakurakuRecordId", "")).strip()
    if not rakuraku_record_id:
        raise HTTPException(status_code=400, detail="rakurakuRecordId is required.")

    bakuraku_request_payload = _build_bakuraku_application_from_rakuraku(payload)

    try:
        bakuraku_response = relay_client.post_bakuraku_application_create(bakuraku_request_payload)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Failed to post application to Bakuraku.",
                "error": str(exc),
                "bakuraku_request_payload": bakuraku_request_payload,
                "rakuraku_payload": payload,
            },
        ) from exc

    application_id = (
        bakuraku_response.get("id")
        or bakuraku_response.get("applicationId")
        or bakuraku_response.get("application_id")
    )
    if not application_id:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Bakuraku response did not include an application ID.",
                "bakuraku_response": bakuraku_response,
            },
        )

    link = _store_bakuraku_application_link(
        application_id=application_id,
        rakuraku_record_id=rakuraku_record_id,
        request_payload=bakuraku_request_payload,
        bakuraku_response=bakuraku_response,
    )

    record = {
        "received_id": received_id,
        "payload": payload,
        "rakuraku_record_id": rakuraku_record_id,
        "bakuraku_application_id": application_id,
        "received_at": _now_iso(),
    }
    RAKURAKU_RECEIVED_REQUESTS[received_id] = record

    return {
        "message": "Rakuraku payload accepted and Bakuraku application created.",
        "received_id": received_id,
        "record": record,
        "bakuraku_request_payload": bakuraku_request_payload,
        "bakuraku_response": bakuraku_response,
        "application_link": link,
    }


@app.get("/api/gui/purchase-submissions/{request_id}/relay-package")
def get_relay_package(request_id: str) -> Dict[str, Any]:
    relay_package = RELAY_PACKAGES.get(request_id)
    if relay_package is None:
        raise HTTPException(status_code=404, detail="relay package was not found.")

    return {
        "message": "Relay package found.",
        "relay_package": relay_package,
    }


@app.get("/api/bakuraku/applications/{application_id}")
def get_bakuraku_application_link(application_id: str) -> Dict[str, Any]:
    link = BAKURAKU_APPLICATION_LINKS.get(application_id)
    if link is None:
        raise HTTPException(status_code=404, detail="application link was not found.")

    return {
        "message": "Bakuraku application link found.",
        "application_link": link,
    }


if __name__ == "__main__":
    uvicorn.run("purchase_server:app", host="0.0.0.0", port=8000, reload=True)
