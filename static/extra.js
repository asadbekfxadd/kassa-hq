// =====================================================================
//  Расширения интерфейса: клиенты, бонусы, насия, отложенные чеки,
//  подтверждение админом, экран покупателя, ценники, автозаказ, ABC,
//  кассовые операции, журнал, Telegram.
// =====================================================================
let CUSTOMER = null;

// ---------------------------------------------------------------- admin approval
function askAdminPin(reason){
  return new Promise(resolve=>{
    const m=modal(`<h3>🔐 Подтверждение администратора</h3><div class="muted">${esc(reason||'')}</div>
      <input class="bigin" id="admPin" type="password" inputmode="numeric" placeholder="PIN администратора" autofocus>
      <div class="actions"><button id="admNo">Отмена</button><button class="p" id="admOk">Подтвердить</button></div>`);
    const done=v=>{ closeModal(m); resolve(v); };
    m.querySelector('#admOk').onclick=()=>done(m.querySelector('#admPin').value.trim()||null);
    m.querySelector('#admNo').onclick=()=>done(null);
    m.querySelector('#admPin').onkeydown=e=>{ if(e.key==='Enter') done(e.target.value.trim()||null); if(e.key==='Escape') done(null); };
  });
}
// выполняет запрос; если сервер требует подтверждение админа — спрашивает PIN и повторяет
async function withAdmin(url, body, reason){
  try{ return await api(url,{method:'POST',body}); }
  catch(e){
    if(e.status!==403 || !/администратор/i.test(e.message)) throw e;
    const pin=await askAdminPin(e.message || reason);
    if(!pin) throw new Error('Отменено');
    return await api(url,{method:'POST',body:{...body, approve_pin:pin}});
  }
}

// ---------------------------------------------------------------- customer on POS
const custPct=c=>c?Math.max(+c.discount_pct||0,+c.level_discount||0):0;
function personalDisc(base){ const p=custPct(CUSTOMER); return p>0 && !CHECK_DISC ? Math.round(base*p/100) : 0; }
cartTotals = function(){
  const sub = CART.reduce((s,c)=>s+c.qty*c.price,0);
  const ld = CART.reduce((s,c)=>s+(+c.discount||0),0);
  const pers = personalDisc(sub-ld);
  const check = (+CHECK_DISC||0) + pers;
  const disc = Math.min(ld + check, sub);
  return {sub, disc, check, total: Math.round((sub-disc)*100)/100};
};
function renderCust(){
  const b=$('#custBox'); if(!b) return;
  if(!CUSTOMER){ b.innerHTML=`<div class="row"><span class="muted grow">Клиент не выбран</span><button class="ghost" onclick="pickCustomer()">👤 Клиент <kbd>F5</kbd></button></div>`; return; }
  const c=CUSTOMER;
  b.innerHTML=`<div class="row"><div class="grow"><b>👤 ${esc(c.name)}</b><div class="muted" style="font-size:12px">${esc(c.phone||'')}${c.level?` · <span class="badge ok">${esc(c.level)}</span>`:''}${custPct(c)?` · скидка ${custPct(c)}%`:''}${c.level_cashback?` · кешбэк ${c.level_cashback}%`:''}</div></div>
    <button class="ghost" style="padding:4px 9px" onclick="setCustomer(null)" title="Убрать клиента">✕</button></div>
    <div class="row" style="margin-top:6px;font-size:13px"><span class="grow">Бонусы: <b class="green">${fmt(c.bonus)}</b></span>${c.debt>0?`<span>Долг: <b class="red">${fmt(c.debt)}</b></span>`:''}</div>`;
}
function setCustomer(c){ CUSTOMER=c; renderCust(); renderCart(); focusScan(); }
function pickCustomer(cb){
  let list=[], idx=0, t=null;
  const m=modal(`<h3>Клиент</h3>
    <input id="pcQ" placeholder="Телефон, имя или карта — начните вводить" autofocus>
    <div id="pcList" style="margin-top:8px;max-height:320px;overflow:auto"></div>
    <div class="actions"><button onclick="closeModal()">Отмена</button><button class="p" id="pcNew">+ Новый клиент</button></div>`);
  const draw=()=>{ m.querySelector('#pcList').innerHTML = list.map((c,i)=>`<div data-i="${i}" style="padding:9px 10px;border-radius:8px;cursor:pointer;display:flex;justify-content:space-between;${i===idx?'background:#eff6ff':''}">
      <span><b>${esc(c.name)}</b> <span class="muted">${esc(c.phone||'')}</span></span><span class="muted">бонусы ${fmt(c.bonus)}${c.debt>0?` · <span class="red">долг ${fmt(c.debt)}</span>`:''}</span></div>`).join('') || '<div class="muted" style="padding:10px">Не найдено — нажмите «Новый клиент»</div>';
    m.querySelectorAll('[data-i]').forEach(d=>d.onclick=()=>choose(list[+d.dataset.i])); };
  const choose=c=>{ closeModal(m); if(cb) cb(c); else setCustomer(c); };
  const q=m.querySelector('#pcQ');
  q.oninput=()=>{ clearTimeout(t); t=setTimeout(async()=>{ list=await api('/api/customers?q='+encodeURIComponent(q.value.trim())); idx=0; draw(); },200); };
  q.onkeydown=e=>{ if(e.key==='ArrowDown'){ idx=Math.min(idx+1,list.length-1); draw(); e.preventDefault(); }
    else if(e.key==='ArrowUp'){ idx=Math.max(idx-1,0); draw(); e.preventDefault(); }
    else if(e.key==='Enter'){ if(list[idx]) choose(list[idx]); else m.querySelector('#pcNew').click(); }
    else if(e.key==='Escape') closeModal(m); };
  m.querySelector('#pcNew').onclick=()=>{ const v=q.value.trim(); closeModal(m); editCustomer(/\d{5,}/.test(v.replace(/\D/g,''))?{phone:v}:{name:v}, c=>{ if(cb) cb(c); else setCustomer(c); }); };
  q.dispatchEvent(new Event('input'));
}

// ---------------------------------------------------------------- customer display
let dispTimer=null;
function pushDisplay(extra){
  clearTimeout(dispTimer);
  dispTimer=setTimeout(()=>{
    const t=cartTotals();
    const state=Object.assign({status:'cart', total:t.total, discount:t.disc,
      items:CART.map(c=>({name:c.name, qty:fq(c.qty), price:c.price, discount:c.discount, sum:c.qty*c.price-(+c.discount||0)})),
      customer: CUSTOMER?CUSTOMER.name:null, bonus: CUSTOMER?CUSTOMER.bonus:null}, extra||{});
    fetch('/api/display',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(state)}).catch(()=>{});
  }, extra?0:120);
}
window.onCartChange=()=>{ renderCust(); pushDisplay(); };
function openDisplay(){ window.open('/display','kassa_display','popup,width=1280,height=800'); }

// ---------------------------------------------------------------- payment (replaces base openPay)
openPay = function(mode){
  if(!CART.length) return toast('Корзина пуста','err');
  if(!SHIFT) return openShiftDlg();
  if(mode==='debt' && !CUSTOMER){ toast('Для насии выберите клиента'); return pickCustomer(c=>{ setCustomer(c); openPay('debt'); }); }
  const t=cartTotals(); const total=t.total;
  const maxBonus = CUSTOMER ? Math.floor(Math.min(CUSTOMER.bonus, total*(+SET.bonus_max_pay_pct||0)/100)) : 0;
  const m=modal(`<h3>${mode==='debt'?'Насия (в долг)':'Оплата'} — ${fmt(total)} ${esc(SET.currency||'')}</h3>
    ${CUSTOMER?`<div class="muted">👤 ${esc(CUSTOMER.name)} · бонусов ${fmt(CUSTOMER.bonus)}${CUSTOMER.debt>0?` · долг ${fmt(CUSTOMER.debt)}`:''}</div>`:''}
    ${maxBonus>0?`<label>Списать бонусы (до ${fmt(maxBonus)})</label><div class="row"><input class="bigin" id="payBonus" value="0"><button id="bonusAll">Все</button></div>`:''}
    ${mode==='mixed'?`<label>Картой</label><input class="bigin" id="payCard" value="0">`:''}
    ${mode==='debt'?`<label>Предоплата наличными (остальное — в долг)</label>`:mode!=='card'?`<label>Получено наличными</label>`:''}
    ${mode!=='card'?`<input class="bigin" id="payGiven" value="${mode==='cash'?total:0}">
      ${mode!=='debt'?`<div class="quick" id="quick"></div>`:''}`:'<div class="muted" style="margin:10px 0">Проведите оплату на терминале, затем нажмите «Провести».</div>'}
    <div style="font-size:22px;margin-top:12px" id="payChange"></div>
    <div class="actions"><button onclick="closeModal()">Отмена (Esc)</button><button class="g" id="payGo">Провести (Enter)</button></div>`);
  const g=id=>m.querySelector(id), n=el=>el?(parseFloat((el.value||'0').replace(/\s/g,'').replace(',','.'))||0):0;
  const bonus=()=>Math.min(n(g('#payBonus')), maxBonus);
  const toPay=()=>Math.round((total-bonus())*100)/100;
  const card=()=> mode==='card'? toPay() : mode==='mixed'? Math.min(n(g('#payCard')), toPay()) : 0;
  const given=()=> mode==='card'? 0 : n(g('#payGiven'));
  const debt=()=> mode==='debt'? Math.max(0, Math.round((toPay()-given())*100)/100) : 0;
  const quick=()=>{ const q=g('#quick'); if(!q) return; const need=toPay()-card();
    const arr=[...new Set([need, Math.ceil(need/1000)*1000, Math.ceil(need/5000)*5000, Math.ceil(need/10000)*10000, Math.ceil(need/50000)*50000, Math.ceil(need/100000)*100000])].filter(x=>x>=need&&x>0).slice(0,5);
    q.innerHTML=arr.map(x=>`<button data-q="${x}">${fmt(x)}</button>`).join('');
    q.querySelectorAll('[data-q]').forEach(b=>b.onclick=()=>{ g('#payGiven').value=b.dataset.q; upd(); }); };
  const upd=()=>{
    const need=Math.round((toPay()-card())*100)/100;
    if(mode==='debt'){ g('#payChange').innerHTML=`В долг: <b class="red">${fmt(debt())}</b>${CUSTOMER.debt_limit>0?` <span class="muted" style="font-size:14px">(лимит ${fmt(CUSTOMER.debt_limit)}, будет ${fmt(CUSTOMER.debt+debt())})</span>`:''}`; return; }
    const ch=given()-need;
    g('#payChange').innerHTML = (bonus()?`<div style="font-size:15px" class="muted">Бонусами: ${fmt(bonus())} · к оплате ${fmt(toPay())}</div>`:'') +
      (mode==='card'?'' : (ch>=0?`Сдача: <b class="green">${fmt(ch)}</b>`:`Не хватает: <b class="red">${fmt(-ch)}</b>`));
  };
  m.querySelectorAll('input').forEach(i=>{ i.oninput=()=>{ if(i.id!=='payGiven'&&mode==='cash'){ g('#payGiven').value=toPay(); } quick(); upd(); };
    i.onkeydown=e=>{ if(e.key==='Enter') go(); if(e.key==='Escape') closeModal(m);} });
  g('#bonusAll')?.addEventListener('click',()=>{ g('#payBonus').value=maxBonus; if(mode==='cash') g('#payGiven').value=toPay(); quick(); upd(); });
  setTimeout(()=>{ const f=g(mode==='mixed'?'#payCard':'#payGiven')||g('#payGo'); f.focus(); f.select&&f.select(); },40);
  quick(); upd();
  let busy=false;
  async function go(){
    if(busy) return;
    const c=card(), gv=given(), need=Math.round((toPay()-c)*100)/100;
    if(mode!=='card' && mode!=='debt' && gv < need-0.001) return toast('Получено меньше суммы','err');
    busy=true; g('#payGo').disabled=true; g('#payGo').textContent='Фискализация…';
    try{
      const sale = await withAdmin('/api/sales',{
        items:CART.map(x=>({product_id:x.product_id,qty:x.qty,price:x.price,discount:x.discount,marking:x.marking||[]})),
        discount:t.check, bonus:bonus(), card:c, debt:debt(), given: mode==='card'? 0 : (mode==='debt'? gv : gv),
        customer_id: CUSTOMER?CUSTOMER.id:null}, 'Подтвердите операцию');
      closeModal(m); LAST_SALE=sale;
      const cust=CUSTOMER; CART=[]; CHECK_DISC=0; SEL=-1; CUSTOMER=null; renderCart();
      afterSale(sale, cust);
    }catch(e){
      toast(e.message,'err'); busy=false; g('#payGo').disabled=false; g('#payGo').textContent='Провести (Enter)';
      if(e.status===409){ closeModal(m); openShiftDlg(); }
    }
  }
  g('#payGo').onclick=go;
};
const _afterSale = afterSale;
afterSale = function(sale, cust){
  pushDisplay({status:'done', change:sale.change, bonus_earned:sale.bonus_earned, items:(sale.items||[]).map(i=>({name:i.name,qty:fq(i.qty),price:i.price,sum:i.total,discount:i.discount})), total:sale.total});
  setTimeout(()=>{ if(!CART.length) pushDisplay({status:'idle'}); }, 8000);
  if(sale.debt>0) toast(`Насия: ${fmt(sale.debt)} записано на ${cust?cust.name:'клиента'}`,'ok');
  if(sale.bonus_earned>0) toast(`Начислено бонусов: ${fmt(sale.bonus_earned)}`,'ok');
  _afterSale(sale);
};

// ---------------------------------------------------------------- line delete / clear with audit
const _delLine = delLine;
delLine = async function(i){
  const c=CART[i]; if(!c) return;
  if(ME.role!=='admin' && SET.delete_line_needs_admin==='1'){
    const pin=await askAdminPin('Удаление товара из чека: '+c.name); if(!pin) return;
    try{ await api('/api/auth/check',{method:'POST',body:{pin,reason:'Удаление строки: '+c.name}}); }catch(e){ return toast(e.message,'err'); }
  }
  api('/api/audit',{method:'POST',body:{action:'Удалена строка из чека',details:`${c.name} × ${fq(c.qty)} = ${fmt(c.qty*c.price)}`,severity:1}}).catch(()=>{});
  _delLine(i);
};
const _clearCart = clearCart;
clearCart = function(ask){
  if(ask && CART.length){
    if(!confirm('Очистить чек?')) return;
    const t=cartTotals();
    api('/api/audit',{method:'POST',body:{action:'Чек очищен без оплаты',details:`${CART.length} поз. на ${fmt(t.total)}`,severity:1}}).catch(()=>{});
  }
  CART=[]; CHECK_DISC=0; SEL=-1; if(ask) CUSTOMER=null; renderCart(); focusScan();
};

// ---------------------------------------------------------------- refunds with approval
doRefund = async function(s,m){
  const items=[...m.querySelectorAll('[data-item]')].map(i=>({item_id:+i.dataset.item, qty:parseFloat(i.value)||0})).filter(i=>i.qty>0);
  if(!items.length) return toast('Укажите количество к возврату','err');
  if(!SHIFT) return toast('Откройте смену','err');
  const sum=items.reduce((a,x)=>{ const it=s.items.find(y=>y.id===x.item_id); return a + x.qty*it.price - it.discount*x.qty/it.qty; },0);
  const debtPart = s.debt>0 ? Math.min(s.debt*sum/s.total, sum) : 0;
  const rest = sum - debtPart;
  let card=0;
  if(s.card>0 && s.cash>0){ const v=prompt(`Вернуть ${fmt(rest)}. Сколько на карту?`, Math.min(s.card,rest)); if(v===null) return; card=parseFloat(v)||0; }
  else if(s.card>0) card=rest;
  if(!confirm(`Оформить возврат на ${fmt(sum)}?${debtPart?`\nИз них ${fmt(debtPart)} спишется с долга клиента.`:''}`)) return;
  try{
    const r=await withAdmin(`/api/sales/${s.id}/refund`,{items,card},'Возврат');
    closeModal(m); toast(`Возврат №${r.id} оформлен`+(r.fiscal_status==='error'?' (ФМ: ошибка)':''), r.fiscal_status==='error'?'err':'ok');
    if(SET.auto_print!=='0') printUrl('/receipt/'+r.id);
    loadSalesIfOpen(); fiscalStatus();
  }catch(e){ toast(e.message,'err'); }
};

// ---------------------------------------------------------------- parked checks
async function parkCart(){
  if(!CART.length) return toast('Чек пуст');
  const title=prompt('Подпись для отложенного чека (необязательно)', CUSTOMER?CUSTOMER.name:'');
  if(title===null) return;
  await api('/api/parked',{method:'POST',body:{title, cart:CART, check_disc:CHECK_DISC, customer:CUSTOMER}});
  toast('Чек отложен','ok'); CART=[]; CHECK_DISC=0; SEL=-1; CUSTOMER=null; renderCart(); updParkBtn(); focusScan();
}
async function updParkBtn(){ try{ const l=await api('/api/parked'); $('#parkBtn').innerHTML=`Отложенные${l.length?` (${l.length})`:''} <kbd>F11</kbd>`; }catch(e){} }
async function showParked(){
  const l=await api('/api/parked');
  if(!l.length) return toast('Отложенных чеков нет');
  const m=modal(`<h3>Отложенные чеки</h3><table><tbody>${l.map(p=>{ const tot=p.data.cart.reduce((s,c)=>s+c.qty*c.price-(+c.discount||0),0);
      return `<tr><td><b>${esc(p.title||'Без подписи')}</b><div class="muted" style="font-size:12px">${p.created_at.slice(11,16)} · ${esc(p.user||'')} · ${p.data.cart.length} поз.</div></td><td class="n"><b>${fmt(tot)}</b></td>
      <td class="n"><button class="p" data-r="${p.id}">Вернуть</button> <button class="ghost" data-d="${p.id}">✕</button></td></tr>`; }).join('')}</tbody></table>
    <div class="actions"><button onclick="closeModal()">Закрыть</button></div>`);
  m.querySelectorAll('[data-r]').forEach(b=>b.onclick=async()=>{
    const p=l.find(x=>x.id==b.dataset.r);
    if(CART.length && !confirm('Текущий чек будет отложен. Продолжить?')) return;
    if(CART.length) await api('/api/parked',{method:'POST',body:{title:'Авто', cart:CART, check_disc:CHECK_DISC, customer:CUSTOMER}});
    CART=p.data.cart; CHECK_DISC=p.data.check_disc||0; CUSTOMER=p.data.customer||null; SEL=CART.length-1;
    await api('/api/parked/'+p.id,{method:'DELETE'}); closeModal(m); renderCart(); updParkBtn(); focusScan();
  });
  m.querySelectorAll('[data-d]').forEach(b=>b.onclick=async()=>{ if(!confirm('Удалить отложенный чек?')) return; await api('/api/parked/'+b.dataset.d,{method:'DELETE'}); closeModal(m); updParkBtn(); showParked(); });
}

// ---------------------------------------------------------------- extra hotkeys
document.addEventListener('keydown',e=>{
  if(!ME || !$('#v-pos').classList.contains('on') || $('#modalRoot .modal')) return;
  if(e.key==='F5'){ e.preventDefault(); pickCustomer(); }
  else if(e.key==='F6'){ e.preventDefault(); openPay('debt'); }
  else if(e.key==='F10'){ e.preventDefault(); parkCart(); }
  else if(e.key==='F11'){ e.preventDefault(); showParked(); }
}, true);

// ---------------------------------------------------------------- customers view
let custTimer=null;
['#cQ','#cDebt'].forEach(s=>$(s).addEventListener(s==='#cQ'?'input':'change',()=>{ clearTimeout(custTimer); custTimer=setTimeout(loadCustomers,200); }));
async function loadCustomers(){
  const p=new URLSearchParams({q:$('#cQ').value.trim()}); if($('#cDebt').checked) p.set('debtors',1);
  const rows=await api('/api/customers?'+p);
  const all=await api('/api/customers');
  const K=(l,v,c='')=>`<div class="card kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div></div>`;
  $('#custKpis').innerHTML=K('Клиентов в базе',all.length)+K('Должников',all.filter(c=>c.debt>0).length,'red')+K('Общий долг (насия)',fmt(all.reduce((s,c)=>s+c.debt,0)),'red')+K('Бонусов на счетах',fmt(all.reduce((s,c)=>s+c.bonus,0)),'green');
  $('#custBody').innerHTML=rows.map(c=>`<tr><td><b>${esc(c.name)}</b>${c.discount_pct?` <span class="badge ok">-${c.discount_pct}%</span>`:''}${c.card?`<div class="muted" style="font-size:11px">карта ${esc(c.card)}</div>`:''}</td>
    <td>${esc(fmtPhone(c.phone))}</td><td class="n">${c.purchases}</td><td class="n">${fmt(c.spent)}</td><td>${c.last_visit?c.last_visit.slice(0,10):'—'}</td>
    <td class="n green">${fmt(c.bonus)}</td><td class="n ${c.debt>0?'red':''}"><b>${c.debt>0?fmt(c.debt):'—'}</b></td>
    <td style="white-space:nowrap">${c.debt>0?`<button class="g" style="padding:5px 10px" onclick="payDebt(${c.id})">Погасить</button> `:''}<button class="ghost" onclick="showCustomer(${c.id})">Открыть</button></td></tr>`).join('')||'<tr><td colspan="8" class="muted">Клиентов нет</td></tr>';
}
function fmtPhone(p){ p=String(p||''); return p.length===12?`+${p.slice(0,3)} ${p.slice(3,5)} ${p.slice(5,8)}-${p.slice(8,10)}-${p.slice(10)}`:p; }
function editCustomer(c, onSaved){
  c=c||{}; const adm=ME.role==='admin';
  const m=modal(`<h3>${c.id?'Клиент':'Новый клиент'}</h3>
    <div class="grid2"><div><label>Телефон</label><input id="cuP" value="${esc(c.phone||'')}" placeholder="90 123 45 67" ${c.phone?'':'autofocus'}></div>
    <div><label>Имя</label><input id="cuN" value="${esc(c.name||'')}" ${c.phone&&!c.id?'autofocus':''}></div></div>
    <div class="grid2"><div><label>№ дисконтной карты</label><input id="cuC" value="${esc(c.card||'')}"></div><div><label>День рождения</label><input id="cuB" type="date" value="${esc(c.birthday||'')}"></div></div>
    ${adm?`<div class="grid2"><div><label>Персональная скидка, %</label><input id="cuD" type="number" step="any" value="${c.discount_pct||''}"></div><div><label>Лимит долга (0 — без лимита)</label><input id="cuL" type="number" value="${c.debt_limit||''}"></div></div>`:''}
    <label>Заметка</label><input id="cuNote" value="${esc(c.note||'')}">
    <div class="actions"><button onclick="closeModal()">Отмена</button><button class="p" id="cuS">Сохранить</button></div>`);
  const g=id=>m.querySelector(id);
  g('#cuS').onclick=async()=>{ try{
    const r=await api('/api/customers',{method:'POST',body:{id:c.id,phone:g('#cuP').value,name:g('#cuN').value,card:g('#cuC').value,birthday:g('#cuB').value,note:g('#cuNote').value,
      discount_pct:g('#cuD')?.value, debt_limit:g('#cuL')?.value}});
    closeModal(m); toast('Сохранено','ok'); if($('#v-customers').classList.contains('on')) loadCustomers(); onSaved&&onSaved(r);
  }catch(e){ toast(e.message,'err'); } };
}
const OPN={bonus_add:'Начислены бонусы',bonus_use:'Списаны бонусы',bonus_back:'Бонусы (возврат)',debt_add:'Насия',debt_pay:'Погашение долга',debt_back:'Долг списан (возврат)'};
async function showCustomer(id){
  const c=await api('/api/customers/'+id); const adm=ME.role==='admin';
  const m=modal(`<div class="row"><h3 class="grow" style="margin:0">👤 ${esc(c.name)}</h3><button class="ghost" id="scEdit">Изменить</button></div>
    <div class="muted">${esc(fmtPhone(c.phone))}${c.card?' · карта '+esc(c.card):''}${c.birthday?' · 🎂 '+esc(c.birthday):''}${c.note?' · '+esc(c.note):''}</div>
    <div class="kpis" style="margin-top:10px">
      <div class="card kpi"><div class="l">Долг (насия)</div><div class="v ${c.debt>0?'red':''}">${fmt(c.debt)}</div>${c.debt_limit?`<div class="muted">лимит ${fmt(c.debt_limit)}</div>`:''}</div>
      <div class="card kpi"><div class="l">Бонусы</div><div class="v green">${fmt(c.bonus)}</div></div>
      <div class="card kpi"><div class="l">Скидка</div><div class="v">${c.discount_pct||0}%</div></div>
      <div class="card kpi"><div class="l">Чеков</div><div class="v">${c.sales.filter(s=>s.operation==='sale').length}</div></div>
    </div>
    ${c.fav.length?`<div style="margin-top:10px"><b>Любимые товары:</b> ${c.fav.map(f=>esc(f.name)+' ('+fq(f.q)+')').join(', ')}</div>`:''}
    <div class="grid2" style="gap:12px;margin-top:12px">
      <div><b>Покупки</b><div class="tbl-wrap" style="max-height:260px"><table><tbody>${c.sales.map(s=>`<tr style="cursor:pointer" onclick="showSale(${s.id})"><td>${s.created_at.slice(0,16)}</td><td>${s.operation==='refund'?'<span class="red">возврат</span>':'№'+s.id}</td><td class="n">${fmt(s.total)}</td><td class="n red">${s.debt>0?'насия '+fmt(s.debt):''}</td></tr>`).join('')||'<tr><td class="muted">Нет</td></tr>'}</tbody></table></div></div>
      <div><b>Операции</b><div class="tbl-wrap" style="max-height:260px"><table><tbody>${c.ops.map(o=>`<tr><td>${o.created_at.slice(0,16)}</td><td>${OPN[o.type]||o.type}${o.method?` (${o.method==='card'?'карта':'нал'})`:''}</td><td class="n ${/debt_add|bonus_use/.test(o.type)?'red':'green'}">${fmt(o.amount)}</td></tr>`).join('')||'<tr><td class="muted">Нет</td></tr>'}</tbody></table></div></div>
    </div>
    <div class="actions" style="flex-wrap:wrap">
      ${adm?'<button id="scBonus">± Бонусы</button>':''}
      ${c.debt>0?'<button class="g" id="scPay">Погасить долг</button>':''}
      <button class="b" id="scPos">В кассу</button>
      <button class="p" onclick="closeModal()">Закрыть</button></div>`,'wide');
  m.querySelector('#scEdit').onclick=()=>{ closeModal(m); editCustomer(c); };
  m.querySelector('#scPay')?.addEventListener('click',()=>{ closeModal(m); payDebt(id); });
  m.querySelector('#scPos').onclick=()=>{ closeModal(m); setCustomer(c); show('pos'); };
  m.querySelector('#scBonus')?.addEventListener('click',async()=>{ const v=prompt('Сколько бонусов добавить (минус — списать)?'); if(!v) return;
    await api(`/api/customers/${id}/bonus`,{method:'POST',body:{amount:v}}); closeModal(m); showCustomer(id); loadCustomers(); });
}
async function payDebt(id){
  const c=await api('/api/customers/'+id);
  if(!SHIFT) return toast('Откройте смену — погашение попадает в кассу','err');
  const m=modal(`<h3>Погашение долга</h3><div class="muted">${esc(c.name)} · долг <b class="red">${fmt(c.debt)}</b></div>
    <label>Сумма</label><input class="bigin" id="dpA" value="${c.debt}" autofocus>
    <div class="quick"><button data-p="1">Весь долг</button><button data-p=".5">Половина</button></div>
    <label>Способ</label><select id="dpM"><option value="cash">Наличные</option><option value="card">Карта</option></select>
    <div class="actions"><button onclick="closeModal()">Отмена</button><button class="g" id="dpGo">Принять оплату</button></div>`);
  m.querySelectorAll('[data-p]').forEach(b=>b.onclick=()=>m.querySelector('#dpA').value=Math.round(c.debt*+b.dataset.p));
  const go=async()=>{ try{ const r=await api(`/api/customers/${id}/debt_pay`,{method:'POST',body:{amount:m.querySelector('#dpA').value,method:m.querySelector('#dpM').value}});
      closeModal(m); toast(`Принято. Остаток долга: ${fmt(r.debt)}`,'ok'); if($('#v-customers').classList.contains('on')) loadCustomers(); }catch(e){ toast(e.message,'err'); } };
  m.querySelector('#dpGo').onclick=go; m.querySelector('#dpA').onkeydown=e=>{ if(e.key==='Enter') go(); };
}

// ---------------------------------------------------------------- cash operations
function cashOp(type){
  const T={in:'Внесение наличных',out:'Изъятие наличных (инкассация)',expense:'Расход из кассы'}[type];
  const m=modal(`<h3>${T}</h3><label>Сумма</label><input class="bigin" id="coA" autofocus>
    ${type==='expense'?`<label>Категория</label><select id="coC"><option>Хоз. нужды</option><option>Обед / питание</option><option>Логистика / доставка</option><option>Аренда</option><option>Зарплата / аванс</option><option>Коммунальные услуги</option><option>Маркетинг / реклама</option><option>Поставщику</option><option>Прочее</option></select>`:''}
    <label>Комментарий</label><input id="coN">
    <div class="actions"><button onclick="closeModal()">Отмена</button><button class="p" id="coGo">Провести</button></div>`);
  const go=async()=>{ try{ await api('/api/cash_ops',{method:'POST',body:{type,amount:m.querySelector('#coA').value,category:m.querySelector('#coC')?.value,note:m.querySelector('#coN').value}});
    closeModal(m); toast('Проведено','ok'); loadShifts(); }catch(e){ toast(e.message,'err'); } };
  m.querySelector('#coGo').onclick=go; m.querySelector('#coA').onkeydown=e=>{ if(e.key==='Enter') go(); };
}

// ---------------------------------------------------------------- labels
document.addEventListener('change',e=>{ if(e.target.id==='pAll') $$('.pSel').forEach(c=>c.checked=e.target.checked); });
function labelsDialog(ids, qtyMap){
  if(!ids.length) return toast('Отметьте товары галочками','err');
  const m=modal(`<h3>🏷 Печать ценников / этикеток (${ids.length} тов.)</h3>
    <div class="grid3"><div><label>Тип</label><select id="lK"><option value="price">Ценник (крупная цена)</option><option value="label">Этикетка со штрихкодом</option></select></div>
    <div><label>Размер, мм</label><select id="lS"><option>58x40</option><option>58x30</option><option>40x30</option><option>43x25</option><option>70x50</option></select></div>
    <div><label>Копий</label><select id="lQ"><option value="1">по 1 шт</option>${qtyMap?'<option value="qty" selected>по кол-ву прихода</option>':''}</select></div></div>
    <p class="muted">Печатается на принтер этикеток (термо). В драйвере выставьте тот же размер этикетки.</p>
    <div class="actions"><button onclick="closeModal()">Отмена</button><button id="lV">Просмотр</button><button class="p" id="lP">Печать</button></div>`);
  const url=()=>{ const q=m.querySelector('#lQ').value; const copies=q==='qty'&&qtyMap? ids.map(i=>`${i}:${Math.ceil(qtyMap[i]||1)}`).join(',') : '';
    return `/labels?ids=${ids.join(',')}&kind=${m.querySelector('#lK').value}&size=${m.querySelector('#lS').value}${copies?'&copies='+copies:''}`; };
  m.querySelector('#lV').onclick=()=>window.open(url(),'_blank');
  m.querySelector('#lP').onclick=()=>{ printUrl(url()); closeModal(m); };
}
function printLabelsSelected(){ labelsDialog($$('.pSel:checked').map(c=>+c.value)); }
const _savePurchase = savePurchase;
savePurchase = async function(){
  const snap=puItems.map(x=>({id:x.product_id, qty:x.qty}));
  await _savePurchase();
  if(!puItems.length && snap.length && confirm('Приход проведён. Напечатать этикетки на принятый товар?')){
    const qm={}; snap.forEach(x=>qm[x.id]=x.qty); labelsDialog(snap.map(x=>x.id), qm);
  }
};

// ---------------------------------------------------------------- auto order
async function autoOrder(){
  const r=await api('/api/reorder');
  if(!r.items.length) return toast('Всего хватает — по продажам за 30 дней заказывать нечего','ok');
  const m=modal(`<h3>⚡ Автозаказ — запас на ${r.cover} дн.</h3>
    <div class="muted">Рассчитано по продажам за ${r.days} дней. Красным — закончится менее чем через 3 дня.</div>
    <div class="tbl-wrap" style="max-height:55vh;margin-top:8px"><table><thead><tr><th><input type="checkbox" id="aoAll" checked style="width:auto"></th><th>Товар</th><th class="n">Остаток</th><th class="n">Продаж/день</th><th class="n">Хватит на</th><th class="n">Заказать</th><th class="n">Сумма</th></tr></thead><tbody>
    ${r.items.map((x,i)=>`<tr class="${x.urgent?'red':''}"><td><input type="checkbox" class="aoS" data-i="${i}" checked style="width:auto"></td><td>${esc(x.name)}</td><td class="n">${fq(x.stock)}</td><td class="n">${x.per_day}</td>
      <td class="n">${x.stock<=0?'<b>нет в наличии</b>':x.days_left===null?'нет продаж':x.days_left+' дн.'}</td><td class="n"><input type="number" value="${x.qty}" data-q="${i}" style="width:80px"></td><td class="n">${fmt(x.sum)}</td></tr>`).join('')}
    </tbody></table></div>
    <div class="row" style="margin-top:10px"><b class="grow">Итого ≈ ${fmt(r.total)}</b></div>
    <div class="actions"><button onclick="closeModal()">Отмена</button><button id="aoTxt">Скопировать для поставщика</button><button class="p" id="aoAdd">Добавить в приход</button></div>`,'wide');
  m.querySelector('#aoAll').onchange=e=>m.querySelectorAll('.aoS').forEach(c=>c.checked=e.target.checked);
  const sel=()=>[...m.querySelectorAll('.aoS:checked')].map(c=>{ const x=r.items[+c.dataset.i]; return {...x, qty:+m.querySelector(`[data-q="${c.dataset.i}"]`).value||x.qty}; });
  m.querySelector('#aoTxt').onclick=()=>{ const t=`Заявка от ${SET.store_name} (${today()}):\n`+sel().map((x,i)=>`${i+1}. ${x.name}${x.barcode?' ['+x.barcode+']':''} — ${x.qty} ${x.unit_name||'шт'}`).join('\n');
    navigator.clipboard.writeText(t).then(()=>toast('Заявка скопирована — вставьте в Telegram поставщику','ok')); };
  m.querySelector('#aoAdd').onclick=()=>{ sel().forEach(x=>{ const ex=puItems.find(p=>p.product_id===x.id); if(ex) ex.qty=x.qty; else puItems.push({product_id:x.id,name:x.name,stock:x.stock,qty:x.qty,cost:x.cost_price||'',price:''}); });
    closeModal(m); renderPu(); toast('Добавлено в приход — проверьте цены и проведите','ok'); };
}

// ---------------------------------------------------------------- reports: ABC
const _loadReport = loadReport;
loadReport = async function(){
  await _loadReport();
  const r=await api(`/api/abc?from=${$('#rFrom').value}&to=${$('#rTo').value}`);
  const S=r.summary; $('#abcSum').textContent=`A: ${S.A[0]} тов. (${fmt(S.A[1])}) · B: ${S.B[0]} · C: ${S.C[0]}`;
  const col={A:'#16a34a',B:'#d97706',C:'#dc2626'};
  $('#abcBody').innerHTML=r.items.map(x=>`<tr><td><span class="badge" style="background:${col[x.cls]};color:#fff">${x.cls}</span></td><td>${esc(x.name)}</td><td class="n">${fq(x.qty)}</td><td class="n">${fmt(x.revenue)}</td><td class="n">${x.share}%</td><td class="n">${x.cum}%</td><td class="n green">${fmt(x.profit)}</td></tr>`).join('')||'<tr><td class="muted" colspan="7">Нет продаж</td></tr>';
};

// ---------------------------------------------------------------- audit
async function loadAudit(){
  if(!$('#aFrom').value){ $('#aFrom').value=today(7); $('#aTo').value=today(); }
  const rows=await api(`/api/audit?from=${$('#aFrom').value}&to=${$('#aTo').value}&min=${$('#aMin').value}`);
  $('#auditBody').innerHTML=rows.map(a=>`<tr style="${a.severity>=2?'background:#fef2f2':a.severity==1?'background:#fffbeb':''}"><td style="white-space:nowrap">${a.created_at}</td><td>${esc(a.user||'—')}</td><td>${a.severity>=2?'🔴 ':a.severity==1?'🟡 ':''}${esc(a.action)}</td><td class="muted">${esc(a.details||'')}</td></tr>`).join('')||'<tr><td colspan="4" class="muted">Событий нет</td></tr>';
}

// ---------------------------------------------------------------- telegram
async function tgFindChat(){
  try{ const r=await api('/api/telegram/chat_id',{method:'POST',body:{token:$('[data-s="tg_token"]').value.trim()}});
    const ids=r.chats.map(c=>c.id).join(','); $('[data-s="tg_chat_id"]').value=ids;
    toast('Найдено: '+r.chats.map(c=>c.name+' ('+c.id+')').join(', ')+' — нажмите «Сохранить»','ok');
  }catch(e){ toast(e.message,'err'); }
}
async function tgTest(){ await saveSettings(); try{ await api('/api/telegram/test',{method:'POST'}); toast('Отчёт отправлен в Telegram','ok'); }catch(e){ toast(e.message,'err'); } }

// ---------------------------------------------------------------- navigation extension
const _show = show;
show = function(v){
  _show(v);
  if(v==='customers') loadCustomers();
  else if(v==='audit') loadAudit();
  else if(v==='pos'){ renderCust(); updParkBtn(); }
};



$('#cImp').onchange=async e=>{ const f=e.target.files[0]; if(!f) return; const fd=new FormData(); fd.append('file',f);
  try{ const r=await api('/api/customers/import',{method:'POST',body:fd}); toast(`Клиенты: создано ${r.created}, обновлено ${r.updated}${r.skipped?`, пропущено ${r.skipped}`:''}`,'ok'); loadCustomers(); }
  catch(er){ toast(er.message,'err'); } e.target.value=''; };
