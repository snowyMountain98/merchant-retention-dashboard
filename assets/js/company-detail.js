const DATA_URL = 'public-data/dashboard_summary.json';
const fmt = new Intl.NumberFormat('ko-KR');
const money = value => `${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(value ?? 0)}원`;
const labels = { HIGH: '위험', MEDIUM: '주의', LOW: '낮은 위험', UNKNOWN: '확인 필요' };
let chart = null;
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
  const risk = document.getElementById('enterprise-risk');
  risk.className = `level-chip ${enterprise.risk_level || 'UNKNOWN'}`;
  risk.textContent = labels[enterprise.risk_level] || labels.UNKNOWN;
  document.getElementById('approval-amount').textContent = money(enterprise.current_approval_amount);
  document.getElementById('sales-change').innerHTML = formatRate(enterprise.sales_change_rate);
  document.getElementById('count-change').innerHTML = formatRate(enterprise.transaction_change_rate);
  const baselineDrop = enterprise.sales_below_3m_pct;
  document.getElementById('sales-baseline-change').textContent =
    Number(enterprise.baseline_month_count || 0) < 1 ? '비교 이력 부족' :
    baselineDrop == null ? 'N/A' : Number(baselineDrop) > 0 ? `${Number(baselineDrop).toFixed(1)}% 낮음` : '뚜렷한 하락 없음';
  document.getElementById('detail-notice').textContent =
    data.notice || '기업 단위 요약입니다. AI 진단은 참고 정보이며 이탈 확률을 의미하지 않습니다.';
  renderCompanyAI(enterprise.openai_insight);
  renderCompanyChart();
  renderCompanySignals();
  renderMonthlyTable();
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
  const reasons = Array.isArray(enterprise.risk_reasons) ? enterprise.risk_reasons : [];
  const target = document.getElementById('company-signal-list');
  target.innerHTML = reasons.length
    ? reasons.map(reason => `<span class="reason-tag">${esc(reason)}</span>`).join('')
    : '<p class="empty-reasons">현재 확인할 주요 신호가 없습니다.</p>';
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
