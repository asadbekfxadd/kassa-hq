// =====================================================================
//  Центр сети: Dashboard по ТЗ + уведомления (ALERTS)
// =====================================================================
let DP='day', DR=null, ALERTS=[];
const PER={day:['Сегодня','вчера к этому часу'],week:['Неделя','прошлая неделя'],month:['Месяц','прошлый месяц'],year:['Год','прошлый год']};
const LVL={danger:['#b42318','#fef3f2','!'],warn:['#b54708','#fffaeb','◷'],info:['#1e40af','#eff6ff','i']};
const pct=(a,b)=>b?Math.round(a/b*1000)/10:0;
const short=v=>{ v=+v||0; const a=Math.abs(v), f=x=>String(+x.toFixed(1)).replace('.',','); return a>=1e9?f(v/1e9)+' млрд':a>=1e6?f(v/1e6)+' млн':a>=1e4?Math.round(v/1e3)+' тыс':fmt(v); };
const chgTag=(p,inv)=>p==null?'<span class="muted" title="За прошлый период продаж не было">— нет базы</span>':`<span class="kc ${(p>=0)!==!!inv?'up':'dn'}">${p>=0?'↑':'↓'} ${Math.abs(p)}%</span>`;
const hBars=rows=>{ const mx=Math.max(1,...rows.map(r=>Math.abs(r.v))); return rows.map(r=>`<div class="hb"><div class="row" style="font-size:13px"><span class="grow ell">${r.l}</span><b>${fmt(r.v)}</b>${r.sub?`<span class="muted" style="width:56px;text-align:right">${r.sub}</span>`:''}</div><div class="tr"><i style="width:${Math.max(1,Math.abs(r.v)/mx*100)}%;${r.v<0?'background:var(--red)':''}"></i></div></div>`).join(''); };

async function loadDash(){
  let r; try{ r=DR=await api('/api/dash2?period='+DP); }catch(e){ $('#dashBox').innerHTML=`<div class="card red">${esc(e.message)}</div>`; return; }
  const c=r.cur, ch=r.chg, cmp=PER[DP][1];
  const K=(l,v,tag,sub='',cls='')=>`<div class="card kpi dk2"><div class="l">${l}</div><div class="v ${cls}">${v}</div><div class="ks">${tag||''}${tag&&tag.includes('нет базы')?'':`<span class="muted">${sub}</span>`}</div></div>`;
  const S=r.stores;
  $('#dashBox').innerHTML=`
    <div class="dtool"><div class="seg" id="dPer">${Object.entries(PER).map(([k,v])=>`<button data-p="${k}" class="${k===DP?'on':''}">${v[0]}</button>`).join('')}</div>
      <span class="muted" style="font-size:12.5px">сравнение: ${cmp} · данные на ${r.now} (Ташкент)</span></div>
    <div class="kpis dk">
      ${K('Выручка · '+PER[DP][0].toLowerCase(),fmt(c.net),chgTag(ch.net))}
      ${K('Выручка за месяц',fmt(r.month.net),chgTag(r.month_chg))}
      ${K('Продаж (чеков)',fmt(c.checks),chgTag(ch.checks))}
      ${K('Средний чек',fmt(c.avg),chgTag(ch.avg))}
      ${r.finance?K('Валовая прибыль',fmt(c.profit),chgTag(ch.profit),`маржа ${c.margin}%`):K('План месяца',r.month_plan?pct(r.month.net,r.month_plan)+'%':'не задан','',r.month_plan?'выполнено':'')}
      ${K('Продано товаров',fq(Math.round(c.items*10)/10),chgTag(ch.items))}
      ${K('Активных магазинов',`${r.active_stores} <span class="muted" style="font-size:14px">из ${S.length}</span>`,'',r.active_stores<S.length?'<span class="amber">есть без связи</span>':'все на связи')}
      ${r.stock_value==null?K('Товаров без продаж',r.nosale.n,'','30 дней'):K('Товарный остаток',short(r.stock_value),'','по закупке')}
    </div>
    <div class="dgrid">
      <div class="card dc" style="grid-column:span 2"><div class="row"><b class="grow">Динамика продаж сети</b><span class="lg"><i style="background:var(--brand)"></i>${PER[DP][0]}</span><span class="lg"><i class="ln"></i>${cmp.split(' к ')[0]}</span></div><div class="body" id="dDyn"></div></div>
      <div class="card dc"><div class="row"><b class="grow">Требует внимания</b><span class="muted" id="dAlN"></span></div><div class="body" id="dAlerts"></div></div>
      <div class="card dc"><b>Выручка по магазинам</b><div class="body">${S.length?hBars([...S].sort((a,b)=>b.net-a.net).map(s=>({l:`<span class="dot ${s.online?'on':''}"></span>${esc(s.name)}`,v:s.net,sub:pct(s.net,c.net)+'%'}))):'<div class="pad muted">Добавьте магазины</div>'}</div></div>
      ${r.finance?`<div class="card dc"><b>Прибыль по магазинам</b><div class="body">${S.length?hBars([...S].sort((a,b)=>b.profit-a.profit).map(s=>({l:esc(s.name),v:s.profit,sub:s.margin+'%'}))):''}</div></div>`
        :`<div class="card dc"><b>План месяца по магазинам</b><div class="body">${planBars(r)}</div></div>`}
      ${r.finance?`<div class="card dc"><b>Выполнение плана месяца</b><div class="body">${planBars(r)}</div></div>`:''}
      <div class="card dc"><b>Продажи по категориям</b><div class="body">${r.categories.length?hBars(r.categories.map(x=>({l:esc(x.name),v:x.net,sub:pct(x.net,c.net)+'%'}))):'<div class="pad muted">Нет продаж за период</div>'}</div></div>
      <div class="card dc"><div class="row"><b class="grow">Топ-10 товаров сети</b><span class="muted" style="font-size:12px">выручка</span></div><div class="body">
        ${r.top.map((x,i)=>`<div class="dl"><span class="rk">${i+1}</span><span class="grow ell">${esc(x.name)}</span><span class="muted">${fq(x.q)}</span><b class="num">${fmt(x.t)}</b></div>`).join('')||'<div class="pad muted">Нет продаж</div>'}</div></div>
      <div class="card dc"><div class="row"><b class="grow">Товары без продаж 30 дней</b><span class="badge ${r.nosale.n?'pend':'ok'}">${r.nosale.n}${r.nosale.value!=null?' · '+short(r.nosale.value):''}</span></div><div class="body">
        ${r.nosale.items.map(x=>`<div class="dl"><span class="grow ell">${esc(x.name)}</span><span class="muted">${fq(x.qty)} шт</span><b class="num">${x.value==null?'':fmt(x.value)}</b></div>`).join('')||'<div class="pad muted">Все товары продаются ✓</div>'}</div></div>
      <div class="card dc"><div class="row"><b class="grow">Динамика среднего чека</b><b>${fmt(c.avg)}</b></div><div class="body" id="dAvg"></div></div>
    </div>
    <div class="card" style="margin-top:12px"><div class="row" style="margin-bottom:8px"><b class="grow">Сравнение магазинов · ${PER[DP][0].toLowerCase()}</b><button class="ghost" onclick="show('stores')">Управление магазинами →</button></div>
      <div style="overflow:auto"><table><thead><tr><th>Магазин</th><th>Связь</th><th class="n">Выручка</th><th class="n">К прошлому</th><th class="n">Чеков</th><th class="n">Средний чек</th>${r.finance?'<th class="n">Прибыль</th><th class="n">Маржа</th>':''}<th class="n">План месяца</th><th class="n">Продано</th><th class="n">Остаток (закупка)</th><th class="n">SKU</th></tr></thead><tbody>
      ${S.map(s=>`<tr><td><b>${esc(s.name)}</b><div class="muted" style="font-size:12px">${esc(s.address||'')}</div></td><td><span class="dot ${s.online?'on':''}"></span>${s.online?'на связи':(s.last_seen?s.last_seen.slice(5,16):'не подключён')}</td>
        <td class="n"><b>${fmt(s.net)}</b></td><td class="n">${chgTag(s.chg)}</td><td class="n">${s.checks}</td><td class="n">${fmt(s.avg)}</td>${r.finance?`<td class="n green">${fmt(s.profit)}</td><td class="n">${s.margin}%</td>`:''}<td class="n">${planCell(r,s)}</td><td class="n">${fq(Math.round(s.items))}</td><td class="n">${s.stock_value==null?'—':fmt(s.stock_value)}</td><td class="n">${s.skus}</td></tr>`).join('')||'<tr><td colspan="11" class="muted">Магазинов нет — добавьте во вкладке «Магазины»</td></tr>'}
      </tbody></table></div></div>`;
  $$('#dPer button').forEach(b=>b.onclick=()=>{ DP=b.dataset.p; loadDash(); });
  drawDyn(); drawAvg(); loadAlerts();
}
function drawDyn(){
  const el=$('#dDyn'); if(!el||!DR) return; const S=DR.series, W=Math.max(240,el.clientWidth), H=Math.max(200, el.clientHeight-6), L=60, B=22, T=10;
  const max=Math.max(1,...S.map(x=>Math.max(x.net||0,x.prev||0))); const step=10**Math.floor(Math.log10(max)); const top=Math.ceil(max/step)*step;
  const n=S.length, bw=(W-L)/n, y=v=>T+(H-B-T)*(1-v/top), every=Math.ceil(n/16);
  const grid=[0,.25,.5,.75,1].map(f=>`<line x1="${L}" x2="${W}" y1="${y(top*f)}" y2="${y(top*f)}" stroke="#eef0f3"/><text x="${L-6}" y="${y(top*f)+4}" text-anchor="end">${short(top*f)}</text>`).join('');
  const bars=S.map((x,i)=>{ const v=x.net||0, cx=L+i*bw; return `<g class="hv"><rect x="${cx}" y="${T}" width="${bw}" height="${H-B-T}" fill="transparent"/>
    ${x.net!=null?`<rect x="${cx+bw*.18}" y="${y(v)}" width="${bw*.64}" height="${Math.max(0,(H-B-T)*v/top)}" rx="${Math.min(4,bw*.2)}" fill="var(--brand)"/>`:''}
    <title>${x.label}: ${x.net==null?'ещё не наступило':fmt(v)+' · чеков '+x.checks}\nПрошлый период: ${fmt(x.prev)}</title>${i%every===0?`<text x="${cx+bw/2}" y="${H-6}" text-anchor="middle">${x.label}</text>`:''}</g>`; }).join('');
  const line=S.map((x,i)=>`${i?'L':'M'}${L+i*bw+bw/2},${y(x.prev||0)}`).join('');
  el.innerHTML=`<svg width="${W}" height="${H}" class="ch">${grid}${bars}<path d="${line}" fill="none" stroke="#94a3b8" stroke-width="2" stroke-dasharray="4 4"/></svg>`;
}
function drawAvg(){
  const el=$('#dAvg'); if(!el||!DR) return; const S=DR.series.filter(x=>x.avg), W=Math.max(200,el.clientWidth), H=Math.max(160,el.clientHeight-6);
  if(S.length<2){ el.innerHTML='<div class="pad muted" style="text-align:center">Мало данных для графика</div>'; return; }
  const mn=Math.min(...S.map(x=>x.avg)), mx=Math.max(...S.map(x=>x.avg)), rg=Math.max(1,mx-mn);
  const pts=S.map((x,i)=>[8+i*(W-16)/(S.length-1), 10+(H-34)*(1-(x.avg-mn)/rg)]);
  const d=pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+','+p[1].toFixed(1)).join('');
  el.innerHTML=`<svg width="${W}" height="${H}" class="ch"><path d="${d} L${pts[pts.length-1][0]},${H-22} L8,${H-22}Z" fill="var(--brand)" opacity=".07"/><path d="${d}" fill="none" stroke="var(--brand)" stroke-width="2"/>
    ${pts.map((p,i)=>`<circle cx="${p[0]}" cy="${p[1]}" r="3" fill="#fff" stroke="var(--brand)" stroke-width="2"><title>${S[i].label}: ${fmt(S[i].avg)}</title></circle>`).join('')}
    <text x="8" y="${H-6}">${S[0].label}</text><text x="${W-8}" y="${H-6}" text-anchor="end">${S[S.length-1].label}</text></svg>`;
}
window.addEventListener('resize',()=>{ if($('#v-dash').classList.contains('on')){ drawDyn(); drawAvg(); } });

function alertHtml(a){ const L=LVL[a.level];
  return `<div class="al" onclick="closeBell();show('${a.view}')"><span class="ai" style="background:${L[1]};color:${L[0]}">${L[2]}</span><div class="grow" style="min-width:0">
    <div class="row" style="gap:6px"><b class="grow ell">${esc(a.title)}</b><span class="badge" style="background:${L[1]};color:${L[0]}">${a.count}</span></div>
    <div class="muted" style="font-size:12px">${esc(a.text)}</div>${a.items.length?`<div class="ell" style="font-size:12px;color:var(--text-2)">${a.items.map(esc).join(' · ')}</div>`:''}</div></div>`; }
async function loadAlerts(){
  try{ const r=await api('/api/alerts'); ALERTS=r.items;
    $('#bellN').textContent=r.total>99?'99+':r.total; $('#bellN').classList.toggle('hidden',!r.total);
    const box=$('#dAlerts'); if(box){ box.innerHTML=ALERTS.map(alertHtml).join('')||'<div class="pad" style="text-align:center"><div style="font-size:26px">✓</div><div class="muted">Всё в порядке</div></div>'; $('#dAlN').textContent=ALERTS.length?ALERTS.length+' тем':''; }
    if(!$('#bellPanel').classList.contains('hidden')) renderBell();
  }catch(e){}
}
function renderBell(){ $('#bellPanel').innerHTML=`<div class="row" style="padding:12px 14px 8px"><b class="grow">Уведомления сети</b><span class="muted" style="font-size:12px">каждые 2 мин</span></div>
  <div style="max-height:62vh;overflow:auto;padding:0 6px 8px">${ALERTS.map(alertHtml).join('')||'<div class="pad muted" style="text-align:center">Новых уведомлений нет</div>'}</div>`; }
function closeBell(){ $('#bellPanel').classList.add('hidden'); }
$('#bellBtn').onclick=e=>{ e.stopPropagation(); const p=$('#bellPanel'), r=$('#bellBtn').getBoundingClientRect(); p.style.top=(r.bottom+8)+'px'; p.style.right=(innerWidth-r.right)+'px';
  p.classList.toggle('hidden'); if(!p.classList.contains('hidden')){ renderBell(); loadAlerts(); } };
document.addEventListener('click',e=>{ if(!e.target.closest('#bellPanel')&&!e.target.closest('#bellBtn')) closeBell(); });
setInterval(()=>{ if(!$('#app').classList.contains('hidden')) loadAlerts(); },120000);

function planCell(r,s){ const f=(r.month_store[s.id]||{}).net||0; if(!s.plan) return '<span class="muted">не задан</span>';
  const p=pct(f,s.plan); return `<b class="${p>=100?'green':p<70?'red':''}">${p}%</b><div class="muted" style="font-size:11px">${fmt(f)} из ${fmt(s.plan)}</div>`; }
function planBars(r){ const S=r.stores.filter(s=>s.plan); if(!S.length) return '<div class="pad muted">План не задан. Откройте магазин → «План продаж».</div>';
  return S.map(s=>{ const f=(r.month_store[s.id]||{}).net||0, p=pct(f,s.plan);
    return `<div class="hb"><div class="row" style="font-size:13px"><span class="grow ell">${esc(s.name)}</span><b class="${p>=100?'green':''}">${p}%</b><span class="muted" style="width:96px;text-align:right">${short(f)} / ${short(s.plan)}</span></div>
      <div class="tr"><i style="width:${Math.min(100,p)}%;background:${p>=100?'var(--green)':p<70?'var(--amber)':'var(--brand)'}"></i></div></div>`; }).join(''); }
