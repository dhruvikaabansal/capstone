/**
 * Smart Policing Portal
 *
 * Express front end: authentication, session handling, the operations
 * dashboard, and a guarded proxy to the Python prediction service.
 */

require('dotenv').config();

const path = require('path');
const express = require('express');
const mongoose = require('mongoose');
const session = require('express-session');
const MongoStore = require('connect-mongo').default; // connect-mongo v6 ships the store as a default export
const flash = require('express-flash');
const axios = require('axios');

const passport = require('./config/passport');
const authRoutes = require('./routes/auth');
const apiRoutes = require('./routes/api');
const { ensureAuthenticated, ensureGuest } = require('./middleware/auth');

const app = express();

const PORT = Number(process.env.PORT || 3000);
const MONGODB_URI = process.env.MONGODB_URI || 'mongodb://localhost:27017/smart-policing-portal';
const MODEL_API_URL = apiRoutes.MODEL_API_URL;

if (!process.env.SESSION_SECRET && process.env.NODE_ENV === 'production') {
  console.error('SESSION_SECRET must be set in production. Refusing to start with a default secret.');
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Core middleware
// ---------------------------------------------------------------------------
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));
app.set('trust proxy', 1);
app.disable('x-powered-by');

app.use(express.urlencoded({ extended: true, limit: '100kb' }));
app.use(express.json({ limit: '1mb' }));
app.use('/static', express.static(path.join(__dirname, 'public'), { maxAge: '1h' }));

// Sessions live in MongoDB so they survive a restart and are shared across
// replicas. SESSION_STORE=memory swaps in express-session's in-process store,
// which lets the HTTP tests run without a database. Never use it in production:
// it leaks memory and does not survive a restart.
const useMemoryStore = process.env.SESSION_STORE === 'memory';

let sessionStore;
if (useMemoryStore) {
  console.warn('Using the in-memory session store. Do not run this configuration in production.');
} else {
  sessionStore = MongoStore.create({
    mongoUrl: MONGODB_URI,
    collectionName: 'sessions',
    ttl: 60 * 60 * 24,
  });
  // Without a listener, a connection failure surfaces as an unhandled rejection
  // and takes the process down.
  sessionStore.on('error', (err) => console.error('Session store error:', err.message));
}

// Kept on app.locals so tests and the shutdown path can close the store's
// MongoClient; otherwise it holds the event loop open.
app.locals.sessionStore = sessionStore || null;

app.use(
  session({
    name: 'spp.sid',
    secret: process.env.SESSION_SECRET || 'development-only-secret',
    resave: false,
    saveUninitialized: false,
    ...(sessionStore ? { store: sessionStore } : {}),
    cookie: {
      httpOnly: true,
      sameSite: 'lax',
      secure: process.env.NODE_ENV === 'production',
      maxAge: 1000 * 60 * 60 * 24,
    },
  })
);

app.use(passport.initialize());
app.use(passport.session());
app.use(flash());

// Make the signed-in user available to every template without passing it
// explicitly from each route.
app.use((req, res, next) => {
  res.locals.currentUser = req.user || null;
  res.locals.googleEnabled = authRoutes.googleConfigured;
  next();
});

// ---------------------------------------------------------------------------
// Pages
// ---------------------------------------------------------------------------
app.get('/', (req, res) => res.redirect(req.user ? '/dashboard' : '/login'));

app.get('/login', ensureGuest, (req, res) =>
  res.render('login', { error: req.flash('error'), success: req.flash('success') })
);

app.get('/signup', ensureGuest, (req, res) =>
  res.render('signup', { error: req.flash('error') })
);

app.get('/dashboard', ensureAuthenticated, (req, res) =>
  res.render('dashboard', { user: req.user })
);

// The pre-rendered incident density map, shown inside the dashboard's map tab.
app.get('/density-map', ensureAuthenticated, (req, res) =>
  res.sendFile(path.join(__dirname, 'dashboard', 'incident_density_map.html'))
);

app.use('/', authRoutes);
app.use('/api', ensureAuthenticated, apiRoutes);

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------
// Reports the portal's own state plus its two dependencies. Returns 200 while
// the portal itself can serve traffic, so a restarting model service does not
// cause the orchestrator to kill a healthy web container.
app.get('/health', async (req, res) => {
  const mongoStates = ['disconnected', 'connected', 'connecting', 'disconnecting'];
  const health = {
    status: 'ok',
    service: 'smart-policing-portal',
    uptime_seconds: Math.round(process.uptime()),
    dependencies: {
      mongodb: mongoStates[mongoose.connection.readyState] || 'unknown',
      model_api: 'unknown',
    },
  };

  try {
    const { data } = await axios.get(`${MODEL_API_URL}/health`, { timeout: 4000 });
    health.dependencies.model_api = data.status === 'healthy' ? 'connected' : 'degraded';
    health.dependencies.sectors_indexed = data.sectors_indexed;
  } catch (err) {
    health.dependencies.model_api = 'unreachable';
  }

  if (health.dependencies.mongodb !== 'connected') health.status = 'degraded';

  res.status(200).json(health);
});

// ---------------------------------------------------------------------------
// Fallbacks
// ---------------------------------------------------------------------------
app.use((req, res) => {
  if (req.accepts('html') && !req.path.startsWith('/api')) {
    return res.status(404).render('error', {
      code: 404,
      message: 'That page does not exist.',
    });
  }
  return res.status(404).json({ error: 'Not found' });
});

// eslint-disable-next-line no-unused-vars
app.use((err, req, res, next) => {
  console.error('Unhandled error:', err);
  const showDetail = process.env.NODE_ENV !== 'production';

  if (req.accepts('html') && !req.path.startsWith('/api')) {
    return res.status(500).render('error', {
      code: 500,
      message: showDetail ? err.message : 'Something went wrong on our end.',
    });
  }
  return res.status(500).json({
    error: 'Internal server error',
    detail: showDetail ? err.message : undefined,
  });
});

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------
let server;

async function start() {
  const maxAttempts = Number(process.env.MONGO_CONNECT_ATTEMPTS || 12);

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      await mongoose.connect(MONGODB_URI, { serverSelectionTimeoutMS: 5000 });
      console.log('MongoDB connected');
      break;
    } catch (err) {
      console.error(`MongoDB connection attempt ${attempt}/${maxAttempts} failed: ${err.message}`);
      if (attempt === maxAttempts) {
        console.error('Could not reach MongoDB. Exiting.');
        process.exit(1);
      }
      await new Promise((resolve) => setTimeout(resolve, 5000));
    }
  }

  server = app.listen(PORT, () => {
    console.log(`Smart Policing Portal listening on http://localhost:${PORT}`);
    console.log(`Prediction service: ${MODEL_API_URL}`);
  });
}

async function shutdown(signal) {
  console.log(`${signal} received, shutting down.`);
  if (server) await new Promise((resolve) => server.close(resolve));
  if (sessionStore) {
    try { await sessionStore.close(); } catch (err) { /* already closed */ }
  }
  await mongoose.connection.close();
  process.exit(0);
}

['SIGTERM', 'SIGINT'].forEach((signal) =>
  process.on(signal, () => shutdown(signal).catch(() => process.exit(1)))
);

// Only listen when run directly, so tests can import the app.
if (require.main === module) {
  start();
}

module.exports = app;
