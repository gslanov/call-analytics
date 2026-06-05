"""Behavioral-тест промпта v5: проверяем, что критерии встают как просила РОП.

Гоняет РЕАЛЬНЫЙ промпт v5 (импорт из app.services.llm_service) на 3 диалогах
через Gemini на OpenRouter и сверяет ключевые критерии с ожиданием РОП.

Запуск локально:
  OPENROUTER_API_KEY=sk-or-... python scripts/test_v5_prompt.py
"""
from __future__ import annotations
import os, sys, json
sys.path.insert(0, "backend")
from openai import OpenAI
from app.services.llm_service import SYSTEM_PROMPT, LLMService

MODEL = os.environ.get("TEST_MODEL", "google/gemini-3-flash-preview")

# case 1 — РЕАЛЬНЫЙ звонок ec92bf6f (склейка Gemini). Посуда/приборы + вопрос про количество + «Екатерина» + «курьер позвонит».
CASE1 = """[0:03] ОПЕРАТОР: Алло. Алло, Екатерина, здравствуйте. Меня зовут Александра, «Пироги №1». Звоню для подтверждения вашего заказа.
[0:06] ОПЕРАТОР: Давайте сверимся. У вас 1-й Нагатинский проезд, 10, строение 3.
[0:08] ОПЕРАТОР: Метро ближайшее Нагатинская.
[0:10] ОПЕРАТОР: Указали в комментариях для курьера, что это «Нью Таун Арена».
[0:14] КЛИЕНТ: Да, все верно.
[0:14] ОПЕРАТОР: Будем у вас с 13:35 до 14:05 сегодня. Подойдет такой интервал по доставке?
[0:20] КЛИЕНТ: Да, конечно.
[0:24] ОПЕРАТОР: У вас килограммовые пироги: «Жульен», с мясом и с картофелем и сыром.
[0:34] ОПЕРАТОР: Вам достаточно будет на компанию такого количества пирогов?
[0:38] КЛИЕНТ: Да, все подходит.
[0:39] ОПЕРАТОР: Отлично. Напитки дополнительно или приборы, чтобы удобнее было кушать коллегам, потребуются?
[0:45] КЛИЕНТ: Ничего не потребуется, спасибо.
[0:46] ОПЕРАТОР: У вас тогда заказ полностью оплачен — 5215 рублей.
[0:53] ОПЕРАТОР: По приезду курьер вам позвонит, как будет на месте.
[0:57] ОПЕРАТОР: Спасибо большое за заказ. Всего доброго."""

# case 2 — морс с перечислением вкусов (НЕ выгода) + чистое «курьер позвонит/будьте на связи» (НЕ комментарий курьера), без обращения по имени.
CASE2 = """[0:01] ОПЕРАТОР: Здравствуйте, компания Пироги №1, меня зовут Галина.
[0:05] ОПЕРАТОР: Хотите добавить морсы к заказу? Есть клюква, облепиха, ягода.
[0:09] КЛИЕНТ: Нет, спасибо.
[0:11] ОПЕРАТОР: Хорошо. Курьер, как приедет, позвонит. Главное, будьте на связи, пожалуйста.
[0:15] КЛИЕНТ: Хорошо.
[0:16] ОПЕРАТОР: Всего доброго!"""

# case 3 — ТОЛЬКО платный комплект посуды (с выгодой «удобно нарезать») + обращение «Андрей» в приветствии + вопрос про количество НЕ задан.
CASE3 = """[0:01] ОПЕРАТОР: Добрый день, Андрей! Компания Пироги номер один, меня зовут Анна, звоню подтвердить заказ.
[0:06] ОПЕРАТОР: Нужен ли вам комплект одноразовой посуды, чтобы удобно было нарезать и подать?
[0:10] КЛИЕНТ: Да, давайте.
[0:12] ОПЕРАТОР: Отлично, добавила. Всего доброго!"""

# case 4 — ВХОДЯЩИЙ: клиент сам позвонил, оператор представился (Галина+компания), но к клиенту по имени НЕ обращался.
CASE4 = """[0:00] КЛИЕНТ: Алло, здравствуйте, хочу заказать пироги.
[0:03] ОПЕРАТОР: Здравствуйте! Компания Пироги №1, меня зовут Галина. Что желаете заказать?
[0:08] КЛИЕНТ: Два осетинских с мясом.
[0:11] ОПЕРАТОР: Записала. Куда доставить?
[0:14] КЛИЕНТ: Ленина, 5.
[0:16] ОПЕРАТОР: Хорошо, оформляю."""

# case 5 — ВХОДЯЩИЙ: клиент назвал имя в ходе разговора, оператор затем ОБРАЩАЕТСЯ по нему («Дмитрий»).
CASE5 = """[0:00] КЛИЕНТ: Здравствуйте, хочу узнать по своему заказу.
[0:03] ОПЕРАТОР: Здравствуйте, компания Пироги №1, меня зовут Анна. Как вас зовут?
[0:06] КЛИЕНТ: Дмитрий.
[0:08] ОПЕРАТОР: Дмитрий, назовите номер заказа, пожалуйста.
[0:11] КЛИЕНТ: 12345.
[0:13] ОПЕРАТОР: Вижу ваш заказ, Дмитрий, всё в силе."""

CASES = [
    ("1 ec92bf6f (real)", CASE1, {
        "standard.introduced_self": True, "standard.named_company": True,
        "standard.offered_upsell": True, "standard.explained_upsell_benefit": True,
        "standard.clarified_portion_sufficiency": True, "loyalty.addressed_by_name": True,
    }),
    ("2 морс+будьте на связи", CASE2, {
        "standard.offered_upsell": True, "standard.explained_upsell_benefit": False,
        "standard.clarified_courier_comment": False, "loyalty.addressed_by_name": False,
    }),
    ("3 посуда+Андрей", CASE3, {
        "standard.introduced_self": True, "standard.offered_upsell": True,
        "standard.explained_upsell_benefit": True, "standard.clarified_portion_sufficiency": False,
        "loyalty.addressed_by_name": True,
    }),
    ("4 ВХОДЯЩИЙ без имени клиента", CASE4, {
        "standard.introduced_self": True, "standard.named_company": True,
        "loyalty.addressed_by_name": False,
    }),
    ("5 ВХОДЯЩИЙ имя узнал в разговоре", CASE5, {
        "standard.introduced_self": True, "standard.named_company": True,
        "loyalty.addressed_by_name": True,
    }),
]


def run(client, transcript: str):
    user = LLMService._build_user_message(transcript, "")
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        timeout=180, extra_body={"reasoning": {"enabled": False}},
    )
    raw = (resp.choices[0].message.content or "").strip()
    res = LLMService.get_instance()._parse_and_validate(raw, model_used=MODEL)
    return res, raw


def main():
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        print("НЕТ OPENROUTER_API_KEY"); sys.exit(1)
    client = OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")

    total_ok = total = 0
    for name, transcript, expected in CASES:
        print(f"\n===== CASE {name} =====")
        res, raw = run(client, transcript)
        if res is None:
            print(f"  [ОШИБКА] не распарсилось. raw[:200]={raw[:200]}"); continue
        d = res.details or {}
        reasons = d.get("reasons", {})
        for key_path, exp in expected.items():
            grp, crit = key_path.split(".")
            got = (d.get(grp) or {}).get(crit)
            ok = got is exp
            total += 1; total_ok += 1 if ok else 0
            why = (reasons.get(grp, {}) or {}).get(crit, "")
            print(f"  [{'OK ' if ok else 'FAIL'}] {key_path}: got={got} exp={exp}  | {why[:80]}")
    print(f"\nИТОГ: {total_ok}/{total} критериев совпали с ожиданием РОП")


if __name__ == "__main__":
    main()
