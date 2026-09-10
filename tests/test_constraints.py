"""
Технологические ограничения: пределы из configs/constraints.yaml обязаны
совпадать с константами validate_output.py и с методикой ЧДД.

Организаторы назвали физически невозможные режимы основанием для
дисквалификации, а расхождение конфига с кодом — типовой источник таких
режимов: правило описано в одном месте, а проверяется по другому.

Запуск:  pytest tests/test_constraints.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "vendor" / "chdd"))

import validate_output as vo  # noqa: E402

CFG_PATH = ROOT / "configs" / "constraints.yaml"


@pytest.fixture(scope="module")
def cfg() -> dict:
    with open(CFG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ------------------------------------------------- конфиг против кода

def test_max_liquid_matches_code(cfg):
    """Потолок дебита жидкости: 500 м3/сут в конфиге и в валидаторе."""
    assert cfg["well"]["max_liquid_rate"] == vo.MAX_LIQUID_RATE == 500.0


def test_min_bhp_producer_matches_code(cfg):
    """Минимальное забойное добывающих — 50 бар, подтверждено организаторами."""
    assert cfg["well"]["min_bhp_producer"] == vo.MIN_BHP_PROD == 50.0


def test_max_bhp_injector_matches_code(cfg):
    """Максимальное забойное нагнетательных — 300 бар, из WCONINJE."""
    assert cfg["well"]["max_bhp_injector"] == vo.MAX_BHP_INJ == 300.0


def test_watercut_limit_is_economic_not_textbook(cfg):
    """
    Экономический предел обводнённости 98,5%, а не 90-95% из литературы.
    Следует из методики: маржа нефти 8360 руб/т против 100 руб/т на подъём.
    Занижение предела приводит к преждевременному закрытию скважин.
    """
    wc = cfg["well"]["economic_watercut_limit"]
    assert wc == 0.985
    assert wc > 0.95, "предел из учебника здесь неверен"


def test_liquid_ceiling_matches_pump_table():
    """
    Потолок 500 м3/сут совпадает с верхней границей самого крупного насоса.
    Если разойдётся — появятся режимы, для которых нет оборудования.
    """
    from chdd_model import DEFAULT_PUMPS
    assert DEFAULT_PUMPS[-1]["max"] == vo.MAX_LIQUID_RATE


# ------------------------------------------------- границы допустимого

@pytest.mark.parametrize("rate,ok", [
    (0.0, True),
    (87.0, True),      # средний дебит фонда
    (499.9, True),
    (500.0, True),     # ровно на границе — допустимо
    (500.1, False),    # выше — методика ЧДД не считает
    (520.0, False),
])
def test_liquid_rate_boundary(rate, ok):
    assert (rate <= vo.MAX_LIQUID_RATE) is ok


@pytest.mark.parametrize("bhp,ok", [
    (45.0, False),     # ниже техминимума
    (49.9, False),
    (50.0, True),      # ровно на границе
    (120.0, True),
    (395.0, True),
])
def test_producer_bhp_boundary(bhp, ok):
    assert (bhp >= vo.MIN_BHP_PROD) is ok


@pytest.mark.parametrize("bhp,ok", [
    (280.0, True),
    (300.0, True),
    (300.1, False),    # риск разрыва пласта
    (420.0, False),
])
def test_injector_bhp_boundary(bhp, ok):
    assert (bhp <= vo.MAX_BHP_INJ) is ok


def test_reservoir_pressure_sanity():
    """Пластовое давление в разумных пределах — защита от битой сводки."""
    assert vo.MIN_PRESSURE == 1.0
    assert vo.MAX_PRESSURE == 500.0
    assert vo.MIN_PRESSURE < vo.MAX_PRESSURE


# ------------------------------------------------- словарь действий

def test_action_vocabulary_closed(cfg):
    """
    Словарь допустимых воздействий закрыт: гипотезы порождаются только
    отсюда. Действие вне списка не должно попасть в расписание.
    """
    allowed = set(cfg["actions"]["allowed"])
    assert allowed == {
        "SET_LIQUID_RATE",
        "SET_BHP",
        "SET_INJECTION_RATE",
        "SHUT_WELL",
        "OPEN_WELL",
    }


def test_conversion_disabled_until_confirmed(cfg):
    """
    Перевод под закачку методика описывает (5 млн), но лекция относила
    его ко второму треку. До подтверждения организаторами не используется.
    """
    assert "CONVERT_TO_INJECTOR" in cfg["actions"]["disabled"]
    assert "CONVERT_TO_INJECTOR" not in cfg["actions"]["allowed"]


def test_allowed_and_disabled_do_not_overlap(cfg):
    """Действие не может быть одновременно разрешено и запрещено."""
    allowed = set(cfg["actions"]["allowed"])
    disabled = set(cfg["actions"].get("disabled") or [])
    assert not (allowed & disabled)


# ------------------------------------------------- частота вмешательств

def test_min_interval_covers_response_lag(cfg):
    """
    Минимальный интервал между воздействиями не меньше медианы лага отклика
    (6 месяцев по матрице влияния). Иначе решение принимается до того, как
    проявился эффект предыдущего — вслепую.
    """
    interval = cfg["well"]["min_interval_months"]
    median_lag = 6
    assert interval >= median_lag, (
        f"интервал {interval} мес меньше медианы лага {median_lag} мес"
    )


def test_min_interval_fits_horizon(cfg):
    """
    При горизонте 23 месяца интервал должен допускать хотя бы два
    вмешательства на скважину, иначе управление вырождается в одно решение.
    """
    horizon = 23
    interval = cfg["well"]["min_interval_months"]
    assert horizon // interval >= 2, (
        f"при интервале {interval} мес на горизонте {horizon} мес "
        f"помещается лишь {horizon // interval} вмешательство"
    )


# ------------------------------------------------- групповые ограничения

def test_field_limits_absent_on_training_model(cfg):
    """
    На обучающей модели групповых ограничений нет — подтверждено
    организаторами. В валидационном кейсе придут квоты и лимиты,
    и ключи должны существовать заранее, чтобы код их подхватил.
    """
    field = cfg["field"]
    for key in ("max_total_liquid", "max_total_injection",
                "max_actions_per_step"):
        assert key in field, f"нет ключа {key} — валидационный кейс не ляжет"
        assert field[key] is None


# ------------------------------------------------- защита от регрессий

def test_no_hardcoded_limits_in_validator():
    """
    Валидатор берёт пределы из констант модуля, а не из литералов
    внутри функций. Проверка грубая, но ловит копипасту чисел.
    """
    src = (ROOT / "scripts" / "validate_output.py").read_text(encoding="utf-8")
    body = src.split("def validate(", 1)[-1]
    for literal in ("> 500", "< 50.0", "> 300.0"):
        assert literal not in body, (
            f"литерал {literal!r} внутри validate() — предел должен "
            f"браться из константы модуля"
        )
