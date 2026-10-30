(() => {
  const panel = document.getElementById('usagePanel');
  if (!panel) return;
  const el = id => document.getElementById(id);
  const number = n => Number(n || 0).toLocaleString('zh-CN');
  const day = value => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(value);
  el('usageEnd').value = day(new Date());
  el('usageStart').value = day(new Date(Date.now() - 6 * 86400000));
  const colors = ['#65d7e4', '#9d9aff', '#ffbd75', '#91dcb4', '#f394bb', '#93bfff', '#edda83', '#c4a3ef'];
  const svgNode = (tag, attrs, text) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    if (text !== undefined) node.textContent = text;
    return node;
  };
  function render(data) {
    const counts = Object.fromEntries(data.actions.map(a => [a.action, a.count]));
    el('usageCards').replaceChildren(...[
      ['累计记录', data.total], ['未区分角色', data.unknown_role_count], ['视频播放估算', counts.video_play], ['提问次数', counts.qa],
    ].map(([label, count]) => {
      const card = document.createElement('article'); card.className = 'usage-card';
      const name = document.createElement('span'); name.textContent = label;
      const value = document.createElement('strong'); value.textContent = number(count);
      card.append(name, value); return card;
    }));
    const cloud = el('usageCloud'); cloud.replaceChildren();
    const max = Math.max(1, ...data.actions.map(a => a.count));
    data.actions.forEach((action, i) => {
      const x = 120 + (i % 4) * 225, y = i < 4 ? 105 : 295;
      const radius = 43 + 39 * Math.sqrt(action.count / max);
      const group = svgNode('g', { class: `usage-bubble${action.count ? '' : ' empty'}` });
      group.append(svgNode('title', {}, `${action.label}：${number(action.count)} 次`));
      group.append(svgNode('circle', { cx: x, cy: y, r: radius, fill: colors[i] }));
      group.append(svgNode('text', { x, y: y - 4, 'text-anchor': 'middle', class: 'usage-bubble-label' }, action.label));
      group.append(svgNode('text', { x, y: y + 26, 'text-anchor': 'middle', class: 'usage-bubble-count' }, `${number(action.count)} 次`));
      cloud.append(group);
    });
    const peak = Math.max(1, ...data.trend.map(d => d.count));
    const step = Math.max(1, Math.ceil(data.trend.length / 12));
    el('usageTrend').replaceChildren(...data.trend.map((item, i) => {
      const column = document.createElement('div'); column.className = 'usage-trend-column';
      column.title = `${item.date}：${number(item.count)} 次`;
      const bar = document.createElement('div'); bar.className = 'usage-bar';
      bar.style.height = `${Math.max(2, item.count / peak * 120)}px`;
      const label = document.createElement('small'); label.textContent = i % step === 0 ? item.date.slice(5) : '';
      column.append(bar, label); return column;
    }));
    el('usageStatus').textContent = data.first_event_at
      ? `${data.start} 至 ${data.end} · 自 ${day(new Date(data.first_event_at))} 起按后台请求和上传累计，无需更新眼镜。`
      : '尚无统计记录。部署后按后台请求和成功上传累计，无需更新眼镜；不回填历史次数。';
  }
  async function load() {
    el('usageRefresh').disabled = true;
    try {
      const query = new URLSearchParams({ start: el('usageStart').value, end: el('usageEnd').value, role: el('usageRole').value });
      const response = await fetch(`/api/workspace/usage?${query}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '读取统计失败');
      render(data);
    } catch (error) { el('usageStatus').textContent = error.message; }
    finally { el('usageRefresh').disabled = false; }
  }
  el('usageRefresh').onclick = load;
  document.querySelector('[data-tab="usage"]').addEventListener('click', load);
  el('usageFullscreen').onclick = async () => {
    try { if (document.fullscreenElement) await document.exitFullscreen(); else await panel.requestFullscreen(); }
    catch { el('usageStatus').textContent = '当前浏览器不支持全屏，请按 F11 展示。'; }
  };
  document.addEventListener('fullscreenchange', () => { el('usageFullscreen').textContent = document.fullscreenElement ? '退出全屏' : '全屏展示'; });
  if (panel.classList.contains('active')) load();
})();
