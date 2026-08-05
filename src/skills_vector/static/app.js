const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const workspace = $('#workspace');
const content = $('#run-content');
const loading = $('#loading');
const notice = $('#notice');
const uiState = {run: null, evidence: {cluster: '', stage: '', lens: '', status: '', offset: 0}};
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const pretty = value => String(value ?? '').replaceAll('_', ' ').replace(/\b\w/g, character => character.toUpperCase());
const phaseLabels = {scope:'Scope',research:'Research lenses',validation:'Validation',analysis:'Analysis lenses',forecast:'Forecast',draft:'Draft',approval:'Human gate'};
const phaseOrder = ['scope', 'research', 'validation', 'analysis', 'forecast', 'draft', 'approval'];

function showError(message) { notice.textContent = message; notice.classList.remove('hidden'); }
function formatDuration(value) {
  if (value === null || value === undefined) return '—';
  if (value < 1000) return `${Math.max(0, Math.round(value))} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}
async function jsonFetch(url, options = {}) {
  const response = await fetch(url, {headers:{'Content-Type':'application/json'}, ...options});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

function traceClaim(item, trace) {
  return trace?.claims?.find(claim => claim.claim_key === item.claim_key || claim.statement === item.statement);
}
function claim(item, trace) {
  const record = traceClaim(item, trace);
  const sourceCount = record?.source_count ?? item.evidence_ids?.length ?? 0;
  const findingCount = record?.finding_count ?? 0;
  const blocked = item.validation_status === 'blocked' || record?.validation_status === 'blocked';
  return `<article class="claim ${blocked ? 'claim-blocked' : ''}">
    <div class="claim-copy"><p>${escapeHtml(item.statement)}</p><div class="claim-coverage"><span>${sourceCount} source${sourceCount === 1 ? '' : 's'}</span><span>${findingCount} finding${findingCount === 1 ? '' : 's'}</span><span class="claim-state ${blocked ? 'blocked' : 'validated'}">${blocked ? 'Support incomplete' : 'Validated'}</span></div></div>
    <p class="uncertainty">Uncertainty · ${escapeHtml(item.uncertainty)}</p>
    ${item.disagreement ? `<p class="disagreement">Visible disagreement · ${escapeHtml(item.disagreement)}</p>` : ''}
    <button type="button" class="trace-link" data-cluster="${escapeHtml(item.cluster_key || record?.cluster_key || item.section || '')}">Trace support</button>
  </article>`;
}
function section(title, body, wide = false) { return `<section class="brief-section ${wide ? 'wide' : ''}"><h3>${title}</h3>${body}</section>`; }

function approvalPanel(brief, approval) {
  if (approval) return `<div class="approval-box approved-box"><p class="approved-mark">Approved by ${escapeHtml(approval.reviewer)}</p><p>${escapeHtml(approval.note || 'No note')}</p><p class="uncertainty">${escapeHtml(approval.decided_at)}</p></div>`;
  if (brief.approval_eligible === false) return `<div class="approval-box blocked-box"><p class="approval-title">Approval blocked</p><p>This partial draft remains inspectable, but ${brief.validation?.blocked_claim_keys?.length || 'one or more'} required claim${brief.validation?.blocked_claim_keys?.length === 1 ? '' : 's'} do not have validated support.</p><button type="button" class="button text evidence-jump">Review missing support</button></div>`;
  return `<div class="approval-box"><p>This ${brief.completeness === 'partial' ? 'partial ' : ''}draft is paused at the human gate. Approval is private and does not publish anything.</p><form id="approval-form" class="approval-form"><label><span>Reviewer</span><input name="reviewer" required placeholder="Reviewer name"></label><label><span>Note</span><input name="note" placeholder="Approval note (optional)"></label><button class="button primary">Approve brief</button></form></div>`;
}

function renderBrief(brief, approval, trace) {
  const failedStages = brief.validation?.failed_stages || [];
  const partial = brief.completeness === 'partial';
  return `${partial ? `<div class="partial-banner" role="status"><div><strong>Partial draft</strong><p>${brief.approval_eligible ? 'Required claims remain validated, but some worker output is missing.' : 'Required support is missing, so this draft cannot be approved.'}</p></div><span>${failedStages.length} affected stage${failedStages.length === 1 ? '' : 's'}</span></div>` : ''}
  <div class="brief-grid">
    ${section('Role context', `<p>${escapeHtml(brief.role_context)}</p><p class="uncertainty">${escapeHtml(brief.geography)} · As of ${escapeHtml(brief.as_of)} · Private</p>`)}
    ${section('What is changing', claim(brief.what_is_changing, trace))}
    ${section('Task shifts', brief.task_shifts.map(item => claim(item, trace)).join(''))}
    ${section('Skill shifts', brief.skill_shifts.map(item => claim(item, trace)).join(''))}
    ${section('Durable & meta capabilities', brief.durable_capabilities.map(item => claim(item, trace)).join(''), true)}
    ${section('Two-year scenario', claim(brief.scenario, trace) + `<p><strong>${escapeHtml(brief.scenario.horizon_start)} → ${escapeHtml(brief.scenario.horizon_end)}</strong></p>`, true)}
    ${section('Counter-evidence & disagreement', brief.counter_evidence.map(item => claim(item, trace)).join(''), true)}
    ${section('Overall uncertainty', `<p>${escapeHtml(brief.uncertainty_summary)}</p>`, true)}
    ${section('Evidence coverage', `<div class="coverage-overview"><p><strong>${trace?.summary?.source_count || brief.sources.length} source snapshots</strong> support ${trace?.summary?.claim_count || 0} material claims across the controlled research and analysis lenses.</p><button type="button" class="button text evidence-jump">Open evidence explorer</button></div>`, true)}
    ${section('Private approval', approvalPanel(brief, approval), true)}
  </div>`;
}

function traceSummary(trace) {
  const summary = trace?.summary || {};
  return `<div class="trace-summary" aria-label="Run trace summary">
    <div><strong>${summary.stage_count ?? 0}</strong><span>logical stages</span></div>
    <div><strong>${summary.invocation_count ?? 0}</strong><span>invocations</span></div>
    <div><strong>${summary.finding_count ?? 0}</strong><span>findings</span></div>
    <div><strong>${summary.claim_count ?? 0}</strong><span>claims</span></div>
    <div><strong>${summary.source_count ?? 0}</strong><span>sources</span></div>
    <div class="${summary.failure_count ? 'has-failure' : ''}"><strong>${summary.failure_count ?? 0}</strong><span>failures</span></div>
  </div>`;
}

function stageNode(stage) {
  return `<button type="button" class="graph-node status-${escapeHtml(stage.status)}" data-stage="${escapeHtml(stage.stage_key)}" aria-label="Inspect ${escapeHtml(stage.label)}" aria-pressed="false">
    <span class="node-status">${pretty(stage.status)}</span><strong>${escapeHtml(stage.label)}</strong>
    <span class="node-metrics">${stage.invocation_count} invocation${stage.invocation_count === 1 ? '' : 's'} · ${stage.source_count} source${stage.source_count === 1 ? '' : 's'} · ${formatDuration(stage.duration_ms)}</span>
  </button>`;
}
function renderGraph(trace) {
  if (!trace?.stages?.length) return '<div class="empty-state"><h3>No normalized trace</h3><p>This legacy or failed run has no stage-level trace data.</p></div>';
  const phases = phaseOrder.map(phase => {
    const stages = trace.stages.filter(stage => stage.phase === phase);
    return `<section class="graph-phase phase-${phase} ${['research','analysis'].includes(phase) ? 'phase-fan' : ''}"><h3>${phaseLabels[phase]}</h3><div class="phase-nodes">${stages.map(stageNode).join('')}</div></section>`;
  }).join('');
  return `<div class="graph-intro"><div><h3>Investigation graph</h3><p>Each node is a logical stage. Open a node to inspect the deterministic worker invocation and its structured finding.</p></div><span class="graph-legend"><i></i> Awaiting human action</span></div><div class="workflow-graph">${phases}</div><div id="stage-detail" class="stage-detail"><p>Select a stage to inspect its invocation, finding, timing, and raw payload.</p></div>`;
}

function renderStageDetail(stage) {
  const invocations = stage.invocations || [];
  const findings = stage.findings || [];
  return `<div class="detail-head"><div><span class="node-status">${pretty(stage.status)}</span><h3>${escapeHtml(stage.label)}</h3></div><button type="button" class="detail-close" aria-label="Close stage detail">Close</button></div>
    <div class="detail-metrics"><span>${stage.invocation_count} invocation${stage.invocation_count === 1 ? '' : 's'}</span><span>${stage.finding_count} finding${stage.finding_count === 1 ? '' : 's'}</span><span>${stage.source_count} source${stage.source_count === 1 ? '' : 's'}</span><span>${formatDuration(stage.duration_ms)}</span></div>
    <p class="detail-summary">${escapeHtml(stage.summary)}</p>
    ${stage.failure_count ? `<p class="failure-copy">${escapeHtml(invocations.find(item => item.error)?.error || 'This stage did not complete successfully.')}</p>` : ''}
    <div class="detail-columns"><section><h4>Findings</h4>${findings.length ? findings.map(finding => `<article class="finding"><span>${pretty(finding.finding_type)}</span><p>${escapeHtml(finding.summary)}</p>${finding.uncertainty ? `<small>${escapeHtml(finding.uncertainty)}</small>` : ''}</article>`).join('') : '<p class="muted-copy">This gate has no worker-produced finding.</p>'}</section>
    <section><h4>Invocations</h4>${invocations.length ? invocations.map(invocation => `<article class="invocation"><div><strong>${escapeHtml(invocation.worker_key)}</strong><span class="status ${escapeHtml(invocation.status)}">${pretty(invocation.status)}</span></div><p>${escapeHtml(invocation.summary)}</p><small>${formatDuration(invocation.duration_ms)} · attempt ${invocation.attempt}</small><details class="raw-output" data-invocation="${escapeHtml(invocation.id)}"><summary>Expand raw payload</summary><pre>Payload loads on demand.</pre></details></article>`).join('') : '<p class="muted-copy">Human action completes this stage.</p>'}</section></div>`;
}

function option(value, label, selected) { return `<option value="${escapeHtml(value)}" ${value === selected ? 'selected' : ''}>${escapeHtml(label)}</option>`; }
function renderEvidence(data) {
  const filters = uiState.evidence;
  const active = data.clusters.find(cluster => cluster.cluster_key === data.active_cluster);
  const clusters = data.clusters.map(cluster => `<button type="button" class="cluster-row ${cluster.cluster_key === data.active_cluster ? 'active' : ''}" data-cluster="${escapeHtml(cluster.cluster_key)}" aria-pressed="${cluster.cluster_key === data.active_cluster}"><span><strong>${escapeHtml(cluster.label)}</strong><small>${cluster.claim_count} claims · ${cluster.finding_count} findings</small></span><span><b>${cluster.source_count}</b> sources</span><i class="cluster-state ${cluster.status}">${pretty(cluster.status)}</i></button>`).join('');
  const sources = data.sources.map(source => `<article class="evidence-source"><div class="source-rank"><span>${pretty(source.category)}</span><div class="reason-chips">${source.rank_reasons.map(reason => `<span>${escapeHtml(reason.label)}</span>`).join('')}</div></div><h4><a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(source.title)} <span aria-hidden="true">↗</span></a></h4><p class="source-byline">${escapeHtml(source.publisher)} · ${escapeHtml(source.published_on)}</p><p>${escapeHtml(source.relevant_excerpt)}</p><details><summary>Provenance and connected findings</summary><p class="provenance">${escapeHtml(source.provenance)}</p>${source.findings.map(finding => `<article class="source-finding"><strong>${escapeHtml(finding.stage_label)}</strong><p>${escapeHtml(finding.summary)}</p><details class="raw-output" data-invocation="${escapeHtml(finding.invocation_id)}"><summary>Expand worker payload</summary><pre>Payload loads on demand.</pre></details></article>`).join('')}</details></article>`).join('');
  const previousDisabled = data.offset === 0 ? 'disabled' : '';
  const nextDisabled = data.offset + data.limit >= data.total ? 'disabled' : '';
  return `<div class="evidence-layout"><aside class="cluster-list"><div><h3>Evidence clusters</h3><p>Start with the claim family, then move through findings to individual sources.</p></div>${clusters}</aside><section class="evidence-results"><div class="evidence-toolbar"><label class="cluster-select-control">Cluster<select id="evidence-cluster">${data.clusters.map(item => option(item.cluster_key, item.label, data.active_cluster)).join('')}</select></label><label>Lens<select id="evidence-lens">${option('', 'All lenses', filters.lens)}${data.filters.lenses.map(item => option(item, pretty(item), filters.lens)).join('')}</select></label><label>Stage<select id="evidence-stage">${option('', 'All stages', filters.stage)}${data.filters.stages.map(item => option(item.stage_key, item.label, filters.stage)).join('')}</select></label><label>Status<select id="evidence-status">${option('', 'All statuses', filters.status)}${data.filters.statuses.map(item => option(item, pretty(item), filters.status)).join('')}</select></label></div><div id="evidence-active-heading" class="evidence-heading" tabindex="-1" aria-live="polite"><div><span>${active ? escapeHtml(active.label) : 'Evidence'}</span><h3>${data.total} ranked source${data.total === 1 ? '' : 's'}</h3></div><p>Reason chips explain placement; counter-evidence and limitations remain visible.</p></div><div class="source-list">${sources || '<div class="empty-state"><h3>No matching sources</h3><p>Clear a filter or choose another evidence cluster.</p></div>'}</div><div class="pager"><button type="button" class="button text page-previous" ${previousDisabled}>Previous</button><span>${data.total ? `${data.offset + 1}–${Math.min(data.offset + data.limit, data.total)} of ${data.total}` : '0 sources'}</span><button type="button" class="button text page-next" ${nextDisabled}>Next</button></div></section></div>`;
}

function evidenceSkeleton() { return '<div class="evidence-skeleton"><span></span><span></span><span></span><p>Organizing evidence by claim, finding, and source…</p></div>'; }
async function loadEvidence(overrides = {}) {
  if (!uiState.run) return;
  const {focus = false, ...filterOverrides} = overrides;
  uiState.evidence = {...uiState.evidence, ...filterOverrides};
  const panel = $('[data-panel="evidence"]', content);
  panel.innerHTML = evidenceSkeleton();
  const query = new URLSearchParams({limit:'25', offset:String(uiState.evidence.offset || 0)});
  Object.entries(uiState.evidence).forEach(([key, value]) => { if (value && key !== 'offset') query.set(key === 'cluster' ? 'cluster' : key, value); });
  try {
    const data = await jsonFetch(`/api/runs/${uiState.run.id}/evidence?${query}`);
    uiState.evidence.cluster = data.active_cluster || '';
    panel.innerHTML = renderEvidence(data);
    wireEvidence(data);
    if (focus) {
      const heading = $('#evidence-active-heading', panel);
      heading.focus({preventScroll:true});
      heading.scrollIntoView({behavior:'smooth', block:'start'});
    }
  } catch (error) {
    panel.innerHTML = `<div class="empty-state"><h3>Evidence could not load</h3><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function activateTab(name, evidenceOverrides = null) {
  $$('.tab', content).forEach(tab => {
    const selected = tab.dataset.tab === name;
    tab.classList.toggle('active', selected);
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  $$('.tab-panel', content).forEach(panel => {
    const selected = panel.dataset.panel === name;
    panel.classList.toggle('hidden', !selected);
    panel.hidden = !selected;
  });
  if (name === 'evidence' && (evidenceOverrides || !$('[data-panel="evidence"] .evidence-layout', content))) loadEvidence(evidenceOverrides || {});
}

async function loadRawPayload(details) {
  if (!details.open || details.dataset.loaded || !uiState.run) return;
  const output = $('pre', details);
  output.textContent = 'Loading payload…';
  try {
    const invocation = await jsonFetch(`/api/runs/${uiState.run.id}/invocations/${details.dataset.invocation}`);
    output.textContent = JSON.stringify(invocation.raw_payload, null, 2);
    details.dataset.loaded = 'true';
  } catch (error) {
    output.textContent = `Payload unavailable: ${error.message}`;
  }
}
function wireRawPayloads(root) { $$('.raw-output', root).forEach(details => details.addEventListener('toggle', () => loadRawPayload(details))); }

function wireEvidence(data) {
  const panel = $('[data-panel="evidence"]', content);
  $$('.cluster-row', panel).forEach(button => button.addEventListener('click', () => loadEvidence({cluster:button.dataset.cluster, offset:0})));
  $('#evidence-cluster', panel).addEventListener('change', event => loadEvidence({cluster:event.target.value, offset:0, focus:true}));
  $('#evidence-lens', panel).addEventListener('change', event => loadEvidence({lens:event.target.value, offset:0}));
  $('#evidence-stage', panel).addEventListener('change', event => loadEvidence({stage:event.target.value, offset:0}));
  $('#evidence-status', panel).addEventListener('change', event => loadEvidence({status:event.target.value, offset:0}));
  $('.page-previous', panel).addEventListener('click', () => loadEvidence({offset:Math.max(0, data.offset - data.limit)}));
  $('.page-next', panel).addEventListener('click', () => loadEvidence({offset:data.offset + data.limit}));
  wireRawPayloads(panel);
}

function renderRun(run) {
  uiState.run = run;
  uiState.evidence = {cluster:'', stage:'', lens:'', status:'', offset:0};
  $('#run-title').textContent = `${run.role_name} · Run`;
  const brief = run.brief;
  const trace = run.trace;
  content.innerHTML = `<div class="run-summary"><div><span class="status ${escapeHtml(run.status)}">${pretty(run.status)}</span> <span class="run-id">${escapeHtml(run.id)}</span></div><p class="run-id">Started ${escapeHtml(run.started_at)}</p></div>${traceSummary(trace)}
    <div class="tabs" role="tablist" aria-label="Run views"><button id="tab-brief" class="tab active" data-tab="brief" role="tab" aria-selected="true" aria-controls="panel-brief" tabindex="0">Brief & approval</button><button id="tab-graph" class="tab" data-tab="graph" role="tab" aria-selected="false" aria-controls="panel-graph" tabindex="-1">Workflow graph</button><button id="tab-evidence" class="tab" data-tab="evidence" role="tab" aria-selected="false" aria-controls="panel-evidence" tabindex="-1">Evidence explorer</button></div>
    <div id="panel-brief" class="tab-panel" data-panel="brief" role="tabpanel" aria-labelledby="tab-brief">${brief ? renderBrief(brief, run.approval, trace) : '<div class="empty-state"><h3>No draft was produced</h3><p>Inspect the workflow graph for the failure boundary.</p></div>'}</div>
    <div id="panel-graph" class="tab-panel hidden" data-panel="graph" role="tabpanel" aria-labelledby="tab-graph" hidden>${renderGraph(trace)}</div>
    <div id="panel-evidence" class="tab-panel hidden" data-panel="evidence" role="tabpanel" aria-labelledby="tab-evidence" hidden>${evidenceSkeleton()}</div>`;
  const tabs = $$('.tab', content);
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activateTab(tab.dataset.tab));
    tab.addEventListener('keydown', event => {
      const keys = ['ArrowRight', 'ArrowLeft', 'Home', 'End'];
      if (!keys.includes(event.key)) return;
      event.preventDefault();
      const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 :
        (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      tabs[nextIndex].focus();
      activateTab(tabs[nextIndex].dataset.tab);
    });
  });
  $$('.graph-node', content).forEach(button => button.addEventListener('click', () => {
    $$('.graph-node', content).forEach(node => {
      const selected = node === button;
      node.classList.toggle('selected', selected);
      node.setAttribute('aria-pressed', String(selected));
    });
    const stage = trace.stages.find(item => item.stage_key === button.dataset.stage);
    const detail = $('#stage-detail', content); detail.innerHTML = renderStageDetail(stage); detail.scrollIntoView({behavior:'smooth', block:'nearest'});
    $('.detail-close', detail).addEventListener('click', () => {
      detail.innerHTML = '<p>Select a stage to inspect its invocation, finding, timing, and raw payload.</p>';
      button.classList.remove('selected');
      button.setAttribute('aria-pressed', 'false');
      button.focus();
    });
    wireRawPayloads(detail);
  }));
  $$('.trace-link', content).forEach(button => button.addEventListener('click', () => activateTab('evidence', {cluster:button.dataset.cluster, offset:0, focus:true})));
  $$('.evidence-jump', content).forEach(button => button.addEventListener('click', () => activateTab('evidence')));
  const form = $('#approval-form', content);
  if (form) form.addEventListener('submit', async event => {
    event.preventDefault(); const button = $('button', form); button.disabled = true;
    try { const data = Object.fromEntries(new FormData(form)); renderRun(await jsonFetch(`/api/runs/${run.id}/approve`, {method:'POST',body:JSON.stringify(data)})); await refreshCards(); }
    catch (error) { showError(error.message); button.disabled = false; }
  });
}

async function openRun(url, options) {
  workspace.classList.remove('hidden'); workspace.scrollIntoView({behavior:'smooth'}); notice.classList.add('hidden'); content.innerHTML=''; loading.classList.remove('hidden');
  try { renderRun(await jsonFetch(url, options)); if (options?.method === 'POST') await refreshCards(); }
  catch (error) { showError(error.message); }
  finally { loading.classList.add('hidden'); }
}

function wireButtons() {
  $$('.investigate').forEach(button => button.onclick = () => openRun(`/api/roles/${button.dataset.role}/investigations`, {method:'POST'}));
  $$('.inspect').forEach(button => button.onclick = () => openRun(`/api/runs/${button.dataset.run}`));
}
async function refreshCards() {
  const roles = await jsonFetch('/api/roles');
  roles.forEach(role => {
    const card = $(`.role-card[data-role="${role.slug}"]`); if (!card) return;
    const status=$('.status',card); status.className=`status ${role.latest_status||'new'}`; status.textContent=pretty(role.latest_status||'not investigated');
    $('.run-count',card).textContent = `${role.run_count} run${role.run_count === 1 ? '' : 's'}`;
    let inspect = $('.inspect', card);
    if (role.latest_run_id && !inspect) { inspect = document.createElement('button'); inspect.className='button text inspect'; inspect.textContent='Inspect latest'; $('.card-actions',card).append(inspect); }
    if (inspect) inspect.dataset.run = role.latest_run_id;
  });
  wireButtons();
}
$('#close-workspace').addEventListener('click', () => workspace.classList.add('hidden'));
wireButtons();
