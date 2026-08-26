/**
 * Unit tests that need no database and no prediction service.
 */

const { describe, it, after } = require('node:test');
const assert = require('node:assert');

process.env.MONGODB_URI = process.env.MONGODB_URI || 'mongodb://127.0.0.1:27017/spp-test';
process.env.SESSION_SECRET = process.env.SESSION_SECRET || 'test-secret';
// Keep the app importable without a live database.
process.env.SESSION_STORE = 'memory';

describe('User model', () => {
  const User = require('../models/User');

  it('registers under the name "User"', () => {
    assert.strictEqual(User.modelName, 'User');
  });

  it('requires name and email', () => {
    const err = new User({}).validateSync();
    assert.ok(err.errors.name, 'name should be required');
    assert.ok(err.errors.email, 'email should be required');
  });

  it('lowercases and trims the email', () => {
    const user = new User({ name: 'Test', email: '  MiXeD@Example.COM ', password: 'password123' });
    assert.strictEqual(user.email, 'mixed@example.com');
  });

  it('hashes the password and never stores it in the clear', async () => {
    const user = new User({ name: 'Test', email: 'hash@example.com', password: 'password123' });
    await User.hashPasswordField(user);
    assert.notStrictEqual(user.password, 'password123');
    assert.match(user.password, /^\$2[aby]\$/, 'should be a bcrypt hash');
    assert.strictEqual(await user.comparePassword('password123'), true);
    assert.strictEqual(await user.comparePassword('wrong-password'), false);
  });

  it('returns false from comparePassword for OAuth-only accounts', async () => {
    const user = new User({ name: 'G', email: 'g@example.com', googleId: 'abc' });
    assert.strictEqual(await user.comparePassword('anything'), false);
  });

  it('omits the password hash from JSON output', () => {
    const user = new User({ name: 'Test', email: 'json@example.com', password: 'password123' });
    assert.strictEqual(user.toJSON().password, undefined);
  });
});

describe('Auth middleware', () => {
  const { ensureAuthenticated, ensureGuest } = require('../middleware/auth');

  const res = () => {
    const r = { statusCode: 200, redirected: null, body: null };
    r.status = (code) => { r.statusCode = code; return r; };
    r.json = (b) => { r.body = b; return r; };
    r.redirect = (to) => { r.redirected = to; return r; };
    return r;
  };

  it('lets an authenticated user through', () => {
    let called = false;
    ensureAuthenticated({ isAuthenticated: () => true }, res(), () => { called = true; });
    assert.strictEqual(called, true);
  });

  it('redirects an anonymous browser request to the login page', () => {
    const r = res();
    ensureAuthenticated(
      { isAuthenticated: () => false, accepts: () => true, path: '/dashboard', originalUrl: '/dashboard', session: {} },
      r,
      () => assert.fail('should not call next')
    );
    assert.strictEqual(r.redirected, '/login');
  });

  it('returns 401 rather than a redirect for API requests', () => {
    const r = res();
    ensureAuthenticated(
      { isAuthenticated: () => false, accepts: () => false, path: '/api/stats', originalUrl: '/api/stats', session: {} },
      r,
      () => assert.fail('should not call next')
    );
    assert.strictEqual(r.statusCode, 401);
    assert.strictEqual(r.redirected, null);
  });

  it('sends a signed-in user away from guest-only pages', () => {
    const r = res();
    ensureGuest({ isAuthenticated: () => true }, r, () => assert.fail('should not call next'));
    assert.strictEqual(r.redirected, '/dashboard');
  });
});

describe('Module wiring', () => {
  it('loads the passport configuration', () => {
    const passport = require('../config/passport');
    assert.ok(passport);
    assert.ok(passport._strategy('local'), 'local strategy should be registered');
  });

  it('loads the auth router', () => {
    assert.ok(require('../routes/auth'));
  });

  it('loads the api router and resolves the model URL', () => {
    const api = require('../routes/api');
    assert.ok(api);
    assert.match(api.MODEL_API_URL, /^https?:\/\//);
  });

  it('loads the express app without starting a server', () => {
    const app = require('../app');
    assert.strictEqual(typeof app, 'function');
    assert.ok('sessionStore' in app.locals, 'session store should be exposed for teardown');
  });

  // The Mongo-backed session store opens a MongoClient that would hold the
  // event loop open. Close whatever the app built.
  after(async () => {
    const app = require('../app');
    if (app.locals.sessionStore) {
      await app.locals.sessionStore.close().catch(() => {});
    }
    await require('mongoose').connection.close().catch(() => {});
  });
});
