#!/usr/bin/env python3
"""Файл платёжек для импорта в Точку (формат 1С, 1CClientBankExchange 1.03).

Плательщик всегда ООО «Гоголь Школа», счёт в Точке. Вход — JSON:

{
  "date": "10.11.2026",                  # дата платёжек, по умолчанию сегодня
  "payments": [{
    "name": "ООО \"ЯНДЕКС.ТАКСИ\"",      # получатель как в счёте
    "inn": "7704340310", "kpp": "997750001",   # kpp у ИП/физлица — пусто; inn у физлица можно не давать
    "account": "40702810238000094474", "bik": "044525225",
    "korr": "30101810400000000225", "bank": "ПАО Сбербанк", "bank_city": "Г. МОСКВА",
    "amount": "18666.00",
    "purpose": "Оплата по счету № ЛСТ-2429502807-1 в т.ч. НДС 22% 3366.00",
    "number": 1                            # необязательно, Точка всё равно перенумерует
  }]
}

Запуск: python3 make_1c.py input.json out.txt
Перед записью проверяет контрольные цифры ИНН и ключ счёта по БИК — опечатка
в реквизитах остановит генерацию, а не уйдёт в банк.
"""
import json
import sys
from datetime import date

PAYER = {
    'name': 'ООО "ГОГОЛЬ ШКОЛА"', 'inn': '9709053471', 'kpp': '770901001',
    'account': '40702810601500051355', 'bik': '044525104',
    'korr': '30101810745374525104', 'bank': 'ООО "Банк Точка"', 'bank_city': 'Г. МОСКВА',
}


def inn_ok(inn):
    d = [int(c) for c in inn]

    def ctrl(w):
        return sum(a * b for a, b in zip(w, d)) % 11 % 10

    if len(inn) == 10:
        return ctrl([2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[9]
    if len(inn) == 12:
        return (ctrl([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[10]
                and ctrl([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[11])
    return False


def account_ok(account, bik):
    # ключ счёта: последние 3 цифры БИК + 20 цифр счёта, веса 7-1-3, сумма кратна 10
    s = bik[-3:] + account
    return sum(int(c) * (7, 1, 3)[i % 3] for i, c in enumerate(s)) % 10 == 0


def check(p):
    errs = []
    for k in ('name', 'account', 'bik', 'korr', 'bank', 'amount', 'purpose'):
        if not str(p.get(k, '')).strip():
            errs.append(f'нет поля {k}')
    if errs:
        return errs
    # ИНН можно не указывать только у физлица (возврат клиенту на личный счёт 408/423)
    if not p.get('inn'):
        if not p['account'].startswith(('408', '423')) or p['name'].upper().startswith('ИП '):
            errs.append('нет ИНН — без него можно платить только физлицу на счёт 408…/423…')
    elif not p['inn'].isdigit() or not inn_ok(p['inn']):
        errs.append(f'ИНН {p["inn"]} не проходит контрольные цифры')
    elif p['name'].upper().startswith('ИП ') and len(p['inn']) != 12:
        errs.append(f'у ИП ИНН из 12 цифр, а здесь {p["inn"]} — похоже на ИНН организации')
    if p.get('kpp') and not (len(p['kpp']) == 9 and p['kpp'].isdigit()):
        errs.append(f'КПП {p["kpp"]} — не 9 цифр')
    if not (len(p['bik']) == 9 and p['bik'].isdigit()):
        errs.append(f'БИК {p["bik"]} — не 9 цифр')
    elif not (len(p['account']) == 20 and p['account'].isdigit()) or not account_ok(p['account'], p['bik']):
        errs.append(f'счёт {p["account"]} не сходится с БИК {p["bik"]} (ключ счёта)')
    if not (len(p['korr']) == 20 and p['korr'].startswith('301') and p['korr'].endswith(p['bik'][-3:])):
        errs.append(f'корсчёт {p["korr"]} не похож на корсчёт банка с БИК {p["bik"]}')
    try:
        if float(p['amount']) <= 0:
            errs.append('сумма должна быть больше нуля')
    except ValueError:
        errs.append(f'сумма {p["amount"]} — не число (нужно 12345.67)')
    if len(p['purpose']) > 210:
        errs.append(f'назначение {len(p["purpose"])} знаков, банк примет не больше 210')
    return errs


def main():
    data = json.load(open(sys.argv[1], encoding='utf-8'))
    out = sys.argv[2]
    d = data.get('date') or date.today().strftime('%d.%m.%Y')
    pays = data['payments']

    bad = [(i, e) for i, p in enumerate(pays, 1) for e in check(p)]
    if bad:
        for i, e in bad:
            print(f'платёж {i}: {e}', file=sys.stderr)
        sys.exit(1)

    a = PAYER
    lines = ['1CClientBankExchange', 'ВерсияФормата=1.03', 'Кодировка=Windows',
             'Отправитель=Бухгалтерия', 'Получатель=Банк Точка',
             f'ДатаСоздания={d}', 'ВремяСоздания=12:00:00',
             f'ДатаНачала={d}', f'ДатаКонца={d}', f'РасчСчет={a["account"]}',
             'Документ=Платежное поручение']
    for i, p in enumerate(pays, 1):
        amount = f'{float(p["amount"]):.2f}'
        lines += [
            'СекцияДокумент=Платежное поручение', f'Номер={p.get("number", i)}',
            f'Дата={d}', f'Сумма={amount}',
            f'ПлательщикСчет={a["account"]}', f'Плательщик=ИНН {a["inn"]} {a["name"]}',
            f'ПлательщикИНН={a["inn"]}', f'ПлательщикКПП={a["kpp"]}', f'Плательщик1={a["name"]}',
            f'ПлательщикРасчСчет={a["account"]}', f'ПлательщикБанк1={a["bank"]}',
            f'ПлательщикБанк2={a["bank_city"]}', f'ПлательщикБИК={a["bik"]}',
            f'ПлательщикКорсчет={a["korr"]}',
            f'ПолучательСчет={p["account"]}',
            f'Получатель=ИНН {p["inn"]} {p["name"]}' if p.get('inn') else f'Получатель={p["name"]}',
            f'ПолучательИНН={p.get("inn", "")}', f'ПолучательКПП={p.get("kpp", "")}',
            f'Получатель1={p["name"]}', f'ПолучательРасчСчет={p["account"]}',
            f'ПолучательБанк1={p["bank"]}', f'ПолучательБанк2={p.get("bank_city", "Г. МОСКВА")}',
            f'ПолучательБИК={p["bik"]}', f'ПолучательКорсчет={p["korr"]}',
            'ВидОплаты=01', 'Очередность=5', f'НазначениеПлатежа={p["purpose"]}',
            'КонецДокумента']
    lines.append('КонецФайла')
    with open(out, 'w', encoding='cp1251', newline='\r\n') as f:
        f.write('\n'.join(lines) + '\n')

    total = sum(float(p['amount']) for p in pays)
    print(f'{out}: {len(pays)} платёж(ей), итого ' + f'{total:,.2f}'.replace(',', ' '))
    for p in pays:
        print(f'  {p["name"]} | ИНН {p.get("inn") or "—"} | {p["account"]} | {float(p["amount"]):.2f} | {p["purpose"]}')


if __name__ == '__main__':
    main()
