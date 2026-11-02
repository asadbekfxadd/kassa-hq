// =====================================================================
//  Dashboard по ТЗ: периоды и сравнения, KPI, динамика, категории,
//  топ-10, товары без продаж, средний чек, ALERTS. Уведомления и поиск.
// =====================================================================
let DP='day', DR=null;
const PER={day:['Сегодня','вчера к этому часу'],week:['Неделя','прошлая неделя к этому дню'],month:['Месяц','прошлый месяц к этому дню'],year:['Год','прошлый год к этой дате']};
const LVL={danger:['#b42318','#fef3f2','!'],warn:['#b54708','#fffaeb','◷'],info:['#1e40af','#eff6ff','i']};
const short=v=>{ v=+v||0; const a=Math.abs(v), f=x=>String(+x.toFixed(1)).replace('.',','); return a>=1e9?f(v/1e9)+' млрд':a>=1e6?f(v/1e6)+' млн':a>=1e4?Math.round(v/1e3)+' тыс':fmt(v); };
const chgTag=(p,inv)=>p==null?'<span class="muted" title="За прошлый период продаж не было">— нет базы</span>':`<span class="kc ${(p>=0)!==!!inv?'up':'dn'}">${p>=0?'↑':'↓'} ${Math.abs(p)}%</span>`;

async function loadDash(){
  let r; try{ r=DR=await api('/api/dash2?period='+DP); }catch(e){ $('#dashBox').innerHTML=`<div class="card red">${esc(e.message)}</div>`; return; }
  const c=r.cur, ch=r.chg, cmp=PER[DP][1];
  const K=(l,v,tag,sub='',cls='')=>`<div class="card kpi dk2"><div class="l">${l}</div><div class="v ${cls}">${v}</div><div class="ks">${tag||''}${tag&&tag.includes('нет базы')?'':`<span class="muted">${sub}</span>`}</div></div>`;
  const qa=(v,l,fn)=>`<button class="qa" onclick="${fn}" title="${l}">${(document.querySelector(`#nav button[data-v="${v}"] svg`)||{}).outerHTML||''}<span>${l}</span></button>`;
  $('#dashBox').innerHTML=`
    <div class="dtool">
      <div class="seg" id="dPer">${Object.entries(PER).map(([k,v])=>`<button data-p="${k}" class="${k===DP?'on':''}">${v[0]}</button>`).join('')}</div>
      <span class="muted" style="font-size:12.5px">сравнение: ${cmp} · данные на ${r.now}</span>
      <span class="grow"></span>
      ${qa('pos','Касса',"show('pos')")}${qa('purchase','Приход',"show('purchase')")}${qa('inventory','Ревизия',"show('inventory')")}
      ${qa('products','Товар',"show('products');editProduct()")}${qa('promos','Акция',"show('promos');editPromo()")}
    </div>
    <div class="kpis dk">
      ${K('Выручка · '+PER[DP][0].toLowerCase(),fmt(c.net),chgTag(ch.net),' '+cmp.split(' ')[0])}
      ${K('Выручка за месяц',fmt(r.month.net),chgTag(r.month_chg),' к прошлому')}
      ${K('Продаж (чеков)',fmt(c.checks),chgTag(ch.checks))}
      ${K('Средний чек',fmt(c.avg),chgTag(ch.avg))}
      ${K('Валовая прибыль',fmt(c.profit),chgTag(ch.profit),` · маржа ${c.margin}%`)}
      ${K('Продано товаров',fq(Math.round(c.items*10)/10),chgTag(ch.items))}
      ${K('Товарный остаток',short(r.stock.cost),'',`по закупке · ${r.stock.skus} SKU`)}
    </div>
    <div class="dgrid">
      <div class="card dc" style="grid-column:span 2"><div class="row"><b class="grow">Динамика продаж</b>
        <span class="lg"><i style="background:var(--brand)"></i>${PER[DP][0]}</span><span class="lg"><i class="ln"></i>${cmp.split(' к ')[0]}</span></div>
        <div class="body" id="dDyn"></div></div>
      <div class="card dc"><div class="row"><b class="grow">Требует внимания</b><span class="muted" id="dAlN"></span></div><div class="body" id="dAlerts"><div class="muted">…</div></div></div>
      <div class="card dc"><b>Продажи по категориям</b><div class="body">${r.categories.length?hBars(r.categories.map(x=>({l:esc(x.name),v:x.net,sub:pct(x.net,c.net)+'%'}))):'<div class="muted pad">Нет продаж за период</div>'}</div></div>
      <div class="card dc"><div class="row"><b class="grow">Топ-10 товаров</b><span class="muted" style="font-size:12px">выручка</span></div><div class="body">
        ${r.top.map((x,i)=>`<div class="dl" onclick="openProd(${x.id})"><span class="rk">${i+1}</span><span class="grow ell">${esc(x.name)}</span><span class="muted">${fq(x.q)}</span><b class="num">${fmt(x.t)}</b></div>`).join('')||'<div class="muted pad">Нет продаж за период</div>'}</div></div>
      <div class="card dc"><div class="row"><b class="grow">Средний чек</b><b>${fmt(c.avg)}</b></div><div id="dAvg" style="height:84px;margin:4px 0 6px"></div>
        <div class="row" style="margin-top:2px"><b class="grow">Без продаж 30 дней</b><span class="badge ${r.nosale.n?'pend':'ok'}">${r.nosale.n} · ${short(r.nosale.value)}</span></div>
        <div class="body">${r.nosale.items.map(x=>`<div class="dl" onclick="openProd(${x.id})"><span class="grow ell">${esc(x.name)}</span><span class="muted">${fq(x.stock)} ${esc(x.unit_name||'')}</span><b class="num">${fmt(x.value)}</b></div>`).join('')||'<div class="muted pad">Все товары продаются ✓</div>'}</div></div>
    </div>`;
  $$('#dPer button').forEach(b=>b.onclick=()=>{ DP=b.dataset.p; loadDash(); });
  drawDyn(); drawAvg(); loadAlerts();
}
function svgW(el){ return Math.max(200, el.clientWidth); }
function drawDyn(){
  const el=$('#dDyn'); if(!el||!DR) return; const S=DR.series, W=svgW(el), H=Math.max(140, el.clientHeight-6), L=60, B=22, T=10;
  const max=Math.max(1,...S.map(x=>Math.max(x.net||0,x.prev||0))); const step=10**Math.floor(Math.log10(max)); const top=Math.ceil(max/step)*step;
  const n=S.length, bw=(W-L)/n, y=v=>T+(H-B-T)*(1-v/top);
  const grid=[0,.25,.5,.75,1].map(f=>`<line x1="${L}" x2="${W}" y1="${y(top*f)}" y2="${y(top*f)}" stroke="#eef0f3"/><text x="${L-6}" y="${y(top*f)+4}" text-anchor="end">${short(top*f)}</text>`).join('');
  const every=Math.ceil(n/16);
  const bars=S.map((x,i)=>{ const v=x.net||0, h=(H-B-T)*v/top, cx=L+i*bw;
    return `<g class="hv"><rect x="${cx}" y="${T}" width="${bw}" height="${H-B-T}" fill="transparent"/>
      ${x.net!=null?`<rect x="${cx+bw*.18}" y="${y(v)}" width="${bw*.64}" height="${Math.max(0,h)}" rx="${Math.min(4,bw*.2)}" fill="var(--brand)" opacity="${i===S.length-1||x.net===null?1:.88}"/>`:''}
      <title>${x.label}: ${x.net==null?'ещё не наступило':fmt(v)+' · чеков '+x.checks}\nПрошлый период: ${fmt(x.prev)}</title>
      ${i%every===0?`<text x="${cx+bw/2}" y="${H-6}" text-anchor="middle">${x.label}</text>`:''}</g>`; }).join('');
  const line=S.map((x,i)=>`${i?'L':'M'}${L+i*bw+bw/2},${y(x.prev||0)}`).join('');
  el.innerHTML=`<svg width="${W}" height="${H}" class="ch">${grid}${bars}<path d="${line}" fill="none" stroke="#94a3b8" stroke-width="2" stroke-dasharray="4 4"/></svg>`;
}
function drawAvg(){
  const el=$('#dAvg'); if(!el||!DR) return; const S=DR.series.filter(x=>x.avg), W=svgW(el), H=84;
  if(S.length<2){ el.innerHTML='<div class="muted" style="padding-top:28px;text-align:center;font-size:12px">Мало данных для графика</div>'; return; }
  const mn=Math.min(...S.map(x=>x.avg)), mx=Math.max(...S.map(x=>x.avg)), rg=Math.max(1,mx-mn);
  const pts=S.map((x,i)=>[6+i*(W-12)/(S.length-1), 8+(H-24)*(1-(x.avg-mn)/rg)]);
  const d=pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+','+p[1].toFixed(1)).join('');
  el.innerHTML=`<svg width="${W}" height="${H}" class="ch"><path d="${d} L${pts[pts.length-1][0]},${H-14} L6,${H-14}Z" fill="var(--brand)" opacity=".07"/>
    <path d="${d}" fill="none" stroke="var(--brand)" stroke-width="2" stroke-linejoin="round"/>
    ${pts.map((p,i)=>`<circle cx="${p[0]}" cy="${p[1]}" r="3" fill="#fff" stroke="var(--brand)" stroke-width="2"><title>${S[i].label}: ${fmt(S[i].avg)}</title></circle>`).join('')}
    <text x="6" y="${H-2}">${S[0].label}</text><text x="${W-6}" y="${H-2}" text-anchor="end">${S[S.length-1].label}</text></svg>`;
}
window.addEventListener('resize',()=>{ if($('#v-dash').classList.contains('on')){ drawDyn(); drawAvg(); } });
async function openProd(id){ try{ const p=(await api('/api/products?limit=5000')).find(x=>x.id===id); if(p) productCard(p); }catch(e){} }

// ---------------------------------------------------------------- уведомления
let ALERTS=[];
function alertHtml(a){ const L=LVL[a.level];
  return `<div class="al" onclick="closeBell();show('${a.view}')"><span class="ai" style="background:${L[1]};color:${L[0]}">${L[2]}</span>
    <div class="grow" style="min-width:0"><div class="row" style="gap:6px"><b class="grow ell">${esc(a.title)}</b><span class="badge" style="background:${L[1]};color:${L[0]}">${a.count}</span></div>
    <div class="muted" style="font-size:12px">${esc(a.text)}</div>${a.items.length?`<div class="ell" style="font-size:12px;color:var(--text-2)">${a.items.map(esc).join(' · ')}</div>`:''}</div></div>`; }
async function loadAlerts(){
  try{ const r=await api('/api/alerts'); ALERTS=r.items;
    const b=$('#bellN'); if(b){ b.textContent=r.total>99?'99+':r.total; b.classList.toggle('hidden',!r.total); }
    const box=$('#dAlerts'); if(box){ box.innerHTML=ALERTS.map(alertHtml).join('')||'<div class="pad" style="text-align:center"><div style="font-size:26px">✓</div><div class="muted">Всё в порядке</div></div>'; $('#dAlN').textContent=ALERTS.length?ALERTS.length+' тем':''; }
    const p=$('#bellPanel'); if(p&&!p.classList.contains('hidden')) renderBell();
  }catch(e){}
}
function renderBell(){ $('#bellPanel').innerHTML=`<div class="row" style="padding:12px 14px 8px"><b class="grow">Уведомления</b><span class="muted" style="font-size:12px">обновляются каждые 2 мин</span></div>
  <div style="max-height:62vh;overflow:auto;padding:0 6px 8px">${ALERTS.map(alertHtml).join('')||'<div class="pad muted" style="text-align:center">Новых уведомлений нет</div>'}</div>`; }
function closeBell(){ $('#bellPanel')?.classList.add('hidden'); }
(function(){
  const right=$('header .right'); if(!right) return;
  right.insertAdjacentHTML('afterbegin',`<button id="bellBtn" class="icon" title="Уведомления"><svg viewBox="0 0 24 24"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg><span id="bellN" class="hidden"></span></button>`);
  document.body.insertAdjacentHTML('beforeend','<div id="bellPanel" class="hidden"></div>');
  $('#bellBtn').onclick=e=>{ e.stopPropagation(); const p=$('#bellPanel'); const r=$('#bellBtn').getBoundingClientRect();
    p.style.top=(r.bottom+8)+'px'; p.style.right=(innerWidth-r.right)+'px'; p.classList.toggle('hidden'); if(!p.classList.contains('hidden')){ renderBell(); loadAlerts(); } };
  document.addEventListener('click',e=>{ if(!e.target.closest('#bellPanel')&&!e.target.closest('#bellBtn')) closeBell(); });
  setInterval(()=>{ if(ME) loadAlerts(); }, 120000);
  setTimeout(()=>{ if(ME) loadAlerts(); }, 1500);
})();

// ---------------------------------------------------------------- поиск по всей системе
(function(){
  const right=$('header .right'); if(!right) return;
  right.insertAdjacentHTML('beforebegin',`<div class="gsearch"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg><input id="gQ" autocomplete="off" placeholder="Поиск: товар, штрихкод, клиент, чек №, поставщик…"><kbd>Ctrl K</kbd><div class="sugg hidden" id="gSugg"></div></div>`);
  const q=$('#gQ');
  document.addEventListener('keydown',e=>{ if((e.ctrlKey||e.metaKey)&&(e.key==='k'||e.key==='л'||e.code==='KeyK')&&ME){ e.preventDefault(); q.focus(); q.select(); } });
  const box=$('#gSugg'); let flat=[], idx=0, t=null;
  const pages=()=>Object.entries(TITLES).filter(([v])=>{ const b=$(`#nav button[data-v="${v}"]`); return b&&!b.classList.contains('hidden'); });
  const open=it=>{ box.classList.add('hidden'); q.value=''; q.blur();
    if(it.kind==='page') show(it.x.v); else if(it.kind==='products') productCard(it.x); else if(it.kind==='customers') showCustomer(it.x.id);
    else if(it.kind==='sales') showSale(it.x.id); else if(it.kind==='suppliers') showSupplier(it.x.id); };
  const line=it=>{ const x=it.x;
    if(it.kind==='page') return `<span>↪ ${esc(x.name)}</span><span class="muted">раздел</span>`;
    if(it.kind==='products') return `<span>${esc(x.name)} <span class="muted">${esc(x.barcode||'')}</span></span><span>${fmt(x.price)} · <span class="${x.stock<=0?'red':'muted'}">${fq(x.stock)} ${esc(x.unit_name||'')}</span></span>`;
    if(it.kind==='customers') return `<span>👤 ${esc(x.name)} <span class="muted">${esc(x.phone||'')}</span></span><span class="muted">бонусы ${fmt(x.bonus)}${x.debt>0?` · <span class="red">долг ${fmt(x.debt)}</span>`:''}</span>`;
    if(it.kind==='sales') return `<span>🧾 Чек №${x.id} <span class="muted">${x.created_at.slice(0,16)} · ${esc(x.cashier||'')}</span></span><b class="${x.operation==='refund'?'red':''}">${fmt(x.total)}</b>`;
    return `<span>🚚 ${esc(x.name)} <span class="muted">${esc(x.phone||'')}</span></span><span class="${x.balance>0?'red':'muted'}">${x.balance>0?'долг '+fmt(x.balance):''}</span>`; };
  const draw=groups=>{ flat=[]; let h='';
    groups.forEach(g=>{ h+=`<div class="sg">${esc(g.title)}</div>`; g.items.forEach(x=>{ const i=flat.length; flat.push({kind:g.kind,x}); h+=`<div class="${i===idx?'on':''}" data-i="${i}">${line(flat[i])}</div>`; }); });
    box.innerHTML=h||'<div class="muted">Ничего не найдено</div>'; box.classList.remove('hidden');
    box.querySelectorAll('[data-i]').forEach(d=>d.onmousedown=e=>{ e.preventDefault(); open(flat[+d.dataset.i]); }); };
  let last=[];
  const run=async()=>{ const v=q.value.trim(); if(v.length<2){ box.classList.add('hidden'); return; }
    const pg=pages().filter(([,n])=>n.toLowerCase().includes(v.toLowerCase())).map(([v2,n])=>({v:v2,name:n}));
    let r={groups:[]}; try{ r=await api('/api/search?q='+encodeURIComponent(v)); }catch(e){}
    last=[...(pg.length?[{kind:'page',title:'Разделы',items:pg}]:[]),...r.groups];
    idx=0; draw(last); };
  q.oninput=()=>{ clearTimeout(t); t=setTimeout(run,170); };
  q.onkeydown=e=>{ const vis=!box.classList.contains('hidden')&&flat.length;
    if(e.key==='ArrowDown'&&vis){ e.preventDefault(); idx=Math.min(idx+1,flat.length-1); draw(last); }
    else if(e.key==='ArrowUp'&&vis){ e.preventDefault(); idx=Math.max(idx-1,0); draw(last); }
    else if(e.key==='Enter'&&vis){ e.preventDefault(); open(flat[idx]); }
    else if(e.key==='Escape'){ q.value=''; box.classList.add('hidden'); q.blur(); focusScan(); } };
  q.onblur=()=>setTimeout(()=>box.classList.add('hidden'),150);
})();
