"""
USDC Receipt Generator

usdc_receipt.py

scans the most recent 999 blocks for USDC transfers and prints
a letterhead-styled receipt for each transfer.

must have python installed, and the requirements.txt, secrets.env, and letterhead.pdf 
must be in the same directory as this script.

NOTE: This file is not audited. Provided for demonstration purposes only.

Requirements
────────────
web3
python-dotenv 
pypdf 
reportlab 
pytz

secrets.env  (same directory)
─────────────────────────────
RPC_URL="https://eth.llamarpc.com"               # HTTP or WSS endpoint, #llamagang 
WATCH_ADDRESS="0xYourDestinationAddress"         # checksummed or lowercase
RECEIPT_DIR="C:/receipts"                        # will be created if absent
LETTERHEAD_PDF="C:/receipts/letterhead.pdf"      # single-page template with logo in header

How to run
──────────
.bat file to run the script in a virtual environment automatically (installing dependencies), for Windows.

run_script.bat
───────────────
@echo off
REM Check if the virtual environment folder exists; create it if not
if not exist venv (
   echo Creating virtual environment...
   python -m venv venv
)

REM Activate the virtual environment
call venv\Scripts\activate

REM Install dependencies
pip install -r requirements.txt

REM Run the script
python usdc_receipt.py

REM Keep the window open after execution
pause


Command line to run the script manually:

> python usdc_receipt.py

Output
───────────────
Receipts appear in RECEIPT_DIR with formatting: YYYYMMDD_[first six digits of sender address]_USDC_Receipt.pdf.  

Example:
    20250618_a1b2c3_USDC_Receipt.pdf
"""

# ── Imports ─────────────────────────────────────────────────────────
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
import pytz
from web3 import Web3
from web3._utils.events import get_event_data
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from io import BytesIO

# ── Constants ───────────────────────────────────────────────────────
USDC_ADDRESS = Web3.to_checksum_address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
DECIMALS = 10 ** 6                     # USDC uses 6 decimals
MIN_USDC = 1 * DECIMALS                # ignore < 1 USDC

TRANSFER_EVENT_ABI = {                
    "anonymous": False,
    "inputs": [
        {"indexed": True,  "name": "from", "type": "address"},
        {"indexed": True,  "name": "to",   "type": "address"},
        {"indexed": False, "name": "value","type": "uint256"},
    ],
    "name": "Transfer",
    "type": "event",
}
TRANSFER_SIG_HASH = Web3.keccak(text="Transfer(address,address,uint256)").hex()

# ── Environment / paths ────────────────────────────────────────────
load_dotenv("secrets.env")

RPC_URL       = os.getenv("RPC_URL")
WATCH_ADDRESS = Web3.to_checksum_address(os.getenv("WATCH_ADDRESS", ""))
RECEIPT_DIR   = Path(os.getenv("RECEIPT_DIR", "./receipts")).expanduser()
LETTERHEAD    = Path(os.getenv("LETTERHEAD_PDF", RECEIPT_DIR / "letterhead.pdf")).expanduser()

assert RPC_URL and WATCH_ADDRESS, "RPC_URL and WATCH_ADDRESS must be set in secrets.env"
assert LETTERHEAD.exists(), f"Letterhead PDF not found at {LETTERHEAD}"
RECEIPT_DIR.mkdir(parents=True, exist_ok=True)

# ── Web3 provider ───────────────────────────────────────────────────
w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 30}))

# ── PDF helper ─────────────────────────────────────────────────────
def _create_receipt_pdf(*, template_path: Path, save_path: Path, details: dict):
    """
    Merge `details` text onto page 1 of `template_path`
    starting ~10 lines below header, then save to `save_path`.
    """
    # 1. Build overlay PDF in-memory
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    x, y = 72, letter[1] - 200   # 1 inch margin, 200 pt drop
    for k, v in details.items():
        c.drawString(x, y, f"{k}: {v}")
        y -= 14
    c.save(); buf.seek(0)

    # 2. Merge
    template = PdfReader(template_path.open("rb"))
    overlay  = PdfReader(buf)
    page = template.pages[0]
    page.merge_page(overlay.pages[0])

    writer = PdfWriter()
    writer.add_page(page)
    with save_path.open("wb") as fp:
        writer.write(fp)

# ── Scan the most-recent 999 blocks ─────────────────────────────────
LATEST_BLOCK = w3.eth.block_number
FROM_BLOCK   = max(0, LATEST_BLOCK - 998)
TO_BLOCK     = LATEST_BLOCK
MAX_WINDOW   = 1000

print(f"[+] Scanning blocks {FROM_BLOCK} → {TO_BLOCK} (latest 999)")
print()                                               # ← blank line

watch_topic = "0x" + WATCH_ADDRESS.lower()[2:].rjust(64, "0")
logs, step_start = [], FROM_BLOCK

while step_start <= TO_BLOCK:
    step_end = min(step_start + MAX_WINDOW - 1, TO_BLOCK)
    try:
        chunk = w3.eth.get_logs({
            "fromBlock": step_start,
            "toBlock":   step_end,
            "address":   USDC_ADDRESS,
            "topics":    [TRANSFER_SIG_HASH, None, watch_topic],
        })
        logs.extend(chunk)
        print(f"  • {step_start}–{step_end}: {len(chunk)} logs")
        print()                                       # ← blank line
    except Exception as exc:
        print(f"  ! Error on {step_start}–{step_end}: {exc}")
        print()                                       # ← blank line
    step_start = step_end + 1

print(f"[+] Total logs fetched: {len(logs)}")
print()                                               # ← blank line


# Precompute event decoder
event_abi = TRANSFER_EVENT_ABI
event_codec = w3.codec
event_decoder = lambda log: get_event_data(event_codec, event_abi, log)

# ── Main processing loop ───────────────────────────────────────────
eastern = pytz.timezone("US/Eastern")

for log in logs:
    evt = event_decoder(log)
    value = evt["args"]["value"]
    if value < MIN_USDC:
        continue

    sender = evt["args"]["from"]
    tx_hash = evt["transactionHash"].hex()
    block = w3.eth.get_block(evt["blockNumber"])
    unix_ts = block["timestamp"]
    est = datetime.fromtimestamp(unix_ts, tz=pytz.utc).astimezone(eastern)
    est_str = est.strftime("%Y-%m-%d %H:%M:%S %Z")

    # Output filename
    file_name = f"{est.strftime('%Y%m%d')}_{sender[2:8]}_USDC_Receipt.pdf"
    out_path  = RECEIPT_DIR / file_name

    if out_path.exists():
        print(f"[-] Receipt already exists for {tx_hash[:10]}… → skipping")
        continue

    # Fetch full transaction for msg.data
    tx = w3.eth.get_transaction(tx_hash)

    # Build receipt
    details = {
        "Date / Time (US/Eastern)": f"{est_str} ({unix_ts})",
        "Transaction Hash":         tx_hash,
        "From":                     sender,
        "To":                       evt['args']['to'],
        "Amount":                   f"{value / DECIMALS:,.2f} USDC",
        "Block Number":             evt["blockNumber"],
        "Gas Price (wei)":          tx["gasPrice"],
    }
    print(f"[+] Creating receipt {out_path.name}")
    _create_receipt_pdf(template_path=LETTERHEAD, save_path=out_path, details=details)

print("[✓] Done. New receipts saved to", RECEIPT_DIR)

# ── Auto-open the latest receipt ───────────────────────────────────
try:
    newest_pdf = max(
        (p for p in RECEIPT_DIR.glob("*.pdf")),
        key=lambda p: p.stat().st_mtime,
        default=None,
    )
    if newest_pdf:
        print(f"[+] Opening latest receipt: {newest_pdf.name}")
        print()  # blank line for spacing

        if os.name == "nt":               # Windows
            os.startfile(newest_pdf)      
        elif sys.platform == "darwin":    # macOS
            subprocess.run(["open", newest_pdf])
        else:                             # Linux / WSL
            subprocess.run(["xdg-open", newest_pdf])
    else:
        print("[-] No receipts found to open.")
        print()
except Exception as e:
    print(f"[!] Could not open PDF automatically: {e}")
    print()

