"""Thermal printer support for KFR POS receipts.

The app can print directly to a thermal printer over raw TCP/IP or a local USB
serial device. The UI also keeps the browser-based print fallback, so a failing or
missing printer never blocks bill creation.
"""

from __future__ import annotations

import os
import socket
from datetime import datetime
from typing import Any, Dict, Iterable, List


ESC = b"\x1b"
GS = b"\x1d"
LF = b"\n"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, str(default)).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _limit_text(value: str, max_len: int = 24) -> str:
    text = _safe_text(value)
    if len(text.encode("utf-8")) <= max_len:
        return text
    out = text.encode("utf-8", "ignore")[:max_len].decode("utf-8", "ignore")
    return out.rstrip()


def _pad_right_left(left: str, right: str, width: int = 42) -> str:
    left_bytes = len(left.encode("utf-8"))
    right_bytes = len(right.encode("utf-8"))
    gap = max(0, width - left_bytes - right_bytes)
    return f"{left}{' ' * gap}{right}"


def _receipt_items_block(items: Iterable[Dict[str, Any]]) -> str:
    rows: List[str] = []
    for item in items:
        name = _safe_text(item.get("item_name") or item.get("name") or "Item")
        qty = int(item.get("qty") or 0)
        line_total = _money(item.get("line_total") or (item.get("price") or 0) * qty)
        rows.append(_pad_right_left(f"{_limit_text(name, 22)} x{qty}", f"{line_total:,.2f}", 42))
    return "\n".join(rows)


def build_receipt_text(payload: Dict[str, Any]) -> str:
    bill_type = _safe_text(payload.get("bill_type") or "FOOD").upper()
    bill_no = _safe_text(payload.get("bill_no") or "BILL")
    created_at = _safe_text(payload.get("created_at") or datetime.now().strftime("%d-%m-%Y %H:%M"))
    customer_name = _safe_text(payload.get("customer_name") or "Walk-in")
    customer_phone = _safe_text(payload.get("customer_phone") or "-")
    payment_method = _safe_text(payload.get("payment_method") or "Cash")
    subtotal = _money(payload.get("subtotal"))
    tax = _money(payload.get("tax"))
    discount = _money(payload.get("discount"))
    grand_total = _money(payload.get("grand_total"))
    table_no = _safe_text(payload.get("table_no") or payload.get("table_label") or "")
    items = payload.get("items") or []

    lines = [
        "",
        "KING FAMILY RESTAURANT",
        "------------------------",
        f"{bill_type} BILL",
        f"Bill No: {bill_no}",
        f"Date: {created_at}",
        f"Table: {table_no or 'Counter Sale'}",
        "------------------------",
        f"Customer: {customer_name}",
        f"Phone: {customer_phone}",
        f"Payment: {payment_method}",
        "",
        _receipt_items_block(items),
        "",
        "------------------------",
        _pad_right_left("Subtotal", f"{subtotal:,.2f}", 42),
        _pad_right_left("Tax", f"{tax:,.2f}", 42),
        _pad_right_left("Discount", f"-{discount:,.2f}", 42),
        _pad_right_left("GRAND TOTAL", f"{grand_total:,.2f}", 42),
        "------------------------",
        "Thank you, visit again!",
        "",
        "",
    ]
    return "\n".join(lines)


def _printer_config() -> Dict[str, Any]:
    enabled = _env_bool("PRINTER_ENABLED", False)
    mode = (os.environ.get("PRINTER_MODE") or "network").strip().lower()
    host = (os.environ.get("PRINTER_HOST") or "").strip()
    port = int(os.environ.get("PRINTER_PORT") or "9100")
    path = (os.environ.get("PRINTER_PATH") or "").strip()
    timeout = float(os.environ.get("PRINTER_TIMEOUT") or "5")

    if not enabled:
        return {"enabled": False, "mode": mode, "host": host, "port": port, "path": path, "timeout": timeout}

    if mode == "network" and not host:
        raise ValueError("PRINTER_HOST is required when PRINTER_MODE=network")
    if mode in {"usb", "serial", "file"} and not path:
        raise ValueError("PRINTER_PATH is required for USB/serial/file printer mode")

    return {"enabled": True, "mode": mode, "host": host, "port": port, "path": path, "timeout": timeout}


def _build_escpos_bytes(text: str) -> bytes:
    body = text.encode("utf-8", "ignore")
    return b"\x1b\x40" + b"\x1b\x74\x00" + body + b"\n\n" + b"\x1d\x56\x00"


def print_receipt(payload: Dict[str, Any]) -> Dict[str, Any]:
    config = _printer_config()
    if not config["enabled"]:
        return {"printed": False, "mode": config["mode"], "status": "disabled"}

    receipt = build_receipt_text(payload)
    payload_bytes = _build_escpos_bytes(receipt)

    mode = config["mode"]
    try:
        if mode == "network":
            with socket.create_connection((config["host"], int(config["port"])), timeout=config["timeout"]) as sock:
                sock.sendall(payload_bytes)
        elif mode in {"usb", "serial", "file"}:
            path = config["path"]
            with open(path, "wb") as fh:
                fh.write(payload_bytes)
        else:
            raise ValueError(f"Unsupported PRINTER_MODE: {mode}")
    except Exception as exc:
        return {"printed": False, "mode": mode, "status": "failed", "error": str(exc)}

    return {"printed": True, "mode": mode, "status": "ok"}
