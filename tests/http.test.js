/**
 * HTTP tests against the real Express app.
 *
 * Runs with the in-process session store so no database is needed. Routes that
 * read or write user records are covered by the unit tests instead.
 */

process.env.SESSION_STORE = 'memory';
process.env.SESSION_SECRET = 'test-secret';
process.env.NODE_ENV = 'test';
process.env.MONGODB_URI = 'mongodb://127.0.0.1:27017/spp-test';
process.env.MODEL_API_URL = process.env.MODEL_API_URL || 'http://127.0.0.1:8000';

const { describe, it, before, after } = require('node:test');
const assert = require('node:assert');

const app = require('../app');

let server;
let base;

before(async () => {
  await new Promise((resolve) => {
    server = app.listen(0, () => {
      base = `http://127.0.0.1:${server.address().port}`;
      resolve();
    });
  });
});

after(async () => {
  if (server) await new Promise((resolve) => server.close(resolve));
});

const get = (path, opts = {}) => fetch(base + path, { redirect: 'manual', ...opts });

describe('Public pages', () => {
  it('redirects the root to the login page for anonymous visitors', async () => {
    const res = await get('/');
    assert.strictEqual(res.status, 302);
    assert.strictEqual(res.headers.get('location'), '/login');
  });

  it('renders the login page', async () => {
    const res = await get('/login');
    const html = await res.text();
    assert.strictEqual(res.status, 200);
    assert.match(html, /Smart Policing Portal/);
    assert.match(html, /action="\/login"/);
    assert.doesNotMatch(html, /Chicago Police Department/i, 'old branding should be gone');
  });

  it('renders the signup page', async () => {
    const res = await get('/signup');
    const html = await res.text();
    assert.strictEqual(res.status, 200);
    assert.match(html, /Create an account/);
    assert.match(html, /name="confirmPassword"/);
  });

  it('serves the shared stylesheet', async () => {
    const res = await get('/static/css/portal.css');
    assert.strictEqual(res.status, 200);
    assert.match(res.headers.get('content-type') || '', /text\/css/);
  });

  it('does not advertise the framework in headers', async () => {
    const res = await get('/login');
    assert.strictEqual(res.headers.get('x-powered-by'), null);
  });
});

describe('Health check', () => {
  it('reports the portal state and its dependencies', async () => {
    const res = await get('/health');
    const body = await res.json();
    assert.strictEqual(res.status, 200);
    assert.ok(['ok', 'degraded'].includes(body.status));
    assert.strictEqual(body.service, 'smart-policing-portal');
    assert.ok('mongodb' in body.dependencies);
    assert.ok('model_api' in body.dependencies);
  });
});

describe('Route protection', () => {
  it('sends anonymous users away from the dashboard', async () => {
    const res = await get('/dashboard');
    assert.strictEqual(res.status, 302);
    assert.strictEqual(res.headers.get('location'), '/login');
  });

  it('protects the density map', async () => {
    const res = await get('/density-map');
    assert.strictEqual(res.status, 302);
  });

  for (const path of ['/api/stats', '/api/hotspots', '/api/model-card', '/api/risk-distribution']) {
    it(`returns 401 JSON for ${path} when signed out`, async () => {
      const res = await get(path, { headers: { Accept: 'application/json' } });
      assert.strictEqual(res.status, 401);
      assert.deepStrictEqual(await res.json(), { error: 'Authentication required' });
    });
  }

  it('returns 401 for the prediction endpoint when signed out', async () => {
    const res = await fetch(`${base}/api/predict`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ lat: 41.88, lon: -87.63 }),
      redirect: 'manual',
    });
    assert.strictEqual(res.status, 401);
  });
});

describe('Signup validation', () => {
  const submit = (form) =>
    fetch(`${base}/signup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(form).toString(),
      redirect: 'manual',
    });

  it('rejects mismatched passwords before touching the database', async () => {
    const res = await submit({
      name: 'Test', email: 'a@example.com',
      password: 'password123', confirmPassword: 'password124',
    });
    assert.strictEqual(res.status, 400);
    assert.match(await res.text(), /do not match/i);
  });

  it('rejects a short password', async () => {
    const res = await submit({
      name: 'Test', email: 'a@example.com', password: 'short', confirmPassword: 'short',
    });
    assert.strictEqual(res.status, 400);
    assert.match(await res.text(), /at least 8 characters/i);
  });

  it('rejects a malformed email', async () => {
    const res = await submit({
      name: 'Test', email: 'not-an-email',
      password: 'password123', confirmPassword: 'password123',
    });
    assert.strictEqual(res.status, 400);
    assert.match(await res.text(), /valid email/i);
  });

  it('rejects missing fields', async () => {
    const res = await submit({ name: '', email: '', password: '' });
    assert.strictEqual(res.status, 400);
    assert.match(await res.text(), /required/i);
  });
});

describe('Not found handling', () => {
  it('renders an HTML 404 for page requests', async () => {
    const res = await get('/no-such-page');
    assert.strictEqual(res.status, 404);
    assert.match(await res.text(), /does not exist/i);
  });

  // The auth guard is mounted ahead of the API router, so an unknown /api path
  // answers 401 rather than 404 while signed out. That is deliberate: it keeps
  // the endpoint list from leaking to anonymous callers.
  it('answers unknown API routes with JSON, not an HTML page', async () => {
    const res = await get('/api/no-such-endpoint', { headers: { Accept: 'application/json' } });
    assert.ok([401, 404].includes(res.status), `unexpected status ${res.status}`);
    assert.match(res.headers.get('content-type') || '', /application\/json/);
  });
});
