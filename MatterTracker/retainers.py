"""Retainer balance and transaction tracking for Matter Tracker."""

import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for


retainers_bp = Blueprint('retainers', __name__)
_database_path = None


def init_retainer_db(database_path):
    """Create the isolated retainer tables and remember the app database path."""
    global _database_path
    _database_path = database_path

    db = sqlite3.connect(database_path)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS retainers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL UNIQUE,
            account TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients (id)
        );

        CREATE TABLE IF NOT EXISTS retainer_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            retainer_id INTEGER NOT NULL,
            transaction_type TEXT NOT NULL CHECK (transaction_type IN ('contribution', 'deduction')),
            amount REAL NOT NULL CHECK (amount > 0),
            transaction_date DATE NOT NULL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (retainer_id) REFERENCES retainers (id)
        );

        CREATE INDEX IF NOT EXISTS idx_retainers_client ON retainers(client_id);
        CREATE INDEX IF NOT EXISTS idx_retainer_transactions_retainer
            ON retainer_transactions(retainer_id);
        CREATE INDEX IF NOT EXISTS idx_retainer_transactions_date
            ON retainer_transactions(retainer_id, transaction_date);

        CREATE TRIGGER IF NOT EXISTS cleanup_retainer_before_client_delete
        BEFORE DELETE ON clients
        BEGIN
            DELETE FROM retainer_transactions
            WHERE retainer_id IN (SELECT id FROM retainers WHERE client_id = OLD.id);
            DELETE FROM retainers WHERE client_id = OLD.id;
        END;
    ''')
    db.commit()
    db.close()


def _get_db():
    """Return a connection for this blueprint without coupling to app.py globals."""
    if not _database_path:
        raise RuntimeError('Retainer database has not been initialized.')
    db = sqlite3.connect(_database_path)
    db.row_factory = sqlite3.Row
    return db


def _parse_amount(raw_amount):
    """Parse a positive currency value with at most two decimal places."""
    try:
        amount = Decimal(str(raw_amount).strip().replace(',', ''))
    except (InvalidOperation, ValueError):
        return None

    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        return None
    return amount.quantize(Decimal('0.01'))


def _valid_date(raw_date):
    """Return an ISO date string when valid, otherwise None."""
    try:
        return date.fromisoformat((raw_date or '').strip()).isoformat()
    except ValueError:
        return None


def _balance_for(db, retainer_id):
    row = db.execute('''
        SELECT COALESCE(SUM(
            CASE WHEN transaction_type = 'contribution' THEN amount ELSE -amount END
        ), 0) AS balance
        FROM retainer_transactions
        WHERE retainer_id = ?
    ''', (retainer_id,)).fetchone()
    return Decimal(str(row['balance'] or 0)).quantize(Decimal('0.01'))


@retainers_bp.route('/retainers')
def list_retainers():
    """Show every configured client retainer and its transaction history."""
    db = _get_db()
    retainers = db.execute('''
        SELECT r.id, r.client_id, r.account, c.name AS client_name,
               COUNT(t.id) AS transaction_count,
               COALESCE(SUM(
                   CASE WHEN t.transaction_type = 'contribution' THEN t.amount
                        WHEN t.transaction_type = 'deduction' THEN -t.amount
                        ELSE 0 END
               ), 0) AS balance,
               MAX(t.transaction_date) AS last_activity
        FROM retainers r
        JOIN clients c ON c.id = r.client_id
        LEFT JOIN retainer_transactions t ON t.retainer_id = r.id
        GROUP BY r.id, r.client_id, r.account, c.name
        ORDER BY c.name ASC
    ''').fetchall()

    transactions = db.execute('''
        SELECT id, retainer_id, transaction_type, amount, transaction_date, note
        FROM retainer_transactions
        ORDER BY transaction_date DESC, id DESC
    ''').fetchall()

    transactions_by_retainer = {}
    for transaction in transactions:
        transactions_by_retainer.setdefault(transaction['retainer_id'], []).append(transaction)

    available_clients = db.execute('''
        SELECT c.id, c.name
        FROM clients c
        LEFT JOIN retainers r ON r.client_id = c.id
        WHERE r.id IS NULL
        ORDER BY c.name ASC
    ''').fetchall()
    db.close()

    total_held = sum((Decimal(str(row['balance'] or 0)) for row in retainers), Decimal('0'))
    return render_template(
        'retainers/list.html',
        retainers=retainers,
        transactions_by_retainer=transactions_by_retainer,
        available_clients=available_clients,
        total_held=float(total_held),
    )


@retainers_bp.route('/retainers/new', methods=['POST'])
def create_retainer():
    """Create one retainer account for a client."""
    client_id = request.form.get('client_id', type=int)
    account = request.form.get('account', '').strip()
    if not client_id or not account:
        flash('Client and account are required.', 'error')
        return redirect(url_for('retainers.list_retainers'))

    db = _get_db()
    client = db.execute('SELECT id FROM clients WHERE id = ?', (client_id,)).fetchone()
    existing = db.execute('SELECT id FROM retainers WHERE client_id = ?', (client_id,)).fetchone()
    if not client:
        db.close()
        flash('Client not found.', 'error')
        return redirect(url_for('retainers.list_retainers'))
    if existing:
        db.close()
        flash('That client already has a retainer.', 'error')
        return redirect(url_for('retainers.list_retainers', _anchor=f'retainer-{existing["id"]}'))

    cursor = db.execute(
        'INSERT INTO retainers (client_id, account) VALUES (?, ?)',
        (client_id, account),
    )
    db.commit()
    retainer_id = cursor.lastrowid
    db.close()
    flash('Retainer created successfully.', 'success')
    return redirect(url_for('retainers.list_retainers', _anchor=f'retainer-{retainer_id}'))


@retainers_bp.route('/retainers/<int:retainer_id>/account', methods=['POST'])
def update_account(retainer_id):
    """Update the account where a client's retainer is held."""
    account = request.form.get('account', '').strip()
    if not account:
        flash('Account is required.', 'error')
        return redirect(url_for('retainers.list_retainers', _anchor=f'retainer-{retainer_id}'))

    db = _get_db()
    cursor = db.execute('''
        UPDATE retainers
        SET account = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (account, retainer_id))
    db.commit()
    db.close()
    flash('Retainer account updated.' if cursor.rowcount else 'Retainer not found.',
          'success' if cursor.rowcount else 'error')
    return redirect(url_for('retainers.list_retainers', _anchor=f'retainer-{retainer_id}'))


@retainers_bp.route('/retainers/<int:retainer_id>/transactions', methods=['POST'])
def add_transaction(retainer_id):
    """Record a contribution to or deduction from a retainer."""
    transaction_type = request.form.get('transaction_type', '').strip().lower()
    amount = _parse_amount(request.form.get('amount', ''))
    transaction_date = _valid_date(request.form.get('transaction_date', ''))
    note = request.form.get('note', '').strip()

    if transaction_type not in ('contribution', 'deduction'):
        flash('Choose contribution or deduction.', 'error')
        return redirect(url_for('retainers.list_retainers', _anchor=f'retainer-{retainer_id}'))
    if amount is None:
        flash('Enter a positive amount with no more than two decimal places.', 'error')
        return redirect(url_for('retainers.list_retainers', _anchor=f'retainer-{retainer_id}'))
    if not transaction_date:
        flash('Enter a valid transaction date.', 'error')
        return redirect(url_for('retainers.list_retainers', _anchor=f'retainer-{retainer_id}'))

    db = _get_db()
    retainer = db.execute('SELECT id FROM retainers WHERE id = ?', (retainer_id,)).fetchone()
    if not retainer:
        db.close()
        flash('Retainer not found.', 'error')
        return redirect(url_for('retainers.list_retainers'))

    if transaction_type == 'deduction' and amount > _balance_for(db, retainer_id):
        db.close()
        flash('A deduction cannot exceed the current retainer balance.', 'error')
        return redirect(url_for('retainers.list_retainers', _anchor=f'retainer-{retainer_id}'))

    db.execute('''
        INSERT INTO retainer_transactions
            (retainer_id, transaction_type, amount, transaction_date, note)
        VALUES (?, ?, ?, ?, ?)
    ''', (retainer_id, transaction_type, float(amount), transaction_date, note))
    db.commit()
    db.close()
    flash(f'{transaction_type.title()} recorded successfully.', 'success')
    return redirect(url_for('retainers.list_retainers', _anchor=f'retainer-{retainer_id}'))


@retainers_bp.route('/retainers/transactions/<int:transaction_id>/delete', methods=['POST'])
def delete_transaction(transaction_id):
    """Delete a transaction while preventing an invalid negative balance."""
    db = _get_db()
    transaction = db.execute('''
        SELECT id, retainer_id, transaction_type, amount
        FROM retainer_transactions
        WHERE id = ?
    ''', (transaction_id,)).fetchone()
    if not transaction:
        db.close()
        flash('Retainer transaction not found.', 'error')
        return redirect(url_for('retainers.list_retainers'))

    retainer_id = transaction['retainer_id']
    if transaction['transaction_type'] == 'contribution':
        resulting_balance = _balance_for(db, retainer_id) - Decimal(str(transaction['amount']))
        if resulting_balance < 0:
            db.close()
            flash('This contribution cannot be deleted because later deductions depend on it.', 'error')
            return redirect(url_for('retainers.list_retainers', _anchor=f'retainer-{retainer_id}'))

    db.execute('DELETE FROM retainer_transactions WHERE id = ?', (transaction_id,))
    db.commit()
    db.close()
    flash('Retainer transaction deleted.', 'success')
    return redirect(url_for('retainers.list_retainers', _anchor=f'retainer-{retainer_id}'))
