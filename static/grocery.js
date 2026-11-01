// =====================================================================
//  Продуктовый магазин: быстрые кнопки и вес, маркировка DataMatrix,
//  18+, сроки годности, поставщики, упаковки.
// =====================================================================
let AGE_OK=false, QUICK=[];

// ---------------------------------------------------------------- быстрые кнопки + вес
async function loadQuick(){
  try{ QUICK=await api('/api/quick'); }catch(e){ QUICK=[]; }
  const box=$('#quickBox');
  if(!QUICK.length){ box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  const weighted=u=>/^(кг|г|л|kg)$/i.test((u||'').trim());
  box.innerHTML=`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(118px,1fr));gap:6px">${QUICK.map(p=>`
    <button class="qb" data-id="${p.id}" style="background:#fff;border:1px solid var(--line);padding:8px 6px;text-align:left;height:58px;line-height:1.15">
      <div style="font-weight:600;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.name)}</div>
      <div class="muted" style="font-size:12px">${fmt(p.price)}${weighted(p.unit_name)?' / '+esc(p.unit_name):''}${weighted(p.unit_name)?' ⚖':''}</div></button>`).join('')}</div>`;
  box.querySelectorAll('.qb').forEach(b=>b.onclick=()=>{ const p=QUICK.find(x=>x.id==b.dataset.id); weighted(p.unit_name)?askWeight(p):addQuick(p,1); });
}
function addQuick(p, qty){ QTY_PREFIX=qty; addToCart(p); }
function askWeight(p){
  const m=modal(`<h3>⚖ ${esc(p.name)} — ${fmt(p.price)} за ${esc(p.unit_name)}</h3>
    <label>Вес, ${esc(p.unit_name)} (поставьте на весы и введите показание)</label><input class="bigin" id="wQ" inputmode="decimal" autofocus>
    <div class="quick">${[0.25,0.5,1,1.5,2,3].map(v=>`<button data-w="${v}">${v}</button>`).join('')}</div>
    <div style="font-size:22px;margin-top:10px" id="wSum"></div>
    <div class="actions"><button onclick="closeModal()">Отмена</button><button class="g" id="wOk">Добавить (Enter)</button></div>`);
  const inp=m.querySelector('#wQ'), val=()=>parseFloat((inp.value||'').replace(',','.'))||0;
  const upd=()=>m.querySelector('#wSum').innerHTML=val()?`Сумма: <b>${fmt(val()*p.price)}</b>`:'';
  inp.oninput=upd; m.querySelectorAll('[data-w]').forEach(b=>b.onclick=()=>{ inp.value=b.dataset.w; upd(); inp.focus(); });
  const go=()=>{ if(!(val()>0)) return toast('Введите вес','err'); closeModal(m); addQuick(p, Math.round(val()*1000)/1000); };
  m.querySelector('#wOk').onclick=go; inp.onkeydown=e=>{ if(e.key==='Enter') go(); if(e.key==='Escape') closeModal(m); };
}

// ---------------------------------------------------------------- маркировка и 18+
function askAge(p){
  return new Promise(res=>{
    const m=modal(`<div style="text-align:center"><div style="font-size:56px;font-weight:800;color:#dc2626">18+</div>
      <h3>${esc(p.name)}</h3><p>Проверьте документ: покупателю исполнилось 18 лет?</p></div>
      <div class="actions" style="justify-content:center"><button class="r" id="agNo">Нет — отказать</button><button class="g" id="agYes" autofocus>Да, есть 18 (Enter)</button></div>`);
    m.querySelector('#agYes').onclick=()=>{ closeModal(m); res(true); }; m.querySelector('#agNo').onclick=()=>{ closeModal(m); res(false); };
    m.addEventListener('keydown',e=>{ if(e.key==='Enter'){ e.preventDefault(); closeModal(m); res(true); } if(e.key==='Escape'){ closeModal(m); res(false); } });
    setTimeout(()=>m.querySelector('#agYes').focus(),30);
  });
}
function askMarking(p){
  return new Promise(res=>{
    const m=modal(`<h3>🏷 Маркировка: ${esc(p.name)}</h3><p class="muted">Это маркируемый товар (Asl Belgisi). Отсканируйте <b>DataMatrix</b> — квадратный код на упаковке, не обычный штрихкод.</p>
      <input class="bigin" id="mkC" autocomplete="off" placeholder="Сканируйте код маркировки" autofocus>
      <div class="actions"><button id="mkNo">Отмена</button></div>`);
    const inp=m.querySelector('#mkC');
    m.querySelector('#mkNo').onclick=()=>{ closeModal(m); res(null); };
    inp.onkeydown=async e=>{ if(e.key==='Escape'){ closeModal(m); res(null); } if(e.key!=='Enter') return; e.preventDefault();
      const v=inp.value.trim(); inp.value=''; if(!v) return;
      try{ const r=await api('/api/products/barcode/'+encodeURIComponent(v));
        if(!r.marking){ beep(true); return toast('Это обычный штрихкод. Нужен код DataMatrix (квадратный)','err'); }
        if(r.id!==p.id){ beep(true); return toast(`Код марки от другого товара: «${r.name}»`,'err'); }
        closeModal(m); res(r.marking);
      }catch(er){ beep(true); toast(er.message,'err'); } };
  });
}
const _addToCartBase = addToCart;
addToCart = async function(p){
  if(!p) return;
  if(p.restrict_kind && !AGE_OK){
    const ok=await askAge(p); if(!ok){ toast('Продажа отменена: нет 18 лет','err'); scan.value=''; QTY_PREFIX=null; focusScan(); return; }
    AGE_OK=true; api('/api/audit',{method:'POST',body:{action:'Подтверждён возраст 18+',details:p.name}}).catch(()=>{});
  }
  if(p.requires_marking){
    let code=p.marking;
    if(!code){
      if(SET.marking_strict!=='1'){ toast('Маркируемый товар — по возможности сканируйте DataMatrix'); }
      else { code=await askMarking(p); if(!code){ scan.value=''; QTY_PREFIX=null; focusScan(); return; } }
    }
    if(code){
      if(CART.some(c=>(c.marking||[]).includes(code))){ beep(true); toast('Этот код марки уже в чеке','err'); scan.value=''; return; }
      let i=CART.findIndex(c=>c.product_id===p.id && !c.discount);
      if(i<0){ CART.push({product_id:p.id,name:p.name,barcode:p.barcode,price:+p.price,qty:0,discount:0,stock:+p.stock,unit:p.unit_name,marking:[],requires_marking:1,restrict_kind:p.restrict_kind}); i=CART.length-1; }
      CART[i].marking.push(code); CART[i].qty=CART[i].marking.length; SEL=i; QTY_PREFIX=null;
      beep(); scan.value=''; hideSugg('#sugg'); renderCart(); focusScan(); return;
    }
  }
  _addToCartBase(p);
  const i=CART.findIndex(c=>c.product_id===p.id); if(i>=0){ CART[i].restrict_kind=p.restrict_kind; CART[i].requires_marking=p.requires_marking; }
};
// маркируемые: количество = числу марок, вручную не меняется
const _setQtyBase = setQty;
setQty = function(i,v){ const c=CART[i]; if(c&&c.marking&&c.marking.length){ toast('Количество маркируемого товара = числу отсканированных марок. Удалите строку или сканируйте ещё','err'); renderCart(); return; } _setQtyBase(i,v); };
const _clearCartG = clearCart;
clearCart = function(ask){ _clearCartG(ask); if(!CART.length) AGE_OK=false; };

// ---------------------------------------------------------------- сроки
async function loadExpiry(){
  const r=await api('/api/expiry?days='+$('#expDays').value);
  const K=(l,v,c='')=>`<div class="card kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div></div>`;
  $('#expKpis').innerHTML=K('Просрочено позиций',r.expired,'red')+K('Потери по себестоимости',fmt(r.loss),'red')+K('Истекают в период',r.items.filter(i=>i.days_left>=0).length,'amber');
  $('#expBody').innerHTML=r.items.map(i=>`<tr style="background:${i.days_left<0?'#fef2f2':i.days_left<=1?'#fff7ed':''}">
    <td><b>${esc(i.name)}</b><div class="muted" style="font-size:12px">${esc(i.barcode||'')}</div></td><td>${i.received}</td><td>${i.expiry}</td>
    <td class="n ${i.days_left<0?'red':i.days_left<=2?'amber':''}"><b>${i.days_left<0?'просрочен '+(-i.days_left)+' дн.':i.days_left===0?'сегодня':i.days_left}</b></td>
    <td class="n">${fq(i.qty)} ${esc(i.unit_name||'')}</td><td class="n">${fmt(i.cost_price)}</td><td class="n">${fmt(i.price)}</td>
    <td style="white-space:nowrap">${ME.role==='admin'?(i.days_left<0?`<button class="r" style="padding:5px 10px" onclick='writeoff([${JSON.stringify({product_id:i.id,qty:i.qty,batch_id:i.batch_id,expiry:i.expiry})}])'>Списать</button>`
      :`<button class="ghost" onclick="markdown(${i.id},'${esc(i.name).replace(/'/g,'')}',${i.price})">Уценить</button>`):''}</td></tr>`).join('')||'<tr><td colspan="8" class="muted">Всё в порядке — ничего не истекает ✓</td></tr>';
  window._EXP=r.items;
}
async function writeoff(items){
  const n=items.reduce((a,x)=>a+x.qty,0); if(!confirm(`Списать ${fq(n)} ед. просрочки?`)) return;
  await api('/api/expiry/writeoff',{method:'POST',body:{items}}); toast('Списано','ok'); loadExpiry(); updExpBadge();
}
function writeoffExpired(){ const it=(window._EXP||[]).filter(i=>i.days_left<0).map(i=>({product_id:i.id,qty:i.qty,batch_id:i.batch_id,expiry:i.expiry})); if(!it.length) return toast('Просрочки нет'); writeoff(it); }
function markdown(pid,name,price){
  const m=modal(`<h3>Уценка: ${esc(name)}</h3><div class="muted">Текущая цена ${fmt(price)}</div>
    <div class="quick">${[20,30,50].map(p=>`<button data-p="${p}">−${p}% → ${fmt(Math.round(price*(1-p/100)/100)*100)}</button>`).join('')}</div>
    <label>Или новая цена</label><input id="mdP" type="number">
    <div class="actions"><button onclick="closeModal()">Отмена</button><button class="p" id="mdOk">Уценить и напечатать ценник</button></div>`);
  let pct=null; m.querySelectorAll('[data-p]').forEach(b=>b.onclick=()=>{ pct=+b.dataset.p; m.querySelector('#mdP').value=Math.round(price*(1-pct/100)/100)*100; });
  m.querySelector('#mdOk').onclick=async()=>{ try{ const r=await api('/api/expiry/markdown',{method:'POST',body:{product_id:pid,price:m.querySelector('#mdP').value}});
    closeModal(m); toast('Новая цена '+fmt(r.price),'ok'); printUrl(`/labels?ids=${pid}&kind=price`); loadExpiry(); }catch(e){ toast(e.message,'err'); } };
}
async function updExpBadge(){
  try{ const r=await api('/api/expiry?days='+(+SET.expiry_warn_days||3)); const n=r.items.length;
    $('#navExp').innerHTML='Сроки'+(n?` <span class="badge" style="background:${r.expired?'#fecaca;color:#991b1b':'#fde68a;color:#92400e'}">${n}</span>`:'');
  }catch(e){}
}
async function setExpiry(lid,v){ try{ await api(`/api/docs/${DOC.id}/expiry`,{method:'POST',body:{line_id:lid,expiry:v}}); const l=DOC.lines.find(x=>x.id===lid); if(l) l.expiry=v; }catch(e){ toast(e.message,'err'); } }

// ---------------------------------------------------------------- поставщики
async function loadSuppliers(){
  const rows=await api('/api/suppliers');
  const K=(l,v,c='')=>`<div class="card kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div></div>`;
  $('#supKpis').innerHTML=K('Поставщиков',rows.length)+K('Наш долг поставщикам',fmt(rows.reduce((a,s)=>a+Math.max(0,s.balance),0)),'red')+K('Кому должны',rows.filter(s=>s.balance>0).length,'amber');
  $('#supBody').innerHTML=rows.map(s=>`<tr><td><b>${esc(s.name)}</b>${s.inn?`<div class="muted" style="font-size:11px">ИНН ${esc(s.inn)}</div>`:''}</td><td>${esc(s.phone||'')}</td><td class="n">${s.purchases}</td><td class="n">${fmt(s.turnover)}</td>
    <td>${s.last_purchase?s.last_purchase.slice(0,10):'—'}</td><td class="n ${s.balance>0?'red':''}"><b>${s.balance>0?fmt(s.balance):'—'}</b></td>
    <td style="white-space:nowrap">${s.balance>0?`<button class="g" style="padding:5px 10px" onclick="paySupplier(${s.id},'${esc(s.name).replace(/'/g,'')}',${s.balance})">Оплатить</button> `:''}<button class="ghost" onclick="showSupplier(${s.id})">Открыть</button></td></tr>`).join('')||'<tr><td colspan="7" class="muted">Поставщиков нет — они появятся при приходе</td></tr>';
}
function editSupplier(s){ s=s||{};
  const m=modal(`<h3>${s.id?'Поставщик':'Новый поставщик'}</h3><label>Название</label><input id="spN" value="${esc(s.name||'')}" autofocus>
    <div class="grid2"><div><label>Телефон</label><input id="spP" value="${esc(s.phone||'')}"></div><div><label>ИНН</label><input id="spI" value="${esc(s.inn||'')}"></div></div>
    <label>Заметка (агент, график завоза)</label><input id="spT" value="${esc(s.note||'')}">
    <div class="actions"><button onclick="closeModal()">Отмена</button><button class="p" id="spOk">Сохранить</button></div>`);
  m.querySelector('#spOk').onclick=async()=>{ try{ await api('/api/suppliers',{method:'POST',body:{id:s.id,name:m.querySelector('#spN').value,phone:m.querySelector('#spP').value,inn:m.querySelector('#spI').value,note:m.querySelector('#spT').value}}); closeModal(m); loadSuppliers(); }catch(e){ toast(e.message,'err'); } };
}
function paySupplier(id,name,bal){
  const m=modal(`<h3>Оплата поставщику: ${esc(name)}</h3><div class="muted">Наш долг <b class="red">${fmt(bal)}</b></div>
    <label>Сумма</label><input class="bigin" id="psA" value="${bal}" autofocus>
    <label>Чем</label><select id="psM"><option value="cash">Наличные из кассы (уменьшит кассу смены)</option><option value="bank">Перечисление</option><option value="card">Карта</option></select>
    <label>Комментарий</label><input id="psN">
    <div class="actions"><button onclick="closeModal()">Отмена</button><button class="g" id="psOk">Оплатить</button></div>`);
  m.querySelector('#psOk').onclick=async()=>{ try{ const r=await api(`/api/suppliers/${id}/pay`,{method:'POST',body:{amount:m.querySelector('#psA').value,method:m.querySelector('#psM').value,note:m.querySelector('#psN').value}});
    closeModal(m); toast('Оплачено. Остаток долга '+fmt(r.balance),'ok'); loadSuppliers(); }catch(e){ toast(e.message,'err'); } };
}
const SOP={purchase:'Приход товара',payment:'Оплата',correction:'Корректировка'};
async function showSupplier(id){
  const s=await api('/api/suppliers/'+id);
  const m=modal(`<div class="row"><h3 class="grow" style="margin:0">${esc(s.name)}</h3><button class="ghost" id="ssE">Изменить</button></div>
    <div class="muted">${esc(s.phone||'')} ${s.inn?'· ИНН '+esc(s.inn):''} ${s.note?'· '+esc(s.note):''}</div>
    <div style="font-size:22px;margin:10px 0">Наш долг: <b class="${s.balance>0?'red':''}">${fmt(s.balance)}</b></div>
    <div class="tbl-wrap" style="max-height:50vh"><table><thead><tr><th>Дата</th><th>Операция</th><th>Способ</th><th class="n">Сумма</th><th>Кто</th></tr></thead><tbody>
    ${s.ops.map(o=>`<tr><td>${o.created_at.slice(0,16)}</td><td>${SOP[o.type]||o.type} ${esc(o.note||'')}</td><td>${o.method==='cash'?'нал. из кассы':o.method==='bank'?'перечисление':o.method==='card'?'карта':''}</td>
      <td class="n ${o.type==='purchase'?'red':'green'}">${o.type==='purchase'?'+':'−'}${fmt(o.amount)}</td><td>${esc(o.user||'')}</td></tr>`).join('')||'<tr><td class="muted">Нет операций</td></tr>'}
    </tbody></table></div>
    <div class="actions">${s.balance>0?'<button class="g" id="ssP">Оплатить</button>':''}<button class="p" onclick="closeModal()">Закрыть</button></div>`,'wide');
  m.querySelector('#ssE').onclick=()=>{ closeModal(m); editSupplier(s); };
  m.querySelector('#ssP')?.addEventListener('click',()=>{ closeModal(m); paySupplier(s.id,s.name,s.balance); });
}
async function fillSupplierList(){ try{ const r=await api('/api/suppliers'); $('#supList').innerHTML=r.map(s=>`<option value="${esc(s.name)}">`).join(''); return r; }catch(e){ return []; } }

// ---------------------------------------------------------------- упаковки в карточке товара
function packsDialog(pid,name){
  const m=modal(`<h3>Упаковки: ${esc(name)}</h3><p class="muted" style="margin-top:0">Второй штрихкод на блок, ящик или упаковку. При скане добавится сразу нужное количество штук.</p>
    <table><tbody id="pkB"></tbody></table>
    <div class="grid3" style="margin-top:8px"><div><label>Штрихкод упаковки</label><input id="pkC" autofocus></div><div><label>Штук внутри</label><input id="pkQ" type="number" value="6"></div><div><label>Название</label><input id="pkN" placeholder="Блок / Ящик"></div></div>
    <div class="actions"><button onclick="closeModal()">Закрыть</button><button class="p" id="pkAdd">Добавить</button></div>`);
  const load=async()=>{ const r=await api(`/api/products/${pid}/barcodes`); m.querySelector('#pkB').innerHTML=r.map(b=>`<tr><td>${esc(b.barcode)}</td><td>${esc(b.name||'')}</td><td class="n">×${fq(b.qty)}</td><td><button class="ghost" data-d="${b.id}">✕</button></td></tr>`).join('')||'<tr><td class="muted">Упаковок нет</td></tr>';
    m.querySelectorAll('[data-d]').forEach(x=>x.onclick=async()=>{ await api('/api/products/barcodes/'+x.dataset.d,{method:'DELETE'}); load(); }); };
  m.querySelector('#pkAdd').onclick=async()=>{ try{ await api(`/api/products/${pid}/barcodes`,{method:'POST',body:{barcode:m.querySelector('#pkC').value,qty:m.querySelector('#pkQ').value,name:m.querySelector('#pkN').value}}); m.querySelector('#pkC').value=''; load(); }catch(e){ toast(e.message,'err'); } };
  m.querySelector('#pkC').onkeydown=e=>{ if(e.key==='Enter') m.querySelector('#pkAdd').click(); };
  load();
}
const _editProductG = editProduct;
editProduct = async function(arg,onSaved){
  await _editProductG(arg,onSaved);
  const box=$('#modalRoot .modal:last-child'); if(!box) return;
  const sups=await fillSupplierList(); const p=typeof arg==='number'?PRODUCTS_CACHE.find(x=>x.id===arg):null;
  const sel=box.querySelector('#fSup'); if(sel) sel.innerHTML='<option value="">—</option>'+sups.map(s=>`<option value="${s.id}" ${p&&p.supplier_id==s.id?'selected':''}>${esc(s.name)}</option>`).join('');
  box.querySelector('#fPacks')?.addEventListener('click',e=>{ e.preventDefault(); packsDialog(p.id,p.name); });
};

// ---------------------------------------------------------------- hooks
const _showG = show;
show = function(v){
  _showG(v);
  if(v==='expiry') loadExpiry();
  else if(v==='suppliers') loadSuppliers();
  else if(v==='purchase') fillSupplierList();
  else if(v==='pos') loadQuick();
};
function buildMoreMenu(){
  const nav=$('#nav'); if($('#navMore')) return;
  const more=[...nav.querySelectorAll('button[data-more]')].filter(b=>!b.classList.contains('hidden'));
  if(!more.length) return;
  const btn=document.createElement('button'); btn.id='navMore'; btn.textContent='Ещё ▾'; nav.appendChild(btn);
  const menu=document.createElement('div'); menu.id='moreMenu'; menu.className='hidden'; document.body.appendChild(menu);
  more.forEach(b=>{ b.remove(); menu.appendChild(b); b.addEventListener('click',()=>menu.classList.add('hidden')); });
  btn.onclick=e=>{ e.stopPropagation(); const r=btn.getBoundingClientRect(); menu.style.left=Math.min(r.left, innerWidth-220)+'px'; menu.classList.toggle('hidden'); };
  document.addEventListener('click',e=>{ if(!e.target.closest('#moreMenu')) menu.classList.add('hidden'); });
}
const _bootG = boot;
boot = async function(){ await _bootG(); buildMoreMenu(); loadQuick(); updExpBadge(); };
const _showMore = show;
show = function(v){ _showMore(v); const b=$('#navMore'); if(b) b.classList.toggle('on', !!$(`#moreMenu button[data-v="${v}"]`)); $$('#moreMenu button').forEach(x=>x.classList.toggle('on',x.dataset.v===v)); };
setInterval(()=>{ if(ME) updExpBadge(); }, 10*60*1000);
Object.assign(MV,{writeoff:'Списание'});
document.addEventListener('keydown',e=>{ if(e.key==='F12' && ME && $('#v-pos').classList.contains('on')){ e.preventDefault(); $('#quickBox').classList.toggle('hidden'); } });

boot();

// ---------------------------------------------------------------- автообновление
async function updCheck(force){
  try{
    const r=await api(force?'/api/update/check':'/api/update/status',force?{method:'POST'}:{});
    let b=$('#updBtn');
    const ready=r.staged && r.staged!==r.version;
    if(ready && ME.role==='admin'){
      if(!b){ b=document.createElement('button'); b.id='updBtn'; b.style.cssText='background:#fde68a;color:#78350f;font-weight:600'; $('header .right').prepend(b); b.onclick=updApply; }
      b.textContent='⬆ Обновление'; b.title='Готова новая версия '+r.staged+(r.notes?'\n'+r.notes:'')+'\nУстановится сама при закрытии смены или при запуске. Нажмите, чтобы обновить сейчас.';
    } else if(b) b.remove();
    const el=$('#updInfo'); if(el) el.innerHTML=`Версия кассы: <b>${esc(r.version)}</b>${ready?` · <span class="amber">готово обновление ${esc(r.staged)}</span>`:r.available?` · загружается ${esc(r.available)}`:' · актуальная'}${r.checked_at?' · проверено '+esc(r.checked_at.slice(11,16)):''}${r.error?` · <span class="red">${esc(r.error)}</span>`:''}`;
    return r;
  }catch(e){}
}
async function updApply(){
  if(CART.length) return toast('Сначала завершите текущий чек','err');
  if(!confirm('Установить обновление сейчас? Касса перезапустится примерно за 10 секунд. Данные не затрагиваются.')) return;
  try{ await api('/api/update/apply',{method:'POST'}); }catch(e){ return toast(e.message,'err'); }
  modal('<div style="text-align:center;padding:20px"><div style="font-size:40px">⬆</div><h3>Устанавливается обновление…</h3><p class="muted">Касса перезапустится сама</p></div>');
  const wait=async()=>{ try{ const r=await fetch('/api/me'); if(r.status<500){ location.reload(); return; } }catch(e){} setTimeout(wait,1500); };
  setTimeout(wait,6000);
}
const _showU = show;
show = function(v){ _showU(v); if(v==='settings') updCheck(); };
setInterval(()=>{ if(ME) updCheck(); }, 10*60*1000);
setTimeout(()=>{ if(ME) updCheck(); }, 3000);
