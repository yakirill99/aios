"""Воркер очереди: берёт задание и запускает rolling_horizon.py.

Запускается там, где есть OPM Flow и права на запись в ~/aios-data —
на хосте или в контейнере из scripts/Dockerfile. Дашборд этого не
делает и делать не должен.

    python -m aios.dashboard.worker

Командная строка собирается под НАСТОЯЩИЙ интерфейс цикла:

    rolling_horizon.py run --base <дека> --workdir <каталог прогона>
                           --cut ... --end ... --step ... --candidates ...
                           --parallel ... --llm ... [--matrix ...]
                           [--constraints ...]

Оверлея конфига нет: у цикла нет ключа --config, он настраивается
флагами. Число шагов из формы превращается в --end, потому что цикл
считает горизонт по датам, а не по количеству окон.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from aios.dashboard import collect, jobs

log = logging.getLogger("aios.worker")

# Тот же контракт путей, что у rolling_horizon.py и lib_common.sh
AIOS_ROOT = Path(os.environ.get("AIOS_ROOT", "~/aios")).expanduser()
DATA_ROOT = Path(
    os.environ.get("DATA_ROOT", os.environ.get("AIOS_DATA_DIR", "~/aios-data"))
).expanduser()

RUN_SCRIPT = os.getenv("AIOS_RUN_SCRIPT", str(AIOS_ROOT / "scripts" / "rolling_horizon.py"))
PYTHON = os.getenv("AIOS_PYTHON", sys.executable)
POLL_SECONDS = int(os.getenv("AIOS_WORKER_POLL", "5"))

_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def add_months(d: date, months: int) -> date:
    """Сдвиг на месяцы — тот же расчёт, что в цикле, иначе горизонты разъедутся."""
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    last = _DAYS[month]
    if month == 1 and year % 4 == 0 and (year % 100 or year % 400 == 0):
        last = 29
    return date(year, month + 1, min(d.day, last))


def build_command(job: dict[str, Any]) -> list[str]:
    """Собирает аргументы rolling_horizon.py из задания."""
    params = dict(job.get("params") or {})
    deck = job["deck"]

    cut = date.fromisoformat(params.get("cut", "2014-01-01"))
    step_months = int(params.get("step_months", 3))
    steps = int(params.get("steps", 6))
    end = add_months(cut, steps * step_months)

    base = Path(deck["path"])
    if not base.is_absolute():
        base = AIOS_ROOT / base

    cmd = [
        PYTHON, RUN_SCRIPT, "run",
        "--base", str(base),
        "--workdir", str(DATA_ROOT / job["out_dir"]),
        "--cut", cut.isoformat(),
        "--end", end.isoformat(),
        "--step", str(step_months),
        "--candidates", str(params.get("candidates", 25)),
        "--parallel", str(params.get("parallel", 6)),
        "--llm", str(params.get("llm_mode", "off")),
    ]

    matrix = DATA_ROOT / "probes" / "response_matrix.csv"
    if matrix.is_file():
        cmd += ["--matrix", str(matrix)]

    constraints = deck.get("constraints")
    if constraints:
        path = Path(constraints)
        if not path.is_absolute():
            path = AIOS_ROOT / path
        if path.is_file():
            cmd += ["--constraints", str(path)]

    return cmd


def expected_steps(job: dict[str, Any]) -> int:
    return int((job.get("params") or {}).get("steps", 0) or 0)


def read_progress(out_dir: Path) -> int:
    """Сколько шагов цикл уже записал в свой journal.json.

    Цикл сохраняет журнал после каждого шага, поэтому длина списка —
    честный прогресс, а не оценка.
    """
    try:
        with (out_dir / "journal.json").open(encoding="utf-8") as f:
            return len(json.load(f).get("journal", []))
    except (FileNotFoundError, ValueError, OSError):
        return 0


def run_job(job: dict[str, Any]) -> None:
    run_id = job["run_id"]
    total = expected_steps(job)
    jobs.update(run_id, status="running", started_at=jobs.now(),
                steps_done=0, steps_total=total)
    jobs.append_log(run_id, f"Взято в работу, дека {job['deck']['id']}")

    try:
        if job.get("instant"):
            # Проигрывание записи: симулятор не нужен, достаточно
            # пересобрать JSON из уже имеющегося журнала.
            jobs.append_log(run_id, "Проигрывание записи, симулятор не запускается")
            _collect(run_id)
            jobs.update(run_id, status="done", detail="Запись проиграна",
                        steps_done=total, steps_total=total)
            return

        script = Path(RUN_SCRIPT)
        if not script.is_file():
            raise RuntimeError(
                f"Не найден скрипт цикла: {script}. Задайте AIOS_RUN_SCRIPT или AIOS_ROOT."
            )

        out_dir = DATA_ROOT / job["out_dir"]
        out_dir.mkdir(parents=True, exist_ok=True)

        cmd = build_command(job)
        jobs.append_log(run_id, "Запуск: " + " ".join(cmd))
        log.info("[%s] %s", run_id, " ".join(cmd))

        with (out_dir / "run.log").open("a", encoding="utf-8") as logfile:
            process = subprocess.Popen(cmd, stdout=logfile, stderr=subprocess.STDOUT,
                                       cwd=str(AIOS_ROOT))
            last = -1
            while process.poll() is None:
                done = read_progress(out_dir)
                if done != last:
                    jobs.update(run_id, steps_done=done, steps_total=total)
                    if done:
                        jobs.append_log(run_id, f"Шаг {done} из {total} завершён")
                    last = done

                jobs.heartbeat(f"{run_id}: шаг {done} из {total}")
                current = jobs.read(run_id)
                if current and current.get("status") == "cancelled":
                    process.terminate()
                    jobs.append_log(run_id, "Остановлено по отмене")
                    return
                time.sleep(2)

        if process.returncode != 0:
            raise RuntimeError(
                f"Цикл завершился с кодом {process.returncode}, "
                f"подробности в {out_dir / 'run.log'}"
            )

        jobs.update(run_id, steps_done=read_progress(out_dir))
        jobs.append_log(run_id, "Прогон завершён, собираю данные для дашборда")
        _collect(run_id, out_dir)
        jobs.update(run_id, status="done", detail="Прогон завершён")

    except Exception as exc:  # воркер не должен падать из-за одного задания
        log.exception("[%s] задание провалено", run_id)
        jobs.append_log(run_id, f"Ошибка: {exc}")
        jobs.update(run_id, status="failed", detail=str(exc))


def _collect(run_id: str, out_dir: Path | None = None) -> None:
    """Пересобирает state.json/journal.json, не роняя задание."""
    try:
        if out_dir is not None:
            os.environ["AIOS_LIVE_DIR"] = str(out_dir)
        collect.main()
    except SystemExit as exc:
        if exc.code:
            jobs.append_log(run_id, f"collect вернул код {exc.code}")
    except Exception as exc:
        jobs.append_log(run_id, f"collect не отработал: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Воркер очереди прогонов AIOS")
    parser.add_argument("--once", action="store_true", help="взять одно задание и выйти")
    parser.add_argument("--dry-run", action="store_true",
                        help="показать команду для следующего задания и не запускать")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv("AIOS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("Воркер запущен. Очередь: %s", jobs.QUEUE_DIR)
    log.info("Скрипт цикла: %s", RUN_SCRIPT)
    if not Path(RUN_SCRIPT).is_file():
        log.warning("Скрипт не найден — пойдут только задания с instant")

    if args.dry_run:
        job = jobs.next_queued()
        if job is None:
            print("Очередь пуста")
            return 0
        print("Задание:", job["run_id"])
        print("Команда:", " ".join(build_command(job)))
        return 0

    jobs.heartbeat("ожидание заданий")
    while True:
        job = jobs.next_queued()
        if job is not None:
            jobs.heartbeat(f"работает над {job['run_id']}")
            run_job(job)
            jobs.heartbeat("ожидание заданий")
        elif args.once:
            log.info("Очередь пуста")
            return 0
        if args.once:
            return 0
        jobs.heartbeat("ожидание заданий")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
