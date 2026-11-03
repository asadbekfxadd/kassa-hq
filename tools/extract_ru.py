"""Собирает русские строки интерфейса (кассы и Центра) для словаря перевода."""
import re, glob, json, sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
files = sorted(glob.glob(''+ROOT+'/templates/*.html') + glob.glob(''+ROOT+'/static/*.js') +
               glob.glob(''+ROOT+'/hq/templates/*.html') + glob.glob(''+ROOT+'/hq/templates/*.js'))
CY = re.compile(r'[А-Яа-яЁё]')
SPLIT = re.compile(r'\s*[·|—•:;()«»"\[\]/]\s*|\s*\d[\d\s.,%]*\s*|\s+[-–]\s+|\s*[,?!]\s+')
CORE = re.compile(r'[A-Za-zА-Яа-яЁёʻʼ‘’][\s\S]*[A-Za-zА-Яа-яЁёʻʼ‘’]|[A-Za-zА-Яа-яЁё]')
out = {}
for f in files:
    s = open(f, encoding='utf-8').read()
    s = re.sub(r'<style.*?</style>', '', s, flags=re.S)
    cands = []
    # текст между тегами
    cands += re.findall(r'>([^<>]*?[А-Яа-яЁё][^<>]*?)<', s)
    # атрибуты
    cands += re.findall(r'(?:placeholder|title|aria-label|data-t)="([^"]*?[А-Яа-яЁё][^"]*?)"', s)
    # строки JS: '...', "...", `...` (статичные куски между ${})
    for m in re.finditer(r"'((?:[^'\\\n]|\\.)*?)'|\"((?:[^\"\\\n]|\\.)*?)\"|`((?:[^`\\]|\\.)*?)`", s):
        v = m.group(1) or m.group(2) or m.group(3) or ''
        if not CY.search(v):
            continue
        for part in re.split(r'\$\{[^}]*\}', v):
            for p in re.split(r'<[^>]+>', part):
                cands.append(p)
    for c0 in cands:
        c0 = c0.replace('\\n', ' ').replace('&nbsp;', ' ')
        if re.search(r'[{};=]|\$\(|=>', c0):
            continue
        # те же правила, что и в i18n.js: куски между числами и разделителями, ядро — от первой до последней буквы
        for piece in SPLIT.split(re.sub(r'\s+', ' ', c0)):
            m = CORE.search(piece or '')
            if not m:
                continue
            c = m.group(0)
            if not CY.search(c) or len(c) > 220:
                continue
            out.setdefault(c, set()).add(f.split('/')[-1])
keys = sorted(out, key=lambda k: (len(k), k))
json.dump({k: sorted(out[k]) for k in keys}, open(sys.argv[1], 'w'), ensure_ascii=False, indent=0)
print(len(keys), sum(len(k) for k in keys))
