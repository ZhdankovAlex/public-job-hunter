// Страница «Резюме»: форма -> POST /api/cv -> результат с объяснением подбора.
const form = document.getElementById('cv-form');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const SOURCES = { text: 'текст вставлен вручную', page: 'прочитана со страницы вакансии' };

// ?url=…&one_page=1 — открыть страницу с уже заполненной формой (ссылка из отчёта)
const params = new URLSearchParams(location.search);
if (params.get('url')) form.url.value = params.get('url');
if (params.get('one_page')) form.one_page.checked = true;

form.addEventListener('submit', async e => {
  e.preventDefault();
  const status = document.getElementById('cv-status');
  const button = form.querySelector('button[type=submit]');
  button.disabled = true;
  status.textContent = 'Собираю: подбор пунктов, PDF' + (form.one_page.checked ? ', ужимаю до одной страницы' : '') + '…';
  const body = {
    url: form.url.value.trim(), text: form.text.value, company: form.company.value.trim(),
    title: form.title.value.trim(), one_page: form.one_page.checked, date: params.get('date') || '',
  };
  try {
    const r = await fetch('/api/cv', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'не удалось');
    show(d);
    status.textContent = '';
  } catch (err) {
    status.textContent = '⚠️ ' + err.message;
  } finally {
    button.disabled = false;
  }
});

function show(d) {
  document.getElementById('cv-result').hidden = false;
  document.getElementById('r-title').textContent = `${d.company} — ${d.title}`;
  const source = SOURCES[d.source] || `из отчёта за ${d.source}`;
  document.getElementById('r-meta').textContent =
    `Fit ${d.fit}% · ${d.pages} ${d.pages === 1 ? 'страница' : 'страницы'}${d.one_page && d.pages > 1 ? ' (не влезло даже после ужимания)' : ''} · вакансия ${source}`;
  const vac = form.url.value.trim();
  if (vac) document.getElementById('r-meta').insertAdjacentHTML('beforeend', ` · <a href="${esc(vac)}" target="_blank" rel="noopener">вакансия ↗</a>`);
  document.getElementById('r-pdf').href = d.pdf;
  document.getElementById('r-md').href = d.md;
  document.getElementById('r-missing').innerHTML = d.missing.length
    ? `<div class="missing">Нет в резюме, но есть в вакансии: ${d.missing.map(m => `<span class="chip">${esc(m)}</span>`).join(' ')}</div>`
    : '<div class="muted small">Все ключевые навыки вакансии в резюме есть.</div>';
  document.getElementById('r-picks').innerHTML = d.picks.map(p => {
    const why = [
      p.pin ? 'закреплён' : '',
      p.techs.length ? `технологии: ${p.techs.join(', ')}` : '',
      p.topics.length ? `темы: ${p.topics.join(', ')}` : '',
      p.similarity ? `близость текста ${Math.round(p.similarity * 100)}%` : '',
    ].filter(Boolean).join(' · ');
    const text = p.text.replace(/\*\*/g, '');
    return `<li><span class="pick-sec">${esc(p.section)}</span><span class="pick-score">${p.score}</span>
      <div><div>${esc(text.length > 170 ? text.slice(0, 170) + '…' : text)}</div><div class="muted small">${esc(why)}</div></div></li>`;
  }).join('');
}

if (params.get('url') && params.get('auto')) form.requestSubmit();
