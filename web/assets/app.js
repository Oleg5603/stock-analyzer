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

function fundamentalStatus(company) {
  const passed = company.is_bank ? company.bank_metrics_passed : company.fundamental_passed;
  if (passed !== null && passed !== undefined) return flag(passed);
  const coverage = company.annual_coverage;
  if (coverage) return `<span class="mini-status unknown">МСФО ${coverage.available}/${coverage.required}</span>`;
  return flag(null);
}

function technicalStatus(company) {
  if (!company.intraday) return flag(company.d1_confirmed && company.h4_confirmed && company.volume_profile_confirmed);
  const hint = company.intraday.h4_trend_confirmed ? 'H4: ориентир +' : 'H4: ориентир −';
  return `<span class="mini-status ${company.intraday.h4_trend_confirmed ? 'yes' : 'no'}">${hint}</span>`;
}

function verdictStatus(item) {
  const status = state.verdicts[item.ticker]?.status;
  if (status === 'consider') return '<span class="mini-status yes">можно рассматривать</span>';
  if (status === 'exclude_now') return '<span class="mini-status no">не рассматривать</span>';
  if (status === 'watch') return '<span class="mini-status unknown">нужна проверка</span>';
  if (status === 'unavailable') return '<span class="mini-status unknown">данные недоступны</span>';
  return '';
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
  const rank = (company) => ({
    consider: 0,
    watch: 1,
    undefined: 2,
    exclude_now: 3,
    unavailable: 4,
  })[String(state.verdicts[company.ticker]?.status)] ?? 2;
  const ordered = [...found].sort((left, right) => rank(left) - rank(right) || left.ticker.localeCompare(right.ticker));
  target.innerHTML = ordered.map((company) => `<tr>
    <td>${company.ticker}</td><td>${company.name}</td><td>${company.last_price == null ? '—' : company.last_price.toLocaleString('ru-RU')}</td><td>${company.sector}</td>
    <td>${company.category ?? '—'}</td>
    <td>${fundamentalStatus(company)}</td>
    <td>${shortDebtStatus(company.short_debt)}</td>
    <td>${technicalStatus(company)}</td>
    <td>${verdictStatus(company)}<button class="table-action" data-ticker="${company.ticker}">Проверить</button></td>
  </tr>`).join('');
}

async function loadCompanies() {
  const response = await fetch('/api/companies');
  const data = await response.json();
  state.companies = data.items || [];
  renderCompanies();
}

function restoreBatchScan(data) {
  for (const item of data.items || []) {
    state.verdicts[item.ticker] = { status: item.status === 'review' ? 'watch' : item.status };
  }
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

function intradayFacts(intraday) {
  if (!intraday) return '';
  const zones = (intraday.volume_zones || []).map((zone) => zone.toLocaleString('ru-RU')).join(' · ');
  return `<div class="technical-facts">
    <p><b>H4 и объёмный контекст · ориентир</b></p>
    <div class="metrics">
      <div class="metric"><b>${intraday.latest_close.toLocaleString('ru-RU')}</b><span>последнее закрытие H4</span></div>
      <div class="metric"><b>${intraday.sma5_h4.toLocaleString('ru-RU')}</b><span>SMA 5 H4</span></div>
      <div class="metric"><b>${intraday.h4_trend_confirmed ? 'да' : 'нет'}</b><span>краткий H4-тренд</span></div>
      <div class="metric"><b>${zones || '—'}</b><span>две объёмные зоны</span></div>
    </div>
    <p class="form-note">${intraday.source}. Это ориентир для открытия графика, а не автоматическое подтверждение зоны входа или Volume Profile.</p>
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

function targetEvidenceBlock(item) {
  const target = item?.target_evidence;
  if (!target?.price) return '<p class="form-note">Целевая цена не задана: потенциал не рассчитывается.</p>';
  if (!target.source || !target.as_of || !target.confirmed) return '<p class="form-note"><b>Нужна проверка цели:</b> добавьте источник, дату оценки и отметьте сверку.</p>';
  return `<p class="form-note"><b>Целевая цена:</b> ${target.price.toLocaleString('ru-RU')} · ${target.as_of} · источник: ${target.source}</p>`;
}

function manualTechnicalEvidenceBlock(item) {
  const evidence = item?.manual_technical_evidence;
  if (!evidence) return '';
  const zones = evidence.volume_zones || [];
  return `<div class="technical-facts"><p><b>Подтверждение на графике</b></p>
    <p class="form-note"><b>H4, зона входа:</b> ${evidence.h4_entry_zone || 'не указана'}.</p>
    <p class="form-note"><b>Две объёмные зоны:</b> ${zones.length === 2 ? zones.join(' · ') : 'нужно указать обе зоны'}.</p>
  </div>`;
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

function automaticCheckBlock(data) {
  const summary = data.summary;
  const steps = summary.manual_steps.map((step) => `<li>${step}</li>`).join('');
  const candidates = data.candidates || (data.scan?.items || []).filter((item) => item.status === 'review');
  const candidateRows = candidates.map((item) => {
    const h4 = item.h4_trend_hint ? 'есть' : 'ещё нет';
    const price = item.last_price == null ? '—' : item.last_price.toLocaleString('ru-RU');
    const target = item.technical_target == null ? '—' : item.technical_target.toLocaleString('ru-RU');
    const potential = item.technical_potential_pct == null ? '—' : `${item.technical_potential_pct.toLocaleString('ru-RU')}%`;
    const pending = ((item.pending || item.reasons || []).join('; ') || 'уточнить H4, объём и цель')
      .replaceAll('short_debt_ebitda', 'краткосрочный долг/EBITDA за два года');
    return `<li><b>${item.ticker}</b> · ${item.sector || 'сектор не указан'}<br>Цена: ${price}; ориентир цели: ${target}; потенциал: ${potential}<br>D1: пройден; H4: ${h4}. Осталось: ${pending}<br><button class="secondary-action" data-ticker="${item.ticker}">Открыть проверку</button></li>`;
  }).join('');
  $('#result').className = 'verdict manual_review';
  $('#result').innerHTML = `<h3>Автопроверка завершена</h3>
    <p>${data.note}</p>
    <div class="metrics">
      <div class="metric"><b>${summary.checked}</b><span>проверено акций</span></div>
      <div class="metric"><b>${summary.d1_and_base_data}</b><span>D1 и базовые данные</span></div>
      <div class="metric"><b>${summary.d1_blocker}</b><span>блокер D1</span></div>
      <div class="metric"><b>${summary.official_debt_found}</b><span>долг найден в отчётах</span></div>
      <div class="metric"><b>${summary.h4_hints_available ?? 0}</b><span>есть H4-ориентир</span></div>
    </div>
    <p><b>Можно рассматривать после проверки:</b></p>
    <ul class="classification-reasons">${candidateRows || '<li>Сейчас нет бумаг без блокера D1.</li>'}</ul>
    <p><b>Осталось вручную:</b></p><ul class="classification-reasons">${steps}</ul>
    <p class="form-note">Проверено: ${data.completed_at ? new Date(data.completed_at).toLocaleString('ru-RU') : 'в этом сеансе'}. В таблице первыми показаны акции без блокера D1. Откройте «Проверить» у нужной акции, чтобы увидеть её цепочку фактов.</p>`;
}

function classificationBlock(item) {
  if (!item) return '';
  const category = item.category == null ? '—' : item.category;
  const rows = (item.reasons || []).map((reason) => `<li>${reason}</li>`).join('');
  const series = Object.entries(item.annual_series || {}).map(([key, values]) => `<div class="metric"><b>${values.join(' → ')}</b><span>${key}</span></div>`).join('');
  return `<div class="technical-facts"><p><b>Категория · годовые МСФО</b> · статус: ${item.status}</p><div class="metrics"><div class="metric"><b>${category}</b><span>расчётная категория</span></div>${series}</div><ul class="classification-reasons">${rows}</ul><p class="form-note"><a href="${item.annual_source_url}" target="_blank" rel="noreferrer">Открыть годовой источник</a>. Ряды без нужного показателя не дополняются предположениями.</p></div>`;
}

function manualDebtBlock(item) {
  const values = item?.manual_short_debt_ebitda;
  if (!values) return '';
  return `<p class="form-note"><b>Вручную подтверждено:</b> краткосрочный долг/EBITDA ${values.join(' → ')}. Значения использованы только для текущего расчёта.</p>`;
}

function officialDebtRatioBlock(item) {
  const values = item?.official_short_debt_ebitda;
  if (!values) return '';
  return `<p class="form-note"><b>Подтверждено источником:</b> краткосрочный долг/EBITDA ${values.join(' → ')} за 2024–2025. Ряд взят только из одного отчёта с одинаковыми единицами измерения.</p>`;
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
    ${targetEvidenceBlock(data.classification)}
    ${technicalFacts(data.technical)}
    ${intradayFacts(data.intraday)}
    ${manualTechnicalEvidenceBlock(data.classification)}
    ${fundamentalFacts(data.fundamentals)}
    ${classificationBlock(data.classification)}
    ${manualDebtBlock(data.classification)}
    ${officialDebtRatioBlock(data.classification)}
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
  const targetSource = $('#target-source').value.trim();
  const targetAsOf = $('#target-as-of').value;
  if (targetSource) payload.target_source = targetSource;
  if (targetAsOf) payload.target_as_of = targetAsOf;
  if ($('#target-confirmed').checked) payload.target_confirmed = true;
  const shortDebt = $('#short-debt-ebitda').value.trim();
  if (shortDebt) payload.short_debt_ebitda = shortDebt;
  if ($('#h4-confirmed').checked) payload.h4_confirmed = true;
  if ($('#volume-confirmed').checked) payload.volume_profile_confirmed = true;
  const h4EntryZone = $('#h4-entry-zone').value.trim();
  const volumeZones = [$('#volume-zone-one').value.trim(), $('#volume-zone-two').value.trim()].filter(Boolean);
  if (h4EntryZone) payload.h4_entry_zone = h4EntryZone;
  if (volumeZones.length) payload.volume_zones_confirmed = volumeZones;
  const response = await fetch('/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const result = await response.json();
  if (!response.ok) { announce(result.error || 'Не удалось выполнить проверку'); return; }
  state.verdicts[ticker] = result.recommendation || {};
  renderCompanies();
  renderResult(result);
}

$('#fill-technical-hints').addEventListener('click', async () => {
  const ticker = $('#ticker').value.trim().toUpperCase();
  if (!ticker) { announce('Сначала укажите тикер.'); return; }
  const button = $('#fill-technical-hints');
  button.disabled = true; button.textContent = 'Получаю MOEX…';
  try {
    const response = await fetch('/api/analyze', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker, portfolio: [] }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Не удалось получить ориентиры MOEX');
    const intraday = result.intraday;
    const [lower, upper] = [intraday.sma5_h4, intraday.latest_close].sort((a, b) => a - b);
    $('#h4-entry-zone').value = `${lower.toFixed(2)}–${upper.toFixed(2)}`;
    $('#volume-zone-one').value = intraday.volume_zones[0] == null ? '' : `центр ${intraday.volume_zones[0].toFixed(2)}`;
    $('#volume-zone-two').value = intraday.volume_zones[1] == null ? '' : `центр ${intraday.volume_zones[1].toFixed(2)}`;
    $('#target-price').value = result.technical.recent_high_20;
    $('#target-source').value = 'MOEX ISS · технический ориентир: максимум 20 торговых дней';
    $('#target-as-of').value = result.technical.candle_date;
    const h4Passed = Boolean(intraday.h4_trend_confirmed);
    const volumePassed = intraday.volume_zones.length === 2;
    const targetPassed = result.technical.recent_high_20 > result.technical.close;
    $('#h4-confirmed').checked = h4Passed;
    $('#volume-confirmed').checked = volumePassed;
    $('#target-confirmed').checked = targetPassed;
    announce(`Ориентиры MOEX заполнены. Автоподтверждение: H4 — ${h4Passed ? 'да' : 'нет'}; две зоны — ${volumePassed ? 'да' : 'нет'}; цель выше цены — ${targetPassed ? 'да' : 'нет'}.`);
  } catch (error) { announce(error.message); }
  finally { button.disabled = false; button.textContent = 'Подставить ориентиры MOEX'; }
});

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
    restoreBatchScan(result); batchScanBlock(result); announce(`Готово: проверено ${result.items.length} голубых фишек. Сначала показаны те, где D1 и доступные фундаментальные данные не дали блокер.`);
  } catch (error) { announce(error.message); }
  finally { button.disabled = false; button.textContent = 'Проверить голубые фишки'; }
});

$('#automatic-check').addEventListener('click', async () => {
  const button = $('#automatic-check');
  button.disabled = true; button.textContent = 'Проверяю…';
  announce('Автопроверка: 1/2 D1 и годовые МСФО; затем 2/2 официальные страницы долга. Это может занять до 3 минут…');
  try {
    const response = await fetch('/api/check/moex-blue-chips', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Не удалось выполнить автопроверку');
    restoreBatchScan(result.scan); await loadCompanies(); automaticCheckBlock(result);
    const summary = result.summary;
    announce(`Готово: ${summary.checked} акций. D1 и базовые данные: ${summary.d1_and_base_data}; блокер D1: ${summary.d1_blocker}; строки долга найдены: ${summary.official_debt_found}.`);
  } catch (error) { announce(error.message); }
  finally { button.disabled = false; button.textContent = 'Автопроверка голубых фишек'; }
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
  $('#ticker').value = button.dataset.ticker;
  $('#analysis').scrollIntoView({ behavior: 'smooth', block: 'start' });
  $('#analysis-form').requestSubmit();
});
$('#result').addEventListener('click', async (event) => {
  const candidate = event.target.closest('[data-ticker]');
  if (candidate) {
    $('#ticker').value = candidate.dataset.ticker;
    $('#analysis').scrollIntoView({ behavior: 'smooth', block: 'start' });
    return;
  }
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
    const scanResponse = await fetch('/api/scan/moex-blue-chips');
    if (scanResponse.ok) restoreBatchScan(await scanResponse.json());
    const healthResponse = await fetch('/api/health');
    if (healthResponse.ok) {
      const health = await healthResponse.json();
      if (health.last_auto_check) automaticCheckBlock(health.last_auto_check);
    }
    if (!state.companies.length) {
      announce('Автозагрузка: получаю 15 голубых фишек и цены из публичного MOEX ISS…');
      const response = await fetch('/api/import/moex-blue-chips', { method: 'POST' });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Не удалось получить данные MOEX');
      announce(`MOEX ISS / MOEXBC: загружено ${result.accepted}. Полный список доступен по кнопке «Все TQBR».`);
      await loadCompanies();
    }
  } catch (error) { announce(error.message || 'Сервер недоступен. Запустите приложение командой из README.'); }
}

initialize();
