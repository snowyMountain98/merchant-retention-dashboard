const DATA_URL = 'public-data/dashboard_summary.json';
let data = null;
let enterprise = null;
let companyMerchants = [];
let chart = null;
const numberFormat = new Intl.NumberFormat('ko-KR');
const moneyFormat = value => new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(value ?? 0);
const levelLabel = { HIGH: '위험', MEDIUM: '주의', LOW: '낮은 위험', UNKNOWN: '확인 필요' };
const levelOrder = { HIGH: 0, MEDIUM: 1, LOW: 2, UNKNOWN: 3 };

async function init() {
  const enterpriseId = new URLSearchParams(window.location.search).get('enterprise_id');
  if (!enterpriseId) throw new Error('기업관리번호가 URL에 없습니다. 기업 목록에서 기업을 선택해 주세요.');
  const response = await fetch(DATA_URL, { cache: 'no-store' });
  if (!response.ok) throw new Error('분석 결과 JSON을 불러오지 못했습니다. GitHub Actions 실행 결과를 확인해 주세요.');
  data = await response.json();
  enterprise = (data.enterprises || []).find(x => String(x.enterprise_id) === enterpriseId);
  if (!enterprise) throw new Error('해당 기업을 분석 결과에서 찾지 못했습니다. 최신 분석을 실행했는지 확인해 주세요.');
  companyMerchants = (data.merchants || []).filter(x => String(x.enterprise_id) === enterpriseId);
  document.title = `${enterprise.enterprise_name} - 가맹점 상세`;
  document.getElementById('enterprise-name').textContent = enterprise.enterprise_name || '기업명 없음';
  document.getElementById('enterprise-id').textContent = enterprise.enterprise_id;
  document.getElementById('analysis-month').textContent = data.analysis_month || '—';
  document.getElementById('enterprise-risk').className = `level-chip ${enterprise.risk_level || 'UNKNOWN'}`;
  document.getElementById('enterprise-risk').textContent = levelLabel[enterprise.risk_level] || levelLabel.UNKNOWN;
  document.getElementById('merchant-count').textContent = numberFormat.format(enterprise.merchant_count || companyMerchants.length);
  document.getElementById('high-count').textContent = numberFormat.format(enterprise.high_risk_count || 0);
  document.getElementById('medium-count').textContent = numberFormat.format(enterprise.medium_risk_count || 0);
  document.getElementById('approval-amount').textContent = `${moneyFormat(enterprise.current_approval_amount)}원`;
  document.getElementById('detail-notice').textContent = data.notice || 'AI 이상징후 점수는 이탈 확률이 아닙니다.';
  renderCompanyChart();
  renderRiskSummary();
  renderMerchants();
}

function renderCompanyChart() {
  if (!window.Chart) return;
  const canvas = document.getElementById('company-sales-chart');
  const rows = enterprise.monthly_sales || [];
  if (chart) chart.destroy();
  chart = new Chart(canvas, {
    type: 'line',
    data: { labels: rows.map(x => x.month), datasets: [{ label: '승인금액', data: rows.map(x => x.approval_amount), borderColor: '#e7ad05', backgroundColor: 'rgba(244,189,37,.15)', fill: true, tension: .32, pointRadius: 4, pointBackgroundColor: '#e7ad05' }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${moneyFormat(c.raw)}원` } } }, scales: { y: { beginAtZero: true, ticks: { callback: v => numberFormat.format(v) }, grid: { color: '#eff0f3' } }, x: { grid: { display: false } } } }
  });
}

function renderRiskSummary() {
  const counts = [
    ['위험', enterprise.high_risk_count || 0, '#d54c4c'],
    ['주의', enterprise.medium_risk_count || 0, '#cd8123'],
    ['낮은 위험', enterprise.low_risk_count || 0, '#258563'],
    ['확인 필요', enterprise.unknown_count || 0, '#858b96']
  ];
  const total = Math.max(enterprise.merchant_count || 0, 1);
  document.getElementById('risk-summary-list').innerHTML = counts.map(([label, count, color]) =>
    `<div class="risk-line"><div class="risk-line-top"><span>${label}</span><strong>${numberFormat.format(count)}개</strong></div><div class="bar-track"><div class="bar-fill" style="width:${Math.min(100, count / total * 100)}%;background:${color}"></div></div></div>`
  ).join('');
}

function renderMerchants() {
  if (!enterprise) return;
  const term = (document.getElementById('merchant-search').value || '').trim().toLowerCase();
  const filter = document.getElementById('merchant-risk-filter').value;
  const rows = companyMerchants.filter(m => `${m.merchant_id || ''} ${m.merchant_name || ''}`.toLowerCase().includes(term) && (filter === 'ALL' || m.risk_level === filter))
    .sort((a, b) => (levelOrder[a.risk_level] ?? 9) - (levelOrder[b.risk_level] ?? 9) || (b.risk_score ?? -1) - (a.risk_score ?? -1));
  const tbody = document.getElementById('merchant-rows');
  if (!rows.length) { tbody.innerHTML = '<tr><td colspan="8">조건에 맞는 가맹점이 없습니다.</td></tr>'; return; }
  tbody.innerHTML = rows.map(m => {
    const url = `merchant-detail.html?enterprise_id=${encodeURIComponent(enterprise.enterprise_id)}&merchant_id=${encodeURIComponent(m.merchant_id)}`;
    const score = m.risk_score === null || m.risk_score === undefined ? '—' : Number(m.risk_score).toFixed(1);
    return `<tr class="clickable-row" data-href="${escapeHtml(url)}">
      <td><strong>${escapeHtml(m.merchant_name || '가맹점명 없음')}</strong><small class="cell-subtitle">${escapeHtml(m.merchant_id)}</small></td>
      <td><strong>${score}</strong>${score === '—' ? '' : ' / 100'}</td>
      <td><span class="badge ${m.risk_level || 'UNKNOWN'}">${levelLabel[m.risk_level] || levelLabel.UNKNOWN}</span></td>
      <td>${m.current_approval_amount == null ? '—' : `${moneyFormat(m.current_approval_amount)}원`}</td>
      <td>${m.current_approval_count == null ? '—' : numberFormat.format(m.current_approval_count)}</td>
      <td>${formatRate(m.sales_change_rate)}</td>
      <td>${escapeHtml(m.last_transaction_date || '—')}</td>
      <td><a class="detail-link" href="${escapeHtml(url)}">분석 보기 →</a></td>
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
  document.getElementById('merchant-search').addEventListener('input', renderMerchants);
  document.getElementById('merchant-risk-filter').addEventListener('change', renderMerchants);
  init().catch(err => {
    document.getElementById('detail-notice').textContent = err.message;
    document.getElementById('merchant-rows').innerHTML = `<tr><td colspan="8">${escapeHtml(err.message)}</td></tr>`;
  });
});
