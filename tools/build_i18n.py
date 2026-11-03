"""Собирает словарь RU→UZ из uz*.tsv и кладёт i18n.js в кассу (static/) и Центр (hq/templates/)."""
import json, glob, re
import os
S = os.path.dirname(os.path.abspath(__file__)) + '/'
keys = open(S + 'ru_keys.txt', encoding='utf-8').read().split('\n')
D = {}
for f in sorted(glob.glob(S + 'uz*.tsv')):
    for line in open(f, encoding='utf-8'):
        line = line.rstrip('\n')
        if not line.strip():
            continue
        i, uz = line.split('\t', 1)
        i = int(i)
        if uz.strip():
            D[keys[i]] = uz.strip()
# дополнительные целые фразы, которые часто собираются из кусков
EXTRA = {
    'Обзор сети': 'Tarmoq ko‘rinishi', 'AI-помощник': 'AI-yordamchi', 'Резервные копии': 'Zaxira nusxalar', 'Доступ в Центр': 'Markazga kirish',
    'Журнал действий': 'Amallar jurnali', 'Переезд данных': 'Ma’lumotlarni ko‘chirish', 'Поставки и перемещения': 'Yetkazib berish va ko‘chirishlar',
    'Персонал': 'Xodimlar', 'Клиенты': 'Mijozlar', 'Интеграции': 'Integratsiyalar', 'Финансы': 'Moliya', 'Аналитика': 'Tahlil',
    'Центр сети': 'Tarmoq markazi', 'Пароль': 'Parol', 'Логин': 'Login', 'Войти': 'Kirish', 'Выйти': 'Chiqish',
}
for k, v in EXTRA.items():
    D.setdefault(k, v)
# строки, найденные на экранах при обходе (collect_missing.py): «русский<TAB>узбекский»
for line in open(S + 'extra_uz.tsv', encoding='utf-8'):
    if '\t' in line:
        k, v = line.rstrip('\n').split('\t', 1)
        if k.strip() and v.strip():
            D[k.strip()] = v.strip()
missing = [k for k in keys if k not in D and re.search('[А-Яа-яЁё]{2,}', k)]
print('dict', len(D), 'of', len(keys), 'missing', len(missing), missing[:30])
rt = open(S + 'i18n_runtime.js', encoding='utf-8').read().replace('__DICT__', json.dumps(D, ensure_ascii=False, separators=(',', ':')))
for p in (S + '../static/i18n.js', S + '../hq/templates/i18n.js'):
    open(p, 'w', encoding='utf-8').write(rt)
print('size', len(rt.encode()) // 1024, 'KB')
