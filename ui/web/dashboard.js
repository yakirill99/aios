// ============================================================
// ДАННЫЕ ПРОГОНА
// Приезжают с бэкенда из GET /api/journal. Структура повторяет
// journal.json, который пишет цикл оптимизации, — при подключении
// реальных прогонов фронт менять не нужно.
// ============================================================
let journalData = { config: {}, schedule: [], journal: [] };
let wellsData = {};
let agentsData = [];
let matrixData = [];

async function loadJournal() {
    const r = await fetch('/api/journal', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    journalData = { config: d.config || {}, schedule: d.schedule || [], journal: d.journal || [] };
    wellsData = d.wells || {};
    agentsData = d.agents || [];
    matrixData = d.matrix || [];
}

function renderAll() {
    updateMetrics();
    updateNPVChart();
    updateWellsTable();
    updateAgents();
    updateActionsList();
    updateJSONView();
    updateSystemStatus();
}

function showLoadError(err) {
    const status = document.getElementById('system-status');
    if (status) {
        status.textContent = 'Нет данных прогона';
        status.className = 'status-badge warning';
    }
    const tbody = document.getElementById('wells-tbody');
    if (tbody) {
        tbody.innerHTML = '<tr><td colspan="9" style="padding:24px;text-align:center;color:#8b949e;">' +
            'Журнал прогона недоступен. Запустите прогон или проверьте, что сервер отдаёт /api/journal.</td></tr>';
    }
    console.error('Загрузка журнала не удалась:', err);
}


        // =====================================================
        // РЕАЛЬНЫЕ ДАННЫЕ ИЗ ВАШЕЙ СИСТЕМЫ
        // =====================================================
        
        // 1. ДАННЫЕ ИЗ journal.json
                
        // 2. ДАННЫЕ О СКВАЖИНАХ (из wells_schedule.inc и summary)
                
        // 3. ДАННЫЕ ОБ АГЕНТАХ
                
        // 4. ДАННЫЕ ИЗ response_matrix.csv (если есть)
                
        // =====================================================
        // ЛОГИКА ДАШБОРДА
        // =====================================================
        
        let currentFilter = 'all';
        let npvChart = null;
        
        // Инициализация
        document.addEventListener('DOMContentLoaded', () => {
    loadJournal().then(renderAll).catch(showLoadError);
});
        
        // Обновление метрик
        function updateMetrics() {
            const lastStep = journalData.journal[journalData.journal.length - 1];
            const npv = lastStep ? lastStep.npv_window / 1000 : 0;
            const gain = lastStep ? lastStep.gain / 1000 : 0;
            const totalActions = journalData.schedule.length;
            const activeWells = Object.values(wellsData).filter(w => w.status === 'active').length;
            const totalWells = Object.keys(wellsData).length;
            const steps = journalData.journal.length;
            
            const metrics = [
                { label: 'Текущий NPV', value: `${npv.toFixed(2)} млрд ₽`, class: npv > 0 ? 'positive' : 'negative' },
                { label: 'Выигрыш к бездействию', value: `+${gain.toFixed(2)} млрд ₽`, class: gain > 0 ? 'positive' : 'negative' },
                { label: 'Выполнено действий', value: totalActions, class: '' },
                { label: 'Активных скважин', value: `${activeWells}/${totalWells}`, class: '' },
                { label: 'Итераций выполнено', value: steps, class: '' },
                { label: 'Скважин с проблемами', value: Object.values(wellsData).filter(w => w.status === 'warning').length, class: 'negative' }
            ];
            
            document.getElementById('metrics-grid').innerHTML = metrics.map(m => `
                <div class="metric-card">
                    <div class="metric-label">${m.label}</div>
                    <div class="metric-value ${m.class}">${m.value}</div>
                </div>
            `).join('');
        }
        
        // Обновление графика NPV
        function updateNPVChart() {
            const ctx = document.getElementById('npvChart').getContext('2d');
            
            if (npvChart) {
                npvChart.destroy();
            }
            
            const labels = journalData.journal.map(step => `Шаг ${step.step + 1}`);
            const npvData = journalData.journal.map(step => step.npv_window / 1000);
            const npvNoopData = journalData.journal.map(step => step.npv_noop / 1000);
            
            npvChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: 'NPV с оптимизацией',
                            data: npvData,
                            borderColor: '#58a6ff',
                            backgroundColor: 'rgba(88,166,255,0.1)',
                            tension: 0.4,
                            fill: true,
                            pointBackgroundColor: '#58a6ff',
                            pointRadius: 6,
                            pointHoverRadius: 8
                        },
                        {
                            label: 'NPV бездействия',
                            data: npvNoopData,
                            borderColor: '#8b949e',
                            backgroundColor: 'rgba(139,148,158,0.1)',
                            tension: 0.4,
                            borderDash: [5, 5],
                            pointRadius: 4,
                            pointHoverRadius: 6
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {
                        mode: 'index',
                        intersect: false,
                    },
                    plugins: {
                        legend: {
                            display: true,
                            position: 'top',
                            labels: {
                                color: '#c9d1d9',
                                font: {
                                    weight: 'bold',
                                    size: 12
                                }
                            }
                        },
                        title: {
                            display: false
                        },
                        tooltip: {
                            backgroundColor: '#21262d',
                            titleColor: '#c9d1d9',
                            bodyColor: '#c9d1d9',
                            borderColor: '#30363d',
                            borderWidth: 1,
                            padding: 12,
                            callbacks: {
                                label: function(context) {
                                    return `${context.dataset.label}: ${context.parsed.y.toFixed(2)} млрд ₽`;
                                }
                            }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: false,
                            title: {
                                display: true,
                                text: 'NPV (млрд ₽)',
                                color: '#c9d1d9',
                                font: {
                                    weight: 'bold'
                                }
                            },
                            ticks: {
                                color: '#c9d1d9',
                                callback: function(value) {
                                    return value.toFixed(2) + ' млрд ₽';
                                }
                            },
                            grid: {
                                color: '#30363d',
                                drawBorder: false
                            }
                        },
                        x: {
                            title: {
                                display: true,
                                text: 'Итерации',
                                color: '#c9d1d9',
                                font: {
                                    weight: 'bold'
                                }
                            },
                            ticks: {
                                color: '#c9d1d9'
                            },
                            grid: {
                                color: '#30363d',
                                drawBorder: false
                            }
                        }
                    }
                }
            });
        }
        
        // Фильтрация скважин
        function filterWells(filter) {
            currentFilter = filter;
            
            // Обновляем активные кнопки
            document.querySelectorAll('.filter-btn').forEach((btn, index) => {
                const filters = ['all', 'prod', 'inj', 'warning'];
                btn.classList.toggle('active', filters[index] === filter);
            });
            
            updateWellsTable();
        }
        
        // Обновление таблицы скважин
        function updateWellsTable() {
            const tbody = document.getElementById('wells-tbody');
            
            // Фильтрация
            let wells = Object.entries(wellsData);
            
            if (currentFilter === 'prod') {
                wells = wells.filter(([name, well]) => well.type === 'prod');
            } else if (currentFilter === 'inj') {
                wells = wells.filter(([name, well]) => well.type === 'inj');
            } else if (currentFilter === 'warning') {
                wells = wells.filter(([name, well]) => well.status === 'warning' || well.status === 'danger');
            }
            
            // Генерация HTML
            tbody.innerHTML = wells.map(([name, well]) => {
                const statusClass = well.status === 'active' ? 'active' : 
                                    well.status === 'warning' ? 'warning' :
                                    well.status === 'danger' ? 'danger' : 'inactive';
                const statusText = well.status === 'active' ? '✅ Работает' : 
                                   well.status === 'warning' ? '⚠️ Проблемы' :
                                   well.status === 'danger' ? '🔴 Критично' : '❌ Закрыта';
                
                return `
                    <tr>
                        <td><strong style="color: #58a6ff;">${name}</strong></td>
                        <td>${well.type === 'prod' ? 'Добывающая' : 'Нагнетательная'}</td>
                        <td>${well.oilRate.toFixed(1)}</td>
                        <td>${well.liquidRate.toFixed(1)}</td>
                        <td>${well.watercut.toFixed(1)}%</td>
                        <td><span class="well-status ${statusClass}">${statusText}</span></td>
                        <td style="font-size: 0.85rem; color: #8b949e;">${well.lastAction}</td>
                    </tr>
                `;
            }).join('');
        }
        
        // Обновление агентов
        function updateAgents() {
            const agentsGrid = document.getElementById('agents-grid');
            
            agentsGrid.innerHTML = agentsData.map(agent => `
                <div class="agent-card">
                    <div class="agent-header">
                        <span class="agent-name">🤖 ${agent.name}</span>
                        <span class="agent-status ${agent.status}"></span>
                    </div>
                    <div style="color: #8b949e; font-size: 0.9rem; margin-bottom: 10px;">
                        ${agent.description}
                    </div>
                    <div style="font-size: 0.85rem; color: #8b949e;">
                        <strong>Последнее действие:</strong> ${agent.lastAction}
                    </div>
                </div>
            `).join('');
        }
        
        // Обновление списка действий
        function updateActionsList() {
            const actionsList = document.getElementById('actions-list');
            
            // Получаем последние действия из journal
            const recentActions = [];
            journalData.journal.forEach(step => {
                (step.chosen_actions || []).forEach(action => {
                    recentActions.push({
                        ...action,
                        step: step.step,
                        date: step.date,
                        npv: step.npv_window,
                        gain: step.gain
                    });
                });
            });
            
            // Сортируем по дате (сначала последние)
            recentActions.reverse();
            
            // Ограничиваем до 10
            const actionsToShow = recentActions.slice(0, 10);
            
            actionsList.innerHTML = actionsToShow.map(action => {
                const typeClass = action.control === 'LRAT' ? 'prod' : 
                                  action.control === 'RATE' ? 'inj' : 'shut';
                const typeText = action.control === 'LRAT' ? 'Добыча' : 
                                 action.control === 'RATE' ? 'Закачка' : 
                                 action.control === 'SHUT' ? 'Закрытие' : 
                                 action.control === 'OPEN' ? 'Открытие' : action.control;
                
                return `
                    <div class="action-item">
                        <div>
                            <strong style="color: #58a6ff;">Скв. ${action.well}</strong>
                            <span style="color: #8b949e; margin-left: 10px; font-size: 0.85rem;">${action.date}</span>
                            <span style="color: #8b949e; margin-left: 10px; font-size: 0.85rem;">Шаг ${action.step + 1}</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <span class="action-type ${typeClass}">${typeText}</span>
                            <span style="color: #8b949e; font-size: 0.85rem;">${action.value} м³/сут</span>
                        </div>
                    </div>
                `;
            }).join('');
            
            if (actionsToShow.length === 0) {
                actionsList.innerHTML = '<div style="padding: 20px; text-align: center; color: #8b949e;">Нет выполненных действий</div>';
            }
        }
        
        // Просмотр JSON данных
        function updateJSONView() {
            const jsonView = document.getElementById('json-view');
            
            const formattedData = {
                "config": journalData.config,
                "schedule": journalData.schedule,
                "journal": journalData.journal
            };
            
            jsonView.innerHTML = `<pre>${JSON.stringify(formattedData, null, 2)}</pre>`;
        }
        
        // Обновление статуса системы
        function updateSystemStatus() {
            const statusBadge = document.getElementById('system-status');
            const iterationsBadge = document.getElementById('iterations-badge');
            
            statusBadge.textContent = '✅ Система работает';
            statusBadge.className = 'status-badge success';
            
            iterationsBadge.textContent = `Итераций: ${journalData.journal.length}`;
        }
        
        // Добавляем стили для pre в json-view
        const _jv = document.querySelector('.json-view');
        if (_jv) { _jv.style.whiteSpace = 'pre-wrap'; _jv.style.wordBreak = 'break-all'; }
    