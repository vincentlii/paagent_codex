"""Self-contained operational dashboard page for loopback VPS access."""

DASHBOARD_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>PA Agent Control</title>
  <style>
    :root{--ink:#17201d;--muted:#62706b;--line:#dce3df;--paper:#f6f8f7;--white:#fff;--green:#18794e;--amber:#a15c00;--red:#c23b32;--cyan:#087e8b}
    *{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;letter-spacing:0}
    header{height:64px;background:var(--ink);color:white;display:flex;align-items:center;justify-content:space-between;padding:0 28px;position:sticky;top:0;z-index:2}
    .brand{font-size:18px;font-weight:700}.brand small{font-size:11px;font-weight:500;color:#b8c7c1;margin-left:10px;text-transform:uppercase}
    main{max-width:1440px;margin:auto;padding:22px 28px 40px}.toolbar{display:flex;gap:10px;align-items:center}.status{display:inline-flex;align-items:center;gap:7px;font-weight:650}.dot{width:9px;height:9px;border-radius:50%;background:#9ca8a3}.dot.running{background:#35c47b}.dot.paused{background:#f4ad42}.dot.stale,.dot.offline{background:#ef6157}
    button{height:34px;border:1px solid #52615b;background:transparent;color:white;padding:0 12px;border-radius:5px;font-weight:650;cursor:pointer}button.primary{background:#eef5f1;color:var(--ink);border-color:#eef5f1}button.danger{border-color:#e2837c;color:#ffd8d5}button:disabled{opacity:.45;cursor:default}
    .metrics{display:grid;grid-template-columns:repeat(5,minmax(140px,1fr));border:1px solid var(--line);background:var(--white);margin-bottom:20px}.metric{padding:16px 18px;border-right:1px solid var(--line)}.metric:last-child{border:0}.metric label{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;margin-bottom:5px}.metric strong{font-size:20px;font-variant-numeric:tabular-nums}
    section{margin-top:22px}h2{font-size:15px;margin:0 0 10px;display:flex;justify-content:space-between;align-items:center}h2 span{font-size:12px;color:var(--muted);font-weight:500}
    .table-wrap{overflow:auto;border:1px solid var(--line);background:var(--white)}table{border-collapse:collapse;width:100%;min-width:820px}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap}th{font-size:11px;text-transform:uppercase;color:var(--muted);background:#f0f4f2}tbody tr:last-child td{border-bottom:0}.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}.pill{display:inline-block;border:1px solid var(--line);border-radius:4px;padding:2px 6px;font-size:11px}.ok{color:var(--green)}.warn{color:var(--amber)}.bad{color:var(--red)}.empty{color:var(--muted);padding:20px}.notice{display:none;margin-bottom:16px;border-left:3px solid var(--amber);background:#fff7e8;padding:10px 12px}.notice.show{display:block}.split{display:grid;grid-template-columns:2fr 1fr;gap:20px}.panel{border:1px solid var(--line);background:white;padding:16px}.kv{display:grid;grid-template-columns:1fr auto;gap:8px;border-bottom:1px solid var(--line);padding:7px 0}.kv:last-child{border:0}.kv span{color:var(--muted)}
    @media(max-width:900px){header{height:auto;min-height:64px;padding:12px 16px;align-items:flex-start;gap:12px}.toolbar{flex-wrap:wrap;justify-content:flex-end}main{padding:16px}.metrics{grid-template-columns:repeat(2,1fr)}.metric{border-bottom:1px solid var(--line)}.split{grid-template-columns:1fr}.brand small{display:block;margin:2px 0 0}}
  </style>
</head>
<body>
<header><div class="brand">PA Agent <small>VPS Control</small></div><div class="toolbar"><span class="status"><i id="health-dot" class="dot"></i><span id="health-label">连接中</span></span><button id="pause" class="danger">暂停分析</button><button id="resume" class="primary">恢复运行</button></div></header>
<main>
  <div id="notice" class="notice"></div>
  <div class="metrics">
    <div class="metric"><label>运行状态</label><strong id="service-state">--</strong></div>
    <div class="metric"><label>分析中 / 排队</label><strong id="queue">--</strong></div>
    <div class="metric"><label>最近分析</label><strong id="last-run">--</strong></div>
    <div class="metric"><label>PA Paper 权益</label><strong id="equity">--</strong></div>
    <div class="metric"><label>VPA 模式</label><strong id="vpa-mode">--</strong></div>
  </div>
  <section><h2>品种监控 <span id="asset-count"></span></h2><div class="table-wrap"><table><thead><tr><th>品种</th><th>来源 / Venue</th><th>周期</th><th>连接</th><th>任务</th><th>最近结果</th><th>失败次数</th></tr></thead><tbody id="assets"></tbody></table></div></section>
  <section><h2>最近分析 <span>脱敏审计索引</span></h2><div class="table-wrap"><table><thead><tr><th>时间</th><th>品种</th><th>PA 背景 / 方向</th><th>Gate</th><th>订单</th><th>置信度</th><th>VPA</th><th>状态</th></tr></thead><tbody id="runs"></tbody></table></div></section>
  <section class="split"><div><h2>Paper 账户</h2><div class="table-wrap"><table><thead><tr><th>账户</th><th>权益</th><th>订单</th><th>Open</th><th>Closed</th><th>Realized PnL</th></tr></thead><tbody id="accounts"></tbody></table></div></div><div><h2>运维摘要</h2><div class="panel" id="ops"></div></div></section>
</main>
<script>
const $=id=>document.getElementById(id), text=(id,v)=>$(id).textContent=v??'--';
const esc=v=>String(v??'--'); const fmtTime=ms=>ms?new Date(ms).toLocaleString():'--'; const fmtNum=v=>Number.isFinite(Number(v))?Number(v).toLocaleString(undefined,{maximumFractionDigits:2}):'--';
function cell(row,value,cls=''){const td=document.createElement('td');td.textContent=esc(value);if(cls)td.className=cls;row.appendChild(td)}
function empty(body,count,message){body.replaceChildren();const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=count;td.className='empty';td.textContent=message;tr.appendChild(td);body.appendChild(tr)}
function render(data){const h=data.health||{},rt=data.runtime||{},q=rt.scheduler||{},runs=data.recent_runs||[],paper=data.paper||{},control=data.control||{};
  $('health-dot').className='dot '+esc(h.state);text('health-label',h.label||h.state);text('service-state',rt.service_state||h.state);text('queue',`${q.running||0} / ${q.queued||0}`);text('last-run',runs.length?fmtTime(runs[0].timestamp_local_ms):'暂无');text('vpa-mode',(rt.mode||{}).vpa||'--');
  const official=(paper.accounts||[]).find(a=>a.account_id==='pa-official')||(paper.accounts||[])[0];text('equity',official?fmtNum(official.equity):'暂无');
  $('pause').disabled=control.desired_state==='paused';$('resume').disabled=control.desired_state==='running';const notice=$('notice');if(control.applied===false){notice.textContent='控制请求已写入, 等待 Bot 到达安全边界后应用。';notice.className='notice show'}else if(h.message){notice.textContent=h.message;notice.className='notice show'}else{notice.className='notice'}
  const assets=$('assets'),items=rt.assets||[];text('asset-count',`${items.length} 个`);if(!items.length)empty(assets,7,'尚无运行时资产状态');else{assets.replaceChildren();for(const a of items){const tr=document.createElement('tr');cell(tr,a.symbol,'mono');cell(tr,`${a.source||'--'} / ${a.venue||'auto'}`);cell(tr,a.timeframe);cell(tr,a.connected?'已连接':'未连接',a.connected?'ok':'warn');cell(tr,a.in_flight?'分析中':'等待');cell(tr,a.last_analysis_status||'--');cell(tr,a.fetch_failures||0);assets.appendChild(tr)}}
  const rb=$('runs');if(!runs.length)empty(rb,8,'尚无分析记录');else{rb.replaceChildren();for(const r of runs){const tr=document.createElement('tr');cell(tr,fmtTime(r.timestamp_local_ms));cell(tr,`${r.symbol} ${r.timeframe}`,'mono');cell(tr,`${r.stage1_cycle_position||'--'} / ${r.stage1_direction||'--'}`);cell(tr,r.stage1_gate_result||'--');cell(tr,`${r.order_direction||'--'} ${r.order_type||'--'}`);cell(tr,r.trade_confidence??'--');cell(tr,[r.vpa_data_status,r.vpa_background_relation,r.vpa_timing_relation,r.vpa_effect].filter(Boolean).join(' / ')||'--');cell(tr,r.run_status,r.run_status==='completed'?'ok':(r.run_status||'').includes('warning')?'warn':'bad');rb.appendChild(tr)}}
  const ab=$('accounts'),accounts=paper.accounts||[];if(!accounts.length)empty(ab,6,'Paper 账本尚无账户');else{ab.replaceChildren();for(const a of accounts){const tr=document.createElement('tr');cell(tr,a.account_id,'mono');cell(tr,fmtNum(a.equity));cell(tr,a.orders);cell(tr,a.open);cell(tr,a.closed);cell(tr,fmtNum(a.realized_pnl),Number(a.realized_pnl)>=0?'ok':'bad');ab.appendChild(tr)}}
  const ops=$('ops'),research=paper.research||{};ops.replaceChildren();const pairs=[['心跳',fmtTime(rt.updated_at_ms)],['启动时间',fmtTime(rt.started_at_ms)],['已完成',q.completed||0],['失败',q.failed||0],['审计写入失败',q.audit_failures||0],['A/A/B/B 研究快照',research.complete_aabb_snapshots||0],['控制版本',control.generation||0]];for(const [k,v] of pairs){const d=document.createElement('div');d.className='kv';const a=document.createElement('span'),b=document.createElement('strong');a.textContent=k;b.textContent=esc(v);d.append(a,b);ops.appendChild(d)}
}
async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw new Error(`HTTP ${r.status}`);render(await r.json())}catch(e){text('health-label','连接失败');$('health-dot').className='dot offline';const n=$('notice');n.textContent=`无法读取运行状态: ${e.message}`;n.className='notice show'}}
async function control(action){for(const b of [$('pause'),$('resume')])b.disabled=true;try{const r=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json','X-PA-Agent-Control':'1'},body:JSON.stringify({action})});if(!r.ok){const x=await r.json();throw new Error(x.error||`HTTP ${r.status}`)}await refresh()}catch(e){alert(`控制失败: ${e.message}`);await refresh()}}
$('pause').addEventListener('click',()=>control('pause'));$('resume').addEventListener('click',()=>control('resume'));refresh();setInterval(refresh,5000);
</script>
</body></html>"""
