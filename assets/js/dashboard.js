const DATA_URL = 'public-data/dashboard_summary.json';
let dashboardData = null;
let salesChart = null;
const num = new Intl.NumberFormat('ko-KR');
const money = value => `${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(value ?? 0)}원`;
const labels = {
  LONG_INACTIVE: '장기 미거래', RECENTLY_INACTIVE: '최근 미거래', DECLINING: '거래 감소 관찰',
  NO_ACTIVITY: '관측기간 내 거래 없음', RESTARTED: '거래 재개', FIRST_ACTIVITY: '최근 관측기간 첫 거래',
  SHORT_HISTORY: '거래 이력 짧음', TRADING: '거래 유지 중'
};
const order = { LONG_INACTIVE: 0, RECENTLY_INACTIVE: 1, DECLINING: 2, NO_ACTIVITY: 3, RESTARTED: 4, FIRST_ACTIVITY: 5, SHORT_HISTORY: 6, TRADING: 7 };
const colors = {
  LONG_INACTIVE: '#d54c4c', RECENTLY_INACTIVE: '#cd8123', DECLINING: '#b38a14',
  NO_ACTIVITY: '#727985', RESTARTED: '#4977c9', FIRST_ACTIVITY: '#7a69ad', SHORT_HISTORY: '#858b96', TRADING: '#258563'
};

async function loadDashboard() {
  const response = await fetch(DATA_URL, { cache: 'no-store' });
  if (!response.ok) throw new Error('기업 분석 결과를 불러오지 못했습니다. GitHub Actions 실행 결과를 확인해 주세요.');
  dashboardData = await response.json();
  const summary = dashboardData.summary || {};
  const companies = dashboardData.enterprises || [];
  document.getElementById('analysis-month').textContent = `${dashboardData.analysis_month || '-'} (${dashboardData.as_of_date || '기준일 미상'})`;
  document.getElementById('enterprise-count').textContent = num.format(summary.enterprise_count ?? companies.length);
  document.getElementById('active-enterprise-count').textContent = num.format(companies.filter(isTradingNow).length);
  document.getElementById('long-inactive-enterprise-count').textContent = num.format(companies.filter(e => e.transaction_status === 'LONG_INACTIVE').length);
  document.getElementById('total-approval-amount').textContent = money(companies.reduce((sum, e) => sum + Number(e.current_approval_amount || 0), 0));
  document.getElementById('data-notice').textContent = dashboardData.notice || '거래 상태는 월별 승인금액·승인건수에서 확인할 수 있는 관측 정보입니다.';
  renderPortfolioAI(dashboardData.openai_portfolio_insight);
  renderStatusBars(); renderSalesChart(); renderEnterpriseRows();
}
function isTradingNow(company) {
  return Number(company.current_approval_amount || 0) > 0 || Number(company.current_approval_count || 0) > 0;
}
function renderPortfolioAI(insight) {
  const target = document.getElementById('portfolio-ai-content');
  if (!insight) { target.innerHTML = '<p class="ai-muted">OpenAI 브리핑이 없습니다. Actions에서 OpenAI 인사이트 단계를 확인하세요.</p>'; return; }
  target.innerHTML = `<h3 class="ai-headline">${escapeHtml(insight.headline || '기업 포트폴리오 요약')}</h3>
    <p class="ai-summary">${escapeHtml(insight.summary || '')}</p>
    <div class="ai-columns"><div><h4>집중 영역</h4>${renderList(insight.focus_areas || [])}</div><div><h4>권장 조치</h4>${renderList(insight.recommended_actions || [])}</div></div>
    <p class="ai-limitation">${escapeHtml(insight.limitations || '거래 집계만으로 기업의 재무 건전성 또는 미래 이탈을 확정할 수 없습니다.')}</p>`;
}
function renderList(items) { return items?.length ? `<ul class="ai-list">${items.map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>` : '<p class="ai-muted">표시할 내용이 없습니다.</p>'; }
function renderStatusBars() {
  const companies = dashboardData.enterprises || [];
  const states = Object.keys(labels);
  const total = Math.max(companies.length, 1);
  document.getElementById('status-bars').innerHTML = states.map(key => {
    const count = companies.filter(e => e.transaction_status === key).length;
    return `<div class="status-line"><div class="status-line-top"><span><span class="badge ${key}">${labels[key]}</span></span><strong>${num.format(count)}개</strong></div><div class="bar-track"><div class="bar-fill" style="width:${Math.min(100, count / total * 100)}%;background:${colors[key]}"></div></div></div>`;
  }).join('');
}
function renderSalesChart() {
  const canvas = document.getElementById('sales-chart');
  if (!window.Chart) { canvas.insertAdjacentHTML('afterend', '<p>차트 라이브러리를 불러오지 못했습니다.</p>'); return; }
  const rows = dashboardData.monthly_sales || [];
  if (salesChart) salesChart.destroy();
  salesChart = new Chart(canvas, { type: 'line', data: { labels: rows.map(x => x.month), datasets: [{ label: '승인금액', data: rows.map(x => x.approval_amount), borderColor: '#e7ad05', backgroundColor: 'rgba(244,189,37,.15)', fill: true, tension: .32, pointRadius: 4 }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => money(c.raw) } } }, scales: { y: { beginAtZero: true, ticks: { callback: v => num.format(v) }, grid: { color: '#eff0f3' } }, x: { grid: { display: false } } } } });
}
function renderEnterpriseRows() {
  if (!dashboardData) return;
  const term = (document.getElementById('search').value || '').trim().toLowerCase();
  const filter = document.getElementById('status-filter').value;
  const rows = (dashboardData.enterprises || []).filter(e => `${e.display_name || e.enterprise_name || ''} ${e.enterprise_id || ''}`.toLowerCase().includes(term) && (filter === 'ALL' || e.transaction_status === filter))
    .sort((a,b) => (order[a.transaction_status] ?? 99) - (order[b.transaction_status] ?? 99) || (a.enterprise_name || '').localeCompare(b.enterprise_name || '', 'ko'));
  const tbody = document.getElementById('enterprise-rows');
  if (!rows.length) { tbody.innerHTML = '<tr><td colspan="7">조건에 맞는 기업이 없습니다.</td></tr>'; return; }
  tbody.innerHTML = rows.map(e => {
    const detailUrl = `company.html?enterprise_id=${encodeURIComponent(e.enterprise_id)}`;
    const insight = e.openai_insight || {};
    const reasons = Array.isArray(e.status_reasons) ? e.status_reasons.join(' · ') : '';
    return `<tr class="clickable-row" data-href="${escapeHtml(detailUrl)}">
      <td><strong>${escapeHtml(e.display_name || e.enterprise_name || '기업명 없음')}</strong><small class="cell-subtitle">관리번호 ${escapeHtml(e.enterprise_id)}</small></td>
      <td><span class="badge ${escapeHtml(e.transaction_status || 'NO_ACTIVITY')}">${escapeHtml(e.transaction_status_label || labels[e.transaction_status] || '상태 확인 필요')}</span></td>
      <td>${money(e.current_approval_amount)}</td>
      <td>${formatRate(e.sales_change_rate)}</td>
      <td class="reasons">${escapeHtml(reasons || '특이사항 없음')}</td>
      <td class="ai-cell-summary">${escapeHtml(insight.headline || insight.summary || 'AI 진단 없음')}</td>
      <td><a class="detail-link" href="${escapeHtml(detailUrl)}">기업 분석 →</a></td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('tr[data-href]').forEach(row => row.addEventListener('click', event => { if (!event.target.closest('a')) window.location.href = row.dataset.href; }));
}
function formatRate(value) {
  if (value == null || !Number.isFinite(Number(value))) return 'N/A';
  const n = Number(value);
  return `<span class="${n < 0 ? 'danger' : n > 0 ? 'positive' : ''}">${n > 0 ? '+' : ''}${n.toFixed(1)}%</span>`;
}
function escapeHtml(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c])); }
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('search').addEventListener('input', renderEnterpriseRows);
  document.getElementById('status-filter').addEventListener('change', renderEnterpriseRows);
  loadDashboard().catch(err => { document.getElementById('data-notice').textContent = err.message; document.getElementById('enterprise-rows').innerHTML = `<tr><td colspan="7">${escapeHtml(err.message)}</td></tr>`; });
});
