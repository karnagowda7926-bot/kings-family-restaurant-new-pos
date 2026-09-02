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

# Thermal printers speak a single-byte code page, not UTF-8. We drive them in
# PC437 (the default on virtually every 80mm ESC/POS clone), so anything outside
# that page has to be folded down to plain ASCII before it is sent.
PRINTER_CODEPAGE = "cp437"
PRINTER_WIDTH = 42  # characters per line for font A on an 80mm roll

# Header identity. A food bill is a GST invoice and carries the restaurant name
# and GSTIN; a bar bill carries neither - the slip stays anonymous.
RESTAURANT_NAME = os.environ.get("RESTAURANT_NAME", "KING FAMILY RESTAURANT")
GST_NUMBER = os.environ.get("GST_NUMBER", "29EAFPK7266B1ZK")

_ASCII_FOLD = {
    "₹": "Rs.",   # rupee sign
    "·": "-",     # middle dot (used in "T5 . TABLE BILL")
    "•": "*",     # bullet
    "−": "-",     # minus sign
    "–": "-",     # en dash
    "—": "-",     # em dash
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "…": "...",
    " ": " ",     # non-breaking space
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, str(default)).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _qty(value: Any) -> str:
    """Quantities are usually whole numbers but must never crash the receipt."""
    amount = _money(value)
    return str(int(amount)) if amount == int(amount) else f"{amount:g}"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    for src, dst in _ASCII_FOLD.items():
        text = text.replace(src, dst)
    # Drop anything the printer's code page still cannot render.
    return text.encode(PRINTER_CODEPAGE, "replace").decode(PRINTER_CODEPAGE)


def _limit_text(value: Any, max_len: int = 24) -> str:
    return _safe_text(value)[:max_len].rstrip()


def _pad_right_left(left: str, right: str, width: int = PRINTER_WIDTH) -> str:
    gap = max(1, width - len(left) - len(right))
    return f"{left}{' ' * gap}{right}"


def _center(text: Any, width: int = PRINTER_WIDTH) -> str:
    return _safe_text(text)[:width].center(width).rstrip()


def _rule(char: str = "-", width: int = PRINTER_WIDTH) -> str:
    return char * width


def _receipt_items_block(items: Iterable[Dict[str, Any]]) -> str:
    rows: List[str] = []
    for item in items or []:
        name = _limit_text(item.get("item_name") or item.get("name") or "Item", 26)
        qty = _qty(item.get("qty"))
        if item.get("line_total") is not None:
            line_total = _money(item.get("line_total"))
        else:
            line_total = _money(item.get("price")) * _money(item.get("qty"))
        rows.append(_pad_right_left(f"{name} x{qty}", f"{line_total:,.2f}"))
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

    if bill_type == "ALCOHOL":
        header = [_center("BAR BILL")]
    else:
        header = [
            _center(RESTAURANT_NAME),
            _center(f"GSTIN: {GST_NUMBER}"),
            _center(f"{bill_type} BILL"),
        ]

    lines = [
        *header,
        _rule(),
        f"Bill No: {bill_no}",
        f"Date: {created_at}",
        f"Table: {table_no or 'Counter Sale'}",
        _rule(),
        f"Customer: {customer_name}",
        f"Phone: {customer_phone}",
        f"Payment: {payment_method}",
        _rule(),
        _receipt_items_block(items),
        _rule(),
        _pad_right_left("Subtotal", f"{subtotal:,.2f}"),
        _pad_right_left("Tax", f"{tax:,.2f}"),
        _pad_right_left("Discount", f"-{discount:,.2f}" if discount else f"{0:,.2f}"),
        _pad_right_left("GRAND TOTAL", f"{grand_total:,.2f}"),
        _rule("="),
        _center("Thank you, visit again!"),
    ]
    return "\n".join(lines)


def _printer_config() -> Dict[str, Any]:
    enabled = _env_bool("PRINTER_ENABLED", False)
    mode = (os.environ.get("PRINTER_MODE") or "network").strip().lower()
    host = (os.environ.get("PRINTER_HOST") or "").strip()
    path = (os.environ.get("PRINTER_PATH") or "").strip()

    try:
        port = int(os.environ.get("PRINTER_PORT") or "9100")
    except ValueError:
        port = 9100
    try:
        timeout = float(os.environ.get("PRINTER_TIMEOUT") or "5")
    except ValueError:
        timeout = 5.0

    config = {
        "enabled": enabled, "mode": mode, "host": host, "port": port,
        "path": path, "timeout": timeout, "error": "",
    }
    if not enabled:
        return config

    if mode not in {"network", "usb", "serial", "file"}:
        config["error"] = f"Unsupported PRINTER_MODE: {mode}"
    elif mode == "network" and not host:
        config["error"] = "PRINTER_HOST is required when PRINTER_MODE=network"
    elif mode != "network" and not path:
        config["error"] = "PRINTER_PATH is required for USB/serial/file printer mode"
    return config


def _build_escpos_bytes(text: str) -> bytes:
    body = text.encode(PRINTER_CODEPAGE, "replace")
    return b"".join([
        ESC + b"@",                  # initialise
        ESC + b"t" + b"\x00",        # select code page PC437
        ESC + b"a" + b"\x00",        # left align
        body,
        LF * 4,                      # feed the cut line clear of the text
        GS + b"V" + b"B" + b"\x00",  # partial cut
    ])


def print_receipt(payload: Dict[str, Any]) -> Dict[str, Any]:
    config = _printer_config()
    if not config["enabled"]:
        return {"printed": False, "mode": config["mode"], "status": "disabled"}
    if config["error"]:
        return {"printed": False, "mode": config["mode"], "status": "misconfigured",
                "error": config["error"]}

    payload_bytes = _build_escpos_bytes(build_receipt_text(payload))
    mode = config["mode"]

    try:
        if mode == "network":
            with socket.create_connection((config["host"], config["port"]), timeout=config["timeout"]) as sock:
                sock.sendall(payload_bytes)
        else:
            with open(config["path"], "wb") as fh:
                fh.write(payload_bytes)
                fh.flush()
    except Exception as exc:
        return {"printed": False, "mode": mode, "status": "failed", "error": str(exc)}

    return {"printed": True, "mode": mode, "status": "ok"}
