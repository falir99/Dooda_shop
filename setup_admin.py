"""Создание или сброс локальной учётной записи DOODA SHOP."""
import argparse
import getpass
import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'catalog.sqlite3'


def generate_password_hash(password):
    salt = secrets.token_hex(16)
    iterations = 1_000_000
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), iterations).hex()
    return f'pbkdf2:sha256:{iterations}${salt}${digest}'

parser = argparse.ArgumentParser()
parser.add_argument('--username', default='admin')
parser.add_argument('--generate', action='store_true', help='сгенерировать временный пароль')
args = parser.parse_args()
username = args.username.strip()[:64]
if not username:
    raise SystemExit('Логин не может быть пустым')
password = secrets.token_urlsafe(16) if args.generate else getpass.getpass('Новый пароль администратора: ')
if len(password) < 12:
    raise SystemExit('Нужно не меньше 12 символов')
DB.parent.mkdir(exist_ok=True)
now = datetime.now(timezone.utc).isoformat()
with sqlite3.connect(DB) as conn:
    conn.execute('''CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE NOT NULL,
        password_hash TEXT NOT NULL, must_change INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    existing = conn.execute('SELECT id FROM admins WHERE username=? COLLATE NOCASE', (username,)).fetchone()
    if existing:
        conn.execute('UPDATE admins SET password_hash=?,must_change=1,updated_at=? WHERE id=?',
                     (generate_password_hash(password), now, existing[0]))
        action = 'обновлена'
    else:
        conn.execute('INSERT INTO admins(username,password_hash,must_change,created_at,updated_at) VALUES(?,?,?,?,?)',
                     (username, generate_password_hash(password), 1, now, now))
        action = 'создана'
print(f'Учётная запись {action}.')
print(f'Логин: {username}')
if args.generate:
    print(f'Временный пароль: {password}')
    print('Смените его после первого входа во вкладке «Настройки».')
