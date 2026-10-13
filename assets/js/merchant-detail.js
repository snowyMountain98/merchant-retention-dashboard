const DATA_URL = 'public-data/dashboard_summary.json';
const num = new Intl.NumberFormat('ko-KR');
const money = value => new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(value ?? 0);
const labels = { HIGH: '위험', MEDIUM: '주의', LOW: '낮은 위험', UNKNOWN: '확인 필요' };
let chart = null;

async function init() {
  const params = new URLSearchParams(window.location.search);
  const enterpriseId = params.get('enterprise_id');
  const merchantId = params.get('merchant_id');
  if (!enterpriseId || !merchantId) throw new Error('기업 또는 가맹점 식별자가 URL에 없습니다. 목록 화면에서 다시 선택해 주세요.');
  const response = await fetch(DATA_URL, { cache: 'no-store' });
  if (!response.ok) throw new Error('분석 결과 JSON을 불러오지 못했습니다. GitHub Actions 실행 결과를 확인해 주세요.');
  const data = await response.json();
  const enterprise = (data.enterprises || []).find(x => String(x.enterprise_id) === enterpriseId);
  const merchant = (data.merchants || []).find(x => String(x.enterprise_id) === enterpriseId && String(x.merchant_id) === merchantId);
  if (!enterprise || !merchant) throw new Error('가맹점 분석 결과를 찾지 못했습니다. 최신 AI 분석을 실행했는지 확인해 주세요.');

  document.title = `${merchant.merchant_name || merchant.merchant_id} - AI 분석`;
  document.getElementById('merchant-name').textContent = merchant.merchant_name || '가맹점명 없음';
  document.getElementById('merchant-id').textContent = merchant.merchant_id;
  document.getElementById('enterprise-name').textContent = enterprise.enterprise_name || '기업명 없음';
  document.getElementById('enterprise-id').textContent = enterprise.enterprise_id;
  const returnUrl = `company.html?enterprise_id=${encodeURIComponent(enterpriseId)}`;
  document.getElementById('back-to-company').href = returnUrl;
  document.getElementById('back-to-company-nav').href = returnUrl;
  const risk = document.getElementById('merchant-risk');
  risk.className = `level-chip ${merchant.risk_level || 'UNKNOWN'}`;
  risk.textContent = labels[merchant.risk_level] || labels.UNKNOWN;
  document.getElementById('merchant-score').textContent = `AI 점수 ${merchant.risk_score == null ? '—' : Number(merchant.risk_score).toFixed(1) + ' / 100'}`;
  document.getElementById('approval-amount').textContent = merchant.current_approval_amount == null ? '—' : `${money(merchant.current_approval_amount)}원`;
  document.getElementById('approval-count').textContent = merchant.current_approval_count == null ? '—' : num.format(merchant.current_approval_count);
  const salesChange = merchant.sales_change_rate == null ? null : Number(merchant.sales_change_rate);
  document.getElementById('sales-change').innerHTML = salesChange == null ? 'N/A' : `<span class="${salesChange < 0 ? 'danger' : salesChange > 0 ? 'positive' : ''}">${salesChange > 0 ? '+' : ''}${salesChange.toFixed(1)}%</span>`;
  document.getElementById('last-transaction').textContent = merchant.last_transaction_date || '—';
  document.getElementById('detail-notice').textContent = `${data.analysis_month || '기준월 미상'} 기준 분석 · ${merchant.data_quality === 'INSUFFICIENT_HISTORY' ? '비교 이력이 부족하여 AI 점수를 산출하지 못했습니다.' : '점수는 이탈 확률이 아니라 이상징후 상대 점수입니다.'}`;
  const reasons = merchant.risk_reasons || [];
  document.getElementById('risk-reasons').innerHTML = reasons.length ? reasons.map(r => `<span class="reason-tag">${escapeHtml(r)}</span>`).join('') : '<span class="empty-reasons">표시할 위험 사유가 없습니다.</span>';
  renderHistory(merchant.monthly_sales || []);
}

function renderHistory(rows) {
  const ordered = [...rows].sort((a, b) => String(a.month).localeCompare(String(b.month)));
  const tbody = document.getElementById('monthly-rows');
  if (!ordered.length) tbody.innerHTML = '<tr><td colspan="7">월별 실적이 없습니다.</td></tr>';
  else tbody.innerHTML = ordered.slice().reverse().map(x => `<tr><td>${escapeHtml(x.month)}</td><td>${money(x.approval_amount)}원</td><td>${num.format(x.approval_count || 0)}</td><td>${money(x.cancel_amount)}원</td><td>${num.format(x.cancel_count || 0)}</td><td>${money(x.partial_cancel_amount)}원</td><td>${num.format(x.partial_cancel_count || 0)}</td></tr>`).join('');
  if (!window.Chart) return;
  const canvas = document.getElementById('merchant-sales-chart');
  if (chart) chart.destroy();
  chart = new Chart(canvas, { type: 'line', data: { labels: ordered.map(x => x.month), datasets: [{ label: '승인금액', data: ordered.map(x => x.approval_amount), borderColor: '#e7ad05', backgroundColor: 'rgba(244,189,37,.15)', fill: true, tension: .3, pointRadius: 4, pointBackgroundColor: '#e7ad05' }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${money(c.raw)}원` } } }, scales: { y: { beginAtZero: true, ticks: { callback: v => num.format(v) }, grid: { color: '#eff0f3' } }, x: { grid: { display: false } } } } });
}
function escapeHtml(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
document.addEventListener('DOMContentLoaded', () => init().catch(err => {
  document.getElementById('detail-notice').textContent = err.message;
  document.getElementById('monthly-rows').innerHTML = `<tr><td colspan="7">${escapeHtml(err.message)}</td></tr>`;
}));
