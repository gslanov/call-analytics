"""LLMService — call quality analysis via GPT-4 API.

Evaluates operator performance on 22 binary criteria across three groups:
  - standard (13):  protocol compliance checklist
  - loyalty (6):    client-orientation & tone
  - kindness (3):   politeness & professionalism

Plus markers group (not scored — used for filtering/tagging):
  - markers (2):    prepayment_20k, order_confirmation

Each criterion is true/false/null (null = not applicable).
Scores are computed mathematically: % of passed items among applicable ones.
Markers do NOT affect scores — they are informational flags.

Graceful degradation: returns None when API is unavailable / key not set.
Retry: 3x with exponential backoff.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Retry
MAX_RETRIES = 5
RETRY_BASE_DELAY = 2.0  # seconds
RETRY_MAX_DELAY = 60.0
RATE_LIMIT_BASE_DELAY = 10.0

# Criteria version — increment when criteria change
CRITERIA_VERSION = "v5"

# Groups used in overall score calculation (markers excluded — informational only)
SCORED_GROUPS = ("standard", "loyalty", "kindness")

# Expected keys per group for validation
CRITERIA_SCHEMA: dict[str, list[str]] = {
    "standard": [
        "introduced_self",
        "named_company",
        "clarified_delivery_date",
        "stated_delivery_time",
        "stated_full_address",
        "named_metro",
        "stated_order_contents",
        "offered_upsell",
        "explained_upsell_benefit",
        "named_order_total",
        "clarified_courier_comment",
        "clarified_portion_sufficiency",
        "clarified_cash_change",
    ],
    "loyalty": [
        "addressed_by_name",
        "did_not_raise_voice",
        "friendly_calm_confident_tone",
        "did_not_interrupt",
        "calm_in_conflict",
        "answered_all_questions",
    ],
    "kindness": [
        "no_profanity_filler_words",
        "polite_goodbye",
        "no_sarcasm_irony_aggression",
    ],
    # Markers — informational flags, not scored
    "markers": [
        "prepayment_20k",
        "order_confirmation",
    ],
}

# Russian labels for each criterion (used by frontend too via API)
CRITERIA_LABELS: dict[str, dict[str, str]] = {
    "standard": {
        "introduced_self": "Представился",
        "named_company": "Назвал компанию",
        "clarified_delivery_date": "Уточнил дату доставки",
        "stated_delivery_time": "Проговорил время доставки",
        "stated_full_address": "Проговорил адрес полностью",
        "named_metro": "Назвал метро",
        "stated_order_contents": "Проговорил состав заказа",
        "offered_upsell": "Предложил апсейл",
        "explained_upsell_benefit": "Рассказал про выгоду апсейла",
        "named_order_total": "Назвал сумму заказа",
        "clarified_courier_comment": "Уточнил комментарий для курьера",
        "clarified_portion_sufficiency": "Уточнил количество человек / хватит ли пирогов",
        "clarified_cash_change": "Уточнил сдачу при оплате наличными",
    },
    "loyalty": {
        "addressed_by_name": "Обращался к клиенту по имени",
        "did_not_raise_voice": "Не повышал тон",
        "friendly_calm_confident_tone": "Дружелюбный, спокойный и уверенный тон",
        "did_not_interrupt": "Не перебивал клиента",
        "calm_in_conflict": "Спокойствие в конфликтной ситуации",
        "answered_all_questions": "Ответил на все вопросы клиента",
    },
    "kindness": {
        "no_profanity_filler_words": "Нет мата и слов-паразитов",
        "polite_goodbye": "Вежливо попрощался",
        "no_sarcasm_irony_aggression": "Нет сарказма, иронии и агрессии",
    },
    "markers": {
        "prepayment_20k": "Предоплата заказа ≥20 000 ₽",
        "order_confirmation": "Подтверждение заказа",
    },
}

SYSTEM_PROMPT = """Ты — эксперт по оценке качества обслуживания в контакт-центре доставки осетинских пирогов.
Операторы и принимают входящие звонки от клиентов, и сами звонят клиентам — чаще всего для подтверждения и уточнения заказов. Направление звонка (входящий или исходящий) определяй по содержанию диалога, а НЕ предполагай заранее.

Проверь оператора по каждому из 22 критериев (standard/loyalty/kindness) ниже. По каждому верни:
- true — оператор выполнил
- false — оператор НЕ выполнил
- null — критерий неприменим к данному звонку

Дополнительно — 2 маркера (markers), они НЕ влияют на оценку, это информационные флаги для фильтрации звонков.

## 1. СТАНДАРТЫ (standard)
1. introduced_self — Оператор представился клиенту в начале звонка. КОНТЕКСТ: звонок может быть входящим (клиент сам позвонил) или исходящим (оператор звонит подтвердить заказ) — НЕ предполагай направление заранее, определяй по диалогу. В приветствии всегда может звучать имя оператора, а иногда (если оператор уже знает клиента) ещё и имя клиента — не путай их (как различить — см. пункт «а» ниже и addressed_by_name).
   Засчитывай true если выполнено ХОТЯ БЫ ОДНО:
   а) оператор назвал своё имя. Имена операторов: Галина, Александра, Анна, Анастасия (ASR искажает: «Калина», «Лина», «Анестасия», «Аннастасия», «Сашина» и т.п.). Если в приветствии звучат ДВА имени — имя из набора операторов (Галина/Александра/Анна/Анастасия) это представление оператора, а ДРУГОЕ имя — это обращение к клиенту (учитывается в addressed_by_name), НЕ считай его именем оператора;
   б) оператор сказал что звонит из «компании Пироги №1» / «Пироги номер один» — это форма представления через компанию. ASR часто искажает название: «Брагин-Брагин», «Пирогин», «Барагин Родин», «Дорогие номер один», «Треккинг-01», «Пирогин номер один», «Пироги номер 1», «Пирожки номер один» — это всё одна и та же компания. Если в первой реплике есть «компания + любое название» — засчитывай.
   Примеры:
   - «Добрый день, Галина, компания Пироги №1» → true (имя + компания)
   - «Добрый день, компания "Пироги №1", звоню по поводу вашего заказа» → true (компания тоже считается представлением)
   - «Здравствуйте, компания Брагин-Брагин» → true (ASR-искажение «Пироги №1» — всё равно представление через компанию)
   - «Здравствуйте, я по поводу вашего заказа» → false (ни имени, ни компании)
2. named_company — Оператор произнёс название компании «Пироги №1» / «Пироги номер один». ASR часто искажает название: «Брагин-Брагин», «Пирогин», «Барагин Родин», «Дорогие номер один», «Треккинг-01», «Пирогин номер один», «Пироги номер 1», «Пирожки номер один» — если в начале звонка звучит «компания + любое такое название», засчитывай true.
3. clarified_delivery_date — Оператор уточнил дату доставки. ВАЖНО: «сегодня», «завтра», «послезавтра» — тоже считается датой, засчитывай как true
4. stated_delivery_time — Оператор проговорил время доставки
5. stated_full_address — Оператор полностью проговорил адрес доставки
6. named_metro — Оператор назвал станцию метро (null если метро неприменимо — например, доставка за МКАД или в области)
7. stated_order_contents — Оператор проговорил состав заказа (перечислил что заказано)
8. offered_upsell — Оператор предложил клиенту дополнительный ПЛАТНЫЙ товар или сервисный набор сверх уже собранного заказа (апсейл).
   ✅ ЗАСЧИТЫВАЕТСЯ:
   - напитки: «Не хотите взять морс?», «Может, добавить лимонад?»
   - дополнительные пироги / десерты / выпечка: «Может быть к десерту пирожок?»
   - подарочные товары, акции: «У нас сейчас акция на сладкое, добавим?»
   - сервисный набор / комплект одноразовой посуды / нож для нарезки пирога — у «Пироги №1» это ПЛАТНАЯ допуслуга, поэтому предложение такого набора ЗАСЧИТЫВАЕТСЯ как апсейл: «Нужен ли комплект одноразовой посуды?», «Добавить сервисный набор?», «Нужен ли нож для нарезки?». Засчитывается, даже если оператор предложил ТОЛЬКО набор посуды (без напитков и пирогов).
   ❌ НЕ ЗАСЧИТЫВАЕТСЯ:
   - «Хватит ли пирогов на компанию?» / «На сколько человек?» — это уточнение количества уже заказанного (отдельный критерий clarified_portion_sufficiency), не апсейл.
   Ключевой признак апсейла: оператор предлагает добавить к заказу дополнительный платный товар или набор.
9. explained_upsell_benefit — Оператор объяснил ВЫГОДУ/ПОЛЬЗУ апсейла — назвал ПРИЧИНУ, зачем клиенту это взять (null если апсейл не предлагался, т.е. offered_upsell=false).
   ✅ ЗАСЧИТЫВАЕТСЯ (есть выгода / польза / повод):
   - «для удобства», «чтобы не пачкать руки», «удобно нарезать и подать» (про посуду/нож)
   - «хорошо идёт к пирогам», «освежает», «многие берут вместе с пирогами»
   - «на большие компании отлично заходит», «как раз к празднику»
   - объяснил состав и условия подарочного продукта (что входит в подарок ко дню рождения и как его получить)
   ❌ НЕ ЗАСЧИТЫВАЕТСЯ (просто перечисление, без выгоды):
   - просто назвал вкусы/виды: «есть клюква, облепиха, ягода», «сырная, мясная, овощная» — это перечисление ассортимента, а НЕ выгода
   - просто назвал цену без объяснения пользы
   Ключевой признак: прозвучала ПРИЧИНА взять (удобство, вкус, повод, к чему подходит), а не просто список вариантов.
10. named_order_total — Оператор назвал итоговую сумму заказа
11. clarified_courier_comment — Оператор уточнил информацию которая поможет курьеру найти клиента/попасть к нему. Засчитывается ЛЮБОЕ из:
   - явный вопрос про комментарий курьеру: «нужен ли комментарий для курьера?»
   - вопрос про вход/подъезд: «какой подъезд?», «есть ли вход со двора?», «как лучше пройти?»
   - вопрос про ориентир/название организации: «есть ли ориентир?», «название организации?», «нужно что-то записать?»
   - вопрос про домофон/код/этаж: «какой домофон?», «код?», «какой этаж?»
   - вопрос «нужно ли что-то записать для курьера»
   Засчитывай true если оператор задал ХОТЯ БЫ ОДИН из таких вопросов — не обязательно дословно «комментарий». Цель критерия: проверить, что оператор позаботился чтобы курьеру было легко доехать/найти.
   ❌ НЕ ЗАСЧИТЫВАЕТСЯ — это НЕ уточнение для курьера (оператор ничего не выясняет, а просто информирует клиента):
   - «Курьер, как приедет, позвонит» / «будьте на связи» / «оставайтесь на связи» / «курьер свяжется» → false. Здесь оператор не собирает никакой информации, помогающей курьеру найти/попасть к клиенту, — он лишь сообщает, что курьер позвонит. Засчитывай true ТОЛЬКО когда оператор ВЫЯСНЯЕТ информацию (подъезд, вход, домофон, код, ориентир, «что записать для курьера»).
   Примеры:
   - «Какой-то ориентир, выезд, вход, название организации, нужно что-то записать?» → true
   - «Есть ли домофон или встретите?» → true
   - «Курьер позвонит, как подъедет, будьте на связи» → false (не выясняет, а информирует)
   - Только адрес дома/улицы (без вопросов про подъезд/вход) → false
   - Уточнение комментария к самой еде («без лука?») → false (это не про курьера)
12. clarified_portion_sufficiency — Оператор уточнил количество человек / хватит ли пирогов на компанию. ОЦЕНИВАЙ СТРОГО true ИЛИ false — НЕ используй null и НЕ ставь прочерк:
   - true — оператор задал такой вопрос: «на сколько человек?», «хватит ли пирогов?», «всем хватит?»
   - false — оператор НЕ задал такого вопроса. Ставь false даже если в заказе много позиций, фиксированный сет, подарок ко дню рождения или быстрое подтверждение — раз вопрос не прозвучал, это false (не оправдывай прочерком «уточнение не потребовалось»).
13. clarified_cash_change — Оператор уточнил нужна ли сдача при оплате наличными (null если оплата не наличными)

## 2. ЛОЯЛЬНОСТЬ (loyalty)
1. addressed_by_name — Оператор обращался к клиенту по имени хотя бы раз. Имя клиента оператор может знать заранее (если сам звонит подтвердить заказ) ЛИБО узнать в ходе разговора (если клиент позвонил сам) — в ОБОИХ случаях, если оператор обращается к клиенту по имени, засчитывай true (в том числе в самом начале: «Здравствуйте, Андрей», «Андрей, по вашему заказу...»). Не путай с именем самого оператора: имена операторов — Галина/Александра/Анна/Анастасия; если оператор называет ДРУГОЕ имя, обращаясь к собеседнику, — это имя клиента, засчитывай true.
2. did_not_raise_voice — Оператор не разговаривал на повышенных тонах (оцени по лексике и характеру реплик)
3. friendly_calm_confident_tone — Оператор поддерживал дружелюбный, спокойный и уверенный тон
4. did_not_interrupt — Оператор не перебивал клиента (оцени по структуре диалога)
5. calm_in_conflict — Оператор сохранял спокойствие в конфликтной ситуации (null если конфликта не было)
6. answered_all_questions — Оператор ответил на все вопросы клиента. Ответом считается и прямой ответ, и уточняющий вопрос оператора в ответ на вопрос клиента (например, клиент спросил про авто — оператор уточнил нужен ли паспорт = ответил). Если оператор проактивно уточняет детали — это тоже считается.

## 3. ДОБРОЖЕЛАТЕЛЬНОСТЬ (kindness)
1. no_profanity_filler_words — Оператор не использовал ненормативную лексику и слова-паразиты. Слово-паразит — только если употребляется МНОГОКРАТНО и навязчиво (3+ раз подряд или явно засоряет речь). Редкое «ну» или «значит» в разговорной речи НЕ считается паразитом. Засчитывай false только если паразиты реально портят впечатление от речи.
2. polite_goodbye — Оператор вежливо попрощался с клиентом
3. no_sarcasm_irony_aggression — Оператор не допускал ЯВНОГО сарказма, насмешки, агрессии или хамства. Ставь false ТОЛЬКО при однозначно грубых, оскорбительных или издевательских высказываниях. Нейтральные и бытовые фразы («можете ответить», «ну просто», «подождите», «я же говорю») — это НЕ сарказм и НЕ агрессия, даже если в тексте они могут показаться резкими. Помни: ты не слышишь интонацию, только текст — при сомнении ставь true.

## 4. МАРКЕРЫ (markers) — НЕ ВЛИЯЮТ НА ОЦЕНКУ
Это информационные флаги для фильтрации звонков. В reason клади либо точную цитату, либо «не озвучено».

1. prepayment_20k — Маркер предоплаты для крупных заказов.
   - СНАЧАЛА определи сумму заказа (итоговую сумму, которую оператор называет клиенту).
   - Если сумма заказа < 20 000 ₽ ИЛИ сумма НЕ озвучена/не определяется — value=null (маркер неприменим).
   - Если сумма заказа ≥ 20 000 ₽:
     • value=true, если оператор (или кто-то в разговоре — но на практике это оператор) сказал, что такие заказы принимаются по предоплате — ПОЛНОЙ или ЧАСТИЧНОЙ (например «заказ от 20 тысяч мы принимаем по предоплате», «оплатите полностью или половину», «нужна предоплата на большие заказы», «от двадцати тысяч — предоплата»). Перефразировки допустимы, ключевое — озвучена связка «≥20 тыс. → предоплата (полная или частичная)».
     • value=false, если сумма ≥20k но эта информация НЕ озвучена.
   - ВАЖНО: ищи эту фразу по всему диалогу — и у оператора, и у клиента (клиент может её процитировать, это тоже засчитывается). На практике почти всегда её произносит оператор.

2. order_confirmation — Маркер «звонок для подтверждения заказа».
   - value=true, если в диалоге (у оператора ИЛИ у клиента) звучит фраза о том, что этот звонок — для подтверждения уже существующего заказа. Примеры: «звоню вам для подтверждения заказа», «я по поводу подтверждения вашего заказа», «мы должны подтвердить заказ», «уточняю детали по вашему заказу от [дата]». Перефразировки допустимы. Ключевое — НЕ новый заказ, а уточнение/подтверждение уже оформленного.
   - value=false, если это явно НОВЫЙ заказ (клиент звонит заказать впервые) или звонок о чём-то другом (жалоба, вопрос и т.п.).
   - value=null — только если диалог настолько короткий/неразборчивый, что определить невозможно.

## ФОРМАТ ОТВЕТА
Каждый критерий — объект: {"value": true/false/null, "reason": "пояснение", "timestamp": "M:SS"}.
- value: true (выполнено), false (не выполнено), null (неприменимо)
- reason: ОБЯЗАТЕЛЬНО для false — точная цитата оператора или описание проблемы. Для true/null — краткое пояснение.
- timestamp: время в формате "M:SS" — момент в записи, где это видно. ОБЯЗАТЕЛЬНО для false (когда это должно было быть). Для true — момент, где оператор это сделал. Для null — не указывать.
  Бери таймстемпы из меток [M:SS] в тексте реплик.

Верни ТОЛЬКО JSON без пояснений:
{
  "details": {
    "standard": {
      "introduced_self": {"value": true, "reason": "«Меня зовут Анастасия»", "timestamp": "0:02"},
      "named_company": {"value": true, "reason": "«Компания Пироги номер один»", "timestamp": "0:01"},
      "clarified_delivery_date": {"value": false, "reason": "Дата доставки не была озвучена", "timestamp": "0:30"},
      "stated_delivery_time": {"value": true, "reason": "«с 20:45 до 21:45»", "timestamp": "1:15"},
      "stated_full_address": {"value": true, "reason": "«улица Виненосская, дом 13»", "timestamp": "0:45"},
      "named_metro": {"value": null, "reason": "Метро не упоминалось, адрес в области"},
      "stated_order_contents": {"value": true, "reason": "«хачаны с картофельным сыром и мясом»", "timestamp": "0:35"},
      "offered_upsell": {"value": false, "reason": "Доп. продукт не предложен", "timestamp": "2:10"},
      "explained_upsell_benefit": {"value": null, "reason": "Апсейл не предлагался"},
      "named_order_total": {"value": true, "reason": "«4300 общая сумма заказа»", "timestamp": "1:50"},
      "clarified_courier_comment": {"value": false, "reason": "Комментарий для курьера не обсуждался", "timestamp": "2:30"},
      "clarified_portion_sufficiency": {"value": false, "reason": "Оператор не уточнил, на сколько человек / хватит ли пирогов", "timestamp": "1:40"},
      "clarified_cash_change": {"value": null, "reason": "Оплата картой"}
    },
    "loyalty": {
      "addressed_by_name": {"value": true, "reason": "«Андрей, правильно?»", "timestamp": "0:10"},
      "did_not_raise_voice": {"value": true, "reason": "Ровный спокойный тон"},
      "friendly_calm_confident_tone": {"value": true, "reason": "Дружелюбное общение"},
      "did_not_interrupt": {"value": true, "reason": "Не перебивал"},
      "calm_in_conflict": {"value": null, "reason": "Конфликта не было"},
      "answered_all_questions": {"value": true, "reason": "На все вопросы ответил"}
    },
    "kindness": {
      "no_profanity_filler_words": {"value": false, "reason": "Слова-паразиты: «ну», «как бы», «типа»", "timestamp": "1:30"},
      "polite_goodbye": {"value": true, "reason": "«Спасибо большое, хорошего дня!»", "timestamp": "3:15"},
      "no_sarcasm_irony_aggression": {"value": true, "reason": "Без сарказма и агрессии"}
    },
    "markers": {
      "prepayment_20k": {"value": null, "reason": "Сумма заказа 4300 ₽ — маркер неприменим"},
      "order_confirmation": {"value": true, "reason": "«Звоню вам для подтверждения заказа»", "timestamp": "0:03"}
    }
  },
  "summary": "<2-3 предложения на русском: что хорошо, что улучшить>",
  "quotes": [
    {"text": "<точная цитата ОПЕРАТОРА>", "criterion": "<standard|loyalty|kindness>", "sentiment": "<positive|negative>"}
  ]
}

ВАЖНО:
- reason для false-пунктов: ТОЧНАЯ цитата оператора или конкретное описание. НЕ «не выполнено».
- Цитаты (quotes) — ТОЛЬКО из реплик ОПЕРАТОРА. Мы оцениваем работу оператора, НЕ клиента.
- Цитат: 3-6 штук (и положительные, и отрицательные).
- Никакого текста вне JSON. Никакого Markdown. Только фигурные скобки.
- НЕПОЛНЫЕ ДИАЛОГИ: если транскрипция обрывается на середине, видно что диалог продолжался но текст обрезан — НЕ ставь false за критерии, которые могли быть выполнены в пропущенной части. Ставь null с пометкой «транскрипция неполная». Оценивай ТОЛЬКО то, что реально слышно в записи."""

STRICT_SYSTEM_PROMPT = SYSTEM_PROMPT + (
    "\n\nОТВЕЧАЙ СТРОГО JSON. Никакого Markdown, никакого ```json. Только фигурные скобки."
)


@dataclass
class AnalysisResult:
    standard: int
    loyalty: int
    kindness: int
    overall: int
    summary: str
    details: dict[str, dict[str, bool | None]] | None = None
    criteria_version: str = CRITERIA_VERSION
    quotes: list[dict[str, str]] = field(default_factory=list)
    llm_model: str = "gpt-5.4"
    partial: bool = False   # True if some fields were missing/clamped


def _parse_timestamp(ts: str) -> float:
    """Parse 'M:SS' or 'MM:SS' to seconds."""
    import re
    m = re.match(r"(\d+):(\d{2})", ts.strip())
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return 0.0


def _compute_group_score(items: dict[str, bool | None]) -> int:
    """Compute score as percentage of true items among non-null items."""
    applicable = {k: v for k, v in items.items() if v is not None}
    if not applicable:
        return 100  # all items N/A → no penalty
    passed = sum(1 for v in applicable.values() if v is True)
    return round(passed / len(applicable) * 100)


def _apply_dependencies(details: dict[str, dict[str, bool | None]]) -> None:
    """Apply business logic dependencies between criteria (mutates in place).

    This ensures consistency: if a prerequisite is false, the dependent
    criterion becomes null (not applicable) rather than false.
    """
    std = details.get("standard", {})

    # If upsell not offered → benefit explanation is N/A
    if std.get("offered_upsell") is False:
        std["explained_upsell_benefit"] = None

    loy = details.get("loyalty", {})
    # calm_in_conflict is already null if no conflict — just validate
    # (GPT handles this, we don't override)


class LLMService:
    """GPT-4 analysis service (singleton)."""

    _instance: "LLMService | None" = None
    _clients_cache: list[dict[str, Any]] | None = None

    @classmethod
    def get_instance(cls) -> "LLMService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_client(self) -> Any:
        """Возвращает первый доступный клиент из fallback-цепочки или None.

        Используется внешним кодом (health, pipeline, diarization) чтобы понять
        «настроен ли вообще LLM» — точно такая же семантика, как до 06.05.
        """
        attempts = self._build_attempts()
        return attempts[0]["client"] if attempts else None

    @property
    def _client(self) -> Any:
        """Backward-compat для health.py:31 (svc._client is not None)."""
        return self._get_client()

    def _build_attempts(self) -> list[dict[str, Any]]:
        """Строит цепочку LLM-кандидатов в порядке fallback.

        Цепочка (07.05.2026 — kie скрыта, gemini напрямую):
          1. gemini-direct + gemini-3-flash-preview (Google AI Studio, OpenAI-compat)
          2. openai-fallback + gpt-5-mini           (промежуточный)
          3. openai-direct + gpt-5.4                (последняя надежда)

        kie-цепочка временно скрыта флагом settings.kie_disabled — код оставлен,
        включить обратно одним env: KIE_DISABLED=0. Если флаг снят и kie_api_key
        задан — kie-уровни добавляются МЕЖДУ gemini и openai-fallback.

        Каждый клиент — отдельный OpenAI() с своим api_key/base_url. Кэшируется
        в self._clients_cache на первом вызове.
        """
        if self._clients_cache is not None:
            return self._clients_cache

        from openai import OpenAI
        attempts: list[dict[str, Any]] = []

        if settings.gemini_api_key:
            attempts.append({
                "label": "gemini-direct",
                "client": OpenAI(
                    api_key=settings.gemini_api_key,
                    base_url=settings.gemini_base_url,
                ),
                "model": settings.gemini_direct_model,
                "provider": "gemini",
            })

        if settings.kie_api_key and not settings.kie_disabled:
            kie_levels = [
                ("kie/flash", settings.kie_primary_base_url, settings.llm_model),
                ("kie/pro", settings.kie_fallback_base_url, settings.llm_fallback_model),
                ("kie/gpt5-2", settings.kie_fallback2_base_url, settings.llm_fallback2_model),
            ]
            seen_urls: set[str] = set()
            for label, base_url, model in kie_levels:
                if not base_url or base_url in seen_urls:
                    continue
                seen_urls.add(base_url)
                attempts.append({
                    "label": label,
                    "client": OpenAI(api_key=settings.kie_api_key, base_url=base_url),
                    "model": model,
                    "provider": "kie",
                })

        if settings.openrouter_api_key:
            attempts.append({
                "label": "openrouter",
                "client": OpenAI(
                    api_key=settings.openrouter_api_key,
                    base_url=settings.openrouter_base_url,
                ),
                "model": settings.llm_model,
                "provider": "openrouter",
            })

        if settings.openai_api_key and settings.openai_fallback_model:
            attempts.append({
                "label": "openai-fallback",
                "client": OpenAI(api_key=settings.openai_api_key),
                "model": settings.openai_fallback_model,
                "provider": "openai",
            })

        if settings.openai_api_key:
            attempts.append({
                "label": "openai-direct",
                "client": OpenAI(api_key=settings.openai_api_key),
                "model": settings.openai_direct_model,
                "provider": "openai",
            })

        for a in attempts:
            logger.info("LLM chain: %s → %s", a["label"], a["model"])

        self._clients_cache = attempts
        return attempts

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout: int = 300,
    ) -> tuple[str, str]:
        """Универсальный вызов chat-completion с цепочкой fallback из 4 уровней.

        Цепочка: kie/flash → kie/pro → kie/gpt5-2 → openai-direct/gpt-5.4.
        На каждом уровне ловим:
          - HTTP исключения (5xx, timeout, etc.) — идём на следующий уровень
          - kie {"code":..., "msg":...} в теле 200-OK ответа (choices=None) —
            на следующий уровень
          - Пустой content — на следующий уровень

        Возвращает (content, model_used). model_used — ИМЯ ФАКТИЧЕСКИ ОТВЕТИВШЕЙ
        модели, пишется в AnalysisResult.llm_model для аудита.
        """
        attempts = self._build_attempts()
        if not attempts:
            raise RuntimeError("No LLM client configured (kie/openrouter/openai keys all empty)")

        last_exc: Exception | None = None
        for idx, a in enumerate(attempts):
            label, client, model, provider = a["label"], a["client"], a["model"], a["provider"]
            try:
                kwargs: dict[str, Any] = dict(
                    model=model,
                    messages=messages,
                    timeout=timeout,
                )
                if not model.startswith("gpt-5"):
                    kwargs["temperature"] = 0
                if provider == "kie":
                    kwargs["extra_body"] = {"include_thoughts": False, "reasoning_effort": "low"}
                elif provider == "openrouter":
                    kwargs["extra_body"] = {"reasoning": {"enabled": False}}
                elif provider == "gemini":
                    # Gemini 3 Flash — thinking-модель. Без этого reasoning-токены
                    # тарифицируются как output ($3/M) и легко удваивают-утраивают
                    # цену на ровном месте. "low" вместо "none" — чтобы качество
                    # не просело на сложных диалогах.
                    kwargs["reasoning_effort"] = "low"
                elif provider == "openai" and model.startswith("gpt-5"):
                    # gpt-5* семейство OpenAI — все thinking. Дефолтный reasoning
                    # = medium → 30+ сек + риск APITimeoutError. minimal убивает
                    # качество (тестили 08.05). low — компромисс: 30-60 сек/звонок,
                    # точность ~75% против ~25% у minimal-моделей.
                    kwargs["reasoning_effort"] = "low"

                response = client.chat.completions.create(**kwargs)

                # kie.ai отдаёт HTTP 200 с body {"code":400,"msg":"maintenance"} когда
                # модель лежит. SDK строит ChatCompletion с choices=None — пробуем дальше.
                if not response.choices:
                    raise RuntimeError(
                        f"{label}: empty/null choices (likely kie maintenance) — full response: {response}"
                    )
                msg = response.choices[0].message
                content = (msg.content or "").strip() if msg else ""
                if not content:
                    raise RuntimeError(f"{label}: empty content")

                if idx > 0:
                    logger.warning(
                        "LLM served via fallback level %d (%s/%s)", idx, label, model
                    )
                return content, model

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "LLM %s (%s) failed: %s: %s",
                    label, model, type(exc).__name__, exc,
                )
                continue

        assert last_exc is not None
        raise last_exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        operator_text: str,
        client_context: str = "",
    ) -> AnalysisResult | None:
        """Analyse operator speech via GPT-4.

        Args:
            operator_text: Operator's utterances from diarization.
            client_context: Client's utterances (for context, not scored).

        Returns:
            AnalysisResult or None if GPT-4 is unavailable.
        """
        client = self._get_client()
        if client is None:
            logger.warning(
                "OPENAI_API_KEY not set — LLM analysis unavailable (graceful degradation)"
            )
            return None

        if not operator_text.strip():
            logger.warning("LLM: operator_text is empty — skipping analysis")
            return None

        user_message = self._build_user_message(operator_text, client_context)

        # Try with strict prompt on retry
        result = self._call_with_retry(client, user_message, strict=False)
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _build_user_message(operator_text: str, client_context: str) -> str:
        # Triple merge output: already has [M:SS] ОПЕРАТОР/КЛИЕНТ format
        if "] ОПЕРАТОР:" in operator_text or "] КЛИЕНТ:" in operator_text:
            return f"=== Полный диалог (с таймстемпами и спикерами) ===\n{operator_text.strip()}"
        msg = f"=== Реплики оператора ===\n{operator_text.strip()}"
        if client_context.strip():
            msg += f"\n\n=== Реплики клиента (для контекста, не оценивается) ===\n{client_context.strip()}"
        return msg

    def _call_with_retry(
        self,
        client: Any,
        user_message: str,
        *,
        strict: bool = False,
    ) -> AnalysisResult | None:
        """Call GPT-4 with retry on failure or invalid JSON."""
        last_exc: Exception | None = None

        try:
            from openai import APIConnectionError, APITimeoutError, RateLimitError, APIStatusError
        except ImportError:
            APIConnectionError = APITimeoutError = RateLimitError = APIStatusError = ()  # type: ignore

        for attempt in range(1, MAX_RETRIES + 1):
            sys_prompt = STRICT_SYSTEM_PROMPT if (strict or attempt > 1) else SYSTEM_PROMPT
            try:
                raw, model_used = self._call_api(client, sys_prompt, user_message)
                result = self._parse_and_validate(raw, model_used=model_used)
                if result is not None:
                    logger.info("LLM analysis done on attempt %d (model=%s)", attempt, model_used)
                    return result
                logger.warning(
                    "LLM attempt %d: invalid JSON response, retrying…", attempt
                )
            except Exception as exc:
                last_exc = exc
                err_type = type(exc).__name__
                is_rate_limit = isinstance(exc, RateLimitError) if RateLimitError else False
                is_5xx = (
                    isinstance(exc, APIStatusError)
                    and getattr(exc, "status_code", 0) >= 500
                ) if APIStatusError else False
                is_transient = (
                    is_rate_limit
                    or is_5xx
                    or isinstance(exc, (APIConnectionError, APITimeoutError))
                    if APIConnectionError else True
                )

                if attempt < MAX_RETRIES and is_transient:
                    base = RATE_LIMIT_BASE_DELAY if is_rate_limit else RETRY_BASE_DELAY
                    cap = min(RETRY_MAX_DELAY, base * (2 ** (attempt - 1)))
                    delay = random.uniform(0, cap)
                    logger.warning(
                        "LLM attempt %d/%d failed (%s%s: %s). Retrying in %.1fs…",
                        attempt, MAX_RETRIES, err_type,
                        " RATE_LIMIT" if is_rate_limit else "",
                        exc, delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "LLM failed after %d attempts (%s: %s) — graceful degradation",
                        attempt, err_type, exc,
                    )
                    break

        return None  # graceful degradation

    def _call_api(self, client: Any, system_prompt: str, user_message: str) -> tuple[str, str]:
        """Wrapper над chat_completion. Возвращает (raw_text, model_used)."""
        return self.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            timeout=300,
        )

    def _parse_and_validate(
        self,
        raw: str,
        *,
        model_used: str | None = None,
    ) -> AnalysisResult | None:
        """Parse GPT-4 response and validate all required fields.

        Args:
            raw: raw LLM response text
            model_used: имя фактически ответившей модели (primary/fallback) — пишется
                в AnalysisResult.llm_model. Если не передано, fallback на settings.llm_model.

        Returns AnalysisResult or None if JSON is invalid / unparseable.
        """
        # Strip possible markdown code fences
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                l for l in lines if not l.strip().startswith("```")
            ).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("LLM JSON parse error: %s | raw=%r", exc, raw[:200])
            return None

        if not isinstance(data, dict):
            logger.warning("LLM response is not a dict: %r", data)
            return None

        # --- Validate details ---
        details = data.get("details")
        if not isinstance(details, dict):
            logger.warning("LLM response missing 'details' dict")
            return None

        partial = False

        # Validate each group
        # New format: each criterion is {value: bool|null, reason: str}
        # Also support old format (plain bool/null) for backward compat
        validated_details: dict[str, dict[str, bool | None]] = {}
        reasons: dict[str, dict[str, str]] = {}
        for group, expected_keys in CRITERIA_SCHEMA.items():
            group_data = details.get(group)
            if not isinstance(group_data, dict):
                # Markers group is optional — missing is OK (old prompts, errors, etc.)
                if group == "markers":
                    logger.warning("LLM response missing 'markers' group — filling with nulls")
                    group_data = {}
                else:
                    logger.warning("LLM details missing group '%s'", group)
                    return None

            validated_group: dict[str, bool | None] = {}
            group_reasons: dict[str, str] = {}
            group_timestamps: dict[str, float] = {}
            for key in expected_keys:
                raw_val = group_data.get(key)

                # New format: {"value": ..., "reason": ..., "timestamp": ...}
                if isinstance(raw_val, dict):
                    val = raw_val.get("value")
                    reason = str(raw_val.get("reason", ""))
                    if reason:
                        group_reasons[key] = reason
                    ts = raw_val.get("timestamp")
                    if ts:
                        group_timestamps[key] = _parse_timestamp(str(ts))
                else:
                    val = raw_val

                if val is None:
                    validated_group[key] = None
                elif isinstance(val, bool):
                    validated_group[key] = val
                else:
                    if isinstance(val, str):
                        if val.lower() == "true":
                            validated_group[key] = True
                        elif val.lower() == "false":
                            validated_group[key] = False
                        else:
                            validated_group[key] = None
                            partial = True
                    else:
                        validated_group[key] = None
                        partial = True
                        logger.warning(
                            "LLM details[%s][%s] unexpected type: %r", group, key, raw_val
                        )

            validated_details[group] = validated_group
            reasons[group] = group_reasons
            if group_timestamps:
                reasons[f"{group}_timestamps"] = group_timestamps

        # Apply business logic dependencies
        _apply_dependencies(validated_details)

        # --- Compute scores mathematically ---
        # Note: markers group is EXCLUDED from scoring — informational flags only
        standard_score = _compute_group_score(validated_details["standard"])
        loyalty_score = _compute_group_score(validated_details["loyalty"])
        kindness_score = _compute_group_score(validated_details["kindness"])
        overall_score = round(
            standard_score * 0.4 + loyalty_score * 0.3 + kindness_score * 0.3
        )

        # --- Validate summary ---
        summary = str(data.get("summary", "")).strip()
        if not summary:
            logger.warning("LLM response: empty summary")
            partial = True

        # --- Validate quotes ---
        quotes = data.get("quotes", [])
        if not isinstance(quotes, list):
            quotes = []
            partial = True

        valid_quotes: list[dict[str, str]] = []
        for q in quotes:
            if isinstance(q, dict) and "text" in q and "criterion" in q:
                valid_quotes.append({
                    "text": str(q["text"]),
                    "criterion": str(q["criterion"]),
                    "sentiment": str(q.get("sentiment", "neutral")),
                })

        # Merge reasons into details for frontend
        full_details: dict[str, Any] = {
            **validated_details,
            "reasons": reasons,
        }

        return AnalysisResult(
            standard=standard_score,
            loyalty=loyalty_score,
            kindness=kindness_score,
            overall=overall_score,
            summary=summary,
            details=full_details,
            criteria_version=CRITERIA_VERSION,
            quotes=valid_quotes,
            llm_model=model_used or settings.llm_model,
            partial=partial,
        )
