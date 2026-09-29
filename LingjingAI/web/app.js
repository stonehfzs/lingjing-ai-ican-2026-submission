/* 镜序: dependency-free, local-only storyboard workspace. */
'use strict';
const legacyWorkspaceId = new URLSearchParams(location.search).get('workspace') || localStorage.getItem('studio.workspace');

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = {
  project: null, models: { image: [], video: [] }, jobs: [], selected: new URLSearchParams(location.search).get('shot') || null,
  dirty: false, mutation: 0, saving: false, loading: true, submitting: false,
  online: false, external: null, transform: { x: 45, y: 155, scale: .8 },
  paramError: false, pollBusy: false, initialFit: false, uncertainRequest: null,
  projectPath: '', jobsSignature: '', viewReady: false,
};
const statusLabels = { draft: '待生成', ready: '待生成', imported: '待生成', queued: '排队中', pending: '等待中', submitting: '提交中', submitted: '已提交', running: '生成中', processing: '生成中', generating: '生成中', downloading: '保存中', completed: '已完成', succeeded: '已完成', success: '已完成', done: '已完成', failed: '生成失败', error: '生成失败', cancelled: '已取消', unknown: '待查询' };
const uid = () => crypto.randomUUID();
const selectedShot = () => state.project?.shots.find((shot) => shot.id === state.selected);
function el(tag, className, text) { const element = document.createElement(tag); if (className) element.className = className; if (text !== undefined) element.textContent = text; return element; }
function toast(message, isError = false) { const target = $('#toast'); target.textContent = message; target.classList.toggle('error', isError); target.hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => { target.hidden = true; }, isError ? 7500 : 3300); }
function errorText(error) { return error?.message || String(error); }
async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || 30000);
  try {
    const response = await fetch(path, { ...options, headers: { 'Accept': 'application/json', ...(legacyWorkspaceId ? {'X-Studio-Workspace': legacyWorkspaceId} : {}), ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(['POST', 'PUT', 'DELETE'].includes(options.method) ? { 'X-Studio-Request': '1' } : {}), ...options.headers }, signal: controller.signal });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(payload.error || `请求失败（${response.status}）`); error.status = response.status; error.detail = payload.detail; throw error; }
    return payload;
  } catch (error) { if (error.name === 'AbortError') throw new Error('请求超时。生成请求不会自动重试，请先刷新生成记录确认状态。'); throw error; }
  finally { clearTimeout(timeout); }
}
function markDirty() { state.dirty = true; state.mutation++; updateSaveState(); }
function updateSaveState() { $('#saveState').textContent = state.saving ? '正在保存…' : state.dirty ? '未保存' : '已保存'; $('#saveState').classList.toggle('dirty', state.dirty); $('#saveBtn').disabled = state.saving || !state.project; }
function normalizedProject(raw) {
  if (!raw || typeof raw !== 'object' || !Array.isArray(raw.shots)) throw new Error('项目格式不正确：需要包含 shots 镜头列表。');
  if (raw.shots.length > 2000) throw new Error('项目镜头超过 2000 个，请拆分后导入。');
  const ids = new Set();
  const shots = raw.shots.map((source, i) => {
    const id = typeof source.id === 'string' && source.id ? source.id : uid();
    if (ids.has(id)) throw new Error('项目包含重复的镜头 ID。');
    ids.add(id);
    return { ...source, id, number: String(source.number ?? String(i + 1).padStart(3, '0')), title: String(source.title || `镜头 ${i + 1}`), scene: String(source.scene || ''), shotType: String(source.shotType || ''), camera: String(source.camera || ''), action: String(source.action || ''), prompt: String(source.prompt || ''), videoPrompt: String(source.videoPrompt || ''), kind: source.kind === 'video' ? 'video' : 'image', modelId: String(source.modelId || ''), params: source.params && typeof source.params === 'object' && !Array.isArray(source.params) ? source.params : {}, referencePaths: Array.isArray(source.referencePaths) ? source.referencePaths.map(String) : [], position: { x: Number.isFinite(source.position?.x) ? source.position.x : i * 360, y: Number.isFinite(source.position?.y) ? source.position.y : 0 } };
  });
  return { ...raw, id: raw.id || uid(), title: String(raw.title || '未命名项目'), script: String(raw.script || ''), style: raw.style || '', shots, edges: (Array.isArray(raw.edges) ? raw.edges : []).filter((edge) => ids.has(edge.source) && ids.has(edge.target)).map((edge) => ({ ...edge, id: edge.id || uid() })) };
}
function setProject(project, { fit = false } = {}) {
  state.project = normalizedProject(project); state.external = null; state.dirty = false; state.paramError = false;
  if (!state.project.shots.some((shot) => shot.id === state.selected)) state.selected = state.project.shots[0]?.id || null;
  $('#projectTitle').value = state.project.title; $('#scriptText').value = state.project.script; updateScriptLength();
  $('#externalUpdate').hidden = true; renderList(); renderNodes(); renderInspector(); updateSaveState();
  if (fit && state.project.shots.length) requestAnimationFrame(() => { const requested = new URLSearchParams(location.search).get('shot'); if (requested && state.project.shots.some((shot) => shot.id === requested)) selectShot(requested, true); else fitCanvas(); });
}
async function saveProject({ notify = true } = {}) {
  if (!state.project || state.saving) return false;
  if (state.paramError) { toast('请先修正自定义生成参数中的 JSON。', true); return false; }
  state.saving = true; updateSaveState();
  const version = state.mutation;
  const snapshot = structuredClone(state.project);
  try {
    const result = await api('/api/project', { method: 'PUT', body: JSON.stringify(snapshot) });
    if (version === state.mutation) { state.project = normalizedProject(result); state.dirty = false; }
    else { state.project.revision = result.revision; state.project.updatedAt = result.updatedAt; }
    state.external = null; $('#externalUpdate').hidden = true;
    if (notify) toast('项目已保存');
    return true;
  } catch (error) {
    if (error.status === 409) { state.external = await api('/api/project').catch(() => null); $('#externalUpdate').hidden = false; toast('项目已有新版本。请先导出当前编辑，再载入新版本，避免覆盖。', true); }
    else toast(`保存失败：${errorText(error)}`, true);
    return false;
  } finally { state.saving = false; updateSaveState(); }
}
function getModelId(model) { return String(model.id ?? model.modelId ?? model.model_id ?? model.model_name ?? model.name ?? ''); }
function getModelName(model) { return String(model.name ?? model.label ?? model.display_name ?? getModelId(model)); }
function modelForShot(shot) { return (state.models[shot.kind] || []).find((model) => getModelId(model) === shot.modelId); }
function paramSchema(model) {
  if (!model) return {};
  const raw = model.params || model.parameters || model.schema?.properties || model.schema || {};
  if (Array.isArray(raw)) return Object.fromEntries(raw.map((item) => [item.name || item.key || item.id, item]).filter(([key]) => key));
  return raw.properties || raw;
}
function defaultParams(model) { return Object.fromEntries(Object.entries(paramSchema(model)).filter(([, spec]) => spec && typeof spec === 'object' && Object.hasOwn(spec, 'default')).map(([key, spec]) => [key, spec.default])); }
function safeMediaUrl(url) {
  if (!url || typeof url !== 'string') return '';
  try { const parsed = new URL(url, location.href); return parsed.origin === location.origin && /^https?:$/.test(parsed.protocol) ? parsed.href : ''; }
  catch { return ''; }
}
function jobForShot(shot) { return state.jobs.find((job) => job.shotId === shot.id); }
function mediaForShot(shot) { const job = jobForShot(shot); return { url: safeMediaUrl(job?.mediaUrl || shot.mediaUrl), thumbnail: safeMediaUrl(job?.thumbnailUrl || shot.thumbnailUrl), kind: job?.mediaUrl ? job.kind : (shot.mediaKind || shot.kind), status: job?.status || shot.status || '' }; }
function renderList() {
  if (!state.project) return;
  const list = $('#shotList'); list.replaceChildren();
  state.project.shots.forEach((shot) => {
    const button = el('button', `shot-item${shot.id === state.selected ? ' active' : ''}`); button.dataset.shotId = shot.id;
    const main = el('span', 'list-content'); main.append(el('span', 'list-title', shot.title || '未命名镜头'), el('span', 'list-scene', [shot.shotType, shot.scene || shot.camera].filter(Boolean).join(' · ') || '等待设定画面'));
    button.append(el('span', 'list-number', shot.number), main);
    if (mediaForShot(shot).url) button.append(el('span', 'list-status', '✓'));
    button.addEventListener('click', () => selectShot(shot.id, true)); list.append(button);
  });
  $('#shotCount').textContent = state.project.shots.length; $('#canvasCount').textContent = `${String(state.project.shots.length).padStart(2, '0')} 镜`;
}
function buildNode(shot) {
  const node = el('article', `shot-node${shot.id === state.selected ? ' selected' : ''}`); node.dataset.shotId = shot.id;
  node.style.left = `${shot.position.x}px`; node.style.top = `${shot.position.y}px`;
  node.tabIndex = 0; node.setAttribute('aria-label', `镜头 ${shot.number} ${shot.title}`);
  const media = mediaForShot(shot);
  const heading = el('div', 'node-heading'); heading.append(el('span', 'node-number', shot.number), el('span', 'node-title', shot.title), el('span', `node-state ${media.status}`));
  const frame = el('div', 'node-media');
  if (media.url) {
    if (media.kind === 'video' || /\.(mp4|webm|mov)(?:\?|$)/i.test(media.url)) { const video = el('video'); video.src = media.url; if (media.thumbnail) video.poster = media.thumbnail; video.controls = true; video.preload = 'metadata'; video.playsInline = true; frame.append(video); }
    else { const image = el('img'); image.src = media.url; image.alt = shot.title; image.loading = 'lazy'; image.draggable = false; frame.append(image); }
  } else if (media.thumbnail) { const image = el('img'); image.src = media.thumbnail; image.alt = shot.title; image.draggable = false; frame.append(image); }
  else { const placeholder = el('div', 'node-placeholder'); placeholder.append(el('span', 'frame-corners'), el('span', 'big-number', shot.number), el('span', 'placeholder-caption', media.status ? (statusLabels[media.status] || media.status) : '等待生成画面')); frame.append(placeholder); }
  frame.append(el('span', 'node-badge', shot.validationOnly ? '验证试片' : shot.kind === 'video' ? 'VIDEO' : 'STILL'));
  const footer = el('div', 'node-footer'); const meta = el('div', 'node-meta');
  meta.append(el('span', '', shot.shotType || '景别待定'), el('span', 'dot', '•'), el('span', '', shot.camera || '运镜待定'));
  footer.append(meta, el('p', 'node-action', shot.action || shot.scene || '选择镜头，补充画面动作与提示词。'));
  node.append(heading, frame, footer);
  node.addEventListener('pointerdown', (event) => startNodeDrag(event, shot, node));
  node.addEventListener('keydown', (event) => { if (event.key === 'Enter') selectShot(shot.id, false); });
  return node;
}
function renderNodes() {
  if (!state.project) return;
  $('#nodes').replaceChildren(...state.project.shots.map(buildNode));
  $('#emptyCanvas').hidden = state.project.shots.length > 0; drawEdges();
}
function refreshNode(shot) {
  const node = [...$('#nodes').children].find((item) => item.dataset.shotId === shot.id);
  if (node) node.replaceWith(buildNode(shot)); else renderNodes();
  renderList(); drawEdges();
}
function drawEdges() {
  const svg = $('#connections'); svg.replaceChildren(); if (!state.project) return;
  const index = new Map(state.project.shots.map((shot) => [shot.id, shot]));
  for (const edge of state.project.edges) {
    const source = index.get(edge.source), target = index.get(edge.target); if (!source || !target) continue;
    const x1 = source.position.x + 278, y1 = source.position.y + 118, x2 = target.position.x, y2 = target.position.y + 118;
    const curve = Math.max(48, Math.abs(x2 - x1) * .5); const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', `M${x1} ${y1} C${x1 + curve} ${y1},${x2 - curve} ${y2},${x2} ${y2}`);
    if (source.id === state.selected || target.id === state.selected) path.classList.add('active'); svg.append(path);
  }
}
function selectShot(id, center = false) {
  if (state.paramError) toast('无效的自定义参数未保存。', true);
  state.selected = id; state.paramError = false;
  const currentUrl = new URL(location.href); currentUrl.searchParams.set('shot', id); history.replaceState(null, '', currentUrl);
  $$('.shot-node').forEach((node) => node.classList.toggle('selected', node.dataset.shotId === id));
  renderList(); renderInspector(); drawEdges();
  if (center) { const shot = selectedShot(), viewport = $('#canvasViewport'); if (shot) { state.transform.scale = Math.max(.82, state.transform.scale); state.transform.x = viewport.clientWidth / 2 - (shot.position.x + 139) * state.transform.scale; state.transform.y = viewport.clientHeight / 2 - (shot.position.y + 135) * state.transform.scale; applyTransform(); } }
}
function startNodeDrag(event, shot, node) {
  if (event.button !== 0 || event.target.closest('video,button,a,input')) return;
  event.stopPropagation(); event.preventDefault();
  if (state.selected !== shot.id) selectShot(shot.id);
  const startX = event.clientX, startY = event.clientY, pos = { ...shot.position }; let moved = false;
  node.setPointerCapture(event.pointerId); node.classList.add('dragging');
  function move(next) { const dx = (next.clientX - startX) / state.transform.scale, dy = (next.clientY - startY) / state.transform.scale; if (Math.abs(dx) + Math.abs(dy) > 2) moved = true; shot.position = { x: Math.round(pos.x + dx), y: Math.round(pos.y + dy) }; node.style.left = `${shot.position.x}px`; node.style.top = `${shot.position.y}px`; drawEdges(); }
  function end() { node.classList.remove('dragging'); node.removeEventListener('pointermove', move); node.removeEventListener('pointerup', end); node.removeEventListener('pointercancel', end); if (moved) markDirty(); }
  node.addEventListener('pointermove', move); node.addEventListener('pointerup', end); node.addEventListener('pointercancel', end);
}
function applyTransform() { const { x, y, scale } = state.transform; $('#canvasWorld').style.transform = `translate(${x}px,${y}px) scale(${scale})`; $('#zoomLevel').textContent = `${Math.round(scale * 100)}%`; $('#canvasViewport').style.backgroundSize = `${22 * scale}px ${22 * scale}px`; $('#canvasViewport').style.backgroundPosition = `${x}px ${y}px`; }
function zoomAt(scale, cx, cy) { scale = Math.max(.12, Math.min(2, scale)); const old = state.transform; state.transform = { x: cx - (cx - old.x) / old.scale * scale, y: cy - (cy - old.y) / old.scale * scale, scale }; applyTransform(); }
function zoomBy(factor) { const viewport = $('#canvasViewport'); zoomAt(state.transform.scale * factor, viewport.clientWidth / 2, viewport.clientHeight / 2); }
function fitCanvas() {
  if (!state.project?.shots.length) return;
  const shots = state.project.shots, viewport = $('#canvasViewport');
  const minX = Math.min(...shots.map((shot) => shot.position.x)), minY = Math.min(...shots.map((shot) => shot.position.y));
  const width = Math.max(...shots.map((shot) => shot.position.x + 278)) - minX, height = Math.max(...shots.map((shot) => shot.position.y + 290)) - minY;
  const scale = Math.max(.12, Math.min(1, (viewport.clientWidth - 85) / width, (viewport.clientHeight - 190) / height));
  state.transform = { x: (viewport.clientWidth - width * scale) / 2 - minX * scale, y: (viewport.clientHeight - height * scale) / 2 - minY * scale + 15, scale }; applyTransform();
}
function renderInspector() {
  const shot = selectedShot(); $('#shotForm').hidden = !shot; $('#noSelection').hidden = !!shot; $('#selectedNumber').textContent = shot ? `SHOT ${shot.number}` : '—';
  if (!shot) return;
  for (const name of ['number', 'title', 'scene', 'shotType', 'camera', 'action', 'prompt', 'videoPrompt']) $('#shotForm').elements[name].value = shot[name] || '';
  $('#referencePaths').value = shot.referencePaths.join('\n'); $('#trimSeconds').value = shot.trimSeconds ?? '';
  $$('.segmented button').forEach((button) => button.classList.toggle('active', button.dataset.kind === shot.kind));
  $('#imagePromptLabel').hidden = shot.kind !== 'image'; $('#videoPromptLabel').hidden = shot.kind !== 'video'; $('#trimLabel').hidden = shot.kind !== 'video';
  renderModelSelect(); renderParams(); $('#rawParams').value = JSON.stringify(shot.params, null, 2); $('#rawParams').setCustomValidity('');
  const job = jobForShot(shot); $('#shotError').hidden = !job?.error; $('#shotError').textContent = job?.error || '';
  let validationNotice = $('#validationNotice');
  if (!validationNotice) { validationNotice = el('div', 'validation-notice'); validationNotice.id = 'validationNotice'; $('#timingSummary').after(validationNotice); }
  validationNotice.hidden = !shot.validationOnly;
  validationNotice.textContent = shot.mediaPurpose || '链路验证试片，尚未作为正式镜头验收。';
  updateTimingSummary();
  updateGenerateButton();
}
function updateTimingSummary() {
  const shot = selectedShot(), summary = $('#timingSummary'); if (!shot || !summary) return;
  summary.replaceChildren();
  const planned = shot.plannedDuration;
  const duration = shot.params.duration ?? defaultParams(modelForShot(shot)).duration;
  summary.append(el('span', '', `剧本剪辑 ${planned !== undefined && planned !== null ? `${planned} 秒` : '待定'}`));
  summary.append(el('span', '', shot.kind === 'video' ? `生成设置 ${duration !== undefined ? `${duration} 秒` : '模型默认'}` : '生成设置 静帧'));
  const previous = jobForShot(shot);
  if (previous?.kind === 'video' && previous.mediaUrl && previous.params?.duration) summary.append(el('span', 'completed-timing', `现有视频：生成 ${previous.params.duration} 秒${previous.trimSeconds ? ` / 输出 ${previous.trimSeconds} 秒` : ''}`));
}
function renderModelSelect() {
  const shot = selectedShot(); if (!shot) return;
  const select = $('#modelSelect'); select.replaceChildren();
  const models = state.models[shot.kind] || [];
  if (!models.length) { const option = el('option', '', '当前没有可用模型'); option.value = ''; select.append(option); }
  else {
    if (!shot.modelId) { const option = el('option', '', '选择模型'); option.value = ''; select.append(option); }
    else if (!models.some((model) => getModelId(model) === shot.modelId)) { const option = el('option', '', `${shot.modelId}（目录中未找到）`); option.value = shot.modelId; select.append(option); }
    for (const model of models) { const option = el('option', '', getModelName(model)); option.value = getModelId(model); select.append(option); }
  }
  select.value = shot.modelId;
  const model = modelForShot(shot); $('#modelNote').textContent = model?.description || '';
}
function renderParams() {
  const shot = selectedShot(); if (!shot) return;
  const container = $('#dynamicParams'); container.replaceChildren();
  for (const [key, spec] of Object.entries(paramSchema(modelForShot(shot)))) {
    if (!spec || typeof spec !== 'object' || ['prompt', 'imagePaths', 'images', 'model'].includes(key)) continue;
    const label = el('label', '', key === 'duration' ? '生成时长（秒）' : (spec.label || spec.title || key)); let input;
    const options = spec.options || spec.enum || spec.choices;
    const effective = Object.hasOwn(shot.params, key) ? shot.params[key] : spec.default;
    if (Array.isArray(options)) {
      input = el('select'); const blank = el('option', '', '默认'); blank.value = ''; input.append(blank);
      for (const entry of options) { const value = typeof entry === 'object' ? (entry.value ?? entry.id ?? entry.name) : entry; const option = el('option', '', typeof entry === 'object' ? (entry.label || entry.name || value) : String(entry)); option.value = JSON.stringify(value); input.append(option); }
      input.value = effective !== undefined ? JSON.stringify(effective) : '';
      if (!input.value && effective !== undefined) { const option = el('option', '', String(effective)); option.value = JSON.stringify(effective); input.append(option); input.value = option.value; }
      input.addEventListener('change', () => { if (!input.value) delete shot.params[key]; else shot.params[key] = JSON.parse(input.value); updateParamJson(); markDirty(); });
    } else if (spec.type === 'boolean' || spec.type === 'checkbox') {
      input = el('input'); input.type = 'checkbox'; input.checked = effective === true || effective === 'true'; label.classList.add('boolean-field');
      input.addEventListener('change', () => { shot.params[key] = input.checked; updateParamJson(); markDirty(); });
    } else {
      input = el('input'); input.type = ['number', 'integer', 'float'].includes(spec.type) ? 'number' : 'text'; input.value = effective ?? '';
      if (spec.minimum !== undefined || spec.min !== undefined) input.min = spec.minimum ?? spec.min;
      if (spec.maximum !== undefined || spec.max !== undefined) input.max = spec.maximum ?? spec.max;
      if (input.type === 'number') input.step = spec.step || (spec.type === 'integer' ? 1 : 'any');
      input.addEventListener('input', () => { if (input.value === '') delete shot.params[key]; else shot.params[key] = input.type === 'number' ? Number(input.value) : input.value; updateParamJson(); markDirty(); });
    }
    input.dataset.paramKey = key; label.append(input); if (spec.description) label.title = spec.description; container.append(label);
  }
  updateTimingSummary();
}
function updateParamJson() { $('#rawParams').value = JSON.stringify(selectedShot()?.params || {}, null, 2); state.paramError = false; $('#rawParams').setCustomValidity(''); updateTimingSummary(); }
function updateGenerateButton() { const shot = selectedShot(); $('#generateBtn').disabled = state.submitting || !shot || !shot.modelId || !state.online; $('#generateBtn').querySelector('span').textContent = state.submitting ? '正在提交…' : !state.online ? '等待 Design 连接' : `生成这一镜${shot?.kind === 'video' ? ' · 视频' : ''}`; }
function addShot() {
  if (!state.project) return;
  const previous = state.project.shots.at(-1); const shot = { id: uid(), number: String(state.project.shots.length + 1).padStart(3, '0'), title: '未命名镜头', scene: '', shotType: '中景', camera: '固定', action: '', prompt: '', videoPrompt: '', referencePaths: [], kind: 'image', modelId: '', params: {}, position: { x: previous ? previous.position.x + 360 : 0, y: previous?.position.y || 0 } };
  const first = state.models.image[0]; if (first) { shot.modelId = getModelId(first); shot.params = defaultParams(first); }
  state.project.shots.push(shot); if (previous) state.project.edges.push({ id: uid(), source: previous.id, target: shot.id });
  markDirty(); renderNodes(); selectShot(shot.id, true);
}
function layoutShots() {
  if (!state.project) return;
  state.project.shots.forEach((shot, i) => { shot.position = { x: i * 360, y: 0 }; });
  state.project.edges = state.project.shots.slice(1).map((shot, i) => ({ id: uid(), source: state.project.shots[i].id, target: shot.id }));
  markDirty(); renderNodes(); fitCanvas(); toast('已按镜头列表顺序横向排列');
}
function renderJobs() {
  const list = $('#jobList'); list.replaceChildren(); $('#jobCount').textContent = state.jobs.length;
  if (!state.jobs.length) { list.append(el('div', 'job-empty', '暂无生成任务。选择镜头，确认参数后提交。')); return; }
  for (const job of state.jobs.slice(0, 40)) {
    const row = el('div', 'job-row'); const main = el('div', 'job-main'); const shot = state.project?.shots.find((item) => item.id === job.shotId);
    main.append(el('div', 'job-name', `${shot ? `${shot.number} · ${shot.title}` : '生成验证'} · ${job.kind === 'video' ? '视频' : '图像'}`));
    main.append(el('div', `job-info${job.error ? ' job-error' : ''}`, job.error || `${job.modelId || ''} · ${job.createdAt ? new Date(job.createdAt).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''}`));
    row.append(main, el('span', 'job-state', statusLabels[job.status] || job.status || '未知'));
    const url = safeMediaUrl(job.mediaUrl); if (url) { const link = el('a', '', '打开'); link.href = url; link.target = '_blank'; link.rel = 'noopener'; row.append(link); }
    const refresh = el('button', '', '刷新'); refresh.title = '查询已有任务，不会重新提交生成'; refresh.addEventListener('click', () => refreshJob(job.id, refresh)); row.append(refresh); list.append(row);
  }
}
async function refreshJob(id, button) {
  button.disabled = true;
  try { const updated = await api(`/api/jobs/${encodeURIComponent(id)}/refresh`, { method: 'POST', body: '{}' }); const index = state.jobs.findIndex((item) => item.id === id); if (index >= 0) state.jobs[index] = updated; else state.jobs.unshift(updated); renderJobs(); renderNodes(); renderList(); if (updated.shotId === state.selected) renderInspector(); toast('任务状态已刷新'); }
  catch (error) { toast(`查询失败：${errorText(error)}`, true); button.disabled = false; }
}
function confirmation(payload, shot) {
  const dialog = $('#confirmDialog'); $('#confirmTitle').textContent = `${shot.number} · ${shot.title}`;
  const container = $('#confirmDetails'); container.replaceChildren();
  const model = modelForShot(shot);
  const rows = [['类型 / 模型', `${payload.kind === 'video' ? '视频' : '图像'} · ${model ? getModelName(model) : payload.modelId}`], ['生成参数', Object.entries(payload.params).map(([key, value]) => `${paramSchema(model)[key]?.label || key}: ${typeof value === 'object' ? JSON.stringify(value) : value}`).join('\n') || '使用模型默认参数'], ['参考图片', `${payload.imagePaths.length} 张`], ['提示词', payload.prompt]];
  if (payload.trimSeconds) rows.splice(3, 0, ['输出截取', `${payload.trimSeconds} 秒（模型仍按生成时长计费）`]);
  for (const [name, value] of rows) { const row = el('div', 'confirm-detail'); row.append(el('span', '', name), el('span', '', value)); container.append(row); }
  return new Promise((resolve) => { dialog.returnValue = 'cancel'; dialog.addEventListener('close', () => resolve(dialog.returnValue === 'submit'), { once: true }); dialog.showModal(); });
}
async function generateShot() {
  const shot = selectedShot(); if (!shot || state.submitting) return;
  if (state.paramError) { toast('请修正自定义生成参数中的 JSON。', true); return; }
  const prompt = (shot.kind === 'video' ? shot.videoPrompt : shot.prompt).trim();
  if (!prompt) { toast('请先填写这一镜的生成提示词。', true); return; }
  if (!modelForShot(shot)) { toast('请从当前目录中选择一个可用模型。', true); return; }
  if (!$('#shotForm').reportValidity()) return;
  const payload = { shotId: shot.id, kind: shot.kind, modelId: shot.modelId, params: { ...defaultParams(modelForShot(shot)), ...shot.params }, prompt, imagePaths: [...shot.referencePaths] };
  if (shot.kind === 'video' && shot.trimSeconds) payload.trimSeconds = Number(shot.trimSeconds);
  if (!(await confirmation(payload, shot))) return;
  state.submitting = true; updateGenerateButton();
  try {
    if (state.dirty && !(await saveProject({ notify: false }))) return;
    const signature = JSON.stringify(payload);
    const requestId = state.uncertainRequest?.signature === signature ? state.uncertainRequest.requestId : uid();
    state.uncertainRequest = { signature, requestId };
    const job = await api('/api/jobs', { method: 'POST', body: JSON.stringify({ ...payload, requestId, confirmed: true }), timeout: 60000 });
    state.uncertainRequest = null;
    const index = state.jobs.findIndex((item) => item.id === job.id); if (index >= 0) state.jobs[index] = job; else state.jobs.unshift(job);
    renderJobs(); renderNodes(); renderInspector(); $('.jobs-tray').classList.add('open'); $('#jobsToggleIcon').textContent = '⌄'; updateTrayPosition();
    toast('生成任务已提交，可在生成记录中查询进度');
  } catch (error) { toast(`提交结果：${errorText(error)}。不会自动重复提交。`, true); await loadJobs().catch(() => {}); }
  finally { state.submitting = false; updateGenerateButton(); }
}
async function loadJobs() {
  const result = await api('/api/jobs'); const jobs = Array.isArray(result) ? result : result.jobs || [];
  const signature = JSON.stringify(jobs);
  state.jobs = [...jobs].sort((a, b) => String(b.createdAt || '').localeCompare(String(a.createdAt || '')));
  // Keep the DOM stable while a pointer is captured by a moving node.
  if ($('.shot-node.dragging')) return;
  if (signature !== state.jobsSignature) { state.jobsSignature = signature; renderJobs(); renderNodes(); renderList(); const job = selectedShot() && jobForShot(selectedShot()); $('#shotError').hidden = !job?.error; $('#shotError').textContent = job?.error || ''; }
}
async function loadStatus() {
  try { const status = await api('/api/status'); state.online = !!status.online; state.projectPath = status.projectPath || ''; $('#connection').className = `connection ${state.online ? 'online' : 'offline'}`; $('#connection').replaceChildren(el('i'), document.createTextNode(state.online ? 'Design 已连接' : 'Design 未连接')); $('#connection').title = state.online ? '本地 Design 连接可用' : '请保持 MiniMax Design 开启并登录'; }
  catch { state.online = false; $('#connection').className = 'connection offline'; $('#connection').replaceChildren(el('i'), document.createTextNode('服务未连接')); }
  updateGenerateButton();
}
async function poll() {
  if (state.pollBusy || state.loading || document.hidden) return; state.pollBusy = true;
  try {
    await Promise.allSettled([loadStatus(), loadJobs(), (async () => {
      if (state.saving || state.submitting || $('.shot-node.dragging') || $('#canvasViewport').classList.contains('panning')) return;
      const project = await api('/api/project');
      if (state.saving || state.submitting || $('.shot-node.dragging') || $('#canvasViewport').classList.contains('panning')) return;
      if (state.project && project.revision !== state.project.revision) {
        if (state.dirty) { state.external = project; $('#externalUpdate').hidden = false; }
        else setProject(project);
      }
    })()]);
  } finally { state.pollBusy = false; }
}
function updateScriptLength() { $('#scriptLength').textContent = `${$('#scriptText').value.length.toLocaleString()} 字`; }
function switchTab(name) { $$('.panel-tabs button').forEach((button) => button.classList.toggle('active', button.dataset.tab === name)); $('#shotsTab').classList.toggle('active', name === 'shots'); $('#scriptTab').classList.toggle('active', name === 'script'); }
function exportProject() {
  if (!state.project) return;
  const blob = new Blob([JSON.stringify(state.project, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const anchor = el('a'); anchor.href = url; anchor.download = `${state.project.title.replace(/[\\/:*?"<>|]/g, '_') || 'storyboard'}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); toast('项目已导出');
}
function updateTrayPosition() { const height = $('.jobs-tray').offsetHeight; $('.canvas-bottom').style.bottom = `${height + 18}px`; }

// Application event wiring.
$('#saveBtn').addEventListener('click', () => saveProject());
$('#projectTitle').addEventListener('input', (event) => { if (!state.project) return; state.project.title = event.target.value; markDirty(); });
$('#scriptText').addEventListener('input', (event) => { if (!state.project) return; state.project.script = event.target.value; updateScriptLength(); markDirty(); });
$$('.panel-tabs button').forEach((button) => button.addEventListener('click', () => switchTab(button.dataset.tab)));
$('#openScriptBtn').addEventListener('click', () => switchTab('script'));
$('#addShotBtn').addEventListener('click', addShot); $('#emptyAddBtn').addEventListener('click', addShot);
$('#layoutBtn').addEventListener('click', layoutShots); $('#fitBtn').addEventListener('click', fitCanvas);
$('#zoomOutBtn').addEventListener('click', () => zoomBy(1 / 1.2)); $('#zoomInBtn').addEventListener('click', () => zoomBy(1.2));
$('#resetZoomBtn').addEventListener('click', () => { const viewport = $('#canvasViewport'); zoomAt(1, viewport.clientWidth / 2, viewport.clientHeight / 2); });
$('#shotForm').addEventListener('submit', (event) => event.preventDefault());
for (const name of ['number', 'title', 'scene', 'shotType', 'camera', 'action', 'prompt', 'videoPrompt']) {
  $('#shotForm').elements[name].addEventListener('input', (event) => { const shot = selectedShot(); if (!shot) return; shot[name] = event.target.value; markDirty(); if (!['prompt', 'videoPrompt'].includes(name)) { refreshNode(shot); $('#selectedNumber').textContent = `SHOT ${shot.number}`; } });
}
$('#referencePaths').addEventListener('input', (event) => { const shot = selectedShot(); if (shot) { shot.referencePaths = event.target.value.split('\n').map((line) => line.trim().replace(/^"(.*)"$/, '$1')).filter(Boolean); markDirty(); } });
$('#trimSeconds').addEventListener('input', (event) => { const shot = selectedShot(); if (shot) { if (event.target.value) shot.trimSeconds = Number(event.target.value); else delete shot.trimSeconds; markDirty(); } });
$('#rawParams').addEventListener('input', (event) => { const shot = selectedShot(); if (!shot) return; try { const params = JSON.parse(event.target.value || '{}'); if (!params || typeof params !== 'object' || Array.isArray(params)) throw new Error('参数应为 JSON 对象'); shot.params = params; state.paramError = false; event.target.setCustomValidity(''); renderParams(); markDirty(); } catch { state.paramError = true; event.target.setCustomValidity('请输入有效 JSON 对象'); } });
$$('.segmented button').forEach((button) => button.addEventListener('click', () => { const shot = selectedShot(); if (!shot || shot.kind === button.dataset.kind) return; shot.kind = button.dataset.kind; const model = state.models[shot.kind][0]; shot.modelId = model ? getModelId(model) : ''; shot.params = model ? defaultParams(model) : {}; markDirty(); renderInspector(); refreshNode(shot); }));
$('#modelSelect').addEventListener('change', (event) => { const shot = selectedShot(); if (!shot) return; shot.modelId = event.target.value; shot.params = defaultParams(modelForShot(shot)); markDirty(); renderInspector(); });
$('#deleteShotBtn').addEventListener('click', () => { const shot = selectedShot(); if (!shot || !confirm(`删除镜头 ${shot.number}「${shot.title}」？已生成的媒体文件会保留。`)) return; state.project.shots = state.project.shots.filter((item) => item.id !== shot.id); state.project.edges = state.project.edges.filter((edge) => edge.source !== shot.id && edge.target !== shot.id); state.selected = state.project.shots[0]?.id || null; markDirty(); renderList(); renderNodes(); renderInspector(); });
$('#generateBtn').addEventListener('click', generateShot);
$('#jobsToggle').addEventListener('click', () => { const open = $('.jobs-tray').classList.toggle('open'); $('#jobsToggleIcon').textContent = open ? '⌄' : '⌃'; updateTrayPosition(); });
$('#importBtn').addEventListener('click', () => $('#projectFile').click()); $('#exportBtn').addEventListener('click', exportProject);
$('#projectFile').addEventListener('change', async (event) => {
  const file = event.target.files?.[0]; if (!file) return;
  try { if (file.size > 20 * 1024 * 1024) throw new Error('项目文件超过 20 MB。'); const project = normalizedProject(JSON.parse(await file.text())); if (state.dirty && !confirm('当前有未保存编辑。导入后将替换当前画布，是否继续？')) return; project.revision = state.project?.revision; setProject(project, { fit: true }); markDirty(); toast('项目已载入，保存后写入工作项目'); }
  catch (error) { toast(`导入失败：${errorText(error)}`, true); } finally { event.target.value = ''; }
});
$('#importScriptBtn').addEventListener('click', () => $('#scriptFile').click());
$('#scriptFile').addEventListener('change', async (event) => { const file = event.target.files?.[0]; if (!file || !state.project) return; try { if (file.size > 5 * 1024 * 1024) throw new Error('剧本文件超过 5 MB。'); if (state.project.script.trim() && !confirm('用导入的文件替换当前剧本原文？')) return; state.project.script = await file.text(); $('#scriptText').value = state.project.script; updateScriptLength(); markDirty(); toast('剧本原文已导入。保存后可交给 Codex 拆解分镜。'); } catch (error) { toast(`导入失败：${errorText(error)}`, true); } finally { event.target.value = ''; } });
$('#copyBriefBtn').addEventListener('click', async () => {
  const text = `请读取我的镜序项目${state.projectPath ? `（${state.projectPath}）` : '中的项目 JSON'}，根据 script 剧本原文完成分镜。请保留原文，梳理角色与场景一致性，为每个镜头填写编号、标题、场景、景别、运镜、画面动作、图像提示词和视频提示词，并按顺序横向安排节点及连线。更新项目时遵守 revision 乐观锁。先编排供我查看，不提交付费生成。`;
  try { await navigator.clipboard.writeText(text); toast('已复制，可粘贴给 Codex'); } catch { toast('无法访问剪贴板。请在 Codex 中说明：按当前项目剧本完成分镜并更新画布。', true); }
});
$('#reloadProjectBtn').addEventListener('click', async () => { if (state.dirty && !confirm('载入新版本会替换当前未保存编辑。建议先导出项目备份。确定载入？')) return; try { setProject(await api('/api/project')); toast('已载入最新项目'); } catch (error) { toast(errorText(error), true); } });
const viewport = $('#canvasViewport');
viewport.addEventListener('pointerdown', (event) => {
  if (event.target.closest('.shot-node,button') || ![0, 1].includes(event.button)) return;
  event.preventDefault(); const original = { ...state.transform }, x = event.clientX, y = event.clientY; viewport.setPointerCapture(event.pointerId); viewport.classList.add('panning');
  function move(next) { state.transform.x = original.x + next.clientX - x; state.transform.y = original.y + next.clientY - y; applyTransform(); }
  function end() { viewport.classList.remove('panning'); viewport.removeEventListener('pointermove', move); viewport.removeEventListener('pointerup', end); viewport.removeEventListener('pointercancel', end); }
  viewport.addEventListener('pointermove', move); viewport.addEventListener('pointerup', end); viewport.addEventListener('pointercancel', end);
});
viewport.addEventListener('wheel', (event) => { event.preventDefault(); const box = viewport.getBoundingClientRect(); zoomAt(state.transform.scale * Math.exp(-event.deltaY * .0012), event.clientX - box.left, event.clientY - box.top); }, { passive: false });
window.addEventListener('keydown', (event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); saveProject(); } });
window.addEventListener('beforeunload', (event) => { if (state.dirty || state.paramError) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('resize', updateTrayPosition);
new ResizeObserver(updateTrayPosition).observe($('.jobs-tray'));

async function init() {
  if (!/Mac|iPhone|iPad/.test(navigator.platform)) $('#saveBtn kbd').textContent = 'Ctrl S';
  applyTransform();
  const results = await Promise.allSettled([api('/api/project'), api('/api/models'), loadStatus(), api('/api/jobs')]);
  if (results[1].status === 'fulfilled') { const catalog = results[1].value; state.models = { image: catalog.image || [], video: catalog.video || [] }; }
  else toast(`模型目录读取失败：${errorText(results[1].reason)}`, true);
  if (results[3].status === 'fulfilled') { const result = results[3].value; state.jobs = (Array.isArray(result) ? result : result.jobs || []).sort((a, b) => String(b.createdAt || '').localeCompare(String(a.createdAt || ''))); }
  if (results[0].status === 'fulfilled') { setProject(results[0].value, { fit: true }); }
  else { $('#saveState').textContent = '项目读取失败'; toast(`无法打开项目：${errorText(results[0].reason)}`, true); }
  renderJobs(); state.loading = false; updateGenerateButton(); setInterval(poll, 6000);
}
init().catch((error) => { state.loading = false; toast(errorText(error), true); });
