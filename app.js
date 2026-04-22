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

// ==============================
// App Init
// ==============================
const app = express();
const PORT = process.env.PORT || 3000;

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
// Protected Route (Dashboard)
// ==============================
app.get('/dashboard', (req, res) => {
  if (!req.user) return res.redirect('/login');
  res.send(`Welcome ${req.user.name}`);
});

// ==============================
// Health Check
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