require('dotenv').config();

console.log('✅ Environment loaded');

// ==============================
// Imports
// ==============================
const express = require('express');
const mongoose = require('mongoose');
const session = require('express-session');
const MongoStore = require('connect-mongo').default;
const flash = require('express-flash');
const passport = require('./config/passport');
const authRoutes = require('./routes/auth');
const axios = require('axios');  // ← NEW

// ==============================
// App Init
// ==============================
const app = express();
const PORT = process.env.PORT || 3000;
const MODEL_API_URL = process.env.MODEL_API_URL || 'http://localhost:8000';  // ← NEW

// ==============================
// View Engine
// ==============================
app.set('view engine', 'ejs');
app.set('views', __dirname + '/views');

// ==============================
// Middleware
// ==============================
app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use('/static', express.static(__dirname + '/public'));  // ← NEW

// ==============================
// Session Store (Mongo)
// ==============================
app.use(
  session({
    secret: process.env.SESSION_SECRET || 'fallback-secret',
    resave: false,
    saveUninitialized: false,
    store: MongoStore.create({
      mongoUrl: process.env.MONGODB_URI,
      collectionName: 'sessions',
    }),
    cookie: {
      maxAge: 1000 * 60 * 60 * 24,
    },
  })
);

// ==============================
// Passport
// ==============================
app.use(passport.initialize());
app.use(passport.session());

// ==============================
// Flash Messages
// ==============================
app.use(flash());

// ==============================
// Routes
// ==============================
app.use('/', authRoutes);

// ==============================
// Homepage
// ==============================
app.get('/', (req, res) => {
  res.render('index', { user: req.user });
});

// ==============================
// Protected Dashboard
// ==============================
app.get('/dashboard', (req, res) => {
  if (!req.user) return res.redirect('/login');
  res.render('dashboard', { user: req.user });
});

// ==============================
// Crime Model Routes              ← NEW
// ==============================
app.get('/model/health', async (req, res) => {
    try {
        const response = await axios.get(`${MODEL_API_URL}/health`);
        res.json(response.data);
    } catch (err) {
        res.status(500).json({ error: 'Model API unavailable' });
    }
});

app.get('/model/predict', async (req, res) => {
    try {
        const { lat, lon } = req.query;
        const response = await axios.post(`${MODEL_API_URL}/predict`, {
            lat_bin: parseFloat(lat),
            lon_bin: parseFloat(lon)
        });
        res.json(response.data);
    } catch (err) {
        res.status(500).json({ error: 'Model API unavailable' });
    }
});

app.get('/model/stats', async (req, res) => {
    try {
        const response = await axios.get(`${MODEL_API_URL}/stats`);
        res.json(response.data);
    } catch (err) {
        res.status(500).json({ error: 'Model API unavailable' });
    }
});

app.get('/crime-dashboard', (req, res) => {
    if (!req.user) return res.redirect('/login');
    res.sendFile(__dirname + '/dashboard/index.html');
});

// ==============================
// Health Check (Jenkins)
// ==============================
app.get('/health', (req, res) => {
  res.status(200).json({ status: 'ok' });
});

// ==============================
// MongoDB Connect + Start Server
// ==============================
mongoose
  .connect(process.env.MONGODB_URI)
  .then(() => {
    console.log('✅ MongoDB connected');
    app.listen(PORT, () => {
      console.log(`🚀 Server running on http://localhost:${PORT}`);
    });
  })
  .catch((err) => {
    console.error('❌ MongoDB error:', err.message);
    process.exit(1);
  });

module.exports = app;