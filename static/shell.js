// =====================================================================
//  Оболочка: боковая панель с разделами, заголовок страницы,
//  сворачивание меню (касса — автоматически на весь экран).
// =====================================================================
buildMoreMenu = function(){};          // старое меню «Ещё» больше не нужно
const TITLES={dash:'Обзор бизнеса',pos:'Касса',sales:'Чеки и возвраты',shifts:'Смена и касса',customers:'Клиенты',promos:'Акции и цены',
  products:'Товары',receive:'Поступления',purchase:'Приход товара',inventory:'Ревизия',expiry:'Сроки годности',suppliers:'Поставщики',
  reports:'Отчёты и аналитика',audit:'Журнал действий',settings:'Настройки'};
let MINI_PREF=null;
try{ MINI_PREF=localStorage.getItem('kassa_mini'); }catch(e){}
function setMini(on){ $('#app').classList.toggle('mini',on); }
$('#sideTgl').onclick=()=>{ const on=!$('#app').classList.contains('mini'); setMini(on); MINI_PREF=on?'1':'0'; try{ localStorage.setItem('kassa_mini',MINI_PREF); }catch(e){} };
function sideGroups(){ // прячем заголовок группы, если в ней нет доступных пунктов
  $$('#nav .grp').forEach(g=>{ let n=g.nextElementSibling, any=false;
    while(n && !n.classList.contains('grp')){ if(!n.classList.contains('hidden')) any=true; n=n.nextElementSibling; }
    g.classList.toggle('hidden',!any); });
}
const _showS = show;
show = function(v){
  if(FIRST_SHOW && ME){ FIRST_SHOW=false; if(v==='pos' && ME.role==='admin') v='dash'; }
  _showS(v);
  $('#pageTitle').textContent=TITLES[v]||'';
  // на кассе меню сворачивается само, чтобы чек был шире; в остальных разделах — как выбрал пользователь
  setMini(v==='pos' ? MINI_PREF!=='0' : MINI_PREF==='1');
  sideGroups();
  if(ME){ $('#userAva').textContent=(ME.name||'?').trim()[0].toUpperCase(); $('#userRole').textContent=ME.role==='admin'?'Администратор':'Кассир';
    $('#storeMark').textContent=((SET.store_name||'К').trim()[0]||'К').toUpperCase(); }
};
const _rsl = renderShiftLbl;
renderShiftLbl = function(){
  _rsl();
  $('#shiftLbl').innerHTML = SHIFT ? `<span style="color:var(--green)">●</span> Смена №${SHIFT.id} · с ${SHIFT.opened_at.slice(11,16)}` : '<span style="color:var(--amber)">●</span> <b>Смена закрыта</b>';
  $('#shiftLbl').style.cursor='pointer'; $('#shiftLbl').onclick=()=>show('shifts');
};
// администратор после входа попадает в «Обзор», кассир — сразу на кассу
let FIRST_SHOW=true;
const _bootS = boot;
boot = async function(){ FIRST_SHOW=true; return _bootS(); };
