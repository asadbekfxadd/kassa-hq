// =====================================================================
//  Склад (этап 3): статусы остатков в «Товарах», бренд и история цен
//  в карточке товара, возврат поставщику с актом.
// =====================================================================
const ST_COLORS={out:['#b42318','#fef3f2'],low:['#b54708','#fffaeb'],risk:['#c4320a','#fff6ed'],nosale:['#475467','#f2f4f7'],over:['#6941c6','#f4f3ff'],in:['#067647','#ecfdf3']};
const ST_HINT={out:'Остаток 0 или меньше',low:'Не выше минимального остатка',risk:'При текущих продажах закончится за 3 дня',nosale:'Есть на складе, но не продавался 30 дней',over:'Запаса больше чем на 60 дней продаж',in:'Всё в порядке'};
let STS=null, ST_FILTER='';
const stBadge=(k,t)=>`<span class="badge" title="${ST_HINT[k]}" style="background:${ST_COLORS[k][1]};color:${ST_COLORS[k][0]}">${t}</span>`;

// бренд в форме товара уходит на сервер вместе с остальными полями
const _apiS = api;
api = async function(url,opt={}){
  const b=opt.body;
  if(b && typeof b==='object' && !(b instanceof FormData) && /^\/api\/products(\/\d+)?$/.test(url) && (opt.method==='POST'||opt.method==='PUT') && $('#fBrand')) b.brand=$('#fBrand').value;
  return _apiS(url,opt);
};

// ---------------------------------------------------------------- статусы в списке товаров
const _lplS = loadProductsList;
loadProductsList = async function(){
  await _lplS();
  try{ STS=await api('/api/stock/status'); }catch(e){ return; }
  const byId=Object.fromEntries(STS.items.map(x=>[x.id,x]));
  let bar=$('#stStrip');
  if(!bar){ $('#prodBody').closest('.tbl-wrap').insertAdjacentHTML('beforebegin','<div id="stStrip" class="row wrap" style="gap:6px;margin:0 0 10px"></div>'); bar=$('#stStrip'); }
  bar.innerHTML=`<button class="${ST_FILTER?'ghost':'p'}" data-sf="" style="padding:5px 12px">Все</button>`+Object.entries(STS.titles).map(([k,t])=>
    `<button class="${ST_FILTER===k?'p':'ghost'}" data-sf="${k}" title="${ST_HINT[k]}" style="padding:5px 12px">${t} <b style="margin-left:4px;color:${ST_FILTER===k?'#fff':ST_COLORS[k][0]}">${STS.counts[k]}</b></button>`).join('');
  bar.querySelectorAll('[data-sf]').forEach(b=>b.onclick=()=>{ ST_FILTER=b.dataset.sf; loadProductsList(); });
  $$('#prodBody tr').forEach(tr=>{ const id=+(tr.querySelector('.pSel')||{}).value, x=byId[id]; if(!x) return;
    const td=tr.children[8]; if(td) td.insertAdjacentHTML('beforeend',`<div style="margin-top:2px">${stBadge(x.status,x.status_title)}${x.cover_days?` <span class="muted" style="font-size:11px">${x.cover_days} дн.</span>`:''}</div>`);
    if(x.brand){ const nm=tr.children[1]; nm.insertAdjacentHTML('beforeend',`<div class="muted" style="font-size:11px">${esc(x.brand)}</div>`); }
    if(ST_FILTER && x.status!==ST_FILTER) tr.style.display='none'; });
};

// ---------------------------------------------------------------- бренд и история цен в карточке
const _epS = editProduct;
editProduct = async function(arg,onSaved){
  await _epS(arg,onSaved);
  const box=$('#modalRoot .modal:last-child'); const sku=box&&box.querySelector('#fSku'); if(!sku) return;
  const p=typeof arg==='number'?PRODUCTS_CACHE.find(x=>x.id===arg):null;
  sku.closest('.grid2').insertAdjacentHTML('afterend',`<div class="grid2"><div><label>Бренд / производитель</label><input id="fBrand" list="brandList" value="${esc(p&&p.brand||'')}" placeholder="Напр. Nestlé, Coca-Cola, Lactel"><datalist id="brandList"></datalist></div>
    <div>${p?`<label>&nbsp;</label><button id="fPH" style="width:100%">📈 История цен</button>`:''}</div></div>`);
  const brands=[...new Set(PRODUCTS_CACHE.map(x=>x.brand).filter(Boolean))].sort(); box.querySelector('#brandList').innerHTML=brands.map(b=>`<option value="${esc(b)}">`).join('');
  box.querySelector('#fPH')?.addEventListener('click',e=>{ e.preventDefault(); priceHistory(p.id,p.name); });
};
async function priceHistory(id,name){
  const rows=await api(`/api/products/${id}/prices`);
  const d=(a,b)=>a==null||b==null||Math.abs(a-b)<0.001?'':`<span class="${b>a?'red':'green'}" style="font-size:12px"> ${b>a?'+':''}${fmt(b-a)}</span>`;
  modal(`<h3>История цен: ${esc(name)}</h3><div class="tbl-wrap" style="max-height:60vh"><table><thead><tr><th>Когда</th><th class="n">Цена</th><th class="n">Закупка</th><th>Источник</th><th>Кто</th></tr></thead><tbody>
    ${rows.map(r=>`<tr><td class="muted" style="white-space:nowrap">${r.created_at.slice(0,16)}</td>
      <td class="n">${Math.abs((r.old_price||0)-(r.new_price||0))>0.001?`<span class="muted">${fmt(r.old_price)} →</span> <b>${fmt(r.new_price)}</b>${d(r.old_price,r.new_price)}`:fmt(r.new_price)}</td>
      <td class="n">${Math.abs((r.old_cost||0)-(r.new_cost||0))>0.001?`<span class="muted">${fmt(r.old_cost)} →</span> <b>${fmt(r.new_cost)}</b>`:fmt(r.new_cost)}</td>
      <td>${esc(r.source||'')}</td><td>${esc(r.user||'')}</td></tr>`).join('')||'<tr><td colspan="5" class="muted">Цена не менялась</td></tr>'}
    </tbody></table></div><div class="actions"><button class="p" onclick="closeModal()">Закрыть</button></div>`,'wide');
}

// ---------------------------------------------------------------- возврат поставщику
const _lsS = loadSuppliers;
loadSuppliers = async function(){
  await _lsS();
  const card=$('#supBody').closest('.card');
  if(!$('#srBtn')){ card.querySelector('.row').insertAdjacentHTML('beforeend','<button id="srBtn" onclick="supplierReturn()">↩ Возврат поставщику</button>');
    card.insertAdjacentHTML('afterend','<div class="card" style="margin-top:12px"><b>Возвраты поставщикам</b><table style="margin-top:6px"><thead><tr><th>№</th><th>Дата</th><th>Поставщик</th><th>Причина</th><th class="n">Позиций</th><th class="n">Сумма</th><th>Кто</th><th></th></tr></thead><tbody id="srBody"></tbody></table></div>'); }
  const r=await api('/api/supplier_returns');
  $('#srBody').innerHTML=r.items.map(x=>`<tr><td>${x.id}</td><td class="muted">${x.created_at.slice(0,16)}</td><td><b>${esc(x.supplier||'')}</b></td><td>${esc(x.reason)}${x.note?` <span class="muted">· ${esc(x.note)}</span>`:''}</td>
    <td class="n">${x.lines}</td><td class="n">${fmt(x.total)}</td><td>${esc(x.user||'')}</td><td><button class="ghost" onclick="window.open('/supplier-return/${x.id}','_blank')">Акт</button></td></tr>`).join('')||'<tr><td colspan="8" class="muted">Возвратов не было</td></tr>';
};
async function supplierReturn(){
  const [sups, meta]=await Promise.all([api('/api/suppliers'), api('/api/supplier_returns')]);
  if(!sups.length) return toast('Сначала добавьте поставщика','err');
  let lines=[];
  const m=modal(`<h3>↩ Возврат товара поставщику</h3>
    <div class="grid3"><div><label>Поставщик</label><select id="srS">${sups.map(s=>`<option value="${s.id}">${esc(s.name)}${s.balance>0?' · долг '+fmt(s.balance):''}</option>`).join('')}</select></div>
      <div><label>Причина</label><select id="srR">${meta.reasons.map(x=>`<option>${esc(x)}</option>`).join('')}</select></div><div><label>Комментарий</label><input id="srN" placeholder="№ накладной, имя экспедитора"></div></div>
    <div style="position:relative;margin-top:10px"><input id="srQ" placeholder="Сканируйте штрихкод или ищите товар по названию" autocomplete="off" autofocus><div class="sugg hidden" id="srSugg"></div></div>
    <div class="tbl-wrap" style="max-height:42vh;margin-top:8px"><table><thead><tr><th>Товар</th><th class="n">Остаток</th><th class="n">Вернуть</th><th class="n">Цена закупки</th><th class="n">Сумма</th><th></th></tr></thead><tbody id="srB"></tbody></table></div>
    <div class="row" style="margin-top:8px"><span class="grow muted" style="font-size:12.5px">Остаток уменьшится, долг перед поставщиком — на сумму возврата. После проведения откроется акт для подписи.</span><b id="srT" style="font-size:18px"></b></div>
    <div class="actions"><button onclick="closeModal()">Отмена</button><button class="r" id="srOk">Провести возврат</button></div>`,'wide');
  const g=s=>m.querySelector(s);
  const draw=()=>{ g('#srB').innerHTML=lines.map((l,i)=>`<tr><td>${esc(l.name)}<div class="muted" style="font-size:11px">${esc(l.barcode||'')}</div></td><td class="n">${fq(l.stock)}</td>
      <td class="n"><input type="number" step="any" value="${l.qty}" data-q="${i}" style="width:80px"></td><td class="n"><input type="number" step="any" value="${l.cost}" data-c="${i}" style="width:110px"></td>
      <td class="n">${fmt(l.qty*l.cost)}</td><td><button class="ghost" data-x="${i}">✕</button></td></tr>`).join('')||'<tr><td colspan="6" class="muted">Добавьте товары</td></tr>';
    g('#srT').textContent=lines.length?`Итого ${fmt(lines.reduce((a,l)=>a+l.qty*l.cost,0))}`:'';
    m.querySelectorAll('[data-q]').forEach(x=>x.onchange=()=>{ lines[+x.dataset.q].qty=+x.value; draw(); });
    m.querySelectorAll('[data-c]').forEach(x=>x.onchange=()=>{ lines[+x.dataset.c].cost=+x.value; draw(); });
    m.querySelectorAll('[data-x]').forEach(x=>x.onclick=()=>{ lines.splice(+x.dataset.x,1); draw(); }); };
  const add=p=>{ const ex=lines.find(l=>l.product_id===p.id); if(ex) ex.qty+=1; else lines.unshift({product_id:p.id,name:p.name,barcode:p.barcode,stock:p.stock,qty:1,cost:p.cost_price||0});
    g('#srQ').value=''; g('#srSugg').classList.add('hidden'); draw(); g('#srQ').focus(); };
  let t=null, list=[];
  g('#srQ').oninput=()=>{ clearTimeout(t); const v=g('#srQ').value.trim(); if(v.length<2||/^\d{6,}$/.test(v)) return g('#srSugg').classList.add('hidden');
    t=setTimeout(async()=>{ list=await api('/api/products?limit=12&q='+encodeURIComponent(v)); renderSugg('#srSugg',list,0); $('#srSugg')._pick=add; },170); };
  g('#srQ').onkeydown=async e=>{ if(e.key!=='Enter') return; e.preventDefault(); const v=g('#srQ').value.trim(); if(!v) return;
    if(/^\d{6,}$/.test(v)){ try{ add(await api('/api/products/barcode/'+encodeURIComponent(v))); }catch(er){ beep(true); toast(er.message,'err'); g('#srQ').value=''; } }
    else if(list[0]) add(list[0]); };
  g('#srOk').onclick=async()=>{ if(!lines.length) return toast('Добавьте товары','err');
    try{ const r=await api('/api/supplier_returns',{method:'POST',body:{supplier_id:g('#srS').value,reason:g('#srR').value,note:g('#srN').value,items:lines}});
      closeModal(m); toast(`Возврат №${r.id} проведён на ${fmt(r.total)}. Долг поставщику: ${fmt(r.balance)}`,'ok'); window.open(`/supplier-return/${r.id}?print=1`,'_blank'); loadSuppliers();
    }catch(e){ toast(e.message,'err'); } };
  draw();
}
