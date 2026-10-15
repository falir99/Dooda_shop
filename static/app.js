const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); tg.setHeaderColor?.('#0b0815'); tg.setBackgroundColor?.('#0b0815'); }
let items = [], category = 'Все', brand = 'all', openItem;
let cart;
try { cart = JSON.parse(localStorage.getItem('dooda_cart_v3') || '{}'); } catch { cart = {}; }
const $ = query => document.querySelector(query), $$ = query => document.querySelectorAll(query);
const money = value => value ? new Intl.NumberFormat('ru-RU').format(value / 100) + ' ₽' : 'Цена по запросу';
const cartLines = () => items.filter(item => cart[item.id] > 0).map(item => ({item, quantity:Math.min(cart[item.id], item.stock_quantity)})).filter(line => line.quantity > 0);

function saveCart() {
  for (const key of Object.keys(cart)) if (!Number.isInteger(cart[key]) || cart[key] < 1) delete cart[key];
  localStorage.setItem('dooda_cart_v3', JSON.stringify(cart));
  const count = Object.values(cart).reduce((sum, quantity) => sum + quantity, 0);
  $('#cart-badge').textContent = count; $('#cart-badge').hidden = count === 0; $('#cart-count-profile').textContent = count;
}
function switchPage(name) {
  if (name === 'checkout' && !cartLines().length) name = 'cart';
  $$('.page').forEach(node => node.classList.toggle('active', node.id === 'page-' + name));
  $$('.tab').forEach(node => node.classList.toggle('active', node.dataset.go === name));
  window.scrollTo({top:0, behavior:'instant'});
  if (name === 'cart') renderCart(); if (name === 'checkout') renderCheckout();
  tg?.HapticFeedback?.selectionChanged();
}
$$('[data-go]').forEach(button => button.addEventListener('click', () => switchPage(button.dataset.go)));
function addToCart(item, quantity = 1) {
  if (!item.stock_quantity) return;
  cart[item.id] = Math.min(item.stock_quantity, (cart[item.id] || 0) + quantity); saveCart(); renderCart();
  tg?.HapticFeedback?.notificationOccurred('success');
}

function productCard(item) {
  const product = document.createElement('article'); product.className = 'product';
  const art = document.createElement('div'); art.className = 'product-art ' + item.accent;
  const icon = document.createElement('span'); icon.textContent = item.icon; art.append(icon);
  if (item.images?.length) { const photo = document.createElement('img'); photo.src = item.images[0]; photo.alt = item.name; photo.loading = 'lazy'; art.append(photo); }
  if (item.tag) { const tag = document.createElement('span'); tag.className = 'item-tag'; tag.textContent = item.tag; art.append(tag); }
  const meta = document.createElement('div'); meta.className = 'product-meta'; const cat = document.createElement('small'); cat.textContent = [item.category, item.brand].filter(Boolean).join(' · ');
  const name = document.createElement('h3'); name.textContent = item.name; const price = document.createElement('div'); price.className = 'price'; price.textContent = money(item.price);
  if (item.old_price) { const old = document.createElement('del'); old.textContent = money(item.old_price); price.append(old); }
  const availability = document.createElement('small'); availability.className = item.stock_quantity ? 'available' : 'unavailable'; availability.textContent = item.stock_quantity ? `● В наличии: ${item.stock_quantity}` : '○ Нет в наличии';
  const actions = document.createElement('div'); actions.className = 'product-actions'; const more = document.createElement('button'); more.className = 'product-open'; more.textContent = 'Подробнее'; more.addEventListener('click', () => showDetails(item));
  const buy = document.createElement('button'); buy.className = 'product-buy'; buy.textContent = cart[item.id] ? `В корзине: ${cart[item.id]}` : 'В корзину'; buy.disabled = !item.stock_quantity; buy.addEventListener('click', () => { addToCart(item); renderAll(); });
  actions.append(more, buy); meta.append(cat, name, price, availability, actions); product.append(art, meta); return product;
}
function renderGrid(target, list) { $(target).replaceChildren(...list.map(productCard)); }
function filteredItems() {
  const query = ($('#search').value || '').trim().toLocaleLowerCase('ru');
  let result = items.filter(item => (category === 'Все' || item.category === category) && (brand === 'all' || item.brand === brand) && (!$('#stock-only').checked || item.stock_quantity > 0) && (item.name + ' ' + item.description + ' ' + item.tag + ' ' + item.brand).toLocaleLowerCase('ru').includes(query));
  const sort = $('#sort').value; if (sort === 'price-asc') result.sort((a,b) => (a.price || Infinity) - (b.price || Infinity)); if (sort === 'price-desc') result.sort((a,b) => b.price - a.price); if (sort === 'new') result.sort((a,b) => b.id - a.id); return result;
}
function renderAll() {
  const featured = items.filter(item => item.featured); renderGrid('#home-grid', (featured.length ? featured : items).slice(0, 6));
  const visible = filteredItems(); renderGrid('#catalog-grid', visible); $('#result-count').textContent = `Найдено: ${visible.length}`; $('#catalog-empty').hidden = visible.length !== 0; renderCart();
}
function renderFilters() {
  const types = ['Все', ...new Set(items.map(item => item.category))]; $('#chips').replaceChildren(...types.map(name => { const button = document.createElement('button'); button.className = 'chip' + (category === name ? ' active' : ''); button.textContent = name; button.onclick = () => { category = name; renderFilters(); renderAll(); }; return button; }));
  const availableBrands = [...new Set(items.filter(item => category === 'Все' || item.category === category).map(item => item.brand).filter(Boolean))].sort((a,b) => a.localeCompare(b, 'ru'));
  if (brand !== 'all' && !availableBrands.includes(brand)) brand = 'all'; const select = $('#brand-filter'); select.replaceChildren(); const all = document.createElement('option'); all.value = 'all'; all.textContent = 'Все марки'; select.append(all);
  for (const name of availableBrands) { const option = document.createElement('option'); option.value = name; option.textContent = name; select.append(option); } select.value = brand;
}
function changeQuantity(id, delta) { const item = items.find(entry => entry.id === id); if (!item) return; cart[id] = Math.max(0, Math.min(item.stock_quantity, (cart[id] || 0) + delta)); saveCart(); renderCart(); renderAll(); }
function renderCart() {
  const lines = cartLines(), root = $('#cart-list'); root.replaceChildren();
  for (const {item, quantity} of lines) { const row = document.createElement('article'); row.className = 'cart-row'; const visual = document.createElement('div'); visual.className = 'cart-visual ' + item.accent; if (item.images?.[0]) { const image = document.createElement('img'); image.src = item.images[0]; image.alt = ''; visual.append(image); } else visual.textContent = item.icon;
    const info = document.createElement('div'); info.className = 'cart-info'; const title = document.createElement('strong'); title.textContent = item.name; const meta = document.createElement('span'); meta.textContent = [item.brand, money(item.price)].filter(Boolean).join(' · '); const controls = document.createElement('div'); controls.className = 'qty-controls'; const minus = document.createElement('button'); minus.textContent = '−'; minus.onclick = () => changeQuantity(item.id, -1); const number = document.createElement('b'); number.textContent = quantity; const plus = document.createElement('button'); plus.textContent = '+'; plus.disabled = quantity >= item.stock_quantity; plus.onclick = () => changeQuantity(item.id, 1); const remove = document.createElement('button'); remove.className = 'cart-remove'; remove.textContent = 'Удалить'; remove.onclick = () => { delete cart[item.id]; saveCart(); renderCart(); renderAll(); }; controls.append(minus, number, plus, remove); info.append(title, meta, controls); const sum = document.createElement('strong'); sum.className = 'line-total'; sum.textContent = money(item.price * quantity); row.append(visual, info, sum); root.append(row); }
  const total = lines.reduce((sum, line) => sum + line.item.price * line.quantity, 0); $('#cart-total').textContent = money(total); $('#cart-empty').hidden = lines.length > 0; $('#cart-summary').hidden = lines.length === 0; saveCart();
}
function renderCheckout() {
  const lines = cartLines(); $('#checkout-items').replaceChildren(...lines.map(({item, quantity}) => { const row = document.createElement('div'); row.className = 'checkout-line'; const name = document.createElement('span'); name.textContent = `${item.name} × ${quantity}`; const total = document.createElement('strong'); total.textContent = money(item.price * quantity); row.append(name, total); return row; })); $('#checkout-total').textContent = money(lines.reduce((sum, line) => sum + line.item.price * line.quantity, 0));
}
function showDetails(item) { openItem = item; $('#detail-symbol').textContent = item.icon; $('#detail-name').textContent = item.name; $('#detail-category').textContent = [item.category, item.brand].filter(Boolean).join(' / ').toUpperCase(); $('#detail-description').textContent = item.description || 'Описание появится совсем скоро.'; $('#detail-price').textContent = money(item.price); $('#detail-stock').textContent = item.stock_quantity ? `В наличии: ${item.stock_quantity} шт.` : 'Нет в наличии'; const button = $('#detail-cart'); button.disabled = !item.stock_quantity; button.textContent = cart[item.id] ? `Добавить ещё · в корзине ${cart[item.id]}` : 'Добавить в корзину'; const gallery = $('#detail-gallery'); gallery.replaceChildren(...(item.images || []).map((src, index) => { const image = document.createElement('img'); image.src = src; image.alt = `${item.name}, фото ${index + 1}`; return image; })); gallery.hidden = !item.images?.length; $('#detail-symbol').hidden = !!item.images?.length; $('#details').showModal(); }
$('#close-details').onclick = () => $('#details').close(); $('#details').onclick = event => { if (event.target === $('#details')) $('#details').close(); }; $('#detail-cart').onclick = () => { if (!openItem) return; addToCart(openItem); $('#details').close(); renderAll(); };
$('#search').oninput = renderAll; $('#sort').onchange = renderAll; $('#stock-only').onchange = renderAll; $('#brand-filter').onchange = event => { brand = event.target.value; renderAll(); }; $('#go-checkout').onclick = () => switchPage('checkout');
$('#checkout-form').onsubmit = async event => { event.preventDefault(); const button = $('#place-order'), error = $('#order-error'); error.textContent = ''; const lines = cartLines(); if (!lines.length) return switchPage('cart'); button.disabled = true; button.textContent = 'Оформляем…';
  try { const response = await fetch('/api/orders', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({phone:$('#order-phone').value, telegram_username:$('#order-username').value, initData:tg?.initData || '', items:lines.map(line => ({id:line.item.id, quantity:line.quantity}))})}); const result = await response.json(); if (!response.ok) throw Error(result.error || 'Не удалось оформить заказ'); cart = {}; saveCart(); $('#success-order-id').textContent = result.order_id; event.target.reset(); switchPage('success'); await loadItems(); }
  catch (exception) { error.textContent = exception.message; await loadItems(); renderCheckout(); } finally { button.disabled = false; button.textContent = 'Оформить заказ'; }
};
async function loadItems() { try { const response = await fetch('/api/items'); if (!response.ok) throw Error(); items = await response.json(); for (const item of items) if ((cart[item.id] || 0) > item.stock_quantity) cart[item.id] = item.stock_quantity; $('#catalog-count').textContent = items.length; renderFilters(); renderAll(); saveCart(); } catch { $('#result-count').textContent = 'Не удалось загрузить каталог. Обновите страницу.'; } }
async function loadProfile() { if (!tg?.initData) return; try { const response = await fetch('/api/me', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({initData:tg.initData})}); if (!response.ok) return; const {user} = await response.json(); const name = [user.first_name,user.last_name].filter(Boolean).join(' ') || 'Пользователь Telegram'; $('#profile-name').textContent = name; $('#profile-hint').textContent = user.username ? '@' + user.username : 'Telegram ID: ' + user.id; $('#avatar').textContent = (user.first_name || 'T')[0].toUpperCase(); $('#profile-note').textContent = 'Telegram-профиль будет прикреплён к заказу автоматически.'; if (user.username) { $('#order-username').value = '@' + user.username; $('#order-username').readOnly = true; } } catch {} }
saveCart(); loadItems(); loadProfile();
