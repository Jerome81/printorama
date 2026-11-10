#!/usr/bin/env python3
"""Test program that sends fake receipt data to the Epson TM-m30 receipt printer server."""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import urllib.error
import urllib.request
from typing import Any


def build_fake_receipt(payment_method: str = "cash", mock: bool = False) -> dict[str, Any]:
    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    items = [
        {
            "name": "Classic T-Shirt",
            "variant": "Grösse L / Schwarz",
            "quantity": 2,
            "unit_price": 19.90,
            "gross_price": 39.80,
            "discount": 4.80,
            "total_amount": 35.00,
            "tax_code": 8.1,
        },
        {
            "name": "Blue Jeans",
            "variant": None,
            "quantity": 1,
            "unit_price": 49.50,
            "gross_price": 49.50,
            "discount": 0.00,
            "total_amount": 49.50,
            "tax_code": 8.1,
        },
        {
            "name": "Bio Eistee 500ml",
            "variant": None,
            "quantity": 3,
            "unit_price": 3.50,
            "gross_price": 10.50,
            "discount": 0.00,
            "total_amount": 10.50,
            "tax_code": 2.6,
        },
    ]

    subtotal = 99.80
    total_discount = 4.80
    voucher_amount = 10.00
    total_amount = 85.00

    payload: dict[str, Any] = {
        "order_id": 1042,
        "order_name": "Testbestellung",
        "cash_register": "Hauptkasse 1",
        "cashier": "Admin",
        "timestamp": now_str,
        "currency": "CHF",
        "payment_method": payment_method,
        "subtotal": subtotal,
        "total_discount": total_discount,
        "voucher_amount": voucher_amount,
        "voucher_code": "PROMO-10",
        "total_amount": total_amount,
        "items": items,
    }

    if payment_method == "cash":
        payload["amount_tendered"] = 100.00
        payload["change"] = 15.00
    else:
        payload["amount_tendered"] = total_amount
        payload["change"] = 0.00

    if mock:
        payload["mock"] = True

    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send fake receipt data to the Epson TM-m30 receipt printer server."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Printer server host or IP address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8081,
        help="Printer server TCP port (default: 8081)",
    )
    parser.add_argument(
        "--payment-method",
        choices=["cash", "card"],
        default="cash",
        help="Transaction payment method to test ('cash' or 'card', default: 'cash')",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Ask the server to simulate printing without opening USB hardware",
    )
    args = parser.parse_args(argv)

    url = f"http://{args.host}:{args.port}/print"
    payload = build_fake_receipt(payment_method=args.payment_method, mock=args.mock)

    print(f"Sending test receipt ({args.payment_method}) to {url} ...")
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body_bytes,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            print(f"Server responded with HTTP {resp.status}:")
            print(json.dumps({k: v for k, v in resp_data.items() if k != "receipt_preview"}, indent=2, ensure_ascii=False))
            if "receipt_preview" in resp_data:
                print("\nReceipt Preview:")
                print(resp_data["receipt_preview"])
            return 0
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP error {exc.code}: {err_body}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Failed to connect to printer server at {url}: {exc.reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

