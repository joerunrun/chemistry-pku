(() => {
  const cfg = window.WORKSPACE || {};
  const userApi = `/api/workspace/${cfg.role}`;
  const byId = id => document.getElementById(id);
  const esc = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  const size = value => { const n = Number(value || 0); return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${(n / 1024).toFixed(1)} KB` : `${n} B`; };
  const setStatus = (id, message, error = false) => { const el = byId(id); if (el) { el.textContent = message || ''; el.classList.toggle('error', error); } };
  async function fetchJson(url, options = {}) { const response = await fetch(url, options); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`); return data; }

  const panels = [...document.querySelectorAll('[data-panel]')];
  const tabButtons = [...document.querySelectorAll('[data-tab]')];
  const tabLoaders = {};
  function showTab(name, updateHash = true) {
    const target = panels.some(panel => panel.dataset.panel === name) ? name : panels[0]?.dataset.panel;
    if (!target) return;
    panels.forEach(panel => panel.classList.toggle('active', panel.dataset.panel === target));
    tabButtons.forEach(button => button.classList.toggle('active', button.dataset.tab === target));
    if (updateHash) history.replaceState(null, '', `#${target}`);
    if (tabLoaders[target]) tabLoaders[target]();
  }
  tabButtons.forEach(button => button.addEventListener('click', () => showTab(button.dataset.tab)));
  const aliases = { admin: 'admin-devices', 'teacher-tools': 'teacher-reports' };
  const requestedTab = location.hash.slice(1);
  showTab(aliases[requestedTab] || requestedTab, false);

  function localDate(value) { return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`; }
  function dataItem(item) {
    const title = item.item_type === 'photo' ? '照片' : '音视频记录';
    const claim = item.claim;
    const state = claim ? `已认领：${claim.identity_display} / 眼镜 ${claim.glass_number}` : '尚未认领';
    const links = item.item_type === 'photo'
      ? `<a class="button secondary" href="${esc(item.file_url)}" target="_blank">查看照片</a><a class="button secondary" href="${esc(item.download_url)}">下载</a>`
      : (item.files || []).map(file => `<a class="button secondary" href="${esc(file.file_url)}" target="_blank">打开 ${esc(file.filename)}</a>`).join('');
    return `<article class="item"><div class="item-head"><b>${title}</b><span class="badge">${esc(state)}</span></div><div>${esc(item.created_at || '')} · ${esc(item.experiment_type || '未标记实验')} · ${esc(item.device_id || '未标记设备')}</div><div class="item-links">${links}</div></article>`;
  }

  async function loadExperiments() {
    if (!byId('reportExperiment') && !byId('videoExperiment') && !byId('adminMediaExperiment') && !byId('teacherMediaExperiment')) return;
    try { const data = await fetchJson('/api/glasses/health'); const options = (data.experiments || []).map(item => `<option value="${esc(item.value)}">${esc(item.label || item.value)}</option>`).join(''); if (byId('reportExperiment')) byId('reportExperiment').innerHTML = `<option value="">全部实验</option>${options}`; if (byId('videoExperiment')) byId('videoExperiment').innerHTML = options; if (byId('adminMediaExperiment')) byId('adminMediaExperiment').innerHTML = `<option value="">全部实验</option>${options}`; if (byId('teacherMediaExperiment')) byId('teacherMediaExperiment').innerHTML = `<option value="">全部实验</option>${options}`; } catch (error) { setStatus('reportStatus', error.message, true); }
  }

  // One date produces both the overview and the full cleaned account.
  if (cfg.identityId) {
    const today = () => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
    let month = today().slice(0, 7), days = [], activeDate = '', busy = false;
    function renderCalendar() {
      const [year, number] = month.split('-').map(Number);
      const leading = (new Date(year, number - 1, 1).getDay() + 6) % 7;
      byId('reportMonth').textContent = `${year}年${number}月`;
      byId('reportCalendar').innerHTML = Array.from({ length: 42 }, (_, i) => {
        const date = new Date(year, number - 1, i - leading + 1), key = localDate(date);
        const item = days.find(day => day.date === key);
        const available = item && (item.record_count > 0 || item.status === 'succeeded');
        const status = item?.status === 'succeeded' ? '已生成' : ['queued', 'running'].includes(item?.status) ? '生成中' : item?.record_count ? `${item.record_count}条可用` : item?.pending_count ? '等待转写' : '';
        return `<button class="calendar-cell ${available ? 'available' : ''} ${date.getMonth() === number - 1 ? '' : 'other-month'} ${key === today() ? 'today' : ''}" data-report-date="${key}" ${!available || busy ? 'disabled' : ''}><b>${date.getDate()}</b><small>${status}</small></button>`;
      }).join('');
    }
    async function loadCalendar() {
      try { const data = await fetchJson(`${userApi}/report-calendar?month=${month}`); days = data.days || []; renderCalendar(); }
      catch (error) { setStatus('reportStatus', error.message, true); }
    }
    function showReport(report) {
      if (report.status !== 'succeeded') return;
      const link = `${userApi}/reports/${encodeURIComponent(report.report_id)}/download`;
      byId('reportResult').innerHTML = `<article class="item"><div class="section-head"><h3>${esc(report.title)}</h3><button id="updateDailyReport" class="secondary">更新总结</button></div><h3>当天概括</h3><div class="report-body">${esc(report.course_content)}</div><p><a class="button secondary" href="${link}?part=summary">下载概括</a></p><details><summary>查看完整整理</summary><div class="report-body">${esc(report.detailed_content)}</div><p><a class="button secondary" href="${link}?part=detail">下载完整整理</a></p></details>${(report.warnings || []).map(item => `<p class="muted">${esc(item)}</p>`).join('')}</article>`;
      byId('updateDailyReport').onclick = () => openDate(activeDate, true);
    }
    async function openDate(date, refresh = false) {
      if (busy) return;
      busy = true; activeDate = date; renderCalendar(); byId('reportResult').innerHTML = '';
      setStatus('reportStatus', `${date}：正在整理当天概括和完整记录……`);
      try {
        const data = await fetchJson(`${userApi}/reports`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'daily', date, refresh }) });
        let report = data.report;
        for (let i = 0; i < 300 && ['queued', 'running'].includes(report.status); i += 1) {
          await new Promise(resolve => setTimeout(resolve, 2000));
          report = (await fetchJson(`${userApi}/reports/${encodeURIComponent(report.report_id)}`)).report;
        }
        showReport(report);
        setStatus('reportStatus', report.status === 'succeeded' ? `${date}：两份内容已生成。` : ['queued', 'running'].includes(report.status) ? '仍在后台整理，稍后点击日期查看。' : (report.warnings || ['生成失败，请重试。']).join('；'), report.status === 'failed');
      } catch (error) { setStatus('reportStatus', error.message, true); }
      finally { busy = false; await loadCalendar(); }
    }
    byId('reportCalendar').onclick = event => { const button = event.target.closest('[data-report-date]'); if (button && !button.disabled) openDate(button.dataset.reportDate); };
    function moveMonth(delta) { const [year, number] = month.split('-').map(Number); month = localDate(new Date(year, number - 1 + delta, 1)).slice(0, 7); loadCalendar(); }
    byId('reportPrev').onclick = () => moveMonth(-1);
    byId('reportNext').onclick = () => moveMonth(1);
    byId('reportToday').onclick = () => { month = today().slice(0, 7); loadCalendar(); };
    tabLoaders['teacher-reports'] = loadCalendar;
    loadCalendar();
    fetchJson(`${userApi}/reports`).then(data => {
      byId('reportList').innerHTML = (data.reports || []).filter(report => report.kind !== 'daily').map(report => `<article class="item"><b>${esc(report.title)}</b><div class="report-body">${esc(report.course_content || (report.common_issues || []).map(item => `${item.problem}：${item.guidance}`).join('\n'))}</div>${report.status === 'succeeded' ? `<a href="${userApi}/reports/${encodeURIComponent(report.report_id)}/download">下载以前的报告</a>` : ''}</article>`).join('') || '还没有以前的报告。';
    }).catch(error => setStatus('reportStatus', error.message, true));
  }

  const weekdays = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日'];
  const weekdayName = value => weekdays[value] || '待补选星期';
  const weekdayOptions = (selected, blocked = []) => '<option value="">请选择星期</option>' + weekdays.map((name, i) => `<option value="${i}" ${selected !== null && selected !== undefined && selected !== '' && Number(selected) === i ? 'selected' : ''} ${blocked.includes(i) ? 'disabled' : ''}>${name}${blocked.includes(i) ? '（已占用）' : ''}</option>`).join('');
  let teacherGlasses = [];
  function updateTeacherWeekdays() {
    const select = byId('teacherBindingWeekday'), glass = teacherGlasses.find(item => item.glass_number === byId('teacherBindingGlass').value);
    select.innerHTML = weekdayOptions(select.value, glass?.occupied_weekdays || []);
  }
  let teacherBinding = null;
  async function loadTeacherBinding() {
    const data = await fetchJson(`${userApi}/binding`);
    teacherBinding = data.binding || null;
    teacherGlasses = data.glasses || [];
    const select = byId('teacherBindingGlass');
    if (select) {
      select.innerHTML = '<option value="">请选择眼镜</option>' + (data.glasses || []).map(glass => {
        const note = !glass.calibrated ? '（待校准）' : !glass.available ? '（已被占用）' : '';
        return `<option value="${esc(glass.glass_number)}" ${glass.current ? 'selected' : ''} ${!glass.calibrated || !glass.available ? 'disabled' : ''}>${esc(glass.glass_number)}号${note}</option>`;
      }).join('');
      byId('teacherBindingBadge').textContent = teacherBinding ? `${teacherBinding.glass_number}号 · ${weekdayName(teacherBinding.weekday)}` : '尚未选择';
      byId('teacherBindingWeekday').innerHTML = weekdayOptions(teacherBinding?.weekday);
      updateTeacherWeekdays();
      byId('teacherBindingHistory').innerHTML = (data.history || []).length ? (data.history || []).map(item => `<article class="item"><b>${esc(item.glass_number)}号 · ${weekdayName(item.weekday)}</b><div>${esc(item.started_at)} 至 ${esc(item.ended_at || '当前')}</div></article>`).join('') : '<span class="muted">还没有使用历史。</span>';
    }
    return data;
  }

  if (cfg.identityId) {
    byId('teacherBindingGlass').onchange = updateTeacherWeekdays;
    byId('saveTeacherBinding').onclick = async () => {
      const glassNumber = byId('teacherBindingGlass').value;
      if (!glassNumber) return setStatus('teacherBindingStatus', '请先选择一副已经校准的眼镜。', true);
      const weekday = byId('teacherBindingWeekday').value;
      if (weekday === '') return setStatus('teacherBindingStatus', '请选择固定使用的星期。', true);
      const wasUnbound = !teacherBinding || teacherBinding.weekday === null;
      try {
        const data = await fetchJson(`${userApi}/binding`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ glass_number: glassNumber, weekday: Number(weekday) }) });
        teacherBinding = data.binding;
        await loadTeacherBinding();
        setStatus('teacherBindingStatus', `${glassNumber}号眼镜 · ${weekdayName(Number(weekday))} 已保存。`);
        if (wasUnbound) showTab('teacher-media');
      } catch (error) { setStatus('teacherBindingStatus', error.message, true); }
    };
    byId('openTeacherMedia').onclick = () => showTab('teacher-media');
  }

  function teacherMediaCard(item) {
    const labels = { audio: '录音', video: '录像', photo: '照片' };
    const when = item.started_at || item.captured_at || item.created_at || '';
    const common = `<div>${esc(when)} · ${esc(item.glass_number ? `${item.glass_number}号眼镜` : '眼镜未校准')}${cfg.role === 'teacher' ? ` · ${esc(item.experiment_type || '未标记实验')}` : ''}</div><div class="muted">${esc(item.device_id || '')} · ${size(item.size)}</div>`;
    let preview = '';
    let links = '';
    if (item.item_type === 'photo') {
      preview = `<a href="${esc(item.file_url)}" target="_blank" rel="noopener"><img class="thumb" loading="lazy" decoding="async" src="${esc(item.thumbnail_url)}" alt="实验照片缩略图"></a>`;
      links = `<a class="button secondary" href="${esc(item.file_url)}" target="_blank" rel="noopener">查看原图</a><a class="button secondary" href="${esc(item.download_url)}">下载</a><button class="danger" data-delete-photo="${esc(item.photo_id)}" data-delete-scope="${cfg.role}">删除</button>`;
    } else {
      links = `<a class="button secondary" href="${esc(item.file_url)}" target="_blank" rel="noopener">打开${labels[item.item_type]}</a><a class="button secondary" href="${esc(item.download_url)}">下载</a><button class="danger" data-delete-record="${esc(item.record_id)}" data-delete-scope="${cfg.role}">删除整条记录</button>`;
      if (item.item_type === 'audio') {
        const statusText = item.analysis_status === 'pending' ? '正在转写' : item.analysis_status === 'failed' ? '转写失败' : '暂无转写';
        preview = item.transcript ? `<div class="transcript"><b>语音转写</b><br>${esc(item.transcript)}</div>` : `<div class="transcript muted">${statusText}</div>`;
      }
    }
    return `<article class="item"><div class="item-head"><b>${labels[item.item_type] || '资料'}</b><span class="badge">${esc(item.filename || '')}</span></div>${common}${preview}<div class="item-links">${links}</div></article>`;
  }

  let teacherMediaPage = 1;
  let teacherMediaPages = 1;
  let teacherMediaLoaded = false;
  let teacherMediaLoading = false;
  function teacherMediaQuery(page) {
    return new URLSearchParams({
      page: String(page), page_size: '20', media_type: byId('teacherMediaType').value,
      start_date: byId('teacherMediaStart').value, end_date: byId('teacherMediaEnd').value,
      glass_number: byId('teacherMediaGlass').value, experiment_type: byId('teacherMediaExperiment').value,
    });
  }
  async function loadTeacherMedia(page = teacherMediaPage) {
    if (!byId('teacherMediaList') || teacherMediaLoading) return;
    teacherMediaLoading = true;
    setStatus('teacherMediaStatus', '正在加载当前页……');
    try {
      const data = await fetchJson(`${userApi}/media?${teacherMediaQuery(page)}`);
      teacherMediaPage = Number(data.page || 1);
      teacherMediaPages = Number(data.pages || 1);
      const items = data.items || [];
      byId('teacherMediaList').innerHTML = items.length ? items.map(teacherMediaCard).join('') : '<span class="muted">当前条件下没有你的资料。</span>';
      byId('teacherMediaPage').textContent = `第 ${teacherMediaPage} / ${teacherMediaPages} 页`;
      byId('teacherMediaPrev').disabled = teacherMediaPage <= 1;
      byId('teacherMediaNext').disabled = teacherMediaPage >= teacherMediaPages;
      byId('teacherMediaBinding').textContent = data.binding ? `当前使用：${data.binding.glass_number}号眼镜；旧眼镜资料仍会保留。` : '尚未选择固定使用的眼镜和星期；已认领的旧资料仍可查看。';
      setStatus('teacherMediaStatus', `共 ${Number(data.total || 0)} 条资料，当前显示 ${items.length} 条。`);
      teacherMediaLoaded = true;
    } catch (error) { setStatus('teacherMediaStatus', error.message, true); }
    finally { teacherMediaLoading = false; }
  }
  if (cfg.identityId) {
    tabLoaders['teacher-media'] = () => { if (!teacherMediaLoaded) loadTeacherMedia(1); };
    byId('refreshTeacherMedia').onclick = () => loadTeacherMedia(teacherMediaPage);
    byId('applyTeacherMediaFilters').onclick = () => loadTeacherMedia(1);
    byId('clearTeacherMediaFilters').onclick = () => {
      byId('teacherMediaType').value = 'all';
      ['teacherMediaStart', 'teacherMediaEnd', 'teacherMediaGlass', 'teacherMediaExperiment'].forEach(id => { byId(id).value = ''; });
      loadTeacherMedia(1);
    };
    byId('teacherMediaPrev').onclick = () => { if (teacherMediaPage > 1) loadTeacherMedia(teacherMediaPage - 1); };
    byId('teacherMediaNext').onclick = () => { if (teacherMediaPage < teacherMediaPages) loadTeacherMedia(teacherMediaPage + 1); };
    loadTeacherBinding().then(() => { if (!requestedTab) showTab(teacherBinding && teacherBinding.weekday !== null ? 'teacher-media' : 'claims'); }).catch(error => setStatus('teacherBindingStatus', error.message, true));
    fetchJson('/api/auth/profile').then(data => { byId('profileData').textContent = data.profile ? JSON.stringify(data.profile, null, 2) : '尚未保存门户原始字段。'; }).catch(error => { byId('profileData').textContent = error.message; });
    if (cfg.role === 'teacher') tabLoaders['student-data'] = async () => { try { const data = await fetchJson('/api/workspace/data'); byId('studentData').innerHTML = (data.student_items || []).length ? data.student_items.map(dataItem).join('') : '<span class="muted">还没有学生数据。</span>'; } catch (error) { byId('studentData').textContent = error.message; } };
  }

  // Shared demo videos and feedback.
  async function loadVideos() { if (!byId('videoExperiment')?.value) return; try { const data = await fetchJson(`/admin/api/demo/videos?experiment_type=${encodeURIComponent(byId('videoExperiment').value)}`); byId('videoList').innerHTML = (data.videos || []).length ? data.videos.map(video => `<article class="item"><b>${esc(video.label || video.filename)}</b><div>${esc(video.experiment_label || video.experiment_type)} · ${size(video.size)}</div><div class="item-links"><a class="button secondary" href="/admin/api/demo/videos/${encodeURIComponent(video.id)}/download">下载</a><button class="danger" data-delete-demo="${esc(video.id)}">删除</button></div></article>`).join('') : '<span class="muted">这个实验还没有演示视频。</span>'; setStatus('videoStatus', `共 ${(data.videos || []).length} 个视频。`); } catch (error) { setStatus('videoStatus', error.message, true); } }
  async function loadFeedback() { if (!byId('feedbackList')) return; try { const data = await fetchJson('/admin/api/feedback'); byId('feedbackList').innerHTML = (data.feedback || []).length ? data.feedback.map(item => `<article class="item"><b>${esc(item.title)}</b><div>${esc(item.created_at || '')}</div><div>${esc(item.content || '')}</div><div class="item-links"><button class="danger" data-delete-feedback="${esc(item.feedback_id)}">删除</button></div></article>`).join('') : '<span class="muted">还没有意见。</span>'; } catch (error) { setStatus('feedbackStatus', error.message, true); } }
  if (byId('videoForm')) { byId('videoForm').onsubmit = async event => { event.preventDefault(); const file = byId('videoFile').files[0]; if (!file) return; const body = new FormData(); body.append('experiment_type', byId('videoExperiment').value); body.append('video', file); try { await fetchJson('/admin/api/demo/videos', { method: 'POST', body }); byId('videoFile').value = ''; await loadVideos(); } catch (error) { setStatus('videoStatus', error.message, true); } }; byId('refreshVideos').onclick = loadVideos; byId('videoExperiment').onchange = loadVideos; }
  if (byId('feedbackForm')) { byId('feedbackForm').onsubmit = async event => { event.preventDefault(); try { await fetchJson('/admin/api/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: byId('feedbackTitle').value.trim(), content: byId('feedbackContent').value.trim() }) }); byId('feedbackTitle').value = ''; byId('feedbackContent').value = ''; await loadFeedback(); } catch (error) { setStatus('feedbackStatus', error.message, true); } }; byId('refreshFeedback').onclick = loadFeedback; loadFeedback(); }

  // Administrator tools.
  let unassignedGroups = [];
  async function loadUnassigned() {
    const query = new URLSearchParams({ source_role: byId('unassignedRole').value, date: byId('unassignedDate').value, glass_number: byId('unassignedGlass').value });
    try {
      const data = await fetchJson(`/api/workspace/admin/unassigned?${query}`);
      unassignedGroups = data.groups || [];
      const options = '<option value="">选择接收人</option>' + (data.users || []).map(user => `<option value="${esc(user.identity_id)}">${esc(user.label)}</option>`).join('');
      byId('unassignedList').innerHTML = unassignedGroups.map((group, i) => `<article class="item"><b>${esc(group.date_label)} · ${esc(group.glass_number)}号 · ${group.count}条</b><div>${esc(group.started_at.replace('T', ' '))} 至 ${esc(group.ended_at.replace('T', ' '))}</div><div class="form-row"><select id="assignUser${i}">${options}</select><button data-assign="${i}">分配给此人</button></div></article>`).join('') || '当前条件下没有未归属资料。';
      setStatus('unassignedStatus', `共 ${unassignedGroups.length} 个时段。${data.unmapped_count ? `另有 ${data.unmapped_count} 条资料需要先校准眼镜编号。` : ''}`);
    } catch (error) { setStatus('unassignedStatus', error.message, true); }
  }
  async function loadAdminGlasses() { if (!byId('adminGlasses')) return; try { const data = await fetchJson('/admin/api/auth/glasses'); byId('adminGlasses').innerHTML = (data.glasses || []).map(glass => `<article class="mini-card"><b>${esc(glass.glass_number)} 号</b><input data-glass-device="${esc(glass.glass_number)}" value="${esc(glass.device_id || '')}" placeholder="稳定硬件 device_id"><button data-save-glass="${esc(glass.glass_number)}">保存校准</button></article>`).join(''); } catch (error) { setStatus('adminGlassStatus', error.message, true); } }
  async function loadAdminUsers() {
    if (!byId('adminUsers')) return;
    try {
      const data = await fetchJson('/admin/api/auth/users');
      const bindings = [...(data.teacher_bindings || []), ...(data.student_bindings || [])];
      const bindingByUser = Object.fromEntries(bindings.map(item => [`${item.role}:${item.identity_id}`, item]));
      const glassOptions = user => cfg.glasses.map(glass => `<option value="${esc(glass.glass_number)}" ${bindingByUser[`${user.role}:${user.identity_id}`]?.glass_number === glass.glass_number ? 'selected' : ''} ${!glass.device_id ? 'disabled' : ''}>${esc(glass.glass_number)}号${!glass.device_id ? '（待校准）' : ''}</option>`).join('');
      byId('adminUserStatus').textContent = `共 ${(data.users || []).length} 个身份。`;
      byId('adminUsers').innerHTML = `<table><thead><tr><th>身份</th><th>门户判断</th><th>当前角色</th><th>眼镜与星期</th><th>来源</th><th>原始字段</th></tr></thead><tbody>${(data.users || []).map(user => `<tr><td>${esc(user.identity_id.startsWith('demo-') ? user.identity_id : `${user.identity_id.slice(0, 2)}***${user.identity_id.slice(-2)}`)}</td><td>${user.portal_role === 'student' ? '学生' : user.portal_role === 'teacher' ? '教师' : '未知'}</td><td><select data-user-role="${esc(user.identity_id)}"><option value="student" ${user.role === 'student' ? 'selected' : ''}>学生</option><option value="teacher" ${user.role === 'teacher' ? 'selected' : ''}>教师</option><option value="portal">恢复门户判断</option></select></td><td><select data-user-glass="${esc(user.identity_id)}"><option value="">未绑定</option>${glassOptions(user)}</select><select data-user-weekday="${esc(user.identity_id)}">${weekdayOptions(bindingByUser[`${user.role}:${user.identity_id}`]?.weekday)}</select><button data-save-binding="${esc(user.identity_id)}">保存</button></td><td>${esc(user.role_source || '')}</td><td><details><summary>查看原始字段</summary><pre class="profile">${esc(user.profile ? JSON.stringify(user.profile, null, 2) : '尚未保存')}</pre></details></td></tr>`).join('')}</tbody></table>`;
    } catch (error) { byId('adminUserStatus').textContent = error.message; }
  }
  let adminMediaPage = 1;
  let adminMediaPages = 1;
  let adminMediaLoaded = false;
  let adminMediaLoading = false;
  function adminMediaCard(item) {
    const labels = { audio: '录音', video: '录像', photo: '照片' };
    const role = item.source_role === 'teacher' ? '教师' : '学生';
    const when = item.started_at || item.captured_at || item.created_at || '';
    const glass = item.glass_number ? `${item.glass_number}号眼镜` : '眼镜未校准';
    const common = `<div>${esc(when)} · ${esc(glass)} · ${esc(item.experiment_type || '未标记实验')}</div><div class="muted">${esc(item.device_id || '')} · ${size(item.size)}</div>`;
    let preview = '';
    let links = '';
    if (item.item_type === 'photo') {
      preview = `<a href="${esc(item.file_url)}" target="_blank" rel="noopener"><img class="thumb" loading="lazy" decoding="async" src="${esc(item.thumbnail_url)}" alt="实验照片缩略图"></a>`;
      links = `<a class="button secondary" href="${esc(item.file_url)}" target="_blank" rel="noopener">查看原图</a><a class="button secondary" href="${esc(item.download_url)}">下载</a><button class="danger" data-delete-photo="${esc(item.photo_id)}" data-delete-scope="${esc(item.source_role)}">删除</button>`;
    } else {
      const openLabel = item.item_type === 'video' ? '打开录像' : '打开录音';
      links = `<a class="button secondary" href="${esc(item.file_url)}" target="_blank" rel="noopener">${openLabel}</a><a class="button secondary" href="${esc(item.download_url)}">下载</a><button class="danger" data-delete-record="${esc(item.record_id)}" data-delete-scope="${esc(item.source_role)}">删除整条记录</button>`;
      if (item.item_type === 'audio') {
        const statusText = item.analysis_status === 'pending' ? '正在转写' : item.analysis_status === 'failed' ? '转写失败' : '暂无转写';
        preview = item.transcript ? `<div class="transcript"><b>语音转写</b><br>${esc(item.transcript)}</div>` : `<div class="transcript muted">${statusText}</div>`;
      }
    }
    return `<article class="item"><div class="item-head"><b>${labels[item.item_type] || '资料'} · ${role}</b><span class="badge">${esc(item.filename || '')}</span></div>${common}${preview}<div class="item-links">${links}</div></article>`;
  }
  function adminMediaQuery(page) {
    return new URLSearchParams({
      page: String(page), page_size: '20',
      source_role: byId('adminMediaRole').value,
      media_type: byId('adminMediaType').value,
      start_date: byId('adminMediaStart').value,
      end_date: byId('adminMediaEnd').value,
      glass_number: byId('adminMediaGlass').value,
      experiment_type: byId('adminMediaExperiment').value,
    });
  }
  async function loadAdminMedia(page = adminMediaPage) {
    if (!byId('adminMediaList') || adminMediaLoading) return;
    adminMediaLoading = true;
    setStatus('adminMediaStatus', '正在加载当前页……');
    try {
      const data = await fetchJson(`/api/workspace/admin/media?${adminMediaQuery(page)}`);
      adminMediaPage = Number(data.page || 1);
      adminMediaPages = Number(data.pages || 1);
      const items = data.items || [];
      byId('adminMediaList').innerHTML = items.length ? items.map(adminMediaCard).join('') : '<span class="muted">当前筛选条件下没有资料。</span>';
      byId('adminMediaPage').textContent = `第 ${adminMediaPage} / ${adminMediaPages} 页`;
      byId('adminMediaPrev').disabled = adminMediaPage <= 1;
      byId('adminMediaNext').disabled = adminMediaPage >= adminMediaPages;
      setStatus('adminMediaStatus', `共 ${Number(data.total || 0)} 条资料，当前显示 ${items.length} 条。`);
      adminMediaLoaded = true;
    } catch (error) {
      setStatus('adminMediaStatus', error.message, true);
    } finally {
      adminMediaLoading = false;
    }
  }
  async function loadTestIdentities() { if (!byId('testIdentities')) return; try { const data = await fetchJson('/api/workspace/admin/test-identities'); byId('testIdentities').innerHTML = (data.identities || []).map(item => `<article class="mini-card"><b>${esc(item.name)}</b><div>${esc(item.identity_id)} · ${item.current_role === 'teacher' ? '教师' : '学生'}</div><form action="/admin/test-identities/${encodeURIComponent(item.key)}/login" method="post"><button>以此身份进入</button></form></article>`).join(''); } catch (error) { byId('testIdentities').textContent = error.message; } }
  if (cfg.adminUnlocked) {
    tabLoaders['unassigned'] = loadUnassigned;
    byId('refreshUnassigned').onclick = loadUnassigned;
    byId('unassignedRole').onchange = loadUnassigned;
    if (requestedTab === 'unassigned') loadUnassigned();
    loadAdminGlasses();
    loadAdminUsers();
    loadTestIdentities();
    tabLoaders['admin-media'] = () => { if (!adminMediaLoaded) loadAdminMedia(1); };
    byId('refreshAdminMedia').onclick = () => loadAdminMedia(adminMediaPage);
    byId('applyAdminMediaFilters').onclick = () => loadAdminMedia(1);
    byId('clearAdminMediaFilters').onclick = () => {
      ['adminMediaRole', 'adminMediaType'].forEach(id => { byId(id).value = 'all'; });
      ['adminMediaStart', 'adminMediaEnd', 'adminMediaGlass', 'adminMediaExperiment'].forEach(id => { byId(id).value = ''; });
      loadAdminMedia(1);
    };
    byId('adminMediaPrev').onclick = () => { if (adminMediaPage > 1) loadAdminMedia(adminMediaPage - 1); };
    byId('adminMediaNext').onclick = () => { if (adminMediaPage < adminMediaPages) loadAdminMedia(adminMediaPage + 1); };
    if (document.querySelector('[data-panel="admin-media"].active')) loadAdminMedia(1);
  }

  document.addEventListener('change', async event => {
    const roleIdentity = event.target.dataset.userRole;
    if (roleIdentity) {
      try { await fetchJson(`/api/workspace/admin/users/${encodeURIComponent(roleIdentity)}/role`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role: event.target.value }) }); await loadAdminUsers(); } catch (error) { alert(error.message); }
      return;
    }
  });
  document.addEventListener('click', async event => {
    const assignIndex = event.target.dataset.assign;
    if (assignIndex !== undefined) {
      const identityId = byId(`assignUser${assignIndex}`).value;
      if (!identityId) return setStatus('unassignedStatus', '请先选择接收人。', true);
      event.target.disabled = true;
      try {
        await fetchJson('/api/workspace/admin/assign', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...unassignedGroups[Number(assignIndex)], identity_id: identityId }) });
        await loadUnassigned();
      } catch (error) { setStatus('unassignedStatus', error.message, true); event.target.disabled = false; }
      return;
    }
    const bindingIdentity = event.target.dataset.saveBinding;
    if (bindingIdentity) {
      const glass = document.querySelector(`[data-user-glass="${CSS.escape(bindingIdentity)}"]`).value;
      const weekday = document.querySelector(`[data-user-weekday="${CSS.escape(bindingIdentity)}"]`).value;
      if (glass && weekday === '') return alert('请选择固定使用的星期。');
      try { await fetchJson(`/api/workspace/admin/users/${encodeURIComponent(bindingIdentity)}/glass`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ glass_number: glass, weekday: weekday === '' ? null : Number(weekday) }) }); await loadAdminUsers(); }
      catch (error) { alert(error.message); }
      return;
    }
    const number = event.target.dataset.saveGlass;
    if (number) { const input = document.querySelector(`[data-glass-device="${CSS.escape(number)}"]`); try { await fetchJson(`/admin/api/auth/glasses/${encodeURIComponent(number)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ device_id: input.value }) }); setStatus('adminGlassStatus', `${number} 号眼镜已保存。`); await loadAdminGlasses(); } catch (error) { setStatus('adminGlassStatus', error.message, true); } }
    const recordId = event.target.dataset.deleteRecord;
    if (recordId && confirm('确认删除整条记录？')) { const base = event.target.dataset.deleteScope === 'student' ? (cfg.role === 'student' ? `${userApi}/media/records` : '/admin/api/records') : '/teacher-admin/api/records'; try { await fetchJson(`${base}/${encodeURIComponent(recordId)}`, { method: 'DELETE' }); await Promise.all([loadTeacherMedia(), loadAdminMedia()]); } catch (error) { alert(error.message); } }
    const photoId = event.target.dataset.deletePhoto;
    if (photoId && confirm('确认删除照片？')) { const base = event.target.dataset.deleteScope === 'student' ? (cfg.role === 'student' ? `${userApi}/media/photos` : '/admin/api/photos') : '/teacher-admin/api/photos'; try { await fetchJson(`${base}/${encodeURIComponent(photoId)}`, { method: 'DELETE' }); await Promise.all([loadTeacherMedia(), loadAdminMedia()]); } catch (error) { alert(error.message); } }
    const demoId = event.target.dataset.deleteDemo;
    if (demoId && confirm('确认删除演示视频？')) { try { await fetchJson(`/admin/api/demo/videos/${encodeURIComponent(demoId)}`, { method: 'DELETE' }); await loadVideos(); } catch (error) { alert(error.message); } }
    const feedbackId = event.target.dataset.deleteFeedback;
    if (feedbackId && confirm('确认删除意见？')) { try { await fetchJson(`/admin/api/feedback/${encodeURIComponent(feedbackId)}`, { method: 'DELETE' }); await loadFeedback(); } catch (error) { alert(error.message); } }
  });

  loadExperiments().then(loadVideos);
})();
