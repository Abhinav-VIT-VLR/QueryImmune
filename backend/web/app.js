(() => {
  const $ = (id) => document.getElementById(id);
  const tokenKey = 'queryimmune_api_token';
  const tokenDialog = $('token-dialog');
  const token = () => sessionStorage.getItem(tokenKey) || '';
  let demoMode = false;
  let selectedScenario = null;
  let demoConfigurationReady = Promise.resolve();
  let toastTimer;

  function toast(message) {
    const node = $('toast'); node.textContent = message; node.classList.add('show');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => node.classList.remove('show'), 2800);
  }
  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (token()) headers.Authorization = `Bearer ${token()}`;
    if (options.body) headers['Content-Type'] = 'application/json';
    let response;
    try { response = await fetch(path, { ...options, headers }); }
    catch { throw new Error('Request failed. Check your connection and try again.'); }
    // Read the body before interpreting the status: BLOCK and REVIEW are valid
    // security decisions even though their HTTP statuses are not 2xx success.
    const body = await response.json().catch(() => null);
    const decisionBody = path === '/api/v1/nl-requests' ? structuredDecisionForStatus(body, response.status) : null;
    if (decisionBody) {
      return decisionBody;
    }
    if (!response.ok) {
      const detail = body?.error?.message || body?.detail || body?.message;
      const message = errorMessage(detail);
      if (response.status === 401) throw new Error(message || 'Authentication failed. Check your API token.');
      if (response.status === 403) throw new Error(message || 'You are not authorized to submit this request.');
      if (response.status === 422) throw new Error(message || 'The request failed validation.');
      if (response.status >= 500) throw new Error(message || `Server error (${response.status}). Please try again.`);
      throw new Error(message || `Request failed (${response.status})`);
    }
    if (body === null) throw new Error('The server returned an unreadable response.');
    return body;
  }
  function errorMessage(detail) {
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map(item => item?.msg || item?.message || String(item)).join('; ');
    if (detail && typeof detail === 'object') return detail.message || detail.msg || '';
    return '';
  }
  function structuredDecisionForStatus(body, status) {
    if (!body || typeof body !== 'object' || !Array.isArray(body.reason_codes)) return null;
    const decision = String(body.decision || '').toLowerCase();
    const expected = { 200: 'allow', 202: 'review', 403: 'block' }[status];
    if (!expected || decision !== expected) return null;
    return { ...body, decision };
  }
  function setDb(status) {
    const ready = status === 'ok';
    $('db-status').textContent = ready ? 'PostgreSQL ready' : 'Database unavailable';
    $('db-dot').parentElement.classList.toggle('online', ready);
    $('db-dot').parentElement.classList.toggle('offline', !ready);
    document.querySelector('.heading-badge').innerHTML = `<span>${ready ? '●' : '●'}</span> ${ready ? 'PostgreSQL connected' : 'Database unavailable'}`;
  }
  async function health() {
    try { setDb((await api('/health/ready')).status); }
    catch { setDb('unavailable'); }
  }
  async function demoConfiguration() {
    try { demoMode = Boolean((await api('/api/v1/demo/config')).enabled); }
    catch { demoMode = false; }
    $('demo-mode').hidden = !demoMode;
  }
  function showDecision(result) {
    $('decision-section').hidden = false; $('analysis-section').hidden = false;
    const decision = result.decision || 'block';
    const card = $('decision-card'); card.classList.remove('medium', 'high');
    if (decision !== 'allow') card.classList.add(decision === 'review' ? 'medium' : 'high');
    $('decision-title').textContent = decision.toUpperCase();
    $('decision-icon').textContent = decision === 'allow' ? '✓' : decision === 'review' ? '!' : '×';
    $('decision-kicker').textContent = decision === 'allow' ? 'REQUEST CLEARED' : decision === 'review' ? 'HUMAN REVIEW REQUIRED' : 'REQUEST BLOCKED';
    $('decision-message').textContent = result.message || 'Decision recorded by QueryImmune policy.';
    const score = Number.isFinite(result.risk_score) ? result.risk_score : null;
    $('risk-score').textContent = score === null ? '—' : `${score.toFixed(1)} / 100`;
    const fill = $('risk-fill'); fill.style.width = `${score === null ? 0 : Math.max(0, Math.min(100, score))}%`;
    fill.className = score >= 60 ? 'high' : score >= 25 ? 'medium' : '';
    const reasons = result.reason_codes || [];
    const reasonBox = $('decision-reasons'); reasonBox.replaceChildren();
    if (!reasons.length) addChip(reasonBox, 'No additional risk reasons');
    else reasons.forEach(code => addChip(reasonBox, code));
    const signals = result.signals || result.risk_analysis?.signals || [];
    if (Array.isArray(signals)) signals.forEach(signal => addChip(reasonBox, typeof signal === 'string' ? signal : signal.name || signal.code || JSON.stringify(signal)));
    else if (signals && typeof signals === 'object') Object.entries(signals).forEach(([name, value]) => addChip(reasonBox, `${name}: ${value}`));
    if (decision === 'review' || decision === 'block') {
      $('decision-message').textContent = `${result.message || 'Decision recorded by QueryImmune policy.'} ${decision === 'review' ? 'Human review required; this request was not executed.' : 'This request was not executed.'}`;
    }

    const intent = result.intent || {};
    $('intent-operation').textContent = intent.operation || '—';
    $('intent-action').textContent = intent.action || '—';
    $('intent-scope').textContent = intent.scope || '—';
    $('intent-tables').textContent = (intent.tables || []).join(', ') || '—';
    const sensitivity = (intent.sensitivity_expectations || ['standard']).join(' · ').toUpperCase();
    $('sensitivity-tag').textContent = sensitivity;
    $('sensitivity-tag').style.color = sensitivity.includes('RESTRICTED') ? '#b24d4d' : sensitivity.includes('SENSITIVE') ? '#a67422' : '#477b67';
    $('sql-output').textContent = result.generated_sql || 'No SQL candidate was generated for this request.';

    const risk = result.risk_analysis || {};
    const behavior = risk.behavior || {};
    const unavailable = reasons.includes('BEHAVIOR_CONTEXT_UNAVAILABLE');
    $('behavior-state').textContent = unavailable ? 'Behavior context unavailable' : behavior.similarity == null ? 'Baseline warm-up' : `${Math.round((behavior.similarity || 0) * 100)}% workload similarity`;
    $('drift-tag').textContent = behavior.drift ? `${(behavior.drift_kind || 'detected').toUpperCase()} DRIFT` : 'NO DRIFT';
    $('drift-tag').style.color = behavior.drift ? '#a67422' : '#477b67';
    $('profile-confidence').textContent = `PROFILE ${behavior.sample_count ?? '—'} SAMPLES`;
    $('drift-title').textContent = behavior.drift ? `${behavior.drift_kind || 'Workload'} drift detected` : 'Workload is within observed context';
    $('drift-description').textContent = behavior.drift ? 'This pattern is recorded for review; profile changes follow the controlled update gate.' : 'Comparison uses the actor profile and recent PostgreSQL audit history.';
    renderFactors(risk.risk_factors || {});
    $('results-section').hidden = !(decision === 'allow' && result.results);
    if (!$('results-section').hidden) renderResults(result.results || []);
    if (decision === 'allow') $('result-summary').textContent = `${result.row_count ?? result.results?.length ?? 0} rows · ${result.execution_status || 'complete'}`;
    $('decision-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function addChip(parent, text) {
    const span = document.createElement('span'); span.className = 'reason-chip'; span.textContent = text; parent.append(span);
  }
  function renderFactors(factors) {
    const host = $('factor-list'); host.replaceChildren();
    const entries = Object.entries(factors);
    if (!entries.length) { host.innerHTML = '<div class="empty-state">No factor breakdown was returned for this decision.</div>'; return; }
    entries.forEach(([name, value]) => {
      const row = document.createElement('div'); row.className = 'factor-row';
      const label = document.createElement('span'); label.textContent = name.replaceAll('_', ' ');
      const bar = document.createElement('div'); bar.className = 'factor-bar';
      const inner = document.createElement('i'); inner.style.width = `${Math.max(0, Math.min(100, Number(value) || 0))}%`; bar.append(inner);
      const number = document.createElement('strong'); number.textContent = Number(value).toFixed(1);
      row.append(label, bar, number); host.append(row);
    });
  }
  function renderResults(rows) {
    const host = $('result-content'); host.replaceChildren();
    if (!rows.length) { host.textContent = 'The approved query returned no rows.'; return; }
    const table = document.createElement('table'); table.className = 'result-table';
    const keys = [...new Set(rows.flatMap(row => Object.keys(row)))];
    const thead = document.createElement('thead'), header = document.createElement('tr');
    keys.forEach(key => { const th = document.createElement('th'); th.textContent = key.replaceAll('_', ' ').toUpperCase(); header.append(th); });
    thead.append(header); table.append(thead);
    const tbody = document.createElement('tbody');
    rows.forEach(row => { const tr = document.createElement('tr'); keys.forEach(key => { const td = document.createElement('td'); td.textContent = row[key] == null ? '—' : String(row[key]); tr.append(td); }); tbody.append(tr); });
    table.append(tbody); host.append(table);
  }
  function renderHistory(items) {
    const body = $('history-body'); body.replaceChildren();
    if (!items?.length) { const tr = document.createElement('tr'), td = document.createElement('td'); td.colSpan = 7; td.className = 'empty-state'; td.textContent = 'No persisted request events found for this actor.'; tr.append(td); body.append(tr); return; }
    items.slice(0, 20).forEach(item => {
      const tr = document.createElement('tr');
      const cells = [item.request_id?.slice(0, 8) || '—', formatTime(item.created_at), item.action || '—'];
      cells.forEach((value, index) => { const td = document.createElement('td'); td.textContent = value; if (index === 0) td.className = 'mono'; tr.append(td); });
      const decision = document.createElement('td'), pill = document.createElement('span'); pill.className = `decision-pill ${item.decision}`; pill.textContent = (item.decision || 'unknown').toUpperCase(); decision.append(pill); tr.append(decision);
      const risk = document.createElement('td'); risk.textContent = Number(item.risk_score || 0).toFixed(1); tr.append(risk);
      const result = document.createElement('td'); result.textContent = item.execution_status === 'succeeded' ? `${item.row_count ?? 0} rows` : item.execution_status; tr.append(result);
      const reasons = document.createElement('td'); reasons.textContent = (item.reason_codes || []).join(', ') || '—'; tr.append(reasons); body.append(tr);
    });
  }
  function formatTime(value) { if (!value) return '—'; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
  function renderWorkload(items) {
    const host = $('workload-chart'); host.replaceChildren();
    if (!items?.length) { host.innerHTML = '<div class="empty-state">No workload events in the last 30 days.</div>'; return; }
    const daily = new Map(); items.forEach(item => { const old = daily.get(item.day) || { count: 0, risk: 0, n: 0 }; old.count += item.requests; old.risk += item.mean_risk * item.requests; old.n += item.requests; daily.set(item.day, old); });
    const entries = [...daily.entries()].slice(-30); const max = Math.max(1, ...entries.map(([, v]) => v.count));
    entries.forEach(([day, value]) => { const wrap = document.createElement('div'); wrap.className = 'chart-day'; wrap.title = `${day}: ${value.count} requests · mean risk ${(value.risk / value.n).toFixed(1)}`; const bar = document.createElement('div'); bar.className = 'chart-bar'; bar.style.height = `${Math.max(3, value.count / max * 100)}%`; const label = document.createElement('small'); label.textContent = day.slice(8); wrap.append(bar, label); host.append(wrap); });
  }
  function renderProfile(profile) {
    const host = $('profile-content'); host.replaceChildren();
    if (!profile) { $('profile-status').textContent = 'NO PROFILE'; host.innerHTML = '<div class="empty-state">No baseline profile is stored for this actor yet.</div>'; return; }
    $('profile-status').textContent = profile.update_status.toUpperCase(); $('profile-status').className = `status-pill ${profile.update_status}`;
    const summary = profile.feature_summary || {};
    const stats = document.createElement('div'); stats.className = 'profile-stats';
    [["VERSION", `v${profile.profile_version}`], ["APPROVED SAMPLES", profile.approved_sample_count], ["CONFIDENCE", `${Math.round(profile.confidence * 100)}%`]].forEach(([label, value]) => { const box = document.createElement('div'); box.className = 'profile-stat'; const l = document.createElement('label'); l.textContent = label; const b = document.createElement('b'); b.textContent = String(value); box.append(l, b); stats.append(box); });
    host.append(stats); const tags = document.createElement('div'); tags.className = 'profile-tags';
    for (const [label, key] of [['Actions', 'actions'], ['Tables', 'tables'], ['Scopes', 'scopes']]) (summary[key] || summary.common_entities || []).slice(0, 5).forEach(value => { const tag = document.createElement('span'); tag.textContent = `${label}: ${value}`; tags.append(tag); });
    if (tags.childNodes.length) host.append(tags);
  }
  async function refreshDashboard() {
    if (!token()) { $('identity-status').textContent = 'Token required'; return; }
    try {
      const data = await api('/api/v1/dashboard');
      $('identity-status').textContent = 'Connected · authenticated';
      $('auth-hint').textContent = 'Connected to the authenticated API. Decisions, history, and profiles are sourced from PostgreSQL.';
      renderHistory(data.history); renderWorkload(data.workload); renderProfile(data.behavior_profile);
    } catch (error) {
      $('identity-status').textContent = 'Authentication unavailable';
      $('auth-hint').textContent = error.message;
      toast(error.message);
    }
  }
  async function runQuery() {
    await demoConfigurationReady;
    const prompt = $('query-input').value.trim();
    if (prompt.length < 3) { toast('Enter a natural-language request first.'); return; }
    if (!token()) { tokenDialog.showModal(); toast('Connect an API token before submitting.'); return; }
    const button = $('run-button'); button.disabled = true; button.querySelector('span').textContent = 'Evaluating request…';
    try {
      const payload = { request: prompt };
      if (demoMode && selectedScenario) payload.demo_scenario = selectedScenario;
      const result = await api('/api/v1/nl-requests', { method: 'POST', body: JSON.stringify(payload) });
      showDecision(result); await refreshDashboard();
    } catch (error) { toast(error.message); }
    finally { button.disabled = false; button.querySelector('span').textContent = 'Run security check'; }
  }

  $('run-button').addEventListener('click', runQuery);
  $('query-input').addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') runQuery(); });
  document.querySelectorAll('[data-prompt]').forEach(button => button.addEventListener('click', () => { $('query-input').value = button.dataset.prompt; selectedScenario = button.dataset.scenario || null; document.querySelectorAll('[data-prompt]').forEach(item => item.classList.toggle('selected', item === button)); $('query-input').focus(); }));
  $('token-open').addEventListener('click', () => { $('token-input').value = token(); tokenDialog.showModal(); });
  $('save-token').addEventListener('click', event => { event.preventDefault(); const value = $('token-input').value.trim(); if (!value) { toast('Paste a bearer token to connect.'); return; } sessionStorage.setItem(tokenKey, value); tokenDialog.close(); refreshDashboard(); toast('Token set for this browser tab.'); });
  $('refresh-button').addEventListener('click', async () => { await health(); await refreshDashboard(); });
  $('refresh-history').addEventListener('click', refreshDashboard);
  health(); refreshDashboard(); demoConfigurationReady = demoConfiguration();
})();
