// =====================================================================
//  Центр сети (этап 7): Персонал (HRM) и Клиенты (CRM).
// =====================================================================
Object.assign(NAV_PERM,{staff:'staff',crm:'crm'});
Object.assign(TITLES,{staff:'Персонал',crm:'Клиенты и лояльность',users:'Доступ в Центр'});
let SF_TAB='list', CR_TAB='base', STF=null, CRM=null, CRQ={q:'',segment:'',level:'*',flag:'',sort:'ltv'};
const SEG_C={vip:'#7c3aed',regular:'var(--green)',new:'var(--brand)',casual:'#64748b',once:'#94a3b8',risk:'var(--amber)',lost:'var(--red)',none:'#cbd5e1'};
const dshort=s=>s?s.slice(8,10)+'.'+s.slice(5,7)+'.'+s.slice(2,4):'—';
const MON=['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек'];
const frq=v=>v<1?'< 1 дн.':String(v).replace('.',',')+' дн.';
const monL=m=>MON[+m.slice(5,7)-1]+' '+m.slice(2,4);

document.head.insertAdjacentHTML('beforeend',`<style>
  .seg-chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:99px;padding:5px 11px;margin:0 6px 6px 0;cursor:pointer;font-size:13px;background:#fff}
  .seg-chip i{width:8px;height:8px;border-radius:50%} .seg-chip.on{border-color:var(--brand);background:var(--brand-50);color:var(--brand-d);font-weight:600}
  .podium{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:12px} .podium .card{text-align:center} .podium .pl{font-size:30px}
  .sc{height:6px;background:#f1f3f6;border-radius:3px;overflow:hidden;width:90px;display:inline-block;vertical-align:middle;margin-right:6px} .sc i{display:block;height:100%;background:var(--brand)}
  tr.click{cursor:pointer} tr.click:hover td{background:#f8fafc}
  .lvrow{display:grid;grid-template-columns:1.4fr 1fr .8fr .8fr 40px;gap:8px;align-items:end;margin-bottom:6px} .lvrow input{margin:0}
  .tl{border-left:2px solid var(--line);padding-left:12px;margin-left:4px} .tl>div{padding:6px 0;border-bottom:1px dashed var(--line);font-size:13px}
</style>`);

// ---------------------------------------------------------------- навигация
(function(){
  const ub=$('#nav button[data-v=users]'); if(ub){ ub.title='Доступ в Центр'; ub.querySelector('.t').textContent='Доступ в Центр'; }
  const fin=$('#nav button[data-v=fin]');
  fin.insertAdjacentHTML('afterend',`<div class="grp">Люди</div>
    <button data-v="staff" title="Персонал"><svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2.5"/><path d="M5.5 17a3.5 3.5 0 0 1 7 0"/><path d="M15 9h3M15 13h3"/></svg><span class="t">Персонал</span></button>
    <button data-v="crm" title="Клиенты"><svg viewBox="0 0 24 24"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg><span class="t">Клиенты</span></button>`);
  ['staff','crm'].forEach(v=>$(`#nav button[data-v=${v}]`).onclick=()=>show(v));
  $('main').insertAdjacentHTML('beforeend',`
    <section class="view" id="v-staff"><div class="dtool"><div class="seg" id="sfTabs"><button data-t="list" class="on">Сотрудники</button><button data-t="rating">Рейтинг</button></div></div>
      <div class="fbar" id="sfF"></div><div id="sfBody"></div></section>
    <section class="view" id="v-crm"><div class="dtool"><div class="seg" id="crTabs"><button data-t="base" class="on">База клиентов</button><button data-t="levels">Уровни лояльности</button></div></div>
      <div class="fbar" id="crF"></div><div id="crBody"></div></section>`);
  $$('#sfTabs button').forEach(b=>b.onclick=()=>{ SF_TAB=b.dataset.t; $$('#sfTabs button').forEach(x=>x.classList.toggle('on',x===b)); loadStaff(); });
  $$('#crTabs button').forEach(b=>b.onclick=()=>{ CR_TAB=b.dataset.t; $$('#crTabs button').forEach(x=>x.classList.toggle('on',x===b)); loadCrm(); });
})();
const _showP=show;
show=function(v){ if(v==='staff'||v==='crm'){ if(!can(NAV_PERM[v])) return toast('Нет доступа к разделу','err');
    $$('.view').forEach(x=>x.classList.toggle('on',x.id==='v-'+v)); $$('#nav button').forEach(b=>b.classList.toggle('on',b.dataset.v===v)); $('#pageTitle').textContent=TITLES[v];
    return v==='staff'?loadStaff():loadCrm(); }
  _showP(v); };
const _bootP=boot; boot=async function(){ await _bootP(); ['staff','crm'].forEach(v=>$(`#nav button[data-v=${v}]`)?.classList.toggle('hidden',!can(NAV_PERM[v])));
  $$('#nav .grp').forEach(g=>{ let n=g.nextElementSibling, any=false; while(n&&!n.classList.contains('grp')){ if(!n.classList.contains('hidden')) any=true; n=n.nextElementSibling; } g.classList.toggle('hidden',!any); }); };
async function pFilters(){ try{ return await filters(); }catch(e){ AF={stores:STORES.map(s=>({id:s.id,name:s.name})),categories:[],brands:[],expense_categories:[],finance:can('finance'),can_expense:can('expenses')}; return AF; } }

// ================================================================ ПЕРСОНАЛ
async function loadStaff(){
  await pFilters(); const B=$('#sfBody');
  fbar($('#sfF'),{cat:false,extra:`<button onclick="location.href='/api/staff/export.xlsx?'+qs()">⬇ Excel</button>`},loadStaff);
  B.innerHTML='<div class="pad muted">Загрузка…</div>';
  try{ STF=await api('/api/staff?'+qs()); }catch(e){ B.innerHTML=`<div class="card red">${esc(e.message)}</div>`; return; }
  if(STF.can_edit) $('#sfF .grow').insertAdjacentHTML('afterend',`${STF.can_payroll?'<button onclick="payroll()">Начислить оклады</button>':''}<button onclick="planSplit()">План на сотрудников</button><button class="p" onclick="editEmp()">+ Сотрудник</button>`);
  SF_TAB==='list'?staffList(B):staffRating(B);
}
function tillCell(e){
  if(!e.till_role) return '<span class="muted">нет доступа</span>';
  const r=e.till_role==='admin'?'Администратор':'Кассир';
  if(e.till_error) return `<span class="badge err" title="${esc(e.till_error)}">⚠ ${r}</span>`;
  return e.source==='till'?`<span class="badge" title="Заведён на кассе. Задайте ПИН здесь, чтобы управлять доступом из Центра">${r} · касса</span>`:`<span class="badge ok">${r}${e.has_pin?' · ПИН ✓':''}</span>`;
}
function staffList(B){
  const T=STF.total, E=STF.can_edit;
  B.innerHTML=`<div class="kpis" style="margin-bottom:12px">${kpi('Сотрудников',T.active,`всего в списке ${T.employees}`)}${kpi('Чеков за период',fmt(T.checks))}${kpi('Выручка сотрудников',fmt(T.net))}
      ${kpi('Средний чек',fmt(T.avg))}${E?kpi('Фонд оплаты (оклады)',fmt(T.payroll),'в месяц'):''}</div>
    <div class="card"><div style="overflow:auto"><table><thead><tr><th>Сотрудник</th><th>Магазин</th><th>Статус</th><th>Касса</th><th>График</th>${E?'<th class="n">Оклад</th>':''}
      <th class="n">Чеков</th><th class="n">Выручка</th><th class="n">Ср. чек</th><th class="n" title="Позиций в чеке">Поз./чек</th><th class="n">План</th><th class="n">Место</th></tr></thead><tbody>
      ${STF.items.map(e=>`<tr class="click" onclick="empCard('${e.uid}')"><td><b>${esc(e.full_name)}</b><div class="muted" style="font-size:12px">${esc(e.position||'')}${e.phone?' · '+esc(e.phone):''}</div></td>
        <td>${esc(e.store||'—')}</td><td><span class="badge ${e.status==='active'?'ok':e.status==='vacation'?'pend':''}">${e.status_title}</span></td><td>${tillCell(e)}</td><td class="muted">${esc(e.schedule||'')}</td>
        ${E?`<td class="n">${e.salary?fmt(e.salary):'—'}</td>`:''}<td class="n">${e.checks||'—'}</td><td class="n"><b>${e.net?fmt(e.net):'—'}</b></td><td class="n">${e.avg?fmt(e.avg):'—'}</td><td class="n">${e.upt||'—'}</td>
        <td class="n">${e.plan_pct==null?'<span class="muted">—</span>':`<span class="${e.plan_pct>=100?'green':e.plan_pct<70?'amber':''}">${e.plan_pct}%</span>`}</td><td class="n">${e.rank?'#'+e.rank:''}</td></tr>`).join('')||
        `<tr><td colspan="12" class="muted pad">Сотрудников нет. ${E?'Добавьте их кнопкой «+ Сотрудник» — или они появятся сами, когда кассы пришлют своих кассиров.':''}</td></tr>`}
    </tbody></table></div>
    <div class="muted" style="font-size:12.5px;margin-top:10px">Кассиры, заведённые на кассе, появляются здесь сами. Задайте сотруднику роль и ПИН — он сможет входить на кассу своего магазина, а изменить или отключить его можно будет только здесь. Уволенные скрыты.</div></div>`;
}
function staffRating(B){
  const R=STF.items.filter(e=>e.rank).sort((a,b)=>a.rank-b.rank), hasPlan=R.some(e=>e.plan_pct!=null);
  if(!R.length){ B.innerHTML='<div class="card muted pad">За период нет продаж сотрудников.</div>'; return; }
  const med=['🥇','🥈','🥉'];
  B.innerHTML=`<div class="podium">${R.slice(0,3).map((e,i)=>`<div class="card"><div class="pl">${med[i]}</div><b style="font-size:15px">${esc(e.full_name)}</b><div class="muted" style="font-size:12.5px">${esc(e.store||'')}</div>
      <div style="font-size:22px;font-weight:650;margin-top:6px">${e.score} <span class="muted" style="font-size:13px;font-weight:400">баллов</span></div><div class="muted" style="font-size:12.5px">${fmt(e.net)} · ${e.checks} чеков${e.plan_pct!=null?' · план '+e.plan_pct+'%':''}</div></div>`).join('')}</div>
    <div class="card"><div style="overflow:auto"><table><thead><tr><th>#</th><th>Сотрудник</th><th>Магазин</th><th>Баллы</th><th class="n">Выручка</th><th class="n">Чеков</th><th class="n">Ср. чек</th><th class="n">Поз./чек</th><th class="n">Ед./чек</th>
      ${hasPlan?'<th class="n">План</th>':''}<th class="n">Возвраты</th><th class="n">Скидки</th><th class="n">Конверсия</th></tr></thead><tbody>
      ${R.map(e=>`<tr class="click" onclick="empCard('${e.uid}')"><td><b>${e.rank}</b></td><td><b>${esc(e.full_name)}</b><div class="muted" style="font-size:12px">${esc(e.position||'')}</div></td><td>${esc(e.store||'')}</td>
        <td><span class="sc"><i style="width:${e.score}%"></i></span>${e.score}</td><td class="n">${fmt(e.net)}</td><td class="n">${e.checks}</td><td class="n">${fmt(e.avg)}</td><td class="n">${e.upt}</td><td class="n">${e.units}</td>
        ${hasPlan?`<td class="n">${e.plan_pct==null?'—':e.plan_pct+'%'}</td>`:''}<td class="n ${e.refunds?'red':''}">${e.refunds||0}${e.refunds?' · '+fmt(e.refunds_sum):''}</td><td class="n">${fmt(e.discount)}</td>
        <td class="n muted" title="Нужны данные счётчика посетителей">нет данных</td></tr>`).join('')}</tbody></table></div>
    <div class="muted" style="font-size:12.5px;margin-top:10px">Баллы — среднее четырёх показателей относительно лучшего в списке: выручка, средний чек, позиций в чеке${hasPlan?' и выполнение личного плана (до 150%)':''}. Конверсия появится, когда к магазинам подключат счётчик посетителей — без него она не считается.</div></div>`;
}
async function empCard(uid){
  const r=await api('/api/staff/'+uid), e=r.employee, M=r.months;
  const m=modal(`<div class="row" style="margin-bottom:6px"><h3 class="grow" style="margin:0">${esc(e.full_name)}</h3>${r.can_edit?`<button onclick="closeM();editEmp('${e.uid}')">Изменить</button>`:''}<button onclick="closeM()">✕</button></div>
    <div class="muted">${esc(e.position||'Без должности')} · ${esc(e.store||'')} · <span class="badge ${e.status==='active'?'ok':''}">${e.status_title}</span> ${tillCell(e)}</div>
    ${e.till_error?`<div class="card" style="background:#fef3f2;border-color:#fecdca;margin-top:8px">⚠ Касса не приняла сотрудника: ${esc(e.till_error)}. Задайте другой ПИН.</div>`:''}
    <div class="grid3" style="margin-top:10px;gap:8px 12px"><div><span class="muted">Телефон</span><br>${esc(e.phone||'—')}</div><div><span class="muted">Принят</span><br>${e.hired_at?dshort(e.hired_at):'—'}</div><div><span class="muted">График</span><br>${esc(e.schedule||'—')}</div>
      ${e.salary!=null?`<div><span class="muted">Оклад</span><br><b>${e.salary?fmt(e.salary):'—'}</b></div>`:''}${e.note?`<div style="grid-column:span 2"><span class="muted">Заметка</span><br>${esc(e.note)}</div>`:''}</div>
    <div class="card" style="margin-top:12px"><b>Продажи по месяцам</b> <span class="muted" style="font-size:12px">— серым личный план</span><div id="ecCh" style="margin-top:6px"></div>
      <div style="overflow:auto"><table style="margin-top:6px"><tr><th></th>${M.map(x=>`<th class="n">${monL(x.month)}</th>`).join('')}</tr>
        <tr><td class="muted">Выручка</td>${M.map(x=>`<td class="n">${x.net?short(x.net):''}</td>`).join('')}</tr><tr><td class="muted">Чеков</td>${M.map(x=>`<td class="n">${x.checks||''}</td>`).join('')}</tr>
        <tr><td class="muted">План</td>${M.map(x=>`<td class="n">${x.plan?Math.round(x.net/x.plan*100)+'%':''}</td>`).join('')}</tr></table></div></div>
    ${r.can_edit?`<div class="card" style="margin-top:12px"><b>Личный план продаж</b><div class="grid3" style="margin-top:6px"><div><label>${monL(r.cur_month)}</label><input id="ecP1" type="number" value="${r.plan_cur??''}"></div>
      <div><label>${monL(r.next_month)}</label><input id="ecP2" type="number" value="${r.plan_next??''}"></div><div><label>&nbsp;</label><button class="p" id="ecPS" style="width:100%">Сохранить план</button></div></div></div>`:''}
    ${r.log.length?`<div class="card" style="margin-top:12px"><b>История</b><div class="tl" style="margin-top:6px;max-height:180px;overflow:auto">${r.log.map(l=>`<div><span class="muted">${l.created_at.slice(0,16)} · ${esc(l.user_name||'')}</span><br>${esc(l.action)}: ${esc(l.details||'')}</div>`).join('')}</div></div>`:''}`,'wide');
  vbars(m.querySelector('#ecCh'), M.map(x=>({label:monL(x.month), v:x.net, v2:x.plan||null, tip:monL(x.month)})), {h:170, l2:'План'});
  m.querySelector('#ecPS')?.addEventListener('click',async()=>{ try{ await api(`/api/staff/${uid}/plan`,{method:'POST',body:{plans:[{month:r.cur_month,amount:m.querySelector('#ecP1').value},{month:r.next_month,amount:m.querySelector('#ecP2').value}]}});
    toast('План сохранён','ok'); m.remove(); empCard(uid); loadStaff(); }catch(e){ toast(e.message,'err'); } });
}
async function editEmp(uid){
  const e=uid?(await api('/api/staff/'+uid)).employee:{status:'active',till_role:'',store_id:FL.store||(AF.stores.length===1?AF.stores[0].id:'')};
  const o=(v,t,cur)=>`<option value="${esc(v)}" ${String(cur??'')===String(v)?'selected':''}>${esc(t)}</option>`;
  const fromTill=e.source==='till';
  const m=modal(`<h3>${uid?'Сотрудник':'Новый сотрудник'}</h3>
    <div class="grid2"><div><label>ФИО</label><input id="eN" value="${esc(e.full_name||'')}" autofocus></div><div><label>Должность</label><input id="eP" list="posL" value="${esc(e.position||'')}"><datalist id="posL">${STF.positions.map(p=>`<option value="${esc(p)}">`).join('')}</datalist></div></div>
    <div class="grid2"><div><label>Магазин</label><select id="eS"><option value="">— выберите —</option>${AF.stores.map(s=>o(s.id,s.name,e.store_id)).join('')}</select></div><div><label>Телефон</label><input id="ePh" value="${esc(e.phone||'')}" placeholder="+998 90 123 45 67"></div></div>
    <div class="grid3"><div><label>Дата приёма</label><input id="eH" type="date" value="${esc(e.hired_at||'')}"></div><div><label>График</label><input id="eG" list="grL" value="${esc(e.schedule||'')}" placeholder="2/2, 9:00–21:00"><datalist id="grL"><option value="5/2"><option value="2/2"><option value="6/1"><option value="3/3"><option value="Гибкий"></datalist></div>
      <div><label>Статус</label><select id="eSt">${Object.entries(STF.statuses).map(([k,v])=>o(k,v,e.status)).join('')}</select></div></div>
    <div class="grid3"><div><label>Оклад в месяц, сум</label><input id="eSa" type="number" value="${e.salary??''}"></div>
      <div><label>Доступ на кассу</label><select id="eR">${o('','Нет',e.till_role)}${o('cashier','Кассир',e.till_role)}${o('admin','Администратор кассы',e.till_role)}</select></div>
      <div><label>ПИН для кассы${e.has_pin?' (задан)':''}</label><input id="ePin" inputmode="numeric" maxlength="8" placeholder="${e.has_pin?'оставьте пустым — не менять':'4–8 цифр'}"></div></div>
    ${fromTill?'<div class="muted" style="font-size:12.5px">Сотрудник заведён на кассе. Если задать ПИН здесь, доступом на кассу будет управлять Центр, а на кассе его изменить будет нельзя.</div>':''}
    <label>Заметка</label><input id="eNo" value="${esc(e.note||'')}">
    <div class="muted" style="font-size:12.5px;margin-top:8px">Статус «В отпуске» и «Уволен» закрывает вход на кассу. Сотрудник появится на кассе магазина в течение минуты.</div>
    <div class="actions"><button onclick="closeM()">Отмена</button><button class="p" id="eOk">Сохранить</button></div>`,'wide');
  const g=s=>m.querySelector(s).value;
  m.querySelector('#eOk').onclick=async()=>{ try{ await api('/api/staff',{method:'POST',body:{uid:e.uid,full_name:g('#eN'),position:g('#eP'),store_id:g('#eS'),phone:g('#ePh'),hired_at:g('#eH'),schedule:g('#eG'),
      status:g('#eSt'),salary:g('#eSa'),till_role:g('#eR'),pin:g('#ePin'),note:g('#eNo')}}); m.remove(); toast('Сохранено','ok'); loadStaff(); }catch(er){ toast(er.message,'err'); } };
}
function monthSel(id){ const d=new Date(), opts=[];
  for(let i=-2;i<=1;i++){ const x=new Date(d.getFullYear(),d.getMonth()+i,1), v=x.toLocaleDateString('sv-SE').slice(0,7); opts.push(`<option value="${v}" ${i===0?'selected':''}>${monL(v)}</option>`); }
  return `<select id="${id}">${opts.join('')}</select>`; }
function payroll(){
  const m=modal(`<h3>Начислить оклады</h3><p class="muted" style="margin-top:0">Оклады работающих сотрудников и сотрудников в отпуске запишутся в расходы «Зарплаты» их магазинов — они попадут в отчёт о прибыли.
    Повторное начисление за тот же месяц не задвоит расходы. Премии и удержания вносите в «Финансы → Расходы».</p>
    <label>Месяц</label>${monthSel('prM')}<div class="actions"><button onclick="closeM()">Отмена</button><button class="p" id="prOk">Начислить</button></div>`);
  m.querySelector('#prOk').onclick=async()=>{ try{ const r=await api('/api/staff/payroll',{method:'POST',body:{month:m.querySelector('#prM').value,store_id:FL.store||''}}); m.remove();
    toast(r.made?`Начислено ${r.made} окл. на ${fmt(r.total)}${r.skipped?` · уже были: ${r.skipped}`:''}`:(r.skipped?'За этот месяц оклады уже начислены':'Нет сотрудников с окладом'),'ok'); }catch(e){ toast(e.message,'err'); } };
}
function planSplit(){
  const m=modal(`<h3>Разделить план магазина</h3><p class="muted" style="margin-top:0">План магазина на месяц делится поровну между работающими сотрудниками с доступом на кассу. Личные планы на этот месяц перезапишутся.</p>
    <div class="grid2"><div><label>Магазин</label><select id="psS">${AF.stores.map(s=>`<option value="${s.id}" ${String(FL.store)===String(s.id)?'selected':''}>${esc(s.name)}</option>`).join('')}</select></div><div><label>Месяц</label>${monthSel('psM')}</div></div>
    <div class="actions"><button onclick="closeM()">Отмена</button><button class="p" id="psOk">Разделить</button></div>`);
  m.querySelector('#psOk').onclick=async()=>{ try{ const r=await api('/api/staff/plan_split',{method:'POST',body:{store_id:m.querySelector('#psS').value,month:m.querySelector('#psM').value}});
    m.remove(); toast(`План: ${r.n} сотр. по ${fmt(r.each)}`,'ok'); loadStaff(); }catch(e){ toast(e.message,'err'); } };
}

// ================================================================ КЛИЕНТЫ
async function loadCrm(){
  await pFilters(); const B=$('#crBody');
  if(CR_TAB==='levels'){ $('#crF').innerHTML=''; return crmLevels(B); }
  fbar($('#crF'),{cat:false,extra:`<button onclick="location.href='/api/crm/export.xlsx?'+crq()">⬇ Excel</button>`},loadCrm);
  $$('#crF [data-k=preset],#crF [data-k=from],#crF [data-k=to]').forEach(x=>x.classList.add('hidden'));
  B.innerHTML='<div class="pad muted">Загрузка…</div>';
  try{ CRM=await api('/api/crm/customers?'+crq()); }catch(e){ B.innerHTML=`<div class="card red">${esc(e.message)}</div>`; return; }
  crmBase(B);
}
const crq=()=>`store=${FL.store}&q=${encodeURIComponent(CRQ.q)}&segment=${CRQ.segment}&level=${encodeURIComponent(CRQ.level)}&flag=${CRQ.flag}&sort=${CRQ.sort}`;
let crT=null;
function crmBase(B){
  const S=CRM.summary, L=CRM.levels;
  B.innerHTML=`<div class="kpis" style="margin-bottom:12px">${kpi('Клиентов в базе',fmt(S.total),`покупали: ${fmt(S.buyers)} · новых за 30 дн.: ${S.new30}`)}${kpi('Активные',fmt(S.active90),'покупали за 90 дней')}
      ${kpi('Средний LTV',fmt(S.avg_ltv),'сумма покупок клиента за всё время')}${kpi('Средний чек клиента',fmt(S.avg_check))}${kpi('Частота',S.avg_freq==null?'—':frq(S.avg_freq),'между покупками')}
      ${kpi('Доля выручки клиентов',S.share30==null?'—':S.share30+'%','за 30 дней, остальное — без карты')}</div>
    <div class="card" style="margin-bottom:12px"><div>${Object.entries(CRM.segments).map(([k,v])=>`<span class="seg-chip ${CRQ.segment===k?'on':''}" data-s="${k}"><i style="background:${SEG_C[k]}"></i>${v} <b>${S.segments[k]||0}</b></span>`).join('')}
        <span class="seg-chip ${CRQ.flag==='debt'?'on':''}" data-f="debt">💳 Должники <b>${S.debtors}</b></span><span class="seg-chip ${CRQ.flag==='bday'?'on':''}" data-f="bday">🎂 ДР в этом месяце <b>${S.bdays}</b></span></div>
      <div class="row wrap" style="gap:8px;margin-top:4px"><input id="crQ" placeholder="Имя, телефон или карта" value="${esc(CRQ.q)}" style="max-width:280px;margin:0">
        <select id="crL" style="width:auto;margin:0"><option value="*">Все уровни</option>${L.map(l=>`<option ${CRQ.level===l.name?'selected':''}>${esc(l.name)}</option>`).join('')}<option value="" ${CRQ.level===''?'selected':''}>Без уровня</option></select>
        <select id="crS" style="width:auto;margin:0">${[['ltv','По LTV'],['net365','По сумме за 12 мес.'],['orders','По числу покупок'],['avg','По среднему чеку'],['days_since','Недавно покупали'],['freq','Чаще покупают'],['bonus','По бонусам'],['debt','По долгу'],['created_at','Новые'],['name','По имени']].map(([k,t])=>`<option value="${k}" ${CRQ.sort===k?'selected':''}>${t}</option>`).join('')}</select>
        <span class="grow"></span><span class="muted" style="font-size:12.5px">Найдено ${CRM.found}${CRM.found>500?' · показаны первые 500 (все — в Excel)':''}${CRM.vip_from?` · VIP — от ${short(CRM.vip_from)} за год`:''}</span></div></div>
    <div class="card"><div style="overflow:auto;max-height:620px"><table><thead><tr><th>Клиент</th><th>Уровень</th><th>Сегмент</th><th class="n">Покупок</th><th class="n">LTV</th><th class="n">Ср. чек</th><th class="n">Частота</th><th>Последняя</th><th class="n">Бонусы</th><th class="n">Долг</th></tr></thead><tbody>
      ${CRM.items.map(c=>`<tr class="click" onclick="custCard(${c.id})"><td><b>${esc(c.name)}</b>${c.bday?' 🎂':''}<div class="muted" style="font-size:12px">${esc(c.phone||'')}${c.stores.length>1?' · '+c.stores.length+' магазина':''}</div></td>
        <td>${c.level?`<span class="badge blue">${esc(c.level)}</span>`:'<span class="muted">—</span>'}</td><td><span class="badge" style="background:${SEG_C[c.segment]}22;color:${SEG_C[c.segment]==='#cbd5e1'?'#64748b':SEG_C[c.segment]}">${CRM.segments[c.segment]}</span></td>
        <td class="n">${c.orders||'—'}</td><td class="n"><b>${c.ltv?fmt(c.ltv):'—'}</b></td><td class="n">${c.avg?fmt(c.avg):'—'}</td><td class="n">${c.freq==null?'—':frq(c.freq)}</td>
        <td>${c.last?dshort(c.last)+` <span class="muted" style="font-size:12px">${c.days_since} дн.</span>`:'—'}</td><td class="n">${c.bonus?fmt(c.bonus):'—'}</td><td class="n ${c.debt>0?'red':''}">${c.debt>0?fmt(c.debt):'—'}</td></tr>`).join('')||
      '<tr><td colspan="10" class="muted pad">Клиентов не найдено. Клиенты появляются, когда кассир выбирает покупателя на кассе (F5) — кассы присылают их в Центр.</td></tr>'}</tbody></table></div></div>`;
  B.querySelectorAll('[data-s]').forEach(x=>x.onclick=()=>{ CRQ.segment=CRQ.segment===x.dataset.s?'':x.dataset.s; CRQ.flag=''; loadCrm(); });
  B.querySelectorAll('[data-f]').forEach(x=>x.onclick=()=>{ CRQ.flag=CRQ.flag===x.dataset.f?'':x.dataset.f; CRQ.segment=''; loadCrm(); });
  $('#crQ').oninput=e=>{ clearTimeout(crT); crT=setTimeout(()=>{ CRQ.q=e.target.value.trim(); loadCrm().then(()=>{ const i=$('#crQ'); i.focus(); i.setSelectionRange(i.value.length,i.value.length); }); },300); };
  $('#crL').onchange=e=>{ CRQ.level=e.target.value; loadCrm(); }; $('#crS').onchange=e=>{ CRQ.sort=e.target.value; loadCrm(); };
}
async function custCard(id){
  const r=await api('/api/crm/customers/'+id), c=r.customer, s=r.stats||{}, lv=r.level;
  const nx=s.next_level, prog=nx?Math.max(3,Math.min(100,Math.round(s.net365/(s.net365+nx.left)*100))):100;
  const m=modal(`<div class="row" style="margin-bottom:4px"><h3 class="grow" style="margin:0">${esc(c.name)}${s.bday?' 🎂':''}</h3>${r.can_edit?'<button id="ccEd">Изменить</button>':''}<button onclick="closeM()">✕</button></div>
    <div class="muted">${esc(c.phone||'без телефона')}${c.card?' · карта '+esc(c.card):''}${c.birthday?' · ДР '+esc(c.birthday):''} · в базе с ${dshort(c.created_at)}${c.note?' · '+esc(c.note):''}</div>
    <div class="kpis" style="margin-top:12px">${kpi('Уровень',lv?esc(lv.name):'—',lv?`скидка ${lv.discount_pct}% · кешбэк ${lv.cashback_pct}%`:(r.levels.length?'':'уровни не настроены'))}
      ${kpi('Сегмент',esc(r.segment_title))}${kpi('LTV',fmt(s.ltv||0),`${s.orders||0} покупок · за 12 мес. ${fmt(s.net365||0)}`)}${kpi('Средний чек',fmt(s.avg||0),s.freq!=null?`раз в ${frq(s.freq)}`:'')}</div>
    ${r.levels.length?`<div class="card" style="margin-top:12px"><div class="row"><b class="grow">${nx?`До уровня «${esc(nx.name)}» — ${fmt(nx.left)} сум покупок`:'Максимальный уровень'}</b><span class="muted" style="font-size:12px">сумма покупок в сети за 12 месяцев</span></div><div class="pb"><i style="width:${prog}%;background:var(--brand)"></i></div></div>`:''}
    <div class="ag" style="margin-top:12px"><div class="card"><b>Магазины</b><table style="margin-top:6px"><thead><tr><th>Магазин</th><th class="n">Бонусы</th><th class="n">Долг</th><th class="n">Личная скидка</th></tr></thead><tbody>
        ${r.stores.map(x=>`<tr><td>${esc(x.name)}</td><td class="n">${fmt(x.bonus)}</td><td class="n ${x.debt>0?'red':''}">${fmt(x.debt)}</td><td class="n">${x.discount_pct?x.discount_pct+'%':'—'}</td></tr>`).join('')}</tbody></table>
        <div class="muted" style="font-size:12px;margin-top:6px">Бонусы и долг ведёт каждая касса отдельно; уровень и скидка уровня — общие для сети.</div></div>
      <div class="card"><b>Любимые товары</b><div style="margin-top:6px">${hbars(r.top,'net','name',x=>`<span class="muted" style="width:50px;text-align:right">${x.times}×</span>`)}</div>
        ${r.categories.length?`<div class="muted" style="font-size:12.5px;margin-top:8px">Категории: ${r.categories.map(x=>esc(x.name)).join(', ')}</div>`:''}</div></div>
    <div class="card" style="margin-top:12px"><b>История покупок</b> <span class="muted" style="font-size:12px">последние 100</span><div style="max-height:300px;overflow:auto;margin-top:6px"><table><tbody>
      ${r.sales.map(x=>`<tr><td style="white-space:nowrap">${x.created_at.slice(0,16)}</td><td>${esc(x.store)}<div class="muted" style="font-size:11.5px">${esc(x.cashier||'')}</div></td>
        <td class="muted" style="font-size:12.5px">${x.items.slice(0,4).map(i=>esc(i.name)+' × '+fq(i.qty)).join(', ')}${x.items.length>4?` и ещё ${x.items.length-4}`:''}</td>
        <td class="n ${x.operation==='refund'?'red':''}"><b>${x.operation==='refund'?'−':''}${fmt(x.total)}</b>${x.debt?'<div class="muted" style="font-size:11px">в долг '+fmt(x.debt)+'</div>':''}</td></tr>`).join('')||'<tr><td class="muted pad">Покупок пока нет</td></tr>'}</tbody></table></div></div>`,'wide');
  m.querySelector('#ccEd')?.addEventListener('click',()=>{ closeM(); editCust(c); });
}
function editCust(c){
  const m=modal(`<h3>Клиент</h3><div class="grid2"><div><label>Имя</label><input id="kcN" value="${esc(c.name)}"></div><div><label>Телефон</label><input id="kcP" value="${esc(c.phone||'')}"></div></div>
    <div class="grid2"><div><label>Дисконтная карта</label><input id="kcC" value="${esc(c.card||'')}"></div><div><label>День рождения</label><input id="kcB" type="date" value="${esc((c.birthday||'').slice(0,10))}"></div></div>
    <label>Заметка</label><input id="kcNo" value="${esc(c.note||'')}"><div class="muted" style="font-size:12.5px;margin-top:8px">Изменения уйдут на все кассы сети в течение минуты.</div>
    <div class="actions"><button onclick="closeM()">Отмена</button><button class="p" id="kcOk">Сохранить</button></div>`);
  const g=s=>m.querySelector(s).value;
  m.querySelector('#kcOk').onclick=async()=>{ try{ await api('/api/crm/customers/'+c.id,{method:'POST',body:{name:g('#kcN'),phone:g('#kcP'),card:g('#kcC'),birthday:g('#kcB'),note:g('#kcNo')}});
    m.remove(); toast('Сохранено','ok'); loadCrm(); custCard(c.id); }catch(e){ toast(e.message,'err'); } };
}
async function crmLevels(B){
  const r=await api('/api/crm/levels'), E=r.can_levels;
  const row=l=>`<div class="lvrow"><div><input data-f="name" value="${esc(l.name||'')}" placeholder="Название" ${E?'':'disabled'}></div><div><input data-f="min_spend" type="number" value="${l.min_spend??''}" placeholder="0" ${E?'':'disabled'}></div>
    <div><input data-f="discount_pct" type="number" step="0.5" value="${l.discount_pct??''}" placeholder="0" ${E?'':'disabled'}></div><div><input data-f="cashback_pct" type="number" step="0.5" value="${l.cashback_pct??''}" placeholder="0" ${E?'':'disabled'}></div>
    <div>${E?'<button class="ghost" title="Удалить" onclick="this.closest(\'.lvrow\').remove()">✕</button>':''}</div></div>`;
  B.innerHTML=`<div class="ag"><div class="card w8"><b>Уровни лояльности сети</b><p class="muted" style="margin:4px 0 12px;font-size:13px">Уровень присваивается автоматически по сумме покупок клиента во всех магазинах сети за последние 12 месяцев.
      Скидка уровня применяется на кассе сама, когда кассир выбирает клиента; кешбэк — начисляется бонусами (берётся больший из кешбэка уровня и общего кешбэка магазина).</p>
      <div class="lvrow muted" style="font-size:12px"><div>Название</div><div>От суммы за 12 мес., сум</div><div>Скидка, %</div><div>Кешбэк, %</div><div></div></div>
      <div id="lvRows">${r.levels.map(row).join('')}</div>
      ${E?`<div class="row" style="margin-top:10px;gap:8px"><button onclick="$('#lvRows').insertAdjacentHTML('beforeend',LVROW({}))">+ Уровень</button>${r.levels.length?'':'<button onclick="lvTemplate()">Заполнить примером</button>'}<span class="grow"></span><button class="p" onclick="lvSave()">Сохранить и пересчитать</button></div>`
        :'<div class="muted" style="margin-top:8px">Изменять уровни может владелец или директор сети.</div>'}</div>
    <div class="card w4"><b>Клиентов по уровням</b><div style="margin-top:6px">${hbars([...r.levels.map(l=>({n:l.name,v:r.counts[l.name]||0})),{n:'Без уровня',v:r.counts['']||0}],'v','n')}</div>
      ${r.levels.length?'':'<div class="muted" style="font-size:12.5px;margin-top:8px">Уровни не настроены — скидки и кешбэк по уровням не действуют.</div>'}</div></div>`;
  window.LVROW=row;
}
function lvTemplate(){ $('#lvRows').innerHTML=[{name:'Стандарт',min_spend:0,discount_pct:0,cashback_pct:1},{name:'Серебро',min_spend:3000000,discount_pct:0,cashback_pct:2},{name:'Золото',min_spend:10000000,discount_pct:2,cashback_pct:3},{name:'Платина',min_spend:25000000,discount_pct:3,cashback_pct:5}].map(LVROW).join('');
  toast('Пример заполнен — проверьте суммы и проценты, потом сохраните',''); }
async function lvSave(){
  const lv=[...$$('#lvRows .lvrow')].map(r=>Object.fromEntries([...r.querySelectorAll('[data-f]')].map(i=>[i.dataset.f,i.value.trim()]))).filter(x=>x.name);
  if(!confirm(lv.length?`Сохранить ${lv.length} уровн. и пересчитать уровни всех клиентов? Новые скидки начнут действовать на кассах в течение минуты.`:'Удалить все уровни? Скидки и кешбэк по уровням перестанут действовать.')) return;
  try{ await api('/api/crm/levels',{method:'POST',body:{levels:lv}}); toast('Уровни сохранены','ok'); loadCrm(); }catch(e){ toast(e.message,'err'); }
}
