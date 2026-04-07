# Varia Law - Matter Tracker

A fully offline, local application for tracking clients, matters, invoices, and payments.

## Features

- **Client Management**: Track client names, contacts, emails, addresses, and default rates
- **Matter Tracking**: Full lifecycle tracking from In-Process through Satisfied, with support for Inactive/Terminated status
- **Matter Billing Inputs**: Track hourly rate, hours billed, reimbursements, and optional fixed fees with notes
- **Matter Billing Notes**: Add ongoing billing notes/details separate from the matter description
- **Matter Total Amount**: Calculated as `(Hourly Rate * Hours Billed) + Reimbursements` or `Fixed Fee + Reimbursements`
- **Time Entries**: Add time entries per matter (hours required; date/description optional). Time entries automatically update the matter’s total hours.
- **Client Rate Autofill**: New matters default to the selected client's rate (editable per matter)
- **Auto-Invoicing**: Moving a matter from Needs Invoicing to Awaiting Payment creates an Unpaid invoice automatically. Marking all invoices for a matter as paid automatically advances the matter to Satisfied.
- **Invoice Management**: Create invoices linked to matters, track due dates, mark as paid
- **List Search**: Filter clients and matters in real time using the search bar on each list page
- **Invoice DOCX Generation**: Creating an invoice can generate a local `.docx` from `templates/InvoiceForm.docx`, replace supported placeholders, and open it in your default document editor
- **Payment Recording**: Record payments with method and account information
- **File Search**: Search Word docs (.docx), PDFs (text-based), and text files within your firm directory
- **Client Search Scopes**: Assign a directory per client to narrow file search
- **Dashboard**: Overview of matters and invoices by status with quick-action buttons
- **Reports**: Reports dashboard with A/R Aging, WIP & Pipeline, Client Financial Summary, plus CSV export and print-friendly pages

## Matter Status Workflow

1. **In-Process** - Active work being done
2. **Awaiting Response** - Waiting on client/counterparty
3. **Needs Invoicing** - Work complete, ready to invoice
4. **Awaiting Payment** - Invoice sent, awaiting payment
5. **Satisfied** - Paid and complete

Plus **Inactive/Terminated** for matters that are no longer active.

## Installation

### Prerequisites
- Python 3.8 or higher
- pip (Python package manager)

### Setup

1. Install Flask:
   ```
   pip install -r requirements.txt
   ```

2. Run the application (Windows):
   ```
   open_matter_tracker.bat
   ```
   Or manually:
   ```
   python app.py
   ```

3. Open your browser to: **http://127.0.0.1:5000**

## Configuration

### Firm Directory
Set your firm directory in **Settings** to enable file search. This should be the root folder containing your firm's documents (e.g., `C:\Users\Varia Law`).

### Theme and Font
In **Settings**, you can choose:
- **Themes**: Varia (default), Greyscale, Terminal (amber phosphor), Paper (warm sepia)
- **Fonts**: System, Inter, Playfair Display, JetBrains Mono

For full offline font support, run once (with internet):
```
python download_fonts.py
```
Fonts are cached aggressively (1 year) for offline use.

## Data Storage

Your data is stored in a local SQLite database file:
- `matter_tracker.db` - All clients, matters, invoices, and payments
- `settings.json` - Application settings

### Backup
Simply copy the entire `MatterTracker` folder to back up your data. The database file is portable and can be moved to another computer.

## File Search

The file search feature supports:
- **DOCX** - Microsoft Word documents
- **PDF** - Text-based PDFs (not scanned images)
- **TXT** - Plain text files

Search looks for matches in both file names and file contents.

In the results list, click a file name to open the document locally (or open its folder if the document can't be opened directly).

## Invoice Form Template

When creating a new invoice, the app can generate a new `.docx` invoice form from:

- `templates/InvoiceForm.docx`

Generated files are saved locally in:

- `generated_invoices/`

File naming convention:

- `<CLIENT_NAME>_<INVOICE_NO>.docx`

The app then opens the generated file with your system's default `.docx` editor so you can review and Save As as needed.

## Offline Operation

This application runs entirely on your local machine and **never connects to the internet**. Your data stays on your computer.

The UI also avoids loading any external web resources (no CDN assets, no remote fonts).

## Keyboard Shortcuts

- **Ctrl+K** - Focus search input (on search page)
- **Escape** - Close modals

## Tech Stack

- **Backend**: Python/Flask
- **Database**: SQLite
- **Frontend**: Vanilla HTML/CSS/JS
- **Styling**: Custom CSS with theme support (Varia, Greyscale, Terminal, Paper) and optional local fonts (Inter, Playfair Display, JetBrains Mono)

## Sharing This Application

If you want to share this app (e.g., via a public GitHub repository) so others can use it, follow these guidelines.

### What to Share

Include these files and folders:
- `app.py` - Main application code
- `requirements.txt` - Python dependencies
- `open_matter_tracker.bat` - Windows launcher script
- `download_fonts.py` - Font download script (run once for offline fonts)
- `templates/` - HTML templates
- `static/` - CSS, JavaScript, fonts, and assets
- `README.md` - This documentation

### What NOT to Share

**Do not include these files** as they contain personal/sensitive data:

| File | Reason |
|------|--------|
| `matter_tracker.db` | Contains all your client, matter, invoice, and payment data |
| `settings.json` | Contains your local file paths (firm directory) |
| `__pycache__/` | Python bytecode cache (auto-generated, not needed) |

Add a `.gitignore` file with:
```
matter_tracker.db
settings.json
__pycache__/
*.pyc
```

### Dependencies

Users will need:
- **Python 3.8+** - Download from [python.org](https://www.python.org/downloads/)
- **Flask** - Installed via `pip install -r requirements.txt`
- **python-docx** - Installed via `pip install -r requirements.txt` for invoice template generation

The app still uses Python's built-in `sqlite3` module for local data storage.

### Platform-Specific Launcher Scripts

#### Windows (included)
The `open_matter_tracker.bat` file works as-is. Users may need to adjust line 15 if their Python installation uses a different command:
```batch
python app.py       # Standard Python installation
py app.py           # Windows Python Launcher
python3 app.py      # If multiple versions installed
```

#### macOS / Linux
Create a shell script named `matter_tracker.sh`:
```bash
#!/bin/bash
echo "========================================"
echo "  Matter Tracker"
echo "========================================"
echo ""
echo "Starting server..."
echo "Open your browser to: http://127.0.0.1:5000"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

cd "$(dirname "$0")"

# Open browser after 2 second delay (runs in background)
(sleep 2 && open http://127.0.0.1:5000) &    # macOS
# (sleep 2 && xdg-open http://127.0.0.1:5000) &  # Linux - uncomment this line instead

python3 app.py
```

Make it executable: `chmod +x matter_tracker.sh`

#### Adjusting the Browser Launch

- **Windows**: Uses `start http://127.0.0.1:5000`
- **macOS**: Uses `open http://127.0.0.1:5000`
- **Linux**: Uses `xdg-open http://127.0.0.1:5000`

### First-Time Setup for New Users

1. Clone or download the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Run the launcher script (or `python app.py` directly)
4. Open browser to http://127.0.0.1:5000
5. Go to **Settings** to configure the firm directory path for file search
