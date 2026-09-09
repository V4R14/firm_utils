"""
Varia Law - Matter Tracker
A fully offline, local application for tracking clients, matters, invoices, and payments.
"""

import os
import sqlite3
import json
import subprocess
import sys
import csv
from io import StringIO
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, g, Response, send_from_directory
from pathlib import Path
from retainers import init_retainer_db, retainers_bp

# File search imports
import zipfile
import xml.etree.ElementTree as ET

try:
    from docx import Document  # type: ignore[import-not-found]
except ImportError:
    Document = None

app = Flask(__name__)
app.secret_key = 'varia-law-matter-tracker-local-key'

# Database path - stored alongside the app
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'matter_tracker.db')
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')

# Matter statuses in order
MATTER_STATUSES = [
    'In-Process',
    'Awaiting Response',
    'Needs Invoicing',
    'Awaiting Payment',
    'Satisfied',
    'Inactive/Terminated'
]

MATTER_TYPES = ['Legal', 'Consulting', 'Development']

INVOICE_STATUSES = ['Unpaid', 'Paid']

def parse_numeric_value(value):
    """Parse a numeric value from possibly messy input."""
    if value is None:
        return 0.0, False, True
    if isinstance(value, (int, float)):
        return float(value), False, False

    raw = str(value).strip()
    if raw == '':
        return 0.0, False, True

    sanitized = ''.join(ch for ch in raw if ch.isdigit() or ch == '.')
    if sanitized in ('', '.'):
        return 0.0, True, False
    if sanitized.count('.') > 1:
        return 0.0, True, False

    try:
        return float(sanitized), False, False
    except ValueError:
        return 0.0, True, False


def format_money(amount):
    """Format money with $ and decimals only if needed."""
    if amount is None:
        return '—'
    amount = round(float(amount), 2)
    if amount.is_integer():
        return f'${int(amount)}'
    return f'${amount:.2f}'

def format_number(value):
    """Format numbers without trailing zeros."""
    if value is None:
        return '0'
    value = round(float(value), 2)
    if value.is_integer():
        return f'{int(value)}'
    return f'{value:.2f}'.rstrip('0').rstrip('.')


def format_plain_money_number(value):
    """Format money without a currency symbol."""
    if value is None:
        return ''
    value = round(float(value), 2)
    if value.is_integer():
        return f'{int(value)}'
    return f'{value:.2f}'


def format_date_long(date_value):
    """Format ISO date as Month Day, Year."""
    if not date_value:
        return ''
    try:
        parsed = date.fromisoformat(str(date_value))
    except ValueError:
        return ''
    return f'{parsed.strftime("%B")} {parsed.day}, {parsed.year}'


def sanitize_filename_component(value):
    """Sanitize filename component for cross-platform safety."""
    if not value:
        return ''
    disallowed = '<>:"/\\|?*'
    cleaned = ''.join('_' if ch in disallowed else ch for ch in str(value))
    cleaned = cleaned.replace('\n', ' ').replace('\r', ' ').strip()
    cleaned = '_'.join(cleaned.split())
    return cleaned[:120].strip('._')


def _iter_table_paragraphs(table):
    """Yield all paragraphs in a table (including nested tables)."""
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                yield paragraph
            for nested_table in cell.tables:
                yield from _iter_table_paragraphs(nested_table)


def _iter_doc_paragraphs(doc):
    """Yield all paragraphs from document body, tables, headers, and footers."""
    for paragraph in doc.paragraphs:
        yield paragraph
    for table in doc.tables:
        yield from _iter_table_paragraphs(table)

    for section in doc.sections:
        for paragraph in section.header.paragraphs:
            yield paragraph
        for table in section.header.tables:
            yield from _iter_table_paragraphs(table)
        for paragraph in section.footer.paragraphs:
            yield paragraph
        for table in section.footer.tables:
            yield from _iter_table_paragraphs(table)


def _replace_placeholder_in_paragraph(paragraph, placeholder, replacement):
    """
    Replace placeholder in a paragraph while keeping run-level formatting as much as possible.
    """
    if replacement is None or str(replacement).strip() == '':
        return False

    if not paragraph.runs:
        return False

    changed = False
    replacement = str(replacement)
    paragraph_text = ''.join(run.text for run in paragraph.runs)
    search_from = 0

    while True:
        start_idx = paragraph_text.find(placeholder, search_from)
        if start_idx == -1:
            break
        end_idx = start_idx + len(placeholder)

        spans = []
        cursor = 0
        for run_index, run in enumerate(paragraph.runs):
            run_text = run.text or ''
            next_cursor = cursor + len(run_text)
            spans.append((run_index, cursor, next_cursor))
            cursor = next_cursor

        first_span = None
        last_span = None
        for span in spans:
            _, span_start, span_end = span
            if first_span is None and span_start <= start_idx < span_end:
                first_span = span
            if span_start < end_idx <= span_end:
                last_span = span
                break

        if first_span is None or last_span is None:
            search_from = end_idx
            continue

        first_run_idx, first_start, _ = first_span
        last_run_idx, last_start, _ = last_span

        first_run = paragraph.runs[first_run_idx]
        last_run = paragraph.runs[last_run_idx]

        prefix = first_run.text[:start_idx - first_start]
        suffix = last_run.text[end_idx - last_start:]
        first_run.text = prefix + replacement + suffix

        for idx in range(first_run_idx + 1, last_run_idx + 1):
            paragraph.runs[idx].text = ''

        changed = True
        paragraph_text = ''.join(run.text for run in paragraph.runs)
        search_from = start_idx + len(replacement)

    return changed


def _create_invoice_docx(template_path, invoice_data, client_dir=None):
    """Create an invoice .docx from template and return output path."""
    if Document is None:
        return None, 'python-docx is not installed'

    if not os.path.isfile(template_path):
        return None, 'invoice template not found'

    client_name = invoice_data.get('client_name', '')
    invoice_number = invoice_data.get('invoice_number', '')

    safe_client = sanitize_filename_component(client_name) or 'Client'
    safe_invoice = sanitize_filename_component(invoice_number) or f'INV-{int(datetime.now().timestamp())}'

    fallback_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'generated_invoices')
    if client_dir and os.path.isdir(client_dir):
        output_dir = client_dir
    else:
        output_dir = fallback_dir
    os.makedirs(output_dir, exist_ok=True)

    output_filename = f'{safe_client}_{safe_invoice}.docx'
    output_path = os.path.join(output_dir, output_filename)
    if os.path.exists(output_path):
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = os.path.join(output_dir, f'{safe_client}_{safe_invoice}_{stamp}.docx')

    placeholders = {
        '[INVOICE_NO]': invoice_number.strip() if invoice_number and str(invoice_number).strip() else '',
        '[DATE_ISSUED]': format_date_long(invoice_data.get('date_issued')),
        '[CLIENT_NAME]': (invoice_data.get('client_name') or '').strip(),
        '[CONTACT]': (invoice_data.get('contact_name') or '').strip(),
        '[EMAIL]': (invoice_data.get('contact_email') or '').strip(),
        '[MATTER]': (invoice_data.get('matter_title') or '').strip(),
        '[DESCRIPTION]': (invoice_data.get('matter_description') or '').strip(),
        '[HOURS_BILLED]': '',
        '[RATE]': '',
        '[AMOUNT]': ''
    }

    hours_value, hours_warn, hours_empty = parse_numeric_value(invoice_data.get('hours_billed_amount'))
    if not hours_warn and not hours_empty:
        placeholders['[HOURS_BILLED]'] = format_number(hours_value)

    rate_value, rate_warn, rate_empty = parse_numeric_value(invoice_data.get('default_rate'))
    if not rate_warn and not rate_empty:
        placeholders['[RATE]'] = format_plain_money_number(rate_value)

    amount_value, amount_warn, amount_empty = parse_numeric_value(invoice_data.get('amount'))
    if not amount_warn and not amount_empty:
        placeholders['[AMOUNT]'] = format_plain_money_number(amount_value)

    try:
        doc = Document(template_path)
        for paragraph in _iter_doc_paragraphs(doc):
            for placeholder, replacement in placeholders.items():
                _replace_placeholder_in_paragraph(paragraph, placeholder, replacement)
        doc.save(output_path)
        return output_path, ''
    except Exception:
        return None, 'unable to generate invoice document'


def compute_matter_total(matter):
    """Compute matter total and return warnings."""
    fixed_fee_enabled = bool(matter.get('fixed_fee_enabled'))

    fixed_fee, fixed_fee_warn, _ = parse_numeric_value(matter.get('fixed_fee_amount'))
    reimbursement, reimbursement_warn, _ = parse_numeric_value(matter.get('reimbursement_amount'))

    if fixed_fee_enabled:
        base_amount = fixed_fee
        warnings = []
        if fixed_fee_warn:
            warnings.append('Fixed fee')
    else:
        rate, rate_warn, _ = parse_numeric_value(matter.get('rate'))
        hours, hours_warn, _ = parse_numeric_value(matter.get('hours_billed_amount'))
        base_amount = rate * hours
        warnings = []
        if rate_warn:
            warnings.append('Hourly rate')
        if hours_warn:
            warnings.append('Hours billed')

    if reimbursement_warn:
        warnings.append('Reimbursement')

    total = base_amount + reimbursement
    return total, warnings


def generate_invoice_number():
    """Generate a simple invoice number that can be edited later."""
    now = datetime.now()
    return f"Invoice No. {now.year}.{now.month}"


def resolve_client_directory(firm_directory, client_directory):
    """Resolve a client directory path using firm directory as base."""
    if not client_directory:
        return firm_directory

    client_directory = client_directory.strip()
    if os.path.isabs(client_directory):
        return client_directory

    if not firm_directory:
        return client_directory

    return os.path.normpath(os.path.join(firm_directory, client_directory))


def enrich_matter_totals(matters):
    """Attach total amount fields for template display."""
    enriched = []
    for matter in matters:
        matter_dict = dict(matter)
        rate_value, rate_warn, rate_empty = parse_numeric_value(matter_dict.get('rate'))
        hours_value, hours_warn, hours_empty = parse_numeric_value(matter_dict.get('hours_billed_amount'))
        reimbursement_value, reimbursement_warn, reimbursement_empty = parse_numeric_value(matter_dict.get('reimbursement_amount'))
        fixed_fee_value, fixed_fee_warn, fixed_fee_empty = parse_numeric_value(matter_dict.get('fixed_fee_amount'))

        total, warnings = compute_matter_total(matter_dict)
        matter_dict['total_amount'] = total
        matter_dict['total_amount_display'] = format_money(total)
        matter_dict['total_amount_warnings'] = warnings
        matter_dict['rate_display'] = format_money(rate_value if not rate_empty or rate_warn else 0)
        matter_dict['hours_billed_display'] = format_number(hours_value if not hours_empty or hours_warn else 0)
        matter_dict['reimbursement_display'] = format_money(reimbursement_value if not reimbursement_empty or reimbursement_warn else 0)
        matter_dict['fixed_fee_display'] = format_money(fixed_fee_value if not fixed_fee_empty or fixed_fee_warn else 0)
        enriched.append(matter_dict)
    return enriched


def get_db():
    """Get database connection for current request."""
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.after_request
def add_font_cache_headers(response):
    """Aggressive caching for font files - 1 year, immutable. Preserves offline use."""
    if request.path.startswith('/static/fonts/'):
        response.cache_control.max_age = 31536000
        response.cache_control.public = True
        response.cache_control.immutable = True
    return response


@app.teardown_appcontext
def close_db(error):
    """Close database connection at end of request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    """Initialize the database with schema."""
    db = sqlite3.connect(DATABASE)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            contact_name TEXT,
            contact_email TEXT,
            address TEXT,
            notes TEXT,
            default_rate TEXT,
            client_directory TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS matters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            billing_notes TEXT,
            matter_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'In-Process',
            open_date DATE,
            deliverable TEXT,
            counterparty TEXT,
            deadline DATE,
            rate TEXT,
            hours_billed_amount REAL,
            hours_billed_notes TEXT,
            reimbursement_amount REAL,
            reimbursement_notes TEXT,
            fixed_fee_enabled INTEGER DEFAULT 0,
            fixed_fee_amount REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        );

        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            matter_id INTEGER NOT NULL,
            invoice_number TEXT NOT NULL,
            amount REAL NOT NULL,
            date_issued DATE NOT NULL,
            due_date DATE,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'Unpaid',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (matter_id) REFERENCES matters (id)
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            date_received DATE NOT NULL,
            method TEXT,
            account TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (invoice_id) REFERENCES invoices (id)
        );

        CREATE TABLE IF NOT EXISTS time_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            matter_id INTEGER NOT NULL,
            entry_date DATE,
            hours REAL NOT NULL,
            description TEXT,
            billable INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (matter_id) REFERENCES matters (id)
        );

        CREATE INDEX IF NOT EXISTS idx_matters_client ON matters(client_id);
        CREATE INDEX IF NOT EXISTS idx_matters_status ON matters(status);
        CREATE INDEX IF NOT EXISTS idx_invoices_matter ON invoices(matter_id);
        CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
        CREATE INDEX IF NOT EXISTS idx_invoices_status_due_date ON invoices(status, due_date);
        CREATE INDEX IF NOT EXISTS idx_payments_invoice ON payments(invoice_id);
        CREATE INDEX IF NOT EXISTS idx_time_entries_matter ON time_entries(matter_id);
        CREATE INDEX IF NOT EXISTS idx_time_entries_matter_date ON time_entries(matter_id, entry_date);
    ''')
    db.commit()

    # Backfill new columns for existing databases
    existing_client_columns = [row[1] for row in db.execute("PRAGMA table_info('clients')").fetchall()]
    if 'client_directory' not in existing_client_columns:
        db.execute("ALTER TABLE clients ADD COLUMN client_directory TEXT")

    existing_columns = [row[1] for row in db.execute("PRAGMA table_info('matters')").fetchall()]
    add_columns = {
        'billing_notes': "ALTER TABLE matters ADD COLUMN billing_notes TEXT",
        'hours_billed_amount': "ALTER TABLE matters ADD COLUMN hours_billed_amount REAL",
        'hours_billed_notes': "ALTER TABLE matters ADD COLUMN hours_billed_notes TEXT",
        'reimbursement_amount': "ALTER TABLE matters ADD COLUMN reimbursement_amount REAL",
        'reimbursement_notes': "ALTER TABLE matters ADD COLUMN reimbursement_notes TEXT",
        'fixed_fee_enabled': "ALTER TABLE matters ADD COLUMN fixed_fee_enabled INTEGER DEFAULT 0",
        'fixed_fee_amount': "ALTER TABLE matters ADD COLUMN fixed_fee_amount REAL"
    }

    for column, statement in add_columns.items():
        if column not in existing_columns:
            db.execute(statement)

    db.close()


def _csv_response(filename, header, rows):
    """Return a CSV download response (stdlib only)."""
    output = StringIO()
    writer = csv.writer(output)
    if header:
        writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    resp = Response(output.getvalue(), mimetype='text/csv')
    resp.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return resp


def _sync_matter_hours_from_time_entries(db, matter_id):
    """Update matters.hours_billed_amount from time entries."""
    row = db.execute(
        'SELECT COALESCE(SUM(hours), 0) as total FROM time_entries WHERE matter_id = ?',
        (matter_id,)
    ).fetchone()
    total = float(row['total'] if row else 0.0)
    db.execute(
        'UPDATE matters SET hours_billed_amount = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
        (total, matter_id)
    )


def load_settings():
    """Load settings from JSON file."""
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
            # Ensure theme and font have defaults
            settings.setdefault('theme', 'varia')
            settings.setdefault('font', 'inter')
            return settings
    return {'firm_directory': '', 'theme': 'varia', 'font': 'inter'}


def save_settings(settings):
    """Save settings to JSON file."""
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)


# Initialize database on startup
init_db()
init_retainer_db(DATABASE)
app.register_blueprint(retainers_bp)


@app.context_processor
def utility_processor():
    """Add utility functions and settings to template context."""
    settings = load_settings()
    return {
        'now': datetime.now,
        'format_money': format_money,
        'format_number': format_number,
        'theme': settings.get('theme', 'varia'),
        'font': settings.get('font', 'inter'),
    }


# ============== ROUTES ==============

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'favicon.ico')

@app.route('/Logo.png')
def logo_image():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'Logo.png')

@app.route('/')
def dashboard():
    """Main dashboard showing matters and invoices by status."""
    db = get_db()

    # Get matters grouped by status
    matters_by_status = {}
    for status in MATTER_STATUSES:
        matters = db.execute('''
            SELECT m.*, c.name as client_name
            FROM matters m
            JOIN clients c ON m.client_id = c.id
            WHERE m.status = ?
            ORDER BY m.deadline ASC, m.created_at DESC
        ''', (status,)).fetchall()
        matters_by_status[status] = enrich_matter_totals(matters)

    # Get invoices grouped by status
    invoices_by_status = {}
    for status in INVOICE_STATUSES:
        invoices = db.execute('''
            SELECT i.*, m.title as matter_title, c.name as client_name
            FROM invoices i
            JOIN matters m ON i.matter_id = m.id
            JOIN clients c ON m.client_id = c.id
            WHERE i.status = ?
            ORDER BY i.due_date ASC, i.date_issued DESC
        ''', (status,)).fetchall()
        invoices_by_status[status] = invoices

    # Calculate totals. Start from all unpaid invoice amounts, then add the
    # totals of Awaiting Payment matters that have NO unpaid invoice, so a
    # matter appearing in both sections is not counted twice.
    total_unpaid = db.execute('SELECT COALESCE(SUM(amount), 0) as total FROM invoices WHERE status = ?', ('Unpaid',)).fetchone()['total']

    for matter in matters_by_status['Awaiting Payment']:
        unpaid_invoice_count = db.execute(
            'SELECT COUNT(*) as count FROM invoices WHERE matter_id = ? AND status = ?',
            (matter['id'], 'Unpaid')
        ).fetchone()['count']
        if unpaid_invoice_count == 0:
            total_unpaid += matter['total_amount']

    return render_template('dashboard.html',
                         matters_by_status=matters_by_status,
                         invoices_by_status=invoices_by_status,
                         matter_statuses=MATTER_STATUSES,
                         invoice_statuses=INVOICE_STATUSES,
                         total_unpaid=total_unpaid)


# ============== CLIENT ROUTES ==============

@app.route('/clients')
def clients_list():
    """List all clients."""
    db = get_db()
    clients = db.execute('''
        SELECT c.*,
               COUNT(DISTINCT m.id) as matter_count,
               COUNT(DISTINCT CASE WHEN m.status NOT IN ('Satisfied', 'Inactive/Terminated') THEN m.id END) as active_matter_count
        FROM clients c
        LEFT JOIN matters m ON c.id = m.client_id
        GROUP BY c.id
        ORDER BY c.name ASC
    ''').fetchall()
    return render_template('clients/list.html', clients=clients)


@app.route('/clients/new', methods=['GET', 'POST'])
def client_new():
    """Create a new client."""
    if request.method == 'POST':
        db = get_db()
        db.execute('''
            INSERT INTO clients (name, contact_name, contact_email, address, notes, default_rate, client_directory)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            request.form['name'],
            request.form.get('contact_name', ''),
            request.form.get('contact_email', ''),
            request.form.get('address', ''),
            request.form.get('notes', ''),
            request.form.get('default_rate', ''),
            request.form.get('client_directory', '').strip()
        ))
        db.commit()
        flash('Client created successfully.', 'success')
        return redirect(url_for('clients_list'))
    firm_directory = load_settings().get('firm_directory', '')
    return render_template('clients/form.html', client=None, firm_directory=firm_directory)


@app.route('/clients/<int:id>')
def client_view(id):
    """View a single client with their matters."""
    db = get_db()
    client = db.execute('SELECT * FROM clients WHERE id = ?', (id,)).fetchone()
    if not client:
        flash('Client not found.', 'error')
        return redirect(url_for('clients_list'))

    matters = db.execute('''
        SELECT * FROM matters WHERE client_id = ? ORDER BY created_at DESC
    ''', (id,)).fetchall()

    return render_template('clients/view.html', client=client, matters=enrich_matter_totals(matters), matter_statuses=MATTER_STATUSES)


@app.route('/clients/<int:id>/edit', methods=['GET', 'POST'])
def client_edit(id):
    """Edit a client."""
    db = get_db()
    client = db.execute('SELECT * FROM clients WHERE id = ?', (id,)).fetchone()
    if not client:
        flash('Client not found.', 'error')
        return redirect(url_for('clients_list'))

    if request.method == 'POST':
        db.execute('''
            UPDATE clients
            SET name = ?, contact_name = ?, contact_email = ?, address = ?, notes = ?, default_rate = ?, client_directory = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (
            request.form['name'],
            request.form.get('contact_name', ''),
            request.form.get('contact_email', ''),
            request.form.get('address', ''),
            request.form.get('notes', ''),
            request.form.get('default_rate', ''),
            request.form.get('client_directory', '').strip(),
            id
        ))
        db.commit()
        flash('Client updated successfully.', 'success')
        return redirect(url_for('client_view', id=id))

    firm_directory = load_settings().get('firm_directory', '')
    return render_template('clients/form.html', client=client, firm_directory=firm_directory)


@app.route('/clients/<int:id>/delete', methods=['POST'])
def client_delete(id):
    """Delete a client."""
    db = get_db()
    # Check for associated matters
    matter_count = db.execute('SELECT COUNT(*) as count FROM matters WHERE client_id = ?', (id,)).fetchone()['count']
    if matter_count > 0:
        flash(f'Cannot delete client with {matter_count} associated matter(s). Delete matters first.', 'error')
        return redirect(url_for('client_view', id=id))

    db.execute('DELETE FROM clients WHERE id = ?', (id,))
    db.commit()
    flash('Client deleted.', 'success')
    return redirect(url_for('clients_list'))


# ============== MATTER ROUTES ==============

@app.route('/matters')
def matters_list():
    """List all matters."""
    db = get_db()
    status_filter = request.args.get('status', '')

    if status_filter:
        matters = db.execute('''
            SELECT m.*, c.name as client_name
            FROM matters m
            JOIN clients c ON m.client_id = c.id
            WHERE m.status = ?
            ORDER BY m.deadline ASC, m.created_at DESC
        ''', (status_filter,)).fetchall()
    else:
        matters = db.execute('''
            SELECT m.*, c.name as client_name
            FROM matters m
            JOIN clients c ON m.client_id = c.id
            ORDER BY m.deadline ASC, m.created_at DESC
        ''').fetchall()

    return render_template('matters/list.html', matters=enrich_matter_totals(matters), matter_statuses=MATTER_STATUSES, current_status=status_filter)


@app.route('/matters/new', methods=['GET', 'POST'])
def matter_new():
    """Create a new matter."""
    db = get_db()
    clients = db.execute('SELECT id, name, default_rate FROM clients ORDER BY name').fetchall()

    if request.method == 'POST':
        fixed_fee_enabled = 1 if request.form.get('fixed_fee_enabled') else 0
        hours_billed_amount = request.form.get('hours_billed_amount')
        reimbursement_amount = request.form.get('reimbursement_amount')
        fixed_fee_amount = request.form.get('fixed_fee_amount')
        billing_notes = request.form.get('billing_notes', '')

        db.execute('''
            INSERT INTO matters (
                client_id, title, description, billing_notes, matter_type, status, open_date,
                deliverable, counterparty, deadline, rate,
                hours_billed_amount, hours_billed_notes,
                reimbursement_amount, reimbursement_notes,
                fixed_fee_enabled, fixed_fee_amount
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            request.form['client_id'],
            request.form['title'],
            request.form.get('description', ''),
            billing_notes,
            request.form['matter_type'],
            request.form.get('status', 'In-Process'),
            request.form.get('open_date') or None,
            request.form.get('deliverable', ''),
            request.form.get('counterparty', ''),
            request.form.get('deadline') or None,
            request.form.get('rate') or '',
            float(hours_billed_amount) if hours_billed_amount else None,
            request.form.get('hours_billed_notes', ''),
            float(reimbursement_amount) if reimbursement_amount else None,
            request.form.get('reimbursement_notes', ''),
            fixed_fee_enabled,
            float(fixed_fee_amount) if fixed_fee_amount else None
        ))
        db.commit()
        flash('Matter created successfully.', 'success')
        return redirect(url_for('matters_list'))

    # Pre-select client if provided
    selected_client = request.args.get('client_id', '')
    return render_template('matters/form.html', matter=None, clients=clients, matter_types=MATTER_TYPES, matter_statuses=MATTER_STATUSES, selected_client=selected_client)


@app.route('/matters/<int:id>')
def matter_view(id):
    """View a single matter with its invoices."""
    db = get_db()
    matter = db.execute('''
        SELECT m.*, c.name as client_name
        FROM matters m
        JOIN clients c ON m.client_id = c.id
        WHERE m.id = ?
    ''', (id,)).fetchone()

    if not matter:
        flash('Matter not found.', 'error')
        return redirect(url_for('matters_list'))

    invoices = db.execute('''
        SELECT * FROM invoices WHERE matter_id = ? ORDER BY date_issued DESC
    ''', (id,)).fetchall()

    time_entries = db.execute('''
        SELECT *
        FROM time_entries
        WHERE matter_id = ?
        ORDER BY (entry_date IS NULL) ASC, entry_date DESC, created_at DESC
    ''', (id,)).fetchall()

    billable_hours_total = 0.0
    nonbillable_hours_total = 0.0
    for entry in time_entries:
        hours_value = float(entry['hours'] or 0)
        billable_hours_total += hours_value

    matter_dict = enrich_matter_totals([matter])[0]

    # Get status index for progress bar
    status_index = MATTER_STATUSES.index(matter_dict['status']) if matter_dict['status'] in MATTER_STATUSES else 0

    return render_template(
        'matters/view.html',
        matter=matter_dict,
        invoices=invoices,
        time_entries=time_entries,
        billable_hours_total=billable_hours_total,
        nonbillable_hours_total=nonbillable_hours_total,
                         matter_statuses=MATTER_STATUSES, status_index=status_index,
                         invoice_statuses=INVOICE_STATUSES)


@app.route('/matters/<int:id>/edit', methods=['GET', 'POST'])
def matter_edit(id):
    """Edit a matter."""
    db = get_db()
    matter = db.execute('SELECT * FROM matters WHERE id = ?', (id,)).fetchone()
    if not matter:
        flash('Matter not found.', 'error')
        return redirect(url_for('matters_list'))

    clients = db.execute('SELECT id, name, default_rate FROM clients ORDER BY name').fetchall()

    if request.method == 'POST':
        fixed_fee_enabled = 1 if request.form.get('fixed_fee_enabled') else 0
        hours_billed_amount = request.form.get('hours_billed_amount')
        reimbursement_amount = request.form.get('reimbursement_amount')
        fixed_fee_amount = request.form.get('fixed_fee_amount')
        billing_notes = request.form.get('billing_notes', '')

        db.execute('''
            UPDATE matters
            SET client_id = ?, title = ?, description = ?, billing_notes = ?, matter_type = ?, status = ?,
                open_date = ?, deliverable = ?, counterparty = ?, deadline = ?, rate = ?,
                hours_billed_amount = ?, hours_billed_notes = ?,
                reimbursement_amount = ?, reimbursement_notes = ?,
                fixed_fee_enabled = ?, fixed_fee_amount = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (
            request.form['client_id'],
            request.form['title'],
            request.form.get('description', ''),
            billing_notes,
            request.form['matter_type'],
            request.form['status'],
            request.form.get('open_date') or None,
            request.form.get('deliverable', ''),
            request.form.get('counterparty', ''),
            request.form.get('deadline') or None,
            request.form.get('rate', ''),
            float(hours_billed_amount) if hours_billed_amount else None,
            request.form.get('hours_billed_notes', ''),
            float(reimbursement_amount) if reimbursement_amount else None,
            request.form.get('reimbursement_notes', ''),
            fixed_fee_enabled,
            float(fixed_fee_amount) if fixed_fee_amount else None,
            id
        ))
        db.commit()
        flash('Matter updated successfully.', 'success')
        return redirect(url_for('matter_view', id=id))

    return render_template('matters/form.html', matter=matter, clients=clients,
                         matter_types=MATTER_TYPES, matter_statuses=MATTER_STATUSES, selected_client='')


@app.route('/matters/<int:id>/time/new', methods=['POST'])
def time_entry_new(id):
    """Create a new time entry for a matter (hours required, other fields optional)."""
    db = get_db()
    matter = db.execute('SELECT id FROM matters WHERE id = ?', (id,)).fetchone()
    if not matter:
        flash('Matter not found.', 'error')
        return redirect(url_for('matters_list'))

    hours_raw = (request.form.get('hours') or '').strip()
    if not hours_raw:
        flash('Hours is required.', 'error')
        return redirect(request.referrer or url_for('matter_view', id=id))

    try:
        hours = float(hours_raw)
    except ValueError:
        flash('Hours must be a number.', 'error')
        return redirect(request.referrer or url_for('matter_view', id=id))

    if hours <= 0:
        flash('Hours must be greater than 0.', 'error')
        return redirect(request.referrer or url_for('matter_view', id=id))

    entry_date = request.form.get('entry_date') or None
    description = (request.form.get('description') or '').strip()
    billable = 1

    db.execute('''
        INSERT INTO time_entries (matter_id, entry_date, hours, description, billable)
        VALUES (?, ?, ?, ?, ?)
    ''', (id, entry_date, hours, description, billable))

    _sync_matter_hours_from_time_entries(db, id)
    db.commit()
    flash('Time entry added.', 'success')
    return redirect(request.referrer or url_for('matter_view', id=id))


@app.route('/time/<int:entry_id>/edit', methods=['POST'])
def time_entry_edit(entry_id):
    """Edit a time entry (hours required, other fields optional)."""
    db = get_db()
    entry = db.execute('SELECT * FROM time_entries WHERE id = ?', (entry_id,)).fetchone()
    if not entry:
        flash('Time entry not found.', 'error')
        return redirect(request.referrer or url_for('dashboard'))

    hours_raw = (request.form.get('hours') or '').strip()
    if not hours_raw:
        flash('Hours is required.', 'error')
        return redirect(request.referrer or url_for('matter_view', id=entry['matter_id']))

    try:
        hours = float(hours_raw)
    except ValueError:
        flash('Hours must be a number.', 'error')
        return redirect(request.referrer or url_for('matter_view', id=entry['matter_id']))

    if hours <= 0:
        flash('Hours must be greater than 0.', 'error')
        return redirect(request.referrer or url_for('matter_view', id=entry['matter_id']))

    entry_date = request.form.get('entry_date') or None
    description = (request.form.get('description') or '').strip()
    billable = 1

    db.execute('''
        UPDATE time_entries
        SET entry_date = ?, hours = ?, description = ?, billable = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (entry_date, hours, description, billable, entry_id))

    _sync_matter_hours_from_time_entries(db, entry['matter_id'])
    db.commit()
    flash('Time entry updated.', 'success')
    return redirect(request.referrer or url_for('matter_view', id=entry['matter_id']))


@app.route('/time/<int:entry_id>/delete', methods=['POST'])
def time_entry_delete(entry_id):
    """Delete a time entry."""
    db = get_db()
    entry = db.execute('SELECT id, matter_id FROM time_entries WHERE id = ?', (entry_id,)).fetchone()
    if not entry:
        flash('Time entry not found.', 'error')
        return redirect(request.referrer or url_for('dashboard'))

    db.execute('DELETE FROM time_entries WHERE id = ?', (entry_id,))
    _sync_matter_hours_from_time_entries(db, entry['matter_id'])
    db.commit()
    flash('Time entry deleted.', 'success')
    return redirect(request.referrer or url_for('matter_view', id=entry['matter_id']))


@app.route('/matters/<int:id>/advance', methods=['POST'])
def matter_advance(id):
    """Advance a matter to the next status."""
    db = get_db()
    matter = db.execute('''
        SELECT m.*, c.name as client_name
        FROM matters m
        JOIN clients c ON m.client_id = c.id
        WHERE m.id = ?
    ''', (id,)).fetchone()
    if not matter:
        flash('Matter not found.', 'error')
        return redirect(url_for('matters_list'))

    current_status = matter['status']
    if current_status in MATTER_STATUSES and current_status != 'Inactive/Terminated':
        current_index = MATTER_STATUSES.index(current_status)
        # Don't advance past Satisfied (index 4), and skip Inactive/Terminated
        if current_index < 4:
            next_status = MATTER_STATUSES[current_index + 1]
            # Skip Inactive/Terminated in progression
            if next_status == 'Inactive/Terminated':
                next_status = MATTER_STATUSES[current_index + 2] if current_index + 2 < len(MATTER_STATUSES) else current_status

            db.execute('UPDATE matters SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (next_status, id))
            db.commit()
            flash(f'Matter advanced to {next_status}.', 'success')

    # Return to referrer or matter view
    return redirect(request.referrer or url_for('matter_view', id=id))


@app.route('/matters/<int:id>/regress', methods=['POST'])
def matter_regress(id):
    """Move a matter to the previous status."""
    db = get_db()
    matter = db.execute('SELECT status FROM matters WHERE id = ?', (id,)).fetchone()
    if not matter:
        flash('Matter not found.', 'error')
        return redirect(url_for('matters_list'))

    current_status = matter['status']
    if current_status in MATTER_STATUSES and current_status != 'Inactive/Terminated':
        current_index = MATTER_STATUSES.index(current_status)
        # Don't go below In-Process (index 0)
        if current_index > 0:
            prev_status = MATTER_STATUSES[current_index - 1]
            db.execute('UPDATE matters SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (prev_status, id))
            db.commit()
            flash(f'Matter moved back to {prev_status}.', 'success')

    return redirect(request.referrer or url_for('matter_view', id=id))


@app.route('/matters/<int:id>/set-status', methods=['POST'])
def matter_set_status(id):
    """Set a matter to a specific status."""
    db = get_db()
    new_status = request.form.get('status')
    if new_status in MATTER_STATUSES:
        db.execute('UPDATE matters SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (new_status, id))
        db.commit()
        flash(f'Matter status changed to {new_status}.', 'success')
    return redirect(request.referrer or url_for('matter_view', id=id))


@app.route('/matters/<int:id>/delete', methods=['POST'])
def matter_delete(id):
    """Delete a matter."""
    db = get_db()
    # Check for associated invoices
    invoice_count = db.execute('SELECT COUNT(*) as count FROM invoices WHERE matter_id = ?', (id,)).fetchone()['count']
    if invoice_count > 0:
        flash(f'Cannot delete matter with {invoice_count} associated invoice(s). Delete invoices first.', 'error')
        return redirect(url_for('matter_view', id=id))

    db.execute('DELETE FROM matters WHERE id = ?', (id,))
    db.commit()
    flash('Matter deleted.', 'success')
    return redirect(url_for('matters_list'))


# ============== INVOICE ROUTES ==============

@app.route('/invoices')
def invoices_list():
    """List all invoices."""
    db = get_db()
    status_filter = request.args.get('status', '')

    if status_filter:
        invoices = db.execute('''
            SELECT i.*, m.title as matter_title, c.name as client_name
            FROM invoices i
            JOIN matters m ON i.matter_id = m.id
            JOIN clients c ON m.client_id = c.id
            WHERE i.status = ?
            ORDER BY i.due_date ASC, i.date_issued DESC
        ''', (status_filter,)).fetchall()
    else:
        invoices = db.execute('''
            SELECT i.*, m.title as matter_title, c.name as client_name
            FROM invoices i
            JOIN matters m ON i.matter_id = m.id
            JOIN clients c ON m.client_id = c.id
            ORDER BY i.due_date ASC, i.date_issued DESC
        ''').fetchall()

    return render_template('invoices/list.html', invoices=invoices, invoice_statuses=INVOICE_STATUSES, current_status=status_filter)


@app.route('/invoices/new', methods=['GET', 'POST'])
def invoice_new():
    """Create a new invoice."""
    db = get_db()
    matters = db.execute('''
        SELECT m.id, m.title, c.name as client_name
        FROM matters m
        JOIN clients c ON m.client_id = c.id
        ORDER BY c.name, m.title
    ''').fetchall()

    if request.method == 'POST':
        matter_id = request.form['matter_id']
        invoice_number = request.form['invoice_number']
        amount = float(request.form['amount'])
        date_issued = request.form['date_issued']
        due_date = request.form.get('due_date') or None
        description = request.form.get('description', '')

        context_row = db.execute('''
            SELECT
                m.id as matter_id,
                m.title as matter_title,
                m.description as matter_description,
                m.hours_billed_amount as hours_billed_amount,
                c.name as client_name,
                c.contact_name as contact_name,
                c.contact_email as contact_email,
                c.default_rate as default_rate,
                c.client_directory as client_directory
            FROM matters m
            JOIN clients c ON m.client_id = c.id
            WHERE m.id = ?
        ''', (matter_id,)).fetchone()

        db.execute('''
            INSERT INTO invoices (matter_id, invoice_number, amount, date_issued, due_date, description, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            matter_id,
            invoice_number,
            amount,
            date_issued,
            due_date,
            description,
            'Unpaid'
        ))
        db.commit()

        if context_row:
            firm_directory = load_settings().get('firm_directory', '')
            client_dir = resolve_client_directory(firm_directory, context_row['client_directory'])
            template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'InvoiceForm.docx')
            docx_path, docx_error = _create_invoice_docx(template_path, {
                'invoice_number': invoice_number,
                'date_issued': date_issued,
                'amount': amount,
                'client_name': context_row['client_name'],
                'contact_name': context_row['contact_name'],
                'contact_email': context_row['contact_email'],
                'matter_title': context_row['matter_title'],
                'matter_description': context_row['matter_description'],
                'hours_billed_amount': context_row['hours_billed_amount'],
                'default_rate': context_row['default_rate'],
            }, client_dir=client_dir)

            if docx_path:
                if _open_path_native(docx_path):
                    flash('Invoice created successfully. Invoice form document opened.', 'success')
                else:
                    flash('Invoice created successfully. Invoice form was generated but could not be opened automatically.', 'warning')
            elif docx_error:
                flash(f'Invoice created successfully. Invoice form was not generated: {docx_error}.', 'warning')
            else:
                flash('Invoice created successfully.', 'success')
        else:
            flash('Invoice created successfully.', 'success')

        return redirect(url_for('invoices_list'))

    selected_matter = request.args.get('matter_id', '')
    today = date.today().isoformat()
    prefill_amount = ''
    if selected_matter:
        matter_row = db.execute(
            'SELECT rate, hours_billed_amount, reimbursement_amount, fixed_fee_enabled, fixed_fee_amount FROM matters WHERE id = ?',
            (selected_matter,)
        ).fetchone()
        if matter_row:
            total, _ = compute_matter_total(dict(matter_row))
            if total:
                prefill_amount = f'{total:.2f}'
    return render_template('invoices/form.html', invoice=None, matters=matters, selected_matter=selected_matter, today=today, default_invoice_number=generate_invoice_number(), prefill_amount=prefill_amount)


@app.route('/invoices/<int:id>')
def invoice_view(id):
    """View a single invoice with its payments."""
    db = get_db()
    invoice = db.execute('''
        SELECT i.*, m.title as matter_title, m.id as matter_id, c.name as client_name
        FROM invoices i
        JOIN matters m ON i.matter_id = m.id
        JOIN clients c ON m.client_id = c.id
        WHERE i.id = ?
    ''', (id,)).fetchone()

    if not invoice:
        flash('Invoice not found.', 'error')
        return redirect(url_for('invoices_list'))

    payments = db.execute('SELECT * FROM payments WHERE invoice_id = ? ORDER BY date_received DESC', (id,)).fetchall()

    return render_template('invoices/view.html', invoice=invoice, payments=payments)


@app.route('/invoices/<int:id>/edit', methods=['GET', 'POST'])
def invoice_edit(id):
    """Edit an invoice."""
    db = get_db()
    invoice = db.execute('SELECT * FROM invoices WHERE id = ?', (id,)).fetchone()
    if not invoice:
        flash('Invoice not found.', 'error')
        return redirect(url_for('invoices_list'))

    matters = db.execute('''
        SELECT m.id, m.title, c.name as client_name
        FROM matters m
        JOIN clients c ON m.client_id = c.id
        ORDER BY c.name, m.title
    ''').fetchall()

    if request.method == 'POST':
        db.execute('''
            UPDATE invoices
            SET matter_id = ?, invoice_number = ?, amount = ?, date_issued = ?, due_date = ?, description = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (
            request.form['matter_id'],
            request.form['invoice_number'],
            float(request.form['amount']),
            request.form['date_issued'],
            request.form.get('due_date') or None,
            request.form.get('description', ''),
            id
        ))
        db.commit()
        flash('Invoice updated successfully.', 'success')
        return redirect(url_for('invoice_view', id=id))

    return render_template('invoices/form.html', invoice=invoice, matters=matters, selected_matter='', today='')


@app.route('/invoices/<int:id>/mark-paid', methods=['POST'])
def invoice_mark_paid(id):
    """Mark an invoice as paid and record the payment."""
    db = get_db()
    invoice = db.execute('SELECT * FROM invoices WHERE id = ?', (id,)).fetchone()
    if not invoice:
        flash('Invoice not found.', 'error')
        return redirect(url_for('invoices_list'))

    # Create payment record
    db.execute('''
        INSERT INTO payments (invoice_id, amount, date_received, method, account)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        id,
        invoice['amount'],
        request.form.get('date_received', date.today().isoformat()),
        request.form.get('method', ''),
        request.form.get('account', '')
    ))

    # Mark invoice as paid
    db.execute('UPDATE invoices SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', ('Paid', id))

    # If matter is "Awaiting Payment" and all its invoices are now paid, advance to "Satisfied"
    matter_id = invoice['matter_id']
    matter = db.execute('SELECT status FROM matters WHERE id = ?', (matter_id,)).fetchone()
    if matter and matter['status'] == 'Awaiting Payment':
        unpaid_count = db.execute(
            'SELECT COUNT(*) as count FROM invoices WHERE matter_id = ? AND status != ?',
            (matter_id, 'Paid')
        ).fetchone()['count']
        if unpaid_count == 0:
            db.execute('UPDATE matters SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', ('Satisfied', matter_id))
            flash('Invoice marked as paid. Matter advanced to Satisfied.', 'success')
        else:
            flash('Invoice marked as paid.', 'success')
    else:
        flash('Invoice marked as paid.', 'success')

    db.commit()
    return redirect(request.referrer or url_for('invoice_view', id=id))


@app.route('/matters/<int:id>/mark-paid', methods=['POST'])
def matter_mark_paid(id):
    """Mark an Awaiting Payment matter as paid: pay any unpaid invoices and advance to Satisfied."""
    db = get_db()
    matter = db.execute('SELECT * FROM matters WHERE id = ?', (id,)).fetchone()
    if not matter:
        flash('Matter not found.', 'error')
        return redirect(url_for('matters_list'))

    date_received = request.form.get('date_received', date.today().isoformat())
    method = request.form.get('method', '')
    account = request.form.get('account', '')

    # Mark any unpaid invoices for this matter as paid, recording a payment for
    # each so the Unpaid Invoices section stays in sync.
    unpaid_invoices = db.execute('SELECT * FROM invoices WHERE matter_id = ? AND status = ?', (id, 'Unpaid')).fetchall()
    for inv in unpaid_invoices:
        db.execute('''
            INSERT INTO payments (invoice_id, amount, date_received, method, account)
            VALUES (?, ?, ?, ?, ?)
        ''', (inv['id'], inv['amount'], date_received, method, account))
        db.execute('UPDATE invoices SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', ('Paid', inv['id']))

    db.execute('UPDATE matters SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', ('Satisfied', id))
    db.commit()
    flash('Matter marked as paid. Matter advanced to Satisfied.', 'success')
    return redirect(request.referrer or url_for('dashboard'))


@app.route('/invoices/<int:id>/delete', methods=['POST'])
def invoice_delete(id):
    """Delete an invoice."""
    db = get_db()
    # Delete associated payments first
    db.execute('DELETE FROM payments WHERE invoice_id = ?', (id,))
    db.execute('DELETE FROM invoices WHERE id = ?', (id,))
    db.commit()
    flash('Invoice deleted.', 'success')
    return redirect(url_for('invoices_list'))


# ============== PAYMENT ROUTES ==============

@app.route('/payments')
def payments_list():
    """List all payments."""
    db = get_db()
    payments = db.execute('''
        SELECT p.*, i.invoice_number, i.amount as invoice_amount, m.title as matter_title, c.name as client_name
        FROM payments p
        JOIN invoices i ON p.invoice_id = i.id
        JOIN matters m ON i.matter_id = m.id
        JOIN clients c ON m.client_id = c.id
        ORDER BY p.date_received DESC
    ''').fetchall()
    return render_template('payments/list.html', payments=payments)


@app.route('/payments/<int:id>/delete', methods=['POST'])
def payment_delete(id):
    """Delete a payment."""
    db = get_db()
    payment = db.execute('SELECT invoice_id FROM payments WHERE id = ?', (id,)).fetchone()
    if payment:
        db.execute('DELETE FROM payments WHERE id = ?', (id,))
        # Check if invoice should be marked unpaid
        remaining = db.execute('SELECT COUNT(*) as count FROM payments WHERE invoice_id = ?', (payment['invoice_id'],)).fetchone()['count']
        if remaining == 0:
            db.execute('UPDATE invoices SET status = ? WHERE id = ?', ('Unpaid', payment['invoice_id']))
        db.commit()
        flash('Payment deleted.', 'success')
    return redirect(request.referrer or url_for('payments_list'))


# ============== SEARCH ROUTES ==============

def extract_text_from_docx(filepath):
    """Extract text content from a .docx file."""
    try:
        with zipfile.ZipFile(filepath, 'r') as z:
            xml_content = z.read('word/document.xml')
            tree = ET.fromstring(xml_content)
            # Extract all text from w:t elements
            namespaces = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            texts = []
            for elem in tree.iter():
                if elem.tag.endswith('}t'):
                    if elem.text:
                        texts.append(elem.text)
            return ' '.join(texts)
    except Exception as e:
        return ''


def extract_text_from_pdf(filepath):
    """Extract text content from a PDF file (text-based PDFs only)."""
    try:
        # Simple PDF text extraction without external dependencies
        with open(filepath, 'rb') as f:
            content = f.read()

        # Very basic text extraction for simple PDFs
        # For production, you'd want PyPDF2 or pdfplumber
        text_parts = []

        # Try to find text streams in the PDF
        import re
        # Look for text between BT and ET markers (basic approach)
        stream_pattern = rb'stream\s*(.*?)\s*endstream'
        for match in re.finditer(stream_pattern, content, re.DOTALL):
            stream = match.group(1)
            # Try to decode as text
            try:
                decoded = stream.decode('utf-8', errors='ignore')
                # Extract readable text
                text = re.sub(r'[^\x20-\x7E\s]', ' ', decoded)
                text_parts.append(text)
            except:
                pass

        return ' '.join(text_parts)
    except Exception as e:
        return ''


def search_files(directory, query):
    """Search for query in files within directory."""
    results = []
    query_lower = query.lower()

    if not directory or not os.path.isdir(directory):
        return results

    # Supported extensions
    extensions = {'.docx', '.pdf', '.txt'}

    for root, dirs, files in os.walk(directory):
        # Skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in extensions:
                continue

            filepath = os.path.join(root, filename)

            # Check filename match
            filename_match = query_lower in filename.lower()

            # Extract and search content
            content = ''
            content_match = False
            snippet = ''

            try:
                if ext == '.txt':
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                elif ext == '.docx':
                    content = extract_text_from_docx(filepath)
                elif ext == '.pdf':
                    content = extract_text_from_pdf(filepath)

                if content and query_lower in content.lower():
                    content_match = True
                    # Create snippet around match
                    idx = content.lower().find(query_lower)
                    start = max(0, idx - 50)
                    end = min(len(content), idx + len(query) + 50)
                    snippet = '...' + content[start:end].replace('\n', ' ') + '...'
            except Exception as e:
                pass

            if filename_match or content_match:
                results.append({
                    'filepath': filepath,
                    'filename': filename,
                    'filename_match': filename_match,
                    'content_match': content_match,
                    'snippet': snippet,
                    'type': ext[1:].upper()
                })

    return results[:100]  # Limit results


@app.route('/search')
def search():
    """Search page."""
    query = request.args.get('q', '')
    results = []
    settings = load_settings()
    firm_directory = settings.get('firm_directory', '')
    client_id = request.args.get('client_id', '').strip()
    directory_override = request.args.get('directory', '').strip()

    selected_directory = firm_directory
    selected_client_id = ''

    clients = get_db().execute('SELECT id, name, client_directory FROM clients ORDER BY name').fetchall()

    if client_id:
        selected_client_id = client_id
        client = get_db().execute('SELECT client_directory FROM clients WHERE id = ?', (client_id,)).fetchone()
        if client and client['client_directory']:
            selected_directory = resolve_client_directory(firm_directory, client['client_directory'])

    if directory_override:
        selected_directory = directory_override

    if query and selected_directory:
        results = search_files(selected_directory, query)
    elif query and not selected_directory:
        flash('Please set your firm directory in Settings before searching.', 'error')

    return render_template('search.html', query=query, results=results, firm_directory=firm_directory,
                           clients=clients, selected_client_id=selected_client_id,
                           selected_directory=selected_directory)


# ============== SETTINGS ROUTES ==============

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    """Settings page."""
    current_settings = load_settings()

    if request.method == 'POST':
        firm_directory = request.form.get('firm_directory', '').strip()
        theme = request.form.get('theme', 'varia').strip() or 'varia'
        font = request.form.get('font', 'inter').strip() or 'inter'

        # Validate theme and font
        valid_themes = {'varia', 'greyscale', 'retrofuturist', 'paper'}
        valid_fonts = {'system', 'inter', 'playfair', 'jetbrains-mono'}
        if theme not in valid_themes:
            theme = 'varia'
        if font not in valid_fonts:
            font = 'inter'

        # Validate directory exists
        if firm_directory and not os.path.isdir(firm_directory):
            flash('The specified directory does not exist.', 'error')
        else:
            current_settings['firm_directory'] = firm_directory
            current_settings['theme'] = theme
            current_settings['font'] = font
            save_settings(current_settings)
            flash('Settings saved successfully.', 'success')
            return redirect(url_for('settings'))

    db_path = os.path.abspath(DATABASE)
    db_folder = os.path.dirname(db_path)
    return render_template('settings.html', settings=current_settings, db_path=db_path, db_folder=db_folder)


# ============== REPORTS ROUTES ==============

@app.route('/reports')
def reports_index():
    """Reports dashboard."""
    return render_template('reports/index.html')


@app.route('/reports/ar-aging')
def report_ar_aging():
    """Accounts receivable aging (unpaid invoices bucketed by overdue days)."""
    db = get_db()
    as_of_raw = (request.args.get('as_of') or '').strip()
    as_of = date.today()
    if as_of_raw:
        try:
            as_of = date.fromisoformat(as_of_raw)
        except ValueError:
            flash('Invalid As Of date. Using today.', 'error')

    invoices = db.execute('''
        SELECT i.*, m.title as matter_title, c.name as client_name
        FROM invoices i
        JOIN matters m ON i.matter_id = m.id
        JOIN clients c ON m.client_id = c.id
        WHERE i.status = 'Unpaid'
        ORDER BY c.name ASC, i.due_date ASC, i.date_issued DESC
    ''').fetchall()

    buckets = [
        ('Current', 0, 0),
        ('1-30', 1, 30),
        ('31-60', 31, 60),
        ('61-90', 61, 90),
        ('90+', 91, None),
        ('No Due Date', None, None),
    ]

    def bucket_for(days_overdue, has_due_date):
        if not has_due_date:
            return 'No Due Date'
        if days_overdue <= 0:
            return 'Current'
        if 1 <= days_overdue <= 30:
            return '1-30'
        if 31 <= days_overdue <= 60:
            return '31-60'
        if 61 <= days_overdue <= 90:
            return '61-90'
        return '90+'

    detailed = []
    totals_by_bucket = {name: 0.0 for name, _, _ in buckets}
    totals_by_client = {}

    for inv in invoices:
        due_raw = inv['due_date']
        amount = float(inv['amount'] or 0)
        has_due_date = bool(due_raw)
        days_overdue = None
        if has_due_date:
            try:
                due_date = date.fromisoformat(due_raw)
                days_overdue = (as_of - due_date).days
            except ValueError:
                has_due_date = False

        bucket = bucket_for(days_overdue or 0, has_due_date)
        totals_by_bucket[bucket] = totals_by_bucket.get(bucket, 0.0) + amount

        client_key = inv['client_name']
        if client_key not in totals_by_client:
            totals_by_client[client_key] = {name: 0.0 for name, _, _ in buckets}
            totals_by_client[client_key]['Total'] = 0.0
        totals_by_client[client_key][bucket] += amount
        totals_by_client[client_key]['Total'] += amount

        detailed.append({
            **dict(inv),
            'days_overdue_calc': days_overdue,
            'aging_bucket': bucket
        })

    if request.args.get('format') == 'csv':
        rows = []
        for inv in detailed:
            rows.append([
                inv.get('client_name', ''),
                inv.get('matter_title', ''),
                inv.get('invoice_number', ''),
                f'{float(inv.get("amount") or 0):.2f}',
                inv.get('date_issued') or '',
                inv.get('due_date') or '',
                '' if inv.get('days_overdue_calc') is None else int(inv.get('days_overdue_calc')),
                inv.get('aging_bucket', ''),
            ])
        filename = f'ar_aging_{as_of.isoformat()}.csv'
        return _csv_response(
            filename,
            ['Client', 'Matter', 'Invoice #', 'Amount', 'Date Issued', 'Due Date', 'Days Overdue', 'Bucket'],
            rows
        )

    client_rows = []
    for client_name in sorted(totals_by_client.keys()):
        client_rows.append({'client_name': client_name, **totals_by_client[client_name]})

    return render_template(
        'reports/ar_aging.html',
        as_of=as_of.isoformat(),
        buckets=[b[0] for b in buckets],
        totals_by_bucket=totals_by_bucket,
        clients=client_rows,
        invoices=detailed
    )


@app.route('/reports/wip-pipeline')
def report_wip_pipeline():
    """WIP and pipeline report (matters by status + deadlines + needs invoicing value)."""
    db = get_db()
    horizon_raw = (request.args.get('days') or '').strip()
    horizon_days = 30
    if horizon_raw:
        try:
            horizon_days = max(1, min(365, int(horizon_raw)))
        except ValueError:
            horizon_days = 30

    today = date.today()
    horizon_date = today + timedelta(days=horizon_days)

    matters = db.execute('''
        SELECT m.*, c.name as client_name
        FROM matters m
        JOIN clients c ON m.client_id = c.id
        WHERE m.status NOT IN ('Satisfied', 'Inactive/Terminated')
        ORDER BY m.deadline ASC, m.created_at DESC
    ''').fetchall()

    matters_enriched = enrich_matter_totals(matters)

    status_summary = []
    for status in MATTER_STATUSES:
        if status in ('Satisfied', 'Inactive/Terminated'):
            continue
        subset = [m for m in matters_enriched if m.get('status') == status]
        total = sum(float(m.get('total_amount') or 0) for m in subset)
        status_summary.append({
            'status': status,
            'count': len(subset),
            'total': total,
            'total_display': format_money(total),
        })

    needs_invoicing = [m for m in matters_enriched if m.get('status') == 'Needs Invoicing']
    needs_invoicing_total = sum(float(m.get('total_amount') or 0) for m in needs_invoicing)

    upcoming_deadlines = []
    for m in matters_enriched:
        deadline_raw = m.get('deadline') or ''
        if not deadline_raw:
            continue
        try:
            deadline_date = date.fromisoformat(deadline_raw)
        except ValueError:
            continue
        if today <= deadline_date <= horizon_date:
            upcoming_deadlines.append(m)
    upcoming_deadlines.sort(key=lambda m: (m.get('deadline') or ''))

    if request.args.get('format') == 'csv':
        rows = []
        for s in status_summary:
            rows.append(['Status Summary', s['status'], s['count'], f'{float(s["total"]):.2f}', '', '', ''])
        for m in needs_invoicing:
            rows.append(['Needs Invoicing', m.get('status', ''), '', f'{float(m.get("total_amount") or 0):.2f}', m.get('client_name', ''), m.get('title', ''), m.get('deadline') or ''])
        for m in upcoming_deadlines:
            rows.append(['Upcoming Deadlines', m.get('status', ''), '', f'{float(m.get("total_amount") or 0):.2f}', m.get('client_name', ''), m.get('title', ''), m.get('deadline') or ''])
        filename = f'wip_pipeline_{today.isoformat()}.csv'
        return _csv_response(
            filename,
            ['Section', 'Status', 'Count', 'Total', 'Client', 'Matter', 'Deadline'],
            rows
        )

    return render_template(
        'reports/wip_pipeline.html',
        today=today.isoformat(),
        horizon_days=horizon_days,
        status_summary=status_summary,
        needs_invoicing=needs_invoicing,
        needs_invoicing_total=needs_invoicing_total,
        upcoming_deadlines=upcoming_deadlines
    )


@app.route('/reports/clients')
def report_clients():
    """Client financial summary (WIP + invoices)."""
    db = get_db()
    clients = db.execute('SELECT id, name FROM clients ORDER BY name ASC').fetchall()
    matters = db.execute('''
        SELECT m.*, c.name as client_name
        FROM matters m
        JOIN clients c ON m.client_id = c.id
    ''').fetchall()
    invoices = db.execute('''
        SELECT i.*, m.client_id, c.name as client_name
        FROM invoices i
        JOIN matters m ON i.matter_id = m.id
        JOIN clients c ON m.client_id = c.id
    ''').fetchall()

    matters_by_client = {}
    for m in enrich_matter_totals(matters):
        matters_by_client.setdefault(m['client_id'], []).append(m)

    invoices_by_client = {}
    for inv in invoices:
        invoices_by_client.setdefault(inv['client_id'], []).append(inv)

    rows = []
    for client in clients:
        cid = client['id']
        client_matters = matters_by_client.get(cid, [])
        client_invoices = invoices_by_client.get(cid, [])

        active_matters = [m for m in client_matters if m.get('status') not in ('Satisfied', 'Inactive/Terminated')]
        wip_matters = [m for m in client_matters if m.get('status') in ('In-Process', 'Awaiting Response', 'Needs Invoicing')]
        needs_invoicing = [m for m in client_matters if m.get('status') == 'Needs Invoicing']

        wip_total = sum(float(m.get('total_amount') or 0) for m in wip_matters)
        needs_invoicing_total = sum(float(m.get('total_amount') or 0) for m in needs_invoicing)

        unpaid_total = sum(float(i['amount'] or 0) for i in client_invoices if i['status'] == 'Unpaid')
        paid_total = sum(float(i['amount'] or 0) for i in client_invoices if i['status'] == 'Paid')
        total_invoiced = sum(float(i['amount'] or 0) for i in client_invoices)

        rows.append({
            'client_id': cid,
            'client_name': client['name'],
            'active_matters': len(active_matters),
            'wip_total': wip_total,
            'wip_total_display': format_money(wip_total),
            'needs_invoicing_total': needs_invoicing_total,
            'needs_invoicing_total_display': format_money(needs_invoicing_total),
            'unpaid_total': unpaid_total,
            'unpaid_total_display': format_money(unpaid_total),
            'paid_total': paid_total,
            'paid_total_display': format_money(paid_total),
            'total_invoiced': total_invoiced,
            'total_invoiced_display': format_money(total_invoiced),
        })

    if request.args.get('format') == 'csv':
        csv_rows = []
        for r in rows:
            csv_rows.append([
                r['client_name'],
                r['active_matters'],
                f'{float(r["wip_total"]):.2f}',
                f'{float(r["needs_invoicing_total"]):.2f}',
                f'{float(r["unpaid_total"]):.2f}',
                f'{float(r["paid_total"]):.2f}',
                f'{float(r["total_invoiced"]):.2f}',
            ])
        filename = f'clients_report_{date.today().isoformat()}.csv'
        return _csv_response(
            filename,
            ['Client', 'Active Matters', 'WIP Total', 'Needs Invoicing', 'Unpaid', 'Paid', 'Total Invoiced'],
            csv_rows
        )

    return render_template('reports/clients.html', clients=rows)


@app.route('/reports/outstanding')
def report_outstanding():
    """Outstanding invoices report."""
    db = get_db()
    invoices = db.execute('''
        SELECT i.*, m.title as matter_title, c.name as client_name,
               julianday(date('now')) - julianday(i.due_date) as days_overdue
        FROM invoices i
        JOIN matters m ON i.matter_id = m.id
        JOIN clients c ON m.client_id = c.id
        WHERE i.status = 'Unpaid'
        ORDER BY i.due_date ASC
    ''').fetchall()

    total = sum(inv['amount'] for inv in invoices)

    return render_template('reports/outstanding.html', invoices=invoices, total=total)


# ============== API ROUTES ==============

@app.route('/api/search-clients')
def api_search_clients():
    """API endpoint to search clients for autocomplete."""
    query = request.args.get('q', '').lower()
    db = get_db()
    clients = db.execute('''
        SELECT id, name, contact_name, contact_email
        FROM clients
        WHERE LOWER(name) LIKE ? OR LOWER(contact_name) LIKE ? OR LOWER(contact_email) LIKE ?
        LIMIT 10
    ''', (f'%{query}%', f'%{query}%', f'%{query}%')).fetchall()

    return jsonify([dict(c) for c in clients])


@app.route('/api/list-directories')
def api_list_directories():
    """API endpoint to list directories under firm directory."""
    settings = load_settings()
    firm_directory = settings.get('firm_directory', '')
    if not firm_directory or not os.path.isdir(firm_directory):
        return jsonify({'error': 'Firm directory not configured.'}), 400

    requested = request.args.get('path', '').strip()
    if requested.lower() in ('none', 'null', 'undefined'):
        requested = ''
    base_path = Path(firm_directory).resolve()
    target_path = (base_path / requested).resolve() if requested else base_path

    try:
        target_path.relative_to(base_path)
    except ValueError:
        return jsonify({'error': 'Invalid path.'}), 400

    if not target_path.is_dir():
        return jsonify({'error': 'Directory not found.'}), 404

    directories = sorted([p.name for p in target_path.iterdir() if p.is_dir() and not p.name.startswith('.')])
    parent = ''
    if target_path != base_path:
        parent = str(target_path.parent.relative_to(base_path))

    return jsonify({
        'path': str(target_path.relative_to(base_path)) if target_path != base_path else '',
        'parent': parent,
        'directories': directories
    })


def _is_local_origin():
    """Basic CSRF mitigation for local-only privileged actions."""
    origin = request.headers.get('Origin', '')
    if not origin:
        return False
    return origin.startswith('http://127.0.0.1:') or origin.startswith('http://localhost:')


def _open_path_native(path):
    """Open a file/folder using the OS default handler."""
    try:
        if os.name == 'nt':
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        if sys.platform == 'darwin':
            subprocess.Popen(['open', path])
            return True
        subprocess.Popen(['xdg-open', path])
        return True
    except Exception:
        return False


@app.route('/api/open-path', methods=['POST'])
def api_open_path():
    """Open a file locally, or fall back to its folder."""
    if not _is_local_origin():
        return jsonify({'error': 'Forbidden'}), 403

    data = request.get_json(silent=True) or {}
    path = (data.get('path') or '').strip()
    if not path:
        return jsonify({'error': 'Missing path'}), 400

    if not os.path.isabs(path):
        return jsonify({'error': 'Path must be absolute'}), 400

    # Restrict to supported types
    ext = os.path.splitext(path)[1].lower()
    if ext not in ('.docx', '.pdf', '.txt'):
        return jsonify({'error': 'Unsupported file type'}), 400

    if not os.path.isfile(path):
        return jsonify({'error': 'File not found'}), 404

    opened = _open_path_native(path)
    if opened:
        return jsonify({'status': 'opened'})

    folder = os.path.dirname(path)
    if folder and os.path.isdir(folder) and _open_path_native(folder):
        return jsonify({'status': 'opened_folder'})

    return jsonify({'error': 'Unable to open'}), 500


@app.route('/api/open-data-folder', methods=['POST'])
def api_open_data_folder():
    """Open the folder containing the local database file."""
    if not _is_local_origin():
        return jsonify({'error': 'Forbidden'}), 403

    folder = os.path.dirname(os.path.abspath(DATABASE))
    if not folder or not os.path.isdir(folder):
        return jsonify({'error': 'Data folder not found'}), 404

    if _open_path_native(folder):
        return jsonify({'status': 'opened'})

    return jsonify({'error': 'Unable to open'}), 500


if __name__ == '__main__':
    print("\n" + "="*50)
    print("  VARIA LAW - Matter Tracker")
    print("="*50)
    print("\n  Open your browser to: http://127.0.0.1:5000")
    print("  Press Ctrl+C to stop the server\n")
    app.run(debug=False, host='127.0.0.1', port=5000)
