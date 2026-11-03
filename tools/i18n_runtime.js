// =====================================================================
//  Язык интерфейса: русский / oʻzbekcha (латиница). Общий для кассы и Центра.
//  Переводит текст страницы на лету (включая то, что появляется позже), placeholder/title,
//  а также confirm/prompt/alert. Выбор языка — кнопка RU/UZ в шапке, запоминается в браузере.
//  Словарь собирается из строк интерфейса (scripts: extract_ru.py → build_i18n.py).
// =====================================================================
(function(){
  const D = __DICT__;
  let L = 'ru';
  try { L = localStorage.getItem('lang') || document.documentElement.getAttribute('data-lang-default') || 'ru'; } catch(e) {}
  const CY = /[А-Яа-яЁё]/;
  const SPLIT = /(\s*[·|—•:;()«»"\[\]\/]\s*|\s*\d[\d\s.,%]*\s*|\s+[-–]\s+|\s*[,?!]\s+)/;
  const CORE = /[A-Za-zА-Яа-яЁёʻʼ‘’][\s\S]*[A-Za-zА-Яа-яЁёʻʼ‘’]|[A-Za-zА-Яа-яЁё]/;
  const cache = new Map();
  const up = s => s.charAt(0).toUpperCase() + s.slice(1), low = s => s.charAt(0).toLowerCase() + s.slice(1);
  function look(c){
    if (D[c]) return D[c];
    const f = c.charAt(0);
    if (f !== f.toLowerCase() && D[low(c)]) return up(D[low(c)]);
    if (f !== f.toUpperCase() && D[up(c)]) return low(D[up(c)]);
    return null;
  }
  function piece(p){
    const m = p.match(CORE);
    if (!m || !CY.test(m[0])) return p;
    const t = look(m[0]);
    return t ? p.replace(m[0], t) : p;
  }
  function tr(s){
    if (L !== 'uz' || !s || !CY.test(s)) return s;
    if (cache.has(s)) return cache.get(s);
    const lead = s.match(/^\s*/)[0], trail = s.match(/\s*$/)[0];
    const body = s.trim().replace(/\s+/g, ' ');
    let out;
    const m = body.match(CORE);
    if (m && look(m[0])) out = body.replace(m[0], look(m[0]));
    else out = body.split(SPLIT).map((p, i) => i % 2 ? p : piece(p)).join('');
    out = lead + out + trail;
    if (cache.size > 20000) cache.clear();
    cache.set(s, out);
    return out;
  }
  window.T = tr;     // для кода: T('Сохранить')
  const SKIP = {SCRIPT: 1, STYLE: 1, TEXTAREA: 1, NOSCRIPT: 1, CODE: 1, PRE: 1};
  const ATTRS = ['placeholder', 'title', 'aria-label'];
  let busy = false;
  function doText(n){
    if (n.__uz !== undefined && n.data === n.__uz && n.__lang === L) return;   // уже переведено нами на текущий язык
    if (n.__uz === undefined || n.data !== n.__uz) n.__ru = n.data;           // новый исходный текст от программы
    const t = L === 'uz' ? tr(n.__ru) : n.__ru;
    n.__uz = t; n.__lang = L;
    if (n.data !== t) n.data = t;
  }
  function doEl(el){
    for (const a of ATTRS) {
      if (!el.hasAttribute || !el.hasAttribute(a)) continue;
      const v = el.getAttribute(a), k = 'i18n' + a;
      if (el[k] === undefined || v !== el[k + 'Out']) el[k] = v;
      const t = L === 'uz' ? tr(el[k]) : el[k];
      el[k + 'Out'] = t;
      if (v !== t) el.setAttribute(a, t);
    }
    if (el.tagName === 'INPUT' && (el.type === 'button' || el.type === 'submit') && el.value) {
      if (el.i18nVal === undefined || el.value !== el.i18nValOut) el.i18nVal = el.value;
      el.i18nValOut = L === 'uz' ? tr(el.i18nVal) : el.i18nVal;
      el.value = el.i18nValOut;
    }
  }
  function walk(root){
    if (!root) return;
    if (root.nodeType === 3) { if (!(root.parentNode && SKIP[root.parentNode.nodeName])) doText(root); return; }
    if (root.nodeType !== 1 || SKIP[root.nodeName] || root.closest && root.closest('[data-noi18n]')) return;
    doEl(root);
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode: n => (n.nodeType === 1 && (SKIP[n.nodeName] || n.hasAttribute('data-noi18n'))) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
    let n;
    while ((n = w.nextNode())) n.nodeType === 3 ? doText(n) : doEl(n);
  }
  function apply(){ busy = true; try { walk(document.body); document.title = tr(document.__ruTitle || (document.__ruTitle = document.title)); } finally { busy = false; } }
  const pending = new Set(); let sched = false;
  const mo = new MutationObserver(ms => {
    if (busy) return;
    for (const m of ms) {
      if (m.type === 'childList') m.addedNodes.forEach(x => pending.add(x));
      else pending.add(m.target);
    }
    if (!sched) { sched = true; Promise.resolve().then(() => { sched = false; busy = true;
      try { pending.forEach(x => x.isConnected && walk(x)); } finally { pending.clear(); busy = false; } }); }
  });
  // диалоги браузера
  const _c = window.confirm, _p = window.prompt, _a = window.alert;
  window.confirm = m => _c.call(window, tr(String(m ?? '')));
  window.prompt = (m, d) => _p.call(window, tr(String(m ?? '')), d);
  window.alert = m => _a.call(window, tr(String(m ?? '')));
  // переключатель
  function setLang(l){
    L = l; try { localStorage.setItem('lang', l); } catch(e) {}
    document.documentElement.lang = l === 'uz' ? 'uz' : 'ru';
    cache.clear(); apply(); paint();
  }
  window.setLang = setLang;
  function paint(){
    document.querySelectorAll('.lang-sw button').forEach(b => b.classList.toggle('on', b.dataset.l === L));
  }
  function sw(){
    const d = document.createElement('div');
    d.className = 'lang-sw'; d.setAttribute('data-noi18n', '');
    d.innerHTML = '<button data-l="ru" title="Русский">RU</button><button data-l="uz" title="Oʻzbekcha">UZ</button>';
    d.querySelectorAll('button').forEach(b => b.onclick = e => { e.preventDefault(); setLang(b.dataset.l); });
    return d;
  }
  function mount(){
    const css = document.createElement('style');
    css.textContent = `.lang-sw{display:inline-flex;border:1px solid var(--line,#e5e7eb);border-radius:9px;overflow:hidden;background:#fff;flex:none}
      .lang-sw button{border:0;background:transparent;padding:5px 9px;font:600 12px/1 inherit;color:#667085;cursor:pointer;border-radius:0;min-height:0;box-shadow:none}
      .lang-sw button.on{background:var(--brand,#1d4ed8);color:#fff} .lang-sw.fixed{position:fixed;top:14px;right:14px;z-index:9999}`;
    document.head.appendChild(css);
    const hdr = document.querySelector('header .right');
    if (hdr) hdr.prepend(sw());
    const fx = sw(); fx.classList.add('fixed');
    document.body.appendChild(fx);
    // плавающий переключатель — только пока открыт экран входа
    const vis = () => { const lg = document.querySelector('#login, #pinScreen, .login, #lock');
      fx.style.display = lg && !lg.classList.contains('hidden') && getComputedStyle(lg).display !== 'none' && getComputedStyle(lg).visibility !== 'hidden' ? '' : 'none'; };
    setInterval(vis, 700); vis();
    document.documentElement.lang = L === 'uz' ? 'uz' : 'ru';
    paint(); apply();
    mo.observe(document.body, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
})();
