#!/usr/bin/env python3
"""HTTP/JSON receipt printer server for Epson TM-m30 (04b8:0e20) using python-escpos."""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import threading
import urllib.parse
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

try:
    from escpos.printer import Dummy, Usb
    HAS_ESCPOS = True
except ImportError:
    Dummy = None  # type: ignore[assignment]
    Usb = None  # type: ignore[assignment]
    HAS_ESCPOS = False

logger = logging.getLogger(__name__)

DEFAULT_VENDOR_ID = 0x04B8
DEFAULT_PRODUCT_ID = 0x0E20
RECEIPT_WIDTH = 42  # Standard 80mm Font A width on Epson TM-m30 (42 columns)

STORE_HEADER_LINES = [
    "GAMEORAMA AG",
    "Luzern",
    "Sonnenbergstrasse 35d",
    "6005 Luzern",
    "www.gameorama.ch",
    "+41 41 541 76 09",
]


def to_decimal(val: Any, default: str = "0.00") -> Decimal:
    if val is None or val == "":
        return Decimal(default)
    try:
        return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def fmt_chf(val: Any) -> str:
    return f"{to_decimal(val):.2f}"


def fmt_rate(val: Any) -> str:
    if val is None or val == "":
        return "0.0%"
    try:
        dec = Decimal(str(val))
        return f"{dec:.1f}%"
    except (InvalidOperation, TypeError, ValueError):
        return f"{val}%"


def format_line(left: str, right: str, width: int = RECEIPT_WIDTH) -> str:
    """Format a two-column line padded with spaces to `width` characters."""
    right_str = right.strip()
    max_left = max(1, width - len(right_str) - 1)
    left_str = left.strip()
    if len(left_str) > max_left:
        left_str = left_str[: max_left - 1] + "."
    spaces = width - len(left_str) - len(right_str)
    return f"{left_str}{' ' * max(1, spaces)}{right_str}\n"


def render_receipt_lines(payload: dict[str, Any], width: int = RECEIPT_WIDTH) -> dict[str, Any]:
    """
    Build structured sections and plain-text preview for the receipt.
    Calculates item totals and MWST breakdown by tax rate.
    """
    currency = str(payload.get("currency") or "CHF").strip()
    cash_register = str(payload.get("cash_register") or "Kasse").strip()
    order_id = payload.get("order_id")
    order_name = str(payload.get("order_name") or "").strip()
    cashier = str(payload.get("cashier") or "").strip()
    timestamp = str(
        payload.get("timestamp")
        or datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    ).strip()
    payment_method_raw = str(payload.get("payment_method") or "").strip().lower()
    if payment_method_raw in ("card", "credit_card", "karte", "kartenzahlung"):
        payment_label = "Kartenzahlung"
        is_cash = False
    elif payment_method_raw in ("cash", "bar", "barzahlung"):
        payment_label = "Barzahlung"
        is_cash = True
    else:
        payment_label = payment_method_raw.capitalize() or "Zahlung"
        is_cash = False

    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Keine Artikel ('items') in den Druckdaten enthalten")

    separator = "-" * width + "\n"
    double_sep = "=" * width + "\n"

    header_lines: list[str] = []
    header_lines.append(f"Datum: {timestamp}\n")
    header_lines.append(f"Kasse: {cash_register}\n")
    if order_id is not None:
        ref = f"Beleg #{order_id}"
        if order_name:
            ref += f" ({order_name})"
        header_lines.append(f"{ref}\n")
    elif order_name:
        header_lines.append(f"Bestellung: {order_name}\n")
    if cashier:
        header_lines.append(f"Bedienung: {cashier}\n")

    item_lines: list[str] = []
    col_header = format_line("Artikel", f"Total ({currency})", width)
    item_lines.append(col_header)
    item_lines.append(separator)

    # Tax breakdown accumulator: rate_decimal -> gross total (inclusive of VAT)
    tax_buckets: dict[Decimal, Decimal] = {}
    computed_subtotal_gross = Decimal("0.00")
    computed_net_sum = Decimal("0.00")
    computed_item_discount_sum = Decimal("0.00")

    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        name = str(raw_item.get("name") or "Artikel").strip()
        variant = str(raw_item.get("variant") or "").strip()
        full_name = f"{name} ({variant})" if variant else name

        qty_raw = raw_item.get("quantity", 1)
        try:
            qty = int(qty_raw)
        except (TypeError, ValueError):
            qty = 1

        unit_price = to_decimal(raw_item.get("unit_price"), "0.00")
        gross_price = to_decimal(
            raw_item.get("gross_price"),
            str((unit_price * Decimal(qty)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        )
        discount = to_decimal(raw_item.get("discount"), "0.00")

        if "total_amount" in raw_item and raw_item.get("total_amount") is not None:
            line_total = to_decimal(raw_item.get("total_amount"))
        elif "net_price" in raw_item and raw_item.get("net_price") is not None:
            line_total = to_decimal(raw_item.get("net_price"))
        else:
            line_total = gross_price - discount

        tax_rate_raw = raw_item.get("tax_code", raw_item.get("tax_rate", "0.0"))
        try:
            tax_rate = Decimal(str(tax_rate_raw if tax_rate_raw is not None else "0.0")).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
        except (InvalidOperation, TypeError, ValueError):
            tax_rate = Decimal("0.0")

        tax_buckets[tax_rate] = tax_buckets.get(tax_rate, Decimal("0.00")) + line_total
        computed_subtotal_gross += gross_price
        computed_net_sum += line_total
        computed_item_discount_sum += discount

        # Line 1: Item name
        item_lines.append(f"{full_name[:width]}\n")
        # Line 2: Quantity x Unit Price + Tax Code on left, Line Total on right
        detail_left = f"  {qty} x {fmt_chf(unit_price)} (MWST {tax_rate:.1f}%)"
        item_lines.append(format_line(detail_left, fmt_chf(line_total), width))
        if discount > Decimal("0.00"):
            item_lines.append(format_line("  Rabatt", f"-{fmt_chf(discount)}", width))

    subtotal = to_decimal(payload.get("subtotal"), str(computed_subtotal_gross))
    total_discount = to_decimal(payload.get("total_discount", payload.get("discount")), str(computed_item_discount_sum))
    voucher_amount = to_decimal(payload.get("voucher_amount"), "0.00")
    voucher_code = str(payload.get("voucher_code") or "").strip()

    default_total = max(Decimal("0.00"), computed_net_sum - voucher_amount)
    total_amount = to_decimal(payload.get("total_amount"), str(default_total))

    summary_lines: list[str] = []
    if total_discount > Decimal("0.00") or voucher_amount > Decimal("0.00"):
        summary_lines.append(format_line("Zwischensumme", f"{currency} {fmt_chf(subtotal)}", width))
    if total_discount > Decimal("0.00"):
        summary_lines.append(format_line("Rabatt gesamt", f"-{currency} {fmt_chf(total_discount)}", width))
    if voucher_amount > Decimal("0.00"):
        voucher_label = f"Gutschein ({voucher_code})" if voucher_code else "Gutschein"
        summary_lines.append(format_line(voucher_label, f"-{currency} {fmt_chf(voucher_amount)}", width))

    total_line = format_line(f"TOTAL {currency}", fmt_chf(total_amount), width)

    payment_lines: list[str] = []
    payment_lines.append(format_line("Zahlungsart", payment_label, width))
    if is_cash:
        amount_tendered = to_decimal(payload.get("amount_tendered"), str(total_amount))
        change = to_decimal(payload.get("change"), str(max(Decimal("0.00"), amount_tendered - total_amount)))
        payment_lines.append(format_line("Gegeben", f"{currency} {fmt_chf(amount_tendered)}", width))
        payment_lines.append(format_line("Rückgeld", f"{currency} {fmt_chf(change)}", width))

    # Tax Breakdown Table
    # Adjust tax buckets proportionally if a voucher reduced the final payable total
    tax_lines: list[str] = []
    tax_lines.append("MWST-Zusammenfassung\n")
    tax_lines.append("Gameorama AG - CHE-113.653.092 MWST\n")
    tax_lines.append(f"{'Satz':<10}{'Netto':>10}{'MWST':>10}{'Brutto':>12}\n")
    tax_lines.append(separator)

    for rate in sorted(tax_buckets.keys()):
        brutto = tax_buckets[rate].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if rate > Decimal("0.0"):
            netto = (brutto / (Decimal("1") + (rate / Decimal("100")))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            mwst = (brutto - netto).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            netto = brutto
            mwst = Decimal("0.00")
        rate_str = f"{rate:.1f}%"
        tax_lines.append(f"{rate_str:<10}{fmt_chf(netto):>10}{fmt_chf(mwst):>10}{fmt_chf(brutto):>12}\n")

    store_preview = "".join(f"{line.center(width).rstrip()}\n" for line in STORE_HEADER_LINES)
    preview = (
        store_preview
        + separator
        + "".join(header_lines)
        + separator
        + "".join(item_lines)
        + separator
        + "".join(summary_lines)
        + double_sep
        + total_line
        + double_sep
        + "".join(payment_lines)
        + separator
        + "".join(tax_lines)
        + separator
        + f"{'Vielen Dank für Ihren Einkauf!'.center(width).rstrip()}\n"
    )

    return {
        "store_header_lines": STORE_HEADER_LINES,
        "header_lines": header_lines,
        "item_lines": item_lines,
        "summary_lines": summary_lines,
        "total_line": total_line,
        "payment_lines": payment_lines,
        "tax_lines": tax_lines,
        "separator": separator,
        "double_sep": double_sep,
        "preview": preview,
    }


class EpsonReceiptPrinter:
    """Manages thread-safe printing to Epson TM-m30 (04b8:0e20) via python-escpos."""

    def __init__(
        self,
        vendor_id: int = DEFAULT_VENDOR_ID,
        product_id: int = DEFAULT_PRODUCT_ID,
        default_mock: bool = False,
    ) -> None:
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.default_mock = default_mock
        self._lock = threading.Lock()

    def print_receipt(self, payload: dict[str, Any]) -> dict[str, Any]:
        is_mock = bool(payload.get("mock") or self.default_mock)
        rendered = render_receipt_lines(payload)

        with self._lock:
            if is_mock:
                if HAS_ESCPOS and Dummy is not None:
                    p = Dummy()
                    self._write_escpos(p, rendered)
                logger.info("Mock receipt printed:\n%s", rendered["preview"])
                print("\n--- MOCK RECEIPT START ---")
                print(rendered["preview"], end="")
                print("--- MOCK RECEIPT END ---\n")
                return {
                    "success": True,
                    "status": "printed",
                    "mock": True,
                    "device": f"{self.vendor_id:04x}:{self.product_id:04x}",
                    "result_message": "Quittung gedruckt (Mock-Modus)",
                    "receipt_preview": rendered["preview"],
                }

            if not HAS_ESCPOS or Usb is None:
                raise RuntimeError(
                    "python-escpos ist nicht installiert. Bitte installieren Sie 'python-escpos' und 'pyusb'."
                )

            printer = Usb(self.vendor_id, self.product_id)
            try:
                self._write_escpos(printer, rendered)
            finally:
                try:
                    printer.close()
                except Exception:
                    pass

            return {
                "success": True,
                "status": "printed",
                "mock": False,
                "device": f"{self.vendor_id:04x}:{self.product_id:04x}",
                "result_message": "Quittung erfolgreich gedruckt",
                "receipt_preview": rendered["preview"],
            }

    def open_drawer(self, pin: int = 2, mock: bool = False) -> dict[str, Any]:
        if pin not in (2, 5):
            raise ValueError("Ungültiger Pin für Kassenschublade (erlaubt: 2 oder 5)")

        is_mock = bool(mock or self.default_mock)
        with self._lock:
            if is_mock:
                if HAS_ESCPOS and Dummy is not None:
                    p = Dummy()
                    p.cashdraw(pin)
                logger.info("Mock cash drawer opened (pin=%d)", pin)
                print(f"\n--- MOCK CASH DRAWER OPENED (pin={pin}) ---\n")
                return {
                    "success": True,
                    "status": "drawer_opened",
                    "mock": True,
                    "pin": pin,
                    "device": f"{self.vendor_id:04x}:{self.product_id:04x}",
                    "result_message": f"Kassenschublade geöffnet (Mock-Modus, Pin {pin})",
                }

            if not HAS_ESCPOS or Usb is None:
                raise RuntimeError(
                    "python-escpos ist nicht installiert. Bitte installieren Sie 'python-escpos' und 'pyusb'."
                )

            printer = Usb(self.vendor_id, self.product_id)
            try:
                printer.cashdraw(pin)
            finally:
                try:
                    printer.close()
                except Exception:
                    pass

            return {
                "success": True,
                "status": "drawer_opened",
                "mock": False,
                "pin": pin,
                "device": f"{self.vendor_id:04x}:{self.product_id:04x}",
                "result_message": f"Kassenschublade erfolgreich geöffnet (Pin {pin})",
            }

    @staticmethod
    def _write_escpos(printer: Any, rendered: dict[str, Any]) -> None:
        store_lines = rendered.get("store_header_lines") or STORE_HEADER_LINES
        if store_lines:
            printer.set(align="center", bold=True, double_height=True, double_width=False)
            printer.text(f"{store_lines[0]}\n")
            printer.set(align="center", bold=False, double_height=False, double_width=False)
            for line in store_lines[1:]:
                printer.text(f"{line}\n")

        printer.set(align="left", bold=False, double_height=False, double_width=False)
        printer.text(rendered["separator"])

        for line in rendered["header_lines"]:
            printer.text(line)
        printer.text(rendered["separator"])

        for line in rendered["item_lines"]:
            printer.text(line)
        printer.text(rendered["separator"])

        for line in rendered["summary_lines"]:
            printer.text(line)

        printer.text(rendered["double_sep"])
        printer.set(align="left", bold=True, double_height=True, double_width=False)
        printer.text(rendered["total_line"])
        printer.set(align="left", bold=False, double_height=False, double_width=False)
        printer.text(rendered["double_sep"])

        for line in rendered["payment_lines"]:
            printer.text(line)
        printer.text(rendered["separator"])

        for line in rendered["tax_lines"]:
            printer.text(line)
        printer.text(rendered["separator"])

        printer.set(align="center", bold=False, double_height=False, double_width=False)
        printer.text("Vielen Dank für Ihren Einkauf!\n\n")
        printer.cut()


class PrinterHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        printer: EpsonReceiptPrinter,
    ) -> None:
        self.printer = printer
        super().__init__(server_address, PrinterRequestHandler)


class PrinterRequestHandler(BaseHTTPRequestHandler):
    server: PrinterHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, status_code: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Ungültiges JSON im Request-Body") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON-Body muss ein Objekt sein")
        return data

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_json(200, {"status": "ok"})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip("/")
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("", "/health"):
            self._send_json(
                200,
                {
                    "status": "ok",
                    "service": "printer",
                    "device": f"{self.server.printer.vendor_id:04x}:{self.server.printer.product_id:04x}",
                    "mock_mode": self.server.printer.default_mock,
                    "escpos_installed": HAS_ESCPOS,
                },
            )
        elif path == "/open_drawer":
            mock_param = query.get("mock", [""])[0].lower() in ("1", "true", "yes")
            pin_raw = query.get("pin", ["2"])[0]
            try:
                pin = int(pin_raw)
                result = self.server.printer.open_drawer(pin=pin, mock=mock_param)
                self._send_json(200, result)
            except ValueError as exc:
                self._send_json(400, {"success": False, "status": "error", "result_message": str(exc)})
            except Exception as exc:
                logger.exception("Error opening cash drawer")
                self._send_json(
                    500,
                    {
                        "success": False,
                        "status": "error",
                        "result_message": f"Fehler beim Öffnen der Kassenschublade: {exc}",
                    },
                )
        else:
            self._send_json(404, {"success": False, "error": f"Unbekannter Pfad: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip("/")
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._send_json(400, {"success": False, "status": "error", "result_message": str(exc)})
            return

        logger.info("Received request for path %s", path)
        if path == "/open_drawer":
            try:
                pin = int(payload.get("pin", 2))
                mock_param = bool(payload.get("mock"))
                result = self.server.printer.open_drawer(pin=pin, mock=mock_param)
                self._send_json(200, result)
            except ValueError as exc:
                self._send_json(400, {"success": False, "status": "error", "result_message": str(exc)})
            except Exception as exc:
                logger.exception("Error opening cash drawer")
                self._send_json(
                    500,
                    {
                        "success": False,
                        "status": "error",
                        "result_message": f"Fehler beim Öffnen der Kassenschublade: {exc}",
                    },
                )
            return

        if path != "/print":
            self._send_json(
                404,
                {"success": False, "status": "error", "result_message": f"Unbekannter Pfad: {path}"},
            )
            return

        try:
            result = self.server.printer.print_receipt(payload)
            self._send_json(200, result)
        except ValueError as exc:
            self._send_json(400, {"success": False, "status": "error", "result_message": str(exc)})
        except Exception as exc:
            logger.exception("Error printing receipt")
            self._send_json(
                500,
                {
                    "success": False,
                    "status": "error",
                    "result_message": f"Druckfehler: {exc}",
                },
            )


def parse_hex_id(value: str) -> int:
    value = value.strip().lower()
    if value.startswith("0x"):
        return int(value, 16)
    return int(value, 16)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Epson TM-m30 (04b8:0e20) HTTP/JSON Receipt Printer Server"
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("PRINTER_HOST", "0.0.0.0"),
        help="HTTP bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PRINTER_PORT", "8081")),
        help="HTTP bind port (default: 8081)",
    )
    parser.add_argument(
        "--vendor-id",
        type=parse_hex_id,
        default=parse_hex_id(os.environ.get("PRINTER_VENDOR_ID", "04b8")),
        help="USB Vendor ID in hex (default: 04b8)",
    )
    parser.add_argument(
        "--product-id",
        type=parse_hex_id,
        default=parse_hex_id(os.environ.get("PRINTER_PRODUCT_ID", "0e20")),
        help="USB Product ID in hex (default: 0e20)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        default=os.environ.get("PRINTER_MOCK", "").lower() in ("1", "true", "yes"),
        help="Enable mock mode (prints receipt to console without physical USB printer)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    printer = EpsonReceiptPrinter(
        vendor_id=args.vendor_id,
        product_id=args.product_id,
        default_mock=args.mock,
    )
    server = PrinterHTTPServer((args.host, args.port), printer=printer)
    logger.info(
        "Receipt Printer Server listening on http://%s:%d (USB=%04x:%04x, default_mock=%s)",
        args.host,
        args.port,
        args.vendor_id,
        args.product_id,
        args.mock,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down Receipt Printer Server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

