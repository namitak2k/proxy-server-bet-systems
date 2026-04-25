import json
import time
import uuid
from copy import deepcopy
from typing import Any, Dict, Tuple


_CACHE_STORE: Dict[str, Dict[str, Any]] = {}
_STATUS_STORE: Dict[str, Dict[str, Any]] = {}


def _json_response(body: Dict[str, Any], status_code: int = 200) -> Tuple[str, int, Dict[str, str]]:
    return (
        json.dumps(body, ensure_ascii=False, indent=2),
        status_code,
        {"Content-Type": "application/json; charset=utf-8"},
    )


def _parse_request_json(request) -> Dict[str, Any]:
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        return payload
    return {}


def _utc_epoch() -> int:
    return int(time.time())


def _build_mock_purchase(payload: Dict[str, Any]) -> Dict[str, Any]:
    items = payload.get("items") or payload.get("details") or []
    total_amount = 0

    normalized_items = []
    for index, item in enumerate(items, start=1):
        quantity = int(item.get("quantity") or item.get("109928") or 0)
        unit_price = int(item.get("unit_price") or item.get("109933") or 0)
        line_total = quantity * unit_price
        total_amount += line_total

        normalized_items.append(
            {
                "line_no": index,
                "item_code": item.get("item_code") or item.get("109926") or f"ITEM-{index:03d}",
                "item_name": item.get("item_name") or item.get("109927") or "mock-item",
                "department": item.get("department") or item.get("109945") or "general",
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
            }
        )

    return {
        "request_id": payload.get("request_id") or str(uuid.uuid4()),
        "client_id": payload.get("client_id") or payload.get("applicant_id") or "demo-client",
        "applicant_name": payload.get("applicant_name") or payload.get("values", {}).get("109935") or "mock-user",
        "title": payload.get("title") or payload.get("subject") or "mock purchase request",
        "currency": payload.get("currency") or "JPY",
        "total_amount": total_amount,
        "items": normalized_items,
        "requested_at": _utc_epoch(),
    }


def _to_rakuraku_format(payload: Dict[str, Any]) -> Dict[str, Any]:
    purchase = _build_mock_purchase(payload)
    return {
        "dbSchemaId": "mock-schema-id",
        "keyMode": "0",
        "values": {
            "applicant_name": purchase["applicant_name"],
            "client_id": purchase["client_id"],
            "title": purchase["title"],
            "total_amount": str(purchase["total_amount"]),
            "currency": purchase["currency"],
            "details": [
                {
                    "item_code": item["item_code"],
                    "item_name": item["item_name"],
                    "department": item["department"],
                    "quantity": str(item["quantity"]),
                    "unit_price": str(item["unit_price"]),
                }
                for item in purchase["items"]
            ],
        },
    }


def _to_bakuraku_format(payload: Dict[str, Any]) -> Dict[str, Any]:
    purchase = _build_mock_purchase(payload)
    return {
        "applicationId": f"bak-{purchase['request_id']}",
        "applicant": {
            "id": purchase["client_id"],
            "name": purchase["applicant_name"],
        },
        "title": purchase["title"],
        "currency": purchase["currency"],
        "amount": purchase["total_amount"],
        "lines": purchase["items"],
    }


def health_check(request):
    return _json_response(
        {
            "service": "proxy-purchase-systems-mock",
            "status": "ok",
            "available_functions": [
                "health_check",
                "client_input_api",
                "transform_format_api",
                "rakuraku_sales_api",
                "bakuraku_api",
                "cache_db_api",
                "bakuraku_status_polling_api",
            ],
        }
    )


def client_input_api(request):
    payload = _parse_request_json(request)
    purchase = _build_mock_purchase(payload)
    cache_key = f"req:{purchase['request_id']}"

    _CACHE_STORE[cache_key] = {
        "type": "client_request",
        "value": deepcopy(purchase),
        "created_at": _utc_epoch(),
    }

    return _json_response(
        {
            "message": "Client input accepted by mock endpoint.",
            "cache_key": cache_key,
            "normalized_request": purchase,
        },
        202,
    )


def transform_format_api(request):
    payload = _parse_request_json(request)
    target = payload.get("target") or "both"
    source = payload.get("source") or "client_input"
    request_payload = payload.get("payload") or payload

    response_body = {
        "source": source,
        "target": target,
    }

    if target in ("rakuraku", "both"):
        response_body["rakuraku"] = _to_rakuraku_format(request_payload)

    if target in ("bakuraku", "both"):
        response_body["bakuraku"] = _to_bakuraku_format(request_payload)

    return _json_response(response_body)


def rakuraku_sales_api(request):
    payload = _parse_request_json(request)
    rakuraku_payload = payload.get("payload") or _to_rakuraku_format(payload)
    external_id = f"rak-{uuid.uuid4().hex[:10]}"

    _CACHE_STORE[f"link:rakuraku:{external_id}"] = {
        "type": "rakuraku_mapping",
        "value": {
            "external_id": external_id,
            "source_request_id": payload.get("request_id"),
            "payload": deepcopy(rakuraku_payload),
        },
        "created_at": _utc_epoch(),
    }

    return _json_response(
        {
            "message": "Mock request accepted by Rakuraku Hanbai connector.",
            "external_id": external_id,
            "status": "registered",
            "payload": rakuraku_payload,
        },
        201,
    )


def bakuraku_api(request):
    payload = _parse_request_json(request)
    bakuraku_payload = payload.get("payload") or _to_bakuraku_format(payload)
    application_id = bakuraku_payload.get("applicationId") or f"bak-{uuid.uuid4().hex[:10]}"

    _STATUS_STORE[application_id] = {
        "application_id": application_id,
        "status_sequence": ["submitted", "under_review", "approved"],
        "current_index": 0,
        "updated_at": _utc_epoch(),
    }

    _CACHE_STORE[f"link:bakuraku:{application_id}"] = {
        "type": "bakuraku_mapping",
        "value": {
            "application_id": application_id,
            "source_request_id": payload.get("request_id"),
            "payload": deepcopy(bakuraku_payload),
        },
        "created_at": _utc_epoch(),
    }

    return _json_response(
        {
            "message": "Mock request accepted by Bakuraku connector.",
            "application_id": application_id,
            "status": "submitted",
            "payload": bakuraku_payload,
        },
        201,
    )


def cache_db_api(request):
    payload = _parse_request_json(request)
    method = request.method.upper()

    if method == "GET":
        key = request.args.get("key")
        if not key:
            return _json_response({"error": "Query parameter 'key' is required."}, 400)

        cached = _CACHE_STORE.get(key)
        if cached is None:
            return _json_response({"message": "Cache key not found.", "key": key}, 404)

        return _json_response({"key": key, "record": cached})

    key = payload.get("key") or f"cache:{uuid.uuid4().hex[:12]}"
    value = payload.get("value") or {}
    ttl_seconds = int(payload.get("ttl_seconds") or 3600)

    _CACHE_STORE[key] = {
        "type": payload.get("type") or "generic",
        "value": value,
        "ttl_seconds": ttl_seconds,
        "created_at": _utc_epoch(),
    }

    return _json_response(
        {
            "message": "Cache record stored in mock DB.",
            "key": key,
            "record": _CACHE_STORE[key],
        },
        201,
    )


def bakuraku_status_polling_api(request):
    payload = _parse_request_json(request)
    application_id = payload.get("application_id") or request.args.get("application_id")

    if not application_id:
        return _json_response({"error": "'application_id' is required."}, 400)

    status_info = _STATUS_STORE.get(application_id)
    if status_info is None:
        return _json_response(
            {
                "message": "Application was not found in mock status store.",
                "application_id": application_id,
            },
            404,
        )

    if request.method.upper() == "POST" and payload.get("advance", True):
        if status_info["current_index"] < len(status_info["status_sequence"]) - 1:
            status_info["current_index"] += 1
            status_info["updated_at"] = _utc_epoch()

    current_status = status_info["status_sequence"][status_info["current_index"]]
    is_finished = current_status in {"approved", "rejected", "cancelled"}

    return _json_response(
        {
            "application_id": application_id,
            "status": current_status,
            "is_finished": is_finished,
            "updated_at": status_info["updated_at"],
            "remaining_steps": status_info["status_sequence"][status_info["current_index"] + 1 :],
        }
    )
