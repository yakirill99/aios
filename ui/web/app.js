
// ============================================================
// ДАННЫЕ
// ============================================================
// ============================================================
// СОСТОЯНИЕ
// Наполняется с бэкенда из GET /api/state. Форма JSON совпадает
// один-в-один с полями ниже — менять фронт при смене источника
// данных не нужно, достаточно чтобы сервер отдавал те же ключи.
// ============================================================
const state = {
    currentFilter: "all",
    agentStatuses: ["Завершён", "Активен", "Ожидание", "Ошибка"],
    agentIndex: {"analyst": 0, "strategist": 1, "critic": 2}
};

// Поля, которые приходят с сервера (значения по умолчанию до первой загрузки)
Object.assign(state, {
    npv: 0, baselineNpv: 0, improvement: 0, iteration: 0,
    wellsActive: 0, wellsTotal: 0, progress: 0, candidates: 0,
    llmMode: '—', autoUpdate: true, logAuto: true, logCount: 0,
    agents: { analyst: '—', strategist: '—', critic: '—' },
    wells: [], logs: [],
});

// ============================================================
// ЗАГРУЗКА С БЭКЕНДА
// ============================================================
const POLL_MS = 2000;
let pollTimer = null;
let liveMode = false;      // true — данные едут с сервера, ручные кнопки не мешают
let lastPollOk = null;

async function pullState() {
    try {
        const r = await fetch('/api/state', { cache: 'no-store' });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const fresh = await r.json();

        // Сохраняем клиентские поля, перетираем только серверные
        Object.assign(state, fresh);

        if (lastPollOk === false) showToast('Связь с сервером восстановлена', 'success');
        lastPollOk = true;
        setConnBadge(true);
        updateDashboard();
        renderLogs();
    } catch (e) {
        if (lastPollOk !== false) showToast('Нет связи с сервером — показаны последние данные', 'warning');
        lastPollOk = false;
        setConnBadge(false);
    }
}

function setConnBadge(ok) {
    const el = document.getElementById('connBadge');
    if (!el) return;
    el.textContent = ok ? 'Данные с сервера' : 'Сервер недоступен';
    el.className = 'conn-badge ' + (ok ? 'ok' : 'fail');
}

function startPolling() {
    if (pollTimer) return;
    liveMode = true;
    pullState();
    pollTimer = setInterval(pullState, POLL_MS);
}

function stopPolling() {
    clearInterval(pollTimer);
    pollTimer = null;
    liveMode = false;
    setConnBadge(false);
}

function toggleLive() {
    if (liveMode) {
        stopPolling();
        showToast('Автообновление с сервера выключено', 'info');
    } else {
        startPolling();
        showToast('Автообновление с сервера включено', 'success');
    }
    const btn = document.getElementById('liveToggle');
    if (btn) btn.textContent = liveMode ? '⏸ Остановить поток' : '▶ Данные с сервера';
}

async function startRun() {
    try {
        const r = await fetch('/api/run/start', { method: 'POST' });
        const data = await r.json();
        showToast(data.message || 'Прогон запущен', r.ok ? 'success' : 'warning');
        if (r.ok) startPolling();
    } catch (e) {
        showToast('Не удалось запустить прогон', 'warning');
    }
}


// ============================================================
// ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
// ============================================================
function formatNum(n) {
    return n.toLocaleString('ru-RU', { minFractionDigits: 1, maxFractionDigits: 1 });
}

function randomBetween(min, max) {
    return Math.round((Math.random() * (max - min) + min) * 10) / 10;
}

function getTime() {
    return new Date().toLocaleTimeString();
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ============================================================
// ОБНОВЛЕНИЕ ДАШБОРДА
// ============================================================
function updateDashboard() {
    // Время
    document.getElementById('updateTime').textContent = new Date().toLocaleString();
    
    // NPV
    document.getElementById('npvValue').textContent = formatNum(state.npv);
    document.getElementById('improvementValue').textContent = `+${formatNum(state.improvement)} млн`;
    const pct = (state.improvement / state.baselineNpv) * 100;
    document.getElementById('percentValue').textContent = `(${pct > 0 ? '+' : ''}${pct.toFixed(1)}%)`;
    document.getElementById('baselineDisplay').textContent = formatNum(state.baselineNpv);
    
    // Итерация
    document.getElementById('iterationCount').textContent = state.iteration;
    
    // Скважины
    document.getElementById('wellsActive').textContent = state.wellsActive;
    const producers = state.wells.filter(w => w.type === 'producer').length;
    const injectors = state.wells.filter(w => w.type === 'injector').length;
    document.getElementById('producerCount').textContent = producers;
    document.getElementById('injectorCount').textContent = injectors;
    
    // Кандидаты
    document.getElementById('candidateCount').textContent = state.candidates;
    
    // LLM
    document.getElementById('llmMode').textContent = state.llmMode;
    document.getElementById('llmMode').style.color = state.llmMode === 'Live' ? '#22c55e' : '#64748b';
    
    // Агенты
    document.getElementById('analystStatus').textContent = state.agents.analyst;
    document.getElementById('strategistStatus').textContent = state.agents.strategist;
    document.getElementById('criticStatus').textContent = state.agents.critic;
    
    // Обновляем классы статусов агентов
    updateAgentBadge('analystStatus', state.agents.analyst);
    updateAgentBadge('strategistStatus', state.agents.strategist);
    updateAgentBadge('criticStatus', state.agents.critic);
    
    // График
    const maxVal = Math.max(state.npv, state.baselineNpv) * 1.15;
    const maxH = 140;
    const baselineH = Math.max((state.baselineNpv / maxVal) * maxH, 20);
    const solutionH = Math.max((state.npv / maxVal) * maxH, 20);
    
    document.getElementById('baselineBar').style.height = baselineH + 'px';
    document.getElementById('solutionBar').style.height = solutionH + 'px';
    document.getElementById('baselineValueOn').textContent = Math.round(state.baselineNpv);
    document.getElementById('solutionValueOn').textContent = Math.round(state.npv);
    document.getElementById('baselineValue').textContent = formatNum(state.baselineNpv);
    document.getElementById('solutionValue').textContent = formatNum(state.npv);
    document.getElementById('chartImprovement').textContent = `+${formatNum(state.improvement)} млн руб.`;
    
    // Прогресс
    document.getElementById('progressText').textContent = state.progress + '%';
    document.getElementById('progressFill').style.width = state.progress + '%';
    
    // Таблица
    renderWells(state.currentFilter);
    
    // Лог
    renderLogs();
    document.getElementById('logCount').textContent = state.logs.length + ' событий';
    
    // Статистика логов
    const infoCount = state.logs.filter(l => l.type === 'info').length;
    const successCount = state.logs.filter(l => l.type === 'success').length;
    const warningCount = state.logs.filter(l => l.type === 'warning').length;
    document.getElementById('logTypeStats').textContent = 
        `info: ${infoCount} | success: ${successCount} | warning: ${warningCount}`;
}

function updateAgentBadge(elementId, status) {
    const el = document.getElementById(elementId);
    el.className = 'status-badge';
    if (status === 'Завершён') el.classList.add('success');
    else if (status === 'Активен') el.classList.add('active');
    else if (status === 'Ожидание') el.classList.add('warning');
    else if (status === 'Ошибка') el.classList.add('error');
    else el.classList.add('idle');
}

// ============================================================
// ТАБЛИЦА СКВАЖИН
// ============================================================
function renderWells(filter) {
    const tbody = document.getElementById('wellsTable');
    tbody.innerHTML = '';
    const filtered = filter === 'all' ? state.wells : state.wells.filter(w => w.type === filter);
    document.getElementById('wellCount').textContent = filtered.length + ' показано';
    
    filtered.forEach(w => {
        const tr = document.createElement('tr');
        const typeLabel = w.type === 'producer' ? 'Доб.' : 'Нагн.';
        const typeClass = w.type === 'producer' ? 'producer' : 'injector';
        const statusClass = w.status === 'Активна' ? 'active' : 'inactive';
        const action = w.status === 'Активна' ? '🟢 Работает' : '⏸️ Закрыта';
        
        tr.innerHTML = `
            <td><strong>${w.id}</strong></td>
            <td><span class="type-badge ${typeClass}">${typeLabel}</span></td>
            <td><span class="status-small ${statusClass}">${w.status}</span></td>
            <td>${w.rate > 0 ? w.rate + ' м³/сут' : '-'}</td>
            <td>${w.wc !== null ? w.wc + '%' : '-'}</td>
            <td style="text-align: left; cursor: pointer;" onclick="toggleSingleWell('${w.id}')">${action}</td>
        `;
        tbody.appendChild(tr);
    });
}

function filterWells(filter) {
    state.currentFilter = filter;
    renderWells(filter);
    showToast(`Показаны: ${filter === 'all' ? 'все скважины' : filter === 'producer' ? 'добывающие' : 'нагнетательные'}`, 'info');
}

function toggleSingleWell(id) {
    const well = state.wells.find(w => w.id === id);
    if (!well) return;
    well.status = well.status === 'Активна' ? 'Закрыта' : 'Активна';
    const producers = state.wells.filter(w => w.type === 'producer' && w.status === 'Активна').length;
    state.wellsActive = producers + state.wells.filter(w => w.type === 'injector' && w.status === 'Активна').length;
    renderWells(state.currentFilter);
    updateDashboard();
    showToast(`Скважина ${id}: ${well.status}`, well.status === 'Активна' ? 'success' : 'warning');
    addLog(`🔄 Скважина ${id} → ${well.status}`, 'info');
}

function toggleAllWells() {
    const allActive = state.wells.every(w => w.status === 'Активна');
    state.wells.forEach(w => {
        w.status = allActive ? 'Закрыта' : 'Активна';
    });
    state.wellsActive = state.wells.filter(w => w.status === 'Активна').length;
    renderWells(state.currentFilter);
    updateDashboard();
    const msg = allActive ? 'Все скважины закрыты' : 'Все скважины открыты';
    showToast(msg, 'info');
    addLog(`🔀 ${msg}`, 'warning');
}

// ============================================================
// ЛОГ
// ============================================================
function renderLogs() {
    const container = document.getElementById('logContainer');
    container.innerHTML = '';
    state.logs.forEach(log => {
        const div = document.createElement('div');
        div.className = `log-entry ${log.type}`;
        div.innerHTML = `<span class="time">[${log.time}]</span><span class="msg">${log.msg}</span>`;
        container.appendChild(div);
    });
    container.scrollTop = container.scrollHeight;
}

function addLog(msg, type = 'info') {
    const time = getTime();
    state.logs.push({ time, msg, type });
    if (state.logs.length > 200) state.logs.shift();
    renderLogs();
    document.getElementById('logCount').textContent = state.logs.length + ' событий';
}

function addRandomLog() {
    const types = ['info', 'success', 'warning', 'error'];
    const messages = [
        '📊 Анализ состояния пласта',
        '🔄 Обновление прогнозной модели',
        '💰 Расчёт NPV завершён',
        '🧠 Агент-аналитик: оценка выполнена',
        '🎯 Стратег: новое решение предложено',
        '🔍 Критик: ограничения проверены',
        '📈 Оптимизация продолжается',
        '✅ wells_schedule.inc обновлён',
        '⚠️ Внимание: приближение к лимиту',
        '🚀 Запущена новая итерация',
    ];
    const type = types[Math.floor(Math.random() * types.length)];
    const msg = messages[Math.floor(Math.random() * messages.length)];
    addLog(msg, type);
    showToast(`Лог: ${msg}`, type);
}

function clearLogs() {
    state.logs = [];
    renderLogs();
    document.getElementById('logCount').textContent = '0 событий';
    showToast('Лог очищен', 'info');
}

function toggleLogAuto() {
    state.logAuto = !state.logAuto;
    document.getElementById('logAutoToggle').textContent = state.logAuto ? '⏸️ Авто' : '▶️ Авто';
    showToast(`Авто-лог: ${state.logAuto ? 'включён' : 'выключен'}`, 'info');
}

// ============================================================
// NPV
// ============================================================
function addNPV(delta) {
    state.npv += delta;
    state.improvement = state.npv - state.baselineNpv;
    updateDashboard();
    showToast(`NPV ${delta >= 0 ? 'увеличен' : 'уменьшен'} на ${Math.abs(delta)} млн`, delta >= 0 ? 'success' : 'error');
    addLog(`💰 NPV → ${formatNum(state.npv)} млн (${delta >= 0 ? '+' : ''}${delta} млн)`, 'info');
}

function randomNPV() {
    const newNPV = randomBetween(1100, 1250);
    state.npv = newNPV;
    state.improvement = state.npv - state.baselineNpv;
    updateDashboard();
    showToast(`NPV случайно: ${formatNum(state.npv)} млн`, 'success');
    addLog(`🎲 NPV → ${formatNum(state.npv)} млн (случайно)`, 'success');
}

// ============================================================
// АГЕНТЫ
// ============================================================
function cycleAgentStatus(agent) {
    const statuses = ['Завершён', 'Активен', 'Ожидание', 'Ошибка'];
    const current = state.agents[agent];
    let idx = statuses.indexOf(current);
    idx = (idx + 1) % statuses.length;
    state.agents[agent] = statuses[idx];
    updateDashboard();
    showToast(`Агент ${agent}: ${statuses[idx]}`, 'info');
    addLog(`🤖 Агент ${agent} → ${statuses[idx]}`, 'info');
}

// ============================================================
// ПРОГРЕСС
// ============================================================
function increaseProgress() {
    state.progress = Math.min(state.progress + 5, 100);
    updateDashboard();
    if (state.progress === 100) {
        showToast('🎉 Оптимизация завершена!', 'success');
        addLog('🎉 Оптимизация завершена на 100%', 'success');
    }
}

function randomProgress() {
    state.progress = randomBetween(10, 100);
    updateDashboard();
    showToast(`Прогресс: ${state.progress}%`, 'info');
}

function resetProgress() {
    state.progress = 0;
    updateDashboard();
    showToast('Прогресс сброшен', 'warning');
}

// ============================================================
// КАНДИДАТЫ
// ============================================================
function changeCandidates() {
    const newVal = prompt('Введите количество кандидатов:', state.candidates);
    if (newVal !== null && !isNaN(newVal) && parseInt(newVal) > 0) {
        state.candidates = parseInt(newVal);
        updateDashboard();
        showToast(`Кандидатов: ${state.candidates}`, 'info');
        addLog(`📊 Кандидатов изменено на ${state.candidates}`, 'info');
    }
}

// ============================================================
// LLM
// ============================================================
function toggleLLM() {
    state.llmMode = state.llmMode === 'Live' ? 'Off' : 'Live';
    updateDashboard();
    showToast(`LLM режим: ${state.llmMode}`, 'info');
    addLog(`🧠 LLM → ${state.llmMode}`, 'info');
}

// ============================================================
// SCHEDULE
// ============================================================
function refreshSchedule() {
    const el = document.getElementById('scheduleContent');
    const wells = state.wells;
    let html = `<span class="comment">-- wells_schedule.inc (обновлён ${getTime()})</span>\n`;
    html += `<span class="comment">-- Управляющий горизонт: 01.01.2014 - 01.12.2015</span>\n`;
    html += `<span class="comment">-- Сгенерировано: AIOS Multi-Agent System</span>\n\n`;
    html += `<span class="keyword">WELSPECS</span>\n`;
    wells.filter(w => w.type === 'producer').forEach((w, i) => {
        const status = w.status === 'Активна' ? 'OPEN' : 'SHUT';
        html += ` <span class="string">'${w.id}'</span> <span class="string">'PROD'</span> <span class="number">${10 + i}</span> <span class="number">${12 + i}</span> <span class="number">1</span> <span class="string">'OIL'</span> <span class="comment">-- ${status}</span>\n`;
    });
    html += `<span class="comment">... (${wells.filter(w => w.type === 'producer').length} добывающие)</span>\n\n`;
    html += `<span class="keyword">WELSPECS</span>\n`;
    const injs = wells.filter(w => w.type === 'injector');
    injs.forEach((w, i) => {
        const status = w.status === 'Активна' ? 'OPEN' : 'SHUT';
        html += ` <span class="string">'${w.id}'</span> <span class="string">'INJ'</span> <span class="number">${25 + i}</span> <span class="number">${30 + i}</span> <span class="number">1</span> <span class="string">'WATER'</span> <span class="comment">-- ${status}</span>\n`;
    });
    html += `<span class="comment">... (${injs.length} нагнетательные)</span>`;
    el.innerHTML = html;
    showToast('📄 wells_schedule.inc обновлён', 'success');
    addLog('📄 wells_schedule.inc обновлён', 'success');
}

function downloadSchedule() {
    const content = document.getElementById('scheduleContent').textContent;
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'wells_schedule.inc';
    a.click();
    URL.revokeObjectURL(url);
    showToast('⬇️ wells_schedule.inc скачан', 'success');
    addLog('⬇️ wells_schedule.inc скачан', 'success');
}

// ============================================================
// ГИДРОДИНАМИЧЕСКАЯ МОДЕЛЬ
// ============================================================
function showModelInfo() {
    showToast('📊 Model_Y: 324,723 ячейки, 49 скважин, 3 разлома', 'info');
    addLog('ℹ️ Информация о модели: 49×47×141, 324,723 ячейки', 'info');
}

// ============================================================
// СИМУЛЯЦИЯ ОПТИМИЗАЦИИ
// ============================================================
let simInterval = null;

function simulateOptimization() {
    if (simInterval) {
        clearInterval(simInterval);
        simInterval = null;
        document.querySelector('.btn-green')?.style?.setProperty('background', '#22c55e');
        showToast('⏹️ Оптимизация остановлена', 'warning');
        addLog('⏹️ Оптимизация остановлена пользователем', 'warning');
        return;
    }
    
    showToast('▶️ Запуск оптимизации...', 'success');
    addLog('🚀 Запуск цикла оптимизации', 'success');
    document.querySelector('.btn-green')?.style?.setProperty('background', '#ef4444');
    document.querySelector('.btn-green')?.style?.setProperty('color', 'white');
    
    let steps = 0;
    simInterval = setInterval(() => {
        steps++;
        state.iteration++;
        
        // Случайное изменение NPV
        const delta = randomBetween(-2, 8);
        state.npv += delta;
        state.improvement = state.npv - state.baselineNpv;
        
        // Прогресс
        state.progress = Math.min(state.progress + randomBetween(1, 5), 100);
        
        // Случайное переключение скважины
        if (Math.random() > 0.7) {
            const well = state.wells[Math.floor(Math.random() * state.wells.length)];
            well.status = well.status === 'Активна' ? 'Закрыта' : 'Активна';
            state.wellsActive = state.wells.filter(w => w.status === 'Активна').length;
        }
        
        // Случайное изменение статуса агента
        if (Math.random() > 0.8) {
            const agents = ['analyst', 'strategist', 'critic'];
            const agent = agents[Math.floor(Math.random() * agents.length)];
            const statuses = ['Завершён', 'Активен', 'Ожидание'];
            state.agents[agent] = statuses[Math.floor(Math.random() * statuses.length)];
        }
        
        // Лог
        const msgs = [
            `🔄 Итерация ${state.iteration}: NPV ${formatNum(state.npv)} млн`,
            `📊 Оптимизация: прогресс ${state.progress}%`,
            `🧠 Агенты: аналитик ${state.agents.analyst}, стратег ${state.agents.strategist}`,
            `💰 Текущий NPV: ${formatNum(state.npv)} млн`,
            `📈 Прирост: ${formatNum(state.improvement)} млн`,
        ];
        addLog(msgs[Math.floor(Math.random() * msgs.length)], 'info');
        
        updateDashboard();
        
        if (state.progress >= 100 || steps > 20) {
            clearInterval(simInterval);
            simInterval = null;
            document.querySelector('.btn-green')?.style?.setProperty('background', '#22c55e');
            showToast('✅ Оптимизация завершена!', 'success');
            addLog('✅ Оптимизация завершена! Результат достигнут', 'success');
        }
    }, 1500);
}

// ============================================================
// СБРОС
// ============================================================
function resetSimulation() {
    if (simInterval) {
        clearInterval(simInterval);
        simInterval = null;
        document.querySelector('.btn-green')?.style?.setProperty('background', '#22c55e');
    }
    
    state.npv = 1148.5;
    state.baselineNpv = 1023.355;
    state.improvement = 125.2;
    state.iteration = 7;
    state.wellsActive = 33;
    state.progress = 65;
    state.candidates = 25;
    state.llmMode = 'Live';
    state.agents = {
        analyst: 'Завершён',
        strategist: 'Активен',
        critic: 'Ожидание'
    };
    state.wells.forEach((w, i) => {
        w.status = i < 8 ? 'Активна' : (i < 12 ? 'Активна' : 'Закрыта');
        if (i === 3 || i === 8) w.status = 'Закрыта';
    });
    state.wellsActive = state.wells.filter(w => w.status === 'Активна').length;
    state.logs = [
        { time: getTime(), msg: '🔄 Система сброшена к исходному состоянию', type: 'info' },
        { time: getTime(), msg: '📊 Данные восстановлены', type: 'success' },
    ];
    updateDashboard();
    showToast('🔄 Система сброшена', 'info');
}

// ============================================================
// АВТО-ОБНОВЛЕНИЕ
// ============================================================
let autoUpdateInterval = null;

function toggleAutoUpdate() {
    state.autoUpdate = !state.autoUpdate;
    document.getElementById('autoToggle').textContent = state.autoUpdate ? '⏸️ Авто-обновление' : '▶️ Авто-обновление';
    if (state.autoUpdate) {
        autoUpdateInterval = setInterval(() => {
            document.getElementById('updateTime').textContent = new Date().toLocaleString();
        }, 5000);
        showToast('Авто-обновление включено', 'success');
    } else {
        clearInterval(autoUpdateInterval);
        showToast('Авто-обновление выключено', 'warning');
    }
}

// ============================================================
// АВТО-ЛОГ В ФОНЕ
// ============================================================
let bgLogInterval = null;

function startBackgroundLog() {
    bgLogInterval = setInterval(() => {
        if (state.logAuto && state.logs.length < 100) {
            const msgs = [
                '🔄 Фоновая оптимизация...',
                '📊 Обновление данных модели',
                '🧠 Анализ состояния агентов',
                '💰 Пересчёт NPV',
                '📈 Мониторинг скважин',
            ];
            const types = ['info', 'info', 'info', 'success', 'warning'];
            const idx = Math.floor(Math.random() * msgs.length);
            addLog(msgs[idx], types[idx]);
        }
    }, 5000);
}

// ============================================================
// ЗАПУСК
// ============================================================
document.addEventListener('DOMContentLoaded', function() {
    updateDashboard();
    startBackgroundLog();
    autoUpdateInterval = setInterval(() => {
        if (state.autoUpdate) {
            document.getElementById('updateTime').textContent = new Date().toLocaleString();
        }
    }, 5000);
    
    // Первое обновление schedule
    refreshSchedule();
    
    console.log('🎮 AIOS Интерактивный дашборд загружен!');
    console.log('Доступные команды:');
    console.log('  updateNPV(число)     - обновить NPV');
    console.log('  state               - посмотреть состояние');
    console.log('  addLog("текст")      - добавить лог');
    console.log('  simulateOptimization() - запустить симуляцию');
    console.log('  resetSimulation()    - сбросить всё');
});

// Вывод в глобальную область для консоли
window.updateNPV = function(val) {
    state.npv = val;
    state.improvement = state.npv - state.baselineNpv;
    updateDashboard();
    showToast(`NPV → ${formatNum(state.npv)} млн`, 'success');
    addLog(`💰 NPV обновлён → ${formatNum(state.npv)} млн`, 'success');
};

window.state = state;
window.addLog = addLog;
window.simulateOptimization = simulateOptimization;
window.resetSimulation = resetSimulation;


// ============================================================
// РЕЖИМ ОТОБРАЖЕНИЯ: простой / продвинутый
// Чисто фронтовая история — бэкенд про неё не знает и всегда
// отдаёт полный набор данных.
// ============================================================
function setViewMode(mode) {
    document.body.classList.toggle('simple', mode === 'simple');
    document.querySelectorAll('[data-mode]').forEach(b => {
        b.classList.toggle('active', b.dataset.mode === mode);
    });
    try { localStorage.setItem('aios.viewMode', mode); } catch (e) { /* приватный режим */ }
}

(function initViewMode() {
    let saved = 'simple';
    try { saved = localStorage.getItem('aios.viewMode') || 'simple'; } catch (e) { /* ignore */ }
    document.addEventListener('DOMContentLoaded', () => setViewMode(saved));
})();

// Стартуем поток с сервера сразу после загрузки страницы
document.addEventListener('DOMContentLoaded', () => {
    startPolling();
    const btn = document.getElementById('liveToggle');
    if (btn) btn.textContent = '⏸ Остановить поток';
});

window.setViewMode = setViewMode;
window.toggleLive = toggleLive;
window.startRun = startRun;
window.pullState = pullState;
