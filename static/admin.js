let csrf = '', categories = [], cards = [], editing = null, currentPassword = '';
const $ = selector => document.querySelector(selector);
const el = (tag, text) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; return node; };
const rubles = kopecks => new Intl.NumberFormat('ru-RU').format((kopecks || 0) / 100) + ' ₽';
function status(message, error = false) { const node = $('#status'); node.textContent = message; node.classList.toggle('error', error); node.style.display = 'block'; clearTimeout(status.timer); status.timer = setTimeout(() => node.style.display = 'none', 4500); }
async function api(url, options = {}) {
  const response = await fetch(url, {credentials:'same-origin', ...options, headers:{...(options.body instanceof FormData ? {} : {'Content-Type':'application/json'}), 'X-CSRF-Token':csrf, ...(options.headers || {})}});
  const type = response.headers.get('content-type') || ''; const result = type.includes('json') ? await response.json() : {};
  if (!response.ok) throw Error(result.error || 'Ошибка запроса'); return result;
}
function showAuth(authenticated) { $('#dashboard').hidden = !authenticated; $('#login').hidden = authenticated; }
function showView(name) {
  document.querySelectorAll('.view').forEach(panel => panel.hidden = panel.id !== 'view-' + name);
  document.querySelectorAll('.tabs button').forEach(button => button.classList.toggle('active', button.dataset.view === name));
  window.scrollTo({top:0, behavior:'smooth'});
}
document.querySelectorAll('.tabs button').forEach(button => button.onclick = () => showView(button.dataset.view));

async function init() {
  const session = await api('/api/admin/session'); csrf = session.csrf; showAuth(session.authenticated);
  if (session.authenticated) { $('#admin-name').textContent = session.username; $('#password-warning').hidden = !session.must_change; await reload(); }
}
async function login(body) {
  try {
    const result = await api('/api/admin/login', {method:'POST', body:JSON.stringify(body)}); csrf = result.csrf;
    currentPassword = body.password || ''; $('#admin-name').textContent = result.username; $('#password-warning').hidden = !result.must_change;
    showAuth(true); await reload(); if (result.must_change) showView('settings');
  } catch (error) { status(error.message, true); }
}
$('#login-form').onsubmit = event => { event.preventDefault(); login({username:$('#username').value, password:$('#password').value}); };
$('#telegram-login').onclick = () => { const initData = window.Telegram?.WebApp?.initData; if (!initData) return status('Откройте страницу внутри Telegram Mini App', true); login({initData}); };
$('#logout').onclick = async () => { await api('/api/admin/logout', {method:'POST', body:'{}'}); csrf = ''; currentPassword = ''; showAuth(false); };

async function reload() {
  const [categoryData, cardData, stats] = await Promise.all([api('/api/admin/categories'), api('/api/admin/cards'), api('/api/admin/stats')]);
  categories = categoryData; cards = cardData; renderStats(stats); renderCategories(); fillCategories(); renderCards();
}
function renderStats(data) {
  const entries = [['▤', data.cards, 'Всего товаров'], ['●', data.published, 'Опубликовано'], ['▦', data.categories, 'Категорий'], ['◌', data.out_of_stock, 'Нет в наличии'], ['▧', data.images, 'Фотографий']];
  $('#stats').replaceChildren(...entries.map(([icon, value, label]) => { const box = el('div'); box.className = 'stat'; box.innerHTML = `<i>${icon}</i><strong>${value}</strong><span>${label}</span>`; return box; }));
}
function renderCategories() {
  const root = $('#categories'); root.replaceChildren(); $('#category-count').textContent = '(' + categories.length + ')';
  for (const category of categories) {
    const row = el('div'); row.className = 'entry'; const info = el('div'); const title = el('div', category.name); title.className = 'entry-title';
    const sub = el('div', cards.filter(card => card.category === category.name).length + ' товаров'); sub.className = 'entry-sub'; info.append(title, sub);
    const actions = el('div'); actions.className = 'entry-actions'; const edit = el('button', 'Переименовать');
    edit.onclick = async () => { const name = prompt('Новое название категории:', category.name); if (name === null || name.trim() === category.name) return; try { await api('/api/admin/categories/' + category.id, {method:'PUT', body:JSON.stringify({name})}); await reload(); status('Название изменено'); } catch (error) { status(error.message, true); } };
    const remove = el('button', 'Удалить'); remove.className = 'secondary'; remove.onclick = async () => { if (!confirm(`Удалить категорию «${category.name}»?`)) return; try { await api('/api/admin/categories/' + category.id, {method:'DELETE'}); await reload(); status('Категория удалена'); } catch (error) { status(error.message, true); } };
    actions.append(edit, remove); row.append(info, actions); root.append(row);
  }
}
$('#category-form').onsubmit = async event => { event.preventDefault(); try { await api('/api/admin/categories', {method:'POST', body:JSON.stringify({name:$('#category-name').value})}); $('#category-name').value = ''; await reload(); status('Категория создана'); } catch (error) { status(error.message, true); } };
function fillCategories() {
  const select = $('#card-category'), filter = $('#card-filter'), previous = filter.value; select.replaceChildren(); filter.replaceChildren();
  const all = el('option', 'Все категории'); all.value = 'all'; filter.append(all);
  for (const category of categories) for (const target of [select, filter]) { const option = el('option', category.name); option.value = category.id; target.append(option); }
  filter.value = [...filter.options].some(option => option.value === previous) ? previous : 'all';
}
function resetForm() {
  editing = null; $('#card-heading').textContent = 'Новый товар'; $('#card-form').reset(); $('#card-icon').value = '✦'; $('#card-published').checked = true; $('#card-stock').checked = true;
  $('#editor-images').hidden = true; $('#edit-photos').replaceChildren(); $('#upload-photo').value = '';
}
function openEditor(card = null) {
  resetForm();
  if (card) {
    editing = card.id; $('#card-heading').textContent = 'Редактирование: ' + card.name; $('#card-name').value = card.name; $('#card-description').value = card.description;
    $('#card-published').checked = card.published; $('#card-stock').checked = card.in_stock; $('#card-featured').checked = card.featured;
    $('#card-category').value = categories.find(category => category.name === card.category)?.id || ''; $('#card-price').value = card.price / 100; $('#card-old-price').value = card.old_price / 100;
    $('#card-tag').value = card.tag; $('#card-icon').value = card.icon; $('#card-accent').value = card.accent; renderImages(card);
  }
  showView('editor');
}
$('#new-card').onclick = () => openEditor(); $('#quick-new-card').onclick = () => openEditor(); $('#quick-categories').onclick = () => showView('categories');
$('#back-cards').onclick = () => showView('cards'); $('#cancel-edit').onclick = () => showView('cards');
$('#card-form').onsubmit = async event => {
  event.preventDefault(); const payload = {name:$('#card-name').value, category_id:Number($('#card-category').value), description:$('#card-description').value,
    price:Math.round(Number($('#card-price').value || 0) * 100), old_price:Math.round(Number($('#card-old-price').value || 0) * 100), tag:$('#card-tag').value,
    icon:$('#card-icon').value, accent:$('#card-accent').value, published:$('#card-published').checked, in_stock:$('#card-stock').checked, featured:$('#card-featured').checked};
  try { await api('/api/admin/cards' + (editing ? '/' + editing : ''), {method:editing ? 'PUT' : 'POST', body:JSON.stringify(payload)}); resetForm(); await reload(); showView('cards'); status('Товар сохранён'); } catch (error) { status(error.message, true); }
};
['input', 'change'].forEach(event => $('#card-search').addEventListener(event, renderCards)); $('#card-filter').onchange = renderCards; $('#publication-filter').onchange = renderCards;
function renderCards() {
  const root = $('#cards'); root.replaceChildren(); const query = $('#card-search').value.trim().toLocaleLowerCase('ru'); const filter = $('#card-filter').value; const publication = $('#publication-filter').value;
  const result = cards.filter(card => card.name.toLocaleLowerCase('ru').includes(query) && (filter === 'all' || categories.find(category => category.name === card.category)?.id === Number(filter)) && (publication === 'all' || (publication === 'published') === card.published));
  $('#card-count').textContent = `(${result.length} / ${cards.length})`; $('#cards-empty').hidden = result.length > 0;
  for (const card of result) {
    const row = el('div'); row.className = 'entry card-entry'; const thumb = el('div', card.icon); thumb.className = 'admin-thumb ' + card.accent; if (card.images?.[0]) { const img = el('img'); img.src = card.images[0]; img.alt = ''; thumb.replaceChildren(img); }
    const info = el('div'); info.className = 'entry-info'; const name = el('div', card.name); name.className = 'entry-title'; const sub = el('div', `${card.category} · ${rubles(card.price)} · ${card.in_stock ? 'В наличии' : 'Нет в наличии'}`); sub.className = 'entry-sub'; info.append(name, sub);
    const actions = el('div'); actions.className = 'entry-actions'; const visibility = el('button', card.published ? 'Скрыть' : 'Опубликовать'); visibility.className = 'secondary'; visibility.onclick = async () => { try { await api(`/api/admin/cards/${card.id}/visibility`, {method:'PATCH', body:JSON.stringify({published:!card.published})}); await reload(); status(card.published ? 'Товар скрыт' : 'Товар опубликован'); } catch (error) { status(error.message, true); } };
    const edit = el('button', 'Изменить'); edit.onclick = () => openEditor(card); const duplicate = el('button', 'Копия'); duplicate.className = 'secondary'; duplicate.onclick = async () => { try { await api(`/api/admin/cards/${card.id}/duplicate`, {method:'POST', body:'{}'}); await reload(); status('Создана скрытая копия товара'); } catch (error) { status(error.message, true); } };
    const remove = el('button', 'Удалить'); remove.className = 'danger'; remove.onclick = async () => { if (!confirm(`Удалить «${card.name}» и все фотографии?`)) return; try { await api('/api/admin/cards/' + card.id, {method:'DELETE'}); await reload(); status('Товар удалён'); } catch (error) { status(error.message, true); } };
    actions.append(visibility, edit, duplicate, remove); row.append(thumb, info, actions); root.append(row);
  }
}
async function renderImages(card) {
  $('#editor-images').hidden = false; const root = $('#edit-photos'); root.replaceChildren();
  try { const photos = await api('/api/admin/cards/' + card.id + '/images'); for (const photo of photos) { const wrap = el('div'); wrap.className = 'photo-wrapper'; const image = el('img'); image.src = photo.url; image.alt = 'Изображение товара'; const remove = el('button', 'Удалить фото'); remove.className = 'secondary'; remove.onclick = async () => { if (!confirm('Удалить изображение?')) return; try { await api('/api/admin/images/' + photo.id, {method:'DELETE'}); await renderImages(card); } catch (error) { status(error.message, true); } }; wrap.append(image, remove); root.append(wrap); } } catch (error) { status(error.message, true); }
}
$('#upload-photo').onchange = async () => { if (!editing) return; for (const file of [...$('#upload-photo').files]) { const form = new FormData(); form.append('image', file); try { await api('/api/admin/cards/' + editing + '/images', {method:'POST', body:form}); } catch (error) { status(file.name + ': ' + error.message, true); break; } } $('#upload-photo').value = ''; await renderImages({id:editing}); await reload(); };
$('#password-form').onsubmit = async event => {
  event.preventDefault(); const current = $('#current-password').value || currentPassword; const next = $('#new-password').value;
  if (next !== $('#confirm-password').value) return status('Новые пароли не совпадают', true);
  try { await api('/api/admin/change-password', {method:'POST', body:JSON.stringify({current_password:current, new_password:next})}); currentPassword = next; event.target.reset(); $('#password-warning').hidden = true; status('Пароль успешно изменён'); } catch (error) { status(error.message, true); }
};
init().catch(error => status(error.message, true));
