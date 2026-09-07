const state = { companies: [], verdicts: {}, search: '' };
const $ = (selector) => document.querySelector(selector);

function flag(value) {
  if (value === true) return '<span class="mini-status yes">подтв.</span>';
  if (value === false) return '<span class="mini-status no">нет</span>';
  return '<span class="mini-status unknown">проверить</span>';
}

function shortDebtStatus(item) {
  if (!item) return '<span class="mini-status unknown">нет источника</span>';
  if (item.status === 'found') return '<span class="mini-status yes">найдено</span>';
  if (item.status === 'not_found') return '<span class="mini-status no">нет строки</span>';
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
    target.innerHTML = '<tr><td colspan="9" class="empty">Загрузите CSV или обновите список с MOEX.</td></tr>';
    return;
  }
  const query = state.search.trim().toLocaleUpperCase('ru-RU');
  const found = state.companies.filter((company) => !query || `${company.ticker} ${company.name}`.toLocaleUpperCase('ru-RU').includes(query));
  if (!found.length) {
    target.innerHTML = '<tr><td colspan="9" class="empty">По этому тикеру или названию ничего не найдено. Измените запрос или очистите поле поиска.</td></tr>';
    return;
  }
  const rank = (company) => state.verdicts[company.ticker]?.status === 'consider' ? 0 : 1;
  const ordered = [...found].sort((left, right) => rank(left) - rank(right) || left.ticker.localeCompare(right.ticker));
  target.innerHTML = ordered.map((company) => `<tr>
    <td>${company.ticker}</td><td>${company.name}</td><td>${company.last_price == null ? '—' : company.last_price.toLocaleString('ru-RU')}</td><td>${company.sector}</td>
    <td>${company.category ?? '—'}</td>
    <td>${flag(company.is_bank ? company.bank_metrics_passed : company.fundamental_passed)}</td>
    <td>${shortDebtStatus(company.short_debt)}</td>
    <td>${flag(company.d1_confirmed && company.h4_confirmed && company.volume_profile_confirmed)}</td>
    <td>${state.verdicts[company.ticker]?.status === 'consider' ? '<span class="mini-status yes">можно рассматривать</span>' : ''}<button class="table-action" data-ticker="${company.ticker}">Проверить</button></td>
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

function technicalFacts(technical) {
  if (!technical) return '';
  const yesNo = (value) => value ? 'да' : 'нет';
  return `<div class="technical-facts">
    <p><b>D1 · MOEX ISS</b> · свеча ${technical.candle_date}</p>
    <div class="metrics">
      <div class="metric"><b>${technical.close.toLocaleString('ru-RU')}</b><span>закрытие</span></div>
      <div class="metric"><b>${technical.sma50.toLocaleString('ru-RU')}</b><span>SMA 50</span></div>
      <div class="metric"><b>${yesNo(technical.rising_high)}</b><span>максимум 20д растёт</span></div>
      <div class="metric"><b>${technical.latest_volume.toLocaleString('ru-RU')}</b><span>объём дня</span></div>
    </div>
    <p class="form-note">Тренд D1: ${technical.price_above_sma50 ? 'цена выше SMA 50' : 'цена не выше SMA 50'}; ${technical.rising_high ? 'максимум последних 20 дней выше предыдущих 20' : 'максимум не растёт'}. H4 и Volume Profile не подменяются этим расчётом.</p>
  </div>`;
}

function fundamentalFacts(fundamentals) {
  if (!fundamentals) return '';
  const values = Object.entries(fundamentals.metrics || {}).map(([label, value]) => `<div class="metric"><b>${value}</b><span>${label}</span></div>`).join('');
  return `<div class="technical-facts">
    <p><b>Фундамент · Smart-Lab</b>${fundamentals.report_date ? ` · отчёт: ${fundamentals.report_date}` : ''}</p>
    ${values ? `<div class="metrics">${values}</div>` : ''}
    <p class="form-note">${fundamentals.note} <a href="${fundamentals.source_url}" target="_blank" rel="noreferrer">Открыть источник</a>.</p>
  </div>`;
}

function recommendationBlock(item) {
  if (!item) return '';
  const potential = item.potential_pct == null ? 'не задан' : `${item.potential_pct.toLocaleString('ru-RU')}%`;
  return `<div class="recommendation ${item.status}"><p class="eyebrow">ИТОГ СКАНЕРА</p><h4>${item.title}</h4><p>${item.message}</p><b>Потенциал по цели: ${potential}</b></div>`;
}

function batchScanBlock(data) {
  const rows = data.items.map((item) => {
    const foundation = item.fundamental_passed ? 'пройден' : item.base_series_ready ? 'базовые данные есть' : 'проверить';
    const facts = [`D1: ${item.d1_confirmed ? 'да' : 'нет'}`, `фундамент: ${foundation}`].join(' · ');
    return `<li><span class="rule-status ${item.status === 'review' ? 'passed' : item.status === 'exclude_now' ? 'failed' : 'warning'}">${item.ticker}<br>${item.title}</span><span><b>${item.name}</b><br>${facts}<br>${(item.reasons || []).join('; ')}</span></li>`;
  }).join('');
  $('#result').className = 'verdict manual_review';
  $('#result').innerHTML = `<h3>Предварительная очередь голубых фишек</h3><p>${data.source}</p><p>${data.note}</p><ul class="rules">${rows}</ul>`;
}

function debtBatchBlock(data) {
  const rows = data.items.map((item) => {
    const amount = item.amount == null ? 'не распознана' : item.amount.toLocaleString('ru-RU');
    const state = item.status === 'found' ? 'passed' : item.status === 'not_found' ? 'failed' : 'warning';
    const label = item.status === 'found' ? 'НАЙДЕНО' : item.status === 'not_found' ? 'НЕТ СТРОКИ' : 'ПРОВЕРИТЬ';
    return `<li><span class="rule-status ${state}">${item.ticker}<br>${label}</span><span><b>${item.issuer}</b><br>Сумма: ${amount}. ${item.note}<br><a href="${item.source_url || item.page_url}" target="_blank" rel="noreferrer">Открыть источник</a></span></li>`;
  }).join('');
  $('#result').className = 'verdict manual_review';
  $('#result').innerHTML = `<h3>Краткосрочный долг: доказательства</h3><p>${data.source} · ${data.year}</p><p>${data.note}</p><ul class="rules">${rows || '<li>Для загруженных компаний пока нет подтверждённых страниц раскрытия.</li>'}</ul>`;
}

function classificationBlock(item) {
  if (!item) return '';
  const category = item.category == null ? '—' : item.category;
  const rows = (item.reasons || []).map((reason) => `<li>${reason}</li>`).join('');
  const series = Object.entries(item.annual_series || {}).map(([key, values]) => `<div class="metric"><b>${values.join(' → ')}</b><span>${key}</span></div>`).join('');
  return `<div class="technical-facts"><p><b>Категория · годовые МСФО</b> · статус: ${item.status}</p><div class="metrics"><div class="metric"><b>${category}</b><span>расчётная категория</span></div>${series}</div><ul class="classification-reasons">${rows}</ul><p class="form-note"><a href="${item.annual_source_url}" target="_blank" rel="noreferrer">Открыть годовой источник</a>. Ряды без нужного показателя не дополняются предположениями.</p></div>`;
}

function officialDebtBlock(ticker, source) {
  if (!source) return '<div class="technical-facts"><p><b>Краткосрочный долг · официальный отчёт</b></p><p class="form-note">Для этого тикера ещё не добавлена подтверждённая страница эмитента.</p></div>';
  return `<div class="technical-facts"><p><b>Краткосрочный долг · официальный отчёт</b></p><p class="form-note">${source.issuer}: <a href="${source.page_url}" target="_blank" rel="noreferrer">страница раскрытия</a>. Автосбор не меняет категорию, пока единицы и отношение к EBITDA не подтверждены.</p><button class="secondary-action" data-official-debt="${ticker}">Загрузить из отчёта</button><p id="official-debt-result" class="form-note"></p></div>`;
}

function renderResult(data) {
  const range = data.position_min_pct == null ? 'не задан' : `${data.position_min_pct}–${data.position_max_pct}%`;
  $('#result').className = 'verdict ' + data.decision;
  $('#result').innerHTML = `<h3>${humanDecision(data.decision)}</h3>
    <p>${data.ticker} · достоверность: ${data.confidence === 'high' ? 'высокая' : data.confidence === 'medium' ? 'средняя' : 'требуется уточнение'}</p>
    <div class="metrics"><div class="metric"><b>${data.category ?? '—'}</b><span>категория</span></div><div class="metric"><b>${range}</b><span>диапазон веса</span></div><div class="metric"><b>${data.projected_sector_pct ?? '—'}%</b><span>сектор после добавления</span></div></div>
    ${recommendationBlock(data.recommendation)}
    ${technicalFacts(data.technical)}
    ${fundamentalFacts(data.fundamentals)}
    ${classificationBlock(data.classification)}
    ${officialDebtBlock(data.ticker, data.classification?.official_report_source)}
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
  const target = $('#target-price').value === '' ? undefined : Number($('#target-price').value);
  if (target !== undefined) payload.target_price = target;
  if ($('#h4-confirmed').checked) payload.h4_confirmed = true;
  if ($('#volume-confirmed').checked) payload.volume_profile_confirmed = true;
  const response = await fetch('/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const result = await response.json();
  if (!response.ok) { announce(result.error || 'Не удалось выполнить проверку'); return; }
  state.verdicts[ticker] = result.recommendation || {};
  renderCompanies();
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

$('#blue-chips-import').addEventListener('click', async () => {
  const button = $('#blue-chips-import');
  button.disabled = true; button.textContent = 'Загружаю…'; announce('Получаю текущий состав MOEXBC и цены из MOEX ISS…');
  try {
    const response = await fetch('/api/import/moex-blue-chips', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Не удалось получить состав голубых фишек');
    announce(`${result.source}: добавлено ${result.accepted}. ${result.note}`);
    await loadCompanies();
  } catch (error) { announce(error.message); }
  finally { button.disabled = false; button.textContent = 'Только голубые фишки'; }
});

$('#blue-chips-scan').addEventListener('click', async () => {
  const button = $('#blue-chips-scan');
  button.disabled = true; button.textContent = 'Проверяю…'; announce('Проверяю D1 и доступные годовые данные по голубым фишкам. Это может занять до минуты…');
  try {
    const response = await fetch('/api/scan/moex-blue-chips', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Не удалось выполнить пакетную проверку');
    for (const item of result.items) state.verdicts[item.ticker] = { status: item.status === 'review' ? 'watch' : item.status };
    renderCompanies(); batchScanBlock(result); announce(`Готово: проверено ${result.items.length} голубых фишек. Сначала показаны те, где D1 и доступные фундаментальные данные не дали блокер.`);
  } catch (error) { announce(error.message); }
  finally { button.disabled = false; button.textContent = 'Проверить голубые фишки'; }
});

$('#short-debt-scan').addEventListener('click', async () => {
  const button = $('#short-debt-scan');
  button.disabled = true; button.textContent = 'Читаю отчёты…'; announce('Читаю только подтверждённые официальные страницы эмитентов. PDF может потребовать ручной проверки…');
  try {
    const response = await fetch('/api/official-short-debt/batch', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Не удалось собрать краткосрочный долг');
    await loadCompanies(); debtBatchBlock(result); announce(`Готово: найдено значений ${result.found} из ${result.items.length}. Категории не менялись без проверки единиц и EBITDA.`);
  } catch (error) { announce(error.message); }
  finally { button.disabled = false; button.textContent = 'Собрать краткосрочный долг'; }
});

$('#companies').addEventListener('click', (event) => {
  const button = event.target.closest('[data-ticker]'); if (!button) return;
  $('#ticker').value = button.dataset.ticker; $('#analysis').scrollIntoView({ behavior: 'smooth', block: 'start' });
});
$('#result').addEventListener('click', async (event) => {
  const button = event.target.closest('[data-official-debt]'); if (!button) return;
  button.disabled = true; button.textContent = 'Читаю PDF…';
  const output = $('#official-debt-result'); output.textContent = 'Получаю PDF только с сайта эмитента…';
  try {
    const response = await fetch('/api/official-short-debt', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker: button.dataset.officialDebt }) });
    const item = await response.json();
    if (!response.ok) throw new Error(item.error || 'Не удалось прочитать отчёт');
    const amount = item.amount == null ? 'не распознано' : item.amount.toLocaleString('ru-RU');
    output.innerHTML = `<b>${item.status === 'found' ? 'Найдено' : 'Нужна проверка'}:</b> ${amount}. ${item.note}${item.source_url ? ` <a href="${item.source_url}" target="_blank" rel="noreferrer">Открыть PDF</a>.` : ''}`;
  } catch (error) { output.textContent = error.message; }
  finally { button.disabled = false; button.textContent = 'Загрузить из отчёта'; }
});
$('#analysis-form').addEventListener('submit', analyze);
$('#company-search').addEventListener('input', (event) => {
  state.search = event.target.value;
  renderCompanies();
});
async function initialize() {
  try {
    await loadCompanies();
    if (!state.companies.length) {
      announce('Автозагрузка: получаю список TQBR и цены из публичного MOEX ISS…');
      const response = await fetch('/api/import/moex', { method: 'POST' });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Не удалось получить данные MOEX');
      announce(`MOEX ISS: загружено ${result.accepted}. Категории и признаки методики требуют ручной проверки.`);
      await loadCompanies();
    }
  } catch (error) { announce(error.message || 'Сервер недоступен. Запустите приложение командой из README.'); }
}

initialize();
