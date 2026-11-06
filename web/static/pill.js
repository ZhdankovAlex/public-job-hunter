// Плашка внизу левой панели на всех страницах: идёт ли прогон, этап и проценты; клик — на страницу пайплайна.
(async function pill() {
  const el = document.getElementById('run-pill');
  async function tick() {
    try {
      const d = await (await fetch('/api/progress')).json();
      if (d && d.status === 'running') {
        const stage = d.stages.find(s => s.key === d.stage);
        const pct = stage && stage.total ? ` ${Math.floor(100 * stage.done / stage.total)}%` : '';
        const text = `${d.script}.py: ${stage ? stage.title : ''}${pct}`;
        document.getElementById('run-pill-text').textContent = text;
        el.title = text + ' — открыть пайплайн';
        el.hidden = false;
      } else {
        el.hidden = true;
      }
    } catch (e) { el.hidden = true; }
  }
  el.addEventListener('click', () => location.href = '/pipeline');
  if (!document.body.classList.contains('no-pill')) { tick(); setInterval(tick, 3000); }
})();
