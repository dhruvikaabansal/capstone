/**
 * Passport strategies: email/password and Google OAuth.
 *
 * The Google strategy is registered only when credentials are present, so a
 * deployment without them starts cleanly instead of failing at sign-in time.
 */

const passport = require('passport');
const LocalStrategy = require('passport-local').Strategy;
const GoogleStrategy = require('passport-google-oauth20').Strategy;
const User = require('../models/User');

passport.serializeUser((user, done) => done(null, user.id));

passport.deserializeUser(async (id, done) => {
  try {
    const user = await User.findById(id);
    // A deleted account leaves a live session pointing at nothing.
    return done(null, user || false);
  } catch (err) {
    return done(err);
  }
});

// ---------------------------------------------------------------------------
// Email and password
// ---------------------------------------------------------------------------
passport.use(
  new LocalStrategy({ usernameField: 'email' }, async (email, password, done) => {
    try {
      const user = await User.findOne({ email: String(email).trim().toLowerCase() });

      // Same message whether the address is unknown or the password is wrong,
      // so the form cannot be used to discover which emails are registered.
      const genericFailure = { message: 'Invalid email or password.' };

      if (!user) return done(null, false, genericFailure);
      if (!user.password) {
        return done(null, false, { message: 'This account signs in with Google.' });
      }
      if (!(await user.comparePassword(password))) return done(null, false, genericFailure);

      user.lastLoginAt = new Date();
      await user.save();

      return done(null, user);
    } catch (err) {
      return done(err);
    }
  })
);

// ---------------------------------------------------------------------------
// Google OAuth
// ---------------------------------------------------------------------------
const { GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET } = process.env;

if (GOOGLE_CLIENT_ID && GOOGLE_CLIENT_SECRET) {
  passport.use(
    new GoogleStrategy(
      {
        clientID: GOOGLE_CLIENT_ID,
        clientSecret: GOOGLE_CLIENT_SECRET,
        // A relative path is resolved against the incoming request, so the same
        // build works on localhost and behind a domain.
        callbackURL: process.env.GOOGLE_CALLBACK_URL || '/auth/google/callback',
      },
      async (accessToken, refreshToken, profile, done) => {
        try {
          const email = profile.emails && profile.emails[0] && profile.emails[0].value;
          if (!email) {
            return done(null, false, { message: 'Google did not return an email address.' });
          }

          const avatar = (profile.photos && profile.photos[0] && profile.photos[0].value) || '';

          let user = await User.findOne({ googleId: profile.id });

          // Link the Google identity to an existing email/password account.
          if (!user) user = await User.findOne({ email: email.toLowerCase() });

          if (user) {
            user.googleId = profile.id;
            if (avatar) user.avatar = avatar;
            user.lastLoginAt = new Date();
            await user.save();
            return done(null, user);
          }

          user = await User.create({
            googleId: profile.id,
            name: profile.displayName || email.split('@')[0],
            email: email.toLowerCase(),
            avatar,
            lastLoginAt: new Date(),
          });

          return done(null, user);
        } catch (err) {
          return done(err);
        }
      }
    )
  );
} else {
  console.warn('Google OAuth is not configured; only email and password sign-in is available.');
}

module.exports = passport;
