const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const workspace = $('#workspace');
const content = $('#run-content');
const loading = $('#loading');
const notice = $('#notice');
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const pretty = value => String(value).replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());

function showError(message) { notice.textContent = message; notice.classList.remove('hidden'); }
async function jsonFetch(url, options = {}) {
  const response = await fetch(url, {headers:{'Content-Type':'application/json'}, ...options});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

function evidenceTags(ids = []) { return `<div class="evidence-tags">${ids.map(id => `<span>${escapeHtml(id)}</span>`).join('')}</div>`; }
function claim(item) { return `<div class="claim"><p>${escapeHtml(item.statement)}</p>${evidenceTags(item.evidence_ids)}<p class="uncertainty">Uncertainty · ${escapeHtml(item.uncertainty)}</p>${item.disagreement ? `<p class="disagreement">Visible disagreement · ${escapeHtml(item.disagreement)}</p>` : ''}</div>`; }
function section(title, body, wide = false) { return `<section class="brief-section ${wide ? 'wide' : ''}"><h3>${title}</h3>${body}</section>`; }

function renderBrief(brief, approval) {
  return `<div class="brief-grid">
    ${section('Role context', `<p>${escapeHtml(brief.role_context)}</p><p class="uncertainty">${escapeHtml(brief.geography)} · As of ${escapeHtml(brief.as_of)} · Private</p>`)}
    ${section('What is changing', claim(brief.what_is_changing))}
    ${section('Task shifts', brief.task_shifts.map(claim).join(''))}
    ${section('Skill shifts', brief.skill_shifts.map(claim).join(''))}
    ${section('Durable & meta capabilities', brief.durable_capabilities.map(claim).join(''), true)}
    ${section('Two-year scenario', claim(brief.scenario) + `<p><strong>${escapeHtml(brief.scenario.horizon_start)} → ${escapeHtml(brief.scenario.horizon_end)}</strong></p>`, true)}
    ${section('Counter-evidence & disagreement', brief.counter_evidence.map(claim).join(''), true)}
    ${section('Overall uncertainty', `<p>${escapeHtml(brief.uncertainty_summary)}</p>`, true)}
    ${section('Sources', `<div class="source-list">${brief.sources.map(source => `<article class="source"><p class="source-meta">${pretty(source.category)} · ${escapeHtml(source.publisher)} · ${escapeHtml(source.published_on)}</p><a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(source.title)} ↗</a><p>${escapeHtml(source.relevant_excerpt)}</p><p class="provenance">Provenance · ${escapeHtml(source.provenance)}</p><p class="provenance">Role connection · ${pretty(source.role_connection)}</p></article>`).join('')}</div>`, true)}
    ${section('Private approval', approval ? `<div class="approval-box"><p class="approved-mark">Approved by ${escapeHtml(approval.reviewer)}</p><p>${escapeHtml(approval.note || 'No note')}</p><p class="uncertainty">${escapeHtml(approval.decided_at)}</p></div>` : `<div class="approval-box"><p>This draft is paused at the human gate. Approval is private and does not publish anything.</p><form id="approval-form" class="approval-form"><input name="reviewer" required placeholder="Reviewer name"><input name="note" placeholder="Approval note (optional)"><button class="button primary">Approve brief</button></form></div>`, true)}
  </div>`;
}

function renderRun(run) {
  $('#run-title').textContent = `${run.role_name} · Run`;
  const stages = run.artifacts.map((item, index) => `<article class="stage"><span class="stage-number">${String(index + 1).padStart(2,'0')}</span><div><span class="kind">${pretty(item.kind)}</span><h4>${pretty(item.stage)}</h4></div><div><p>${escapeHtml(item.payload.finding || item.payload.consensus || item.payload.status || item.payload.required_action || 'Artifact persisted')}</p></div></article>`).join('');
  content.innerHTML = `<div class="run-summary"><div><span class="status ${run.status}">${pretty(run.status)}</span> <span class="run-id">${escapeHtml(run.id)}</span></div><p class="run-id">Started ${escapeHtml(run.started_at)}</p></div>
    <div class="tabs"><button class="tab active" data-tab="brief">Role brief</button><button class="tab" data-tab="stages">Workflow stages (${run.artifacts.length})</button></div>
    <div class="tab-panel" data-panel="brief">${renderBrief(run.brief, run.approval)}</div><div class="tab-panel hidden" data-panel="stages"><div class="stage-list">${stages}</div></div>`;
  $$('.tab', content).forEach(tab => tab.addEventListener('click', () => { $$('.tab',content).forEach(t=>t.classList.toggle('active',t===tab)); $$('.tab-panel',content).forEach(panel=>panel.classList.toggle('hidden',panel.dataset.panel!==tab.dataset.tab)); }));
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
