const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); tg.setHeaderColor?.('#0b0815'); tg.setBackgroundColor?.('#0b0815'); }
let items = [], category = 'Все', openItem;
let favorites;
try { favorites = new Set(JSON.parse(localStorage.getItem('dooda_favorites_v2') || '[]').map(Number)); }
catch { favorites = new Set(); }
const $ = q => document.querySelector(q), $$ = q => document.querySelectorAll(q);
const money = value => value ? new Intl.NumberFormat('ru-RU').format(value / 100) + ' ₽' : 'Цена по запросу';

function saveFavorites() {
  try { localStorage.setItem('dooda_favorites_v2', JSON.stringify([...favorites])); } catch {}
  $('#saved-count').textContent = favorites.size;
}
function switchPage(name) {
  $$('.page').forEach(node => node.classList.toggle('active', node.id === 'page-' + name));
  $$('.tab').forEach(node => node.classList.toggle('active', node.dataset.go === name));
  window.scrollTo({top: 0, behavior: 'instant'});
  if (name === 'favorites') renderFavorites();
  tg?.HapticFeedback?.selectionChanged();
}
$$('[data-go]').forEach(button => button.addEventListener('click', () => switchPage(button.dataset.go)));

function productCard(item) {
  const product = document.createElement('article'); product.className = 'product';
  const art = document.createElement('div'); art.className = 'product-art ' + item.accent;
  const icon = document.createElement('span'); icon.textContent = item.icon; art.append(icon);
  if (item.images?.length) {
    const photo = document.createElement('img'); photo.src = item.images[0]; photo.alt = item.name;
    photo.loading = 'lazy'; art.append(photo);
  }
  if (item.tag) { const tag = document.createElement('span'); tag.className = 'item-tag'; tag.textContent = item.tag; art.append(tag); }
  const heart = document.createElement('button'); heart.className = 'favorite' + (favorites.has(item.id) ? ' saved' : '');
  heart.textContent = favorites.has(item.id) ? '♥' : '♡'; heart.setAttribute('aria-label', 'Добавить в избранное');
  heart.addEventListener('click', () => { toggleFavorite(item); renderAll(); }); art.append(heart);
  const meta = document.createElement('div'); meta.className = 'product-meta';
  const cat = document.createElement('small'); cat.textContent = item.category;
  const name = document.createElement('h3'); name.textContent = item.name;
  const price = document.createElement('div'); price.className = 'price'; price.textContent = money(item.price);
  if (item.old_price) { const old = document.createElement('del'); old.textContent = money(item.old_price); price.append(old); }
  const availability = document.createElement('small'); availability.className = item.in_stock ? 'available' : 'unavailable';
  availability.textContent = item.in_stock ? '● В наличии' : '○ Нет в наличии';
  const more = document.createElement('button'); more.className = 'product-open'; more.textContent = 'Подробнее ↗';
  more.addEventListener('click', () => showDetails(item));
  meta.append(cat, name, price, availability, more); product.append(art, meta); return product;
}
function toggleFavorite(item) {
  favorites.has(item.id) ? favorites.delete(item.id) : favorites.add(item.id);
  saveFavorites(); tg?.HapticFeedback?.impactOccurred('light');
}
function renderGrid(target, list) { $(target).replaceChildren(...list.map(productCard)); }
function filteredItems() {
  const query = ($('#search').value || '').trim().toLocaleLowerCase('ru');
  let result = items.filter(item => (category === 'Все' || item.category === category) &&
    (!$('#stock-only').checked || item.in_stock) &&
    (item.name + ' ' + item.description + ' ' + item.tag).toLocaleLowerCase('ru').includes(query));
  const sort = $('#sort').value;
  if (sort === 'price-asc') result.sort((a,b) => (a.price || Infinity) - (b.price || Infinity));
  if (sort === 'price-desc') result.sort((a,b) => b.price - a.price);
  if (sort === 'new') result.sort((a,b) => b.id - a.id);
  return result;
}
function renderAll() {
  const featured = items.filter(item => item.featured);
  renderGrid('#home-grid', (featured.length ? featured : items).slice(0, 6));
  const visible = filteredItems(); renderGrid('#catalog-grid', visible);
  $('#result-count').textContent = `Найдено: ${visible.length}`; $('#catalog-empty').hidden = visible.length !== 0;
  renderFavorites();
}
function renderFavorites() {
  const list = items.filter(item => favorites.has(item.id)); renderGrid('#favorites-grid', list);
  $('#favorites-empty').hidden = list.length !== 0;
}
function renderChips() {
  const group = ['Все', ...new Set(items.map(item => item.category))];
  $('#chips').replaceChildren(...group.map(name => {
    const button = document.createElement('button'); button.className = 'chip' + (category === name ? ' active' : '');
    button.textContent = name; button.addEventListener('click', () => { category = name; renderChips(); renderAll(); }); return button;
  }));
}
function showDetails(item) {
  openItem = item; $('#detail-symbol').textContent = item.icon; $('#detail-name').textContent = item.name;
  $('#detail-category').textContent = item.category + (item.in_stock ? ' / В НАЛИЧИИ' : ' / НЕТ В НАЛИЧИИ');
  $('#detail-description').textContent = item.description || 'Описание появится совсем скоро.';
  $('#detail-price').textContent = money(item.price); $('#detail-favorite').textContent = favorites.has(item.id) ? '♥ Убрать' : '♡ В избранное';
  const gallery = $('#detail-gallery'); gallery.replaceChildren(...(item.images || []).map((src, index) => {
    const image = document.createElement('img'); image.src = src; image.alt = `${item.name}, фото ${index + 1}`; return image;
  })); gallery.hidden = !item.images?.length; $('#detail-symbol').hidden = !!item.images?.length;
  $('#details').showModal();
}
$('#close-details').addEventListener('click', () => $('#details').close());
$('#details').addEventListener('click', event => { if (event.target === $('#details')) $('#details').close(); });
$('#detail-favorite').addEventListener('click', () => { if (!openItem) return; toggleFavorite(openItem); renderAll(); $('#detail-favorite').textContent = favorites.has(openItem.id) ? '♥ Убрать' : '♡ В избранное'; });
$('#detail-share').addEventListener('click', async () => {
  if (!openItem) return; const data = {title: openItem.name, text: `${openItem.name} — ${money(openItem.price)}`, url: location.href};
  try { if (navigator.share) await navigator.share(data); else { await navigator.clipboard.writeText(location.href); alert('Ссылка скопирована'); } } catch {}
});
['input', 'change'].forEach(event => $('#search').addEventListener(event, renderAll));
$('#sort').addEventListener('change', renderAll); $('#stock-only').addEventListener('change', renderAll);

async function loadItems() {
  try {
    const response = await fetch('/api/items'); if (!response.ok) throw Error('Bad response'); items = await response.json();
    $('#catalog-count').textContent = items.length; renderChips(); renderAll();
  } catch { $('#result-count').textContent = 'Не удалось загрузить каталог. Обновите страницу.'; }
}
async function loadProfile() {
  if (!tg?.initData) return;
  try {
    const response = await fetch('/api/me', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({initData:tg.initData})});
    if (!response.ok) return; const {user} = await response.json();
    const name = [user.first_name, user.last_name].filter(Boolean).join(' ') || 'Пользователь Telegram';
    $('#profile-name').textContent = name; $('#profile-hint').textContent = user.username ? '@' + user.username : 'Telegram ID: ' + user.id;
    $('#avatar').textContent = (user.first_name || 'T')[0].toUpperCase(); $('#profile-note').textContent = 'Профиль подтверждён Telegram.';
  } catch {}
}
saveFavorites(); loadItems(); loadProfile();
