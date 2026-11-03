// =====================================================================
//  Касса (этап 5): промокоды в чеке, выбор QR-провайдера (Payme/Click/Uzum),
//  управление промокодами, платёжные системы, отчёт по кассиру.
// =====================================================================
let PROMO=null, QR_PROV='';
const PROV_T={payme:'Payme',click:'Click',uzum:'Uzum',other:'Другой QR'};
const enabledProv=()=>((SET.qr_providers||'payme,click,uzum').split(',').filter(Boolean));

// ---------------------------------------------------------------- промокод в чеке
function promoCalc(base){
  if(!PROMO) return {d:0};
  if(base < (PROMO.min_sum||0)) return {d:0, warn:`Промокод ${PROMO.code} — от суммы ${fmt(PROMO.min_sum)}`};
  let d = PROMO.kind==='percent' ? base*PROMO.value/100 : PROMO.value;
  if(PROMO.max_discount) d=Math.min(d,PROMO.max_discount);
  return {d:Math.round(Math.min(d,base))};
}
const _ctP = cartTotals;
cartTotals = function(){
  const t=_ctP(); if(!PROMO) return t;
  const ld=t.disc-t.check, pc=promoCalc(t.sub-ld);
  const disc=Math.min(t.disc+pc.d, t.sub);
  return {...t, disc, total:Math.round((t.sub-disc)*100)/100, pcode:pc.d, pwarn:pc.warn};
};
const _rcP = renderCart;
renderCart = function(){
  _rcP();
  let box=$('#promoRow');
  if(!box){ $('#tDisc').closest('.row').insertAdjacentHTML('afterend','<div id="promoRow" style="margin-top:4px"></div>'); box=$('#promoRow'); }
  if(!PROMO){ box.innerHTML=CART.length?'<a href="#" onclick="askPromo();return false" style="font-size:13px">+ Промокод</a>':''; return; }
  const t=cartTotals();
  box.innerHTML=`<div class="row" style="font-size:13px"><span class="grow">🎟 <b>${esc(PROMO.code)}</b> <span class="muted">${PROMO.kind==='percent'?PROMO.value+'%':fmt(PROMO.value)}</span></span>
    <b class="red">${t.pcode?'−'+fmt(t.pcode):''}</b><a href="#" onclick="PROMO=null;renderCart();return false" title="Убрать промокод" style="margin-left:6px">✕</a></div>${t.pwarn?`<div class="amber" style="font-size:12px">${esc(t.pwarn)}</div>`:''}`;
};
function askPromo(){
  const m=modal(`<h3>🎟 Промокод</h3><input class="bigin" id="pcIn" style="text-align:left;text-transform:uppercase" placeholder="Введите или отсканируйте код" autofocus>
    <div class="actions"><button onclick="closeModal()">Отмена</button><button class="p" id="pcOk">Применить</button></div>`);
  const go=async()=>{ const code=m.querySelector('#pcIn').value.trim(); if(!code) return;
    const t=_ctP();
    try{ const r=await api('/api/promocodes/check',{method:'POST',body:{code,base:t.sub-(t.disc-t.check),customer_id:CUSTOMER?CUSTOMER.id:null}});
      PROMO=r; closeModal(m); renderCart(); toast(`Промокод ${r.code}: скидка ${fmt(r.discount)}`,'ok'); }catch(e){ toast(e.message,'err'); } };
  m.querySelector('#pcOk').onclick=go; m.querySelector('#pcIn').onkeydown=e=>{ if(e.key==='Enter') go(); };
}
const _apiP = api;
api = async function(url,opt={}){
  const b=opt.body;
  if(url==='/api/sales' && b && typeof b==='object'){
    if(PROMO) b.promo_code=PROMO.code;
    if(typeof PAY_QR!=='undefined' && PAY_QR) b.qr_provider=QR_PROV||'other';
  }
  return _apiP(url,opt);
};
const _afterP = afterSale;
afterSale = function(...a){ PROMO=null; renderCart(); return _afterP(...a); };
const _clearP = clearCart;
clearCart = function(ask){ _clearP(ask); if(!CART.length){ PROMO=null; renderCart(); } };

// ---------------------------------------------------------------- QR: выбор провайдера
const _opP = openPay;
openPay = function(mode){
  const before=$$('#modalRoot .modal').length;
  _opP(mode);
  if(mode!=='qr' || $$('#modalRoot .modal').length===before) return;
  const m=$('#modalRoot .modal:last-child'), list=[...enabledProv(),'other'];
  if(!list.includes(QR_PROV)) QR_PROV=list[0];
  const h=m.querySelector('h3');
  h.insertAdjacentHTML('afterend',`<div class="seg" id="qrProv" style="margin:6px 0 4px">${list.map(p=>`<button data-p="${p}" class="${p===QR_PROV?'on':''}">${PROV_T[p]}</button>`).join('')}</div>`);
  m.querySelectorAll('#qrProv button').forEach(b=>b.onclick=e=>{ e.preventDefault(); QR_PROV=b.dataset.p; m.querySelectorAll('#qrProv button').forEach(x=>x.classList.toggle('on',x===b)); });
};

// ---------------------------------------------------------------- промокоды (раздел «Акции и цены»)
async function loadPromocodes(){
  let card=$('#pcCard');
  if(!card){ $('#v-promos').insertAdjacentHTML('beforeend',`<div class="card" id="pcCard" style="margin-top:12px"><div class="row wrap" style="margin-bottom:10px"><h2 class="grow" style="margin:0">Промокоды</h2>
      <button class="p" onclick="editPromocode()">+ Промокод</button></div>
      <p class="muted" style="margin-top:0">Скидка на весь чек по коду: процент или сумма, с минимальной суммой чека, сроком, лимитом использований и «один раз на клиента». Кассир вводит код в чеке — скидку считает система.</p>
      <div class="tbl-wrap"><table><thead><tr><th>Код</th><th>Скидка</th><th>Условия</th><th>Срок</th><th class="n">Использовано</th><th class="n">Выдано скидок</th><th>Статус</th><th></th></tr></thead><tbody id="pcBody"></tbody></table></div></div>`); }
  const rows=await api('/api/promocodes'); window._PC=rows; const td=new Date().toLocaleDateString('sv-SE');
  $('#pcBody').innerHTML=rows.map(p=>{ const exp=p.date_to&&p.date_to<td, lim=p.max_uses&&p.uses>=p.max_uses;
    return `<tr style="${p.active&&!exp&&!lim?'':'opacity:.55'}"><td><b style="font-family:monospace;font-size:14px">${esc(p.code)}</b>${p.note?`<div class="muted" style="font-size:11.5px">${esc(p.note)}</div>`:''}</td>
    <td><b>${p.kind==='percent'?p.value+'%':fmt(p.value)}</b>${p.max_discount?` <span class="muted">до ${fmt(p.max_discount)}</span>`:''}</td>
    <td style="font-size:12.5px">${[p.min_sum?'от '+fmt(p.min_sum):'',p.once_per_customer?'1 раз на клиента':''].filter(Boolean).join(' · ')||'—'}</td>
    <td style="font-size:12.5px">${p.date_from||p.date_to?`${p.date_from||'…'} — ${p.date_to||'…'}`:'бессрочно'}</td>
    <td class="n">${p.uses}${p.max_uses?' / '+p.max_uses:''}</td><td class="n">${fmt(p.given)}</td>
    <td>${!p.active?'<span class="badge">выключен</span>':exp?'<span class="badge">истёк</span>':lim?'<span class="badge pend">лимит исчерпан</span>':'<span class="badge ok">действует</span>'}</td>
    <td><button class="ghost" onclick="editPromocode(${p.id})">Изм.</button></td></tr>`; }).join('')||'<tr><td colspan="8" class="muted">Промокодов нет. Например: WELCOME10 — 10% на первую покупку, один раз на клиента.</td></tr>';
}
function editPromocode(id){
  const p=(window._PC||[]).find(x=>x.id===id)||{kind:'percent',active:1};
  const m=modal(`<h3>${p.id?'Промокод':'Новый промокод'}</h3>
    <div class="grid2"><div><label>Код</label><input id="ecC" value="${esc(p.code||'')}" style="text-transform:uppercase;font-family:monospace" placeholder="WELCOME10" autofocus></div>
      <div><label>Комментарий</label><input id="ecN" value="${esc(p.note||'')}" placeholder="Для Instagram, для открытия…"></div></div>
    <div class="grid3"><div><label>Тип</label><select id="ecK"><option value="percent">Процент</option><option value="fixed" ${p.kind==='fixed'?'selected':''}>Сумма</option></select></div>
      <div><label>Скидка</label><input id="ecV" type="number" value="${p.value||''}"></div><div><label>Не больше, сум (0 — без лимита)</label><input id="ecX" type="number" value="${p.max_discount||''}"></div></div>
    <div class="grid3"><div><label>Мин. сумма чека</label><input id="ecM" type="number" value="${p.min_sum||''}"></div><div><label>С даты</label><input id="ecF" type="date" value="${p.date_from||''}"></div><div><label>По дату</label><input id="ecT" type="date" value="${p.date_to||''}"></div></div>
    <div class="grid3"><div><label>Лимит использований (0 — без лимита)</label><input id="ecL" type="number" value="${p.max_uses||''}"></div>
      <div><label>Один раз на клиента</label><select id="ecO"><option value="0">Нет</option><option value="1" ${p.once_per_customer?'selected':''}>Да — нужен клиент в чеке</option></select></div>
      <div><label>Статус</label><select id="ecA"><option value="1">Действует</option><option value="0" ${p.active?'':'selected'}>Выключен</option></select></div></div>
    <div class="actions"><button onclick="closeModal()">Отмена</button><button class="p" id="ecOk">Сохранить</button></div>`,'wide');
  const g=s=>m.querySelector(s).value;
  m.querySelector('#ecOk').onclick=async()=>{ try{ await api('/api/promocodes',{method:'POST',body:{id:p.id,code:g('#ecC'),note:g('#ecN'),kind:g('#ecK'),value:g('#ecV'),max_discount:g('#ecX'),
      min_sum:g('#ecM'),date_from:g('#ecF'),date_to:g('#ecT'),max_uses:g('#ecL'),once_per_customer:g('#ecO')==='1',active:+g('#ecA')}}); closeModal(m); toast('Сохранено','ok'); loadPromocodes(); }catch(e){ toast(e.message,'err'); } };
}

// ---------------------------------------------------------------- платёжные системы (Настройки)
async function loadIntegrations(){
  let card=$('#intCard');
  if(!card){ const host=$('#v-settings .grid2')||$('#v-settings');
    host.insertAdjacentHTML('beforeend',`<div class="card" id="intCard" style="grid-column:1/-1"><h2>Платёжные системы (QR)</h2>
      <p class="muted" style="margin-top:0">QR-оплата учитывается по провайдерам в чеке, Z-отчёте и отчётах. Автоматическая проверка оплаты по API включается только после того, как провайдер выдал ключи и подключение проверено, — до этого кассир подтверждает оплату вручную, увидев её в приложении.</p>
      <label>Кнопки провайдеров на кассе</label><div id="intEn" class="row" style="gap:14px"></div><input type="hidden" data-s="qr_providers">
      <div class="grid3" style="margin-top:6px">
        <div><label>Payme — ID кассы (merchant)</label><input data-s="payme_merchant_id"><label>Payme — ключ</label><input data-s="payme_key" type="password"></div>
        <div><label>Click — service_id</label><input data-s="click_service_id"><label>Click — merchant_id</label><input data-s="click_merchant_id"><label>Click — secret_key</label><input data-s="click_secret" type="password"></div>
        <div><label>Uzum — merchant ID</label><input data-s="uzum_merchant_id"><label>Uzum — ключ</label><input data-s="uzum_secret" type="password"></div></div>
      <div id="intSt" style="margin-top:10px"></div>
      <details style="margin-top:8px"><summary><b>Журнал обмена с провайдерами</b></summary><div id="intLog"></div></details></div>`);
    const s=await api('/api/settings'); $$('#intCard [data-s]').forEach(el=>{ el.value=s[el.dataset.s]??''; }); }
  const r=await api('/api/integrations'); const en=new Set(r.enabled);
  $('#intEn').innerHTML=r.items.map(i=>`<label class="row" style="margin:0;color:var(--text)"><input type="checkbox" value="${i.key}" ${en.has(i.key)?'checked':''} style="width:auto"> ${i.title}</label>`).join('');
  const sync=()=>{ $('#intCard [data-s=qr_providers]').value=$$('#intEn input:checked').map(x=>x.value).join(','); }; sync();
  $$('#intEn input').forEach(x=>x.onchange=sync);
  const st=Object.fromEntries(r.stats.map(x=>[x.prov,x]));
  $('#intSt').innerHTML=`<table><thead><tr><th>Провайдер</th><th>Режим</th><th>Пояснение</th><th class="n">QR-оплат за 30 дн.</th></tr></thead><tbody>${r.items.map(i=>`<tr><td><b>${i.title}</b></td>
    <td><span class="badge ${i.keys_set?'pend':''}">${i.active?'● API подключён':'○ '+esc(i.mode)}</span></td><td class="muted" style="font-size:12.5px">${esc(i.note)}<div style="font-size:11.5px">${esc(i.doc)}</div></td>
    <td class="n">${st[i.key]?`${st[i.key].n} · ${fmt(st[i.key].sum)}`:'—'}</td></tr>`).join('')}</tbody></table>`;
  $('#intLog').innerHTML=r.log.length?`<table>${r.log.map(l=>`<tr><td class="muted">${l.created_at}</td><td>${esc(l.provider)}</td><td>${esc(l.action)}</td><td>${esc(l.status)}</td><td>${esc(l.error||'')}</td></tr>`).join('')}</table>`:'<div class="muted" style="padding:8px 0">Обменов с API не было — интеграции не подключены.</div>';
}

// ---------------------------------------------------------------- отчёт по кассиру
async function cashierReport(){
  const adm=ME.role==='admin', td=today();
  const m=modal(`<h3>Отчёт по кассиру</h3><div class="grid3"><div><label>С</label><input type="date" id="crF" value="${td}"></div><div><label>По</label><input type="date" id="crT" value="${td}"></div>
      <div>${adm?'<label>Кассир</label><select id="crU"><option value="">Все</option></select>':''}</div></div>
    <div id="crRes" style="margin-top:10px"></div>
    <div class="actions"><button onclick="closeModal()">Закрыть</button><button id="crPrint">🖨 Печать</button><button class="p" id="crGo">Показать</button></div>`,'wide');
  const qs=()=>`from=${m.querySelector('#crF').value}&to=${m.querySelector('#crT').value}${adm&&m.querySelector('#crU').value?'&user='+m.querySelector('#crU').value:''}`;
  const run=async()=>{ const r=await api('/api/rep/cashier?'+qs());
    if(adm&&m.querySelector('#crU').options.length===1) m.querySelector('#crU').innerHTML+=r.users.map(u=>`<option value="${u.id}">${esc(u.name)}</option>`).join('');
    m.querySelector('#crRes').innerHTML=r.items.map(c=>`<div class="card" style="margin-bottom:10px"><div class="row"><b class="grow" style="font-size:15px">${esc(c.name)}</b><span class="muted">${c.hours} ч · ${c.shifts.length} смен</span></div>
      <div class="kpis" style="margin-top:8px;grid-template-columns:repeat(5,minmax(0,1fr))">${[['Выручка',fmt(c.net)],['Чеков',c.checks],['Средний чек',fmt(c.avg)],['Возвраты',`${c.refund_n} · ${fmt(c.refunds)}`],['Расхождение кассы',(c.cash_diff>0?'+':'')+fmt(c.cash_diff)]]
        .map(([l,v],i)=>`<div class="card kpi" style="padding:10px"><div class="l">${l}</div><div class="v ${i===4&&c.cash_diff?'red':''}" style="font-size:18px">${v}</div></div>`).join('')}</div>
      <table style="margin-top:8px"><tbody><tr><td>Наличные</td><td class="n">${fmt(c.cash)}</td><td>Карта</td><td class="n">${fmt(c.card)}</td><td>QR</td><td class="n">${fmt(c.qr)}${Object.keys(c.qr_by).length?`<div class="muted" style="font-size:11.5px">${Object.entries(c.qr_by).map(([k,v])=>PROV_T[k]+' '+fmt(v)).join(' · ')}</div>`:''}</td><td>Насия</td><td class="n">${fmt(c.debt)}</td></tr>
        <tr><td>Скидки вручную</td><td class="n">${fmt(c.manual_disc)}</td><td>Акции</td><td class="n">${fmt(c.promo_lines)}</td><td>Промокоды</td><td class="n">${fmt(c.promo_disc)} (${c.promo_n})</td><td>Бонусы</td><td class="n">${fmt(c.bonus)}</td></tr>
        <tr><td>Удалено строк</td><td class="n ${c.deleted>5?'amber':''}">${c.deleted}</td><td>Очищено чеков</td><td class="n ${c.cleared?'amber':''}">${c.cleared}</td><td>Подтверждений админа</td><td class="n">${c.approvals}</td><td>Не в ФМ</td><td class="n ${c.unfiscal?'red':''}">${c.unfiscal}</td></tr></tbody></table>
      <table style="margin-top:8px"><thead><tr><th>Смена</th><th>Открыта</th><th>Закрыта</th><th class="n">Выручка</th><th class="n">Должно быть нал.</th><th class="n">Сдано</th><th class="n">Расхождение</th></tr></thead><tbody>
      ${c.shifts.map(s=>`<tr><td>№${s.id}</td><td>${s.opened_at.slice(5,16)}</td><td>${s.closed_at?s.closed_at.slice(5,16):'<span class="green">открыта</span>'}</td><td class="n">${fmt(s.net)}</td><td class="n">${fmt(s.expected)}</td>
        <td class="n">${s.closing_cash==null?'—':fmt(s.closing_cash)}</td><td class="n ${s.diff?'red':''}">${s.diff==null?'—':(s.diff>0?'+':'')+fmt(s.diff)}</td></tr>`).join('')||'<tr><td class="muted" colspan="7">Смен нет</td></tr>'}</tbody></table></div>`).join('')||'<div class="muted">За период нет продаж и смен.</div>'; };
  m.querySelector('#crGo').onclick=run; m.querySelector('#crPrint').onclick=()=>window.open('/cashier-report?print=1&'+qs(),'_blank'); run();
}

// ---------------------------------------------------------------- подключение к разделам
const _showP = show;
show = function(v){
  _showP(v);
  if(v==='promos') loadPromocodes();
  else if(v==='settings') loadIntegrations();
  else if(v==='shifts'){ const c=$('#shiftCard'); if(c && !$('#crBtn')) c.insertAdjacentHTML('afterend','<div style="margin-top:12px"><button id="crBtn" onclick="cashierReport()">📋 Отчёт по кассиру</button></div>'); }
};
