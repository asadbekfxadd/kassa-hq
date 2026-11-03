// =====================================================================
//  Центр сети (этап 6): аналитика сети и финансы.
//  Аналитика: продажи / товары (ABC-XYZ, топы, остатки) / сезонность — фильтры период, магазин, категория, бренд.
//  Финансы: прибыль и убытки по филиалам, денежный поток, расходы, долги и наличные в кассах.
// =====================================================================
NAV_PERM.fin='finance';
Object.assign(TITLES,{sales:'Аналитика сети',fin:'Финансы'});
let AF=null, AN_TAB='sales', FN_TAB='pnl', AN_TOP=20, AN_LIST='best';

const chgT=v=>v==null?'<span class="muted">нет данных</span>':`<span class="${v>=0?'green':'red'}">${v>=0?'▲':'▼'} ${Math.abs(v).toString().replace('.',',')}%</span>`;
const dISO=d=>d.toLocaleDateString('sv-SE');
const PRESETS={month:'Этот месяц',prev:'Прошлый месяц',d7:'7 дней',d30:'30 дней',quarter:'Квартал',year:'Этот год',custom:'Свой период'};
function presetRange(k){ const d=new Date(), y=d.getFullYear(), m=d.getMonth();
  return ({month:[new Date(y,m,1),d],prev:[new Date(y,m-1,1),new Date(y,m,0)],d7:[new Date(+d-6*864e5),d],d30:[new Date(+d-29*864e5),d],
    quarter:[new Date(y,Math.floor(m/3)*3,1),d],year:[new Date(y,0,1),d]})[k]; }

document.head.insertAdjacentHTML('beforeend',`<style>
  .fbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px} .fbar select,.fbar input{width:auto;min-width:0;margin:0}
  .fbar input[type=date]{width:145px} .fbar select{max-width:200px}
  .ag{display:grid;grid-template-columns:repeat(12,1fr);gap:12px} .ag>.card{grid-column:span 6;min-width:0} .ag>.w12{grid-column:span 12} .ag>.w4{grid-column:span 4} .ag>.w8{grid-column:span 8}
  .hb{display:flex;align-items:center;gap:8px;padding:5px 0;font-size:13px} .hb .nm{width:38%;min-width:0} .hb .bar{flex:1;height:8px;background:#f1f3f6;border-radius:4px;overflow:hidden}
  .hb .bar i{display:block;height:100%;background:var(--brand);border-radius:4px} .hb .v{width:110px;text-align:right;font-variant-numeric:tabular-nums}
  .heat td{padding:0;border:1px solid #fff;height:20px;min-width:18px;text-align:center;font-size:10px} .heat th{font-size:10.5px;color:var(--muted);font-weight:500;padding:2px 4px;border:0}
  .abcx td{text-align:center;padding:10px;border:1px solid var(--line)} .abcx b{display:block;font-size:16px}
  .pl td,.pl th{white-space:nowrap} .pl tr.t td{font-weight:650;background:#f8fafc} .pl tr.s td:first-child{padding-left:22px;color:var(--text-2)}
  .tg{display:inline-block;min-width:20px;text-align:center;border-radius:6px;padding:1px 6px;font-weight:600;font-size:12px}
  .tg.A,.tg.X{background:#ecfdf3;color:#067647} .tg.B,.tg.Y{background:var(--brand-50);color:var(--brand-d)} .tg.C,.tg.Z{background:#fef3f2;color:#b42318}
  @media(max-width:1200px){.ag>.card,.ag>.w4,.ag>.w8{grid-column:span 12}}
</style>`);

// ---------------------------------------------------------------- разметка разделов
(function(){
  const nb=$('#nav button[data-v=sales]'); nb.title='Аналитика'; nb.querySelector('.t').textContent='Аналитика';
  nb.insertAdjacentHTML('afterend',`<button data-v="fin" title="Финансы"><svg viewBox="0 0 24 24"><rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01M18 12h.01"/></svg><span class="t">Финансы</span></button>`);
  $('#nav button[data-v=fin]').onclick=()=>show('fin');
  $('#v-sales').innerHTML=`<div class="dtool"><div class="seg" id="anTabs"><button data-t="sales" class="on">Продажи</button><button data-t="products">Товары</button><button data-t="season">Сезонность</button></div></div>
    <div class="fbar" id="anF"></div><div id="anBody"><div class="pad muted">Загрузка…</div></div>`;
  $('main').insertAdjacentHTML('beforeend',`<section class="view" id="v-fin"><div class="dtool"><div class="seg" id="fnTabs"><button data-t="pnl" class="on">Прибыль и убытки</button><button data-t="cashflow">Денежный поток</button><button data-t="expenses">Расходы</button><button data-t="balances">Долги и касса</button></div></div>
    <div class="fbar" id="fnF"></div><div id="fnBody"></div></section>`);
  $$('#anTabs button').forEach(b=>b.onclick=()=>{ AN_TAB=b.dataset.t; $$('#anTabs button').forEach(x=>x.classList.toggle('on',x===b)); loadAn(); });
  $$('#fnTabs button').forEach(b=>b.onclick=()=>{ FN_TAB=b.dataset.t; $$('#fnTabs button').forEach(x=>x.classList.toggle('on',x===b)); loadFin(); });
})();
const _show0=show;
show=function(v){ if(v==='fin'){ if(!can('finance')) return toast('Нет доступа к разделу','err');
    $$('.view').forEach(x=>x.classList.toggle('on',x.id==='v-fin')); $$('#nav button').forEach(b=>b.classList.toggle('on',b.dataset.v==='fin')); $('#pageTitle').textContent=TITLES.fin; return loadFin(); }
  _show0(v); };
initSales=()=>loadAn();
const _boot0=boot; boot=async function(){ await _boot0(); $('#nav button[data-v=fin]')?.classList.toggle('hidden',!can('finance'));
  $$('#nav .grp').forEach(g=>{ let n=g.nextElementSibling, any=false; while(n&&!n.classList.contains('grp')){ if(!n.classList.contains('hidden')) any=true; n=n.nextElementSibling; } g.classList.toggle('hidden',!any); }); };

// ---------------------------------------------------------------- фильтры
const FL={preset:'month',from:'',to:'',store:'',category:'',brand:''};
(function(){ const r=presetRange('month'); FL.from=dISO(r[0]); FL.to=dISO(r[1]); })();
async function filters(){ if(!AF) AF=await api('/api/an/filters'); return AF; }
function fbar(el, opt, reload){
  const o=(a,v,t)=>`<option value="${esc(v)}" ${a==v?'selected':''}>${esc(t)}</option>`;
  el.innerHTML=`<select data-k="preset">${Object.entries(PRESETS).map(([k,t])=>o(FL.preset,k,t)).join('')}</select>
    <input type="date" data-k="from" value="${FL.from}"><input type="date" data-k="to" value="${FL.to}">
    <select data-k="store">${o(FL.store,'',AF.stores.length>1||!AF.stores.length?'Все магазины':AF.stores[0].name)}${AF.stores.length>1?AF.stores.map(s=>o(FL.store,s.id,s.name)).join(''):''}</select>
    ${opt.cat?`<select data-k="category">${o(FL.category,'','Все категории')}${AF.categories.map(c=>o(FL.category,c,c)).join('')}</select>
      <select data-k="brand">${o(FL.brand,'','Все бренды')}${AF.brands.map(c=>o(FL.brand,c,c)).join('')}</select>`:''}
    <span class="grow"></span>${opt.extra||''}
    ${opt.kind?`<button onclick="anExport('${opt.kind}','xlsx')" title="Скачать Excel">⬇ Excel</button><button onclick="anExport('${opt.kind}','pdf')" title="Открыть для печати и сохранения в PDF">⬇ PDF</button>`:''}`;
  el.querySelectorAll('[data-k]').forEach(i=>i.onchange=()=>{ const k=i.dataset.k; FL[k]=i.value;
    if(k==='preset'&&i.value!=='custom'){ const r=presetRange(i.value); FL.from=dISO(r[0]); FL.to=dISO(r[1]); }
    if(k==='from'||k==='to') FL.preset='custom'; reload(); });
}
const qs=(extra='')=>`from=${FL.from}&to=${FL.to}&store=${FL.store}&category=${encodeURIComponent(FL.category)}&brand=${encodeURIComponent(FL.brand)}${extra}`;
function anExport(kind,fmt_){ const q=qs(kind==='products'?'&top=50':'');
  if(fmt_==='xlsx') location.href=`/api/an/export/${kind}.xlsx?${q}`; else window.open(`/report/${kind}?${q}`,'_blank'); }

// ---------------------------------------------------------------- графики
function vbars(el, S, o={}){ // S: [{label, v, v2?}]
  if(!el) return; const W=Math.max(260,el.clientWidth), H=o.h||220, L=58, B=22, T=10;
  const max=Math.max(1,...S.map(x=>Math.max(x.v||0,x.v2||0))); const step=10**Math.floor(Math.log10(max)); const top=Math.ceil(max/step)*step;
  const n=Math.max(1,S.length), bw=Math.min((W-L)/n, 70), y=v=>T+(H-B-T)*(1-Math.max(0,v)/top), every=Math.ceil(n/(o.labels||16)), two=S.some(x=>x.v2!=null);
  const grid=[0,.5,1].map(f=>`<line x1="${L}" x2="${W}" y1="${y(top*f)}" y2="${y(top*f)}" stroke="#eef0f3"/><text x="${L-6}" y="${y(top*f)+4}" text-anchor="end">${short(top*f)}</text>`).join('');
  const bars=S.map((x,i)=>{ const cx=L+i*bw, w=two?bw*.34:bw*.64;
    return `<g class="hv"><rect x="${cx}" y="${T}" width="${bw}" height="${H-B-T}" fill="transparent"/>
      ${two&&x.v2!=null?`<rect x="${cx+bw*.14}" y="${y(x.v2)}" width="${w}" height="${(H-B-T)*Math.max(0,x.v2)/top}" rx="3" fill="#cbd5e1"/>`:''}
      ${x.v!=null?`<rect x="${cx+(two?bw*.52:bw*.18)}" y="${y(x.v)}" width="${w}" height="${(H-B-T)*Math.max(0,x.v)/top}" rx="3" fill="var(--brand)"/>`:''}
      <title>${esc(x.tip||x.label)}: ${x.v==null?'—':fmt(x.v)}${two?`\n${o.l2||'Прошлый'}: ${fmt(x.v2)}`:''}</title>
      ${i%every===0?`<text x="${cx+bw/2}" y="${H-6}" text-anchor="middle">${esc(x.label)}</text>`:''}</g>`; }).join('');
  el.innerHTML=`<svg width="${W}" height="${H}" class="ch">${grid}${bars}</svg>`;
}
function hbars(rows, val, lbl, extra){ const mx=Math.max(1,...rows.map(r=>Math.abs(r[val]||0)));
  return rows.map(r=>`<div class="hb"><span class="nm ell" title="${esc(r[lbl])}">${esc(r[lbl])}</span><span class="bar"><i style="width:${Math.max(1,Math.abs(r[val]||0)/mx*100)}%"></i></span><b class="v">${fmt(r[val])}</b>${extra?extra(r):''}</div>`).join('')||'<div class="pad muted">Нет данных за период</div>'; }
const kpi=(l,v,sub='')=>`<div class="card kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="muted" style="font-size:12px;margin-top:2px">${sub}</div></div>`;

// ---------------------------------------------------------------- аналитика
async function loadAn(){
  await filters(); fbar($('#anF'),{cat:true,kind:AN_TAB},loadAn); const B=$('#anBody'); B.innerHTML='<div class="pad muted">Загрузка…</div>';
  try{ if(AN_TAB==='sales') await anSales(B); else if(AN_TAB==='products') await anProducts(B); else await anSeason(B); }
  catch(e){ B.innerHTML=`<div class="card red">${esc(e.message)}</div>`; }
}
let AN_D=null;
async function anSales(B){
  const d=AN_D=await api('/api/an/sales?'+qs()), c=d.cur, F=AF.finance, p=d.pay;
  B.innerHTML=`<div class="kpis" style="margin-bottom:12px">
      ${kpi('Выручка',fmt(c.net),(d.chg.net==null?'не было продаж ':chgT(d.chg.net)+' к ')+d.prev_period.map(x=>x.slice(8)+'.'+x.slice(5,7)).join('–'))}${kpi('Чеков',fmt(c.checks),chgT(d.chg.checks))}
      ${kpi('Средний чек',fmt(c.avg),chgT(d.chg.avg))}${kpi('Продано, ед.',fq(Math.round(c.qty*100)/100),chgT(d.chg.qty))}
      ${F?kpi('Валовая прибыль',`<span class="green">${fmt(c.profit)}</span>`,(d.chg.profit==null?'':chgT(d.chg.profit)+' · ')+'маржа '+c.margin+'%'):''}</div>
    <div class="ag">
      <div class="card w12"><div class="row"><b class="grow">Выручка по дням</b><span class="muted" style="font-size:12px">наведите на столбец</span></div><div id="anDay" style="margin-top:6px"></div></div>
      <div class="card"><b>Магазины</b><div style="overflow:auto;margin-top:6px"><table><thead><tr><th>Магазин</th><th class="n">Выручка</th><th class="n">Чеков</th>${F?'<th class="n">Прибыль</th><th class="n">Маржа</th>':''}</tr></thead><tbody>
        ${d.by_store.map(s=>`<tr><td><b>${esc(s.name)}</b></td><td class="n">${fmt(s.net)}</td><td class="n">${s.checks}</td>${F?`<td class="n green">${fmt(s.profit)}</td><td class="n">${s.margin}%</td>`:''}</tr>`).join('')||'<tr><td colspan="5" class="muted">Нет продаж</td></tr>'}</tbody></table></div></div>
      <div class="card"><b>Способы оплаты</b><div style="margin-top:6px">${hbars([{n:'Наличные',v:p.cash},{n:'Карта',v:p.card},{n:'QR (Payme / Click / Uzum)',v:p.qr},{n:'В долг (насия)',v:p.debt}],'v','n')}</div>
        <div class="row muted" style="font-size:12.5px;margin-top:8px;gap:16px"><span>Возвраты: <b class="red">${fmt(p.refunds)}</b></span><span>Скидки: <b>${fmt(p.discount)}</b></span></div></div>
      <div class="card"><b>Категории</b><div style="margin-top:6px;max-height:360px;overflow:auto">${hbars(d.by_category.slice(0,30),'net','name',F?r=>`<span class="muted" style="width:52px;text-align:right">${r.margin}%</span>`:null)}</div></div>
      <div class="card"><b>Бренды</b><div style="margin-top:6px;max-height:360px;overflow:auto">${hbars(d.by_brand.slice(0,30),'net','name',F?r=>`<span class="muted" style="width:52px;text-align:right">${r.margin}%</span>`:null)}</div></div>
      <div class="card w12"><b>Когда покупают: день недели × час</b><div style="overflow:auto;margin-top:8px">${heat(d.heat)}</div></div>
    </div>`;
  vbars($('#anDay'), d.by_day.map(x=>({label:x.d.slice(8)+'.'+x.d.slice(5,7), v:x.net, tip:x.d+' · чеков '+x.checks})));
}
function heat(H){ const mx=Math.max(1,...H.flat()), hrs=[...Array(24).keys()].filter(h=>H.some(r=>r[h]));
  if(!hrs.length) return '<div class="pad muted">Нет продаж за период</div>';
  return `<table class="heat"><tr><th></th>${hrs.map(h=>`<th>${h}</th>`).join('')}</tr>${['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].map((w,i)=>`<tr><th>${w}</th>${hrs.map(h=>{ const v=H[i][h], a=v/mx;
    return `<td title="${w} ${h}:00 — ${fmt(v)}" style="background:rgba(29,78,216,${v?(.08+a*.85).toFixed(2):0});color:${a>.55?'#fff':'#475467'}">${v?short(v).replace(' тыс','к'):''}</td>`; }).join('')}</tr>`).join('')}</table>`; }

async function anProducts(B){
  const d=await api('/api/an/products?'+qs('&top='+AN_TOP)), F=AF.finance, mx=d.matrix;
  const L={best:['Лидеры продаж',d.best],worst:['Худшие продажи',d.worst],zero_sales:['Есть остаток, нет продаж',d.zero_sales],zero_stock:['Продавались, остаток 0',d.zero_stock],risk:['Зона риска (< 3 дней)',d.risk],over:['Избыток (> 60 дней)',d.over]};
  const cnt={best:d.best.length,worst:d.worst.length,...d.counts};
  const cell=k=>{ const m=mx[k]||{n:0,net:0}; return `<td style="background:${{AX:'#ecfdf3',AY:'#ecfdf3',BX:'#eff6ff',AZ:'#fffaeb',BY:'#eff6ff',CX:'#fffaeb',BZ:'#fef3f2',CY:'#fef3f2',CZ:'#fef3f2'}[k]}"><b>${k}</b>${m.n} тов.<div class="muted" style="font-size:11.5px">${short(m.net)}</div></td>`; };
  const list=L[AN_LIST][1], stockL=['zero_sales','zero_stock','risk','over'].includes(AN_LIST);
  B.innerHTML=`<div class="ag">
    <div class="card w4"><b>ABC-анализ</b><div class="muted" style="font-size:12.5px;margin:2px 0 8px">A — 70% выручки, B — следующие 25%, C — последние 5%</div>
      ${['A','B','C'].map(k=>`<div class="dl"><span class="tg ${k}">${k}</span><span class="grow">${{A:'Основа выручки',B:'Средние',C:'Слабые'}[k]}</span><b>${d.abc_n[k]}</b><span class="muted">тов.</span></div>`).join('')}
      <div class="muted" style="font-size:12.5px;margin-top:10px">XYZ — стабильность спроса (по ${d.bucket==='неделя'?'неделям':'дням'}, ${d.days} дн.): X ≤ 10%, Y ≤ 25%, Z — нестабильный.</div></div>
    <div class="card w8"><b>Матрица ABC × XYZ</b><table class="abcx" style="margin-top:8px"><tr><td></td><td><b>X</b>стабильный</td><td><b>Y</b>колеблется</td><td><b>Z</b>случайный</td></tr>
      ${['A','B','C'].map(a=>`<tr><td><b>${a}</b></td>${['X','Y','Z'].map(x=>cell(a+x)).join('')}</tr>`).join('')}</table>
      <div class="muted" style="font-size:12px;margin-top:6px">AX/AY — держать в наличии всегда; CZ — кандидаты на вывод из ассортимента.</div></div>
    <div class="card w12"><div class="row wrap" style="gap:8px"><div class="seg" id="apL">${Object.entries(L).map(([k,v])=>`<button data-l="${k}" class="${k===AN_LIST?'on':''}">${v[0]} <span class="muted">${cnt[k]}</span></button>`).join('')}</div><span class="grow"></span>
      ${['best','worst'].includes(AN_LIST)?`<div class="seg" id="apT">${[10,20,30,50].map(n=>`<button data-n="${n}" class="${n===AN_TOP?'on':''}">Топ ${n}</button>`).join('')}</div>`:''}</div>
      <div style="overflow:auto;margin-top:8px;max-height:560px"><table><thead><tr><th>#</th><th>Товар</th><th>Категория</th><th>Бренд</th>${stockL?
        `<th class="n">Остаток</th><th class="n">Продано</th><th class="n">В день</th><th class="n">Хватит, дн.</th>${F?'<th class="n">По закупке</th>':''}`:
        `<th class="n">Продано</th><th class="n">Выручка</th><th class="n">Доля</th>${F?'<th class="n">Прибыль</th><th class="n">Маржа</th>':''}<th>ABC</th><th>XYZ</th>`}</tr></thead><tbody>
      ${list.map((r,i)=>`<tr><td class="muted">${i+1}</td><td>${esc(r.name)}</td><td class="muted">${esc(r.category||'')}</td><td class="muted">${esc(r.brand||'')}</td>${stockL?
        `<td class="n">${fq(r.stock)}</td><td class="n">${fq(Math.round(r.sold*100)/100)}</td><td class="n">${r.per_day}</td><td class="n">${r.cover??'—'}</td>${F?`<td class="n">${fmt(r.value)}</td>`:''}`:
        `<td class="n">${fq(Math.round(r.qty*100)/100)}</td><td class="n"><b>${fmt(r.net)}</b></td><td class="n">${r.share}%</td>${F?`<td class="n green">${fmt(r.profit)}</td><td class="n">${r.margin??'—'}%</td>`:''}<td><span class="tg ${r.abc}">${r.abc}</span></td><td><span class="tg ${r.xyz}">${r.xyz}</span></td>`}</tr>`).join('')||'<tr><td colspan="11" class="muted pad">Нет товаров в этой группе ✓</td></tr>'}
      </tbody></table></div></div></div>`;
  $$('#apL button').forEach(b=>b.onclick=()=>{ AN_LIST=b.dataset.l; anProducts(B); });
  $$('#apT button').forEach(b=>b.onclick=()=>{ AN_TOP=+b.dataset.n; anProducts(B); });
}

async function anSeason(B){
  const d=await api('/api/an/season?'+qs()), [y0,y1]=d.years;
  const best=d.weekday.reduce((a,b)=>b.net>a.net?b:a,{net:-1}), bh=d.hours.reduce((a,b)=>b.net>a.net?b:a,{net:-1});
  B.innerHTML=`<div class="ag">
    <div class="card w12"><div class="row"><b class="grow">Выручка по месяцам: ${y1} к ${y0}</b><span class="muted" style="font-size:12px"><span style="display:inline-block;width:10px;height:10px;background:#cbd5e1;border-radius:2px"></span> ${y0} &nbsp; <span style="display:inline-block;width:10px;height:10px;background:var(--brand);border-radius:2px"></span> ${y1}</span></div>
      <div id="snM" style="margin-top:6px"></div>
      <div style="overflow:auto"><table style="margin-top:8px"><tr><th></th>${d.months.map(m=>`<th class="n">${m.label}</th>`).join('')}</tr>
        <tr><td class="muted">${y0}</td>${d.months.map(m=>`<td class="n">${short(m.prev)}</td>`).join('')}</tr>
        <tr><td><b>${y1}</b></td>${d.months.map(m=>`<td class="n"><b>${m.cur==null?'':short(m.cur)}</b></td>`).join('')}</tr>
        <tr><td class="muted">Изм.</td>${d.months.map(m=>`<td class="n">${m.chg==null?'':chgT(m.chg)}</td>`).join('')}</tr></table></div>
      <div class="muted" style="font-size:12px;margin-top:6px">Месяцы считаются целиком за два года, фильтр периода здесь не применяется; магазин, категория и бренд — применяются.</div></div>
    <div class="card"><div class="row"><b class="grow">Дни недели</b><span class="muted" style="font-size:12.5px">${best.net>0?'пик — '+best.label:''}</span></div><div id="snW" style="margin-top:6px"></div></div>
    <div class="card"><div class="row"><b class="grow">Часы</b><span class="muted" style="font-size:12.5px">${bh.net>0?'пик — '+bh.label+':00':''}</span></div><div id="snH" style="margin-top:6px"></div></div>
    <div class="card w12"><b>Сезонность категорий</b> <span class="muted" style="font-size:12.5px">— доля месяца в годовой выручке категории, оба года</span><div style="overflow:auto;margin-top:8px">
      <table class="heat" style="width:100%"><tr><th style="text-align:left">Категория</th>${['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек'].map(m=>`<th>${m}</th>`).join('')}<th>Пик</th></tr>
      ${Object.entries(d.category_months).map(([c,v])=>{ const s=v.reduce((a,b)=>a+b,0)||1, mx=Math.max(...v)||1;
        return `<tr><th style="text-align:left;color:var(--text)">${esc(c)}</th>${v.map(x=>`<td title="${fmt(x)}" style="height:26px;background:rgba(29,78,216,${x?(.06+x/mx*.8).toFixed(2):0});color:${x/mx>.55?'#fff':'#475467'}">${x?Math.round(x/s*100)+'%':''}</td>`).join('')}<td><b>${d.category_peak[c]||''}</b></td></tr>`; }).join('')||'<tr><td class="muted pad">Нет данных</td></tr>'}</table></div></div>
  </div>`;
  vbars($('#snM'), d.months.map(m=>({label:m.label, v:m.cur, v2:m.prev, tip:m.label+' '+y1})), {l2:String(y0)});
  vbars($('#snW'), d.weekday.map(x=>({label:x.label, v:x.net})), {h:180});
  vbars($('#snH'), d.hours.filter(x=>x.net||(+x.label>=7&&+x.label<=22)).map(x=>({label:x.label, v:x.net, tip:x.label+':00'})), {h:180});
}

// ---------------------------------------------------------------- финансы
async function loadFin(){
  await filters(); const B=$('#fnBody');
  fbar($('#fnF'),{cat:false,kind:FN_TAB==='balances'?null:FN_TAB,extra:FN_TAB==='expenses'&&AF.can_expense?'<button class="p" onclick="addExpense()">+ Расход</button>':''},loadFin);
  if(FN_TAB==='balances') $$('#fnF [data-k=preset],#fnF [data-k=from],#fnF [data-k=to]').forEach(x=>x.classList.add('hidden'));
  B.innerHTML='<div class="pad muted">Загрузка…</div>';
  try{ await ({pnl:finPnl,cashflow:finCash,expenses:finExp,balances:finBal})[FN_TAB](B); }catch(e){ B.innerHTML=`<div class="card red">${esc(e.message)}</div>`; }
}
async function finPnl(B){
  const d=await api('/api/fin/pnl?'+qs()), S=d.stores, T=d.total, many=S.length>1;
  const row=(cls,l,f,neg)=>`<tr class="${cls}"><td>${l}</td>${S.map(s=>`<td class="n">${fv(f(s),neg)}</td>`).join('')}${many?`<td class="n"><b>${fv(f(T),neg)}</b></td>`:''}</tr>`;
  const fv=(v,neg)=>v?(neg?'−':'')+fmt(v):'<span class="muted">0</span>';
  B.innerHTML=`<div class="kpis" style="margin-bottom:12px">${kpi('Выручка',fmt(T.revenue))}${kpi('Валовая прибыль',fmt(T.gross),'маржа '+T.gross_margin+'%')}
      ${kpi('Расходы',fmt(T.expenses_total),T.revenue?Math.round(T.expenses_total/T.revenue*1000)/10+'% от выручки':'')}${kpi('Чистая прибыль',`<span class="${T.net>=0?'green':'red'}">${fmt(T.net)}</span>`,'маржа '+T.net_margin+'%')}</div>
    <div class="card"><div style="overflow:auto"><table class="pl"><thead><tr><th>Статья</th>${S.map(s=>`<th class="n">${esc(s.store)}</th>`).join('')}${many?'<th class="n">Вся сеть</th>':''}</tr></thead><tbody>
      ${row('t','Выручка',x=>x.revenue)}${row('s','Себестоимость проданного',x=>x.cost,1)}${row('t','Валовая прибыль',x=>x.gross)}
      <tr class="s"><td class="muted">Валовая маржа</td>${S.map(s=>`<td class="n muted">${s.gross_margin}%</td>`).join('')}${many?`<td class="n muted">${T.gross_margin}%</td>`:''}</tr>
      ${d.categories.map(c=>row('s',c,x=>x.expenses[c],1)).join('')}${row('','<b>Расходы всего</b>',x=>x.expenses_total,1)}
      <tr class="t"><td>Чистая прибыль</td>${S.map(s=>`<td class="n ${s.net>=0?'green':'red'}">${fmt(s.net)}</td>`).join('')}${many?`<td class="n ${T.net>=0?'green':'red'}">${fmt(T.net)}</td>`:''}</tr>
      <tr class="s"><td class="muted">Чистая маржа</td>${S.map(s=>`<td class="n muted">${s.net_margin}%</td>`).join('')}${many?`<td class="n muted">${T.net_margin}%</td>`:''}</tr>
    </tbody></table></div>
    <div class="muted" style="font-size:12.5px;margin-top:10px">Себестоимость — по закупочной цене в момент продажи. Расходы — записанные на кассах (статья определяется по названию) и внесённые в Центре во вкладке «Расходы». Оплаты поставщикам — не расход, они в «Денежном потоке».</div></div>`;
}
async function finCash(B){
  const d=await api('/api/fin/cashflow?'+qs()), T=d.total;
  B.innerHTML=`<div class="kpis" style="margin-bottom:12px">${kpi('Поступления',`<span class="green">${fmt(T.inflow)}</span>`,`нал ${short(T.in_cash)} · карта ${short(T.in_card)} · QR ${short(T.in_qr)}`)}
      ${kpi('Погашения долгов',fmt(T.in_debt),'клиенты вернули насию')}${kpi('Выплаты',`<span class="red">${fmt(T.outflow)}</span>`,`возвраты ${short(T.out_refund)} · расходы ${short(T.out_exp)} · поставщикам ${short(T.out_sup)}`)}
      ${kpi('Итого за период',`<span class="${T.net>=0?'green':'red'}">${fmt(T.net)}</span>`)}${kpi('Инкассация',fmt(T.out_cash),'изъятия из касс, не расход')}</div>
    <div class="card" style="margin-bottom:12px"><div class="row"><b class="grow">Поступления и выплаты по дням</b><span class="muted" style="font-size:12px"><span style="display:inline-block;width:10px;height:10px;background:var(--brand);border-radius:2px"></span> поступило &nbsp; <span style="display:inline-block;width:10px;height:10px;background:#cbd5e1;border-radius:2px"></span> выплачено</span></div><div id="cfCh" style="margin-top:6px"></div></div>
    <div class="card"><div style="overflow:auto;max-height:520px"><table><thead><tr><th>Дата</th><th class="n">Наличные</th><th class="n">Карта</th><th class="n">QR</th><th class="n">Долги</th><th class="n">Поступило</th><th class="n">Возвраты</th><th class="n">Расходы</th><th class="n">Поставщикам</th><th class="n">Выплачено</th><th class="n">Итого</th><th class="n">Инкассация</th></tr></thead><tbody>
      ${d.days.slice().reverse().map(x=>`<tr><td>${x.d}</td><td class="n">${fmt(x.in_cash)}</td><td class="n">${fmt(x.in_card)}</td><td class="n">${fmt(x.in_qr)}</td><td class="n">${fmt(x.in_debt)}</td><td class="n green"><b>${fmt(x.inflow)}</b></td>
        <td class="n">${fmt(x.out_refund)}</td><td class="n">${fmt(x.out_exp)}</td><td class="n">${fmt(x.out_sup)}</td><td class="n red"><b>${fmt(x.outflow)}</b></td><td class="n"><b class="${x.net>=0?'':'red'}">${fmt(x.net)}</b></td><td class="n muted">${fmt(x.out_cash)}</td></tr>`).join('')||'<tr><td colspan="12" class="muted">Нет движения денег за период</td></tr>'}
    </tbody></table></div></div>`;
  vbars($('#cfCh'), d.days.map(x=>({label:x.d.slice(8)+'.'+x.d.slice(5,7), v:x.inflow, v2:x.outflow, tip:x.d+' поступило'})), {l2:'Выплачено'});
}
async function finExp(B){
  const d=await api('/api/fin/expenses?'+qs()), by={};
  d.items.forEach(x=>by[x.cat]=(by[x.cat]||0)+x.amount); const tot=d.items.reduce((a,x)=>a+x.amount,0);
  B.innerHTML=`<div class="ag"><div class="card w4"><div class="row"><b class="grow">По статьям</b><b>${fmt(tot)}</b></div><div style="margin-top:6px">${hbars(d.categories.map(c=>({n:c,v:by[c]||0})),'v','n')}</div></div>
    <div class="card w8"><div style="overflow:auto;max-height:560px"><table><thead><tr><th>Дата</th><th>Магазин</th><th>Статья</th><th class="n">Сумма</th><th>Комментарий</th><th>Кто</th><th></th></tr></thead><tbody>
      ${d.items.map(x=>`<tr><td style="white-space:nowrap">${x.created_at.slice(0,16)}</td><td>${esc(x.store||'Вся сеть')}</td><td>${esc(x.cat)}${x.category&&x.category!==x.cat?`<div class="muted" style="font-size:11.5px">${esc(x.category)}</div>`:''}</td>
        <td class="n"><b>${fmt(x.amount)}</b></td><td class="muted">${esc(x.note||'')}</td><td class="muted" style="white-space:nowrap">${esc(x.user_name||'')}<div style="font-size:11px">${x.source==='hq'?'Центр':'касса'}</div></td>
        <td>${x.source==='hq'&&AF.can_expense?`<button class="ghost" onclick="delExpense('${x.uid}')" title="Удалить">✕</button>`:''}</td></tr>`).join('')||'<tr><td colspan="7" class="muted pad">Расходов за период нет</td></tr>'}
    </tbody></table></div></div></div>`;
}
function addExpense(){
  const net=AF.stores.length>1||ME.stores==null;
  const m=modal(`<h3>Новый расход</h3><div class="grid2"><div><label>Статья</label><select id="exC">${AF.expense_categories.map(c=>`<option>${esc(c)}</option>`).join('')}</select></div>
      <div><label>Сумма, сум</label><input id="exA" type="number" min="1" step="any" autofocus></div></div>
    <div class="grid2"><div><label>Магазин</label><select id="exS">${ME.stores==null?'<option value="">Вся сеть (общий расход)</option>':''}${AF.stores.map(s=>`<option value="${s.id}" ${String(FL.store)===String(s.id)?'selected':''}>${esc(s.name)}</option>`).join('')}</select></div>
      <div><label>Дата</label><input id="exD" type="date" value="${dISO(new Date())}"></div></div>
    <div class="grid2"><div><label>Способ оплаты</label><select id="exM"><option value="bank">Перечислением</option><option value="cash">Наличными (не из кассы)</option><option value="card">Картой</option></select></div><div></div></div>
    <label>Комментарий</label><input id="exN" placeholder="Например: аренда за ноябрь">
    <div class="muted" style="font-size:12px;margin-top:8px">Расходы, оплаченные из кассы магазина, вносить не нужно — они приходят с кассы автоматически.</div>
    <div class="actions"><button onclick="closeM()">Отмена</button><button class="p" id="exOk">Сохранить</button></div>`);
  const g=s=>m.querySelector(s).value;
  m.querySelector('#exOk').onclick=async()=>{ try{ await api('/api/fin/expenses',{method:'POST',body:{category:g('#exC'),amount:g('#exA'),store_id:g('#exS'),date:g('#exD'),method:g('#exM'),note:g('#exN')}});
    m.remove(); toast('Расход сохранён','ok'); loadFin(); }catch(e){ toast(e.message,'err'); } };
}
async function delExpense(uid){ if(!confirm('Удалить расход? Действие попадёт в журнал.')) return; try{ await api('/api/fin/expenses/'+uid,{method:'DELETE'}); loadFin(); }catch(e){ toast(e.message,'err'); } }
async function finBal(B){
  const d=await api('/api/fin/balances'), S=FL.store?d.stores.filter(s=>String(s.id)===String(FL.store)):d.stores, T={};
  ['customers_debt','suppliers_debt','cash_in_drawer','stock_value'].forEach(k=>T[k]=S.reduce((a,s)=>a+(+s[k]||0),0));
  B.innerHTML=`<div class="kpis" style="margin-bottom:12px">${kpi('Нам должны клиенты',fmt(T.customers_debt),'насия')}${kpi('Мы должны поставщикам',fmt(T.suppliers_debt))}
      ${kpi('Наличные в кассах',fmt(T.cash_in_drawer),'по открытым сменам')}${kpi('Товар на складах',fmt(T.stock_value),'по закупочной цене')}</div>
    <div class="card"><div style="overflow:auto"><table><thead><tr><th>Магазин</th><th>Данные на</th><th class="n">Должны клиенты</th><th class="n">Должников</th><th class="n">Долг поставщикам</th><th class="n">Наличные в кассе</th><th class="n">Товар (закупка)</th></tr></thead><tbody>
      ${S.map(s=>`<tr><td><b>${esc(s.name)}</b></td><td class="muted">${s.last_seen?s.last_seen.slice(0,16):'не подключался'}</td><td class="n">${s.customers_debt==null?'—':fmt(s.customers_debt)}</td><td class="n">${s.debtors??'—'}</td>
        <td class="n">${s.suppliers_debt==null?'—':fmt(s.suppliers_debt)}</td><td class="n">${s.cash_in_drawer==null?(s.shift===false?'<span class="muted">смена закрыта</span>':'—'):fmt(s.cash_in_drawer)}</td><td class="n">${s.stock_value==null?'—':fmt(s.stock_value)}</td></tr>`).join('')||'<tr><td colspan="7" class="muted">Нет магазинов</td></tr>'}
    </tbody></table></div><div class="muted" style="font-size:12.5px;margin-top:10px">Остатки долгов и наличных присылает касса при каждой синхронизации (раз в минуту). «—» — касса ещё не присылала эти данные (обновится после установки новой версии).</div></div>`;
}
