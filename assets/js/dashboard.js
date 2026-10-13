const DATA_URL = 'public-data/dashboard_summary.json';
let dashboardData = null;
const numberFormat = new Intl.NumberFormat('ko-KR');
const moneyFormat = value => new Intl.NumberFormat('ko-KR',{maximumFractionDigits:0}).format(value ?? 0);

async function loadDashboard(){
  const response = await fetch(DATA_URL);
  if(!response.ok) throw new Error('분석 결과를 불러오지 못했습니다. GitHub Actions 실행 결과를 확인해 주세요.');
  dashboardData = await response.json();
  document.getElementById('analysis-month').textContent = `${dashboardData.analysis_month || '-'} (${dashboardData.as_of_date || '기준일 미상'})`;
  document.getElementById('merchant-count').textContent = numberFormat.format(dashboardData.summary.merchant_count || 0);
  document.getElementById('high-count').textContent = numberFormat.format(dashboardData.summary.high_risk_count || 0);
  document.getElementById('medium-count').textContent = numberFormat.format(dashboardData.summary.medium_risk_count || 0);
  document.getElementById('low-count').textContent = numberFormat.format(dashboardData.summary.low_risk_count || 0);
  const notice = document.querySelector('.notice');
  if(notice) notice.textContent = dashboardData.notice || 'AI 이상징후 탐지 기반 분석입니다. 점수는 이탈 확률이 아닙니다.';
  renderRiskBars(); renderSalesChart(); renderRows();
}
function renderRiskBars(){
  const summary = dashboardData.summary || {};
  const levels=[
    ['HIGH','고위험',summary.high_risk_count,'#d54c4c'],
    ['MEDIUM','주의',summary.medium_risk_count,'#cd8123'],
    ['LOW','정상',summary.low_risk_count,'#258563'],
    ['UNKNOWN','확인 필요',summary.unknown_count,'#858b96']
  ];
  const total=Math.max(summary.merchant_count || 0,1);
  document.getElementById('risk-bars').innerHTML=levels.map(([key,label,count,color])=>`<div class="risk-line"><div class="risk-line-top"><span>${label}</span><strong>${count||0}개</strong></div><div class="bar-track"><div class="bar-fill" style="width:${Math.max(0,Math.min(100,(count||0)/total*100))}%;background:${color}"></div></div></div>`).join('');
}
function renderSalesChart(){
  const canvas=document.getElementById('sales-chart');
  if(!window.Chart){canvas.insertAdjacentHTML('afterend','<p>차트 라이브러리를 불러오지 못했습니다.</p>');return;}
  const rows=dashboardData.monthly_sales||[];
  new Chart(canvas,{type:'line',data:{labels:rows.map(x=>x.month),datasets:[{label:'승인금액',data:rows.map(x=>x.approval_amount),borderColor:'#e7ad05',backgroundColor:'rgba(244,189,37,.15)',fill:true,tension:.32,pointRadius:4,pointBackgroundColor:'#e7ad05'}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>`${moneyFormat(c.raw)}원`}}},scales:{y:{beginAtZero:true,grid:{color:'#eff0f3'},ticks:{callback:v=>numberFormat.format(v)}},x:{grid:{display:false}}}}});
}
function renderRows(){
  if(!dashboardData) return;
  const term=(document.getElementById('search').value||'').trim().toLowerCase();
  const filter=document.getElementById('risk-filter').value;
  const rows=(dashboardData.merchants||[]).filter(m=>`${m.merchant_id||''} ${m.merchant_name||''}`.toLowerCase().includes(term)&&(filter==='ALL'||m.risk_level===filter)).sort((a,b)=>{
    const order={HIGH:0,MEDIUM:1,LOW:2,UNKNOWN:3};
    return (order[a.risk_level]??9)-(order[b.risk_level]??9) || (b.risk_score??-1)-(a.risk_score??-1);
  });
  const tbody=document.getElementById('merchant-rows');
  if(!rows.length){tbody.innerHTML='<tr><td colspan="6">조건에 맞는 가맹점이 없습니다.</td></tr>';return;}
  tbody.innerHTML=rows.map(m=>{
    const score = m.risk_score === null || m.risk_score === undefined ? '—' : `${Number(m.risk_score)||0}`;
    const label = ({HIGH:'고위험',MEDIUM:'주의',LOW:'정상',UNKNOWN:'확인 필요'})[m.risk_level]||'미분류';
    const idLabel = m.merchant_name ? `${escapeHtml(m.merchant_id)}<br><small>${escapeHtml(m.merchant_name)}</small>` : escapeHtml(m.merchant_id);
    return `<tr><td class="merchant-id">${idLabel}</td><td><strong>${score}</strong>${score==='—'?'':' / 100'}</td><td><span class="badge ${m.risk_level}">${label}</span></td><td>${formatRate(m.sales_change_rate)}</td><td>${formatRate(m.transaction_change_rate)}</td><td class="reasons" title="${escapeHtml((m.risk_reasons||[]).join(', '))}">${escapeHtml((m.risk_reasons||[]).join(' · ')||'표시할 위험 사유 없음')}</td></tr>`;
  }).join('');
}
function formatRate(value){if(value===null||value===undefined||!Number.isFinite(Number(value)))return 'N/A';const n=Number(value);return `${n>0?'+':''}${n.toFixed(1)}%`;}
function escapeHtml(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
document.addEventListener('DOMContentLoaded',()=>{document.getElementById('search').addEventListener('input',renderRows);document.getElementById('risk-filter').addEventListener('change',renderRows);loadDashboard().catch(err=>{document.getElementById('merchant-rows').innerHTML=`<tr><td colspan="6">${escapeHtml(err.message)}</td></tr>`;});});
