// Страница «Письмо»: форма -> POST /api/cover -> текст письма, что не закрыто, почему выбраны пункты.
const form = document.getElementById('cover-form');
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const SOURCES = { text: 'текст вставлен вручную', page: 'прочитана со страницы вакансии' };

// ?url=…&date=…&style=full&auto=1 — открыть с заполненной формой (кнопка из отчёта)
const params = new URLSearchParams(location.search);
if (params.get('url')) form.url.value = params.get('url');
if (params.get('style') === 'full') form.style.value = 'full';

async function build() {
  const status = $('cover-status');
  const button = form.querySelector('button[type=submit]');
  button.disabled = true;
  status.textContent = 'Собираю письмо…';
  const body = {
    url: form.url.value.trim(), text: form.text.value, company: form.company.value.trim(),
    title: form.title.value.trim(), style: form.style.value, date: params.get('date') || '',
  };
  try {
    const r = await fetch('/api/cover', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'не удалось');
    show(d);
    status.textContent = '';
  } catch (err) {
    status.textContent = '⚠️ ' + err.message;
  } finally {
    button.disabled = false;
  }
}

form.addEventListener('submit', e => { e.preventDefault(); build(); });
// смена стиля после первой сборки — сразу пересобрать
form.querySelectorAll('input[name=style]').forEach(r => r.addEventListener('change', () => {
  if (!$('cover-result').hidden) build();
}));

function show(d) {
  $('cover-result').hidden = false;
  $('r-title').textContent = `${d.company} — ${d.title}`;
  const source = SOURCES[d.source] || `из отчёта за ${d.source}`;
  $('r-meta').textContent = `Fit ${d.fit}% · ${d.style === 'full' ? 'полное' : 'короткое'} · вакансия ${source}`;
  const vac = form.url.value.trim();
  if (vac) $('r-meta').insertAdjacentHTML('beforeend', ` · <a href="${esc(vac)}" target="_blank" rel="noopener">вакансия ↗</a>`);
  $('r-text').value = d.text;
  $('r-file').href = d.file;
  $('r-missing').innerHTML = d.missing.length
    ? `<div class="missing">Нет в опыте, но есть в вакансии: ${d.missing.map(m => `<span class="chip">${esc(m)}</span>`).join(' ')}</div>`
    : '<div class="muted small">Все ключевые навыки вакансии в опыте есть.</div>';
  $('r-picks').innerHTML = d.picks.map(p => {
    const why = [
      p.techs.length ? `технологии: ${p.techs.join(', ')}` : '',
      p.topics.length ? `темы: ${p.topics.join(', ')}` : '',
      p.similarity ? `близость текста ${Math.round(p.similarity * 100)}%` : '',
    ].filter(Boolean).join(' · ') || 'добор до минимума';
    return `<li><span class="pick-sec">${esc(p.section)}</span><span class="pick-score">${p.score}</span>
      <div><div>${esc(p.text)}</div><div class="muted small">${esc(why)}</div></div></li>`;
  }).join('');
}

$('r-copy').addEventListener('click', async e => {
  await navigator.clipboard.writeText($('r-text').value);
  e.currentTarget.textContent = 'Скопировано ✓';
  setTimeout(() => { $('r-copy').textContent = 'Копировать'; }, 1500);
});

if (params.get('url') && params.get('auto')) form.requestSubmit();
