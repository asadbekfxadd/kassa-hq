// =====================================================================
//  Цены и аналитика: акции, оптовые цены, переоценка, QR-оплата,
//  «Обзор» бизнеса и центр отчётов (продажи, ABC/XYZ, оборотка,
//  прибыль, сотрудники, НДС) с выгрузкой в Excel.
// =====================================================================
const WDN=['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
const KPI=(l,v,c='',sub='')=>`<div class="card kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div>${sub?`<div style="margin-top:2px">${sub}</div>`:''}</div>`;
const CHG=(p,inv)=>p==null?'<span class="muted" style="font-size:12px">— нет базы</span>':`<span class="chg ${(p>=0)!==!!inv?'up':'dn'}">${p>=0?'▲':'▼'} ${Math.abs(p)}%</span>`;
const pct=(a,b)=>b?Math.round(a/b*1000)/10:0;

// ---------------------------------------------------------------- перехват запросов (доп. поля к формам)
let PAY_QR=false;
const _apiL = api;
api = async function(url, opt={}){
  const b=opt.body;
  if(b && typeof b==='object' && !(b instanceof FormData)){
    if(url==='/api/sales' && PAY_QR && +b.card>0){ b.qr=b.card; b.card=0; }
    if(url==='/api/customers' && opt.method==='POST' && $('#cuPT')) b.price_type=$('#cuPT').value;
    if(/^\/api\/products(\/\d+)?$/.test(url) && (opt.method==='POST'||opt.method==='PUT') && $('#fWh')) b.wholesale_price=$('#fWh').value;
  }
  return _apiL(url, opt);
};

// ---------------------------------------------------------------- акции в чеке
let PROMOS=[];
async function loadPromos(){ try{ PROMOS=await api('/api/promos/active'); }catch(e){ PROMOS=[]; } renderCart(); }
function promoDisc(pr,qty,price){
  if(pr.kind==='percent') return Math.round(qty*price*pr.value/100);
  if(pr.kind==='nplus' && pr.n>0 && pr.m>0) return Math.round(Math.floor(qty/(pr.n+pr.m))*pr.m*price);
  return 0;
}
function promoMatch(pr,c){ if(!pr.products.length && !pr.categories.length) return true; return pr.products.includes(c.product_id) || pr.categories.includes(+c.category_id); }
function applyPromosCart(){
  CART.forEach(c=>{
    let best=null, bd=0;
    PROMOS.forEach(pr=>{ if(promoMatch(pr,c)){ const d=promoDisc(pr,c.qty,c.price); if(d>bd){ bd=d; best=pr; } } });
    bd=Math.min(bd, Math.max(0, Math.round((c.qty*c.price-(+c.discount||0))*100)/100));
    c.pdisc=best&&bd>0?bd:0; c.pname=best&&bd>0?best.name:'';
  });
}
cartTotals = function(){
  applyPromosCart();
  const sub = CART.reduce((s,c)=>s+c.qty*c.price,0);
  const ld = CART.reduce((s,c)=>s+(+c.discount||0)+(c.pdisc||0),0);
  const pers = personalDisc(sub-ld);
  const check = (+CHECK_DISC||0) + pers;
  const disc = Math.min(ld + check, sub);
  return {sub, disc, check, total: Math.round((sub-disc)*100)/100, promo: CART.reduce((s,c)=>s+(c.pdisc||0),0)};
};
const _renderCartL = renderCart;
renderCart = function(){
  applyPromosCart();
  _renderCartL();
  const rows=$$('#cartBody tr');
  CART.forEach((c,i)=>{
    const tr=rows[i]; if(!tr) return; const td=tr.children;
    if(c.opt) td[2].innerHTML=`${fmt(c.price)}<div><span class="badge" style="background:#ede9fe;color:#5b21b6">опт</span></div>`;
    if(c.pdisc){
      td[4].innerHTML=`${c.discount?`<span class="red">-${fmt(c.discount)}</span><br>`:''}<span class="red">-${fmt(c.pdisc)}</span><div style="font-size:11px;color:#7c3aed">🎁 ${esc(c.pname)}</div>`;
      td[5].innerHTML=`<b>${fmt(c.qty*c.price-(+c.discount||0)-c.pdisc)}</b>`;
    }
  });
};

// ---------------------------------------------------------------- оптовые цены
const _addToCartL = addToCart;
addToCart = async function(p){
  if(!p) return _addToCartL(p);
  const retail=+p.price, wh=+p.wholesale_price||0;
  const opt = CUSTOMER && CUSTOMER.price_type==='wholesale' && wh>0;
  await _addToCartL(opt?{...p, price:wh}:p);
  CART.filter(c=>c.product_id===p.id).forEach(c=>{ c.category_id=p.category_id; c.retail=retail; c.wholesale=wh; if(opt) c.opt=1; });
  renderCart();
};
const _setCustomerL = setCustomer;
setCustomer = function(c){
  const opt = c && c.price_type==='wholesale';
  let n=0;
  CART.forEach(x=>{
    if(opt && x.wholesale>0 && !x.opt){ x.price=x.wholesale; x.opt=1; n++; }
    else if(!opt && x.opt){ x.price=x.retail; x.opt=0; n++; }
  });
  _setCustomerL(c);
  if(n) toast(opt?`Оптовые цены применены к ${n} поз.`:`Возвращены розничные цены (${n} поз.)`);
};
const _renderCustL = renderCust;
renderCust = function(){
  _renderCustL();
  if(CUSTOMER && CUSTOMER.price_type==='wholesale'){ const b=$('#custBox b'); if(b && !b.querySelector('.badge')) b.insertAdjacentHTML('beforeend',' <span class="badge" style="background:#ede9fe;color:#5b21b6">ОПТ</span>'); }
};
const _editCustomerL = editCustomer;
editCustomer = function(c, onSaved){
  _editCustomerL(c, onSaved);
  if(ME.role!=='admin') return;
  const m=$('#modalRoot .modal:last-child'); const anchor=m&&m.querySelector('#cuNote'); if(!anchor) return;
  anchor.insertAdjacentHTML('beforebegin',`<label>Тип цен</label><select id="cuPT"><option value="retail">Розничные цены</option><option value="wholesale" ${(c||{}).price_type==='wholesale'?'selected':''}>Оптовые цены (для оптовиков, кафе, магазинов)</option></select>`);
};
const _editProductL = editProduct;
editProduct = async function(arg, onSaved){
  await _editProductL(arg, onSaved);
  const box=$('#modalRoot .modal:last-child'); const pr=box&&box.querySelector('#fPrice'); if(!pr) return;
  const p=typeof arg==='number'?PRODUCTS_CACHE.find(x=>x.id===arg):null;
  pr.closest('.grid3').insertAdjacentHTML('afterend',`<div class="grid3"><div><label>Оптовая цена</label><input id="fWh" type="number" step="any" value="${p&&p.wholesale_price||''}" placeholder="не задана"></div>
    <div style="grid-column:span 2"><label>&nbsp;</label><div class="muted" style="font-size:12px;padding-top:6px">Применяется автоматически, когда на кассе выбран клиент с типом «Оптовые цены».</div></div></div>`);
};

// ---------------------------------------------------------------- QR-оплата
const _openPayL = openPay;
openPay = function(mode){
  PAY_QR = mode==='qr';
  const before=$$('#modalRoot .modal').length;
  _openPayL(PAY_QR?'card':mode);
  if(!PAY_QR || $$('#modalRoot .modal').length===before) return;
  const m=$('#modalRoot .modal:last-child'); const h=m.querySelector('h3');
  if(h) h.innerHTML=h.innerHTML.replace('Оплата','<span style="color:#7c3aed">QR-оплата</span>');
  const mt=[...m.querySelectorAll('.muted')].find(x=>/терминал/.test(x.textContent));
  if(mt) mt.innerHTML='Клиент сканирует QR-код Click / Payme / Uzum на стойке. Дождитесь подтверждения оплаты в приложении или в вашем кабинете, затем нажмите «Провести».';
};
window.addEventListener('keydown',e=>{
  if(e.key==='F3' && e.shiftKey && ME && $('#v-pos').classList.contains('on') && !$('#modalRoot .modal')){ e.preventDefault(); e.stopImmediatePropagation(); openPay('qr'); }
},true);

// ---------------------------------------------------------------- экран «Акции»
let ALLP=null;
async function allProducts(){ if(!ALLP) ALLP=await api('/api/products?limit=5000'); return ALLP; }
function promoWhen(p){
  const a=[];
  if(p.date_from||p.date_to) a.push(`${p.date_from||'…'} — ${p.date_to||'…'}`);
  if(p.time_from&&p.time_to) a.push(`${p.time_from}–${p.time_to}`);
  if(p.weekdays) a.push(p.weekdays.split(',').map(d=>WDN[+d-1]).join(', '));
  return a.join('<br>')||'всегда';
}
async function loadPromosView(){
  if(!CATS.length) await loadCats();
  const rows=await api('/api/promos');
  $('#promoBody').innerHTML=rows.map(p=>`<tr style="${p.active?'':'opacity:.55'}">
    <td><b>${esc(p.name)}</b></td>
    <td>${p.kind==='percent'?`<b>−${p.value}%</b>`:`<b>${p.n}+${p.m}</b> <span class="muted">(${p.n+p.m}-й в подарок)</span>`}</td>
    <td>${!p.products.length&&!p.categories.length?'весь магазин':[p.categories.length?p.categories.map(id=>esc((CATS.find(c=>c.id===id)||{}).name||'?')).join(', '):'', p.products.length?p.products.length+' товар(ов)':''].filter(Boolean).join(' · ')}</td>
    <td style="font-size:13px">${promoWhen(p)}</td>
    <td>${!p.active?'<span class="badge">⏸ выключена</span>':p.running?'<span class="badge ok">● действует сейчас</span>':'<span class="badge pend">◷ ждёт своего времени</span>'}</td>
    <td style="white-space:nowrap"><button class="ghost" onclick="editPromo(${p.id})">Изм.</button> <button class="ghost" onclick="togglePromo(${p.id},${p.active?0:1})">${p.active?'Выкл.':'Вкл.'}</button> <button class="ghost" onclick="delPromo(${p.id})">✕</button></td></tr>`).join('')
    ||'<tr><td colspan="6" class="muted">Акций пока нет. Например: «−15% на молочку по выходным» или «2+1 на воду».</td></tr>';
  window._PROMOS=rows;
}
async function togglePromo(id,on){ const p=(window._PROMOS||[]).find(x=>x.id===id); await api('/api/promos',{method:'POST',body:{...p,active:on}}); loadPromosView(); loadPromos(); }
async function delPromo(id){ if(!confirm('Удалить акцию?')) return; await api('/api/promos/'+id,{method:'DELETE'}); loadPromosView(); loadPromos(); }
async function editPromo(id){
  if(!CATS.length) await loadCats();
  const p=(window._PROMOS||[]).find(x=>x.id===id)||{kind:'percent',value:10,n:2,m:1,products:[],categories:[],weekdays:'',active:1};
  const prods=p.products.length?(await allProducts()).filter(x=>p.products.includes(x.id)):[];
  const wd=(p.weekdays||'').split(',').filter(Boolean);
  const scope=p.products.length?'products':p.categories.length?'categories':'all';
  const m=modal(`<h3>${p.id?'Акция':'Новая акция'}</h3>
    <label>Название (видно в чеке и отчётах)</label><input id="prN" value="${esc(p.name||'')}" placeholder="Напр. Счастливые часы −10%" autofocus>
    <div class="grid3"><div><label>Тип</label><select id="prK"><option value="percent">Скидка, %</option><option value="nplus" ${p.kind==='nplus'?'selected':''}>N + M в подарок</option></select></div>
      <div id="prPct"><label>Скидка, %</label><input id="prV" type="number" value="${p.value||''}"></div>
      <div id="prNM" class="row"><div><label>Купи N</label><input id="prNn" type="number" value="${p.n||2}"></div><div><label>+ M даром</label><input id="prMm" type="number" value="${p.m||1}"></div></div></div>
    <label>На что действует</label><select id="prS"><option value="all">На весь магазин</option><option value="categories" ${scope==='categories'?'selected':''}>На категории</option><option value="products" ${scope==='products'?'selected':''}>На выбранные товары</option></select>
    <div id="prCats" style="display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:6px">${CATS.map(c=>`<label style="margin:0;color:var(--text)"><input type="checkbox" value="${c.id}" ${p.categories.includes(c.id)?'checked':''} style="width:auto"> ${esc(c.name)}</label>`).join('')||'<span class="muted">Категорий нет</span>'}</div>
    <div id="prProds" style="position:relative;margin-top:6px"><input id="prQ" placeholder="Найдите товар по названию или штрихкоду"><div class="sugg hidden" id="prSugg"></div><div id="prChips" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px"></div></div>
    <div class="grid2"><div><label>С даты</label><input id="prDf" type="date" value="${p.date_from||''}"></div><div><label>По дату</label><input id="prDt" type="date" value="${p.date_to||''}"></div></div>
    <div class="grid2"><div><label>С часа (необязательно)</label><input id="prTf" type="time" value="${p.time_from||''}"></div><div><label>До часа</label><input id="prTt" type="time" value="${p.time_to||''}"></div></div>
    <label>Дни недели (пусто — каждый день)</label><div style="display:flex;gap:10px">${WDN.map((d,i)=>`<label style="margin:0;color:var(--text)"><input type="checkbox" class="prW" value="${i+1}" ${wd.includes(String(i+1))?'checked':''} style="width:auto"> ${d}</label>`).join('')}</div>
    <div class="muted" style="margin-top:10px;font-size:13px">Если на товар подходят несколько акций — касса берёт самую выгодную для покупателя. Акции не считаются в лимит скидки кассира.</div>
    <div class="actions"><button onclick="closeModal()">Отмена</button><button class="p" id="prOk">Сохранить</button></div>`,'wide');
  const g=s=>m.querySelector(s); let chosen=prods.map(x=>({id:x.id,name:x.name}));
  const vis=()=>{ g('#prPct').style.display=g('#prK').value==='percent'?'':'none'; g('#prNM').style.display=g('#prK').value==='nplus'?'':'none';
    g('#prCats').style.display=g('#prS').value==='categories'?'flex':'none'; g('#prProds').style.display=g('#prS').value==='products'?'':'none'; };
  const chips=()=>{ g('#prChips').innerHTML=chosen.map(x=>`<span class="badge" style="padding:5px 10px">${esc(x.name)} <a href="#" data-x="${x.id}" style="color:#991b1b;text-decoration:none">✕</a></span>`).join('');
    g('#prChips').querySelectorAll('[data-x]').forEach(a=>a.onclick=e=>{ e.preventDefault(); chosen=chosen.filter(x=>x.id!=a.dataset.x); chips(); }); };
  g('#prK').onchange=vis; g('#prS').onchange=vis; vis(); chips();
  let t=null, list=[];
  g('#prQ').oninput=()=>{ clearTimeout(t); const v=g('#prQ').value.trim(); if(v.length<2) return g('#prSugg').classList.add('hidden');
    t=setTimeout(async()=>{ list=await api('/api/products?limit=12&q='+encodeURIComponent(v)); renderSugg('#prSugg',list,0); $('#prSugg')._pick=x=>{ if(!chosen.some(c=>c.id===x.id)) chosen.push({id:x.id,name:x.name}); chips(); g('#prQ').value=''; g('#prSugg').classList.add('hidden'); g('#prQ').focus(); }; },180); };
  g('#prQ').onkeydown=async e=>{ if(e.key==='Enter'){ e.preventDefault(); const v=g('#prQ').value.trim();
    if(/^\d{6,}$/.test(v)){ try{ const x=await api('/api/products/barcode/'+encodeURIComponent(v)); if(!chosen.some(c=>c.id===x.id)) chosen.push({id:x.id,name:x.name}); chips(); g('#prQ').value=''; }catch(er){ toast(er.message,'err'); } }
    else if(list[0]) $('#prSugg')._pick(list[0]); } };
  g('#prOk').onclick=async()=>{
    const sc=g('#prS').value;
    try{ await api('/api/promos',{method:'POST',body:{id:p.id,name:g('#prN').value,kind:g('#prK').value,value:g('#prV').value,n:g('#prNn').value,m:g('#prMm').value,
      categories:sc==='categories'?[...m.querySelectorAll('#prCats input:checked')].map(x=>+x.value):[], products:sc==='products'?chosen.map(x=>x.id):[],
      date_from:g('#prDf').value,date_to:g('#prDt').value,time_from:g('#prTf').value,time_to:g('#prTt').value,
      weekdays:[...m.querySelectorAll('.prW:checked')].map(x=>+x.value),active:p.active??1}});
      if(sc==='categories'&&!m.querySelector('#prCats input:checked')) toast('Категории не выбраны — акция будет на весь магазин');
      closeModal(m); toast('Акция сохранена','ok'); loadPromosView(); loadPromos(); }catch(e){ toast(e.message,'err'); }
  };
}

// ---------------------------------------------------------------- переоценка
async function revalueDialog(){
  if(!CATS.length) await loadCats();
  const sel=$$('.pSel:checked').map(c=>+c.value);
  const m=modal(`<h3>↕ Переоценка</h3>
    <label>Какие товары</label><select id="rvS">${sel.length?`<option value="sel">Отмеченные в списке (${sel.length})</option>`:''}<option value="cat">Категория</option><option value="all">Все товары</option></select>
    <select id="rvC" style="margin-top:6px">${CATS.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select>
    <div class="grid3"><div><label>Способ</label><select id="rvM"><option value="pct">Изменить цену на %</option><option value="markup">Наценка % от закупки</option><option value="fixed">Одна цена для всех</option></select></div>
      <div><label id="rvVl">Изменение, % (−10 — снизить)</label><input id="rvV" type="number" step="any" value="10"></div>
      <div><label>Округлять до</label><select id="rvR">${[1,50,100,500,1000].map(x=>`<option ${x===100?'selected':''}>${x}</option>`).join('')}</select></div></div>
    <div class="actions" style="justify-content:flex-start"><button class="b" id="rvP">Предпросмотр</button><span class="muted" id="rvInfo"></span></div>
    <div class="tbl-wrap" style="max-height:42vh"><table><thead><tr><th>Товар</th><th class="n">Себест.</th><th class="n">Было</th><th class="n">Станет</th><th class="n">Изм.</th><th class="n">Остаток</th><th class="n">Разница по остатку</th></tr></thead><tbody id="rvB"></tbody></table></div>
    <label>Основание (необязательно)</label><input id="rvN" placeholder="Напр. новый прайс поставщика">
    <div class="actions"><button onclick="closeModal()">Отмена</button><button class="p" id="rvA" disabled>Применить и напечатать ценники</button></div>`,'wide');
  const g=s=>m.querySelector(s); let items=[];
  const vis=()=>{ g('#rvC').style.display=g('#rvS').value==='cat'?'':'none';
    g('#rvVl').textContent={pct:'Изменение, % (−10 — снизить)',markup:'Наценка, %',fixed:'Новая цена'}[g('#rvM').value]; };
  g('#rvS').onchange=vis; g('#rvM').onchange=vis; vis();
  g('#rvP').onclick=async()=>{
    const s=g('#rvS').value;
    if(s==='all' && !confirm('Предпросмотр по всем товарам магазина?')) return;
    try{ const r=await api('/api/revalue/preview',{method:'POST',body:{ids:s==='sel'?sel:null,category_id:s==='cat'?g('#rvC').value:null,mode:g('#rvM').value,value:g('#rvV').value,round:g('#rvR').value}});
      items=r.items;
      g('#rvB').innerHTML=items.map(i=>`<tr><td>${esc(i.name)}${i.hq?' <span class="badge" title="Цену задаёт Центр">центр</span>':''}</td><td class="n">${fmt(i.cost_price)}</td><td class="n">${fmt(i.price)}</td><td class="n"><b>${fmt(i.new_price)}</b></td>
        <td class="n ${i.diff>0?'green':'red'}">${i.diff>0?'+':''}${fmt(i.diff)}</td><td class="n">${fq(i.stock)}</td><td class="n">${fmt(i.diff_sum)}</td></tr>`).join('')||'<tr><td colspan="7" class="muted">Цены не меняются</td></tr>';
      const ds=items.reduce((a,i)=>a+i.diff_sum,0), low=items.filter(i=>i.cost_price&&i.new_price<i.cost_price).length;
      g('#rvInfo').innerHTML=`${items.length} товаров · разница по остатку <b>${ds>0?'+':''}${fmt(ds)}</b>${low?` · <span class="red">⚠ ${low} — ниже закупки</span>`:''}`;
      g('#rvA').disabled=!items.length;
    }catch(e){ toast(e.message,'err'); }
  };
  g('#rvA').onclick=async()=>{
    try{ const r=await api('/api/revalue/apply',{method:'POST',body:{items:items.map(i=>({id:i.id,new_price:i.new_price})),note:g('#rvN').value}});
      closeModal(m); toast(`Переоценка №${r.id}: ${r.lines} товаров`+(r.skipped?` · ${r.skipped} пропущено (цену задаёт Центр)`:''),'ok');
      ALLP=null; if(typeof loadProductsList==='function') loadProductsList(); if(r.ids.length) labelsDialog(r.ids);
    }catch(e){ toast(e.message,'err'); }
  };
}
async function revalueHistory(){
  const rows=await api('/api/revaluations');
  modal(`<h3>История переоценок</h3><div class="tbl-wrap" style="max-height:60vh"><table><thead><tr><th>№</th><th>Дата</th><th>Кто</th><th>Основание</th><th class="n">Товаров</th><th class="n">Разница по остатку</th></tr></thead><tbody>
    ${rows.map(r=>`<tr><td>${r.id}</td><td>${r.created_at.slice(0,16)}</td><td>${esc(r.user||'')}</td><td>${esc(r.note||'')}</td><td class="n">${r.lines}</td><td class="n ${r.diff_sum>=0?'green':'red'}">${fmt(r.diff_sum)}</td></tr>`).join('')||'<tr><td class="muted" colspan="6">Переоценок не было</td></tr>'}
    </tbody></table></div><div class="actions"><button class="p" onclick="closeModal()">Закрыть</button></div>`,'wide');
}

// ---------------------------------------------------------------- графики
function colChart(data, lbl, val, opt={}){
  if(!data.length) return '<div class="muted" style="padding:30px;text-align:center">Нет данных</div>';
  const H=opt.h||150, max=Math.max(...data.map(val),1), n=data.length, step=Math.max(1,Math.ceil(n/(opt.ticks||14)));
  return `<div style="display:flex;align-items:flex-end;gap:2px;height:${H}px;padding-top:16px">${data.map((d,i)=>{ const v=val(d);
    return `<div style="flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%;min-width:0" title="${esc(lbl(d))}: ${fmt(v)}${opt.unit||''}">
      ${opt.top&&v?`<div style="font-size:10px;text-align:center;color:var(--muted);white-space:nowrap;overflow:hidden">${opt.top(v)}</div>`:''}
      <div style="height:${Math.max(v>0?2:0,v/max*100)}%;background:${opt.hi&&opt.hi(d,i)?'var(--brand)':'color-mix(in srgb,var(--brand) 45%,#fff)'};border-radius:4px 4px 0 0;max-width:46px;margin:0 auto;width:100%"></div></div>`; }).join('')}</div>
    <div style="display:flex;gap:2px;font-size:10px;color:var(--muted);margin-top:3px">${data.map((d,i)=>`<div style="flex:1;text-align:center;overflow:hidden;white-space:nowrap">${i%step===0?esc(opt.x?opt.x(d):lbl(d)):''}</div>`).join('')}</div>`;
}
function hBars(rows){ // [{l, v, sub}]
  const max=Math.max(...rows.map(r=>Math.abs(r.v)),1);
  return rows.map(r=>`<div style="margin:7px 0"><div class="row" style="font-size:13px"><span class="grow">${r.l}</span><b>${fmt(r.v)}</b>${r.sub?`<span class="muted" style="width:52px;text-align:right">${r.sub}</span>`:''}</div>
    <div style="height:8px;background:#f3f4f6;border-radius:4px;margin-top:3px"><div style="height:100%;width:${Math.abs(r.v)/max*100}%;background:var(--brand);border-radius:4px"></div></div></div>`).join('');
}
function heatmap(heat, cnt){
  let max=1; heat.forEach(r=>r.forEach(v=>{ if(v>max) max=v; }));
  const hrs=[...Array(24).keys()].filter(h=>heat.some(r=>r[h]>0));
  if(!hrs.length) return '<div class="muted">Нет продаж за период</div>';
  const H=[...Array(hrs[hrs.length-1]-hrs[0]+1).keys()].map(i=>i+hrs[0]);
  return `<div class="tbl-wrap"><table class="heat"><tr><td></td>${H.map(h=>`<td class="muted">${h}</td>`).join('')}</tr>
    ${heat.map((r,d)=>`<tr><td class="muted" style="padding-right:6px;font-size:12px">${WDN[d]}</td>${H.map(h=>{ const a=r[h]/max;
      return `<td title="${WDN[d]} ${h}:00–${h+1}:00 · ${fmt(r[h])} · чеков ${cnt[d][h]}" style="background:${r[h]?`color-mix(in srgb,var(--brand) ${Math.round(8+a*92)}%,#fff)`:'#f9fafb'};color:${a>.55?'#fff':'transparent'}">${cnt[d][h]||''}</td>`; }).join('')}</tr>`).join('')}
    </table></div><div class="muted" style="font-size:12px;margin-top:6px">Светлее — меньше выручки, темнее — больше. Цифра — число чеков. Наведите на клетку для деталей. Используйте для графика смен и завоза.</div>`;
}

// ---------------------------------------------------------------- «Обзор»
let DASH_T=null;
function productCard(p){
  if(!p) return; hideSugg('#gSugg'); $('#gQ').value='';
  const adm=ME.role==='admin', mk=p.cost_price?Math.round((p.price/p.cost_price-1)*100):null;
  const m=modal(`<div class="row"><h3 class="grow" style="margin:0">${esc(p.name)}</h3><span class="muted">${esc(p.category||'')}</span></div>
    <div class="muted" style="margin-bottom:10px">${esc(p.barcode||'без штрихкода')}${p.sku?' · арт. '+esc(p.sku):''}</div>
    <div class="kpis" style="grid-template-columns:repeat(${adm?4:2},1fr)">${KPI('Цена',fmt(p.price))}${KPI('Остаток',fq(p.stock)+' '+esc(p.unit_name||''),p.stock<=0?'red':'')}
      ${adm?KPI('Закупка',fmt(p.cost_price),'',mk!=null?`<span class="muted" style="font-size:11px">наценка ${mk}%</span>`:''):''}${adm?KPI('Опт',p.wholesale_price?fmt(p.wholesale_price):'—'):''}</div>
    <div class="actions" style="flex-wrap:wrap">
      <button class="p" id="pcAdd">+ В чек</button><button id="pcLbl">🏷 Ценник</button>
      ${adm?'<button id="pcMov">Движение</button><button id="pcEd">Изменить</button>':''}<button onclick="closeModal()">Закрыть</button></div>`);
  m.querySelector('#pcAdd').onclick=()=>{ closeModal(m); show('pos'); addToCart(p); };
  m.querySelector('#pcLbl').onclick=()=>{ closeModal(m); labelsDialog([p.id]); };
  m.querySelector('#pcMov')?.addEventListener('click',()=>{ closeModal(m); productMoves(p.id); });
  m.querySelector('#pcEd')?.addEventListener('click',()=>{ closeModal(m); if(!PRODUCTS_CACHE.some(x=>x.id===p.id)) PRODUCTS_CACHE.push(p); editProduct(p.id); });
}

// ---------------------------------------------------------------- центр отчётов
let REP_TAB='main', REP_DATA=null, PR_F='all';
$$('#repTabs button').forEach(b=>b.onclick=()=>{ REP_TAB=b.dataset.t; $$('#repTabs button').forEach(x=>x.classList.toggle('on',x===b)); loadReport(); });
const _loadReportL = loadReport;
loadReport = async function(){
  $('#repMain').classList.toggle('hidden',REP_TAB!=='main'); $('#repOther').classList.toggle('hidden',REP_TAB==='main');
  if(REP_TAB==='main') return _loadReportL();
  const box=$('#repOther'); box.innerHTML='<div class="muted" style="padding:30px">Считаю…</div>';
  try{ REP_DATA=await api(`/api/rep/${REP_TAB}?from=${$('#rFrom').value}&to=${$('#rTo').value}`); ({sales:repSales,products:repProducts,movement:repMovement,profit:repProfit,staff:repStaff,vat:repVat})[REP_TAB](REP_DATA, box); }
  catch(e){ box.innerHTML=`<div class="card red">${esc(e.message)}</div>`; }
};
function repReload(){ loadReport(); }
function repExport(){ const k=REP_TAB==='main'?'sales':REP_TAB; location.href=`/api/rep/export/${k}?from=${$('#rFrom').value}&to=${$('#rTo').value}`; }

function repSales(r, box){
  const t=r.totals, c=r.change, p=r.pay, ps=p.cash+p.card+p.qr+p.debt+p.bonus;
  const pays=[{l:'Наличные',v:p.cash},{l:'Карта',v:p.card},{l:'QR Click/Payme',v:p.qr},{l:'Насия',v:p.debt},{l:'Бонусы',v:p.bonus}].filter(x=>x.v).map(x=>({...x,sub:pct(x.v,ps)+'%'}));
  const cs=r.categories.reduce((a,x)=>a+x.net,0);
  box.innerHTML=`<div class="muted" style="margin-bottom:8px">Сравнение с периодом ${r.prev_period[0]} — ${r.prev_period[1]}</div>
    <div class="kpis">${KPI('Выручка',fmt(t.net),'',CHG(c.net))}${KPI('Чеков',t.checks,'',CHG(c.checks))}${KPI('Средний чек',fmt(Math.round(t.avg)),'',CHG(c.avg))}
      ${KPI('Позиций в чеке',t.lines_per_check,'',CHG(c.lines_per_check))}${KPI('Валовая прибыль',fmt(t.profit),'green',CHG(c.profit))}${KPI('Маржа',t.margin+'%','',CHG(c.margin))}
      ${KPI('Скидки',fmt(t.discount),'amber',CHG(c.discount,true))}${KPI('Возвраты',fmt(t.refunds),'red',CHG(c.refunds,true))}
      ${KPI('Покупки с картой клиента',pct(r.customers.net,t.net)+'%','',`<span class="muted" style="font-size:12px">${r.customers.n} клиентов</span>`)}</div>
    <div class="grid2" style="margin-top:12px;gap:12px">
      <div class="card" style="grid-column:1/-1"><b>Выручка по дням</b>${colChart(r.by_day,d=>d.d+' · чеков '+d.checks,d=>d.net,{x:d=>d.d.slice(5),ticks:16})}</div>
      <div class="card" style="grid-column:1/-1"><b>Когда покупают: день недели × час</b>${heatmap(r.heat,r.heat_n)}</div>
      <div class="card"><b>Способы оплаты</b>${pays.length?hBars(pays):'<div class="muted">Нет данных</div>'}</div>
      <div class="card"><b>Акции</b>${r.promos.length?`<table><thead><tr><th>Акция</th><th class="n">Строк</th><th class="n">Продано на</th></tr></thead><tbody>${r.promos.map(x=>`<tr><td>🎁 ${esc(x.promo)}</td><td class="n">${x.n}</td><td class="n">${fmt(x.net)}</td></tr>`).join('')}</tbody></table>`:'<div class="muted" style="padding:10px 0">Продаж по акциям не было. <a href="#" onclick="show(\'promos\');return false">Создать акцию →</a></div>'}</div>
      <div class="card" style="grid-column:1/-1"><b>Категории</b><table><thead><tr><th>Категория</th><th class="n">Выручка</th><th style="width:30%">Доля</th><th class="n">Прибыль</th><th class="n">Маржа</th></tr></thead><tbody>
        ${r.categories.map(x=>`<tr><td>${esc(x.cat)}</td><td class="n">${fmt(x.net)}</td><td><div style="height:8px;background:#f3f4f6;border-radius:4px"><div style="height:100%;width:${pct(x.net,cs)}%;background:var(--brand);border-radius:4px"></div></div><span class="muted" style="font-size:11px">${pct(x.net,cs)}%</span></td><td class="n green">${fmt(x.profit)}</td><td class="n">${pct(x.profit,x.net)}%</td></tr>`).join('')}</tbody></table></div>
    </div>`;
}
const ABC_TIP={AX:'Лидеры, стабильный спрос — никогда не допускать отсутствия, автозаказ',AY:'Лидеры с колебаниями — держать страховой запас',AZ:'Много денег, но спрос скачет — заказывать под акции/сезон',
  BX:'Средние, стабильные — регулярный заказ',BY:'Средние — заказ по факту продаж',BZ:'Средние, нестабильные — минимальный запас',
  CX:'Мало денег, но стабильно — держать минимум',CY:'Кандидаты на сокращение ассортимента',CZ:'Кандидаты на вывод из ассортимента'};
function repProducts(r, box){
  const cnt=k=>r.items.filter(i=>i.abc===k).length;
  const cell=k=>{ const m=r.matrix[k]||{n:0,revenue:0}; const strong=k[0]==='A'?.9:k[0]==='B'?.55:.25;
    return `<td title="${ABC_TIP[k]}" style="background:color-mix(in srgb,var(--brand) ${Math.round(strong*(k[1]==='X'?100:k[1]==='Y'?70:45))}%,#fff);color:${strong>.5?'#fff':'var(--text)'};padding:10px;text-align:center;cursor:pointer;border:2px solid var(--card);border-radius:8px" onclick="PR_F='${k}';repProducts(REP_DATA,$('#repOther'))">
      <div style="font-weight:700">${k}</div><div style="font-size:12px">${m.n} тов. · ${fmt(m.revenue)}</div></td>`; };
  let rows=r.items;
  if(PR_F==='frozen') rows=rows.filter(i=>i.frozen); else if(PR_F.length===1) rows=rows.filter(i=>i.abc===PR_F); else if(PR_F.length===2) rows=rows.filter(i=>i.abc+i.xyz===PR_F);
  const q=($('#prodRepQ')||{}).value||''; if(q) rows=rows.filter(i=>(i.name+' '+(i.barcode||'')).toLowerCase().includes(q.toLowerCase()));
  const chip=(k,l)=>`<button class="${PR_F===k?'p':'ghost'}" onclick="PR_F='${k}';repProducts(REP_DATA,$('#repOther'))">${l}</button>`;
  box.innerHTML=`<div class="kpis">${KPI('Склад по себестоимости',fmt(r.stock_value))}${KPI('Заморожено в товаре',fmt(r.frozen_sum),'amber',`<span class="muted" style="font-size:12px">${r.frozen_n} поз. без продаж или запас &gt; 90 дней</span>`)}
      ${KPI('Группа A',cnt('A'),'green','<span class="muted" style="font-size:12px">80% выручки</span>')}${KPI('Группа B',cnt('B'))}${KPI('Группа C',cnt('C'),'','<span class="muted" style="font-size:12px">последние 5% выручки</span>')}</div>
    <div class="grid2" style="margin-top:12px;gap:12px;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr)">
      <div class="card"><b>Матрица ABC × XYZ</b><div class="muted" style="font-size:12px">ABC — вклад в выручку, XYZ — стабильность спроса (по ${r.bucket==='день'?'дням':'неделям'}). Нажмите клетку — отфильтровать.</div>
        <table style="margin-top:8px;border-collapse:separate"><tr><td></td><td class="muted" style="text-align:center">X стабильно</td><td class="muted" style="text-align:center">Y колеблется</td><td class="muted" style="text-align:center">Z случайно</td></tr>
        ${['A','B','C'].map(a=>`<tr><td class="muted"><b>${a}</b></td>${['X','Y','Z'].map(x=>cell(a+x)).join('')}</tr>`).join('')}</table></div>
      <div class="card"><b>Что делать</b><div style="font-size:13px;margin-top:6px">${Object.entries(ABC_TIP).map(([k,v])=>`<div style="padding:3px 0"><b style="display:inline-block;width:30px">${k}</b>${v}</div>`).join('')}</div></div>
    </div>
    <div class="card" style="margin-top:12px"><div class="row wrap" style="margin-bottom:8px"><b class="grow">Товары (${rows.length})</b>
      ${chip('all','Все')}${chip('A','A')}${chip('B','B')}${chip('C','C')}${chip('frozen','❄ Замороженные')}<input id="prodRepQ" placeholder="Поиск" style="width:200px" value="${esc(q)}"></div>
      <div class="tbl-wrap" style="max-height:60vh"><table><thead><tr><th>Товар</th><th>ABC</th><th>XYZ</th><th class="n">Продано</th><th class="n">В день</th><th class="n">Выручка</th><th class="n">Прибыль</th><th class="n">Маржа</th><th class="n">Остаток</th><th class="n">Хватит, дней</th><th class="n">В товаре</th></tr></thead><tbody>
      ${rows.slice(0,400).map(i=>`<tr><td>${esc(i.name)}<div class="muted" style="font-size:11px">${esc(i.category)}</div></td><td><b>${i.abc}</b></td><td title="${i.cv!=null?'Коэф. вариации '+i.cv+'%':''}">${i.xyz}</td>
        <td class="n">${fq(i.qty)}</td><td class="n">${i.per_day||''}</td><td class="n">${fmt(i.revenue)}</td><td class="n green">${fmt(i.profit)}</td><td class="n">${i.margin!=null?i.margin+'%':''}</td>
        <td class="n ${i.stock<=0&&i.qty>0?'red':''}">${fq(i.stock)}</td><td class="n ${i.cover_days!=null&&i.cover_days<3?'red':i.cover_days>90?'amber':''}">${i.cover_days??(i.stock>0&&i.qty<=0?'∞':'')}</td><td class="n">${fmt(i.stock_value)}</td></tr>`).join('')}
      </tbody></table></div>${rows.length>400?'<div class="muted">Показаны первые 400 — полный список в Excel</div>':''}</div>`;
  const qi=$('#prodRepQ'); qi.oninput=()=>{ clearTimeout(qi._t); qi._t=setTimeout(()=>{ repProducts(REP_DATA,box); const n=$('#prodRepQ'); n.focus(); n.setSelectionRange(n.value.length,n.value.length); },250); };
}
function repMovement(r, box){
  const t=r.totals_cost;
  box.innerHTML=`<div class="kpis">${KPI('Остаток на начало',fmt(t.opening))}${KPI('Приход',fmt(t.in),'green')}${KPI('Расход',fmt(t.out),'red')}${KPI('Остаток на конец',fmt(t.closing))}</div>
    <div class="muted" style="font-size:12px;margin:6px 0">Суммы — по текущей себестоимости. Приход = закупки + приёмки + возвраты покупателей + излишки ревизий. Расход = продажи + списания + отгрузки + возвраты поставщику + недостачи.</div>
    <div class="card"><div class="tbl-wrap" style="max-height:66vh"><table><thead><tr><th>Товар</th><th class="n">Начало</th><th class="n">Закупка</th><th class="n">Приёмка</th><th class="n">Возврат</th><th class="n">Излишки</th><th class="n">Продажа</th><th class="n">Списание</th><th class="n">Отгрузка</th><th class="n">Возврат пост.</th><th class="n">Недостача</th><th class="n">Конец</th></tr></thead><tbody>
    ${r.items.map(i=>{ const z=v=>v?fq(v):'<span class="muted">·</span>'; return `<tr><td>${esc(i.name)}</td><td class="n"><b>${fq(i.opening)}</b></td><td class="n green">${z(i.purchase)}</td><td class="n green">${z(i.receive)}</td><td class="n green">${z(i.refund)}</td><td class="n green">${z(i.inv_plus)}</td>
      <td class="n red">${z(i.sale)}</td><td class="n red">${z(i.writeoff)}</td><td class="n red">${z(i.transfer_out)}</td><td class="n red">${z(i.supplier_return)}</td><td class="n red">${z(i.inv_minus)}</td><td class="n"><b>${fq(i.closing)}</b></td></tr>`; }).join('')||'<tr><td class="muted" colspan="11">Нет движения</td></tr>'}
    </tbody></table></div></div>`;
}
function repProfit(r, box){
  const L=(l,v,cls='',b=false,ind=0)=>`<tr><td style="padding-left:${8+ind*18}px">${b?'<b>'+l+'</b>':l}</td><td class="n ${cls}">${b?'<b>':''}${fmt(v)}${b?'</b>':''}</td><td class="n muted">${r.revenue?pct(v,r.revenue)+'%':''}</td></tr>`;
  box.innerHTML=`<div class="kpis">${KPI('Выручка',fmt(r.revenue))}${KPI('Валовая прибыль',fmt(r.gross),'green',`<span class="muted" style="font-size:12px">маржа ${r.margin}%</span>`)}
      ${KPI('Расходы',fmt(r.expenses_total),'red')}${KPI('Чистая прибыль',fmt(r.net_profit),r.net_profit>=0?'green':'red',`<span class="muted" style="font-size:12px">${r.net_margin}% от выручки</span>`)}</div>
    <div class="grid2" style="margin-top:12px;gap:12px">
      <div class="card"><b>Отчёт о прибыли</b><table style="margin-top:6px"><tbody>
        ${L('Выручка',r.revenue,'',true)}${L('− Себестоимость проданного',-r.cost,'red',false,1)}${L('Валовая прибыль',r.gross,'green',true)}
        ${r.expenses.map(e=>L('− '+esc(e.cat),-e.amount,'red',false,1)).join('')}
        ${r.writeoff?L('− Списания (по себестоимости)',-r.writeoff,'red',false,1):''}${r.inventory_diff?L(r.inventory_diff>0?'+ Излишки ревизий':'− Недостачи ревизий',r.inventory_diff,r.inventory_diff>0?'green':'red',false,1):''}
        ${L('Чистая прибыль',r.net_profit,r.net_profit>=0?'green':'red',true)}</tbody></table>
        <div class="muted" style="font-size:12px;margin-top:8px">Расходы — из «Кассовых операций» (тип «расход») по категориям. Оплаты поставщикам сюда не входят — это не расход, а погашение долга за товар.</div></div>
      <div class="card"><b>Расходы по статьям</b>${r.expenses.length?hBars(r.expenses.map(e=>({l:esc(e.cat),v:e.amount,sub:pct(e.amount,r.expenses_total)+'%'}))):'<div class="muted" style="padding:10px 0">Расходов не внесено. Внести: Смена → Кассовые операции → Расход.</div>'}</div>
      <div class="card" style="grid-column:1/-1"><b>Валовая прибыль по дням</b>${colChart(r.by_day,d=>d.d+' · расходы '+fmt(d.expenses)+' · чистая '+fmt(d.net),d=>Math.max(0,d.gross),{x:d=>d.d.slice(5),ticks:16})}</div>
    </div>`;
}
function repStaff(r, box){
  const RISK={low:['✓ норма','ok'],mid:['◷ проверить','pend'],high:['⚠ высокий','err']};
  box.innerHTML=`<div class="card"><b>Эффективность и контроль кассиров</b>
    <div class="muted" style="font-size:12px;margin:4px 0 8px">Риск считается по удалённым строкам, очищенным чекам, возвратам и расхождениям кассы. Детали каждой операции — в «Журнале».</div>
    <div class="tbl-wrap"><table><thead><tr><th>Сотрудник</th><th class="n">Часов</th><th class="n">Чеков</th><th class="n">Выручка</th><th class="n">В час</th><th class="n">Средний чек</th><th class="n">Скидки</th><th class="n">Возвраты</th><th class="n">Удалено строк</th><th class="n">Очищено чеков</th><th class="n">Расхожд. кассы</th><th>Риск</th></tr></thead><tbody>
    ${r.items.map(i=>`<tr><td><b>${esc(i.name)}</b><div class="muted" style="font-size:11px">${i.role==='admin'?'администратор':'кассир'}</div></td><td class="n">${i.hours}</td><td class="n">${i.checks}</td><td class="n"><b>${fmt(i.net)}</b></td><td class="n">${i.per_hour!=null?fmt(i.per_hour):''}</td><td class="n">${fmt(i.avg)}</td>
      <td class="n ${i.discount_pct>5?'amber':''}">${fmt(i.discount)} <span class="muted">(${i.discount_pct}%)</span></td><td class="n ${i.refunds?'red':''}">${i.refunds}${i.refunds?` · ${fmt(i.refunds_sum)}`:''}</td>
      <td class="n ${i.deleted>5?'amber':''}">${i.deleted}</td><td class="n ${i.cleared?'amber':''}">${i.cleared}</td><td class="n ${i.cash_diff?'red':''}">${i.cash_diff}</td>
      <td><span class="badge ${RISK[i.risk][1]}">${RISK[i.risk][0]}</span></td></tr>`).join('')||'<tr><td class="muted" colspan="12">Нет данных за период</td></tr>'}
    </tbody></table></div></div>`;
}
function repVat(r, box){
  box.innerHTML=`<div class="kpis">${KPI('Продажи с НДС',fmt(r.gross_total))}${KPI('В т.ч. НДС',fmt(r.vat_total),'amber')}${KPI('Без НДС',fmt(r.gross_total-r.vat_total))}</div>
    <div class="card" style="margin-top:12px"><table><thead><tr><th>Ставка</th><th class="n">Продажи с НДС</th><th class="n">В т.ч. НДС</th><th class="n">Без НДС</th></tr></thead><tbody>
    ${r.items.map(i=>`<tr><td>${i.rate?i.rate+'%':'Без НДС'}</td><td class="n">${fmt(i.gross)}</td><td class="n">${fmt(i.vat)}</td><td class="n">${fmt(i.net)}</td></tr>`).join('')||'<tr><td class="muted" colspan="4">Нет продаж</td></tr>'}
    </tbody></table><div class="muted" style="font-size:12px;margin-top:8px">НДС выделяется из цены по ставке товара (как в фискальном чеке). Возвраты вычтены.</div></div>`;
}

// ---------------------------------------------------------------- навигация
const _showL = show;
show = function(v){
  _showL(v);
  clearInterval(DASH_T);
  if(v==='dash'){ loadDash(); DASH_T=setInterval(()=>{ if($('#v-dash').classList.contains('on')) loadDash(); },60000); }
  else if(v==='promos') loadPromosView();
  else if(v==='pos') loadPromos();
};
setInterval(()=>{ if(ME) loadPromos(); }, 5*60*1000);
