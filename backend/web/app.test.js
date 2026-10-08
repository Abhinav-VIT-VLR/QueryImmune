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
  scrollIntoView() {}
  showModal() {}
  close() {}
  focus() {}
}

function response(status, body) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}

function setup(nlResponse) {
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
  const fetch = async path => {
    calls.push(path);
    if (path === '/api/v1/nl-requests') {
      if (nlResponse instanceof Error) throw nlResponse;
      return nlResponse;
    }
    if (path === '/health/ready') return response(200, { status: 'ok' });
    if (path === '/api/v1/demo/config') return response(200, { enabled: false });
    if (path === '/api/v1/dashboard') return response(200, { history: [], workload: [], behavior_profile: null });
    throw new Error(`Unexpected request: ${path}`);
  };
  const document = {
    getElementById: get,
    createElement: () => new FakeElement(),
    querySelector: () => new FakeElement(),
    querySelectorAll: () => [],
  };
  const sandbox = {
    document,
    fetch,
    sessionStorage: { getItem: key => session.get(key) || '', setItem: (key, value) => session.set(key, value) },
    clearTimeout,
    setTimeout: () => 1,
    console,
  };
  vm.runInNewContext(source, sandbox, { filename: 'app.js' });
  get('query-input').value = 'Show a test query';
  return { elements, get, calls };
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
  assert.equal(env.get('risk-score').textContent, '72.0 / 100');
  assert.deepEqual(env.get('decision-reasons').childNodes.map(chip => chip.textContent), ['TEST_REASON', 'TEST_SIGNAL']);
  assert.equal(env.get('results-section').hidden, true);
});

test('403 structured BLOCK renders risk details and confirms no execution', async () => {
  const env = await submit(response(403, decision('block')));
  assert.equal(env.get('decision-title').textContent, 'BLOCK');
  assert.match(env.get('decision-message').textContent, /not executed/);
  assert.equal(env.get('risk-score').textContent, '72.0 / 100');
  assert.deepEqual(env.get('decision-reasons').childNodes.map(chip => chip.textContent), ['TEST_REASON', 'TEST_SIGNAL']);
  assert.equal(env.get('results-section').hidden, true);
});

test('401 shows an authentication error', async () => {
  const env = await submit(response(401, { detail: 'Invalid token' }));
  assert.equal(env.get('toast').textContent, 'Invalid token');
  assert.equal(env.get('decision-section').hidden, true);
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

(async () => {
  let failed = 0;
  for (const [name, fn] of cases) {
    try { await fn(); process.stdout.write(`PASS ${name}\n`); }
    catch (error) { failed++; process.stderr.write(`FAIL ${name}\n${error.stack}\n`); }
  }
  process.stdout.write(`${cases.length - failed}/${cases.length} frontend cases passed\n`);
  if (failed) process.exitCode = 1;
})();
