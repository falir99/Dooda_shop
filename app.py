import os, json, hmac, hashlib, secrets, sqlite3, uuid, time, re, threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl
from urllib.request import Request, urlopen
from functools import wraps

from flask import Flask, render_template, request, jsonify, session, abort, send_from_directory, Response
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv('DOODA_DATA_DIR', ROOT / 'data'))
UPLOADS = DATA / 'uploads'
DATA.mkdir(parents=True, exist_ok=True)
UPLOADS.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv('DOODA_SECRET_KEY', secrets.token_hex(32))
app.config.update(SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SECURE=os.getenv('DOODA_LOCAL_HTTP') != '1',
                  SESSION_COOKIE_SAMESITE='Lax', MAX_CONTENT_LENGTH=4 * 1024 * 1024)
LEGACY_PASSWORD_HASH = os.getenv('DOODA_ADMIN_PASSWORD_HASH', '')
LEGACY_USERNAME = os.getenv('DOODA_ADMIN_USERNAME', 'admin').strip()[:64] or 'admin'
ADMIN_ID = os.getenv('DOODA_ADMIN_TELEGRAM_ID', '')
EXTENSIONS = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
LOGIN_ATTEMPTS = {}
ORDER_ATTEMPTS = {}


def db():
    conn = sqlite3.connect(DATA / 'catalog.sqlite3', timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def add_column(conn, table, definition):
    name = definition.split()[0]
    if name not in [column[1] for column in conn.execute(f'PRAGMA table_info({table})')]:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {definition}')


def init_db():
    with db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS categories (
          id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS cards (
          id INTEGER PRIMARY KEY, category_id INTEGER NOT NULL REFERENCES categories(id),
          name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', published INTEGER NOT NULL DEFAULT 1,
          icon TEXT DEFAULT '✦', accent TEXT DEFAULT 'violet', tag TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS images (
          id INTEGER PRIMARY KEY, card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
          filename TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admins (
          id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE NOT NULL,
          password_hash TEXT NOT NULL, must_change INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
          id INTEGER PRIMARY KEY, public_id TEXT UNIQUE NOT NULL, phone TEXT NOT NULL,
          telegram_id TEXT DEFAULT '', telegram_username TEXT DEFAULT '', customer_name TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'reserved', total INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_items (
          id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
          card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL, product_name TEXT NOT NULL,
          brand TEXT DEFAULT '', price INTEGER NOT NULL, quantity INTEGER NOT NULL, line_total INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS customers (
          id INTEGER PRIMARY KEY, phone TEXT UNIQUE NOT NULL, telegram_id TEXT DEFAULT '',
          telegram_username TEXT DEFAULT '', customer_name TEXT DEFAULT '', first_order_at TEXT NOT NULL,
          last_order_at TEXT NOT NULL, order_count INTEGER NOT NULL DEFAULT 0,
          reserved_total INTEGER NOT NULL DEFAULT 0, completed_total INTEGER NOT NULL DEFAULT 0
        );
        ''')
        had_stock_quantity = 'stock_quantity' in [column[1] for column in conn.execute('PRAGMA table_info(cards)')]
        add_column(conn, 'cards', 'in_stock INTEGER NOT NULL DEFAULT 1')
        add_column(conn, 'cards', "brand TEXT NOT NULL DEFAULT ''")
        add_column(conn, 'cards', 'stock_quantity INTEGER NOT NULL DEFAULT 0')
        add_column(conn, 'cards', 'price INTEGER NOT NULL DEFAULT 0')
        add_column(conn, 'cards', 'old_price INTEGER NOT NULL DEFAULT 0')
        add_column(conn, 'cards', 'featured INTEGER NOT NULL DEFAULT 0')
        add_column(conn, 'cards', "created_at TEXT NOT NULL DEFAULT ''")
        add_column(conn, 'cards', "updated_at TEXT NOT NULL DEFAULT ''")
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE cards SET created_at=? WHERE created_at=''", (now,))
        conn.execute("UPDATE cards SET updated_at=created_at WHERE updated_at=''", ())
        if not had_stock_quantity:
            conn.execute('UPDATE cards SET stock_quantity=CASE WHEN in_stock=1 THEN 1 ELSE 0 END')
        conn.execute('UPDATE cards SET in_stock=CASE WHEN stock_quantity>0 THEN 1 ELSE 0 END')
        if LEGACY_PASSWORD_HASH and not conn.execute('SELECT 1 FROM admins').fetchone():
            conn.execute('INSERT INTO admins(username,password_hash,must_change,created_at,updated_at) VALUES(?,?,?,?,?)',
                         (LEGACY_USERNAME, LEGACY_PASSWORD_HASH, 0, now, now))
        if not conn.execute('SELECT 1 FROM categories').fetchone():
            conn.executemany('INSERT INTO categories(name,sort_order) VALUES (?,?)',
                             [('Коллекции', 0), ('Аксессуары', 1), ('Другое', 2)])
            samples = [('Aurora', 'Коллекции', 'DOODA', '✦', 'pink', 349000, 'NEW', 8),
                       ('Orbit', 'Коллекции', 'DOODA', '◈', 'blue', 279000, 'HIT', 5),
                       ('Nova', 'Аксессуары', 'NOVA LAB', '✳', 'violet', 189000, '', 12),
                       ('Pixel', 'Аксессуары', 'PIXEL', '▣', 'mint', 99000, '', 7)]
            for idx, (name, category, brand, icon, accent, price, tag, stock) in enumerate(samples):
                cid = conn.execute('SELECT id FROM categories WHERE name=?', (category,)).fetchone()['id']
                conn.execute('''INSERT INTO cards(category_id,name,description,brand,icon,accent,price,tag,featured,
                                stock_quantity,in_stock,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                             (cid, name, 'Демонстрационная карточка каталога.', brand, icon, accent, price, tag,
                              1 if idx < 3 else 0, stock, 1, now, now))


init_db()


def telegram_user(init_data):
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not token or not isinstance(init_data, str) or not init_data:
        return None
    try:
        values = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
        expected = values.pop('hash', '')
        if not expected:
            return None
        now = datetime.now(timezone.utc).timestamp()
        if abs(now - int(values.get('auth_date', '0'))) > 3600:
            return None
        check = '\n'.join(f'{key}={values[key]}' for key in sorted(values))
        secret_key = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
        actual = hmac.new(secret_key, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(actual, expected):
            return None
        user = json.loads(values.get('user', '{}'))
        return user if isinstance(user, dict) and isinstance(user.get('id'), int) else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('admin_id'):
            return jsonify(error='Необходим вход администратора'), 401
        if request.method not in ('GET', 'HEAD') and not hmac.compare_digest(
                request.headers.get('X-CSRF-Token', ''), session.get('csrf', '-')):
            return jsonify(error='Сессия устарела. Обновите страницу.'), 403
        return fn(*args, **kwargs)
    return wrapper


def integer(value, default=0, minimum=0, maximum=100_000_000):
    try:
        return max(minimum, min(maximum, int(value)))
    except (ValueError, TypeError):
        return default


def as_card(row, photos):
    return dict(id=row['id'], name=row['name'], category=row['category'], description=row['description'],
                brand=row['brand'], icon=row['icon'], accent=row['accent'], tag=row['tag'], published=bool(row['published']),
                in_stock=row['stock_quantity'] > 0, stock_quantity=row['stock_quantity'],
                featured=bool(row['featured']), price=row['price'],
                old_price=row['old_price'], images=photos, updated_at=row['updated_at'])


def all_cards(published_only=True):
    with db() as conn:
        query = '''SELECT cards.*, categories.name AS category
                   FROM cards JOIN categories ON categories.id=cards.category_id'''
        if published_only:
            query += ' WHERE published=1'
        query += ' ORDER BY featured DESC, cards.id DESC'
        rows = conn.execute(query).fetchall()
        return [as_card(row, ['/media/' + image['filename'] for image in conn.execute(
            'SELECT filename FROM images WHERE card_id=? ORDER BY id', (row['id'],))]) for row in rows]


def normalize_phone(value):
    raw = str(value or '').strip()
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if not 10 <= len(digits) <= 15:
        return ''
    return '+' + digits


def clean_username(value):
    return re.sub(r'[^A-Za-z0-9_]', '', str(value or '').lstrip('@'))[:32]


def order_payload(conn, order_id):
    row = conn.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result['items'] = [dict(item) for item in conn.execute(
        'SELECT id,card_id,product_name,brand,price,quantity,line_total FROM order_items WHERE order_id=? ORDER BY id',
        (order_id,))]
    return result


def send_telegram_notification(order):
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.getenv('DOODA_ORDER_CHAT_ID', '')
    if not token or not chat_id:
        return
    lines = [f"🛒 Новый заказ {order['public_id']}", f"Телефон: {order['phone']}"]
    if order.get('telegram_username'):
        lines.append('Telegram: @' + order['telegram_username'])
    if order.get('customer_name'):
        lines.append('Клиент: ' + order['customer_name'])
    lines.append('')
    for item in order['items']:
        brand = f" ({item['brand']})" if item['brand'] else ''
        lines.append(f"• {item['product_name']}{brand} × {item['quantity']} — {item['line_total'] / 100:.2f} ₽")
    lines.append(f"\nИтого: {order['total'] / 100:.2f} ₽")
    body = json.dumps({'chat_id': chat_id, 'text': '\n'.join(lines)}).encode()
    try:
        request_obj = Request(f'https://api.telegram.org/bot{token}/sendMessage', data=body,
                              headers={'Content-Type': 'application/json'}, method='POST')
        with urlopen(request_obj, timeout=6):
            pass
    except Exception:
        app.logger.exception('Не удалось отправить уведомление о заказе в Telegram')


def notify_order_async(order):
    threading.Thread(target=send_telegram_notification, args=(order,), daemon=True).start()


@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    return response


@app.get('/')
def index(): return render_template('index.html')


@app.get('/admin')
def admin(): return render_template('admin.html')


@app.get('/api/items')
def items(): return jsonify(all_cards())


@app.post('/api/me')
def me():
    user = telegram_user((request.get_json(silent=True) or {}).get('initData', ''))
    if not user:
        return jsonify(error='Неверные данные Telegram'), 401
    return jsonify(user={key: user.get(key) for key in ['id', 'first_name', 'last_name', 'username']})


@app.post('/api/orders')
def create_order():
    body = request.get_json(silent=True) or {}
    client = request.headers.get('CF-Connecting-IP') or request.remote_addr or 'unknown'
    now_clock = time.monotonic()
    attempts = [stamp for stamp in ORDER_ATTEMPTS.get(client, []) if now_clock - stamp < 600]
    if len(attempts) >= 5:
        return jsonify(error='Слишком много заказов. Повторите через 10 минут.'), 429
    phone = normalize_phone(body.get('phone'))
    if not phone:
        return jsonify(error='Введите корректный номер телефона'), 400
    raw_items = body.get('items')
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > 30:
        return jsonify(error='Корзина пуста или содержит слишком много позиций'), 400
    quantities = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            return jsonify(error='Некорректный состав заказа'), 400
        card_id = integer(raw.get('id'), minimum=1)
        quantity = integer(raw.get('quantity'), minimum=1, maximum=99)
        quantities[card_id] = min(99, quantities.get(card_id, 0) + quantity)
    tg_user = telegram_user(body.get('initData', ''))
    username = clean_username(tg_user.get('username') if tg_user else body.get('telegram_username'))
    telegram_id = str(tg_user.get('id', '')) if tg_user else ''
    customer_name = ''
    if tg_user:
        customer_name = ' '.join(filter(None, [tg_user.get('first_name'), tg_user.get('last_name')]))[:120]
    now = datetime.now(timezone.utc).isoformat()
    public_id = 'DS-' + datetime.now(timezone.utc).strftime('%y%m%d') + '-' + secrets.token_hex(3).upper()
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        selected = []
        total = 0
        for card_id, quantity in quantities.items():
            row = conn.execute('SELECT id,name,brand,price,stock_quantity,published FROM cards WHERE id=?',
                               (card_id,)).fetchone()
            if not row or not row['published']:
                return jsonify(error='Один из товаров больше недоступен'), 409
            if row['stock_quantity'] < quantity:
                return jsonify(error=f"Недостаточно товара «{row['name']}». Доступно: {row['stock_quantity']}"), 409
            line_total = row['price'] * quantity
            total += line_total
            selected.append((row, quantity, line_total))
        result = conn.execute('''INSERT INTO orders(public_id,phone,telegram_id,telegram_username,customer_name,
                                 status,total,created_at,updated_at) VALUES(?,?,?,?,?,'reserved',?,?,?)''',
                              (public_id, phone, telegram_id, username, customer_name, total, now, now))
        order_id = result.lastrowid
        for row, quantity, line_total in selected:
            conn.execute('''INSERT INTO order_items(order_id,card_id,product_name,brand,price,quantity,line_total)
                            VALUES(?,?,?,?,?,?,?)''',
                         (order_id, row['id'], row['name'], row['brand'], row['price'], quantity, line_total))
            conn.execute('''UPDATE cards SET stock_quantity=stock_quantity-?,
                            in_stock=CASE WHEN stock_quantity-?>0 THEN 1 ELSE 0 END,updated_at=? WHERE id=?''',
                         (quantity, quantity, now, row['id']))
        conn.execute('''INSERT INTO customers(phone,telegram_id,telegram_username,customer_name,first_order_at,last_order_at,
                        order_count,reserved_total,completed_total) VALUES(?,?,?,?,?,?,1,?,0)
                        ON CONFLICT(phone) DO UPDATE SET telegram_id=excluded.telegram_id,
                        telegram_username=CASE WHEN excluded.telegram_username!='' THEN excluded.telegram_username ELSE customers.telegram_username END,
                        customer_name=CASE WHEN excluded.customer_name!='' THEN excluded.customer_name ELSE customers.customer_name END,
                        last_order_at=excluded.last_order_at,order_count=customers.order_count+1,
                        reserved_total=customers.reserved_total+excluded.reserved_total''',
                     (phone, telegram_id, username, customer_name, now, now, total))
        order = order_payload(conn, order_id)
    attempts.append(now_clock)
    ORDER_ATTEMPTS[client] = attempts
    notify_order_async(order)
    return jsonify(order_id=public_id, total=total, status='reserved'), 201


@app.get('/media/<name>')
def media(name):
    if '/' in name or name.startswith('.'):
        abort(404)
    return send_from_directory(UPLOADS, name)


@app.get('/api/admin/session')
def auth_status():
    admin_id = session.get('admin_id')
    if not admin_id:
        return jsonify(authenticated=False, csrf='')
    with db() as conn:
        row = conn.execute('SELECT username,must_change FROM admins WHERE id=?', (admin_id,)).fetchone()
    if not row:
        session.clear()
        return jsonify(authenticated=False, csrf='')
    return jsonify(authenticated=True, csrf=session.get('csrf', ''), username=row['username'],
                   must_change=bool(row['must_change']))


@app.post('/api/admin/login')
def login():
    payload = request.get_json(silent=True) or {}
    client = request.headers.get('CF-Connecting-IP') or request.remote_addr or 'unknown'
    now = time.monotonic()
    attempts = [stamp for stamp in LOGIN_ATTEMPTS.get(client, []) if now - stamp < 600]
    if len(attempts) >= 8:
        return jsonify(error='Слишком много попыток. Повторите через 10 минут.'), 429
    username = str(payload.get('username', '')).strip()[:64]
    password = payload.get('password')
    row = None
    if username and isinstance(password, str):
        with db() as conn:
            row = conn.execute('SELECT * FROM admins WHERE username=? COLLATE NOCASE', (username,)).fetchone()
        if not row or not check_password_hash(row['password_hash'], password):
            row = None
    if not row and ADMIN_ID and str(ADMIN_ID).isdigit():
        user = telegram_user(payload.get('initData', ''))
        if user and str(user['id']) == str(ADMIN_ID):
            with db() as conn:
                row = conn.execute('SELECT * FROM admins ORDER BY id LIMIT 1').fetchone()
    if not row:
        attempts.append(now)
        LOGIN_ATTEMPTS[client] = attempts
        return jsonify(error='Неверный логин или пароль'), 401
    LOGIN_ATTEMPTS.pop(client, None)
    session.clear()
    session['admin_id'] = row['id']
    session['csrf'] = secrets.token_urlsafe(32)
    return jsonify(ok=True, csrf=session['csrf'], username=row['username'], must_change=bool(row['must_change']))


@app.post('/api/admin/logout')
@require_admin
def logout():
    session.clear()
    return jsonify(ok=True)


@app.post('/api/admin/change-password')
@require_admin
def change_password():
    body = request.get_json(silent=True) or {}
    current, new_password = body.get('current_password'), body.get('new_password')
    if not isinstance(new_password, str) or len(new_password) < 12:
        return jsonify(error='Новый пароль должен содержать не меньше 12 символов'), 400
    if len(new_password) > 128:
        return jsonify(error='Пароль слишком длинный'), 400
    with db() as conn:
        row = conn.execute('SELECT password_hash FROM admins WHERE id=?', (session['admin_id'],)).fetchone()
        if not row or not isinstance(current, str) or not check_password_hash(row['password_hash'], current):
            return jsonify(error='Текущий пароль указан неверно'), 401
        conn.execute('UPDATE admins SET password_hash=?,must_change=0,updated_at=? WHERE id=?',
                     (generate_password_hash(new_password), datetime.now(timezone.utc).isoformat(), session['admin_id']))
    return jsonify(ok=True)


@app.get('/api/admin/stats')
@require_admin
def stats():
    with db() as conn:
        return jsonify(categories=conn.execute('SELECT COUNT(*) FROM categories').fetchone()[0],
                       cards=conn.execute('SELECT COUNT(*) FROM cards').fetchone()[0],
                       published=conn.execute('SELECT COUNT(*) FROM cards WHERE published=1').fetchone()[0],
                       out_of_stock=conn.execute('SELECT COUNT(*) FROM cards WHERE stock_quantity=0').fetchone()[0],
                       stock_units=conn.execute('SELECT COALESCE(SUM(stock_quantity),0) FROM cards').fetchone()[0],
                       reserved_orders=conn.execute("SELECT COUNT(*) FROM orders WHERE status='reserved'").fetchone()[0],
                       completed_orders=conn.execute("SELECT COUNT(*) FROM orders WHERE status='completed'").fetchone()[0],
                       customers=conn.execute('SELECT COUNT(*) FROM customers').fetchone()[0],
                       images=conn.execute('SELECT COUNT(*) FROM images').fetchone()[0])


@app.get('/api/admin/export')
@require_admin
def export_catalog():
    with db() as conn:
        payload = {'exported_at': datetime.now(timezone.utc).isoformat(),
                   'categories': [dict(row) for row in conn.execute('SELECT * FROM categories ORDER BY sort_order,id')],
                   'cards': all_cards(False),
                   'orders': [order_payload(conn, row['id']) for row in conn.execute('SELECT id FROM orders ORDER BY id')],
                   'customers': [dict(row) for row in conn.execute('SELECT * FROM customers ORDER BY last_order_at DESC')]}
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return Response(body, mimetype='application/json', headers={
        'Content-Disposition': f'attachment; filename=dooda-catalog-{datetime.now().date().isoformat()}.json'})


@app.get('/api/admin/orders')
@require_admin
def admin_orders():
    status_filter = request.args.get('status', '').strip()
    with db() as conn:
        query = 'SELECT id FROM orders'
        params = ()
        if status_filter in {'reserved', 'completed', 'cancelled'}:
            query += ' WHERE status=?'
            params = (status_filter,)
        query += " ORDER BY CASE status WHEN 'reserved' THEN 0 WHEN 'completed' THEN 1 ELSE 2 END, id DESC"
        return jsonify([order_payload(conn, row['id']) for row in conn.execute(query, params)])


@app.post('/api/admin/orders/<int:order_id>/status')
@require_admin
def update_order_status(order_id):
    new_status = str((request.get_json(silent=True) or {}).get('status', ''))
    if new_status not in {'completed', 'cancelled'}:
        return jsonify(error='Недопустимый статус заказа'), 400
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        order = conn.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
        if not order:
            return jsonify(error='Заказ не найден'), 404
        if order['status'] != 'reserved':
            return jsonify(error='Этот заказ уже обработан'), 409
        if new_status == 'cancelled':
            for item in conn.execute('SELECT card_id,quantity FROM order_items WHERE order_id=?', (order_id,)):
                if item['card_id']:
                    conn.execute('''UPDATE cards SET stock_quantity=stock_quantity+?,in_stock=1,updated_at=?
                                    WHERE id=?''', (item['quantity'], now, item['card_id']))
            conn.execute('UPDATE customers SET reserved_total=MAX(0,reserved_total-?) WHERE phone=?',
                         (order['total'], order['phone']))
        else:
            conn.execute('''UPDATE customers SET reserved_total=MAX(0,reserved_total-?),
                            completed_total=completed_total+? WHERE phone=?''',
                         (order['total'], order['total'], order['phone']))
        conn.execute('UPDATE orders SET status=?,updated_at=? WHERE id=?', (new_status, now, order_id))
        updated = order_payload(conn, order_id)
    return jsonify(updated)


@app.get('/api/admin/customers')
@require_admin
def admin_customers():
    with db() as conn:
        return jsonify([dict(row) for row in conn.execute(
            'SELECT * FROM customers ORDER BY last_order_at DESC,id DESC')])


@app.get('/api/admin/categories')
@require_admin
def admin_categories():
    with db() as conn:
        return jsonify([dict(row) for row in conn.execute('SELECT * FROM categories ORDER BY sort_order,id')])


@app.post('/api/admin/categories')
@require_admin
def create_category():
    name = str((request.get_json(silent=True) or {}).get('name', '')).strip()[:70]
    if not name:
        return jsonify(error='Укажите название'), 400
    try:
        with db() as conn:
            order = conn.execute('SELECT COALESCE(MAX(sort_order),-1)+1 FROM categories').fetchone()[0]
            result = conn.execute('INSERT INTO categories(name,sort_order) VALUES(?,?)', (name, order))
            return jsonify(id=result.lastrowid), 201
    except sqlite3.IntegrityError:
        return jsonify(error='Категория уже существует'), 409


@app.put('/api/admin/categories/<int:cid>')
@require_admin
def rename_category(cid):
    name = str((request.get_json(silent=True) or {}).get('name', '')).strip()[:70]
    if not name:
        return jsonify(error='Укажите название категории'), 400
    try:
        with db() as conn:
            result = conn.execute('UPDATE categories SET name=? WHERE id=?', (name, cid))
            if not result.rowcount:
                return jsonify(error='Категория не найдена'), 404
        return jsonify(ok=True)
    except sqlite3.IntegrityError:
        return jsonify(error='Категория с таким названием уже существует'), 409


@app.delete('/api/admin/categories/<int:cid>')
@require_admin
def delete_category(cid):
    with db() as conn:
        if conn.execute('SELECT 1 FROM cards WHERE category_id=?', (cid,)).fetchone():
            return jsonify(error='Сначала перенесите или удалите карточки этой категории'), 409
        conn.execute('DELETE FROM categories WHERE id=?', (cid,))
    return jsonify(ok=True)


@app.get('/api/admin/cards')
@require_admin
def admin_cards(): return jsonify(all_cards(False))


@app.post('/api/admin/cards')
@require_admin
def create_card(): return store_card(None)


@app.put('/api/admin/cards/<int:card_id>')
@require_admin
def edit_card(card_id): return store_card(card_id)


def store_card(card_id):
    body = request.get_json(silent=True) or {}
    name = str(body.get('name', '')).strip()[:120]
    description = str(body.get('description', '')).strip()[:4000]
    try:
        category_id = int(body.get('category_id'))
    except (ValueError, TypeError):
        category_id = -1
    if not name:
        return jsonify(error='Название обязательно'), 400
    icon = str(body.get('icon', '✦')).strip()[:4] or '✦'
    accent = str(body.get('accent', 'violet'))
    if accent not in {'pink', 'blue', 'violet', 'mint', 'orange', 'purple'}:
        accent = 'violet'
    tag = str(body.get('tag', '')).strip()[:24].upper()
    brand = str(body.get('brand', '')).strip()[:80]
    stock_quantity = integer(body.get('stock_quantity'), minimum=0, maximum=1_000_000)
    price, old_price = integer(body.get('price')), integer(body.get('old_price'))
    if old_price and old_price <= price:
        old_price = 0
    now = datetime.now(timezone.utc).isoformat()
    values = (category_id, name, description, brand, icon, accent, tag, price, old_price,
              1 if body.get('published', True) else 0, 1 if stock_quantity > 0 else 0, stock_quantity,
              1 if body.get('featured', False) else 0)
    with db() as conn:
        if not conn.execute('SELECT 1 FROM categories WHERE id=?', (category_id,)).fetchone():
            return jsonify(error='Категория не найдена'), 400
        if card_id is None:
            result = conn.execute('''INSERT INTO cards(category_id,name,description,brand,icon,accent,tag,price,old_price,
                                     published,in_stock,stock_quantity,featured,created_at,updated_at)
                                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                                  values + (now, now))
            card_id = result.lastrowid
        else:
            result = conn.execute('''UPDATE cards SET category_id=?,name=?,description=?,brand=?,icon=?,accent=?,tag=?,price=?,
                                     old_price=?,published=?,in_stock=?,stock_quantity=?,featured=?,updated_at=? WHERE id=?''',
                                  values + (now, card_id))
            if not result.rowcount:
                return jsonify(error='Карточка не найдена'), 404
    return jsonify(id=card_id)


@app.post('/api/admin/cards/<int:card_id>/duplicate')
@require_admin
def duplicate_card(card_id):
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        row = conn.execute('SELECT * FROM cards WHERE id=?', (card_id,)).fetchone()
        if not row:
            return jsonify(error='Карточка не найдена'), 404
        result = conn.execute('''INSERT INTO cards(category_id,name,description,brand,published,in_stock,stock_quantity,
                                 icon,accent,tag,price,old_price,featured,created_at,updated_at)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                              (row['category_id'], f"{row['name']} — копия", row['description'], row['brand'], 0,
                               row['in_stock'], row['stock_quantity'], row['icon'], row['accent'], row['tag'],
                               row['price'], row['old_price'], 0, now, now))
    return jsonify(id=result.lastrowid), 201


@app.patch('/api/admin/cards/<int:card_id>/visibility')
@require_admin
def toggle_visibility(card_id):
    published = bool((request.get_json(silent=True) or {}).get('published'))
    with db() as conn:
        result = conn.execute('UPDATE cards SET published=?,updated_at=? WHERE id=?',
                              (1 if published else 0, datetime.now(timezone.utc).isoformat(), card_id))
        if not result.rowcount:
            return jsonify(error='Карточка не найдена'), 404
    return jsonify(ok=True)


@app.delete('/api/admin/cards/<int:card_id>')
@require_admin
def delete_card(card_id):
    with db() as conn:
        filenames = [row['filename'] for row in conn.execute('SELECT filename FROM images WHERE card_id=?', (card_id,))]
        conn.execute('DELETE FROM cards WHERE id=?', (card_id,))
    for filename in filenames:
        (UPLOADS / filename).unlink(missing_ok=True)
    return jsonify(ok=True)


@app.get('/api/admin/cards/<int:card_id>/images')
@require_admin
def list_images(card_id):
    with db() as conn:
        return jsonify([dict(id=row['id'], url='/media/' + row['filename']) for row in conn.execute(
            'SELECT id,filename FROM images WHERE card_id=? ORDER BY id', (card_id,))])


@app.post('/api/admin/cards/<int:card_id>/images')
@require_admin
def upload_image(card_id):
    upload = request.files.get('image')
    if not upload:
        return jsonify(error='Выберите изображение'), 400
    extension = Path(secure_filename(upload.filename or '')).suffix.lower()
    if extension not in EXTENSIONS:
        return jsonify(error='Разрешены JPG, PNG и WEBP'), 400
    data = upload.read()
    if len(data) > 1_500_000:
        return jsonify(error='Изображение должно быть меньше 1,5 МБ'), 400
    valid = (extension == '.png' and data.startswith(b'\x89PNG\r\n\x1a\n') or
             extension in {'.jpg', '.jpeg'} and data.startswith(b'\xff\xd8\xff') or
             extension == '.webp' and data.startswith(b'RIFF') and data[8:12] == b'WEBP')
    if not valid:
        return jsonify(error='Формат файла не соответствует изображению'), 400
    with db() as conn:
        if not conn.execute('SELECT 1 FROM cards WHERE id=?', (card_id,)).fetchone():
            return jsonify(error='Карточка не найдена'), 404
        if conn.execute('SELECT COUNT(*) FROM images WHERE card_id=?', (card_id,)).fetchone()[0] >= 6:
            return jsonify(error='Максимум 6 изображений'), 400
        filename = uuid.uuid4().hex + extension
        (UPLOADS / filename).write_bytes(data)
        conn.execute('INSERT INTO images(card_id,filename) VALUES(?,?)', (card_id, filename))
    return jsonify(url='/media/' + filename), 201


@app.delete('/api/admin/images/<int:image_id>')
@require_admin
def delete_image(image_id):
    with db() as conn:
        row = conn.execute('SELECT filename FROM images WHERE id=?', (image_id,)).fetchone()
        if not row:
            return jsonify(error='Изображение не найдено'), 404
        conn.execute('DELETE FROM images WHERE id=?', (image_id,))
    (UPLOADS / row['filename']).unlink(missing_ok=True)
    return jsonify(ok=True)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '25330')), debug=False)
