# Установка с нуля. Для организаторов и команды

Пошагово, на чистой машине, без предположений о том, что уже стоит.
Каждый шаг заканчивается проверкой: если она не прошла, дальше идти
нельзя.

Проверено 11.09.2026 на Ubuntu 24.04 в WSL2. Время: около получаса,
из них двадцать минут — скачивание архива и сборка образа симулятора.

Короткая версия для жюри — `submission/RUN.md`. Здесь полный путь со
всеми граблями, на которые мы наступили сами.

---

## 0. Что понадобится

| Что | Зачем | Обязательно |
|---|---|---|
| Docker с плагином Compose | симулятор и дашборд | да |
| git | склонировать репозиторий | да |
| python3 (3.10+) | расчёт ЧДД, сборщик дашборда | да |
| curl, wget, tar, zstd | скачать и распаковать архив | да |
| 8 ГБ свободного места | архив, дека, результаты прогонов | да |
| 6+ потоков CPU | параллельные прогоны кандидатов | для раздела 6 |

Проверка:

```bash
docker --version && docker compose version
git --version && python3 --version
tar --zstd --help >/dev/null && echo "zstd есть"
df -h ~ | tail -1
```

Если `docker compose version` ругается, установлен старый
`docker-compose` — поставьте плагин:
`sudo apt install docker-compose-plugin`.

Если `docker ps` требует sudo, добавьте себя в группу и перелогиньтесь:
`sudo usermod -aG docker $USER`.

---

## 1. Репозиторий

```bash
cd ~
git clone --branch submission-v1 \
  https://hackathon-aios.gitlab.yandexcloud.net/ans_kaz/big-data-fish.git aios
cd aios
```

Репозиторий приватный: git спросит логин и пароль GitLab. При вводе
логина не копируйте лишних символов — при скрытом непечатном символе
сервер отвечает `400 Bad Request`, а не «неверный пароль», и это
сбивает с толку.

Проверка:

```bash
cat submission/npv_claimed.txt          # 1165.899
ls cache/llm/*.json | wc -l             # 112
git describe --tags                     # submission-v1
```

Клон встаёт в состояние detached HEAD — так и должно быть, тег
указывает на конкретный коммит.

---

## 2. Переменные окружения

Весь код ходит по двум путям. Пока они не заданы, скрипты будут искать
данные не там.

```bash
export AIOS_ROOT=$HOME/aios
export DATA_ROOT=$HOME/aios-data
echo "export AIOS_ROOT=$HOME/aios"      >> ~/.bashrc
echo "export DATA_ROOT=$HOME/aios-data" >> ~/.bashrc
```

---

## 3. Архив данных

**Делайте это ДО первого запуска дашборда.** Docker при старте создаёт
каталог `~/aios-data` под точку монтирования с владельцем root, и
распаковка в него потом упирается в права.

Публичная ссылка Яндекс.Диска ведёт на страницу просмотра: `wget` по
ней скачает 53 КБ вёрстки вместо архива. Прямая ссылка берётся через
API:

```bash
cd ~
PUB="https://disk.yandex.ru/d/fDUvbF-NcpWExA"
HREF=$(curl -s "https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key=$PUB" \
       | python3 -c "import sys,json;print(json.load(sys.stdin)['href'])")
wget -O aios-submit-data.tar.zst "$HREF"
```

Проверка до распаковки — размер около 126 МБ и правильный формат:

```bash
ls -lh aios-submit-data.tar.zst
file aios-submit-data.tar.zst           # Zstandard compressed data
```

Если размер в десятки килобайт — скачалась страница, повторите с
`HREF`. Можно и просто сохранить файл через браузер.

Распаковка и раскладка:

```bash
tar --zstd -xf aios-submit-data.tar.zst -C ~
sha256sum -c ~/SHA256SUMS | grep -v ': OK$' || echo "суммы сошлись"

mv ~/data  ~/aios-data
mv ~/model ~/aios/model
```

Проверка:

```bash
ls $DATA_ROOT                           # full34, abl_*, kam_*, probes, runs, llm_cache
ls $AIOS_ROOT/model/Model_Y_opm | head
python3 -c "
import json; d=json.load(open('$DATA_ROOT/full34/result.json'))
print('ЧДД', d['npv'], '| планка', d['baseline'], '| допущения', d['assumptions'])"
```

Ожидается 1165.899…, планка 1018.9855…, допущения пустые.

---

## 4. Проверка числа. Пять минут, без симулятора

```bash
cd $AIOS_ROOT/scripts
python3 npv_official.py $DATA_ROOT/full34/runs/FINAL/chdd_input.csv
```

Ожидаемый вывод:

```
ЧДД = 1,165.899 млн руб   (2014–2015, эталон 7.0.2-negative-row-filter)
 2014      122.9    1,053.2 ...  618.4
 2015      121.8    1,206.9 ...  547.5
итого: нефть 244.7 тыс.т, жидкость 2,260.2 тыс.т, обводнённость 89.2%
```

У флага `--annual-csv` есть обязательный аргумент — путь к файлу.
Просто `--annual-csv` без пути даст ошибку разбора аргументов.

Это и есть главная проверка: заявленное число получено вашим кодом из
таблицы притока нашего расчёта.

---

## 5. Дашборд

```bash
cd $AIOS_ROOT
cp .env.example .env
docker compose up --build -d
```

Первая сборка — около десяти секунд, дальше секунды.

Куда заходить:

| Адрес | Что там |
|---|---|
| <http://localhost:8000> | **главный экран**: ЧДД против планки, фонд, агенты |
| <http://localhost:8000/dashboard.html> | **журнал прогона**: шаг за шагом, кандидаты, выигрыш |
| <http://localhost:8000/docs> | документация API, можно дёргать ручки из браузера |

На главном экране слева вверху — переключатель **«простой /
продвинутый»**. В простом часть карточек скрыта; продвинутый
раскрывает гидродинамическую модель, управляющие воздействия,
содержимое `wells_schedule.inc`, параметры оптимизации и лог. Если
экран кажется полупустым — вы в простом режиме.

С экрана журнала кнопки «назад» нет, возвращайтесь кнопкой браузера.

Проверка:

```bash
curl -s localhost:8000/health
curl -sD- localhost:8000/api/state -o /dev/null | grep -i x-data-source
```

Пока сводка не собрана, ответ `demo` — это штатно.

Показать наш прогон вместо эталона:

```bash
cd $AIOS_ROOT
PYTHONPATH=src python3 -m aios.dashboard.collect
curl -sD- localhost:8000/api/state -o /dev/null | grep -i x-data-source   # live
```

Порт занят? Поменяйте `PORT` в `.env` и повторите `docker compose up -d`.

---

## 6. Полное воспроизведение расчёта

Образ симулятора собирается один раз, минут пять-десять:

```bash
cd $AIOS_ROOT
docker build -t aios/opm:2026.04 -f scripts/Dockerfile scripts/
docker images | grep aios/opm
```

Расписание с нуля:

```bash
cd $AIOS_ROOT/scripts
./run_schedule.sh --inc ../submission/wells_schedule.inc
```

Скрипт сам сверит результат с `npv_claimed.txt` и напечатает
«ТЕСТ НА КРУГ ПРОЙДЕН». Полтора-два минуты.

Физика:

```bash
./validate.sh $DATA_ROOT/runs/sched --since 2014-01-01
```

Ожидается «РЕЗУЛЬТАТ ФИЗИЧЕСКИ КОРРЕКТЕН» дважды — по всему горизонту и
по управляющему окну.

Агент без сети:

```bash
cp -r $AIOS_ROOT/cache/llm $DATA_ROOT/llm_cache
python3 rolling_horizon.py run --llm replay --candidates 8 \
    --workdir $DATA_ROOT/check_replay
```

Ожидается 1 152,837 млн при нуле сетевых вызовов.

Оптимизация целиком, как получено сданное расписание (около двух
часов):

```bash
python3 rolling_horizon.py run --llm off --candidates 34 \
    --workdir $DATA_ROOT/check_full34
```

Каждому прогону — свой `--workdir`: два прогона в один каталог
затирают журнал друг друга.

---

## 7. Если что-то пошло не так

**`cannot move ... Permission denied` при распаковке данных.** Каталог
`~/aios-data` создан Docker под root. Остановите контейнер, удалите
каталог, распакуйте заново, поднимите контейнер:

```bash
docker compose -f $AIOS_ROOT/docker-compose.yml down
sudo rm -rf $DATA_ROOT && mv ~/data $DATA_ROOT
cd $AIOS_ROOT && docker compose up -d
```

**Архив не распаковывается, `unsupported format`.** Скачана
HTML-страница вместо файла. Смотрите раздел 3, вариант с `HREF`.

**`Could not import module "aios.dashboard.server"`.** Образ собран
из кеша со слоем-заглушкой. Пересоберите без кеша:
`docker compose build --no-cache && docker compose up -d`.

**Дашборд показывает `demo`, хотя данные распакованы.** Не собрана
сводка: `PYTHONPATH=src python3 -m aios.dashboard.collect`. Именно
`state.json` цикл сам не пишет.

**В сводке «связей 0».** `response_matrix.csv` сохранён с BOM, и
сборщик не узнаёт колонку `source`. На расчёт не влияет, пустует только
карта связей на дашборде. Правится сменой кодировки при чтении файла в
`src/aios/dashboard/collect.py` на `utf-8-sig`.

**`run_flow` или `run_py` не найдены.** Это функции из
`scripts/lib_common.sh`, их надо подтянуть в текущую оболочку:
`source ./lib_common.sh`. Порядок аргументов: `run_py <скрипт>
<каталог для /out> [аргументы]`.

**Расчёт даёт 922,7 вместо 1 018,986.** Включён `chargeInitialPump` на
управляющем окне. Планка должна считаться без него: флаг нужен только
на полной истории 2007–2015.

**Прогон падает по месту на диске.** Каталоги кандидатов занимают
десятки гигабайт. Чистка: `scripts/cleanup.sh`. Каталоги
`runs/history_full` и `runs/baseline` из чистки исключены намеренно —
это опорные прогоны.

---

## 8. Карта проекта

```
aios/
├── submission/          что сдаём: расписание, число, RUN.md
├── vendor/chdd/         эталонный расчёт ЧДД организаторов, не правится
├── scripts/             расчёт, оптимизация, проверки
├── configs/             пределы, пороги паспорта, триггеры
├── src/aios/dashboard/  веб-часть: API и сборщик
├── ui/web/              интерфейс без сборки; ui/demo/ — эталонные данные
├── cache/llm/           ответы модели для режима replay
├── docs/                архитектура, проверки, эта инструкция
└── model/               рабочая дека (из архива, в git не входит)

~/aios-data/             данные (из архива, в git не входит)
├── runs/baseline        планка 1 018,986
├── runs/history_full    история 2007–2015, сверена с эталоном
├── full34/              прогон, из которого сдача
├── abl_*/, kam_*/       абляция режимов и правила гидродинамика
├── probes/              матрица влияния
└── llm_cache/           кэш ответов модели
```
