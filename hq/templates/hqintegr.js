// =====================================================================
//  Центр сети (этап 8): AI-помощник и Интеграции.
// =====================================================================
Object.assign(NAV_PERM,{ai:'ai',integr:'integrations'});
Object.assign(TITLES,{ai:'AI-помощник',integr:'Интеграции'});
let AI_INFO=null, AI_LOG=[], INT=null;
const ST_B={error:['err','Ошибка связи'],active:['ok','Работает'],off:['','Выключена'],inactive:['pend','Нужен ключ'],prepared:['','Ждёт документацию'],builtin:['blue','Встроено']};
const JOB_B={done:['ok','Отправлено'],queued:['blue','В очереди'],retry:['pend','Повтор'],dead:['err','Не доставлено'],running:['blue','Отправляется']};
const INT_ICON={telegram:'✈️',sheets:'📗','1c':'🟡',excel:'📊',payments:'💳',ofd:'🧾',didox:'📑',tasnif:'🏷️',uzum:'🛍️',claude:'✨'};
const VIEW_T={dash:'Обзор сети',stock:'Остатки',docs:'Поставки и перемещения',stores:'Магазины',fin:'Финансы',sales:'Аналитика'};

document.head.insertAdjacentHTML('beforeend',`<style>
  .chat{max-width:980px;margin:0 auto} .msg{margin-bottom:14px} .msg.q{text-align:right} .msg.q span{display:inline-block;background:var(--brand);color:#fff;padding:9px 14px;border-radius:14px 14px 2px 14px;max-width:80%;text-align:left}
  .msg.a .card{border-radius:14px 14px 14px 2px} .msg.a .txt{white-space:pre-wrap;line-height:1.55} .msg.a .src{font-size:11.5px;color:var(--muted);margin-top:8px}
  .sugg{display:flex;gap:6px;margin:0 0 6px;overflow-x:auto;padding-bottom:2px} .sugg button{white-space:nowrap;flex:none} .sugg button{border-radius:99px;font-size:13px;padding:7px 13px;background:#fff}
  .dock{position:sticky;bottom:0;background:var(--bg,#f6f7f9);padding-top:8px;box-shadow:0 40px 0 var(--bg,#f6f7f9)} .askbar{padding:4px 0 8px;display:flex;gap:8px} .askbar input{margin:0;font-size:15px;padding:12px 14px}
  .tips{margin:8px 0 0;padding-left:18px;font-size:13px;color:var(--text-2)} .tips li{margin:3px 0}
  .ig{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px} .ic{display:flex;flex-direction:column;gap:6px} .ic .hd{display:flex;align-items:center;gap:10px}
  .ic .ico{font-size:22px;width:38px;height:38px;border-radius:10px;background:#f1f3f6;display:flex;align-items:center;justify-content:center} .ic .ds{font-size:13px;color:var(--text-2);flex:1}
  .feed{display:flex;gap:6px;align-items:center;margin:4px 0} .feed code{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11.5px;padding:5px 7px;background:#f6f7f9;border-radius:6px}
</style>`);

(function(){
  $('#nav button[data-v=dash]').insertAdjacentHTML('afterend',`<button data-v="ai" title="AI-помощник"><svg viewBox="0 0 24 24"><path d="M12 3l1.9 4.6L18.5 9l-4.6 1.9L12 15.5l-1.9-4.6L5.5 9l4.6-1.4z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"/></svg><span class="t">AI-помощник</span></button>`);
  $('#nav button[data-v=users]').insertAdjacentHTML('beforebegin',`<button data-v="integr" title="Интеграции"><svg viewBox="0 0 24 24"><path d="M9 2v6M15 2v6M7 8h10v4a5 5 0 0 1-10 0z"/><path d="M12 17v5"/></svg><span class="t">Интеграции</span></button>`);
  ['ai','integr'].forEach(v=>$(`#nav button[data-v=${v}]`).onclick=()=>show(v));
  $('main').insertAdjacentHTML('beforeend',`
    <section class="view" id="v-ai"><div class="chat"><div id="aiLog"></div><div class="dock"><div class="sugg" id="aiSugg"></div>
      <div class="askbar"><input id="aiQ" placeholder="Спросите о продажах, остатках, плане, прибыли…" autocomplete="off"><button class="p" id="aiGo">Спросить</button></div>
      <div class="muted" style="font-size:12px;text-align:center;padding-bottom:6px" id="aiNote"></div></div></div></section>
    <section class="view" id="v-integr"><div class="dtool"><div class="seg" id="inTabs"><button data-t="cards" class="on">Подключения</button><button data-t="log">Журнал обмена</button></div></div><div id="inBody"></div></section>`);
  $$('#inTabs button').forEach(b=>b.onclick=()=>{ $$('#inTabs button').forEach(x=>x.classList.toggle('on',x===b)); b.dataset.t==='log'?intLog():loadInt(); });
  $('#aiGo').onclick=()=>aiAsk($('#aiQ').value); $('#aiQ').onkeydown=e=>{ if(e.key==='Enter') aiAsk($('#aiQ').value); };
})();
const _showI=show;
show=function(v){ if(v==='ai'||v==='integr'){ if(!can(NAV_PERM[v])) return toast('Нет доступа к разделу','err');
    $$('.view').forEach(x=>x.classList.toggle('on',x.id==='v-'+v)); $$('#nav button').forEach(b=>b.classList.toggle('on',b.dataset.v===v)); $('#pageTitle').textContent=TITLES[v];
    return v==='ai'?loadAi():($$('#inTabs button').forEach((x,i)=>x.classList.toggle('on',!i)),loadInt()); }
  _showI(v); };
const _bootI=boot; boot=async function(){ await _bootI(); ['ai','integr'].forEach(v=>$(`#nav button[data-v=${v}]`)?.classList.toggle('hidden',!can(NAV_PERM[v])));
  $$('#nav .grp').forEach(g=>{ let n=g.nextElementSibling, any=false; while(n&&!n.classList.contains('grp')){ if(!n.classList.contains('hidden')) any=true; n=n.nextElementSibling; } g.classList.toggle('hidden',!any); }); };

// ================================================================ AI-помощник
const md=s=>esc(s).replace(/\*\*(.+?)\*\*/g,'<b>$1</b>');
async function loadAi(){
  if(!AI_INFO) AI_INFO=await api('/api/ai/info');
  $('#aiSugg').innerHTML=AI_INFO.suggest.map(s=>`<button>${esc(s)}</button>`).join('');
  $$('#aiSugg button').forEach(b=>b.onclick=()=>aiAsk(b.textContent));
  $('#aiNote').innerHTML=AI_INFO.llm?'Ответы по данным сети · свободные вопросы формулирует Claude по цифрам системы':'Ответы считаются по данным сети. Чтобы задавать любые вопросы своими словами, подключите Claude в «Интеграциях».';
  if(!AI_LOG.length){ AI_LOG.push({a:{title:'Здравствуйте!',text:'Я считаю ответы по реальным данным вашей сети: продажам, остаткам, перемещениям, планам и прибыли — с учётом ваших прав. Выберите вопрос ниже или напишите свой.',source:'rules'}}); }
  drawAi(); setTimeout(()=>$('#aiQ').focus(),50);
}
function drawAi(){
  $('#aiLog').innerHTML=AI_LOG.map((m,i)=>m.q?`<div class="msg q"><span>${esc(m.q)}</span></div>`:m.wait?`<div class="msg a"><div class="card muted">Считаю по данным сети…</div></div>`:
    `<div class="msg a"><div class="card"><b>${esc(m.a.title||'')}</b><div class="txt" style="margin-top:6px">${md(m.a.text||'')}</div>
      ${m.a.table&&m.a.table.rows&&m.a.table.rows.length?`<div style="overflow:auto;margin-top:10px;max-height:380px"><table><thead><tr>${m.a.table.cols.map((c,j)=>`<th class="${j?'n':''}">${esc(c)}</th>`).join('')}</tr></thead><tbody>
        ${m.a.table.rows.map(r=>`<tr>${r.map((v,j)=>`<td class="${j&&String(v).length<16?'n':''}" style="${String(v).length>60?'font-size:12px;color:var(--text-2);min-width:280px':''}">${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:''}
      ${m.a.tips&&m.a.tips.length?`<ul class="tips">${m.a.tips.map(t=>`<li>${esc(t)}</li>`).join('')}</ul>`:''}
      <div class="row src">${m.a.source==='llm'?'✨ Claude по данным системы':'Расчёт по данным системы'}<span class="grow"></span>
        ${m.a.intent&&AI_INFO&&AI_INFO.llm&&m.a.source!=='llm'&&!['unknown','denied','error','free'].includes(m.a.intent)&&m.qtext?`<button class="ghost" onclick="aiAsk(AI_LOG[${i}].qtext,true)">Объяснить подробнее</button>`:''}
        ${m.a.view&&VIEW_T[m.a.view]?`<button class="ghost" onclick="show('${m.a.view}')">Открыть «${VIEW_T[m.a.view]}» →</button>`:''}</div></div></div>`).join('');
  const sc=$('main'); setTimeout(()=>{ sc.scrollTop=sc.scrollHeight; window.scrollTo(0,document.body.scrollHeight); },0);
}
async function aiAsk(q,llm){
  q=(q||'').trim(); if(!q) return; $('#aiQ').value='';
  if(!llm) AI_LOG.push({q}); AI_LOG.push({wait:true}); drawAi();
  try{ const a=await api('/api/ai/ask',{method:'POST',body:{q,llm:!!llm}}); AI_LOG.pop(); AI_LOG.push({a,qtext:q}); }
  catch(e){ AI_LOG.pop(); AI_LOG.push({a:{title:'Ошибка',text:e.message,source:'rules'}}); }
  drawAi();
}

// ================================================================ Интеграции
async function loadInt(){
  const B=$('#inBody'); B.innerHTML='<div class="pad muted">Загрузка…</div>';
  INT=(await api('/api/integrations')).items;
  B.innerHTML=`<div class="card" style="margin-bottom:12px;background:var(--brand-50);border-color:#dbeafe;font-size:13.5px">У каждой интеграции — свой модуль, журнал обмена и повторная отправка при ошибке (через 1 мин, 5 мин, 30 мин, 2 ч, 6 ч).
      Подключения, для которых нет ключей или документации сервиса, подготовлены и помечены — данные через них не отправляются, пока подключение не проверено.</div>
    <div class="ig">${INT.map(x=>{ const b=ST_B[x.state];
      return `<div class="card ic"><div class="hd"><div class="ico">${INT_ICON[x.key]||'🔌'}</div><b class="grow" style="font-size:15px">${esc(x.title)}</b><span class="badge ${b[0]}">${b[1]}</span></div>
        <div class="ds">${esc(x.desc)}</div>
        ${x.state==='inactive'||x.state==='prepared'?`<div class="muted" style="font-size:12.5px">${esc(x.need)}</div>`:''}
        ${x.key==='tasnif'?`<div style="font-size:13px">Без ИКПУ: <b class="${x.check.missing?'red':'green'}">${x.check.missing}</b> · неверный формат: <b class="${x.check.invalid?'red':'green'}">${x.check.invalid}</b></div>`:''}
        ${x.key==='ofd'&&x.stores?`<div style="font-size:13px">${x.stores.map(s=>`${esc(s.name)}: ${!s.seen?'<span class="muted">нет связи</span>':s.unsent?`<b class="red">${s.unsent} без фискализации</b>`:'<span class="green">все чеки в ОФД</span>'}`).join(' · ')||'<span class="muted">Магазинов нет</span>'}</div>`:''}
        ${x.jobs.n?`<div class="muted" style="font-size:12.5px">Отправлено ${x.jobs.done}${x.jobs.waiting?` · в очереди ${x.jobs.waiting}`:''}${x.jobs.dead?` · <span class="red">не доставлено ${x.jobs.dead}</span>`:''}</div>`:''}
        ${x.last_error?`<div class="red" style="font-size:12px">Ошибка: ${esc(x.last_error)}</div>`:x.last_ok?`<div class="green" style="font-size:12px">Связь проверена ${x.last_ok.slice(0,16)}</div>`:''}
        <div class="row" style="gap:6px;margin-top:4px;flex-wrap:wrap">${intButtons(x)}</div></div>`; }).join('')}</div>`;
}
function intButtons(x){
  const b=[];
  if(x.state!=='builtin') b.push(`<button onclick="intEdit('${x.key}')">${x.fields.length?'Настроить':(x.enabled?'Управление':'Включить')}</button>`);
  if(x.key==='1c') b.push(`<a href="/api/integrations/1c/commerceml.zip"><button>⬇ CommerceML для 1С</button></a>`);
  if(x.key==='tasnif') b.push(`<button onclick="tasnifList()">Показать товары</button>`);
  if(x.key==='uzum') b.push(`<button onclick="uzumPreview()">Предпросмотр витрины</button>`);
  if(x.key==='telegram'&&x.state==='active') b.push(`<button onclick="tgReport()">Отправить отчёт сейчас</button>`);
  if(x.key==='excel') b.push(`<button onclick="show('migrate')">Загрузка из Excel</button>`);
  if(x.jobs.n) b.push(`<button class="ghost" onclick="$$('#inTabs button')[1].click();intLog('${x.key}')">Журнал</button>`);
  return b.join('');
}
function intEdit(key){
  const x=INT.find(i=>i.key===key);
  const m=modal(`<div class="row"><h3 class="grow" style="margin:0">${INT_ICON[x.key]||''} ${esc(x.title)}</h3><span class="badge ${ST_B[x.state][0]}">${ST_B[x.state][1]}</span></div>
    <p class="muted" style="font-size:13px">${esc(x.need||x.desc)}</p>
    ${x.fields.map(f=>`<label>${esc(f.label)}${f.optional?' <span class="muted">(необязательно)</span>':''}</label><input data-f="${f.name}" value="${esc(x.settings[f.name]||'')}" placeholder="${esc(f.ph||'')}" ${f.secret?'autocomplete="off"':''}>`).join('')}
    ${x.key==='telegram'?`<div style="margin-top:6px"><button id="tgFind" type="button">Найти chat_id</button> <span class="muted" style="font-size:12px">— сначала сохраните токен и напишите боту /start</span></div>
      <label style="margin-top:10px">Что присылать</label>${x.events.map(e=>`<label class="chip"><input type="checkbox" data-ev="${e.key}" ${e.on?'checked':''}> ${esc(e.title)}</label>`).join('')}`:''}
    ${x.key==='uzum'?`<div class="muted" style="font-size:12px">Магазин — ID магазина сети: ${AF&&AF.stores?AF.stores.map(s=>`${s.id} — ${esc(s.name)}`).join(', '):STORES.map(s=>`${s.id} — ${esc(s.name)}`).join(', ')}</div>`:''}
    ${x.key==='sheets'&&x.feeds?`<label style="margin-top:8px">Ссылки для Google Таблиц</label>${x.feeds.map(f=>`<div class="feed"><span style="width:170px;font-size:12.5px">${esc(f.title)}</span><code>${esc(f.url)}</code><button type="button" onclick="navigator.clipboard.writeText('=IMPORTDATA(&quot;${f.url}&quot;)').then(()=>toast('Формула скопирована — вставьте её в ячейку A1','ok'))">Формула</button></div>`).join('')}
      <div class="muted" style="font-size:12px">Вставьте формулу в ячейку A1 листа Google Таблицы. Google обновляет данные примерно раз в час. Ссылка — как пароль: не публикуйте её.</div>`:''}
    ${x.implemented?`<label class="chip" style="margin-top:12px"><input type="checkbox" id="inOn" ${x.enabled?'checked':''}> Включить интеграцию</label>`:'<div class="card" style="margin-top:10px;background:#fffaeb;border-color:#fedf89;font-size:13px">Модуль подготовлен: настройки сохранятся, но обмен включится только после получения документации и проверки на тестовом доступе сервиса — система не имитирует подключение.</div>'}
    <div id="inMsg" style="margin-top:8px;font-size:13px"></div>
    <div class="actions">${x.key==='sheets'&&x.enabled?'<button class="r" id="inTok" style="margin-right:auto">Новый ключ ссылок</button>':''}<button onclick="closeM()">Закрыть</button>${x.can_test?'<button id="inTest">Проверить подключение</button>':''}<button class="p" id="inSave">Сохранить</button></div>`,'wide');
  const body=extra=>({settings:Object.fromEntries([...m.querySelectorAll('[data-f]')].map(i=>[i.dataset.f,i.value])),enabled:m.querySelector('#inOn')?.checked||false,
    events:[...m.querySelectorAll('[data-ev]')].filter(i=>i.checked).map(i=>i.dataset.ev),...extra});
  const save=async extra=>{ const r=await api('/api/integrations/'+key,{method:'POST',body:body(extra)}); Object.assign(x,r); return r; };
  m.querySelector('#inSave').onclick=async()=>{ try{ await save(); toast('Сохранено','ok'); m.remove(); loadInt(); }catch(e){ toast(e.message,'err'); } };
  m.querySelector('#inTok')?.addEventListener('click',async()=>{ if(!confirm('Старые ссылки перестанут работать. Выпустить новый ключ?')) return; await save({new_token:true}); m.remove(); loadInt(); intEdit(key); });
  m.querySelector('#inTest')?.addEventListener('click',async()=>{ const o=m.querySelector('#inMsg'); o.innerHTML='<span class="muted">Проверяю…</span>';
    try{ await save(); const r=await api(`/api/integrations/${key}/test`,{method:'POST'}); o.innerHTML=`<span class="${r.ok?'green':'red'}">${r.ok?'✓':'✗'} ${esc(r.message)}</span>`; }catch(e){ o.innerHTML=`<span class="red">${esc(e.message)}</span>`; } });
  m.querySelector('#tgFind')?.addEventListener('click',async()=>{ const o=m.querySelector('#inMsg');
    try{ await save(); const r=await api('/api/integrations/telegram/find_chat',{method:'POST'});
      if(!r.chats.length){ o.innerHTML='<span class="amber">Чатов не найдено — напишите боту /start (или добавьте его в группу и напишите там) и нажмите ещё раз.</span>'; return; }
      o.innerHTML='Нажмите на чат: '+r.chats.map(c=>`<button class="ghost" type="button" data-cid="${esc(c.id)}">${esc(c.name||c.id)} · ${esc(c.id)}</button>`).join(' ');
      o.querySelectorAll('[data-cid]').forEach(b=>b.onclick=()=>{ m.querySelector('[data-f=chat_id]').value=b.dataset.cid; o.innerHTML='<span class="green">Chat ID подставлен — сохраните и проверьте подключение</span>'; });
    }catch(e){ o.innerHTML=`<span class="red">${esc(e.message)}</span>`; } });
}
async function intLog(key){
  const B=$('#inBody'); const r=await api('/api/integrations/jobs'+(key?'?key='+key:''));
  B.innerHTML=`<div class="card"><div class="row" style="margin-bottom:8px"><b class="grow">Журнал обмена${key?' · '+esc(INT?.find(i=>i.key===key)?.title||key):''}</b>${key?'<button class="ghost" onclick="intLog()">Все интеграции</button>':''}</div>
    <div style="overflow:auto;max-height:640px"><table><thead><tr><th>Время</th><th>Интеграция</th><th>Что</th><th>Статус</th><th class="n">Попыток</th><th>Ответ / ошибка</th><th></th></tr></thead><tbody>
    ${r.items.map(j=>{ const b=JOB_B[j.status]||['',j.status]; return `<tr><td style="white-space:nowrap">${j.created_at.slice(0,16)}</td><td>${esc(INT?.find(i=>i.key===j.key)?.title||j.key)}</td><td>${esc(j.title||j.kind)}</td>
      <td><span class="badge ${b[0]}">${b[1]}</span>${j.status==='retry'?`<div class="muted" style="font-size:11px">след. ${j.next_at.slice(11,16)}</div>`:''}</td><td class="n">${j.attempts}</td>
      <td style="font-size:12px" class="${j.error?'red':'muted'}">${esc(j.error||j.response||'')}</td><td>${['dead','retry'].includes(j.status)?`<button class="ghost" onclick="intRetry(${j.id},'${key||''}')">Повторить</button>`:''}</td></tr>`; }).join('')||'<tr><td colspan="7" class="muted pad">Обменов ещё не было</td></tr>'}
    </tbody></table></div></div>`;
}
async function intRetry(id,key){ const r=await api(`/api/integrations/jobs/${id}/retry`,{method:'POST'}); toast(r.status==='done'?'Отправлено':'Не удалось: '+(r.error||''),r.status==='done'?'ok':'err'); intLog(key||undefined); }
async function tgReport(){ try{ const r=await api('/api/integrations/telegram/report_now',{method:'POST'}); toast(r.ok?'Отчёт отправлен в Telegram':'Не отправлено: '+(r.error||'')+' — будет повтор',r.ok?'ok':'err'); loadInt(); }catch(e){ toast(e.message,'err'); } }
async function tasnifList(){
  const r=await api('/api/integrations/tasnif/check');
  const tb=(t,rows)=>`<b>${t}: ${rows.length}</b><div style="max-height:260px;overflow:auto;margin:6px 0 12px"><table><tbody>${rows.slice(0,300).map(p=>`<tr><td>${esc(p.name)}</td><td class="muted">${esc(p.barcode||'')}</td><td>${esc(p.category||'')}</td><td>${esc(p.spic||'')}</td></tr>`).join('')||'<tr><td class="green">Нет ✓</td></tr>'}</tbody></table></div>`;
  modal(`<h3>Контроль ИКПУ</h3><p class="muted" style="margin-top:0;font-size:13px">Код ИКПУ — 17 цифр из классификатора tasnif.soliq.uz. Без него касса не пробьёт фискальный чек. Код заполняется в карточке товара (Каталог).</p>
    ${tb('Без ИКПУ',r.missing)}${tb('Неверный формат',r.invalid)}<div class="actions"><a href="https://tasnif.soliq.uz/" target="_blank" rel="noopener"><button>Открыть tasnif.soliq.uz</button></a><button class="p" onclick="closeM()">Закрыть</button></div>`,'wide');
}
async function uzumPreview(){
  let sid=''; const x=INT.find(i=>i.key==='uzum'); if(!x.settings.store_id){ sid=prompt('ID магазина сети, откуда отгружать заказы Uzum:\n'+STORES.map(s=>`${s.id} — ${s.name}`).join('\n')); if(!sid) return; }
  try{ const r=await api('/api/integrations/uzum/preview'+(sid?'?store='+sid:''));
    modal(`<h3>Витрина Uzum Market — предпросмотр</h3><p class="muted" style="margin-top:0;font-size:13px">${esc(r.note)}</p><div style="max-height:420px;overflow:auto"><table><thead><tr><th>Товар</th><th>Штрихкод</th><th>Артикул</th><th class="n">Цена</th><th class="n">Остаток</th></tr></thead><tbody>
      ${r.items.map(p=>`<tr><td>${esc(p.name)}</td><td class="muted">${esc(p.barcode||'')}</td><td>${esc(p.sku||'')}</td><td class="n">${fmt(p.price)}</td><td class="n">${fq(p.qty)}</td></tr>`).join('')||'<tr><td class="muted">Нет товаров в наличии</td></tr>'}</tbody></table></div>
      <div class="actions"><button class="p" onclick="closeM()">Закрыть</button></div>`,'wide'); }catch(e){ toast(e.message,'err'); }
}
