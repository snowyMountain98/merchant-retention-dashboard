const DATA_URL = 'public-data/dashboard_summary.json';
const fmt = new Intl.NumberFormat('ko-KR');
const money = value => `${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(value ?? 0)}원`;
const labels = { LONG_INACTIVE: '장기 미거래', RECENTLY_INACTIVE: '최근 미거래', DECLINING: '거래 감소 관찰', NO_ACTIVITY: '관측기간 내 거래 없음', RESTARTED: '거래 재개', FIRST_ACTIVITY: '최근 관측기간 첫 거래', SHORT_HISTORY: '거래 이력 짧음', TRADING: '거래 유지 중' };
let chart = null;
let paymentMethodChart = null;
let financialCompanyChart = null;
let financialTrendChart = null;
let data = null;
let enterprise = null;

async function init() {
  const enterpriseId = new URLSearchParams(window.location.search).get('enterprise_id');
  if (!enterpriseId) throw new Error('기업관리번호가 없습니다. 기업 목록에서 기업을 선택해 주세요.');
  const response = await fetch(DATA_URL, { cache: 'no-store' });
  if (!response.ok) throw new Error('기업 분석 결과를 불러오지 못했습니다. GitHub Actions 실행 결과를 확인해 주세요.');
  data = await response.json();
  enterprise = (data.enterprises || []).find(e => String(e.enterprise_id) === enterpriseId);
  if (!enterprise) throw new Error('기업 분석 결과를 찾지 못했습니다. 최신 분석을 실행했는지 확인해 주세요.');

  document.title = `${enterprise.enterprise_name || '기업'} - 기업 AI 분석`;
  document.getElementById('enterprise-name').textContent = enterprise.enterprise_name || '기업명 없음';
  document.getElementById('enterprise-id').textContent = enterprise.enterprise_id;
  document.getElementById('analysis-month').textContent = data.analysis_month || '—';
  const status = document.getElementById('enterprise-status');
  status.className = `level-chip ${enterprise.transaction_status || 'SHORT_HISTORY'}`;
  status.textContent = enterprise.transaction_status_label || labels[enterprise.transaction_status] || '상태 확인 필요';
  document.getElementById('approval-amount').textContent = money(enterprise.current_approval_amount);
  document.getElementById('sales-change').innerHTML = formatRate(enterprise.sales_change_rate);
  document.getElementById('count-change').innerHTML = formatRate(enterprise.transaction_change_rate);
  const baselineDrop = enterprise.sales_below_3m_pct;
  document.getElementById('sales-baseline-change').textContent =
    Number(enterprise.baseline_month_count || 0) < 1 ? '비교 이력 부족' :
    baselineDrop == null ? 'N/A' : Number(baselineDrop) > 0 ? `${Number(baselineDrop).toFixed(1)}% 낮음` : '뚜렷한 하락 없음';
  document.getElementById('active-months-3').textContent = `${Number(enterprise.active_month_count_3 || 0)} / 3개월`;
  document.getElementById('active-months-6').textContent = `${Number(enterprise.active_month_count_6 || 0)} / 6개월`;
  document.getElementById('zero-streak').textContent = `${Number(enterprise.zero_activity_streak || 0)}개월`;
  document.getElementById('last-active-month').textContent = enterprise.last_active_month || '해당 기간 내 없음';
  document.getElementById('detail-notice').textContent =
    data.notice || '거래 상태는 월별 승인 실적을 바탕으로 산정하며 미래 이탈 확률이나 재무제표 기반 건전성 판정이 아닙니다.';
  renderCompanyAI(enterprise.openai_insight);
  renderCompanyChart();
  renderCompanySignals();
  renderMonthlyTable();
  renderPaymentMethodAnalysis();
  renderFinancialCompanyAnalysis();
}

function renderCompanyAI(insight) {
  const target = document.getElementById('company-ai-content');
  if (!insight) {
    target.innerHTML = '<p class="ai-muted">OpenAI 기업 진단이 없습니다. GitHub Actions 실행 로그를 확인하세요.</p>';
    return;
  }
  target.innerHTML = `<h3 class="ai-headline">${esc(insight.headline || '기업별 진단')}</h3>
    <p class="ai-summary">${esc(insight.summary || '')}</p>
    <div class="ai-columns"><div><h4>관측 결과 해석</h4>${renderList(insight.observations || [])}</div>
    <div><h4>권장 조치</h4>${renderList(insight.recommended_actions || [])}</div></div>
    <p class="ai-limitation">${esc(insight.limitations || 'AI 결과는 담당자가 검토한 뒤 활용하세요.')}</p>`;
}
function renderList(items) {
  return items?.length ? `<ul class="ai-list">${items.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` :
    '<p class="ai-muted">표시할 내용이 없습니다.</p>';
}

function renderCompanyChart() {
  if (!window.Chart) return;
  const canvas = document.getElementById('company-sales-chart');
  const rows = enterprise.monthly_sales || [];
  if (chart) chart.destroy();
  chart = new Chart(canvas, {
    type: 'line',
    data: { labels: rows.map(x => x.month), datasets: [{
      label: '승인금액', data: rows.map(x => x.approval_amount),
      borderColor: '#e7ad05', backgroundColor: 'rgba(244,189,37,.15)',
      fill: true, tension: .32, pointRadius: 4, pointBackgroundColor: '#e7ad05'
    }] },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => money(c.raw) } },
      },
      scales: { y: { beginAtZero: true, ticks: { callback: v => fmt.format(v) }, grid: { color: '#eff0f3' } },
        x: { grid: { display: false } } }
    }
  });
}

function renderCompanySignals() {
  const reasons = Array.isArray(enterprise.status_reasons) ? enterprise.status_reasons : [];
  const target = document.getElementById('company-signal-list');
  target.innerHTML = reasons.length
    ? reasons.map(reason => `<span class="reason-tag">${esc(reason)}</span>`).join('')
    : '<p class="empty-reasons">표시할 거래 상태 근거가 없습니다.</p>'; 
}

function renderMonthlyTable() {
  const rows = enterprise.monthly_sales || [];
  const tbody = document.getElementById('monthly-rows');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="5">월별 실적이 없습니다.</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((row, index) => {
    const previous = index > 0 ? rows[index - 1] : null;
    const salesRate = previous && Number(previous.approval_amount) !== 0
      ? (Number(row.approval_amount) - Number(previous.approval_amount)) / Number(previous.approval_amount) * 100
      : null;
    const countRate = previous && Number(previous.approval_count) !== 0
      ? (Number(row.approval_count) - Number(previous.approval_count)) / Number(previous.approval_count) * 100
      : null;
    return `<tr>
      <td>${esc(row.month)}</td>
      <td>${money(row.approval_amount)}</td>
      <td>${formatRate(salesRate)}</td>
      <td>${fmt.format(row.approval_count ?? 0)}</td>
      <td>${formatRate(countRate)}</td>
    </tr>`;
  }).join('');
}

function renderPaymentMethodAnalysis() {
  const series = Array.isArray(enterprise.payment_method_analysis) ? enterprise.payment_method_analysis : [];
  const table = document.getElementById('payment-method-rows');
  const preferredOrder = ['카드', '계좌'];
  const rows = [...series].sort((a, b) => {
    const ai = preferredOrder.indexOf(a.payment_method);
    const bi = preferredOrder.indexOf(b.payment_method);
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi) || String(a.payment_method).localeCompare(String(b.payment_method), 'ko');
  });
  if (!rows.length) {
    table.innerHTML = '<tr><td colspan="6">결제수단별 집계 데이터가 없습니다. 최신 분석을 실행해 주세요.</td></tr>';
    return;
  }

  const latestMonth = String(data.analysis_month || '');
  const latestRows = rows.map(item => ({
    label: item.payment_method,
    current: getDimensionMonth(item.monthly_sales, latestMonth),
    previous: getPreviousDimensionMonth(item.monthly_sales, latestMonth),
    history: item.monthly_sales || []
  }));
  const totalAmount = latestRows.reduce((sum, item) => sum + number(item.current?.approval_amount), 0);
  table.innerHTML = latestRows.map(item => {
    const amount = number(item.current?.approval_amount);
    const count = number(item.current?.approval_count);
    const share = totalAmount > 0 ? amount / totalAmount * 100 : 0;
    return `<tr><td>${esc(item.label)}</td><td>${money(amount)}</td><td>${share.toFixed(1)}%</td><td>${formatRate(compareDimension(item.current, item.previous, 'approval_amount'))}</td><td>${fmt.format(count)}</td><td>${formatRate(compareDimension(item.current, item.previous, 'approval_count'))}</td></tr>`;
  }).join('');

  if (!window.Chart) return;
  const labels = latestRows[0].history.map(item => item.month);
  const palette = { '카드': '#e7ad05', '계좌': '#4977c9' };
  if (paymentMethodChart) paymentMethodChart.destroy();
  paymentMethodChart = new Chart(document.getElementById('payment-method-chart'), {
    type: 'line',
    data: {
      labels,
      datasets: latestRows.map(item => ({
        label: item.label,
        data: item.history.map(month => number(month.approval_amount)),
        borderColor: palette[item.label] || '#7b8494',
        backgroundColor: palette[item.label] || '#7b8494',
        tension: .28, pointRadius: 3, fill: false
      }))
    },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: true, position: 'bottom' }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${money(c.raw)}` } } },
      scales: { y: { beginAtZero: true, ticks: { callback: v => fmt.format(v) }, grid: { color: '#eff0f3' } }, x: { grid: { display: false } } }
    }
  });
}

function renderFinancialCompanyAnalysis() {
  const companies = Array.isArray(enterprise.financial_company_analysis) ? enterprise.financial_company_analysis : [];
  const table = document.getElementById('financial-company-rows');
  const select = document.getElementById('financial-company-select');
  if (!companies.length) {
    table.innerHTML = '<tr><td colspan="6">금융회사별 집계 데이터가 없습니다. 최신 분석을 실행해 주세요.</td></tr>';
    select.innerHTML = '<option value="">금융회사 데이터 없음</option>';
    return;
  }

  const latestMonth = String(data.analysis_month || '');
  const ranked = companies.map(item => ({
    ...item,
    current: getDimensionMonth(item.monthly_sales, latestMonth),
    previous: getPreviousDimensionMonth(item.monthly_sales, latestMonth)
  })).sort((a, b) => number(b.current?.approval_amount) - number(a.current?.approval_amount)
      || String(a.financial_company).localeCompare(String(b.financial_company), 'ko'));
  const totalAmount = ranked.reduce((sum, item) => sum + number(item.current?.approval_amount), 0);

  table.innerHTML = ranked.map(item => {
    const amount = number(item.current?.approval_amount);
    const count = number(item.current?.approval_count);
    const share = totalAmount > 0 ? amount / totalAmount * 100 : 0;
    return `<tr><td>${esc(item.financial_company)}</td><td>${money(amount)}</td><td>${share.toFixed(1)}%</td><td>${formatRate(compareDimension(item.current, item.previous, 'approval_amount'))}</td><td>${fmt.format(count)}</td><td>${formatRate(compareDimension(item.current, item.previous, 'approval_count'))}</td></tr>`;
  }).join('');

  if (window.Chart) {
    const topTen = ranked.slice(0, 10).reverse();
    if (financialCompanyChart) financialCompanyChart.destroy();
    financialCompanyChart = new Chart(document.getElementById('financial-company-chart'), {
      type: 'bar',
      data: { labels: topTen.map(item => item.financial_company), datasets: [{
        label: '승인금액', data: topTen.map(item => number(item.current?.approval_amount)),
        backgroundColor: '#e7ad05', borderRadius: 4, maxBarThickness: 22
      }] },
      options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => money(c.raw) } } },
        scales: { x: { beginAtZero: true, ticks: { callback: v => fmt.format(v) }, grid: { color: '#eff0f3' } }, y: { grid: { display: false } } }
      }
    });
  }

  select.innerHTML = ranked.map(item => `<option value="${esc(item.financial_company)}">${esc(item.financial_company)}</option>`).join('');
  select.onchange = () => renderFinancialCompanyTrend(ranked, select.value);
  renderFinancialCompanyTrend(ranked, select.value || ranked[0].financial_company);
}

function renderFinancialCompanyTrend(ranked, selectedName) {
  const item = ranked.find(company => String(company.financial_company) === String(selectedName));
  if (!item || !window.Chart) return;
  if (financialTrendChart) financialTrendChart.destroy();
  const history = item.monthly_sales || [];
  financialTrendChart = new Chart(document.getElementById('financial-trend-chart'), {
    type: 'line',
    data: { labels: history.map(row => row.month), datasets: [{
      label: `${item.financial_company} 승인금액`, data: history.map(row => number(row.approval_amount)),
      borderColor: '#4977c9', backgroundColor: 'rgba(73,119,201,.12)',
      fill: true, tension: .28, pointRadius: 3, pointBackgroundColor: '#4977c9'
    }] },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => money(c.raw) } } },
      scales: { y: { beginAtZero: true, ticks: { callback: v => fmt.format(v) }, grid: { color: '#eff0f3' } }, x: { grid: { display: false } } }
    }
  });
}

function getDimensionMonth(history, month) {
  const rows = Array.isArray(history) ? history : [];
  return rows.find(row => String(row.month) === month) || rows[rows.length - 1] || null;
}
function getPreviousDimensionMonth(history, month) {
  const rows = Array.isArray(history) ? history : [];
  const index = rows.findIndex(row => String(row.month) === month);
  return index > 0 ? rows[index - 1] : null;
}
function compareDimension(current, previous, key) {
  if (!current || !previous) return null;
  const currentValue = number(current[key]);
  const previousValue = number(previous[key]);
  if (previousValue === 0) return currentValue === 0 ? 0 : null;
  return (currentValue - previousValue) / previousValue * 100;
}
function number(value) {
  const result = Number(value ?? 0);
  return Number.isFinite(result) ? result : 0;
}

function formatRate(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return 'N/A';
  const rate = Number(value);
  return `<span class="${rate < 0 ? 'danger' : rate > 0 ? 'positive' : ''}">${rate > 0 ? '+' : ''}${rate.toFixed(1)}%</span>`;
}
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}
document.addEventListener('DOMContentLoaded', () => {
  init().catch(err => {
    document.getElementById('detail-notice').textContent = err.message;
    document.getElementById('monthly-rows').innerHTML = `<tr><td colspan="5">${esc(err.message)}</td></tr>`;
  });
});
