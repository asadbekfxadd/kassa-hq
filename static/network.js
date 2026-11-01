// =====================================================================
//  Сеть магазинов: статус связи, настройки подключения, поступления
//  по накладным с приёмкой сканером, отгрузка в другой магазин.
// =====================================================================
let NET={connected:false, stores:[]};

async function netStatus(){
  try{
    NET=await api('/api/network/status');
    const el=$('#netSt');
    if(!NET.connected){ el.classList.add('hidden'); }
    else{
      el.classList.remove('hidden');
      const fresh = NET.last_ok && (Date.now()-new Date(NET.last_ok.replace(' ','T')))<3*60*1000;
      el.innerHTML = fresh&&!NET.error ? `🌐 <span style="color:#a7f3d0">${esc(NET.store||'Сеть')}</span>` : `🌐 <span style="color:#fecaca">нет связи</span>`;
      el.title = (NET.error?('Ошибка: '+NET.error+'\n'):'') + 'Последний обмен: '+(NET.last_ok||'никогда')+(NET.unsent?`\nНе отправлено чеков: ${NET.unsent}`:'')+'\nНажмите — синхронизировать сейчас';
    }
    const b=$('#navRecv'); if(b) b.innerHTML = 'Поступления' + (NET.expected_docs?` <span class="badge" style="background:#fde68a;color:#92400e">${NET.expected_docs}</span>`:'');
  }catch(e){}
}
setInterval(()=>{ if(ME) netStatus(); }, 30000);

async function netSyncNow(){
  try{ const r=await api('/api/network/sync',{method:'POST'}); const x=r.result||{};
    toast(x.skipped?'Касса не подключена к Центру':`Синхронизировано: чеков ${x.sales}, товаров ${x.products}${x.new_docs?`, новых накладных ${x.new_docs}`:''}`, x.skipped?'':'ok');
    await netStatus(); if($('#v-receive').classList.contains('on')&&$('#rcWork').classList.contains('hidden')) loadReceive();
  }catch(e){ toast(e.message,'err'); netStatus(); }
}
async function netConnect(){
  const url=$('#netUrl').value.trim(), token=$('#netTok').value.trim();
  if(!url||!token) return toast('Укажите адрес Центра и токен магазина','err');
  try{ const r=await api('/api/network/connect',{method:'POST',body:{url,token}});
    toast(`Подключено к Центру: «${r.store}». Загружено товаров: ${r.result.products}`,'ok'); netStatus(); loadNetInfo();
  }catch(e){ toast(e.message,'err'); loadNetInfo(); }
}
async function netDisconnect(){ if(!confirm('Отключить кассу от Центра?')) return; await api('/api/network/connect',{method:'POST',body:{url:'',token:''}}); toast('Отключено'); netStatus(); loadNetInfo(); }
async function loadNetInfo(){
  const s=await api('/api/settings'); $('#netUrl').value=s.hq_url||''; $('#netTok').value=s.hq_token||'';
  await netStatus();
  $('#netInfo').innerHTML = NET.connected ? `Магазин в сети: <b>${esc(NET.store||'—')}</b> · последний обмен ${esc(NET.last_ok||'никогда')}${NET.error?` · <span class="red">${esc(NET.error)}</span>`:''}${NET.unsent?` · в очереди чеков: ${NET.unsent}`:''}` : 'Касса работает автономно (не подключена к Центру).';
}

// ---------------------------------------------------------------- receive list
const DS={expected:['Ожидается','pend'],receiving:['Идёт приёмка','pend'],received:['Принята','ok'],to_ship:['К отгрузке','pend'],shipped:['Отгружена','ok'],cancelled:['Отменена','']};
async function loadReceive(){
  $('#rcWork').classList.add('hidden'); $('#rcList').classList.remove('hidden');
  $('#rcOutBtn').classList.toggle('hidden', !NET.connected);
  const rows=await api('/api/docs');
  const open=rows.filter(d=>['expected','receiving','to_ship'].includes(d.status)), done=rows.filter(d=>!open.includes(d));
  $('#rcCards').innerHTML = open.map(d=>{ const out=d.type==='out_transfer'; const pct=d.exp?Math.min(100,d.fact/d.exp*100):0;
    return `<div class="card" style="border-left:4px solid ${out?'#2563eb':'#d97706'};cursor:pointer" onclick="openDoc(${d.id})">
      <div class="row"><b class="grow">${out?'⬆ Отгрузка':'⬇ '+esc(d.type_name)}</b><span class="badge ${DS[d.status][1]}">${DS[d.status][0]}</span></div>
      <div style="font-size:16px;margin-top:4px">${esc(d.number||'')} · ${out?'в ':'от '}<b>${esc(d.partner||'')}</b></div>
      <div class="muted">${d.lines} поз. · ${fq(d.exp)} шт${d.sum?` · ${fmt(d.sum)}`:''} · ${d.created_at.slice(0,16)}</div>
      ${d.status!=='expected'?`<div style="height:6px;background:#e5e7eb;border-radius:3px;margin-top:8px;overflow:hidden"><div style="height:100%;width:${pct}%;background:var(--brand)"></div></div><div class="muted" style="font-size:12px">отсканировано ${fq(d.fact)} из ${fq(d.exp)}</div>`:''}
      <button class="p" style="width:100%;margin-top:10px">${out?'Собрать и отгрузить':(d.status==='receiving'?'Продолжить приёмку':'Начать приёмку')}</button></div>`; }).join('')
    || `<div class="card muted">Нет ожидаемых поступлений.${NET.connected?' Накладные из Центра появятся здесь автоматически.':' Загрузите накладную поставщика из Excel, чтобы принять её сканером.'}</div>`;
  $('#rcHist').innerHTML = done.map(d=>{ const df=d.fact-d.exp; return `<tr><td>${esc(d.number||d.id)}</td><td>${esc(d.type_name)}</td><td>${esc(d.partner||'')}</td>
    <td><span class="badge ${DS[d.status]?.[1]||''}">${DS[d.status]?.[0]||d.status}</span></td><td class="n">${fq(d.exp)}</td>
    <td class="n ${df<0?'red':df>0?'amber':''}">${fq(d.fact)}${df&&d.type!=='out_transfer'?` (${df>0?'+':''}${fq(df)})`:''}</td><td>${(d.done_at||d.created_at).slice(0,16)}</td><td>${esc(d.user||'')}</td>
    <td><button class="ghost" onclick="openDoc(${d.id})">Открыть</button></td></tr>`; }).join('') || '<tr><td colspan="9" class="muted">Пока пусто</td></tr>';
  netStatus();
}
$('#rcImp').onchange=async e=>{
  const f=e.target.files[0]; if(!f) return;
  const sup=prompt('Поставщик', f.name.replace(/\.(xlsx|xlsm|csv)$/i,'')); if(sup===null){ e.target.value=''; return; }
  const fd=new FormData(); fd.append('file',f); fd.append('supplier',sup);
  try{ const r=await api('/api/docs/import',{method:'POST',body:fd});
    if(r.missing.length) modal(`<h3>Не найдено в каталоге: ${r.missing.length}</h3><div class="muted">Эти позиции не попали в накладную. Заведите товары${NET.connected?' в Центре':''} и загрузите файл снова, либо отсканируйте их при приёмке как «лишние».</div>
      <div class="tbl-wrap" style="max-height:300px"><table><tbody>${r.missing.map(m=>`<tr><td>${esc(m.name)}</td><td class="muted">${esc(m.barcode)}</td><td class="n">${fq(m.qty)}</td></tr>`).join('')}</tbody></table></div>
      <div class="actions"><button class="p" onclick="closeModal()">Понятно</button></div>`);
    toast(`Накладная ${r.doc.number}: ${r.doc.lines.length} поз. — сверьте сканером`,'ok'); openDoc(r.doc.id);
  }catch(er){ toast(er.message,'err'); }
  e.target.value='';
};
function newShipment(){
  if(!NET.stores.length) return toast('Нет других магазинов в сети','err');
  const m=modal(`<h3>Отгрузка в другой магазин</h3><label>Куда</label><select id="shTo">${NET.stores.map(s=>`<option value="${s.uid}">${esc(s.name)}</option>`).join('')}</select>
    <label>Комментарий</label><input id="shN"><p class="muted">Далее сканируйте товары, которые кладёте в коробку. Остаток спишется при проведении, а магазин-получатель увидит накладную у себя.</p>
    <div class="actions"><button onclick="closeModal()">Отмена</button><button class="p" id="shGo">Создать и сканировать</button></div>`);
  m.querySelector('#shGo').onclick=async()=>{ try{ const d=await api('/api/docs/out',{method:'POST',body:{to_store_uid:m.querySelector('#shTo').value,note:m.querySelector('#shN').value}}); closeModal(m); openDoc(d.id); }catch(e){ toast(e.message,'err'); } };
}

// ---------------------------------------------------------------- receiving work screen
let DOC=null, LAST_LINE=null, RC_QTY=null;
async function openDoc(id){
  show('receive');
  DOC=await api('/api/docs/'+id); LAST_LINE=null; RC_QTY=null;
  $('#rcList').classList.add('hidden'); $('#rcWork').classList.remove('hidden');
  renderDoc();
}
function lineState(l){ if(l.qty_expected===0) return l.qty_fact?'extra':'none'; if(l.qty_fact===0) return 'none'; if(l.qty_fact<l.qty_expected) return 'partial'; if(l.qty_fact===l.qty_expected) return 'done'; return 'over'; }
const LS={none:['','—'],partial:['#fffbeb','не хватает'],done:['#f0fdf4','✓'],over:['#fef2f2','больше'],extra:['#fef2f2','нет в накладной']};
function renderDoc(){
  const d=DOC, out=d.type==='out_transfer', open=['expected','receiving','to_ship'].includes(d.status);
  const lines=[...d.lines].sort((a,b)=>{ const o={partial:0,none:1,over:2,extra:2,done:3}; return (o[lineState(a)]-o[lineState(b)]) || a.id-b.id; });
  const exp=d.exp, fact=d.fact, pct=exp?Math.min(100,fact/exp*100):(fact?100:0);
  const short=d.lines.reduce((a,l)=>a+Math.max(0,l.qty_expected-l.qty_fact),0), extra=d.lines.reduce((a,l)=>a+Math.max(0,l.qty_fact-l.qty_expected),0);
  const ll=LAST_LINE && d.lines.find(l=>l.id===LAST_LINE.id);
  $('#rcWork').innerHTML=`
  <div class="row wrap" style="margin-bottom:10px">
    <button onclick="loadReceive()">← Назад</button>
    <h2 class="grow" style="margin:0">${out?'⬆ Отгрузка':'⬇ '+esc(d.type_name)} ${esc(d.number||'')} · ${out?'в':'от'} ${esc(d.partner||'')}</h2>
    <span class="badge ${DS[d.status][1]}">${DS[d.status][0]}</span>
  </div>
  <div style="display:grid;grid-template-columns:1fr 360px;gap:12px">
    <div style="display:flex;flex-direction:column;gap:10px;min-width:0">
      ${open?`<div class="scan"><input id="rcScan" autocomplete="off" style="font-size:20px;padding:14px" placeholder="Сканируйте товар… (${out?'каждый скан +1 в коробку':'каждый скан +1 к факту'}; «10*» — сразу 10)"></div>`:''}
      ${ll?`<div class="card" id="rcLast" style="background:${LS[lineState(ll)][0]||'#fff'};border:2px solid ${({done:'#16a34a',partial:'#d97706',over:'#dc2626',extra:'#dc2626'})[lineState(ll)]||'#e5e7eb'}">
        <div class="row"><div class="grow"><div style="font-size:20px;font-weight:700">${esc(ll.name)}</div><div class="muted">${esc(ll.barcode||'')}</div></div>
        <div style="text-align:right"><div style="font-size:40px;font-weight:800;line-height:1">${fq(ll.qty_fact)}${!out&&ll.qty_expected?`<span class="muted" style="font-size:22px"> / ${fq(ll.qty_expected)}</span>`:''}</div><div><b>${LS[lineState(ll)][1]}</b></div></div></div></div>`:''}
      <div class="card" style="padding:0;overflow:auto;max-height:calc(100vh - ${ll?380:250}px)">
        <table><thead><tr><th>Товар</th><th>Штрихкод</th>${out&&!d.exp?'':'<th class="n">Ожидается</th>'}<th class="n">${out?'В коробке':'Принято'}</th><th class="n">Разница</th><th>${out?'':'Годен до'}</th></tr></thead><tbody>
        ${lines.map(l=>{ const st=lineState(l), df=l.qty_fact-l.qty_expected; return `<tr style="background:${LS[st][0]}">
          <td>${esc(l.name)}${st==='extra'?' <span class="badge err">лишний</span>':''}</td><td class="muted">${esc(l.barcode||'')}</td>
          ${out&&!d.exp?'':`<td class="n">${fq(l.qty_expected)}</td>`}
          <td class="n">${open?`<input type="number" step="any" min="0" value="${fq(l.qty_fact)}" style="width:80px;text-align:right" onchange="setFact(${l.id},this.value)">`:`<b>${fq(l.qty_fact)}</b>`}</td>
          <td class="n ${df<0?'amber':df>0?'red':'green'}">${out&&!l.qty_expected?'':(df===0&&l.qty_fact?'✓':(df>0?'+':'')+fq(df))}</td>
          <td style="white-space:nowrap">${!out&&open?`<input type="date" value="${l.expiry||''}" title="Годен до" style="width:132px;padding:5px" onchange="setExpiry(${l.id},this.value)">`:(l.expiry?`<span class="muted">до ${l.expiry}</span>`:'')}
          ${open?`<button class="ghost" style="padding:4px 8px" onclick="setFact(${l.id},0)" title="Обнулить">↺</button>`:''}</td></tr>`; }).join('')||'<tr><td colspan="6" class="muted" style="padding:30px;text-align:center">Сканируйте товары</td></tr>'}
        </tbody></table>
      </div>
    </div>
    <div style="display:flex;flex-direction:column;gap:10px">
      <div class="card">
        <div class="muted">${out?'Собрано':'Принято'}</div>
        <div style="font-size:44px;font-weight:800;line-height:1.1">${fq(fact)}${exp?`<span class="muted" style="font-size:24px"> / ${fq(exp)}</span>`:''}</div>
        ${exp?`<div style="height:10px;background:#e5e7eb;border-radius:5px;margin-top:8px;overflow:hidden"><div style="height:100%;width:${pct}%;background:${pct>=100?'#16a34a':'var(--brand)'}"></div></div>`:''}
        <div style="margin-top:10px;font-size:14px">
          ${exp?`<div class="row"><span class="grow">Позиций сошлось</span><b class="green">${d.lines.filter(l=>lineState(l)==='done').length} из ${d.lines.filter(l=>l.qty_expected>0).length}</b></div>`:''}
          ${short?`<div class="row"><span class="grow">Недостача</span><b class="amber">${fq(short)}</b></div>`:''}
          ${extra?`<div class="row"><span class="grow">Излишек / лишнее</span><b class="red">${fq(extra)}</b></div>`:''}
        </div>
      </div>
      ${open?`
        <button class="g" style="padding:16px;font-size:16px" onclick="completeDoc()">${out?'✔ Отгрузить':'✔ Провести приёмку'}</button>
        ${!out&&ME.role==='admin'?'<button onclick="fillDoc()">Принять всё как в накладной (без скана)</button>':''}
        <button onclick="resetDoc()">Начать заново</button>
        ${['out_transfer','local_supply'].includes(d.type)?'<button class="ghost red" onclick="deleteDoc()">Удалить документ</button>':''}
        <div class="card keys" style="font-size:12px;color:var(--muted)">Сканер добавляет по 1 шт. <kbd>10*</kbd> перед сканом — сразу 10 шт. Количество можно поправить в таблице. Неизвестный штрихкод — короткий низкий сигнал.</div>`
      :`<div class="card">Проведено ${esc(d.done_at||'')} · ${esc(d.user||'')}${d.note?`<div class="muted">${esc(d.note)}</div>`:''}</div>
        ${!out?`<button onclick="labelsDialog(DOC.lines.filter(l=>l.product_id&&l.qty_fact).map(l=>l.product_id), Object.fromEntries(DOC.lines.map(l=>[l.product_id,l.qty_fact])))">🏷 Этикетки на принятый товар</button>`:''}`}
    </div>
  </div>`;
  const sc=$('#rcScan');
  if(sc){ sc.focus(); sc.onkeydown=e=>{ if(e.key==='Enter'){ e.preventDefault(); rcScan(sc.value.trim()); sc.value=''; } };
    sc.oninput=()=>{ const m=sc.value.match(/^(\d+(?:[.,]\d+)?)\*$/); if(m){ RC_QTY=parseFloat(m[1].replace(',','.')); sc.value=''; sc.placeholder=`Количество ${RC_QTY} — сканируйте товар`; } }; }
}
function rcBeep(kind){ try{ ACtx=ACtx||new AudioContext(); const o=ACtx.createOscillator(), g=ACtx.createGain();
  o.frequency.value=({ok:1250,done:1700,warn:600,bad:200})[kind]; g.gain.value=.06; o.connect(g); g.connect(ACtx.destination); o.start(); o.stop(ACtx.currentTime+(kind==='bad'?.4:kind==='done'?.18:.07)); }catch(e){} }
async function rcScan(code){
  if(!code) return;
  try{
    const r=await api(`/api/docs/${DOC.id}/scan`,{method:'POST',body:{code, qty:RC_QTY||undefined}});
    RC_QTY=null; DOC=r.doc; LAST_LINE=r.line;
    rcBeep(r.state==='done'?'done':r.state==='partial'?'ok':DOC.type==='out_transfer'&&r.state==='extra'?'ok':'warn');
    if(r.extra && DOC.type!=='out_transfer') toast(`«${r.line.name}» нет в накладной — отмечен как лишний`,'err');
    renderDoc();
  }catch(e){
    rcBeep('bad'); RC_QTY=null;
    if(e.message.includes('не найден') && ME.role==='admin' && !NET.connected && confirm(e.message+'\n\nСоздать товар с этим штрихкодом?')) editProduct({barcode:code}, ()=>rcScan(code));
    else { toast(e.message,'err'); $('#rcScan')?.focus(); }
  }
}
async function setFact(lid,v){ const l=DOC.lines.find(x=>x.id===lid); try{ const r=await api(`/api/docs/${DOC.id}/scan`,{method:'POST',body:{line_id:lid,set:v,code:l.barcode||''}}); DOC=r.doc; LAST_LINE=r.line; renderDoc(); }catch(e){ toast(e.message,'err'); } }
async function fillDoc(){ if(!confirm('Отметить всё как принятое без сканирования?')) return; DOC=await api(`/api/docs/${DOC.id}/fill`,{method:'POST'}); renderDoc(); }
async function resetDoc(){ if(!confirm('Сбросить всё отсканированное?')) return; DOC=await api(`/api/docs/${DOC.id}/reset`,{method:'POST'}); LAST_LINE=null; renderDoc(); }
async function deleteDoc(){ if(!confirm('Удалить документ?')) return; try{ await api('/api/docs/'+DOC.id,{method:'DELETE'}); loadReceive(); }catch(e){ toast(e.message,'err'); } }
async function completeDoc(){
  const d=DOC, out=d.type==='out_transfer';
  const short=d.lines.filter(l=>l.qty_fact<l.qty_expected), extra=d.lines.filter(l=>l.qty_fact>l.qty_expected);
  const list=(arr,sign)=>arr.map(l=>`<tr><td>${esc(l.name)}</td><td class="n">${fq(l.qty_expected)}</td><td class="n">${fq(l.qty_fact)}</td><td class="n ${sign<0?'amber':'red'}">${sign<0?'':'+'}${fq(l.qty_fact-l.qty_expected)}</td></tr>`).join('');
  const hasDiff = !out && (short.length || extra.length);
  const m=modal(`<h3>${out?'Отгрузить':'Провести приёмку'} ${esc(d.number||'')}?</h3>
    ${hasDiff?`<div class="card" style="background:#fffbeb"><b>Акт расхождений</b>
      <table style="margin-top:6px"><thead><tr><th>Товар</th><th class="n">По накладной</th><th class="n">Факт</th><th class="n">Разница</th></tr></thead><tbody>${list(short,-1)}${list(extra,1)}</tbody></table></div>
      <label>Комментарий к расхождению (уйдёт в Центр)</label><input id="cmC" placeholder="напр. 2 шт брак, коробка вскрыта">`
    :`<p>${out?`Будет списано со склада и отправлено в «${esc(d.partner)}»: <b>${fq(d.fact)}</b> шт.`:`Всё сошлось ✓ — на склад поступит <b>${fq(d.fact)}</b> шт.`}</p>`}
    <div class="actions"><button onclick="closeModal()">Вернуться</button><button class="g" id="cmGo">${out?'Отгрузить':'Провести'}</button></div>`, hasDiff?'wide':'');
  m.querySelector('#cmGo').onclick=async()=>{ try{
    const r=await api(`/api/docs/${d.id}/complete`,{method:'POST',body:{comment:m.querySelector('#cmC')?.value||''}});
    closeModal(m); DOC=r.doc; renderDoc();
    toast(out?'Отгрузка проведена — получатель увидит накладную':(r.short||r.extra?'Приёмка проведена с расхождениями — Центр уведомлён':'Приёмка проведена ✓'), r.short?'err':'ok');
    netStatus();
    if(!out && confirm('Напечатать этикетки на принятый товар?')) labelsDialog(DOC.lines.filter(l=>l.product_id&&l.qty_fact).map(l=>l.product_id), Object.fromEntries(DOC.lines.map(l=>[l.product_id,l.qty_fact])));
  }catch(e){ toast(e.message,'err'); } };
}
document.addEventListener('click',e=>{ if($('#v-receive').classList.contains('on') && !$('#rcWork').classList.contains('hidden') && !$('#modalRoot .modal') && !e.target.closest('input,button,select,a')) $('#rcScan')?.focus(); });

// ---------------------------------------------------------------- hooks
const _show2 = show;
show = function(v){
  _show2(v);
  if(v==='receive') loadReceive();
  else if(v==='settings') loadNetInfo();
};
const _boot = boot;
boot = async function(){ await _boot(); netStatus(); };
Object.assign(MV,{receive:'Приёмка',transfer_out:'Отгрузка в магазин'});
// товары из Центра нельзя менять на кассе (кроме мин. остатка) — подсказываем
const _editProduct = editProduct;
editProduct = async function(arg, onSaved){
  await _editProduct(arg, onSaved);
  const p = typeof arg==='number' ? PRODUCTS_CACHE.find(x=>x.id===arg) : null;
  if(NET.connected && p && p.hq){ const box=$('#modalRoot .modal:last-child .box');
    box?.insertAdjacentHTML('afterbegin','<div class="card" style="background:#eff6ff;margin-bottom:8px">🌐 Товар из каталога Центра: название, цены и коды меняются в Центре. Здесь — только мин. остаток.</div>'); }
};


