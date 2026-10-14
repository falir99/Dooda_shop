import os, json, hmac, hashlib, secrets, sqlite3, uuid, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl
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
        ''')
        add_column(conn, 'cards', 'in_stock INTEGER NOT NULL DEFAULT 1')
        add_column(conn, 'cards', 'price INTEGER NOT NULL DEFAULT 0')
        add_column(conn, 'cards', 'old_price INTEGER NOT NULL DEFAULT 0')
        add_column(conn, 'cards', 'featured INTEGER NOT NULL DEFAULT 0')
        add_column(conn, 'cards', "created_at TEXT NOT NULL DEFAULT ''")
        add_column(conn, 'cards', "updated_at TEXT NOT NULL DEFAULT ''")
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE cards SET created_at=? WHERE created_at=''", (now,))
        conn.execute("UPDATE cards SET updated_at=created_at WHERE updated_at=''", ())
        if LEGACY_PASSWORD_HASH and not conn.execute('SELECT 1 FROM admins').fetchone():
            conn.execute('INSERT INTO admins(username,password_hash,must_change,created_at,updated_at) VALUES(?,?,?,?,?)',
                         (LEGACY_USERNAME, LEGACY_PASSWORD_HASH, 0, now, now))
        if not conn.execute('SELECT 1 FROM categories').fetchone():
            conn.executemany('INSERT INTO categories(name,sort_order) VALUES (?,?)',
                             [('Коллекции', 0), ('Аксессуары', 1), ('Другое', 2)])
            samples = [('Aurora', 'Коллекции', '✦', 'pink', 349000, 'NEW'),
                       ('Orbit', 'Коллекции', '◈', 'blue', 279000, 'HIT'),
                       ('Nova', 'Аксессуары', '✳', 'violet', 189000, ''),
                       ('Pixel', 'Аксессуары', '▣', 'mint', 99000, '')]
            for idx, (name, category, icon, accent, price, tag) in enumerate(samples):
                cid = conn.execute('SELECT id FROM categories WHERE name=?', (category,)).fetchone()['id']
                conn.execute('''INSERT INTO cards(category_id,name,description,icon,accent,price,tag,featured,
                                created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                             (cid, name, 'Демонстрационная карточка каталога.', icon, accent, price, tag,
                              1 if idx < 3 else 0, now, now))


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
                icon=row['icon'], accent=row['accent'], tag=row['tag'], published=bool(row['published']),
                in_stock=bool(row['in_stock']), featured=bool(row['featured']), price=row['price'],
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
                       out_of_stock=conn.execute('SELECT COUNT(*) FROM cards WHERE in_stock=0').fetchone()[0],
                       images=conn.execute('SELECT COUNT(*) FROM images').fetchone()[0])


@app.get('/api/admin/export')
@require_admin
def export_catalog():
    with db() as conn:
        payload = {'exported_at': datetime.now(timezone.utc).isoformat(),
                   'categories': [dict(row) for row in conn.execute('SELECT * FROM categories ORDER BY sort_order,id')],
                   'cards': all_cards(False)}
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return Response(body, mimetype='application/json', headers={
        'Content-Disposition': f'attachment; filename=dooda-catalog-{datetime.now().date().isoformat()}.json'})


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
    price, old_price = integer(body.get('price')), integer(body.get('old_price'))
    if old_price and old_price <= price:
        old_price = 0
    now = datetime.now(timezone.utc).isoformat()
    values = (category_id, name, description, icon, accent, tag, price, old_price,
              1 if body.get('published', True) else 0, 1 if body.get('in_stock', True) else 0,
              1 if body.get('featured', False) else 0)
    with db() as conn:
        if not conn.execute('SELECT 1 FROM categories WHERE id=?', (category_id,)).fetchone():
            return jsonify(error='Категория не найдена'), 400
        if card_id is None:
            result = conn.execute('''INSERT INTO cards(category_id,name,description,icon,accent,tag,price,old_price,
                                     published,in_stock,featured,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                                  values + (now, now))
            card_id = result.lastrowid
        else:
            result = conn.execute('''UPDATE cards SET category_id=?,name=?,description=?,icon=?,accent=?,tag=?,price=?,
                                     old_price=?,published=?,in_stock=?,featured=?,updated_at=? WHERE id=?''',
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
        result = conn.execute('''INSERT INTO cards(category_id,name,description,published,in_stock,icon,accent,tag,
                                 price,old_price,featured,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                              (row['category_id'], f"{row['name']} — копия", row['description'], 0, row['in_stock'],
                               row['icon'], row['accent'], row['tag'], row['price'], row['old_price'], 0, now, now))
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
