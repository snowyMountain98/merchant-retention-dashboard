const DATA_URL = 'public-data/dashboard_summary.json';
let dashboardData = null;
let salesChart = null;
const numberFormat = new Intl.NumberFormat('ko-KR');
const moneyFormat = value => new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(value ?? 0);
const levelLabel = { HIGH: '위험', MEDIUM: '주의', LOW: '낮은 위험', UNKNOWN: '확인 필요' };
const levelOrder = { HIGH: 0, MEDIUM: 1, LOW: 2, UNKNOWN: 3 };

async function loadDashboard() {
  const response = await fetch(DATA_URL, { cache: 'no-store' });
  if (!response.ok) throw new Error('분석 결과를 불러오지 못했습니다. GitHub Actions 실행 결과를 확인해 주세요.');
  dashboardData = await response.json();
  const summary = dashboardData.summary || {};
  document.getElementById('analysis-month').textContent = `${dashboardData.analysis_month || '-'} (${dashboardData.as_of_date || '기준일 미상'})`;
  document.getElementById('enterprise-count').textContent = numberFormat.format(summary.enterprise_count || (dashboardData.enterprises || []).length);
  document.getElementById('high-enterprise-count').textContent = numberFormat.format(summary.high_risk_enterprise_count || 0);
  document.getElementById('merchant-count').textContent = numberFormat.format(summary.merchant_count || 0);
  document.getElementById('high-merchant-count').textContent = numberFormat.format(summary.high_risk_count || 0);
  document.getElementById('data-notice').textContent = dashboardData.notice || 'AI 이상징후 탐지 기반 분석입니다. 점수는 이탈 확률이 아닙니다.';
  renderRiskBars();
  renderSalesChart();
  renderEnterpriseRows();
}

function renderRiskBars() {
  const companies = dashboardData.enterprises || [];
  const counts = [
    ['HIGH', '위험', companies.filter(x => x.risk_level === 'HIGH').length, '#d54c4c'],
    ['MEDIUM', '주의', companies.filter(x => x.risk_level === 'MEDIUM').length, '#cd8123'],
    ['LOW', '낮은 위험', companies.filter(x => x.risk_level === 'LOW').length, '#258563'],
    ['UNKNOWN', '확인 필요', companies.filter(x => x.risk_level === 'UNKNOWN').length, '#858b96']
  ];
  const total = Math.max(companies.length, 1);
  document.getElementById('risk-bars').innerHTML = counts.map(([key, label, count, color]) =>
    `<div class="risk-line"><div class="risk-line-top"><span>${label}</span><strong>${count}개</strong></div><div class="bar-track"><div class="bar-fill" style="width:${Math.min(100, count / total * 100)}%;background:${color}"></div></div></div>`
  ).join('');
}

function renderSalesChart() {
  const canvas = document.getElementById('sales-chart');
  if (!window.Chart) { canvas.insertAdjacentHTML('afterend', '<p>차트 라이브러리를 불러오지 못했습니다.</p>'); return; }
  const rows = dashboardData.monthly_sales || [];
  if (salesChart) salesChart.destroy();
  salesChart = new Chart(canvas, {
    type: 'line',
    data: { labels: rows.map(x => x.month), datasets: [{ label: '승인금액', data: rows.map(x => x.approval_amount), borderColor: '#e7ad05', backgroundColor: 'rgba(244,189,37,.15)', fill: true, tension: .32, pointRadius: 4, pointBackgroundColor: '#e7ad05' }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${moneyFormat(c.raw)}원` } } }, scales: { y: { beginAtZero: true, grid: { color: '#eff0f3' }, ticks: { callback: v => numberFormat.format(v) } }, x: { grid: { display: false } } } }
  });
}

function renderEnterpriseRows() {
  if (!dashboardData) return;
  const term = (document.getElementById('search').value || '').trim().toLowerCase();
  const filter = document.getElementById('risk-filter').value;
  const rows = (dashboardData.enterprises || []).filter(e =>
    `${e.enterprise_id || ''} ${e.enterprise_name || ''}`.toLowerCase().includes(term) && (filter === 'ALL' || e.risk_level === filter)
  ).sort((a, b) => (levelOrder[a.risk_level] ?? 9) - (levelOrder[b.risk_level] ?? 9) || (b.risk_score ?? -1) - (a.risk_score ?? -1) || String(a.enterprise_name).localeCompare(String(b.enterprise_name), 'ko'));
  const tbody = document.getElementById('enterprise-rows');
  if (!rows.length) { tbody.innerHTML = '<tr><td colspan="8">조건에 맞는 기업이 없습니다.</td></tr>'; return; }
  tbody.innerHTML = rows.map(e => {
    const score = e.risk_score === null || e.risk_score === undefined ? '—' : Number(e.risk_score).toFixed(1);
    const detailUrl = `company.html?enterprise_id=${encodeURIComponent(e.enterprise_id)}`;
    return `<tr class="clickable-row" data-href="${escapeHtml(detailUrl)}">
      <td><strong>${escapeHtml(e.enterprise_name || '기업명 없음')}</strong><small class="cell-subtitle">관리번호 ${escapeHtml(e.enterprise_id)}</small></td>
      <td>${numberFormat.format(e.merchant_count || 0)}개</td>
      <td class="danger">${numberFormat.format(e.high_risk_count || 0)}개</td>
      <td class="warning">${numberFormat.format(e.medium_risk_count || 0)}개</td>
      <td><strong>${score}</strong>${score === '—' ? '' : ' / 100'}</td>
      <td>${moneyFormat(e.current_approval_amount)}원</td>
      <td>${formatRate(e.sales_change_rate)}</td>
      <td><a class="detail-link" href="${escapeHtml(detailUrl)}">가맹점 보기 <span aria-hidden="true">→</span></a></td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('tr[data-href]').forEach(row => row.addEventListener('click', event => {
    if (event.target.closest('a')) return;
    window.location.href = row.dataset.href;
  }));
}

function formatRate(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return 'N/A';
  const n = Number(value);
  return `<span class="${n < 0 ? 'danger' : n > 0 ? 'positive' : ''}">${n > 0 ? '+' : ''}${n.toFixed(1)}%</span>`;
}
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('search').addEventListener('input', renderEnterpriseRows);
  document.getElementById('risk-filter').addEventListener('change', renderEnterpriseRows);
  loadDashboard().catch(err => {
    document.getElementById('data-notice').textContent = err.message;
    document.getElementById('enterprise-rows').innerHTML = `<tr><td colspan="8">${escapeHtml(err.message)}</td></tr>`;
  });
});
