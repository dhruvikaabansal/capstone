/**
 * Authentication routes: local signup and login, Google OAuth, logout.
 */

const express = require('express');
const passport = require('passport');
const User = require('../models/User');
const { ensureGuest } = require('../middleware/auth');

const router = express.Router();

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD_LENGTH = 8;

const googleConfigured = Boolean(process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET);

// ---------------------------------------------------------------------------
// Signup
// ---------------------------------------------------------------------------
router.post('/signup', ensureGuest, async (req, res, next) => {
  const name = (req.body.name || '').trim();
  const email = (req.body.email || '').trim().toLowerCase();
  const { password, confirmPassword } = req.body;

  const fail = (message) => res.status(400).render('signup', { error: [message] });

  if (!name || !email || !password) return fail('Name, email and password are all required.');
  if (!EMAIL_PATTERN.test(email)) return fail('Enter a valid email address.');
  if (password.length < MIN_PASSWORD_LENGTH) {
    return fail(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
  }
  if (password !== confirmPassword) return fail('The two passwords do not match.');

  try {
    if (await User.findOne({ email })) {
      return fail('An account with that email already exists.');
    }

    const user = await User.create({ name, email, password });

    // Sign the new user straight in rather than bouncing them to the login form.
    return req.login(user, (err) => {
      if (err) return next(err);
      return res.redirect('/dashboard');
    });
  } catch (err) {
    // A unique-index violation can still win the race against the check above.
    if (err.code === 11000) return fail('An account with that email already exists.');
    return next(err);
  }
});

// ---------------------------------------------------------------------------
// Local login
// ---------------------------------------------------------------------------
router.post('/login', ensureGuest, (req, res, next) => {
  passport.authenticate('local', (err, user, info) => {
    if (err) return next(err);

    if (!user) {
      req.flash('error', (info && info.message) || 'Invalid email or password.');
      return res.redirect('/login');
    }

    return req.login(user, (loginErr) => {
      if (loginErr) return next(loginErr);
      const target = req.session.returnTo || '/dashboard';
      delete req.session.returnTo;
      return res.redirect(target);
    });
  })(req, res, next);
});

// ---------------------------------------------------------------------------
// Google OAuth
// ---------------------------------------------------------------------------
router.get('/auth/google', (req, res, next) => {
  if (!googleConfigured) {
    req.flash('error', 'Google sign-in is not configured on this deployment.');
    return res.redirect('/login');
  }
  return passport.authenticate('google', { scope: ['profile', 'email'] })(req, res, next);
});

router.get('/auth/google/callback', (req, res, next) => {
  if (!googleConfigured) return res.redirect('/login');

  return passport.authenticate('google', (err, user) => {
    if (err || !user) {
      req.flash('error', 'Google sign-in failed. Try again or use your email and password.');
      return res.redirect('/login');
    }
    return req.login(user, (loginErr) => {
      if (loginErr) return next(loginErr);
      return res.redirect('/dashboard');
    });
  })(req, res, next);
});

// ---------------------------------------------------------------------------
// Logout
// ---------------------------------------------------------------------------
router.post('/logout', (req, res, next) => {
  req.logout((err) => {
    if (err) return next(err);
    return req.session.destroy(() => {
      res.clearCookie('spp.sid');
      return res.redirect('/login');
    });
  });
});

// Kept so an existing bookmark or link still signs the user out.
router.get('/logout', (req, res, next) => {
  req.logout((err) => {
    if (err) return next(err);
    return res.redirect('/login');
  });
});

module.exports = router;
module.exports.googleConfigured = googleConfigured;
