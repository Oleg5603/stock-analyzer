const state = { companies: [] };
const $ = (selector) => document.querySelector(selector);

function flag(value) {
  if (value === true) return '<span class="mini-status yes">подтв.</span>';
  if (value === false) return '<span class="mini-status no">нет</span>';
  return '<span class="mini-status unknown">проверить</span>';
}

function announce(text) {
  const node = $('#import-message');
  node.textContent = text;
  node.classList.toggle('show', Boolean(text));
}

function renderCompanies() {
  const target = $('#companies');
  if (!state.companies.length) {
    target.innerHTML = '<tr><td colspan="8" class="empty">Загрузите CSV или обновите список с MOEX.</td></tr>';
    return;
  }
  target.innerHTML = state.companies.map((company) => `<tr>
    <td>${company.ticker}</td><td>${company.name}</td><td>${company.last_price == null ? '—' : company.last_price.toLocaleString('ru-RU')}</td><td>${company.sector}</td>
    <td>${company.category ?? '—'}</td>
    <td>${flag(company.is_bank ? company.bank_metrics_passed : company.fundamental_passed)}</td>
    <td>${flag(company.d1_confirmed && company.h4_confirmed && company.volume_profile_confirmed)}</td>
    <td><button class="table-action" data-ticker="${company.ticker}">Проверить</button></td>
  </tr>`).join('');
}

async function loadCompanies() {
  const response = await fetch('/api/companies');
  const data = await response.json();
  state.companies = data.items || [];
  renderCompanies();
}

function humanDecision(decision) {
  return { candidate: 'Аналитический кандидат', reject: 'Отклонить', manual_review: 'Нужна ручная проверка' }[decision] || 'Нет решения';
}

function humanStatus(status) {
  return { passed: 'ПРОЙДЕНО', failed: 'БЛОКЕР', warning: 'ВНИМАНИЕ', manual_review: 'ВРУЧНУЮ' }[status] || status;
}

function renderResult(data) {
  const range = data.position_min_pct == null ? 'не задан' : `${data.position_min_pct}–${data.position_max_pct}%`;
  $('#result').className = 'verdict ' + data.decision;
  $('#result').innerHTML = `<h3>${humanDecision(data.decision)}</h3>
    <p>${data.ticker} · достоверность: ${data.confidence === 'high' ? 'высокая' : data.confidence === 'medium' ? 'средняя' : 'требуется уточнение'}</p>
    <div class="metrics"><div class="metric"><b>${data.category ?? '—'}</b><span>категория</span></div><div class="metric"><b>${range}</b><span>диапазон веса</span></div><div class="metric"><b>${data.projected_sector_pct ?? '—'}%</b><span>сектор после добавления</span></div></div>
    <ul class="rules">${data.outcomes.map((rule) => `<li><span class="rule-status ${rule.status}">${rule.rule_id}<br>${humanStatus(rule.status)}</span><span>${rule.message}</span></li>`).join('')}</ul>`;
}

async function analyze(event) {
  event.preventDefault();
  const ticker = $('#ticker').value.trim().toUpperCase();
  if (!ticker) return;
  const sector = state.companies.find((item) => item.ticker === ticker)?.sector;
  const occupied = Number($('#sector-weight').value || 0);
  const proposed = $('#weight').value === '' ? undefined : Number($('#weight').value);
  const payload = { ticker, portfolio: sector && occupied ? [{ ticker: '__SECTOR__', sector, weight_pct: occupied }] : [] };
  if (proposed !== undefined) payload.proposed_position_pct = proposed;
  const response = await fetch('/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const result = await response.json();
  if (!response.ok) { announce(result.error || 'Не удалось выполнить проверку'); return; }
  renderResult(result);
}

$('#csv-file').addEventListener('change', async (event) => {
  const file = event.target.files[0]; if (!file) return;
  const response = await fetch('/api/import/companies.csv', { method: 'POST', body: await file.text() });
  const result = await response.json();
  if (!response.ok) { announce(result.error || 'Импорт не выполнен'); return; }
  announce(`Принято: ${result.accepted}. Отклонено: ${result.rejected}. Всего в реестре: ${result.total}.`);
  await loadCompanies();
});

$('#moex-import').addEventListener('click', async () => {
  const button = $('#moex-import');
  button.disabled = true; button.textContent = 'Загружаю…'; announce('Получаю список TQBR и цены из публичного MOEX ISS…');
  try {
    const response = await fetch('/api/import/moex', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Не удалось получить данные MOEX');
    announce(`${result.source}: добавлено ${result.accepted}. ${result.note}`);
    await loadCompanies();
  } catch (error) { announce(error.message); }
  finally { button.disabled = false; button.textContent = 'Обновить с MOEX'; }
});

$('#companies').addEventListener('click', (event) => {
  const button = event.target.closest('[data-ticker]'); if (!button) return;
  $('#ticker').value = button.dataset.ticker; $('#analysis').scrollIntoView({ behavior: 'smooth', block: 'start' });
});
$('#analysis-form').addEventListener('submit', analyze);
loadCompanies().catch(() => announce('Сервер недоступен. Запустите приложение командой из README.'));
