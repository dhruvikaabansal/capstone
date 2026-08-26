/**
 * Route guards.
 */

/** Allow through only signed-in users. HTML routes redirect, API routes 401. */
function ensureAuthenticated(req, res, next) {
  if (req.isAuthenticated && req.isAuthenticated()) return next();

  if (req.accepts('html') && !req.path.startsWith('/api')) {
    req.session.returnTo = req.originalUrl;
    return res.redirect('/login');
  }
  return res.status(401).json({ error: 'Authentication required' });
}

/** Bounce signed-in users away from the login and signup pages. */
function ensureGuest(req, res, next) {
  if (req.isAuthenticated && req.isAuthenticated()) return res.redirect('/dashboard');
  return next();
}

module.exports = { ensureAuthenticated, ensureGuest };
