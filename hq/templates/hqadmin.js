// =====================================================================
//  Центр сети: пользователи и роли, права в интерфейсе, магазины
//  (карточка, план продаж, сравнение филиалов), журнал действий.
// =====================================================================
let ME=null, ROLES=null;
const NAV_PERM={dash:'dash',stores:'stores.view',sales:'sales',catalog:'catalog.view',stock:'stock',docs:'docs.view',migrate:'migrate',users:'users',audit:'audit'};
Object.assign(TITLES,{users:'Сотрудники и доступ',audit:'Журнал действий'});
const can=p=>!!ME&&ME.perms.includes(p);
const dt=s=>s?s.slice(0,16).replace('T',' '):'—';

boot = async function(){
  let me; try{ me=await api('/api/me'); }catch(e){ return; }
  ME=me.user;
  $('#login').classList.add('hidden'); $('#app').classList.remove('hidden');
  $('#pwWarn').innerHTML=me.default_password?'<div class="card" style="background:#fef3f2;border-color:#fecdca;margin-bottom:12px">⚠️ Вход по общему паролю по умолчанию <b>admin</b>. Задайте переменную <code>HQ_PASSWORD</code> на сервере и создайте сотрудникам личные логины в «Сотрудники и доступ».</div>':'';
  $('#meName').textContent=ME.name; $('#meRole').textContent=ME.role_title+(ME.store_names&&ME.store_names.length?' · '+ME.store_names.map(s=>s.name).join(', '):'');
  $$('#nav button').forEach(b=>b.classList.toggle('hidden',!can(NAV_PERM[b.dataset.v])));
  $$('#nav .grp').forEach(g=>{ let n=g.nextElementSibling, any=false; while(n&&!n.classList.contains('grp')){ if(!n.classList.contains('hidden')) any=true; n=n.nextElementSibling; } g.classList.toggle('hidden',!any); });
  $('#bellBtn').classList.toggle('hidden',!can('dash'));
  STORES=can('stores.view')||can('docs.view')||can('sales')?await api('/api/stores').catch(()=>[]):[];
  const first=Object.keys(NAV_PERM).find(v=>can(NAV_PERM[v]));
  if(!first){ $('main').innerHTML='<div class="card">У вашей роли нет доступа к разделам Центра. Обратитесь к владельцу сети.</div>'; return; }
  show(first);
  // кнопки изменения — только при наличии прав
  document.body.classList.toggle('ro-catalog',!can('catalog.edit')); document.body.classList.toggle('ro-docs',!can('docs.create'));
};
document.head.insertAdjacentHTML('beforeend',`<style>
  body.ro-catalog #v-catalog button.p, body.ro-catalog #v-catalog [onclick*="impF"], body.ro-catalog #catBody button{display:none}
  body.ro-docs #v-docs button.p, body.ro-docs #v-docs button.b{display:none}
  .stg{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
  .stc{cursor:pointer;transition:box-shadow .15s,border-color .15s} .stc:hover{border-color:#c7d2fe;box-shadow:0 6px 18px rgba(16,24,40,.08)}
  .stc .big{font-size:24px;font-weight:650;letter-spacing:-.02em;margin:8px 0 2px}
  .pb{height:8px;background:#f1f3f6;border-radius:4px;overflow:hidden;margin-top:6px} .pb i{display:block;height:100%;border-radius:4px}
  .chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:99px;padding:5px 10px;margin:3px;cursor:pointer;font-size:13px;user-select:none}
  .chip input{width:auto;margin:0} .chip:has(input:checked){border-color:var(--brand);background:var(--brand-50);color:var(--brand-d)}
  .rl{padding:10px;border:1px solid var(--line);border-radius:10px;cursor:pointer;margin-bottom:6px} .rl:has(input:checked){border-color:var(--brand);background:var(--brand-50)}
  .rl input{width:auto;margin-right:8px}
</style>`);
show = function(v){
  if(NAV_PERM[v]&&!can(NAV_PERM[v])) return toast('Нет доступа к разделу','err');
  $$('.view').forEach(x=>x.classList.toggle('on',x.id==='v-'+v)); $$('#nav button').forEach(b=>b.classList.toggle('on',b.dataset.v===v));
  $('#pageTitle').textContent=TITLES[v]||'';
  ({dash:loadDash,docs:loadDocs,stock:loadStock,catalog:loadCat,sales:initSales,stores:loadStores,migrate:loadMig,users:loadUsers,audit:loadAudit})[v]?.();
};
const planColor=p=>p==null?'#cbd5e1':p>=100?'var(--green)':p<70?'var(--amber)':'var(--brand)';

// ---------------------------------------------------------------- магазины
let ST_TAB='list';
$$('#stTabs button').forEach(b=>b.onclick=()=>{ ST_TAB=b.dataset.t; $$('#stTabs button').forEach(x=>x.classList.toggle('on',x===b)); loadStores(); });
loadStores = async function(){
  $('#hqUrl').textContent=location.origin;
  $('#stAdd').classList.toggle('hidden',!can('stores.edit')); $('#stHow').classList.toggle('hidden',!can('stores.edit'));
  $('#stList').classList.toggle('hidden',ST_TAB!=='list'); $('#stCmp').classList.toggle('hidden',ST_TAB!=='cmp'); $('#stCmpBar').classList.toggle('hidden',ST_TAB!=='cmp');
  if(ST_TAB==='cmp') return loadCompare();
  const d=new Date(), m0=new Date(d.getFullYear(),d.getMonth(),1).toLocaleDateString('sv-SE'), m1=new Date(d.getFullYear(),d.getMonth()+1,0).toLocaleDateString('sv-SE');
  const [rows, cmp]=await Promise.all([api('/api/stores'), api(`/api/stores/compare?from=${m0}&to=${m1}`)]); STORES=rows;
  const M=Object.fromEntries(cmp.items.map(x=>[x.id,x]));
  $('#stList').innerHTML=`<div class="stg">${rows.map(s=>{ const m=M[s.id]||{}; const p=m.plan_pct;
    return `<div class="card stc" onclick="storeCard(${s.id})" style="${s.active?'':'opacity:.6'}">
      <div class="row"><span class="dot ${s.online?'on':''}"></span><b class="grow ell" style="font-size:15px">${esc(s.name)}</b>${s.active?'':'<span class="badge">отключён</span>'}<span class="muted" style="font-size:12px">${s.last_seen?s.last_seen.slice(11,16):''}</span></div>
      <div class="muted ell" style="font-size:12.5px;margin-top:2px">${esc([s.city,s.address].filter(Boolean).join(', ')||'адрес не указан')}</div>
      <div class="big">${fmt(m.net||0)}</div><div class="muted" style="font-size:12.5px">выручка за месяц · ${m.checks||0} чек. · ср. ${fmt(m.avg||0)}</div>
      <div class="row" style="margin-top:10px;font-size:12.5px"><span class="grow">План месяца</span><b style="color:${planColor(p)}">${p==null?'не задан':p+'%'}</b>${m.plan?`<span class="muted">из ${fmt(m.plan)}</span>`:''}</div>
      <div class="pb"><i style="width:${Math.min(100,p||0)}%;background:${planColor(p)}"></i></div>
      <div class="row" style="margin-top:10px;font-size:12.5px;color:var(--text-2)"><span class="grow">👤 ${esc(s.manager_name||'управляющий не назначен')}</span><span>${s.employees||0} сотр. · ${s.registers||1} касс${(s.registers||1)>1?'ы':'а'}</span></div>
    </div>`; }).join('')||'<div class="card muted">Магазинов нет</div>'}</div>`;
};
async function loadCompare(){
  if(!$('#cF').value){ const d=new Date(); $('#cF').value=new Date(d.getFullYear(),d.getMonth(),1).toLocaleDateString('sv-SE'); $('#cT').value=today(); }
  const r=await api(`/api/stores/compare?from=${$('#cF').value}&to=${$('#cT').value}`); const fin=r.finance;
  const best=k=>Math.max(...r.items.map(x=>x[k]||0));
  const cell=(x,k,f=fmt)=>`<td class="n" style="${x[k]&&x[k]===best(k)&&r.items.length>1?'font-weight:700;color:var(--green)':''}">${x[k]==null?'—':f(x[k])}</td>`;
  $('#stCmp').innerHTML=`<div class="card"><div class="muted" style="margin-bottom:8px">Период ${r.period[0]} — ${r.period[1]} (${r.days} дн.), сравнение с ${r.prev_period[0]} — ${r.prev_period[1]}. Зелёным — лучший показатель.</div>
    <div style="overflow:auto"><table><thead><tr><th>#</th><th>Магазин</th><th class="n">Выручка</th><th class="n">Доля сети</th><th class="n">К прошлому</th><th class="n">План</th><th class="n">Выполнение</th><th class="n">Чеков</th><th class="n">Средний чек</th><th class="n">Товаров в чеке</th><th class="n">В день</th>
      ${fin?'<th class="n">Прибыль</th><th class="n">Маржа</th>':''}<th class="n">Возвраты</th><th class="n">Скидки</th><th class="n">Остаток</th><th class="n">На м²</th></tr></thead><tbody>
    ${r.items.map(x=>`<tr style="cursor:pointer" onclick="storeCard(${x.id})"><td><span class="badge">${x.rank}</span></td><td><b>${esc(x.name)}</b></td>${cell(x,'net')}<td class="n">${x.share}%</td>
      <td class="n">${x.chg==null?'<span class="muted">—</span>':`<span class="${x.chg>=0?'green':'red'}">${x.chg>=0?'↑':'↓'} ${Math.abs(x.chg)}%</span>`}</td><td class="n">${x.plan?fmt(x.plan):'—'}</td>
      <td class="n"><b style="color:${planColor(x.plan_pct)}">${x.plan_pct==null?'—':x.plan_pct+'%'}</b></td>${cell(x,'checks',v=>v)}${cell(x,'avg')}${cell(x,'items_per_check',v=>v)}${cell(x,'per_day')}
      ${fin?cell(x,'profit')+`<td class="n">${x.margin}%</td>`:''}<td class="n ${x.refunds?'red':''}">${fmt(x.refunds)}</td><td class="n">${fmt(x.discount)}</td><td class="n">${fmt(x.stock_value)}</td><td class="n">${x.per_m2==null?'—':fmt(x.per_m2)}</td></tr>`).join('')||'<tr><td colspan="17" class="muted">Нет магазинов</td></tr>'}
    </tbody></table></div></div>`;
}
async function storeCard(id){
  const r=await api(`/api/stores/${id}/card`); const s=r.store, P=r.plan, fin=r.finance;
  const K=(l,v,sub='',cls='')=>`<div class="card kpi" style="padding:12px"><div class="l">${l}</div><div class="v ${cls}" style="font-size:19px">${v}</div>${sub?`<div class="muted" style="font-size:12px">${sub}</div>`:''}</div>`;
  const H=r.history, mx=Math.max(1,...H.map(h=>Math.max(h.net,h.plan||0)));
  const W=880, Hh=170, bw=W/H.length;
  const chart=`<svg viewBox="0 0 ${W} ${Hh+20}" width="100%" class="ch">${H.map((h,i)=>{ const y=Hh-h.net/mx*Hh, py=Hh-(h.plan||0)/mx*Hh;
      return `<g><rect x="${i*bw+bw*.2}" y="${y}" width="${bw*.6}" height="${Hh-y}" rx="4" fill="${i===H.length-1?'var(--brand)':'#bfdbfe'}"><title>${h.label}: ${fmt(h.net)}${h.plan?' · план '+fmt(h.plan)+' ('+h.pct+'%)':''}${fin&&h.profit!=null?' · прибыль '+fmt(h.profit):''}</title></rect>
      ${h.plan?`<line x1="${i*bw+bw*.12}" x2="${i*bw+bw*.88}" y1="${py}" y2="${py}" stroke="#0b0f19" stroke-width="2" stroke-dasharray="3 3"/>`:''}
      <text x="${i*bw+bw/2}" y="${Hh+16}" text-anchor="middle" style="font-size:14px">${h.label}</text></g>`; }).join('')}</svg>`;
  const nm=k=>{ const d=new Date(); d.setDate(1); d.setMonth(d.getMonth()+k); return d.toLocaleDateString('sv-SE').slice(0,7); };
  const planOf=ym=>(H.find(h=>h.month===ym)||{}).plan||'';
  const m=modal(`<div class="row"><span class="dot ${s.online?'on':''}"></span><h3 class="grow" style="margin:0">${esc(s.name)}</h3>${s.active?'':'<span class="badge">отключён</span>'}
      ${r.can_edit?`<button onclick='closeM();editStore(${JSON.stringify(s).replace(/'/g,"&#39;")})'>Изменить</button><button onclick="storeToken(${s.id})">Токен кассы</button>`:''}<button onclick="closeM()">✕</button></div>
    <div class="muted" style="margin:4px 0 12px">${esc([s.city,s.address].filter(Boolean).join(', ')||'')}${s.phone?' · '+esc(s.phone):''} · управляющий: <b style="color:var(--text)">${esc(s.manager_name||'не назначен')}</b>${s.manager_phone?' '+esc(s.manager_phone):''}
      · ${r.staff.length} сотр. · ${s.registers||1} касс${(s.registers||1)>1?'ы':'а'}${s.area?' · '+fq(s.area)+' м²':''}${s.opened_on?' · открыт '+s.opened_on:''} · связь: ${s.online?'<span class="green">онлайн</span>':(s.last_seen||'не подключался')}</div>
    <div class="kpis" style="grid-template-columns:repeat(6,minmax(0,1fr))">${K('Сегодня',fmt(r.today.net),r.today.checks+' чеков')}${K('За месяц',fmt(r.month.net),r.month.checks+' чеков')}${K('Средний чек',fmt(r.month.avg),r.month.items_per_check+' тов. в чеке')}
      ${fin?K('Валовая прибыль',fmt(r.month.profit),'маржа '+r.month.margin+'%','green'):''}${fin?K('Рентабельность запаса',r.rentability.stock_roi_30==null?'—':r.rentability.stock_roi_30+'%','прибыль 30 дн. / запас'):''}
      ${K('Товарный запас',fmt(r.stock.value),r.stock.skus+' SKU · нулевых '+r.stock.zero)}${r.rentability.revenue_per_m2?K('Выручка на м²',fmt(r.rentability.revenue_per_m2),'за месяц'):''}</div>
    <div class="grid2" style="margin-top:12px;grid-template-columns:1fr 1.6fr">
      <div class="card"><div class="row"><b class="grow">План продаж · ${P.month}</b><b style="color:${planColor(P.pct)}">${P.pct==null?'не задан':P.pct+'%'}</b></div>
        <div class="pb" style="height:10px"><i style="width:${Math.min(100,P.pct||0)}%;background:${planColor(P.pct)}"></i></div>
        <div style="font-size:13px;margin-top:10px;line-height:1.8">Факт: <b>${fmt(P.fact)}</b>${P.plan?` из <b>${fmt(P.plan)}</b>`:''}<br>
          Прогноз на конец месяца: <b>${fmt(P.forecast)}</b>${P.forecast_pct!=null?` (<span style="color:${planColor(P.forecast_pct)}">${P.forecast_pct}%</span>)`:''}<br>
          ${P.need_per_day?`Чтобы выполнить план: <b>${fmt(P.need_per_day)}</b> в день, осталось ${P.days_left} дн.`:''}</div>
        ${r.can_plan?`<div style="margin-top:10px"><b style="font-size:13px">Установить план</b><div class="grid3" style="gap:0 8px">${[0,1,2].map(k=>`<div><label>${nm(k)}</label><input type="number" data-pm="${nm(k)}" value="${planOf(nm(k))}"></div>`).join('')}</div>
          <button class="p" style="margin-top:8px;width:100%" id="spSave">Сохранить план</button></div>`:''}</div>
      <div class="card"><b>12 месяцев</b> <span class="muted" style="font-size:12px">— выручка; пунктир — план</span>${chart}</div>
    </div>
    <div class="grid2" style="margin-top:12px">
      <div class="card"><b>Сотрудники с доступом</b><table style="margin-top:6px"><tbody>${r.staff.map(u=>`<tr><td>${esc(u.name)}<div class="muted" style="font-size:11.5px">${esc(u.position||'')}</div></td><td>${esc(u.role_title)}</td><td class="muted">${esc(u.phone||'')}</td></tr>`).join('')||'<tr><td class="muted">Нет — добавьте в «Сотрудники и доступ»</td></tr>'}</tbody></table></div>
      <div class="card"><b>История работы</b><div style="max-height:220px;overflow:auto"><table style="margin-top:6px"><tbody>${r.log.map(l=>`<tr><td class="muted" style="white-space:nowrap">${dt(l.created_at)}</td><td>${esc(l.action)}<div class="muted" style="font-size:11.5px">${esc(l.details||'')}</div></td><td class="muted">${esc(l.user_name||'')}</td></tr>`).join('')||'<tr><td class="muted">Записей нет</td></tr>'}</tbody></table></div></div>
    </div>`,'wide');
  m.querySelector('.box').style.width='1100px';
  m.querySelector('#spSave')?.addEventListener('click',async()=>{ const plans=[...m.querySelectorAll('[data-pm]')].filter(i=>i.value!=='').map(i=>({month:i.dataset.pm,amount:i.value}));
    try{ await api(`/api/stores/${id}/plan`,{method:'POST',body:{plans}}); toast('План сохранён','ok'); m.remove(); storeCard(id); if($('#v-stores').classList.contains('on')) loadStores(); }catch(e){ toast(e.message,'err'); } });
}
async function storeToken(id){
  const s=(await api('/api/stores')).find(x=>x.id===id); if(!s||!s.token) return;
  const m=modal(`<h3>Токен кассы: ${esc(s.name)}</h3><p class="muted">Вставьте его на кассе: Настройки → Сеть магазинов. Адрес Центра: <code>${location.origin}</code></p>
    <code style="display:block;padding:10px;font-size:13px">${esc(s.token)}</code>
    <div class="actions"><button class="r" id="tkNew" style="margin-right:auto">Выпустить новый</button><button onclick="closeM()">Закрыть</button><button class="p" id="tkCp">Скопировать</button></div>`);
  m.querySelector('#tkCp').onclick=()=>copyTok(s.token);
  m.querySelector('#tkNew').onclick=async()=>{ if(!confirm('Касса с текущим токеном отключится до ввода нового. Продолжить?')) return; await api(`/api/stores/${id}/token`,{method:'POST'}); m.remove(); storeToken(id); };
}
editStore = async function(s){ s=s||{active:1,registers:1};
  const users=can('users')?await api('/api/users').catch(()=>[]):[];
  const mgr=users.filter(u=>u.active&&['store_manager','regional','director','owner'].includes(u.role));
  const f=(id,l,v,t='text')=>`<div><label>${l}</label><input id="${id}" type="${t}" value="${esc(v??'')}" step="any"></div>`;
  const m=modal(`<h3>${s.id?'Магазин':'Новый магазин'}</h3>
    ${f('esN','Название',s.name)}<div class="grid2">${f('esC','Город',s.city)}${f('esA','Адрес',s.address)}</div>
    <div class="grid3">${f('esP','Телефон магазина',s.phone)}${f('esR','Количество касс',s.registers||1,'number')}${f('esAr','Площадь, м²',s.area||'','number')}</div>
    <div class="grid2"><div><label>Управляющий</label><select id="esM"><option value="">— не назначен —</option>${mgr.map(u=>`<option value="${u.id}" ${u.id==s.manager_id?'selected':''}>${esc(u.name)} · ${esc(u.role_title)}</option>`).join('')}</select>
      ${can('users')&&!mgr.length?'<div class="muted" style="font-size:12px;margin-top:4px">Сначала создайте управляющего в «Сотрудники и доступ»</div>':''}</div>${f('esO','Дата открытия',s.opened_on,'date')}</div>
    ${s.id?`<label>Статус</label><select id="esS"><option value="1" ${s.active?'selected':''}>Работает</option><option value="0" ${!s.active?'selected':''}>Деактивирован — касса перестанет синхронизироваться, история сохранится</option></select>`:''}
    <div class="actions"><button onclick="closeM()">Отмена</button><button class="p" id="esOk">Сохранить</button></div>`);
  const g=id=>m.querySelector(id)?.value;
  m.querySelector('#esOk').onclick=async()=>{ try{ await api('/api/stores',{method:'POST',body:{id:s.id,name:g('#esN'),city:g('#esC'),address:g('#esA'),phone:g('#esP'),registers:g('#esR'),area:g('#esAr'),manager_id:g('#esM'),opened_on:g('#esO'),active:g('#esS')??1}});
    m.remove(); toast('Сохранено','ok'); loadStores(); }catch(e){ toast(e.message,'err'); } };
};

// ---------------------------------------------------------------- сотрудники и роли
async function loadUsers(){
  if(!ROLES) ROLES=await api('/api/roles'); STORES=await api('/api/stores');
  const rows=await api('/api/users');
  $('#usBody').innerHTML=rows.map(u=>`<tr style="${u.active?'':'opacity:.55'}"><td><b>${esc(u.name)}</b><div class="muted" style="font-size:12px">${esc([u.position,u.phone].filter(Boolean).join(' · '))}</div></td>
    <td>${esc(u.login)}</td><td><span class="badge blue">${esc(u.role_title)}</span></td>
    <td style="font-size:13px">${(ROLES.roles.find(r=>r.key===u.role)||{}).all_stores?'<span class="muted">вся сеть</span>':u.stores.map(s=>esc(s.name)).join(', ')||'<span class="red">не назначены</span>'}</td>
    <td class="muted">${dt(u.last_login)}</td><td>${u.active?'<span class="badge ok">активен</span>':'<span class="badge">отключён</span>'}</td>
    <td><button class="ghost" onclick='editUser(${JSON.stringify(u).replace(/'/g,"&#39;")})'>Изм.</button></td></tr>`).join('')||'<tr><td colspan="7" class="muted">Сотрудников нет. Сейчас вход только по общему паролю — создайте личные логины.</td></tr>';
}
async function editUser(u){
  if(!ROLES) ROLES=await api('/api/roles'); u=u||{active:1,role:'store_manager',stores:[]};
  const sel=new Set((u.stores||[]).map(s=>s.id));
  const avail=ROLES.roles.filter(r=>!(r.key==='superadmin'&&ME.role!=='superadmin')&&!(r.key==='owner'&&!['superadmin','owner'].includes(ME.role)));
  const m=modal(`<h3>${u.id?'Сотрудник':'Новый сотрудник'}</h3>
    <div class="grid2"><div><label>ФИО</label><input id="euN" value="${esc(u.name||'')}" autofocus></div><div><label>Должность</label><input id="euP" value="${esc(u.position||'')}" placeholder="Напр. старший продавец"></div></div>
    <div class="grid2"><div><label>Логин для входа (телефон или почта)</label><input id="euL" value="${esc(u.login||'')}" placeholder="998901234567"></div><div><label>Телефон</label><input id="euT" value="${esc(u.phone||'')}"></div></div>
    <label>${u.id?'Новый пароль (пусто — не менять)':'Пароль (не короче 6 символов)'}</label><input id="euW" type="text" autocomplete="new-password">
    <div class="row" style="margin-top:10px"><label class="grow" style="margin:0">Роль</label><a href="#" onclick="showRoles();return false" style="font-size:12px">что может каждая роль</a></div><div id="euR" style="display:grid;grid-template-columns:1fr 1fr;gap:0 8px;margin-top:4px">${avail.map(r=>`<label class="rl" style="display:flex;margin:0 0 6px;color:var(--text)"><input type="radio" name="role" value="${r.key}" ${r.key===u.role?'checked':''}><span><b>${esc(r.title)}</b> <span class="muted">— ${esc(r.desc)}</span></span></label>`).join('')}</div>
    <div id="euSW"><label>Доступ к магазинам</label><div id="euS">${STORES.map(s=>`<label class="chip"><input type="checkbox" value="${s.id}" ${sel.has(s.id)?'checked':''}>${esc(s.name)}</label>`).join('')||'<span class="muted">Сначала создайте магазины</span>'}</div>
      <div class="muted" style="font-size:12px" id="euSH"></div></div>
    ${u.id?`<label>Статус</label><select id="euA"><option value="1" ${u.active?'selected':''}>Активен</option><option value="0" ${!u.active?'selected':''}>Отключён — не сможет войти</option></select>`:''}
    <div class="actions"><button onclick="closeM()">Отмена</button><button class="p" id="euOk">Сохранить</button></div>`,'wide');
  const role=()=>m.querySelector('input[name=role]:checked')?.value;
  const upd=()=>{ const r=ROLES.roles.find(x=>x.key===role()); m.querySelector('#euSW').classList.toggle('hidden',!r||r.all_stores);
    m.querySelector('#euSH').textContent=['store_manager','cashier'].includes(role())?'Один магазин':role()==='regional'?'Отметьте магазины региона':'Можно несколько'; };
  m.querySelectorAll('input[name=role]').forEach(i=>i.onchange=upd); upd();
  m.querySelector('#euOk').onclick=async()=>{ try{ await api('/api/users',{method:'POST',body:{id:u.id,name:m.querySelector('#euN').value,position:m.querySelector('#euP').value,login:m.querySelector('#euL').value,
      phone:m.querySelector('#euT').value,password:m.querySelector('#euW').value,role:role(),active:m.querySelector('#euA')?.value??1,stores:[...m.querySelectorAll('#euS input:checked')].map(i=>+i.value)}});
    m.remove(); toast('Сохранено. Сотрудник входит в Центр по логину и паролю.','ok'); loadUsers(); }catch(e){ toast(e.message,'err'); } };
}
async function showRoles(){
  if(!ROLES) ROLES=await api('/api/roles'); const P=Object.keys(ROLES.perms);
  modal(`<h3>Роли и права</h3><div style="overflow:auto"><table><thead><tr><th>Право</th>${ROLES.roles.map(r=>`<th class="n" style="white-space:normal;min-width:80px">${esc(r.title)}</th>`).join('')}</tr></thead><tbody>
    ${P.map(p=>`<tr><td>${esc(ROLES.perms[p])}</td>${ROLES.roles.map(r=>`<td class="n">${r.perms.includes(p)?'<span class="green">✓</span>':'<span class="muted">—</span>'}</td>`).join('')}</tr>`).join('')}
    <tr><td><b>Магазины</b></td>${ROLES.roles.map(r=>`<td class="n" style="font-size:12px">${r.all_stores?'вся сеть':'назначенные'}</td>`).join('')}</tr></tbody></table></div>
    <p class="muted" style="font-size:12.5px">Сотрудник с привязкой к магазинам не видит выручку, остатки, накладные и журнал других филиалов. Без права «Прибыль» скрыты себестоимость, прибыль и маржа.</p>
    <div class="actions"><button class="p" onclick="closeM()">Понятно</button></div>`,'wide');
}
async function myAccount(){
  const m=modal(`<h3>${esc(ME.name)}</h3><div class="muted">${esc(ME.role_title)}${ME.login?' · логин '+esc(ME.login):''}</div>
    ${ME.id?`<label>Текущий пароль</label><input id="maO" type="password"><label>Новый пароль</label><input id="maN" type="password">`:'<p class="muted">Это вход по общему паролю Super Admin. Его пароль задаётся переменной HQ_PASSWORD на сервере.</p>'}
    <div class="actions"><button onclick="logout()" style="margin-right:auto">Выйти</button><button onclick="closeM()">Закрыть</button>${ME.id?'<button class="p" id="maOk">Сменить пароль</button>':''}</div>`);
  m.querySelector('#maOk')?.addEventListener('click',async()=>{ try{ await api('/api/me/password',{method:'POST',body:{old:m.querySelector('#maO').value,new:m.querySelector('#maN').value}}); m.remove(); toast('Пароль изменён','ok'); }catch(e){ toast(e.message,'err'); } });
}

// ---------------------------------------------------------------- журнал
async function loadAudit(){
  if(!$('#auSt').options.length) $('#auSt').innerHTML='<option value="">Все магазины и Центр</option>'+STORES.map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('');
  const rows=await api('/api/audit'+($('#auSt').value?'?store='+$('#auSt').value:'')); const SN=Object.fromEntries(STORES.map(s=>[s.id,s.name]));
  $('#auBody').innerHTML=rows.map(r=>`<tr><td class="muted" style="white-space:nowrap">${dt(r.created_at)}</td><td>${esc(r.user_name||'')}</td><td><b>${esc(r.action)}</b></td><td style="font-size:13px">${esc(r.details||'')}</td><td>${esc(SN[r.store_id]||'')}</td></tr>`).join('')||'<tr><td colspan="5" class="muted">Записей нет</td></tr>';
}
