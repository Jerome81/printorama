# Receipt Printer Server (Epson TM-m30)

Lightweight Python HTTP/JSON server that prints POS receipts on an **Epson TM-m30** (`04b8:0e20`) via `python-escpos`.

## Starting the Server

On the POS / printer machine:

```bash
cd /home/outpost/code/printer
python3 server.py --host 0.0.0.0 --port 8081
```

To run in **Mock Mode** (prints the formatted receipt to stdout without requiring physical USB hardware):

```bash
cd /home/outpost/code/printer
python3 server.py --host 0.0.0.0 --port 8081 --mock
```

### CLI Options & Environment Variables
- `--host` (`PRINTER_HOST`, default `0.0.0.0`)
- `--port` (`PRINTER_PORT`, default `8081`)
- `--vendor-id` (`PRINTER_VENDOR_ID`, default `04b8`)
- `--product-id` (`PRINTER_PRODUCT_ID`, default `0e20`)
- `--mock` (`PRINTER_MOCK=1`)

## Testing with Fake Data

With `server.py` running, execute `test_print.py`:

```bash
cd /home/outpost/code/printer
# Print a cash transaction receipt to the local printer server:
python3 test_print.py --host 127.0.0.1 --port 8081 --payment-method cash

# Print a card transaction receipt:
python3 test_print.py --host 127.0.0.1 --port 8081 --payment-method card

# Test in mock mode (without physical USB printer attached):
python3 test_print.py --host 127.0.0.1 --port 8081 --mock
```

## API Endpoints

### `GET /health`
Returns server health, configured USB device ID (`04b8:0e20`), and mock mode status.

### `POST /print`
Prints a receipt with all line items, discounts, tax rates, payment method, cash tendered/change, and MWST summary.

### `GET /open_drawer`
Sends the ESC/POS cash drawer kick pulse (`printer.cashdraw(pin)`, default `pin=2`, supports `?pin=2` or `?pin=5`).

```bash
cd /home/outpost/code/printer
python3 test_drawer.py --host 127.0.0.1 --port 8081
```

