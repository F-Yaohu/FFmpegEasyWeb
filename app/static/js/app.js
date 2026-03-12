const S = { files: { 'convert': { fileId: null, filename: null, info: {} }, 'cut': { fileId: null, filename: null, info: {} }, 'mux-video': { fileId: null, filename: null, info: {} }, 'mux-audio': { fileId: null, filename: null, info: {} }, 'cover-main': { fileId: null, filename: null, info: {} }, 'cover-img': { fileId: null, filename: null, info: {} } }, convertFormat: null, fmtType: 'video', formats: {}, maxFileSize: 500, cutCopy: true, mergeFiles: [], coverAction: 'set', keepCover: false, polling: {}, history: [], accessKey: '', authEnabled: false, dpTimers: {}, selectedHistory: new Set(), runningTasks: new Set() };
const PRESETS = { video: [{ label: '压缩 CRF28', args: '-crf 28 -preset fast' }, { label: '高质量 CRF18', args: '-crf 18 -preset slow' }, { label: '缩放 1080p', args: '-vf scale=1920:1080' }, { label: '缩放 720p', args: '-vf scale=1280:720' }, { label: '缩放 480p', args: '-vf scale=854:480' }, { label: '去掉音频', args: '-an' }, { label: '提取音频', args: '-vn' }, { label: '2倍速', args: '-filter_complex "[0:v]setpts=0.5*PTS[v];[0:a]atempo=2.0[a]" -map [v] -map [a]' }], audio: [{ label: '320k', args: '-b:a 320k' }, { label: '192k', args: '-b:a 192k' }, { label: '128k', args: '-b:a 128k' }, { label: '44100 Hz', args: '-ar 44100' }, { label: '单声道', args: '-ac 1' }, { label: '立体声', args: '-ac 2' }], image: [{ label: '高质量', args: '-q:v 2' }, { label: '缩放 50%', args: '-vf scale=iw/2:ih/2' }, { label: '水平翻转', args: '-vf hflip' }, { label: '垂直翻转', args: '-vf vflip' }, { label: '旋转 90°', args: '-vf transpose=1' }] };

// ── 页面加载 ──
document.addEventListener('DOMContentLoaded', async () => {
    await checkHealth(); await loadFormats(); await loadMaxFileSize(); renderFormats(); renderPresets();
    ['convert', 'cut', 'mux-video', 'mux-audio', 'cover-main', 'cover-img'].forEach(ns => setupDrop(ns));
    setInterval(checkHealth, 30000);
    document.getElementById('auth-input').addEventListener('keydown', e => { if (e.key === 'Enter') submitAuth(); });
    // 加载历史记录
    await loadHistory();
    // 设置退出提示
    setupBeforeUnload();
});

// ── 退出提示 ──
function setupBeforeUnload() {
    window.addEventListener('beforeunload', (e) => {
        // 检查是否有正在运行的任务
        const runningCount = S.runningTasks.size;
        if (runningCount > 0) {
            const msg = `有 ${runningCount} 个任务正在处理中，离开页面将中断任务。确定要离开吗？`;
            e.preventDefault();
            e.returnValue = msg;
            return msg;
        }
    });
}

// ── 确认对话框 ──
function showConfirmDialog(title, msg, icon, onConfirm) {
    document.getElementById('confirm-title').textContent = title;
    document.getElementById('confirm-msg').textContent = msg;
    document.getElementById('confirm-icon').textContent = icon || '⚠️';
    document.getElementById('confirm-btn').onclick = () => { closeConfirmDialog(); onConfirm(); };
    document.getElementById('confirm-dialog').classList.add('show');
}
function closeConfirmDialog() {
    document.getElementById('confirm-dialog').classList.remove('show');
}

// ── 鉴权 ──
async function checkHealth() {
    try {
        const d = await (await fetch('/api/health')).json();
        const dot = document.getElementById('statusDot'), txt = document.getElementById('statusText');
        if (d.status === 'ok' && d.ffmpeg) { dot.className = 'status-dot ok'; txt.textContent = 'FFmpeg 就绪'; }
        else { dot.className = 'status-dot err'; txt.textContent = 'FFmpeg 不可用'; }
        S.authEnabled = !!d.auth_enabled;
        if (S.authEnabled && !S.accessKey) { document.getElementById('auth-overlay').classList.remove('hidden'); }
    } catch { document.getElementById('statusDot').className = 'status-dot err'; document.getElementById('statusText').textContent = '服务离线'; }
}
async function submitAuth() {
    const key = document.getElementById('auth-input').value.trim();
    if (!key) { document.getElementById('auth-err').textContent = '请输入密钥'; return; }
    try {
        const r2 = await fetch('/api/probe', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Access-Key': key }, body: JSON.stringify({ file_id: 'test' }) });
        if (r2.status === 401) { document.getElementById('auth-err').textContent = '密钥错误，请重新输入'; document.getElementById('auth-input').select(); return; }
        S.accessKey = key; document.getElementById('auth-overlay').classList.add('hidden'); document.getElementById('auth-tag').style.display = ''; showToast('验证成功，欢迎使用', 'success');
        // 验证成功后加载历史记录
        await loadHistory();
    } catch { document.getElementById('auth-err').textContent = '网络错误，请重试'; }
}
function lockApp() { S.accessKey = ''; document.getElementById('auth-input').value = ''; document.getElementById('auth-err').textContent = ''; document.getElementById('auth-overlay').classList.remove('hidden'); document.getElementById('auth-tag').style.display = 'none'; }

function authFetch(url, opts = {}) {
    if (S.accessKey) { opts.headers = opts.headers || {}; opts.headers['X-Access-Key'] = S.accessKey; if (opts.body && typeof opts.body === 'string') { try { const b = JSON.parse(opts.body); b.access_key = S.accessKey; opts.body = JSON.stringify(b); } catch { } } }
    return fetch(url, opts);
}

async function loadFormats() { try { S.formats = await (await fetch('/api/formats')).json(); } catch { S.formats = { video: ['mp4', 'avi', 'mov', 'mkv', 'webm'], audio: ['mp3', 'wav', 'aac', 'flac', 'ogg'], image: ['png', 'jpg', 'webp'] }; } }
async function loadMaxFileSize() { try { S.maxFileSize = await (await fetch('/api/maxFileSize').size); } catch { S.maxFileSize = 500; } }

function switchTab(tab, el) { document.querySelectorAll('.func-tab').forEach(t => t.classList.remove('active')); el.classList.add('active'); document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active')); document.getElementById('panel-' + tab).classList.add('active'); }
function renderFormats() { const grid = document.getElementById('fg-convert'); const fmts = S.formats[S.fmtType] || []; grid.innerHTML = fmts.map(f => `<button class="fmt-btn${S.convertFormat === f ? ' selected' : ''}" onclick="selectFmt('${f}')">${f}</button>`).join(''); }
function renderPresets() { const chips = document.getElementById('pc-convert'); chips.innerHTML = ''; (PRESETS[S.fmtType] || []).forEach(p => { const b = document.createElement('button'); b.className = 'preset-chip'; b.textContent = p.label; b.onclick = () => { document.getElementById('ea-convert').value = p.args; dp('convert'); showToast('已应用预设', 'info'); }; chips.appendChild(b); }); }
function switchFmtType(type, el) { S.fmtType = type; S.convertFormat = null; document.querySelectorAll('#ft-convert .format-tab').forEach(t => t.classList.remove('active')); el.classList.add('active'); renderFormats(); renderPresets(); updateConvertBtn(); }
function selectFmt(f) { S.convertFormat = f; renderFormats(); updateConvertBtn(); dp('convert'); }
function toggleAdv() { document.getElementById('adv-h').classList.toggle('open'); document.getElementById('adv-b').classList.toggle('open'); }
function setupDrop(ns) { const zone = document.getElementById('dz-' + ns), inp = document.getElementById('fi-' + ns); if (!zone || !inp) return; zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); }); zone.addEventListener('dragleave', () => zone.classList.remove('drag-over')); zone.addEventListener('drop', e => { e.preventDefault(); zone.classList.remove('drag-over'); const f = e.dataTransfer.files[0]; if (f) uploadFile(ns, f); }); inp.addEventListener('change', e => { const f = e.target.files[0]; if (f) uploadFile(ns, f); }); }
async function uploadFile(ns, file) { if (file.size > S.maxFileSize * 1024 * 1024) { showToast('文件超过 ' + S.maxFileSize + ' MB 限制', 'error'); return; } showUO(ns, true); const fd = new FormData(); fd.append('file', file); if (S.accessKey) fd.append('access_key', S.accessKey); try { const r = await fetch('/api/upload', { method: 'POST', headers: S.accessKey ? { 'X-Access-Key': S.accessKey } : {}, body: fd }); const d = await r.json(); if (!r.ok) { showToast(d.error || '上传失败', 'error'); return; } S.files[ns] = { fileId: d.file_id, filename: d.filename, info: d.info || {} }; showFIC(ns, file, d); afterUpload(ns); showToast('上传成功', 'success'); } catch (e) { showToast('上传失败：' + e.message, 'error'); } finally { showUO(ns, false); } }
function showUO(ns, show) { const uo = document.getElementById('uo-' + ns), fi = document.getElementById('fi-' + ns); if (uo) uo.classList.toggle('show', show); if (fi) fi.disabled = show; }
function showFIC(ns, file, data) { const dz = document.getElementById('dz-' + ns), fic = document.getElementById('fic-' + ns); if (!fic) return; if (dz) dz.style.display = 'none'; fic.classList.add('show'); const ext = file.name.split('.').pop().toLowerCase(); const isV = ['mp4', 'avi', 'mov', 'mkv', 'flv', 'wmv', 'webm', 'mpeg', 'ts'].includes(ext); const isA = ['mp3', 'wav', 'aac', 'flac', 'ogg', 'm4a', 'opus', 'wma'].includes(ext); const ig = document.getElementById('fig-' + ns); if (ig) ig.textContent = isV ? '🎬' : isA ? '🎵' : '🖼️'; const fn = document.getElementById('fn-' + ns); if (fn) fn.textContent = file.name; const info = data.info || {}; const det = [`📦 ${fmtSize(file.size)}`]; if (info.duration) det.push(`⏱ ${fmtDur(info.duration)}`); if (info.video) det.push(`📐 ${info.video.width}×${info.video.height}`); if (info.video) det.push(`🎥 ${info.video.codec?.toUpperCase()}`); if (info.audio) det.push(`🔊 ${info.audio.codec?.toUpperCase()}`); if (info.has_cover) det.push('🖼 有封面'); const fd = document.getElementById('fd-' + ns); if (fd) fd.innerHTML = det.map(d => `<span>${d}</span>`).join(''); }
function removeFile(ns) { S.files[ns] = { fileId: null, filename: null, info: {} }; const dz = document.getElementById('dz-' + ns), fic = document.getElementById('fic-' + ns), fi = document.getElementById('fi-' + ns); if (dz) dz.style.display = ''; if (fic) fic.classList.remove('show'); if (fi) fi.value = ''; afterUpload(ns); }
function afterUpload(ns) { if (ns === 'convert') { updateConvertBtn(); dp('convert'); } else if (ns === 'cut') { updateBtn('cut', !!S.files['cut'].fileId); dp('cut'); } else if (ns.startsWith('mux')) { updateBtn('mux', !!S.files['mux-video'].fileId); dp('mux'); } else if (ns.startsWith('cover')) { updateCoverBtn(); dp('cover'); } }
function updateConvertBtn() { document.getElementById('btn-convert').disabled = !(S.files['convert'].fileId && S.convertFormat); }
function updateBtn(id, enabled) { document.getElementById('btn-' + id).disabled = !enabled; }
function updateCoverBtn() { const mainOk = !!S.files['cover-main'].fileId; const coverOk = S.coverAction !== 'set' || !!S.files['cover-img'].fileId; document.getElementById('btn-cover').disabled = !(mainOk && coverOk); }

// ── 命令预览 ──
function dp(ns, delay = 400) { clearTimeout(S.dpTimers[ns]); S.dpTimers[ns] = setTimeout(() => fetchPreview(ns), delay); }
async function fetchPreview(ns) {
    let payload = null;
    if (ns === 'convert') { if (!S.files['convert'].fileId || !S.convertFormat) { document.getElementById('cpw-convert').style.display = 'none'; return; } const args = (document.getElementById('ea-convert').value.trim() || '').split(/\s+/).filter(Boolean); payload = { endpoint: 'convert', mode: 'convert', file_id: S.files['convert'].fileId, filename: S.files['convert'].filename || 'input', output_format: S.convertFormat, extra_args: args }; }
    else if (ns === 'cut') { const f = S.files['cut']; if (!f.fileId) { document.getElementById('cpw-cut').style.display = 'none'; return; } const fmt = document.getElementById('cut-fmt').value || (f.filename || '').split('.').pop().toLowerCase() || 'mp4'; payload = { endpoint: 'convert', mode: 'cut', file_id: f.fileId, filename: f.filename || 'input', output_format: fmt, ss: document.getElementById('cut-ss').value.trim(), to: document.getElementById('cut-to').value.trim(), t: document.getElementById('cut-t').value.trim(), copy: S.cutCopy, extra_args: [] }; }
    else if (ns === 'merge') { if (S.mergeFiles.length < 2) { document.getElementById('cpw-merge').style.display = 'none'; return; } payload = { endpoint: 'merge', file_ids: S.mergeFiles.map(f => f.fileId), output_format: document.getElementById('merge-fmt').value, extra_args: [] }; }
    else if (ns === 'mux') { const fv = S.files['mux-video']; if (!fv.fileId) { document.getElementById('cpw-mux').style.display = 'none'; return; } const fa = S.files['mux-audio']; payload = { endpoint: 'convert', mode: 'mux', file_id: fv.fileId, filename: fv.filename || 'video', output_format: document.getElementById('mux-fmt').value, audio_file_id: fa.fileId || '', audio_filename: fa.filename || 'audio', mux_mode: document.getElementById('mux-mode').value, video_vol: parseFloat(document.getElementById('mux-vol-v').value) || 1, audio_vol: parseFloat(document.getElementById('mux-vol-a').value) || 1, extra_args: [] }; }
    else if (ns === 'cover') { const fm = S.files['cover-main']; if (!fm.fileId) { document.getElementById('cpw-cover').style.display = 'none'; return; } const fi = S.files['cover-img']; let fmt; if (S.coverAction === 'extract') fmt = document.getElementById('cover-img-fmt').value; else if (S.coverAction === 'extract_audio') fmt = document.getElementById('cover-audio-fmt').value; else fmt = (fm.filename || '').split('.').pop().toLowerCase() || 'mp3'; payload = { endpoint: 'convert', mode: 'cover', file_id: fm.fileId, filename: fm.filename || 'input', output_format: fmt, cover_action: S.coverAction, cover_file_id: fi.fileId || '', cover_filename: fi.filename || 'cover', keep_cover: S.keepCover, extra_args: [] }; }
    if (!payload) return;
    const cpw = document.getElementById('cpw-' + ns); const cl = document.getElementById('cl-' + ns); cpw.style.display = ''; if (cl) cl.textContent = '加载中...';
    try {
        const r = await authFetch('/api/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const d = await r.json();
        if (r.ok) {
            const ta = document.getElementById('cmd-' + ns);
            if (ta) { let display = d.command || ''; if (d.concat_list) display += '\n\n# concat 列表:\n' + d.concat_list; ta.value = display; ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'; }
            const cmdNote = document.getElementById('cmd-note-' + ns);
            if (cmdNote) { if (ns === 'cover' && S.coverAction === 'extract_audio' && S.keepCover) { cmdNote.textContent = '⚠ 保留封面时输出已自动改为 M4A'; cmdNote.className = 'cmd-note warn'; } else { cmdNote.textContent = '以上为实际将执行的 FFmpeg 命令（路径已脱敏），可复制到本地运行学习。'; cmdNote.className = 'cmd-note'; } }
            if (cl) cl.textContent = '';
        } else { if (cl) cl.textContent = ''; }
    } catch { if (cl) cl.textContent = ''; }
}
function copyCmd(ns) { const ta = document.getElementById('cmd-' + ns); if (!ta) return; navigator.clipboard.writeText(ta.value).then(() => showToast('命令已复制', 'success')).catch(() => { ta.select(); document.execCommand('copy'); showToast('命令已复制', 'success'); }); }

// ── 获取自定义文件名 ──
function getCustomName(ns, defaultName) {
    const input = document.getElementById('cn-' + ns);
    if (!input) return defaultName;
    const val = input.value.trim();
    return val || defaultName;
}

// ── 格式转换 ──
async function startConvert() { const f = S.files['convert']; if (!f.fileId || !S.convertFormat) return; const args = (document.getElementById('ea-convert').value.trim() || '').split(/\s+/).filter(Boolean); const customName = getCustomName('convert', null); resetProg('convert'); showProg('convert', true); document.getElementById('btn-convert').disabled = true; try { const body = { mode: 'convert', file_id: f.fileId, filename: f.filename, output_format: S.convertFormat, extra_args: args, duration: f.info?.duration || 0 }; if (customName) body.custom_name = customName; const r = await authFetch('/api/convert', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); const d = await r.json(); if (!r.ok) { showToast(d.error || '请求失败', 'error'); document.getElementById('btn-convert').disabled = false; showProg('convert', false); return; } S.runningTasks.add(d.task_id); pollTask(d.task_id, 'convert'); } catch (e) { showToast('请求失败：' + e.message, 'error'); document.getElementById('btn-convert').disabled = false; showProg('convert', false); } }

// ── 剪切 ──
function toggleCutCopy() { S.cutCopy = !S.cutCopy; document.getElementById('cut-copy-toggle').classList.toggle('on', S.cutCopy); dp('cut'); }
async function startCut() { const f = S.files['cut']; if (!f.fileId) return; const ss = document.getElementById('cut-ss').value.trim(); const to = document.getElementById('cut-to').value.trim(); const t = document.getElementById('cut-t').value.trim(); let fmt = document.getElementById('cut-fmt').value || ((f.filename || '').split('.').pop().toLowerCase()) || 'mp4'; const customName = getCustomName('cut', null); resetProg('cut'); showProg('cut', true); document.getElementById('btn-cut').disabled = true; try { const body = { mode: 'cut', file_id: f.fileId, filename: f.filename, output_format: fmt, ss, to, t, copy: S.cutCopy, duration: f.info?.duration || 0, extra_args: [] }; if (customName) body.custom_name = customName; const r = await authFetch('/api/convert', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); const d = await r.json(); if (!r.ok) { showToast(d.error || '请求失败', 'error'); document.getElementById('btn-cut').disabled = false; showProg('cut', false); return; } S.runningTasks.add(d.task_id); pollTask(d.task_id, 'cut'); } catch (e) { showToast('请求失败：' + e.message, 'error'); document.getElementById('btn-cut').disabled = false; showProg('cut', false); } }

// ── 合并 ──
async function onMergeFileSelect(evt) { const files = Array.from(evt.target.files); document.getElementById('uo-merge').classList.add('show'); for (const file of files) { if (S.mergeFiles.length >= 10) { showToast('最多添加 10 个文件', 'error'); break; } const fd = new FormData(); fd.append('file', file); if (S.accessKey) fd.append('access_key', S.accessKey); try { const r = await fetch('/api/upload', { method: 'POST', headers: S.accessKey ? { 'X-Access-Key': S.accessKey } : {}, body: fd }); const d = await r.json(); if (r.ok) S.mergeFiles.push({ fileId: d.file_id, filename: d.filename, size: d.size }); else showToast(d.error || '上传失败', 'error'); } catch (e) { showToast('上传失败：' + e.message, 'error'); } } document.getElementById('uo-merge').classList.remove('show'); evt.target.value = ''; renderMergeList(); updateBtn('merge', S.mergeFiles.length >= 2); if (S.mergeFiles.length >= 2) dp('merge'); }
function renderMergeList() { const list = document.getElementById('merge-list'); if (!S.mergeFiles.length) { list.innerHTML = '<div class="empty-state"><span class="empty-icon">📂</span>点击下方按钮添加文件</div>'; return; } list.innerHTML = S.mergeFiles.map((f, i) => { const ext = f.filename.split('.').pop().toLowerCase(); const icon = ['mp4', 'avi', 'mov', 'mkv', 'flv', 'wmv', 'webm'].includes(ext) ? '🎬' : '🎵'; return `<div class="merge-item" draggable="true" ondragstart="mds(${i})" ondragover="mdo(event,${i})" ondrop="mdp(event,${i})" ondragleave="mdl(${i})"><span class="merge-order">${i + 1}</span><span class="merge-drag-handle">⠿</span><span class="merge-item-icon">${icon}</span><span class="merge-item-name">${escH(f.filename)}</span><span class="merge-item-size">${fmtSize(f.size)}</span><button class="merge-item-remove" onclick="rmMerge(${i})">✕</button></div>`; }).join(''); }
let _ds = -1;
function mds(i) { _ds = i; } function mdo(e, i) { e.preventDefault(); document.querySelectorAll('.merge-item')[i]?.classList.add('drag-target'); } function mdl(i) { document.querySelectorAll('.merge-item')[i]?.classList.remove('drag-target'); }
function mdp(e, i) { e.preventDefault(); document.querySelectorAll('.merge-item')[i]?.classList.remove('drag-target'); if (_ds === i) return; const tmp = S.mergeFiles[_ds]; S.mergeFiles.splice(_ds, 1); S.mergeFiles.splice(i, 0, tmp); renderMergeList(); updateBtn('merge', S.mergeFiles.length >= 2); dp('merge'); }
function rmMerge(i) { S.mergeFiles.splice(i, 1); renderMergeList(); updateBtn('merge', S.mergeFiles.length >= 2); dp('merge'); }
async function startMerge() { if (S.mergeFiles.length < 2) return; const fmt = document.getElementById('merge-fmt').value; const customName = getCustomName('merge', null); resetProg('merge'); showProg('merge', true); document.getElementById('btn-merge').disabled = true; try { const body = { file_ids: S.mergeFiles.map(f => f.fileId), output_format: fmt, extra_args: [] }; if (customName) body.custom_name = customName; const r = await authFetch('/api/merge', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); const d = await r.json(); if (!r.ok) { showToast(d.error || '请求失败', 'error'); document.getElementById('btn-merge').disabled = false; showProg('merge', false); return; } S.runningTasks.add(d.task_id); pollTask(d.task_id, 'merge'); } catch (e) { showToast('请求失败：' + e.message, 'error'); document.getElementById('btn-merge').disabled = false; showProg('merge', false); } }

// ── 混流 ──
function updateMuxUI() { const mix = document.getElementById('mux-mode').value === 'mix'; document.getElementById('mux-vol-v-g').style.display = mix ? '' : 'none'; document.getElementById('mux-vol-a-g').style.display = mix ? '' : 'none'; }
async function startMux() { const fv = S.files['mux-video'], fa = S.files['mux-audio']; if (!fv.fileId) return; const mode = document.getElementById('mux-mode').value, fmt = document.getElementById('mux-fmt').value; const vv = parseFloat(document.getElementById('mux-vol-v').value) || 1, av = parseFloat(document.getElementById('mux-vol-a').value) || 1; const customName = getCustomName('mux', null); resetProg('mux'); showProg('mux', true); document.getElementById('btn-mux').disabled = true; try { const body = { mode: 'mux', file_id: fv.fileId, filename: fv.filename, output_format: fmt, audio_file_id: fa.fileId || '', mux_mode: mode, video_vol: vv, audio_vol: av, duration: fv.info?.duration || 0, extra_args: [] }; if (customName) body.custom_name = customName; const r = await authFetch('/api/convert', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); const d = await r.json(); if (!r.ok) { showToast(d.error || '请求失败', 'error'); document.getElementById('btn-mux').disabled = false; showProg('mux', false); return; } S.runningTasks.add(d.task_id); pollTask(d.task_id, 'mux'); } catch (e) { showToast('请求失败：' + e.message, 'error'); document.getElementById('btn-mux').disabled = false; showProg('mux', false); } }

// ── 封面管理 ──
function switchCoverAction(action, el) { S.coverAction = action; document.querySelectorAll('#cover-action-tabs .format-tab').forEach(t => t.classList.remove('active')); el.classList.add('active');['set', 'extract', 'extract_audio'].forEach(a => { const e = document.getElementById('cover-desc-' + a); if (e) e.style.display = a === action ? '' : 'none'; }); const hints = { set: { icon: '🎵', title: '点击或拖拽音频文件', hint: '支持 MP3, AAC, M4A 等音频格式' }, extract: { icon: '🎬', title: '点击或拖拽视频或音频文件', hint: '从中提取封面图片' }, extract_audio: { icon: '🎬', title: '点击或拖拽视频文件', hint: '将从视频中提取音频流' } }; const h = hints[action]; document.getElementById('cv-main-icon').textContent = h.icon; document.getElementById('cv-main-title').textContent = h.title; document.getElementById('cv-main-hint').textContent = h.hint; document.getElementById('cover-img-card').style.display = action === 'set' ? '' : 'none'; document.getElementById('cover-audio-opts').style.display = action === 'extract_audio' ? '' : 'none'; document.getElementById('cover-extract-opts').style.display = action === 'extract' ? '' : 'none'; document.getElementById('cover-exec-step').textContent = action === 'set' ? '4' : '3'; document.getElementById('cover-exec-step2').textContent = action === 'set' ? '5' : '4'; updateCoverBtn(); dp('cover'); }
function toggleKeepCover() { S.keepCover = !S.keepCover; document.getElementById('cover-keep-toggle').classList.toggle('on', S.keepCover); dp('cover'); }
async function startCover() { const fm = S.files['cover-main'], fi = S.files['cover-img']; if (!fm.fileId) return; let fmt; if (S.coverAction === 'extract') fmt = document.getElementById('cover-img-fmt').value; else if (S.coverAction === 'extract_audio') fmt = document.getElementById('cover-audio-fmt').value; else fmt = (fm.filename || '').split('.').pop().toLowerCase() || 'mp3'; const customName = getCustomName('cover', null); resetProg('cover'); showProg('cover', true); document.getElementById('btn-cover').disabled = true; try { const body = { mode: 'cover', file_id: fm.fileId, filename: fm.filename, output_format: fmt, cover_action: S.coverAction, cover_file_id: fi.fileId || '', keep_cover: S.keepCover, duration: fm.info?.duration || 0, extra_args: [] }; if (customName) body.custom_name = customName; const r = await authFetch('/api/convert', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); const d = await r.json(); if (!r.ok) { showToast(d.error || '请求失败', 'error'); document.getElementById('btn-cover').disabled = false; showProg('cover', false); return; } S.runningTasks.add(d.task_id); pollTask(d.task_id, 'cover'); } catch (e) { showToast('请求失败：' + e.message, 'error'); document.getElementById('btn-cover').disabled = false; showProg('cover', false); } }

// ── 进度轮询 ──
function pollTask(taskId, ns) { if (S.polling[ns]) clearInterval(S.polling[ns]); S.polling[ns] = setInterval(async () => { try { const d = await (await authFetch('/api/task/' + taskId)).json(); updProg(ns, d, taskId); if (d.status === 'done' || d.status === 'error') { clearInterval(S.polling[ns]); S.polling[ns] = null; S.runningTasks.delete(taskId); const btn = document.getElementById('btn-' + ns); if (btn) btn.disabled = false; addHistory(d, taskId); } } catch { } }, 800); }
function updProg(ns, task, taskId) { const pct = task.progress || 0; document.getElementById('pb-' + ns).style.width = pct + '%'; document.getElementById('pp-' + ns).textContent = pct + '%'; const sp = document.getElementById('sp-' + ns), pl = document.getElementById('pl-' + ns); if (task.status === 'done') { if (sp) sp.style.display = 'none'; pl.textContent = '完成 🎉'; document.getElementById('pb-' + ns).classList.add('done'); document.getElementById('rr-' + ns).classList.add('show'); document.getElementById('rn-' + ns).textContent = task.custom_name || task.original_name || '输出文件'; const dl = document.getElementById('dl-' + ns); dl.href = '/api/download/' + taskId + (S.accessKey ? "?access_key=" + S.accessKey : ""); dl.setAttribute('download', task.custom_name || task.original_name || 'output'); showToast('处理完成！', 'success'); } else if (task.status === 'error') { if (sp) sp.style.display = 'none'; pl.textContent = '处理失败'; document.getElementById('pb-' + ns).classList.add('error'); const el = document.getElementById('el-' + ns); el.textContent = Array.isArray(task.log) ? task.log.join('\n') : (task.log || '未知错误'); el.classList.add('show'); showToast('处理失败，查看错误日志', 'error'); } else { pl.textContent = '处理中...'; } }
function showProg(ns, show) { document.getElementById('ps-' + ns)?.classList.toggle('show', show); }
function resetProg(ns) { const pb = document.getElementById('pb-' + ns); if (pb) { pb.style.width = '0%'; pb.className = 'progress-bar-fill'; } const pp = document.getElementById('pp-' + ns); if (pp) pp.textContent = '0%'; const pl = document.getElementById('pl-' + ns); if (pl) pl.textContent = '处理中...'; const sp = document.getElementById('sp-' + ns); if (sp) sp.style.display = ''; document.getElementById('rr-' + ns)?.classList.remove('show'); const el = document.getElementById('el-' + ns); if (el) { el.classList.remove('show'); el.textContent = ''; } showProg(ns, false); }

// ── 历史记录管理 ──

// 加载历史记录
async function loadHistory() {
    try {
        const r = await authFetch('/api/tasks?limit=100');
        if (!r.ok) { showToast('加载历史记录失败', 'error'); return; }
        const data = await r.json();
        S.history = data.tasks || [];
        renderHistory();
    } catch (e) { console.error('加载历史记录失败:', e); }
}

// 刷新历史记录
async function refreshHistory() {
    await loadHistory();
    showToast('历史记录已刷新', 'success');
}

function addHistory(task, taskId) {
    // 检查是否已存在
    const existingIdx = S.history.findIndex(h => h.task_id === taskId);
    const historyItem = {
        task_id: taskId,
        name: task.custom_name || task.original_name || '输出文件',
        status: task.status,
        time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
        created_at: task.created_at || Date.now() / 1000,
        updated_at: Date.now() / 1000,
        progress: task.progress,
        output_file: task.output_file,
        custom_name: task.custom_name,
        original_name: task.original_name,
        mode: task.mode || 'convert',
        file_size: task.file_size || 0
    };
    if (existingIdx >= 0) { S.history[existingIdx] = historyItem; }
    else { S.history.unshift(historyItem); }
    renderHistory();
}

function renderHistory() {
    const list = document.getElementById('historyList');
    if (!S.history.length) { list.innerHTML = '<div class="empty-state"><span class="empty-icon">📋</span>暂无任务记录</div>'; updateDeleteButton(); return; }
    list.innerHTML = S.history.map(h => {
        const isSelected = S.selectedHistory.has(h.task_id);
        const isDone = h.status === 'done';
        const isError = h.status === 'error';
        const isRunning = h.status === 'running' || h.status === 'pending';
        const icon = isDone ? '✅' : isError ? '❌' : '⏳';
        const sc = isDone ? 'done' : isError ? 'error' : isRunning ? 'running' : 'pending';
        const st = isDone ? '完成' : isError ? '失败' : isRunning ? '处理中' : '等待中';
        const timeStr = h.time || new Date(h.created_at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
        const sizeStr = h.file_size ? ` · ${fmtSize(h.file_size)}` : '';
        // 构建操作按钮
        let actions = '';
        if (isDone) {
            actions += `<a class="history-btn history-btn-download" href="/api/download/${h.task_id}${S.accessKey ? '?access_key=' + S.accessKey : ''}" download="${h.custom_name || h.original_name || 'output'}">⬇ 下载</a>`;
        }
        actions += `<button class="history-btn history-btn-delete" onclick="deleteHistoryItem('${h.task_id}')">🗑️ 删除</button>`;
        const displayName = h.custom_name || h.original_name || '输出文件';
        return `<div class="history-item" data-task-id="${h.task_id}"><input type="checkbox" class="history-checkbox" ${isSelected ? 'checked' : ''} ${!isDone && !isError ? 'disabled' : ''} onchange="toggleHistorySelect('${h.task_id}')"><div class="history-icon">${icon}</div><div class="history-meta"><div class="history-name">${escH(displayName)}</div><div class="history-sub">${timeStr}${sizeStr} · ${h.mode || 'convert'}</div></div><span class="history-status ${sc}">${st}</span><div class="history-actions-group">${actions}</div></div>`;
    }).join('');
    updateDeleteButton();
}

function toggleHistorySelect(taskId) {
    if (S.selectedHistory.has(taskId)) { S.selectedHistory.delete(taskId); }
    else { S.selectedHistory.add(taskId); }
    updateDeleteButton();
    updateSelectAllCheckbox();
}

function updateDeleteButton() {
    const btn = document.getElementById('btn-delete-selected');
    btn.disabled = S.selectedHistory.size === 0;
    btn.textContent = `🗑️ 清理所选 (${S.selectedHistory.size})`;
}

function updateSelectAllCheckbox() {
    const checkbox = document.getElementById('select-all-history');
    const doneTasks = S.history.filter(h => h.status === 'done' || h.status === 'error');
    if (doneTasks.length === 0) { checkbox.checked = false; checkbox.indeterminate = false; return; }
    const selectedDone = doneTasks.filter(h => S.selectedHistory.has(h.task_id)).length;
    if (selectedDone === 0) { checkbox.checked = false; checkbox.indeterminate = false; }
    else if (selectedDone === doneTasks.length) { checkbox.checked = true; checkbox.indeterminate = false; }
    else { checkbox.checked = false; checkbox.indeterminate = true; }
}

function toggleSelectAllHistory() {
    const checkbox = document.getElementById('select-all-history');
    const doneTasks = S.history.filter(h => h.status === 'done' || h.status === 'error');
    if (checkbox.checked) {
        doneTasks.forEach(h => S.selectedHistory.add(h.task_id));
    } else {
        doneTasks.forEach(h => S.selectedHistory.delete(h.task_id));
    }
    renderHistory();
}

async function deleteSelectedHistory() {
    if (S.selectedHistory.size === 0) return;
    showConfirmDialog('确认清理', `确定要删除选中的 ${S.selectedHistory.size} 个任务及其文件吗？此操作不可恢复。`, '🗑️', async () => {
        const taskIds = Array.from(S.selectedHistory);
        try {
            const r = await authFetch('/api/tasks', { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task_ids: taskIds }) });
            if (!r.ok) { showToast('删除失败', 'error'); return; }
            const data = await r.json();
            S.selectedHistory.clear();
            await loadHistory();
            showToast(`已删除 ${data.deleted_count} 个任务`, 'success');
        } catch (e) { showToast('删除失败：' + e.message, 'error'); }
    });
}

async function deleteHistoryItem(taskId) {
    showConfirmDialog('确认删除', '确定要删除此任务及其文件吗？此操作不可恢复。', '🗑️', async () => {
        try {
            const r = await authFetch('/api/task/' + taskId, { method: 'DELETE' });
            if (!r.ok) { showToast('删除失败', 'error'); return; }
            S.selectedHistory.delete(taskId);
            await loadHistory();
            showToast('任务已删除', 'success');
        } catch (e) { showToast('删除失败：' + e.message, 'error'); }
    });
}

async function clearAllHistory() {
    const doneTasks = S.history.filter(h => h.status === 'done' || h.status === 'error');
    if (doneTasks.length === 0) { showToast('没有可清理的任务', 'info'); return; }
    showConfirmDialog('确认清空', `确定要清空所有 ${doneTasks.length} 个已完成/失败的任务及其文件吗？此操作不可恢复。`, '⚠️', async () => {
        const taskIds = doneTasks.map(h => h.task_id);
        try {
            const r = await authFetch('/api/tasks', { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task_ids: taskIds }) });
            if (!r.ok) { showToast('清空失败', 'error'); return; }
            const data = await r.json();
            S.selectedHistory.clear();
            await loadHistory();
            showToast(`已清空 ${data.deleted_count} 个任务`, 'success');
        } catch (e) { showToast('清空失败：' + e.message, 'error'); }
    });
}

// ── Toast ──
let _tt;
function showToast(msg, type = 'info') { const el = document.getElementById('toast'); el.textContent = msg; el.className = `show ${type}`; clearTimeout(_tt); _tt = setTimeout(() => { el.className = ''; }, 3000); }

// ── 工具函数 ──
function fmtSize(b) { if (b < 1024) return b + ' B'; if (b < 1048576) return (b / 1024).toFixed(1) + ' KB'; if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB'; return (b / 1073741824).toFixed(2) + ' GB'; }
function fmtDur(s) { s = Math.floor(s); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60; if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`; return `${m}:${String(ss).padStart(2, '0')}`; }
function escH(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }