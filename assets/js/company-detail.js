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

  document.title = `${enterprise.display_name || enterprise.enterprise_name || '기업'} - 기업 AI 분석`;
  document.getElementById('enterprise-name').textContent = enterprise.display_name || enterprise.enterprise_name || '기업명 없음';
  document.getElementById('enterprise-id').textContent = enterprise.enterprise_id;
  document.getElementById('analysis-month').textContent = data.analysis_month || '—';
  const risk = document.getElementById('enterprise-risk');
  risk.className = `level-chip ${enterprise.risk_level || 'UNKNOWN'}`;
  risk.textContent = labels[enterprise.risk_level] || labels.UNKNOWN;
  document.getElementById('approval-amount').textContent = money(enterprise.current_approval_amount);
  document.getElementById('sales-change').innerHTML = formatRate(enterprise.sales_change_rate);
  document.getElementById('count-change').innerHTML = formatRate(enterprise.transaction_change_rate);
  const baselineDrop = enterprise.sales_below_3m_pct;
  document.getElementById('sales-baseline-change').textContent = Number(enterprise.baseline_month_count || 0) < 1 ? '비교 이력 부족' : baselineDrop == null ? 'N/A' : Number(baselineDrop) > 0 ? `${Number(baselineDrop).toFixed(1)}% 낮음` : '뚜렷한 하락 없음';
  document.getElementById('detail-notice').textContent = data.notice || '기업 단위 요약입니다. AI 진단은 참고 정보이며 이탈 확률을 의미하지 않습니다.';
  renderCompanyAI(enterprise.openai_insight);
  renderCompanyChart();
  renderCompanySignals();
  renderMonthlyTable();

  if (String(enterprise.enterprise_id) === '1999') {
    document.getElementById('news-evidence-panel').hidden = false;
    renderNaverEvidence(enterprise.monthly_change_evidence, enterprise.openai_insight?.monthly_cause_hypotheses || []);
  }
}

function renderCompanyAI(insight) {
  const target = document.getElementById('company-ai-content');
  if (!insight) {
    target.innerHTML = '<p class="ai-muted">OpenAI 기업 진단이 없습니다. GitHub Actions 로그에서 OpenAI 단계를 확인하세요.</p>';
    return;
  }
  target.innerHTML = `<h3 class="ai-headline">${esc(insight.headline || '기업 단위 진단')}</h3>
    <p class="ai-summary">${esc(insight.summary || '')}</p>
    <div class="ai-columns"><div><h4>관측 결과</h4>${list(insight.observations || [])}</div><div><h4>권장 조치</h4>${list(insight.recommended_actions || [])}</div></div>
    <p class="ai-limitation">${esc(insight.limitations || 'AI 결과는 담당자가 검토한 뒤 활용하세요.')}</p>`;
}
function list(items) { return items?.length ? `<ul class="ai-list">${items.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : '<p class="ai-muted">표시할 내용이 없습니다.</p>'; }

function renderCompanyChart() {
  if (!window.Chart) return;
  const canvas = document.getElementById('company-sales-chart');
  const rows = [...(enterprise.monthly_sales || [])].sort((a, b) => String(a.month).localeCompare(String(b.month)));
  if (chart) chart.destroy();
  chart = new Chart(canvas, { type: 'line', data: { labels: rows.map(r => r.month), datasets: [{ label: '승인금액', data: rows.map(r => r.approval_amount), borderColor: '#e7ad05', backgroundColor: 'rgba(244,189,37,.15)', fill: true, tension: .3, pointRadius: 4, pointBackgroundColor: '#e7ad05' }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => money(c.raw) } }, }, scales: { y: { beginAtZero: true, ticks: { callback: v => fmt.format(v) }, grid: { color: '#eff0f3' } }, x: { grid: { display: false } } } } });
}
function renderCompanySignals() {
  const target = document.getElementById('company-signal-list');
  const reasons = enterprise.risk_reasons || [];
  if (!reasons.length) {
    target.innerHTML = '<div class="evidence-empty">현재 계산된 주요 위험 신호가 없습니다. 낮은 위험 등급이 이탈 가능성 0%를 의미하지는 않습니다.</div>';
    return;
  }
  target.innerHTML = `<div class="reason-list">${reasons.map(reason => `<span class="reason-tag">${esc(reason)}</span>`).join('')}</div>`;
}
function renderMonthlyTable() {
  const rows = [...(enterprise.monthly_sales || [])].sort((a, b) => String(a.month).localeCompare(String(b.month)));
  const indexed = new Map(rows.map(r => [String(r.month), r]));
  const tbody = document.getElementById('monthly-rows');
  if (!rows.length) { tbody.innerHTML = '<tr><td colspan="5">월별 실적이 없습니다.</td></tr>'; return; }
  tbody.innerHTML = rows.slice().reverse().map(row => {
    const previousMonth = previousMonthKey(String(row.month));
    const previous = indexed.get(previousMonth);
    const amountRate = previous ? pct(row.approval_amount, previous.approval_amount) : null;
    const countRate = previous ? pct(row.approval_count, previous.approval_count) : null;
    return `<tr><td>${esc(row.month)}</td><td>${money(row.approval_amount)}</td><td class="month-table-change">${formatRate(amountRate)}</td><td>${fmt.format(row.approval_count || 0)}</td><td>${formatRate(countRate)}</td></tr>`;
  }).join('');
}

function renderNaverEvidence(evidence, hypotheses) {
  const intro = document.getElementById('evidence-intro');
  const target = document.getElementById('monthly-evidence-list');
  if (!evidence) {
    intro.textContent = '기업관리번호 1999에 대한 Naver 검색 결과가 없습니다. Actions에서 네이버 API 설정을 확인해 주세요.';
    target.innerHTML = '';
    return;
  }
  if (evidence.status === 'credentials_missing') {
    intro.textContent = '네이버 검색 API 키가 없어 아직 검색하지 않았습니다. GitHub 저장소 Settings → Secrets and variables → Actions에 NAVER_CLIENT_ID와 NAVER_CLIENT_SECRET을 등록한 뒤 워크플로를 다시 실행하세요.';
  } else if (evidence.status === 'api_error') {
    intro.textContent = '일부 월의 뉴스 검색에 실패했습니다. API 키, 사용 가능 여부와 Actions 로그를 확인해 주세요.';
  } else {
    intro.textContent = '기업관리번호 1999(스타벅스)에 대해서만 월별 승인금액 변화와 연관될 수 있는 네이버 뉴스 결과를 검색했습니다. 기사 근거와 AI의 원인 추정을 나눠 확인하세요.';
  }

  const hypothesesByMonth = new Map((hypotheses || []).map(item => [String(item.month), item]));
  const months = [...(evidence.months || [])].sort((a, b) => String(b.month).localeCompare(String(a.month)));
  if (!months.length) { target.innerHTML = '<div class="evidence-empty">비교 가능한 월별 데이터가 없어 검색할 변동 구간이 없습니다.</div>'; return; }

  target.innerHTML = months.map(month => {
    const rate = month.change_rate_pct;
    const rateText = rate == null ? '비교 불가' : `${Number(rate) > 0 ? '+' : ''}${Number(rate).toFixed(1)}%`;
    const rateClass = month.direction === 'DOWN' ? 'down' : month.direction === 'UP' || month.direction === 'UP_FROM_ZERO' ? 'up' : '';
    const statusText = ({ found: '기사 검색 결과 있음', partial: '일부 검색 결과', no_results: '관련 결과 없음', credentials_missing: 'API 키 미설정', api_error: '검색 오류', unchanged: '전월과 동일' })[month.status] || '검색 상태 확인 필요';
    const evidenceResults = month.results || [];
    const hypotheses = hypothesesByMonth.get(String(month.month));
    let hypothesisHtml = '';
    if (month.direction === 'UNCHANGED') {
      hypothesisHtml = '<div class="evidence-empty">승인금액 변화가 없어 이 월에는 원인 추정을 수행하지 않았습니다.</div>';
    } else if (hypotheses?.candidate_reasons?.length) {
      const evidenceMap = new Map(evidenceResults.map(source => [String(source.evidence_id), source]));
      hypothesisHtml = `<div class="hypothesis-list">${hypotheses.candidate_reasons.map(reason => {
        const refs = (reason.evidence_ids || []).map(id => evidenceMap.get(String(id))).filter(Boolean);
        const sourceHtml = refs.length ? `<div class="source-ref">근거: ${refs.map(ref => `<a href="${safeUrl(ref.url)}" target="_blank" rel="noopener noreferrer">${esc(ref.title || ref.evidence_id)}</a>`).join(' · ')}</div>` : '';
        return `<div class="hypothesis-card"><div class="hypothesis-title">${esc(reason.reason)}</div><div class="hypothesis-meta">신뢰 수준: ${esc(reason.confidence || 'LOW')} · ${esc(reason.relationship || '')}</div>${sourceHtml}</div>`;
      }).join('')}</div><p class="evidence-caveat">${esc(hypotheses.limitations || '')}</p>`;
    } else {
      hypothesisHtml = `<div class="evidence-empty">검색 결과 중 이 월의 매출 변화를 설명한다고 볼 만한 근거가 충분하지 않아 원인을 제안하지 않았습니다.${hypotheses?.limitations ? ` ${esc(hypotheses.limitations)}` : ''}</div>`;
    }
    let resultsHtml = '';
    if (evidenceResults.length) {
      resultsHtml = `<div class="evidence-source-list">${evidenceResults.map(source => `<article class="evidence-source"><div class="evidence-source-title"><a href="${safeUrl(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.title || '뉴스 기사 열기')}</a></div><div class="evidence-source-meta">${esc(source.published_at || '발행일 미상')} · Naver News Search API</div><div class="evidence-source-snippet">${esc(source.snippet || '')}</div></article>`).join('')}</div>`;
    } else {
      resultsHtml = `<div class="evidence-empty">${month.status === 'credentials_missing' ? '네이버 API 키가 등록되면 이 월의 기사 근거가 표시됩니다.' : month.status === 'api_error' ? `검색 API 요청에 실패했습니다. ${esc(month.error || '')}` : '이 월의 검색어에서 표시할 뉴스 근거를 찾지 못했습니다. 검색 결과가 없다는 사실이 외부 이슈가 없었다는 뜻은 아닙니다.'}</div>`;
    }
    return `<article class="evidence-month"><div class="evidence-month-head"><div><h3 class="evidence-month-title">${esc(month.month)} 매출 변화</h3><div class="evidence-month-meta">비교 기간: ${esc(month.previous_month || '전월')} → ${esc(month.month)} · 승인금액: ${money(month.previous_approval_amount)} → ${money(month.current_approval_amount)}</div><span class="evidence-status ${esc(month.status || '')}">${statusText}</span></div><div class="evidence-rate ${rateClass}">${rateText}</div></div><div class="evidence-section-label">AI가 근거를 바탕으로 추정한 가능성</div>${hypothesisHtml}<div class="evidence-section-label">네이버 뉴스 검색 근거</div>${resultsHtml}</article>`;
  }).join('');
}

function previousMonthKey(month) { const [year, m] = month.split('-').map(Number); return m === 1 ? `${year - 1}-12` : `${year}-${String(m - 1).padStart(2, '0')}`; }
function pct(current, previous) { const c = Number(current); const p = Number(previous); if (!Number.isFinite(c) || !Number.isFinite(p) || p === 0) return null; return Math.round((c - p) / p * 1000) / 10; }
function formatRate(value) { if (value == null || !Number.isFinite(Number(value))) return 'N/A'; const n = Number(value); return `<span class="${n < 0 ? 'danger' : n > 0 ? 'positive' : ''}">${n > 0 ? '+' : ''}${n.toFixed(1)}%</span>`; }
function formatRateText(value) { if (value == null || !Number.isFinite(Number(value))) return '비교 불가'; const n = Number(value); return `${n > 0 ? '+' : ''}${n.toFixed(1)}%`; }
function safeUrl(value) { try { const url = new URL(String(value || '')); return ['https:', 'http:'].includes(url.protocol) ? esc(url.href) : '#'; } catch { return '#'; } }
function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c])); }
document.addEventListener('DOMContentLoaded', () => init().catch(error => {
  document.getElementById('detail-notice').textContent = error.message;
  document.getElementById('monthly-rows').innerHTML = `<tr><td colspan="5">${esc(error.message)}</td></tr>`;
}));
