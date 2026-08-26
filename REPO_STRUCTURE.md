# Структура репозитория

Три зоны, различающиеся судьбой при коммите:

| Зона | Что | В git |
|---|---|---|
| `vendor/` | материалы организаторов — неизменяемый эталон | да, кроме тяжёлых `.inc` |
| код и конфиги | скрипты, настройки, документация | да |
| `~/aios-data/` | результаты прогонов | нет, вне репозитория |

---

```
aios/
│
├── README.md                      описание проекта и скриптов
├── .gitignore
├── .gitattributes                 CRLF -> LF: дека пришла из Windows
│
├── vendor/                        ЭТАЛОН ОРГАНИЗАТОРОВ — не править
│   ├── chdd/                      CHDD_PYTHON: официальный расчёт ЧДД
│   │   ├── chdd_model.py          ★ единственный источник правды по деньгам
│   │   ├── excel_io.py            маппинг заголовков, чтение xlsx
│   │   └── input/                 нормативы и пример данных
│   ├── model/Model_Y/             дека как выдали
│   │   ├── MODEL_Y.DATA
│   │   ├── INCLUDE/               grid, pvt, rp, regs, init, sch (в .gitignore)
│   │   └── SUMMARY/SUMMARY.INC
│   ├── docs/                      ТЗ, методика ЧДД, регламент, лекции
│   └── VENDOR.md                  что откуда и когда получено
│
├── model/                         РАБОЧИЕ КОПИИ ДЕКИ (в .gitignore)
│   ├── Model_Y_opm/               адаптирована под OPM Flow
│   ├── Model_Y_base/              опорный сценарий, режимы заморожены
│   └── CHANGES_FOR_OPM.md         ★ журнал правок деки
│
├── configs/
│   ├── run.yaml                   ★ даты, горизонт, параметры прогона
│   ├── constraints.yaml           лимиты и словарь действий
│   └── triggers.yaml              условия вмешательства
│
├── scripts/                       ВСЯ ОБВЯЗКА, единственная копия
│   │
│   ├── lib_common.sh              ★ пути, стражи диска и памяти, run_flow
│   ├── setup_dirs.sh              структура каталогов, проверка окружения
│   ├── Dockerfile                 образ OPM Flow 2026.04
│   ├── INSTALL.md                 установка WSL2 + Docker + OPM
│   │
│   ├── smoke_test.sh              проверка контура на SPE1
│   ├── benchmark.sh               замер: время, параллельность, место
│   │
│   ├── npv_official.py            ★ обёртка над эталоном ЧДД
│   ├── summary_to_csv.py          ★ мост UNSMRY -> 14 колонок
│   ├── verify_reference.sh        сверка с эталоном организаторов
│   │
│   ├── prepare_history.sh         прогон истории -> снимок + датасет
│   ├── prepare_baseline.sh        заморозка режимов -> планка ЧДД
│   ├── schedule_builder.py        ★ генератор wells_schedule.inc
│   ├── run_schedule.sh            прогон расписания + сравнение с планкой
│   │
│   ├── probe_wells.py             возмущения и разбор откликов
│   ├── run_probes.sh              прогон 99 вариантов
│   ├── analyze_probes.sh          сборка матрицы влияния
│   │
│   ├── rolling_horizon.py         ★ цикл скользящего горизонта
│   ├── read_state.py              чтение состояния (внутри контейнера)
│   │
│   ├── candidates.py              генератор кандидатов, дискретная сетка
│   ├── run_candidates.sh          пакетный прогон кандидатов
│   ├── night_run.sh               двухэтапный ночной сценарий
│   ├── run_batch.sh               нагрузочный прогон одной деки
│   │
│   ├── validate_output.py         ★ физическая корректность результата
│   ├── validate.sh                обёртка валидатора
│   │
│   ├── llm_client.py              клиент LLM: off / live / replay
│   │
│   ├── cleanup.sh                 уборка результатов
│   ├── cleanup_stale.sh           удаление данных устаревшей даты
│   └── git_prepare.sh             подготовка репозитория к коммиту
│
├── docs/
│   ├── methodology_notes.md       ★ трактовки спорных мест методики
│   ├── PROBE-WELLS(MATRIX).md     матрица влияния: как и зачем
│   ├── ROLLING_HORIZON.md         цикл и триггеры
│   ├── DATA_MANIFEST.md           что в облаке, что воспроизводится
│   ├── LLM_SETUP.md               установка и проверка модели
│   ├── architecture.md            функциональная и компонентная схемы
│   ├── decisions/                 ADR: одно решение — один файл
│   └── diagrams/                  .drawio исходники
│
├── tests/
│   ├── test_npv_parity.py         свой расчёт против эталона
│   ├── test_esp.py                гистерезис насосов
│   ├── test_schedule_roundtrip.py ★ .inc -> симулятор -> чтение
│   └── test_constraints.py        проверка лимитов
│
├── src/aios/                      КОД СИСТЕМЫ (в разработке)
│   ├── core/                      контракты, ограничения, диагностика
│   ├── interference/              построение графа связей
│   ├── adapters/                  симулятор, LLM, пул процессов
│   ├── agents/                    аналитик, стратег, критик + промпты
│   ├── optimize/                  сетка, Candidate Packer
│   └── orchestrator/              граф агентов, трейс
│
├── ui/                            дашборд (в разработке)
│
└── submission/                    ЧТО УХОДИТ ОРГАНИЗАТОРАМ
    ├── wells_schedule.inc         ★ расписание
    ├── npv_claimed.txt            заявленный абсолютный ЧДД
    └── RUN.md                     как воспроизвести
```

---

## Вне репозитория

```
~/aios-data/
├── runs/
│   ├── history_full/              история 2007-2015, сверена с эталоном
│   ├── baseline/                  планка ЧДД
│   └── candidates/                пробные прогоны (чистятся)
├── probes/                        99 возмущений + response_matrix.csv
├── rolling/                       результаты цикла + journal.json
├── cache/                         хеш деки -> результат
├── llm_cache/                     ответы модели для replay
└── logs/                          disk_stop, mem_stop, прогоны
```

Полный объём около 8 ГБ, воспроизводится за полтора часа. Что выкладывать
команде — в `docs/DATA_MANIFEST.md`.

---

## Файлы, от которых зависит допуск

| Файл | Почему |
|---|---|
| `scripts/npv_official.py` | расхождение с эталоном = дисквалификация |
| `scripts/schedule_builder.py` | `.inc` должен читаться моделью без правок |
| `scripts/summary_to_csv.py` | без моста эталонный расчёт не запустить |
| `scripts/validate_output.py` | одна попытка, повторов нет |
| `README.md` | без инструкции решение не допускается |
| `docker-compose.yml` | требование «запуск одной командой» |

---

## `.gitignore`

```gitignore
# тяжёлые файлы модели
vendor/model/*/INCLUDE/*.inc
vendor/model/*/*.inc
vendor/model/*/USER/
model/*/

# выход симулятора
*.UNSMRY  *.UNRST  *.ESMRY  *.SMSPEC
*.EGRID   *.INIT   *.PRT    *.DBG

# окружение
__pycache__/  *.pyc  .venv/  node_modules/  .env

# результаты
runs/  cache/  *.log  *.xlsx  *.csv  .lock  *.tar.gz
```

Сводные `*.json` (ranking, npv, result) — исключение, они маленькие и
нужны для истории результатов.

---

## Ветки

| Ветка | Кто | Для чего |
|---|---|---|
| `main` | все | только рабочее состояние |
| `dev` | все | интеграционная |
| `infra/*` | Кирилл | контейнер, скрипты, прогоны |
| `sim/*` | Кирилл | ScheduleBuilder, адаптер симулятора |
| `agents/*` | Кирилл | LLM-слой, промпты, оркестратор |
| `res/*` | Камелия | ограничения, диагностика, нормативы |
| `arch/*` | Руслан | архитектура, документация |

Работа: ветка от `dev`, слияние через merge request. Перед сдачей —
тег `submission-v1`, чтобы знать, какой код породил сданное число.
