/* ============================================================
   Общая часть обеих страниц:
     - шапка с переходами между экранами (в обе стороны)
     - индикатор источника данных: прогон или эталон
     - меню запуска: дека, пресет, параметры, режим LLM
   ============================================================ */

(function () {
    'use strict';

    const SCREENS = [
        { href: '/', label: 'Сводка' },
        { href: '/dashboard.html', label: 'Журнал прогона' },
    ];

    const SOURCE_TEXT = {
        live: 'Данные прогона',
        demo: 'Эталонный набор',
        mixed: 'Частично эталон',
        unknown: 'Источник неизвестен',
    };

    const SOURCE_HINT = {
        live: 'Всё показанное получено из последнего прогона.',
        demo: 'Результатов прогона нет, показан эталонный набор из ui/demo.',
        mixed: 'Часть разделов из прогона, часть из эталонного набора. Наведите, чтобы увидеть разбивку.',
        unknown: 'Не удалось определить источник данных.',
    };

    let meta = { readonly: false };
    let statusTimer = null;
    let lastActiveId = null;

    // ========================================================
    // Шапка
    // ========================================================

    function currentPath() {
        const p = window.location.pathname;
        return p === '' || p === '/index.html' ? '/' : p;
    }

    function buildTopbar() {
        const bar = document.createElement('div');
        bar.className = 'aios-topbar';

        const nav = document.createElement('nav');
        nav.className = 'aios-nav';
        nav.setAttribute('aria-label', 'Экраны');
        const here = currentPath();
        SCREENS.forEach(s => {
            const a = document.createElement('a');
            a.href = s.href;
            a.textContent = s.label;
            if (s.href === here) {
                a.classList.add('active');
                a.setAttribute('aria-current', 'page');
            }
            nav.appendChild(a);
        });
        bar.appendChild(nav);

        bar.appendChild(el('span', { class: 'aios-spacer' }));

        // Бейдж связи: app.js на сводке обращается к нему по id
        if (currentPath() === '/') {
            // Бейдж связи ведёт app.js, а он есть только на сводке.
            // На журнале он навсегда застревал в «Подключение…».
            bar.appendChild(el('span', { id: 'connBadge', class: 'aios-badge unknown' }, 'Подключение…'));
        }
        bar.appendChild(el('span', { id: 'aiosSource', class: 'aios-badge unknown' }, '—'));
        bar.appendChild(el('span', { id: 'aiosProgress', class: 'aios-progress', hidden: true }));

        const runBtn = el('button', {
            id: 'aiosRunBtn', class: 'aios-btn', type: 'button', hidden: true,
        }, 'Запустить прогон');
        runBtn.addEventListener('click', openRunPanel);
        bar.appendChild(runBtn);

        document.body.insertBefore(bar, document.body.firstChild);
    }

    function el(tag, attrs, text) {
        const node = document.createElement(tag);
        Object.entries(attrs || {}).forEach(([k, v]) => {
            if (v === true) node.setAttribute(k, '');
            else if (v !== false && v != null) node.setAttribute(k, v);
        });
        if (text != null) node.textContent = text;
        return node;
    }

    // ========================================================
    // Источник данных
    // ========================================================

    async function refreshSource() {
        const badge = document.getElementById('aiosSource');
        if (!badge) return;
        try {
            const r = await fetch('/api/journal', { cache: 'no-store' });
            const source = r.headers.get('X-Data-Source') || 'unknown';
            badge.textContent = SOURCE_TEXT[source] || source;
            badge.className = 'aios-badge ' + (SOURCE_TEXT[source] ? source : 'unknown');

            let hint = SOURCE_HINT[source] || '';
            if (source === 'mixed') {
                const data = await r.json();
                const parts = Object.entries(data._source || {})
                    .map(([k, v]) => `${k}: ${v === 'live' ? 'прогон' : 'эталон'}`);
                if (parts.length) hint += '\n' + parts.join('\n');
            }
            badge.title = hint;

            // Данные прогона на экране -> кнопки-мокапы прячутся
            document.body.dataset.live = (source === 'live' || source === 'mixed') ? 'true' : 'false';
        } catch (e) {
            badge.textContent = SOURCE_TEXT.unknown;
            badge.className = 'aios-badge unknown';
        }
    }

    // ========================================================
    // Меню запуска
    // ========================================================

    function buildModal() {
        const back = el('div', { class: 'aios-modal-back', id: 'aiosRunBack', hidden: true });
        back.innerHTML = `
            <div class="aios-modal" role="dialog" aria-modal="true" aria-labelledby="aiosRunTitle">
                <h2 id="aiosRunTitle">Запуск прогона</h2>
                <div class="aios-sub">Параметры проверяются на сервере по схеме из configs/run_presets.yaml</div>
                <div class="aios-error" id="aiosRunError" hidden></div>
                <div id="aiosJobBox"></div>
                <div id="aiosRunForm"></div>
                <div class="aios-actions">
                    <button type="button" class="aios-btn secondary" id="aiosRunClose">Закрыть</button>
                    <button type="button" class="aios-btn" id="aiosRunSubmit">Поставить в очередь</button>
                </div>
            </div>`;
        document.body.appendChild(back);

        back.addEventListener('click', e => { if (e.target === back) closeRunPanel(); });
        document.getElementById('aiosRunClose').addEventListener('click', closeRunPanel);
        document.getElementById('aiosRunSubmit').addEventListener('click', submitRun);
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape' && !back.hidden) closeRunPanel();
        });
    }

    /** Разбирает ответ, не бросая на не-JSON теле. */
    async function readJson(response) {
        const text = await response.text();
        try {
            return JSON.parse(text);
        } catch (e) {
            return { raw: text.slice(0, 300) };
        }
    }

    function showError(message) {
        const box = document.getElementById('aiosRunError');
        box.textContent = message;
        box.hidden = !message;
    }

    async function openRunPanel() {
        document.getElementById('aiosRunBack').hidden = false;
        showError('');
        if (meta.queue_writable === false) {
            showError('Очередь недоступна на запись. ' + (meta.queue_note || '') +
                      ' Обычно помогает: mkdir -p ~/aios-data/queue и перезапуск контейнера.');
        }
        await renderForm();
        pollStatus();
        statusTimer = setInterval(pollStatus, 3000);
    }

    function closeRunPanel() {
        document.getElementById('aiosRunBack').hidden = true;
        clearInterval(statusTimer);
        statusTimer = null;
    }

    async function renderForm() {
        const box = document.getElementById('aiosRunForm');
        box.innerHTML = '<div class="aios-help">Загрузка…</div>';

        let decks, presets, params;
        try {
            const [d, p] = await Promise.all([
                fetch('/api/decks').then(r => r.json()),
                fetch('/api/presets').then(r => r.json()),
            ]);
            decks = d.decks || [];
            presets = p.presets || [];
            params = p.params || [];
        } catch (e) {
            box.innerHTML = '';
            showError('Не удалось загрузить реестры деки и пресетов');
            return;
        }

        box.innerHTML = '';

        // --- дека ---
        const deckField = field('Месторождение', 'aiosDeck');
        const deckSel = deckField.querySelector('select');
        if (!decks.length) {
            showError('Реестр деки пуст — заполните configs/decks.yaml');
        }
        decks.forEach(d => {
            const o = el('option', { value: d.id }, d.name + (d.ready ? '' : ' — не готова'));
            if (!d.ready) o.disabled = true;
            deckSel.appendChild(o);
        });
        const deckHelp = el('div', { class: 'aios-help' });
        deckField.appendChild(deckHelp);
        deckSel.addEventListener('change', () => {
            const d = decks.find(x => x.id === deckSel.value);
            deckHelp.textContent = d
                ? `${d.description || ''} Скважин: ${d.wells ?? '—'}. Планка ЧДД: ${d.baseline_npv ?? 'не посчитана'}.`
                : '';
        });
        box.appendChild(deckField);

        // --- пресет ---
        const presetField = field('Режим запуска', 'aiosPreset');
        const presetSel = presetField.querySelector('select');
        presets.forEach(p => presetSel.appendChild(el('option', { value: p.id }, p.name)));
        const presetHelp = el('div', { class: 'aios-help' });
        presetField.appendChild(presetHelp);
        box.appendChild(presetField);

        // --- параметры по схеме с сервера ---
        const paramBox = el('div', { id: 'aiosParams' });
        box.appendChild(paramBox);

        function renderParams() {
            const preset = presets.find(p => p.id === presetSel.value);
            presetHelp.textContent = preset ? (preset.description || '') : '';
            const values = (preset && preset.params) || {};

            paramBox.innerHTML = '';
            params.forEach(spec => {
                const holder = el('div', {
                    class: 'aios-field',
                    'data-param': spec.id,
                    'data-depends-on': spec.depends_on || '',
                    'data-depends-value': spec.depends_value || '',
                });
                holder.appendChild(el('label', { for: 'p_' + spec.id }, spec.label));

                let input;
                if (spec.type === 'enum') {
                    input = el('select', { id: 'p_' + spec.id });
                    (spec.options || []).forEach(o =>
                        input.appendChild(el('option', { value: o.value, title: o.help || '' }, o.label)));
                } else if (spec.type === 'date') {
                    // Раньше сюда попадал input type=number, и дата
                    // «2014-01-01» в него просто не влезала — поле
                    // оставалось пустым, а параметр молча не уезжал.
                    input = el('input', { type: 'date', id: 'p_' + spec.id });
                } else {
                    input = el('input', {
                        type: 'number', id: 'p_' + spec.id,
                        min: spec.min, max: spec.max,
                    });
                }
                if (values[spec.id] != null) input.value = values[spec.id];
                input.addEventListener('change', applyDependencies);
                holder.appendChild(input);
                if (spec.help) holder.appendChild(el('div', { class: 'aios-help' }, spec.help));
                paramBox.appendChild(holder);
            });
            applyDependencies();
            warnIfSlow(preset);
        }

        presetSel.addEventListener('change', renderParams);
        deckSel.dispatchEvent(new Event('change'));
        renderParams();
    }

    function field(label, id) {
        const holder = el('div', { class: 'aios-field' });
        holder.appendChild(el('label', { for: id }, label));
        holder.appendChild(el('select', { id: id }));
        return holder;
    }

    /** Поле «профиль модели» нужно только при живом вызове LLM. */
    function applyDependencies() {
        document.querySelectorAll('[data-depends-on]').forEach(holder => {
            const on = holder.dataset.dependsOn;
            if (!on) return;
            const master = document.getElementById('p_' + on);
            holder.hidden = !master || master.value !== holder.dataset.dependsValue;
        });
    }

    function warnIfSlow(preset) {
        const old = document.getElementById('aiosSlowWarn');
        if (old) old.remove();
        if (!preset || preset.instant) return;
        const warn = el('div', { class: 'aios-warn', id: 'aiosSlowWarn' },
            'Настоящий расчёт. Займёт минуты или десятки минут — не запускайте во время показа. ' +
            'Для демонстрации есть режим проигрывания записи.');
        document.getElementById('aiosRunForm').prepend(warn);
    }

    function collectParams() {
        const out = {};
        document.querySelectorAll('#aiosParams [data-param]').forEach(holder => {
            if (holder.hidden) return;
            const id = holder.dataset.param;
            const input = document.getElementById('p_' + id);
            if (!input || input.value === '') return;
            out[id] = input.type === 'number' ? Number(input.value) : input.value;
        });
        return out;
    }

    async function submitRun() {
        showError('');
        const btn = document.getElementById('aiosRunSubmit');
        btn.disabled = true;
        try {
            const r = await fetch('/api/run/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    deck: document.getElementById('aiosDeck').value,
                    preset: document.getElementById('aiosPreset').value,
                    params: collectParams(),
                }),
            });
            const data = await readJson(r);
            if (!r.ok) {
                // Раньше здесь терялся текст: если сервер отвечал 500 с
                // не-JSON телом, r.json() бросал, и пользователь видел
                // «не удалось связаться с сервером» вместо причины.
                showError(data.detail || `Сервер ответил ${r.status}: ${data.raw || 'без пояснения'}`);
                return;
            }
            pollStatus();
        } catch (e) {
            showError('Сеть недоступна: ' + e.message);
        } finally {
            btn.disabled = false;
        }
    }

    // ========================================================
    // Состояние задания
    // ========================================================

    /** Полоса прогона в шапке — видна на обеих страницах, окно открывать не нужно. */
    function renderTopProgress(job) {
        const box = document.getElementById('aiosProgress');
        if (!box) return;

        if (!job || !['queued', 'running'].includes(job.status)) {
            box.hidden = true;
            return;
        }
        const done = job.steps_done || 0;
        const total = job.steps_total || 0;
        const pct = total ? Math.round(done / total * 100) : 0;

        box.hidden = false;
        box.innerHTML = '';
        box.appendChild(el('span', { class: 'aios-spin' }));
        box.appendChild(el('span', { class: 'aios-progress-text' },
            job.status === 'queued'
                ? 'В очереди…'
                : total ? `Прогон: шаг ${done} из ${total}` : 'Прогон идёт…'));
        const track = el('span', { class: 'aios-progress-track' });
        const fill = el('span', { class: 'aios-progress-fill' });
        fill.style.width = pct + '%';
        track.appendChild(fill);
        box.appendChild(track);
        box.title = (job.log || []).slice(-1).map(l => l.message).join('') || '';
    }

    /** Фоновый опрос статуса — работает и с закрытым окном запуска. */
    async function watchRun() {
        try {
            const data = await fetch('/api/run/status', { cache: 'no-store' }).then(r => r.json());
            const job = data.active || null;
            renderTopProgress(job);

            // Прогон только что закончился — подтягиваем свежие данные
            if (lastActiveId && (!job || job.run_id !== lastActiveId)) {
                refreshSource();
                if (typeof window.pullState === 'function') window.pullState();
            }
            lastActiveId = job ? job.run_id : null;
        } catch (e) {
            renderTopProgress(null);
        }
    }

    const STATUS_TEXT = {
        queued: 'В очереди', running: 'Считается', done: 'Завершён',
        failed: 'Ошибка', cancelled: 'Отменён',
    };

    async function pollStatus() {
        const box = document.getElementById('aiosJobBox');
        if (!box) return;
        let data;
        try {
            data = await fetch('/api/run/status', { cache: 'no-store' }).then(r => r.json());
        } catch (e) {
            return;
        }

        const job = data.active || (data.recent || [])[0];
        if (!job) {
            box.innerHTML = '<div class="aios-help">Прогонов пока не было.</div>';
            return;
        }

        const isActive = job.status === 'queued' || job.status === 'running';
        box.innerHTML = '';
        const card = el('div', { class: 'aios-job' });

        const head = el('div', { class: 'aios-job-head' });
        head.appendChild(el('span', {
            class: 'aios-badge ' + (isActive ? 'mixed' : job.status === 'done' ? 'live' : 'fail'),
        }, STATUS_TEXT[job.status] || job.status));
        head.appendChild(el('code', {}, job.run_id));
        head.appendChild(el('span', { class: 'aios-spacer' }));
        if (isActive) {
            const cancel = el('button', { class: 'aios-btn danger', type: 'button' }, 'Отменить');
            cancel.addEventListener('click', () => cancelRun(job.run_id));
            head.appendChild(cancel);
        }
        card.appendChild(head);

        if (isActive && job.steps_total) {
            const track = el('div', { class: 'aios-progress-track wide' });
            const fill = el('span', { class: 'aios-progress-fill' });
            fill.style.width = Math.round((job.steps_done || 0) / job.steps_total * 100) + '%';
            track.appendChild(fill);
            card.appendChild(track);
            card.appendChild(el('div', { class: 'aios-help' },
                `Шаг ${job.steps_done || 0} из ${job.steps_total}`));
        }

        const bits = [];
        if (job.deck) bits.push('дека: ' + (job.deck.name || job.deck.id));
        if (job.preset) bits.push('режим: ' + (job.preset.name || job.preset.id));
        if (job.detail) bits.push(job.detail);
        card.appendChild(el('div', { class: 'aios-help' }, bits.join(' · ')));

        if ((job.log || []).length) {
            const logBox = el('div', { class: 'aios-job-log' });
            job.log.slice(-12).forEach(line =>
                logBox.appendChild(el('div', {}, line.message)));
            card.appendChild(logBox);
        }

        box.appendChild(card);
        document.getElementById('aiosRunSubmit').disabled = isActive;
    }

    async function cancelRun(runId) {
        try {
            await fetch(`/api/run/${encodeURIComponent(runId)}/cancel`, { method: 'POST' });
            pollStatus();
        } catch (e) {
            showError('Не удалось отменить задание');
        }
    }

    // ========================================================
    // Запуск
    // ========================================================

    async function init() {
        buildTopbar();
        buildModal();

        try {
            meta = await fetch('/api/meta', { cache: 'no-store' }).then(r => r.json());
        } catch (e) {
            meta = { readonly: true };
        }

        // В сборке только для просмотра кнопки запуска нет вовсе
        const btn = document.getElementById('aiosRunBtn');
        if (btn) btn.hidden = !!meta.readonly;

        refreshSource();
        setInterval(refreshSource, 5000);
        watchRun();
        setInterval(watchRun, 3000);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
