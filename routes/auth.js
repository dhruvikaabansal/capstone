const express = require('express');
const router = express.Router();
const passport = require('passport');
const User = require('../models/User');

// ==============================
// 🟣 REGISTER
// ==============================
router.post('/signup', async (req, res) => {
  try {
    const { name, email, password } = req.body;

    let user = await User.findOne({ email: email.toLowerCase() });
    if (user) {
      return res.render('signup', { error: 'User already exists' });
    }

    // TODO: Confirm passwords match (if not checked in frontend)
    if (req.body.password !== req.body.confirmPassword) {
      return res.render('signup', { error: 'Passwords do not match' });
    }

    user = new User({
      name,
      email: email.toLowerCase(),
      password
    });

    await user.save();

    res.redirect('/login');
  } catch (err) {
    console.error(err);
    res.render('signup', { error: 'Error registering user' });
  }
});

// ==============================
// 🟣 LOGIN (LOCAL)
// ==============================
router.post('/login',
  passport.authenticate('local', {
    successRedirect: '/dashboard',
    failureRedirect: '/login',
    failureFlash: true
  })
);

// ==============================
// 🟣 LOGOUT
// ==============================
router.get('/logout', (req, res, next) => {
  req.logout(function(err) {
    if (err) return next(err);
    res.redirect('/');
  });
});

// ==============================
// 🔵 GOOGLE AUTH
// ==============================

// Step 1: Redirect to Google
router.get('/auth/google',
  passport.authenticate('google', {
    scope: ['profile', 'email']
  })
);

// Step 2: Google callback
router.get('/auth/google/callback',
  passport.authenticate('google', {
    failureRedirect: '/login',
  }),
  (req, res) => {
    res.redirect('/dashboard');
  }
);

module.exports = router;