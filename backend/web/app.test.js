const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const cases = [];
function test(name, fn) { cases.push([name, fn]); }

class FakeElement {
  constructor() {
    this.textContent = '';
    this.innerHTML = '';
    this.hidden = false;
    this.value = '';
    this.style = {};
    this.dataset = {};
    this.childNodes = [];
    this.handlers = {};
    this.classes = new Set();
    this.classList = {
      add: (...items) => items.forEach(item => this.classes.add(item)),
      remove: (...items) => items.forEach(item => this.classes.delete(item)),
      toggle: (item, force) => force ? this.classes.add(item) : this.classes.delete(item),
    };
    this.parentElement = { classList: this.classList };
  }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  querySelector() { return new FakeElement(); }
  replaceChildren(...children) { this.childNodes = children; }
  append(...children) { this.childNodes.push(...children); }
  dispatchEvent(event) { this.handlers[event.type]?.(event); }
  scrollIntoView() {}
  showModal() {}
  close() {}
  focus() {}
}

function response(status, body, contentType = 'application/json; charset=utf-8') {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: name => name.toLowerCase() === 'content-type' ? contentType : null },
    json: async () => body,
    text: async () => typeof body === 'string' ? body : JSON.stringify(body),
  };
}

function setup(nlResponse, dashboardResponse = response(200, { history: [], workload: [], behavior_profile: null }), demoEnabled = false, loginResponse = response(200, { access_token: 'issued-token', token_type: 'bearer', expires_in: 900 })) {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) {
      const element = new FakeElement();
      if (['decision-section', 'analysis-section', 'results-section', 'demo-mode'].includes(id)) element.hidden = true;
      elements.set(id, element);
    }
    return elements.get(id);
  };
  const session = new Map([['queryimmune_api_token', 'test-token']]);
  const calls = [];
  const requests = [];
  const warnings = [];
  const fetch = async (path, options = {}) => {
    calls.push(path);
    requests.push({ path, options });
    if (path === '/api/v1/nl-requests') {
      const nextResponse = Array.isArray(nlResponse) ? nlResponse.shift() : nlResponse;
      if (nextResponse instanceof Error) throw nextResponse;
      return nextResponse;
    }
    if (path === '/api/v1/auth/login') return loginResponse;
    if (path === '/api/v1/auth/me') return response(200, { subject: 'demo:dr-ada', display_name: 'Dr Ada Example', roles: ['doctor'] });
    if (path === '/health/ready') return response(200, { status: 'ok' });
    if (path === '/api/v1/demo/config') return response(200, { enabled: demoEnabled });
    if (path === '/api/v1/dashboard') return dashboardResponse;
    throw new Error(`Unexpected request: ${path}`);
  };
  const promptButtons = [
    ['normal', 'How many patients visited cardiology today?'],
    ['sensitive', 'Show the medical history of every patient.'],
    ['destructive', 'Delete all patient records.'],
    ['mismatch', 'Show my appointments.'],
    ['drift', 'Show my first 10 appointments.'],
  ].map(([scenario, prompt]) => {
    const button = new FakeElement();
    button.dataset = { scenario, prompt };
    return button;
  });
  const document = {
    getElementById: get,
    createElement: () => new FakeElement(),
    querySelector: () => new FakeElement(),
    querySelectorAll: selector => selector === '[data-prompt]' ? promptButtons : [],
  };
  const sandbox = {
    document,
    fetch,
    sessionStorage: { getItem: key => session.get(key) || '', setItem: (key, value) => session.set(key, value), removeItem: key => session.delete(key) },
    clearTimeout,
    setTimeout: () => 1,
    console: { warn: (...args) => warnings.push(args) },
  };
  vm.runInNewContext(source, sandbox, { filename: 'app.js' });
  get('query-input').value = 'Show a test query';
  return { elements, get, calls, requests, warnings, promptButtons, session };
}

const decision = (name, extra = {}) => ({
  request_id: 'id', decision: name, reason_codes: ['TEST_REASON'], message: `${name} message`,
  execution_status: name === 'allow' ? 'succeeded' : 'not_run', risk_score: 72,
  risk_analysis: { risk_factors: { policy: 72 }, signals: ['TEST_SIGNAL'] }, ...extra,
});

async function submit(nlResponse) {
  const env = setup(nlResponse);
  await env.get('run-button').handlers.click();
  return env;
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

async function waitFor(predicate) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 0));
  }
  throw new Error('Timed out waiting for the request to start');
}

test('200 ALLOW renders the normal decision and result', async () => {
  const env = await submit(response(200, decision('allow', { results: [{ count: 2 }], row_count: 1 })));
  assert.equal(env.get('decision-title').textContent, 'ALLOW');
  assert.equal(env.get('results-section').hidden, false);
  assert.match(env.get('result-summary').textContent, /1 rows/);
});

test('202 REVIEW renders risk details and confirms no execution', async () => {
  const env = await submit(response(202, decision('review')));
  assert.equal(env.get('decision-title').textContent, 'REVIEW');
  assert.match(env.get('decision-message').textContent, /Human review required/);
  assert.match(env.get('decision-message').textContent, /not executed/);
  assert.match(env.get('decision-message').textContent, /Execution status: NOT RUN/);
  assert.equal(env.get('risk-score').textContent, '72.0 / 100');
  assert.deepEqual(env.get('decision-reasons').childNodes.map(chip => chip.textContent), ['TEST_REASON', 'TEST_SIGNAL']);
  assert.equal(env.get('results-section').hidden, true);
});

test('403 structured BLOCK renders risk details and confirms no execution', async () => {
  const env = await submit(response(403, decision('block')));
  assert.equal(env.get('decision-title').textContent, 'BLOCK');
  assert.match(env.get('decision-message').textContent, /not executed/);
  assert.match(env.get('decision-message').textContent, /Execution status: NOT RUN/);
  assert.equal(env.get('risk-score').textContent, '72.0 / 100');
  assert.deepEqual(env.get('decision-reasons').childNodes.map(chip => chip.textContent), ['TEST_REASON', 'TEST_SIGNAL']);
  assert.equal(env.get('results-section').hidden, true);
});

test('real 403 BLOCK response shape renders without a request-failed toast', async () => {
  const env = await submit(response(403, {
    decision: 'block', risk_score: 100.0, execution_status: 'not_run',
    reason_codes: ['SCOPE_MISMATCH', 'SENSITIVE_DATA_ACCESS'],
  }));
  assert.equal(env.get('decision-title').textContent, 'BLOCK');
  assert.equal(env.get('risk-score').textContent, '100.0 / 100');
  assert.deepEqual(env.get('decision-reasons').childNodes.map(chip => chip.textContent), ['SCOPE_MISMATCH', 'SENSITIVE_DATA_ACCESS']);
  assert.match(env.get('decision-message').textContent, /not executed/);
  assert.match(env.get('decision-message').textContent, /Execution status: NOT RUN/);
  assert.doesNotMatch(env.get('toast').textContent, /Request failed/);
});

test('403 uppercase BLOCK decision is normalized and rendered', async () => {
  const env = await submit(response(403, decision('BLOCK')));
  assert.equal(env.get('decision-title').textContent, 'BLOCK');
  assert.doesNotMatch(env.get('toast').textContent, /Request failed/);
});

test('403 BLOCK replaces a prior ALLOW and never shows the generic status toast', async () => {
  const pendingResponse = deferred();
  const env = setup([response(200, decision('allow', { results: [{ count: 2 }], row_count: 1 })), pendingResponse.promise]);
  await env.get('run-button').handlers.click();
  assert.equal(env.get('decision-title').textContent, 'ALLOW');
  assert.equal(env.get('results-section').hidden, false);
  env.get('toast').textContent = 'Request failed (403)';
  env.get('toast').classList.add('show');
  const request = env.get('run-button').handlers.click();
  await waitFor(() => env.calls.filter(path => path === '/api/v1/nl-requests').length === 2);
  assert.equal(env.get('decision-section').hidden, true);
  assert.equal(env.get('results-section').hidden, true);
  pendingResponse.resolve(response(403, decision('block')));
  await request;
  assert.equal(env.get('decision-title').textContent, 'BLOCK');
  assert.equal(env.get('decision-section').hidden, false);
  assert.notEqual(env.get('toast').textContent, 'Request failed (403)');
});

test('202 REVIEW replaces a prior ALLOW and states that execution did not happen', async () => {
  const pendingResponse = deferred();
  const env = setup([response(200, decision('allow', { results: [{ count: 2 }], row_count: 1 })), pendingResponse.promise]);
  await env.get('run-button').handlers.click();
  const request = env.get('run-button').handlers.click();
  await waitFor(() => env.calls.filter(path => path === '/api/v1/nl-requests').length === 2);
  assert.equal(env.get('decision-section').hidden, true);
  pendingResponse.resolve(response(202, decision('review')));
  await request;
  assert.equal(env.get('decision-title').textContent, 'REVIEW');
  assert.match(env.get('decision-message').textContent, /not executed/);
  assert.match(env.get('decision-message').textContent, /Execution status: NOT RUN/);
  assert.match(env.get('decision-message').textContent, /Human review required/);
  assert.equal(env.get('results-section').hidden, true);
});

test('a new successful request clears the previous BLOCK toast immediately', async () => {
  const pendingResponse = deferred();
  const env = setup([response(403, decision('block')), pendingResponse.promise]);
  await env.get('run-button').handlers.click();
  env.get('toast').textContent = 'Previous BLOCK request failed';
  env.get('toast').classList.add('show');
  const request = env.get('run-button').handlers.click();
  await waitFor(() => env.calls.filter(path => path === '/api/v1/nl-requests').length === 2);
  assert.equal(env.get('toast').textContent, '');
  assert.equal(env.get('decision-section').hidden, true);
  pendingResponse.resolve(response(200, decision('allow')));
  await request;
  assert.equal(env.get('decision-title').textContent, 'ALLOW');
  assert.equal(env.get('toast').textContent, '');
});

test('background dashboard 403 does not toast over a successful query result', async () => {
  const env = setup(response(200, decision('allow')), response(403, { detail: 'Dashboard access denied' }));
  await env.get('run-button').handlers.click();
  assert.equal(env.get('decision-title').textContent, 'ALLOW');
  assert.equal(env.get('decision-section').hidden, false);
  assert.equal(env.get('toast').textContent, '');
  assert.match(env.get('auth-hint').textContent, /Dashboard access denied/);
  assert.ok(env.warnings.some(([, context]) => context.path === '/api/v1/dashboard' && context.status === 403));
});

test('editing a selected scenario sends a custom prompt without stale demo_scenario', async () => {
  const env = setup(response(200, decision('allow')), response(200, { history: [], workload: [], behavior_profile: null }), true);
  env.promptButtons[0].handlers.click();
  env.get('query-input').value = 'Show me my first 9 appointments.';
  env.get('query-input').dispatchEvent({ type: 'input' });
  await env.get('run-button').handlers.click();
  const submitted = env.requests.find(request => request.path === '/api/v1/nl-requests');
  assert.deepEqual(JSON.parse(submitted.options.body), { request: 'Show me my first 9 appointments.' });
  assert.equal(env.get('decision-title').textContent, 'ALLOW');
  assert.equal(env.get('toast').textContent, '');
});

test('structured server-status BLOCK is still rendered as a decision', async () => {
  const env = await submit(response(503, decision('block', { reason_codes: ['AI_PROVIDER_UNAVAILABLE'] })));
  assert.equal(env.get('decision-title').textContent, 'BLOCK');
  assert.doesNotMatch(env.get('toast').textContent, /Server error|Request failed/);
});

test('401 shows an authentication error', async () => {
  const env = await submit(response(401, { detail: 'Invalid token' }));
  assert.equal(env.get('toast').textContent, 'Invalid token');
  assert.equal(env.get('decision-section').hidden, true);
});

test('login stores the issued token and displays the database identity and roles', async () => {
  const env = setup(response(200, decision('allow')));
  env.get('login-username').value = 'professor-demo';
  env.get('login-password').value = 'correct password';
  await env.get('login-form').handlers.submit({ preventDefault() {} });
  const request = env.requests.find(item => item.path === '/api/v1/auth/login');
  assert.deepEqual(JSON.parse(request.options.body), { username: 'professor-demo', password: 'correct password' });
  assert.equal(request.options.headers.Authorization, undefined);
  assert.equal(env.session.get('queryimmune_api_token'), 'issued-token');
  assert.equal(env.get('current-user-name').textContent, 'Dr Ada Example');
  assert.equal(env.get('identity-status').textContent, 'doctor');
  assert.equal(env.get('logout-button').hidden, false);
});

test('invalid login displays the server rejection and does not store a token', async () => {
  const env = setup(response(200, decision('allow')), undefined, false, response(401, { detail: 'Invalid username or password' }));
  env.session.delete('queryimmune_api_token');
  env.get('login-username').value = 'professor-demo';
  env.get('login-password').value = 'wrong password';
  await env.get('login-form').handlers.submit({ preventDefault() {} });
  assert.equal(env.get('login-error').textContent, 'Invalid username or password');
  assert.equal(env.session.has('queryimmune_api_token'), false);
});

test('logout removes the tab token and clears authenticated dashboard data', async () => {
  const env = setup(response(200, decision('allow')));
  await env.get('logout-button').handlers.click();
  assert.equal(env.session.has('queryimmune_api_token'), false);
  assert.equal(env.get('current-user-name').textContent, 'Signed out');
  assert.equal(env.get('identity-status').textContent, 'Sign in to continue');
  assert.equal(env.get('logout-button').hidden, true);
  assert.equal(env.get('current-user-name').textContent, 'Signed out');
});

test('403 without a structured decision is treated as authorization failure', async () => {
  const env = await submit(response(403, { detail: 'User is not provisioned' }));
  assert.equal(env.get('toast').textContent, 'User is not provisioned');
  assert.equal(env.get('decision-section').hidden, true);
});

test('422 displays the API validation error', async () => {
  const env = await submit(response(422, { detail: [{ msg: 'request is too short' }] }));
  assert.equal(env.get('toast').textContent, 'request is too short');
});

test('500 displays the server error returned by the API', async () => {
  const env = await submit(response(500, { detail: 'Database service unavailable' }));
  assert.equal(env.get('toast').textContent, 'Database service unavailable');
});

test('network failure shows the generic request failed message', async () => {
  const env = await submit(new Error('offline'));
  assert.equal(env.get('toast').textContent, 'Request failed. Check your connection and try again.');
});

test('structured unsupported and ambiguous outcomes render without pretending SQL ran', async () => {
  const unsupported = await submit(response(200, decision('block', { outcome: 'unsupported_intent', reason_codes: ['UNSUPPORTED_INTENT'] })));
  assert.equal(unsupported.get('decision-title').textContent, 'UNSUPPORTED INTENT');
  assert.match(unsupported.get('decision-message').textContent, /No query was executed/);
  const ambiguous = await submit(response(200, decision('block', { outcome: 'ambiguous_request', reason_codes: ['AMBIGUOUS_REQUEST'] })));
  assert.equal(ambiguous.get('decision-title').textContent, 'AMBIGUOUS REQUEST');
});

(async () => {
  let failed = 0;
  for (const [name, fn] of cases) {
    try { await fn(); process.stdout.write(`PASS ${name}\n`); }
    catch (error) { failed++; process.stderr.write(`FAIL ${name}\n${error.stack}\n`); }
  }
  process.stdout.write(`${cases.length - failed}/${cases.length} frontend cases passed\n`);
  if (failed) process.exitCode = 1;
})();
