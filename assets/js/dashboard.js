const DATA_URL = 'public-data/dashboard_summary.json';
let dashboardData = null;
let salesChart = null;
const num = new Intl.NumberFormat('ko-KR');
const money = value => `${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(value ?? 0)}원`;
const label = { HIGH: '위험', MEDIUM: '주의', LOW: '낮은 위험', UNKNOWN: '확인 필요' };
const order = { HIGH: 0, MEDIUM: 1, LOW: 2, UNKNOWN: 3 };

async function loadDashboard() {
  const response = await fetch(DATA_URL, { cache: 'no-store' });
  if (!response.ok) throw new Error('기업 분석 결과를 불러오지 못했습니다. GitHub Actions 실행 결과를 확인해 주세요.');
  dashboardData = await response.json();
  const summary = dashboardData.summary || {};
  const companies = dashboardData.enterprises || [];
  document.getElementById('analysis-month').textContent = `${dashboardData.analysis_month || '-'} (${dashboardData.as_of_date || '기준일 미상'})`;
  document.getElementById('enterprise-count').textContent = num.format(summary.enterprise_count ?? companies.length);
  document.getElementById('high-enterprise-count').textContent = num.format(summary.high_risk_enterprise_count || 0);
  document.getElementById('medium-enterprise-count').textContent = num.format(summary.medium_risk_enterprise_count || 0);
  document.getElementById('total-approval-amount').textContent = money(companies.reduce((sum, e) => sum + Number(e.current_approval_amount || 0), 0));
  document.getElementById('data-notice').textContent = dashboardData.notice || 'AI 이상징후는 담당자가 검토해야 하는 위험 신호입니다.';
  renderPortfolioAI(dashboardData.openai_portfolio_insight);
  renderRiskBars(); renderSalesChart(); renderEnterpriseRows();
}

function renderPortfolioAI(insight) {
  const target = document.getElementById('portfolio-ai-content');
  if (!insight) { target.innerHTML = '<p class="ai-muted">OpenAI 브리핑이 없습니다. Actions에서 OpenAI 인사이트 단계를 확인하세요.</p>'; return; }
  target.innerHTML = `<h3 class="ai-headline">${escapeHtml(insight.headline || '기업 포트폴리오 요약')}</h3>
    <p class="ai-summary">${escapeHtml(insight.summary || '')}</p>
    <div class="ai-columns"><div><h4>집중 영역</h4>${renderList(insight.focus_areas || [])}</div><div><h4>권장 조치</h4>${renderList(insight.recommended_actions || [])}</div></div>
    <p class="ai-limitation">${escapeHtml(insight.limitations || 'AI 결과는 담당자가 검토한 뒤 활용하세요.')}</p>`;
}
function renderList(items) { return items?.length ? `<ul class="ai-list">${items.map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>` : '<p class="ai-muted">표시할 내용이 없습니다.</p>'; }

function renderRiskBars() {
  const companies = dashboardData.enterprises || [];
  const levels = [['HIGH','위험','#d54c4c'],['MEDIUM','주의','#cd8123'],['LOW','낮은 위험','#258563'],['UNKNOWN','확인 필요','#858b96']];
  const total = Math.max(companies.length, 1);
  document.getElementById('risk-bars').innerHTML = levels.map(([key, title, color]) => {
    const count = companies.filter(e => e.risk_level === key).length;
    return `<div class="risk-line"><div class="risk-line-top"><span>${title}</span><strong>${num.format(count)}개</strong></div><div class="bar-track"><div class="bar-fill" style="width:${Math.min(100, count / total * 100)}%;background:${color}"></div></div></div>`;
  }).join('');
}
function renderSalesChart() {
  const canvas = document.getElementById('sales-chart');
  if (!window.Chart) { canvas.insertAdjacentHTML('afterend', '<p>차트 라이브러리를 불러오지 못했습니다.</p>'); return; }
  const rows = dashboardData.monthly_sales || [];
  if (salesChart) salesChart.destroy();
  salesChart = new Chart(canvas, { type: 'line', data: { labels: rows.map(x => x.month), datasets: [{ label: '승인금액', data: rows.map(x => x.approval_amount), borderColor: '#e7ad05', backgroundColor: 'rgba(244,189,37,.15)', fill: true, tension: .32, pointRadius: 4 }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => money(c.raw) } }, }, scales: { y: { beginAtZero: true, ticks: { callback: v => num.format(v) }, grid: { color: '#eff0f3' } }, x: { grid: { display: false } } } } });
}
function renderEnterpriseRows() {
  if (!dashboardData) return;
  const term = (document.getElementById('search').value || '').trim().toLowerCase();
  const filter = document.getElementById('risk-filter').value;
  const rows = (dashboardData.enterprises || []).filter(e => `${e.display_name || e.enterprise_name || ''} ${e.enterprise_id || ''}`.toLowerCase().includes(term) && (filter === 'ALL' || e.risk_level === filter))
    .sort((a,b) => (order[a.risk_level] ?? 9) - (order[b.risk_level] ?? 9) || (b.risk_score ?? -1) - (a.risk_score ?? -1));
  const tbody = document.getElementById('enterprise-rows');
  if (!rows.length) { tbody.innerHTML = '<tr><td colspan="7">조건에 맞는 기업이 없습니다.</td></tr>'; return; }
  tbody.innerHTML = rows.map(e => {
    const detailUrl = `company.html?enterprise_id=${encodeURIComponent(e.enterprise_id)}`;
    const score = e.risk_score == null ? '—' : `${Number(e.risk_score).toFixed(1)} / 100`;
    const insight = e.openai_insight || {};
    return `<tr class="clickable-row" data-href="${escapeHtml(detailUrl)}">
      <td><strong>${escapeHtml(e.display_name || e.enterprise_name || '기업명 없음')}</strong><small class="cell-subtitle">관리번호 ${escapeHtml(e.enterprise_id)}</small></td>
      <td><span class="badge ${e.risk_level || 'UNKNOWN'}">${label[e.risk_level] || label.UNKNOWN}</span></td>
      <td><strong>${score}</strong></td>
      <td>${money(e.current_approval_amount)}</td>
      <td>${formatRate(e.sales_change_rate)}</td>
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
  document.getElementById('risk-filter').addEventListener('change', renderEnterpriseRows);
  loadDashboard().catch(err => { document.getElementById('data-notice').textContent = err.message; document.getElementById('enterprise-rows').innerHTML = `<tr><td colspan="7">${escapeHtml(err.message)}</td></tr>`; });
});
