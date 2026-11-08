/* SasView event dashboard: reads data/<event>/*.json written by collector/collect.py */
(() => {
  const params = new URLSearchParams(location.search);
  const TV = params.get('tv') === '1';
  const REFRESH_MIN = 10;
  const fmt = d3.format(',');
  const num = (v) => (v == null ? '—' : Number.isInteger(v) ? fmt(v) : d3.format(',.1f')(v));
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const $ = (id) => document.getElementById(id);
  const tip = $('tip');
  let state = null;

  if (TV) document.body.classList.add('tv');
  const tvLink = $('tv-toggle');
  const tvParams = new URLSearchParams(location.search);
  TV ? tvParams.delete('tv') : tvParams.set('tv', '1');
  tvLink.href = '?' + tvParams.toString();
  tvLink.textContent = TV ? 'Normal view' : 'Projector view';

  async function getJSON(path) {
    const r = await fetch(path + '?t=' + Date.now(), { cache: 'no-store' });
    if (!r.ok) throw new Error(path + ': ' + r.status);
    return r.json();
  }

  async function load() {
    const events = await getJSON('data/events.json');
    const shown = events.filter((e) => e.status !== 'upcoming');
    const picker = $('event-picker');
    picker.replaceChildren(...shown.map((e) => new Option(`${e.title} (${e.start.slice(0, 7)})`, e.id)));
    picker.hidden = shown.length < 2;
    const wanted = params.get('event');
    const ev = shown.find((e) => e.id === wanted) || shown.find((e) => e.status === 'live') || shown[0];
    if (!ev) {
      $('notice').hidden = false;
      $('notice').textContent = events.length ? 'No event has started yet.' : 'No events configured.';
      return;
    }
    picker.value = ev.id;
    picker.onchange = () => { const p = new URLSearchParams(location.search); p.set('event', picker.value); location.search = p.toString(); };
    const names = ['meta', 'summary', 'daily', 'by_repo', 'pr_closures', 'review_queue', 'first_timers'];
    const data = Object.fromEntries(await Promise.all(names.map(async (n) => [n, await getJSON(`data/${ev.id}/${n}.json`)])));
    state = data;
    render();
  }

  function render() {
    if (!state) return;
    const { meta, summary: s } = state;
    document.title = meta.title;
    $('title').textContent = meta.title;
    const sub = $('subtitle');
    sub.replaceChildren();
    const range = `${d3.utcFormat('%a %-d %b')(new Date(meta.start))} – ${d3.utcFormat('%a %-d %b %Y')(new Date(meta.end))}`;
    sub.append(`${range}${meta.subtitle ? ' · ' + meta.subtitle : ''} · ${meta.repos.map((r) => r.split('/')[1]).join(', ')}`);
    if (meta.link) { sub.append(' · '); const a = document.createElement('a'); a.href = meta.link; a.textContent = 'event page'; sub.append(a); }

    const lede = $('lede');
    lede.replaceChildren();
    const b = (v) => { const el = document.createElement('b'); el.textContent = num(v); return el; };
    lede.append(meta.final ? 'Over the event, ' : 'So far, ', b(s.contributors), ' people opened ', b(s.prs_opened),
      ' pull requests and merged ', b(s.prs_merged), ', about ', b(s.prs_merged_per_day), ' merges a day against a typical ',
      b(s.baseline_prs_merged_per_week), ' a week. ', b(s.awaiting_review), ' event PRs are waiting for a first review.');
    $('freshness').textContent = `${meta.final ? 'Final numbers' : 'Data as of ' + meta.generated_local + ' (' + meta.timezone + ')'} · ` +
      (meta.final ? 'event ended' : `updated hourly while the event runs; this page reloads every ${REFRESH_MIN} minutes`);

    const tiles = [
      ['PRs opened', s.prs_opened, `typical week: ${num(s.baseline_prs_opened_per_week)}`],
      ['PRs merged', s.prs_merged, `typical week: ${num(s.baseline_prs_merged_per_week)}`],
      ['Issues closed', s.issues_closed, `${num(s.issues_opened)} opened`],
      ['Commits', s.commits, 'incl. unmerged PR branches'],
      ['Reviews given', s.reviews, 'on event PRs'],
      ['First-time contributors', s.first_timers, 'first ever PR to these repos'],
    ];
    $('tiles').replaceChildren(...tiles.map(([label, v, note]) => {
      const t = document.createElement('div'); t.className = 'tile';
      const l = document.createElement('span'); l.className = 'muted'; l.textContent = label;
      const n = document.createElement('span'); n.className = 'num'; n.textContent = num(v);
      const m = document.createElement('span'); m.className = 'muted'; m.textContent = note;
      t.append(l, n, m); return t;
    }));

    $('by-pr').textContent = num(s.issues_closed_by_pr);
    $('by-hand').textContent = num(s.issues_closed_by_hand);
    $('ai').textContent = num(s.ai_assisted_prs);
    $('ttfr').textContent = num(s.median_hours_to_first_review);
    const split = $('split'); split.replaceChildren();
    const tot = (s.issues_closed_by_pr || 0) + (s.issues_closed_by_hand || 0);
    if (tot) [[s.issues_closed_by_pr, css('--s1')], [s.issues_closed_by_hand, css('--mutedbar')]].forEach(([v, c]) => {
      const sp = document.createElement('span'); sp.style.width = (100 * v / tot) + '%'; sp.style.background = c; split.append(sp);
    });

    drawCumulative();
    drawClosures();
    table('queue', state.review_queue, [['pr', 'url'], ['title'], ['author'], ['opened'], ['hours_waiting', null, 'n']], 'Nothing waiting for review.');
    table('firsts', state.first_timers, [['contributor'], ['pr', 'url'], ['title'], ['status']], 'None yet.');
    table('repos', state.by_repo, [['repo'], ['prs_opened', null, 'n'], ['prs_merged', null, 'n'], ['issues_opened', null, 'n'], ['issues_closed', null, 'n'], ['commits', null, 'n'], ['reviews', null, 'n']], '');
  }

  function table(id, rows, cols, emptyText) {
    const tb = $(id).querySelector('tbody');
    tb.replaceChildren();
    if (!rows.length && emptyText) {
      const tr = document.createElement('tr'); const td = document.createElement('td');
      td.colSpan = cols.length; td.className = 'empty'; td.textContent = emptyText; tr.append(td); tb.append(tr); return;
    }
    rows.forEach((r) => {
      const tr = document.createElement('tr');
      cols.forEach(([c, link, cls]) => {
        const td = document.createElement('td'); if (cls) td.className = cls;
        const text = typeof r[c] === 'number' ? num(r[c]) : (r[c] ?? '');
        if (link && r[link]) { const a = document.createElement('a'); a.href = r[link]; a.textContent = text; td.append(a); }
        else td.textContent = text;
        tr.append(td);
      });
      tb.append(tr);
    });
  }

  function measure(svg, texts, size) {
    const t = svg.append('text').attr('font-size', size);
    const w = d3.max(texts, (s) => { t.text(s); return t.node().getComputedTextLength(); }) || 0;
    t.remove(); return w;
  }

  function drawCumulative() {
    const el = $('cumulative'); el.replaceChildren();
    const rows = state.daily, days = state.meta.days;
    const fs = TV ? 15 : 12;
    const series = [
      { key: 'cum_prs_opened', name: 'PRs opened', color: css('--s1') },
      { key: 'cum_prs_merged', name: 'PRs merged', color: css('--s2') },
      { key: 'cum_issues_closed', name: 'Issues closed', color: css('--s3') },
    ];
    const W = el.clientWidth || 800, H = TV ? 360 : 280;
    const svg = d3.select(el).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`);
    if (!rows.length) return;
    const last = rows[rows.length - 1];
    const maxV = d3.max(rows, (r) => d3.max(series, (s) => r[s.key])) || 1;
    const y = d3.scaleLinear([0, maxV], [H - 30, 14]).nice();
    const ticks = y.ticks(5);
    const m = { l: measure(svg, ticks.map(fmt), fs) + 10, r: measure(svg, series.map((s) => `${s.name} ${fmt(last[s.key])}`), fs) + 16 };
    const x = d3.scalePoint(days, [m.l, Math.max(m.l + 80, W - m.r)]).padding(0.3);
    const dayLab = (d) => d3.utcFormat(days.length > 10 ? '%-d %b' : '%a %-d')(new Date(d));
    ticks.forEach((t) => {
      svg.append('line').attr('x1', m.l).attr('x2', W - m.r + 6).attr('y1', y(t)).attr('y2', y(t)).attr('stroke', css('--grid'));
      svg.append('text').attr('x', m.l - 6).attr('y', y(t)).attr('dy', '0.32em').attr('text-anchor', 'end').attr('font-size', fs).attr('fill', css('--muted')).text(fmt(t));
    });
    const step = Math.ceil(days.length / Math.max(1, Math.floor((W - m.l - m.r) / 70)));
    days.forEach((d, i) => { if (i % step === 0) svg.append('text').attr('x', x(d)).attr('y', H - 8).attr('text-anchor', 'middle').attr('font-size', fs).attr('fill', css('--muted')).text(dayLab(d)); });
    const ends = [];
    series.forEach((s) => {
      svg.append('path').datum(rows).attr('fill', 'none').attr('stroke', s.color).attr('stroke-width', TV ? 3 : 2)
        .attr('stroke-linecap', 'round').attr('stroke-linejoin', 'round')
        .attr('d', d3.line().x((r) => x(r.day)).y((r) => y(r[s.key])));
      svg.selectAll(null).data(rows).join('circle').attr('cx', (r) => x(r.day)).attr('cy', (r) => y(r[s.key])).attr('r', 3).attr('fill', s.color);
      ends.push({ s, y: y(last[s.key]) });
    });
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < fs + 2) ends[i].y = ends[i - 1].y + fs + 2;
    ends.forEach((e) => svg.append('text').attr('x', x(last.day) + 8).attr('y', e.y).attr('dy', '0.32em').attr('font-size', fs).attr('fill', e.s.color).text(`${e.s.name} ${fmt(last[e.s.key])}`));
    const rule = svg.append('line').attr('stroke', css('--muted')).attr('stroke-dasharray', '3 3').attr('y1', 14).attr('y2', H - 30).style('display', 'none');
    svg.append('rect').attr('x', m.l).attr('width', Math.max(0, W - m.l - m.r)).attr('height', H).attr('fill', 'transparent')
      .on('pointermove', (ev) => {
        const [px] = d3.pointer(ev);
        const r = d3.least(rows, (r) => Math.abs(x(r.day) - px));
        rule.style('display', null).attr('x1', x(r.day)).attr('x2', x(r.day));
        tip.replaceChildren(); tip.hidden = false;
        const h = document.createElement('div'); h.className = 'd'; h.textContent = d3.utcFormat('%A %-d %B')(new Date(r.day)); tip.append(h);
        series.forEach((s) => { const l = document.createElement('div'); const sw = document.createElement('span'); sw.className = 'sw'; sw.style.background = s.color; l.append(sw, `${s.name}: ${fmt(r[s.key])} (+${fmt(r[s.key.slice(4)])} that day)`); tip.append(l); });
        const tw = tip.offsetWidth;
        tip.style.left = Math.min(ev.clientX + 14, innerWidth - tw - 8) + 'px';
        tip.style.top = (ev.clientY + 14) + 'px';
      })
      .on('pointerleave', () => { tip.hidden = true; rule.style('display', 'none'); });
  }

  function drawClosures() {
    const el = $('closures'); el.replaceChildren();
    const rows = state.pr_closures, fs = TV ? 15 : 12;
    const W = el.clientWidth || 400, bh = TV ? 28 : 22, gap = 12, H = rows.length * (bh + gap);
    const svg = d3.select(el).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`);
    const lw = measure(svg, rows.map((r) => r.category), fs), nw = measure(svg, ['0000'], fs);
    const x = d3.scaleLinear([0, d3.max(rows, (r) => r.prs) || 1], [lw + 12, Math.max(lw + 40, W - nw - 10)]);
    const muted = new Set(['Older PR closed (cleanup)', 'New, closed unmerged']);
    rows.forEach((r, i) => {
      const yy = i * (bh + gap);
      svg.append('text').attr('x', lw).attr('y', yy + bh / 2).attr('dy', '0.32em').attr('text-anchor', 'end').attr('font-size', fs).attr('fill', css('--fg')).text(r.category);
      svg.append('rect').attr('x', x(0)).attr('y', yy).attr('height', bh).attr('rx', 3).attr('width', Math.max(0, x(r.prs) - x(0))).attr('fill', muted.has(r.category) ? css('--mutedbar') : css('--s1'));
      svg.append('text').attr('x', x(r.prs) + 6).attr('y', yy + bh / 2).attr('dy', '0.32em').attr('font-size', fs).attr('fill', css('--muted')).text(fmt(r.prs));
    });
  }

  let resizeTimer;
  addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(render, 150); });
  load().catch((e) => { $('notice').hidden = false; $('notice').textContent = 'Could not load data: ' + e.message; });
  setInterval(() => load().catch(() => {}), REFRESH_MIN * 60 * 1000);
})();
