"""
Гистерезис ЭЦН: правило замены насоса асимметрично.

Вверх  — замена сразу, как только дебит превысил max текущего насоса.
Вниз   — замена только если (min_текущего − дебит) > downsizeThreshold (100 м3/сут).

Проверяется против DEFAULT_PUMPS и DEFAULT_ASSUMPTIONS эталонного кода
организаторов: если они поменяют таблицу или порог, тест это поймает.

Каждая замена стоит costM нового насоса + pumpOperationCostM (1.8 млн).
Ошибка в направлении сравнения даёт систематическое смещение ЧДД,
которое не проявляется ни падением, ни исключением.

Запуск:  pytest tests/test_pump_hysteresis.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "vendor" / "chdd"))

from chdd_model import (  # noqa: E402
    DEFAULT_ASSUMPTIONS,
    DEFAULT_PUMPS,
    _pick_downsize_pump,
    _pick_upsize_pump,
)

THRESHOLD = DEFAULT_ASSUMPTIONS["downsizeThreshold"]
OP_COST = DEFAULT_ASSUMPTIONS["pumpOperationCostM"]


def pump_by_nominal(nominal: float) -> dict:
    for p in DEFAULT_PUMPS:
        if p["nominal"] == nominal:
            return p
    raise AssertionError(f"нет насоса с номиналом {nominal} в DEFAULT_PUMPS")


def needs_upsize(rate: float, pump: dict) -> bool:
    """Условие замены вверх — как в chdd_model: rate > current_pump['max']."""
    return rate > pump["max"]


def needs_downsize(rate: float, pump: dict) -> bool:
    """Условие замены вниз — (min - rate) > downsizeThreshold."""
    return (pump["min"] - rate) > THRESHOLD


# ---------------------------------------------------------------- константы

def test_threshold_is_100():
    """Порог понижения — 100 м3/сут. Если эталон изменится, тест упадёт."""
    assert THRESHOLD == 100.0


def test_pump_table_intact():
    """Таблица типоразмеров: 12 позиций, от 15 до 400 м3/сут."""
    assert len(DEFAULT_PUMPS) == 12
    assert DEFAULT_PUMPS[0]["nominal"] == 15
    assert DEFAULT_PUMPS[-1]["nominal"] == 400
    assert DEFAULT_PUMPS[-1]["max"] == 500  # совпадает с потолком методики


def test_pump_intervals_are_monotonic():
    """Интервалы упорядочены и перекрываются — иначе подбор даст дыры."""
    for a, b in zip(DEFAULT_PUMPS, DEFAULT_PUMPS[1:]):
        assert a["nominal"] < b["nominal"]
        assert a["max"] >= b["min"], (
            f"разрыв между {a['nominal']} и {b['nominal']}: "
            f"дебит в ({a['max']}, {b['min']}) не покрыт"
        )


# ---------------------------------------------------------------- вверх

@pytest.mark.parametrize("rate,nominal,expected", [
    (150.0, 80, True),    # 150 > max=100 → замена
    (101.0, 80, True),    # чуть выше границы → замена
    (100.0, 80, False),   # ровно на границе → не замена (строгое >)
    (99.0, 80, False),    # внутри интервала
    (60.0, 80, False),    # внутри интервала, ниже номинала
])
def test_upsize_rule(rate, nominal, expected):
    """Вверх меняем сразу при превышении max, без порога."""
    assert needs_upsize(rate, pump_by_nominal(nominal)) is expected


def test_upsize_has_no_threshold():
    """
    Ключевая асимметрия: превышение на 1 м3/сут уже требует замены,
    тогда как вниз нужно отставание больше 100.
    """
    pump = pump_by_nominal(80)          # интервал 50..100
    assert needs_upsize(100.5, pump) is True
    assert needs_downsize(49.5, pump) is False   # отставание всего 0.5


# ---------------------------------------------------------------- вниз

@pytest.mark.parametrize("rate,nominal,expected", [
    (40.0, 80, False),    # min=50, отставание 10 → не меняем
    (30.0, 160, True),    # min=125, отставание 95 → не меняем? проверка ниже
])
def test_downsize_rule_basic(rate, nominal, expected):
    pump = pump_by_nominal(nominal)
    got = needs_downsize(rate, pump)
    # для 160: min=125, 125-30=95 < 100 → False
    if nominal == 160:
        assert got is False, "отставание 95 меньше порога 100"
    else:
        assert got is expected


@pytest.mark.parametrize("rate,nominal,expected", [
    (24.0, 160, True),    # 125-24 = 101 > 100 → замена
    (25.0, 160, False),   # 125-25 = 100, не строго больше → нет
    (26.0, 160, False),   # 99 < 100 → нет
    (0.0, 80, False),     # 50-0 = 50 < 100 → нет даже при нулевом дебите
    (0.0, 250, True),     # 210-0 = 210 > 100 → замена
])
def test_downsize_threshold_boundary(rate, nominal, expected):
    """Граница порога: строгое неравенство, ровно 100 не срабатывает."""
    assert needs_downsize(rate, pump_by_nominal(nominal)) is expected


def test_zero_rate_on_small_pump_keeps_pump():
    """
    Скважина остановлена, стоит малый насос — замены не происходит.
    Иначе каждая остановка тянула бы за собой лишние миллионы.
    """
    for nominal in (15, 20, 30, 50, 80):
        assert needs_downsize(0.0, pump_by_nominal(nominal)) is False


# ---------------------------------------------------------------- подбор

def test_upsize_pick_is_bigger():
    """При повышении выбирается насос строго большего номинала."""
    cur = pump_by_nominal(80)
    new = _pick_upsize_pump(150.0, cur, DEFAULT_PUMPS)
    assert new["nominal"] > cur["nominal"]
    assert new["min"] <= 150.0 <= new["max"]


def test_upsize_beyond_table_returns_largest():
    """Дебит выше всей таблицы — возвращается самый крупный насос."""
    cur = pump_by_nominal(80)
    new = _pick_upsize_pump(600.0, cur, DEFAULT_PUMPS)
    assert new["nominal"] == 400


def test_downsize_pick_is_smaller():
    """При понижении выбирается насос строго меньшего номинала."""
    cur = pump_by_nominal(250)
    new = _pick_downsize_pump(50.0, cur, DEFAULT_PUMPS)
    assert new["nominal"] < cur["nominal"]


# ---------------------------------------------------------------- стоимость

def test_operation_cost_added_to_pump_cost():
    """
    Полная стоимость события = цена насоса + операция.
    Из методики: операция 1.8 млн, самый дешёвый насос 0.55.
    """
    assert OP_COST == 1.8
    cheapest = min(p["costM"] for p in DEFAULT_PUMPS)
    dearest = max(p["costM"] for p in DEFAULT_PUMPS)
    assert cheapest + OP_COST == pytest.approx(2.35, abs=0.01)
    assert dearest + OP_COST == pytest.approx(9.85, abs=0.01)


def test_payback_in_tonnes():
    """
    Порог окупаемости события в тоннах нефти при марже 8360 руб/т.
    Используется генератором гипотез: воздействие, не дающее прироста
    выше порога, экономически бессмысленно.
    """
    margin_rub_t = 8360.0
    cheap = (0.55 + OP_COST) * 1e6 / margin_rub_t
    dear = (8.05 + OP_COST) * 1e6 / margin_rub_t
    assert cheap == pytest.approx(281, abs=5)
    assert dear == pytest.approx(1178, abs=5)


# ---------------------------------------------------------------- сценарий

def test_no_oscillation_between_two_pumps():
    """
    Гистерезис обязан исключать переключения туда-обратно.
    Скважина колеблется вокруг границы 100 — насос меняется один раз.
    """
    pump = pump_by_nominal(80)          # 50..100
    changes = 0
    for rate in (95, 105, 95, 105, 95):
        if needs_upsize(rate, pump):
            pump = _pick_upsize_pump(rate, pump, DEFAULT_PUMPS)
            changes += 1
        elif needs_downsize(rate, pump):
            pump = _pick_downsize_pump(rate, pump, DEFAULT_PUMPS)
            changes += 1
    assert changes == 1, (
        f"насос переключался {changes} раз при колебании вокруг границы — "
        f"гистерезис не работает"
    )
