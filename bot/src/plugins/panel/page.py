"""内嵌单页（未构建 web/dist 时的兜底页面）。"""

_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>星潮 · 管理面板</title>
<style>
  :root { --bg:#0f1420; --card:#1a2130; --line:#2a3347; --fg:#e8edf7; --sub:#93a0b8;
          --acc:#6ea8fe; --ok:#5ad19c; --bad:#ff7b7b; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--fg); font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }
  .wrap { max-width:960px; margin:0 auto; padding:24px 16px; }
  h1 { font-size:20px; margin-bottom:4px; }
  .sub { color:var(--sub); font-size:12px; margin-bottom:20px; }
  .tabs { display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; }
  .tabs button { background:var(--card); color:var(--sub); border:1px solid var(--line);
                 padding:8px 16px; border-radius:8px; cursor:pointer; font-size:13px; }
  .tabs button.on { color:var(--fg); border-color:var(--acc); background:#20304d; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:14px; }
  .kv { display:flex; flex-wrap:wrap; gap:10px 24px; }
  .kv div span { display:block; color:var(--sub); font-size:12px; }
  .kv div b { font-size:18px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); }
  th { color:var(--sub); font-weight:500; }
  input, select, textarea { background:#111726; color:var(--fg); border:1px solid var(--line);
        border-radius:6px; padding:6px 8px; font-size:13px; }
  button.act { background:var(--acc); color:#0b1220; border:0; border-radius:6px; padding:6px 14px;
               cursor:pointer; font-size:13px; }
  button.mini { background:#20304d; color:var(--fg); border:1px solid var(--line); border-radius:6px;
                padding:4px 10px; cursor:pointer; font-size:12px; }
  .tag { font-size:11px; padding:1px 8px; border-radius:10px; }
  .tag.env { background:#233a2c; color:var(--ok); }
  .tag.rt { background:#20304d; color:var(--acc); }
  .tag.off { background:#3a2323; color:var(--bad); }
  pre.log { background:#111726; border:1px solid var(--line); border-radius:8px; padding:10px;
            max-height:420px; overflow:auto; font-size:12px; }
  .row { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
  .muted { color:var(--sub); }
  #msg { position:fixed; top:14px; right:14px; padding:8px 16px; border-radius:8px; display:none;
         background:#20304d; border:1px solid var(--acc); }
  .item { border:1px solid var(--line); border-radius:8px; padding:10px; margin-bottom:8px; }
  .item .grid { display:grid; grid-template-columns:110px 1fr 110px 90px 70px 32px; gap:6px; align-items:center; }
  @media (max-width:640px){ .item .grid { grid-template-columns:1fr 1fr; } }
</style>
</head>
<body>
<div id="msg"></div>
<div class="wrap" id="app"></div>
<script>
const $ = (h) => { const d = document.createElement('div'); d.innerHTML = h; return d.firstElementChild; };
const msg = (t, ok=true) => { const m = document.getElementById('msg');
  m.textContent = t; m.style.display='block'; m.style.borderColor = ok?'var(--acc)':'var(--bad)';
  setTimeout(()=>m.style.display='none', 2500); };
const fmtDur = (s) => { const h=Math.floor(s/3600), m=Math.floor(s%3600/60);
  return (h? h+' 小时 ':'') + m + ' 分'; };

async function api(path, opt={}) {
  const r = await fetch(path, opt);
  if (r.status === 401) { renderLogin(); throw new Error('unauthorized'); }
  return r.json();
}
const post = (p, body) => api(p, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});

/* ---------- 登录 ---------- */
function renderLogin() {
  const app = document.getElementById('app');
  app.innerHTML = '';
  const card = $(`<div class="card" style="max-width:360px;margin:80px auto;text-align:center">
    <h1>星潮 · 管理面板</h1>
    <p class="sub">请输入面板密码</p>
    <input id="pwd" type="password" style="width:100%;margin:10px 0" placeholder="面板密码">
    <button class="act" style="width:100%" onclick="doLogin()">登 录</button>
  </div>`);
  app.appendChild(card);
  card.querySelector('#pwd').addEventListener('keydown', e => { if (e.key==='Enter') doLogin(); });
}
async function doLogin() {
  const r = await fetch('/panel/api/login', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({password: document.getElementById('pwd').value})});
  if (r.ok) { renderMain(); } else { msg((await r.json()).error || '登录失败', false); }
}

/* ---------- 主界面 ---------- */
function renderMain() {
  const app = document.getElementById('app');
  app.innerHTML = '';
  // 注意：$() 只返回 firstElementChild，必须用一个根元素包住全部内容
  app.appendChild($(`<div>
    <h1>星潮 · 管理面板</h1><p class="sub" id="st"></p>
    <div class="tabs">
      <button data-t="status" class="on">状态</button>
      <button data-t="stats">统计</button>
      <button data-t="logs">日志</button>
      <button data-t="replies">词库</button>
      <button data-t="groups">白名单</button>
    </div><div id="view"></div>
  </div>`));
  document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
    document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('on'));
    b.classList.add('on'); switchTab(b.dataset.t);
  });
  switchTab('status');
}
function switchTab(t) {
  const v = document.getElementById('view'); v.innerHTML = '';
  if (t==='status') loadStatus(); if (t==='stats') loadStats();
  if (t==='logs') loadLogs(); if (t==='replies') loadReplies();
  if (t==='groups') loadGroups();
}

/* ---------- 状态 ---------- */
async function loadStatus() {
  const r = await api('/panel/api/status'); if (!r.ok) return msg(r.error,false);
  const d = r.data;
  document.getElementById('st').textContent = '数据日 ' + d.today;
  document.getElementById('view').appendChild($(`<div class="card"><div class="kv">
    <div><span>运行时长</span><b>${fmtDur(d.uptime_seconds)}</b></div>
    <div><span>白名单群</span><b>${d.whitelist.length}</b></div>
    <div><span>词条数</span><b>${d.replies}</b></div>
    <div><span>关键词模块</span><b class="tag ${d.reply_enabled?'':'off'}">${d.reply_enabled?'开启':'关闭'}</b></div>
    <div><span>进群欢迎</span><b class="tag ${d.welcome_enabled?'':'off'}">${d.welcome_enabled?'开启':'关闭'}</b></div>
    <div><span>插件</span><b style="font-size:13px">${d.plugins.join(' / ')}</b></div>
  </div></div>`));
}

/* ---------- 统计 ---------- */
async function loadStats(day) {
  day = day || new Date().toISOString().slice(0,10);
  const v = document.getElementById('view');
  v.innerHTML = '';
  const bar = $(`<div class="row"><input type="date" id="d" value="${day}">
     <button class="act" onclick="loadStats(document.getElementById('d').value)">查询</button></div>`);
  v.appendChild(bar);
  const r = await api('/panel/api/stats?day='+day); if (!r.ok) return msg(r.error,false);
  if (!r.data.groups.length) { v.appendChild($(`<div class="card muted">当日暂无消息记录</div>`)); return; }
  for (const g of r.data.groups) {
    const rows = g.top.map((t,i)=>`<tr><td>${i+1}</td><td>${t[0]}</td><td>${t[1]} 条</td></tr>`).join('');
    v.appendChild($(`<div class="card">
      <b>群 ${g.group_id}</b> <span class="muted">消息 ${g.total} · 参与 ${g.users} 人</span>
      <table style="margin-top:8px"><tr><th>#</th><th>用户</th><th>发言</th></tr>${rows}</table>
    </div>`));
  }
}

/* ---------- 日志 ---------- */
let LOGFILES = [];
async function loadLogs() {
  const v = document.getElementById('view'); v.innerHTML = '';
  const r = await api('/panel/api/logfiles'); if (!r.ok) return;
  LOGFILES = r.data;
  const opts = LOGFILES.map(f=>`<option>${f.name}</option>`).join('');
  v.appendChild($(`<div class="card">
    <div class="row"><select id="lf">${opts}</select>
      <input id="tail" type="number" value="200" style="width:90px" title="行数">
      <button class="act" onclick="showLog()">查看</button></div>
    <pre class="log" id="logbox">选择日志文件后查看</pre>
  </div>`));
}
async function showLog() {
  const name = document.getElementById('lf').value;
  const tail = document.getElementById('tail').value || 200;
  const r = await api('/panel/api/logs?name='+encodeURIComponent(name)+'&tail='+tail);
  if (!r.ok) return msg(r.error,false);
  const box = document.getElementById('logbox');
  box.textContent = r.data.records.map(x =>
    `[${(x.time||'').slice(11,19)}] 群${x.group_id} 用户${x.user_id}: ${x.raw_plain||''}`).join('\\n') || '(空)';
}

/* ---------- 词库 ---------- */
async function loadReplies() {
  const v = document.getElementById('view'); v.innerHTML = '';
  const r = await api('/panel/api/replies'); if (!r.ok) return;
  const card = $(`<div class="card">
    <div class="row"><b>关键词词库</b>
      <button class="mini" onclick="addItem()">+ 新增词条</button>
      <button class="act" onclick="saveReplies()">保存并热重载</button></div>
    <div id="items"></div></div>`);
  v.appendChild(card);
  const box = card.querySelector('#items');
  const draw = (items) => {
    box.innerHTML = '';
    items.forEach((it, i) => {
      const el = $(`<div class="item"><div class="grid">
        <input data-k="id" value="${it.id||''}" placeholder="id">
        <input data-k="pattern" value="${(it.pattern||'').replace(/"/g,'&quot;')}" placeholder="触发词">
        <select data-k="match">
          <option value="exact">精确</option><option value="contains">包含</option>
        </select>
        <input data-k="cooldown" type="number" step="0.5" value="${it.cooldown??8}">
        <label style="font-size:12px"><input data-k="enabled" type="checkbox" ${it.enabled!==false?'checked':''}> 启用</label>
        <button class="mini" onclick="this.closest('.item').remove()">✕</button>
        <textarea data-k="reply" placeholder="回复内容" style="grid-column:1/-1;min-height:52px">${it.reply||''}</textarea>
      </div></div>`);
      el.querySelector('select').value = it.match || 'exact';
      box.appendChild(el);
    });
  };
  card._draw = draw; draw(r.data.items);
  window.addItem = () => draw([...collectReplies(), {id:'', enabled:true, match:'exact', pattern:'', reply:'', cooldown:8}]);
  window.collectReplies = () => [...box.querySelectorAll('.item')].map(el => {
    const get = k => el.querySelector(`[data-k="${k}"]`);
    return { id:get('id').value.trim(), pattern:get('pattern').value,
             match:get('match').value, cooldown:parseFloat(get('cooldown').value)||8,
             enabled:get('enabled').checked, reply:get('reply').value };
  });
  window.saveReplies = async () => {
    const r2 = await post('/panel/api/replies', {items: window.collectReplies()});
    if (r2.ok) { msg('已保存并重载，共 '+r2.data.count+' 条'); } else { msg(r2.error||'保存失败', false); }
  };
}

/* ---------- 白名单 ---------- */
async function loadGroups() {
  const v = document.getElementById('view'); v.innerHTML = '';
  const r = await api('/panel/api/groups'); if (!r.ok) return;
  const card = $(`<div class="card"><b>群白名单</b>
    <div class="row" style="margin-top:10px">
      <input id="gid" type="number" placeholder="群号">
      <button class="act" onclick="groupAct('add')">添加</button></div>
    <table><tr><th>群号</th><th>来源</th><th></th></tr>
    ${r.data.groups.map(g=>`<tr>
      <td>${g.group_id}</td>
      <td><span class="tag ${g.source==='env'?'env':'rt'}">${g.source==='env'?'环境变量':'运行时'}</span></td>
      <td>${g.source==='env'?'<span class=muted>需改 env</span>':
        `<button class="mini" onclick="groupAct('del',${g.group_id})">移除</button>`}</td>
    </tr>`).join('')}</table></div>`);
  v.appendChild(card);
}
async function groupAct(action, gid) {
  gid = gid ?? parseInt(document.getElementById('gid').value);
  if (!gid) return msg('请输入群号', false);
  const r = await post('/panel/api/groups', {action, group_id: gid});
  if (r.ok) { msg(r.data.message); loadGroups(); } else { msg(r.error, false); }
}

/* ---------- 启动 ---------- */
api('/panel/api/status').then(()=>renderMain()).catch(()=>{});
</script>
</body>
</html>"""
