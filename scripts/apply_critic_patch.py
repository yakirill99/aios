#!/usr/bin/env python3
"""
Внедряет фильтр объяснений критика прямо в agents.py.

    python3 apply_critic_patch.py                    # применить
    python3 apply_critic_patch.py --check            # только проверить
    python3 apply_critic_patch.py --revert           # откатить

Что делает: добавляет функцию check_claims() и вызов её в методе
explain() сразу после scrub(). Промпт не трогается — иначе изменились бы
ключи llm_cache и сломался режим replay.

Перед правкой кладёт рядом agents.py.bak_critic.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

TARGET = Path(sys.argv[sys.argv.index("--file") + 1]) if "--file" in sys.argv \
    else Path(__file__).resolve().parent / "agents.py"

MARK = "# --- фильтр объяснений критика ---"

FUNC = '''

# --- фильтр объяснений критика ---
# Критик объяснял шаг словами про «своевременное закрытие скважин», хотя
# ничего не закрывалось. На расчёт это не влияет — роль объяснительная, —
# но такой текст уходит в отчёт и на дашборд.
#
# Проверка стоит ПОСЛЕ ответа, рядом со scrub(), который уже вычищает
# выдуманные числа. Промпт не меняется намеренно: его текст входит в ключ
# кэша, и правка обесценила бы cache/llm, на котором держится replay.
_ACTION_CLAIMS = {
    "shut": ("закрыт", "закрыв", "закрыл", "закрытие", "закрытия",
             "останов", "отключ", "вывед из эксплуатации"),
    "cut": ("снижени", "снизил", "придуш", "сократил отбор"),
    "inj": ("перевод под закачку", "переведен под закачку"),
}


def check_claims(text: str, best_tag: str, actions=None) -> str:
    """
    Пусто, если объяснение приписывает решению действия, которых в нём
    нет. Что было сделано, определяется тегом варианта и фактическими
    воздействиями, а не текстом модели.
    """
    if not text:
        return text
    tag = (best_tag or "").lower()
    low = text.lower()

    controls = {str(getattr(a, "control", "")).upper() for a in (actions or [])}
    has_shut = "shut" in tag or "SHUT" in controls
    has_cut = "x0" in tag
    has_inj = "inj" in tag or "RATE" in controls

    bad = []
    if not has_shut:
        bad += [w for w in _ACTION_CLAIMS["shut"] if w in low]
    if not (has_cut or has_shut):
        bad += [w for w in _ACTION_CLAIMS["cut"] if w in low]
    if not has_inj:
        bad += [w for w in _ACTION_CLAIMS["inj"] if w in low]

    return "" if bad else text
'''

OLD_BLOCK = '''        res = res or fallback
        res.explanation = scrub(res.explanation)
        res.watch = scrub(res.watch)
        self._trace("critic", {"step": step, "best": best_tag,
                               "gain": gain_mln, "risk": res.risk,
                               "explanation": res.explanation})'''

NEW_BLOCK = '''        res = res or fallback
        res.explanation = scrub(res.explanation)
        res.watch = scrub(res.watch)

        # Объяснение с несовершёнными действиями отбрасывается целиком:
        # сухая формулировка правил лучше правдоподобного вымысла.
        kept = check_claims(res.explanation, best_tag,
                            getattr(self, "_last_actions", None))
        rejected_claim = not kept
        if rejected_claim:
            res.explanation = fallback.explanation

        self._trace("critic", {"step": step, "best": best_tag,
                               "gain": gain_mln, "risk": res.risk,
                               "explanation": res.explanation,
                               "rejected_claim": rejected_claim})'''


def status(src: str) -> str:
    if MARK in src and "rejected_claim" in src:
        return "применён"
    if MARK in src or "rejected_claim" in src:
        return "применён частично"
    return "не применён"


def main() -> int:
    if not TARGET.exists():
        print(f"нет файла {TARGET}")
        return 1
    src = TARGET.read_text(encoding="utf-8")
    st = status(src)
    print(f"файл:  {TARGET}")
    print(f"патч:  {st}")

    if "--check" in sys.argv:
        return 0

    bak = TARGET.with_suffix(".py.bak_critic")

    if "--revert" in sys.argv:
        if not bak.exists():
            print("копии .bak_critic нет, откатывать нечего")
            return 1
        shutil.copy2(bak, TARGET)
        print(f"откатано из {bak}")
        return 0

    if st == "применён":
        print("уже применён, ничего не делаю")
        return 0

    if OLD_BLOCK not in src:
        print("\nНЕ НАШЁЛ блок в explain(). Файл отличается от ожидаемого.")
        print("Вставьте вручную: функцию из этого файла и вызов check_claims")
        print("после двух строк scrub() в методе explain().")
        return 1

    shutil.copy2(TARGET, bak)
    print(f"копия: {bak}")

    out = src.replace(OLD_BLOCK, NEW_BLOCK, 1)

    # Функция ставится перед блоком запуска, если он есть: иначе при
    # прямом вызове agents.py main() выполнится раньше её определения.
    m = re.search(r'\nif __name__ == ["\']__main__["\']:', out)
    out = (out[:m.start()] + "\n" + FUNC + out[m.start():]) if m else out + FUNC

    TARGET.write_text(out, encoding="utf-8")
    print("правка внесена")

    r = subprocess.run([sys.executable, "-c",
                        f"import sys; sys.path.insert(0, '{TARGET.parent}'); "
                        "import agents; "
                        "print('импорт ок, check_claims:', "
                        "callable(agents.check_claims))"],
                       capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        print("\nИМПОРТ УПАЛ — откатываю")
        shutil.copy2(bak, TARGET)
        return 1

    r = subprocess.run([sys.executable, "-c",
                        f"import sys; sys.path.insert(0, '{TARGET.parent}'); "
                        "from agents import check_claims as c; "
                        "was='...стало своевременное закрытие скважин...'; "
                        "ok='форсирование отбора даёт эффект без лага'; "
                        "print('врущее при PACK_prod_x1p5:', "
                        "'ОТБРОШЕНО' if not c(was,'PACK_prod_x1p5') else 'ПРОПУЩЕНО'); "
                        "print('корректное:', "
                        "'оставлено' if c(ok,'PACK_prod_x1p5') else 'ОТБРОШЕНО'); "
                        "print('закрытие при теге 1_shut:', "
                        "'оставлено' if c(was,'1_shut') else 'ОТБРОШЕНО')"],
                       capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    print("\nГотово. Откат: python3 apply_critic_patch.py --revert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
