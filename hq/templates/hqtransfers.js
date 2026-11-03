// =====================================================================
//  Центр: перемещения (статусы, согласование, история), поставки,
//  авто-рекомендации перемещений между магазинами.
// =====================================================================
const DST={draft:['Черновик',''],pending:['На согласовании','pend'],to_ship:['Одобрено','blue'],preparing:['Сборка','blue'],sent:['Отгружено','blue'],
  received:['Получено','pend'],completed:['Завершено','ok'],cancelled:['Отменено','']};
const SUP_ST={sent:['В пути','blue'],received:['Принята','ok'],cancelled:['Отменена','']};
const FLOW=['draft','pending','to_ship','preparing','sent','received','completed'];
let DC_TAB='transfer', DC_ST='', DOCS=[];
const canApproveAny=()=>can('docs.approve');
$$('#dcTabs button').forEach(b=>b.onclick=()=>{ DC_TAB=b.dataset.t; DC_ST=''; $$('#dcTabs button').forEach(x=>x.classList.toggle('on',x===b)); loadDocs(); });

loadDocs = async function(){
  STORES=await api('/api/stores');
  $('#dcNewS').classList.toggle('hidden',!can('docs.create')||DC_TAB!=='supply'); $('#dcNewT').classList.toggle('hidden',!can('docs.create')||DC_TAB==='supply');
  $('#dcList').classList.toggle('hidden',DC_TAB==='reco'); $('#dcReco').classList.toggle('hidden',DC_TAB!=='reco');
  if(DC_TAB==='reco') return loadReco();
  DOCS=(await api('/api/docs')).filter(d=>d.type===DC_TAB);
  const M=DC_TAB==='transfer'?DST:SUP_ST, cnt={}; DOCS.forEach(d=>cnt[d.status]=(cnt[d.status]||0)+1);
  const order=DC_TAB==='transfer'?[...FLOW,'cancelled']:['sent','received','cancelled'];
  $('#dcChips').innerHTML=`<button class="${DC_ST?'ghost':'p'}" data-s="">Все <b style="margin-left:4px">${DOCS.length}</b></button>`+order.filter(k=>cnt[k]).map(k=>
    `<button class="${DC_ST===k?'p':'ghost'}" data-s="${k}">${M[k][0]} <b style="margin-left:4px">${cnt[k]}</b></button>`).join('');
  $$('#dcChips [data-s]').forEach(b=>b.onclick=()=>{ DC_ST=b.dataset.s; loadDocs(); });
  const rows=DOCS.filter(d=>!DC_ST||d.status===DC_ST);
  $('#docsBody').innerHTML=rows.map(d=>{ const s=M[d.status]||[d.status,'']; const diff=['received','completed'].includes(d.status)?d.qty_fact-d.qty:0;
    return `<tr style="cursor:pointer" onclick="showDoc('${d.uid}')"><td><b>${esc(d.number)}</b></td><td>${esc(d.type==='supply'?(d.supplier||'Поставщик'):d.from_name)}</td><td>${esc(d.to_name)}</td>
    <td><span class="badge ${s[1]}">${s[0]}</span>${diff?` <span class="${diff<0?'red':'amber'}" title="Расхождение">${diff>0?'+':''}${fq(diff)}</span>`:''}</td>
    <td class="n">${d.lines}</td><td class="n">${fq(d.qty)}</td><td class="n">${['sent','received','completed'].includes(d.status)?fq(d.qty):'—'}</td><td class="n">${['received','completed'].includes(d.status)?fq(d.qty_fact):'—'}</td>
    <td class="n">${fmt(d.sum)}</td><td class="muted">${dt(d.created_at)}<div style="font-size:11px">${esc(d.created_by||'')}</div></td><td><button class="ghost">Открыть</button></td></tr>`; }).join('')||'<tr><td colspan="11" class="muted">Документов нет</td></tr>';
};

showDoc = async function(uid){
  const [d, ev]=await Promise.all([api('/api/docs/'+uid), api(`/api/docs/${uid}/events`)]);
  const tr=d.type==='transfer', M=tr?DST:SUP_ST, s=M[d.status]||[d.status,''];
  const at={}; ev.forEach(e=>{ if(!at[e.status]) at[e.status]=e; });
  const idx=FLOW.indexOf(d.status);
  const step=tr&&d.status!=='cancelled'?`<div class="flow">${FLOW.map((k,i)=>`<div class="fs ${i<idx?'done':i===idx?'cur':''}"><i>${i<idx?'✓':i+1}</i><b>${DST[k][0]}</b><span>${at[k]?dt(at[k].created_at).slice(5):''}</span></div>`).join('')}</div>`:'';
  const editable=tr&&['draft','pending'].includes(d.status)&&can('docs.create');
  const fromAllowed=ME.stores==null||ME.stores.includes(d.from_store_id);
  const appr=can('docs.approve')&&fromAllowed;
  const btn=[];
  if(tr&&d.status==='draft'&&can('docs.create')) btn.push('<button data-a="submit">Отправить на согласование</button>');
  if(tr&&['draft','pending'].includes(d.status)&&appr) btn.push('<button class="p" data-a="approve">Одобрить — магазин начнёт сборку</button>');
  if(tr&&d.status==='pending'&&appr) btn.push('<button data-a="reject" data-c="1">Вернуть на доработку</button>');
  if(tr&&d.status==='received'&&appr) btn.push('<button class="p" data-a="complete" data-c="1">Завершить</button>');
  if((tr&&['draft','pending','to_ship','preparing'].includes(d.status)||(!tr&&d.status==='sent'))&&can('docs.create')) btn.push('<button class="r" data-a="cancel" style="margin-right:auto">Отменить</button>');
  let lines=d.lines.map(l=>({product_uid:l.product_uid,name:l.name,barcode:l.barcode,qty:l.qty,cost:l.cost}));
  const m=modal(`<div class="row"><h3 class="grow" style="margin:0">${tr?'Перемещение':'Поставка'} ${esc(d.number)} <span class="badge ${s[1]}">${s[0]}</span></h3><button onclick="closeM()">✕</button></div>
    <div class="muted" style="margin:4px 0 8px">${esc(tr?d.from_name:(d.supplier||'Поставщик'))} → <b style="color:var(--text)">${esc(d.to_name)}</b> · создал ${esc(d.created_by||'')} ${dt(d.created_at)}${d.approved_by?' · одобрил '+esc(d.approved_by):''}${d.note?' · '+esc(d.note):''}</div>
    ${step}
    <div style="max-height:46vh;overflow:auto;margin-top:10px"><table><thead><tr><th>Товар</th><th>Штрихкод</th><th class="n">${tr?'План / отгружено':'Отправлено'}</th><th class="n">Принято</th><th class="n">Разница</th><th class="n">Себест.</th>${editable?'<th></th>':''}</tr></thead><tbody id="dlB"></tbody></table></div>
    ${editable?'<div class="row" style="margin-top:8px"><input id="dlQ" class="grow" placeholder="Добавить товар: штрихкод или название + Enter"><button id="dlSave">Сохранить строки</button></div>':''}
    <details style="margin-top:10px" ${['received'].includes(d.status)?'open':''}><summary><b>История документа</b> <span class="muted">(${ev.length})</span></summary>
      <table style="margin-top:6px"><tbody>${ev.map(e=>`<tr><td class="muted" style="white-space:nowrap">${dt(e.created_at)}</td><td><span class="badge ${(M[e.status]||['',''])[1]}">${(M[e.status]||[e.status])[0]}</span></td><td>${esc(e.user_name||'')}</td><td>${esc(e.comment||'')}</td></tr>`).join('')||'<tr><td class="muted">Нет записей</td></tr>'}</tbody></table></details>
    <div class="actions">${btn.join('')}<button onclick="closeM()">Закрыть</button></div>`,'wide');
  const draw=()=>{ m.querySelector('#dlB').innerHTML=lines.map((l,i)=>{ const o=d.lines.find(x=>x.product_uid===l.product_uid)||{}; const df=o.qty_fact==null?null:o.qty_fact-o.qty;
      return `<tr style="${df?'background:'+(df<0?'#fef3f2':'#fffaeb'):''}"><td>${esc(l.name||l.product_uid)}</td><td class="muted">${esc(l.barcode||'')}</td>
      <td class="n">${editable?`<input type="number" step="any" value="${l.qty}" data-q="${i}" style="width:80px">`:fq(l.qty)}</td><td class="n">${o.qty_fact==null?'—':fq(o.qty_fact)}</td>
      <td class="n ${df<0?'red':df>0?'amber':''}">${df?(df>0?'+':'')+fq(df):''}</td><td class="n">${l.cost==null?'—':fmt(l.cost)}</td>${editable?`<td><button class="ghost" data-x="${i}">✕</button></td>`:''}</tr>`; }).join('');
    m.querySelectorAll('[data-q]').forEach(x=>x.onchange=()=>{ lines[+x.dataset.q].qty=+x.value; });
    m.querySelectorAll('[data-x]').forEach(x=>x.onclick=()=>{ lines.splice(+x.dataset.x,1); draw(); }); };
  draw();
  m.querySelector('#dlQ')?.addEventListener('keydown',async e=>{ if(e.key!=='Enter') return; const v=e.target.value.trim(); if(!v) return; e.target.value='';
    let p=null; try{ p=await api('/api/products/barcode/'+encodeURIComponent(v)); }catch(er){ const l=await api('/api/products?q='+encodeURIComponent(v)); p=l[0]; }
    if(!p) return toast('Не найдено: '+v,'err'); const ex=lines.find(x=>x.product_uid===p.uid); if(ex) ex.qty+=1; else lines.unshift({product_uid:p.uid,name:p.name,barcode:p.barcode,qty:1,cost:p.cost_price}); draw(); });
  m.querySelector('#dlSave')?.addEventListener('click',async()=>{ try{ await api(`/api/docs/${uid}/lines`,{method:'POST',body:{lines}}); toast('Строки сохранены','ok'); m.remove(); showDoc(uid); loadDocs(); }catch(e){ toast(e.message,'err'); } });
  m.querySelectorAll('[data-a]').forEach(b=>b.onclick=async()=>{
    let comment='';
    if(b.dataset.c){ comment=prompt(b.dataset.a==='reject'?'Причина (увидит автор):':'Комментарий: как решены расхождения (если были)')??null; if(comment===null) return; }
    if(b.dataset.a==='cancel'&&!confirm('Отменить документ? Касса магазина получит отмену.')) return;
    try{ if(editable&&['submit','approve'].includes(b.dataset.a)) await api(`/api/docs/${uid}/lines`,{method:'POST',body:{lines}});
      const r=await api(`/api/docs/${uid}/action`,{method:'POST',body:{action:b.dataset.a,comment}}); toast(`${r.number}: ${(M[r.status]||[r.status])[0]}`,'ok'); m.remove(); loadDocs(); showDoc(uid); }
    catch(e){ toast(e.message,'err'); } });
};

// ---------------------------------------------------------------- рекомендации
let RECO=null;
async function loadReco(){
  const box=$('#dcReco');
  if(!box.dataset.init){ box.dataset.init=1;
    box.innerHTML=`<div class="card" style="margin-bottom:12px"><div class="row wrap" style="gap:10px;align-items:flex-end">
      <div><label>Продажи за</label><div class="seg" id="rcW">${[7,14,30,60].map(d=>`<button data-w="${d}" class="${d===30?'on':''}">${d} дн.</button>`).join('')}</div></div>
      <div style="width:150px"><label>Держать у получателя, дней</label><input id="rcT" type="number" value="14"></div>
      <div style="width:150px"><label>Оставить у отправителя, дней</label><input id="rcK" type="number" value="7"></div>
      <div style="width:120px"><label>Мин. количество</label><input id="rcM" type="number" value="1"></div>
      <div style="width:170px"><label>Откуда</label><select id="rcF"><option value="">Любой магазин</option>${STORES.filter(s=>s.active).map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select></div>
      <div style="width:170px"><label>Куда</label><select id="rcTo"><option value="">Любой магазин</option>${STORES.filter(s=>s.active).map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select></div>
      <button class="p" onclick="runReco()">Рассчитать</button></div>
      <p class="muted" style="margin:10px 0 0;font-size:12.5px">Система сравнивает остатки и скорость продаж каждого товара во всех магазинах. Получатель — где товар продаётся и запаса меньше, чем на «держать» дней (сначала — где товара нет совсем).
      Отправитель — где запас больше «оставить» дней или товар не продаётся. Товар, уже включённый в перемещения, учитывается — двойных рекомендаций не будет.</p></div>
      <div id="rcRes"></div>`;
    $$('#rcW button').forEach(b=>b.onclick=()=>{ $$('#rcW button').forEach(x=>x.classList.toggle('on',x===b)); runReco(); });
  }
  runReco();
}
async function runReco(){
  const w=$('#rcW button.on').dataset.w;
  RECO=await api(`/api/transfers/recommend?window=${w}&target=${$('#rcT').value}&keep=${$('#rcK').value}&min=${$('#rcM').value}&from=${$('#rcF').value}&to=${$('#rcTo').value}`);
  const P={high:['Нет в наличии','err'],mid:['Скоро закончится','pend'],low:['Пополнить','blue']};
  const k='d'+w;
  $('#rcRes').innerHTML=`<div class="card"><div class="row wrap" style="margin-bottom:8px"><b class="grow">Рекомендации: ${RECO.total}</b>
      <label class="row" style="margin:0;color:var(--text)"><input type="checkbox" id="rcAll" style="width:auto" checked> выбрать все</label>
      <button id="rcDraft">Создать черновики</button><button id="rcSubmit">На согласование</button>${can('docs.approve')?'<button class="p" id="rcApprove">Создать и одобрить</button>':''}</div>
    <div class="tbl"><table><thead><tr><th></th><th>Товар</th><th>Откуда</th><th class="n">Остаток / продажи</th><th>Куда</th><th class="n">Остаток / продажи</th><th class="n">Переместить</th>${RECO.finance?'<th class="n">Сумма</th>':''}<th>Приоритет</th></tr></thead><tbody>
    ${RECO.items.map((r,i)=>`<tr title="${esc(r.reason)}"><td><input type="checkbox" class="rcC" data-i="${i}" checked style="width:auto"></td><td><b>${esc(r.name)}</b><div class="muted" style="font-size:11.5px">${esc(r.category||'')} ${esc(r.barcode||'')}</div></td>
      <td>${esc(r.from)}</td><td class="n">${fq(r.from_stock)} / ${fq(r.from_sold[k])}<div class="muted" style="font-size:11px">${r.from_cover==null?'без продаж':'запас '+r.from_cover+' дн.'}</div></td>
      <td>${esc(r.to)}</td><td class="n"><b class="${r.to_stock<=0?'red':''}">${fq(r.to_stock)}</b> / ${fq(r.to_sold[k])}<div class="muted" style="font-size:11px">${r.to_stock<=0?'нет в наличии':'хватит на '+r.to_cover+' дн.'}</div></td>
      <td class="n"><input type="number" step="any" value="${r.qty}" data-rq="${i}" style="width:80px"> <span class="muted">${esc(r.unit||'')}</span></td>${RECO.finance?`<td class="n">${fmt(r.value)}</td>`:''}
      <td><span class="badge ${P[r.priority][1]}">${P[r.priority][0]}</span></td></tr>`).join('')||'<tr><td colspan="9" class="muted">Перемещать нечего — остатки распределены по продажам ✓</td></tr>'}
    </tbody></table></div><div class="muted" style="font-size:12px;margin-top:6px">Наведите на строку — причина рекомендации. Строки группируются в документы по паре «откуда → куда».</div></div>`;
  $('#rcAll').onchange=e=>$$('.rcC').forEach(c=>c.checked=e.target.checked);
  const go=async mode=>{ const items=$$('.rcC:checked').map(c=>{ const r=RECO.items[+c.dataset.i]; return {product_uid:r.product_uid,from_id:r.from_id,to_id:r.to_id,qty:+$(`[data-rq="${c.dataset.i}"]`).value}; });
    if(!items.length) return toast('Отметьте строки','err');
    try{ const r=await api('/api/transfers/create',{method:'POST',body:{items,mode}}); toast(`Создано перемещений: ${r.created}`,'ok'); DC_TAB='transfer'; $$('#dcTabs button').forEach(x=>x.classList.toggle('on',x.dataset.t==='transfer')); loadDocs(); }
    catch(e){ toast(e.message,'err'); } };
  $('#rcDraft').onclick=()=>go('draft'); $('#rcSubmit').onclick=()=>go('submit'); $('#rcApprove')?.addEventListener('click',()=>go('approve'));
}
document.head.insertAdjacentHTML('beforeend',`<style>
  .flow{display:flex;gap:0;margin:6px 0 4px;overflow-x:auto}
  .fs{flex:1;min-width:96px;display:flex;flex-direction:column;align-items:center;text-align:center;position:relative;font-size:12px;color:var(--muted)}
  .fs:not(:last-child)::after{content:'';position:absolute;top:13px;left:calc(50% + 15px);right:calc(-50% + 15px);height:2px;background:var(--line)}
  .fs.done:not(:last-child)::after{background:var(--brand)}
  .fs i{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font-style:normal;font-weight:700;background:#f1f3f6;margin-bottom:4px}
  .fs.done i{background:var(--brand);color:#fff} .fs.cur i{background:#fff;border:2px solid var(--brand);color:var(--brand)}
  .fs.cur b,.fs.done b{color:var(--text)} .fs b{font-weight:600} .fs span{font-size:11px}
</style>`);
