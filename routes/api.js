/**
 * Proxy routes between the portal and the Python prediction service.
 *
 * The portal never calls the model service directly from the browser: every
 * request passes through here so it inherits the session guard, a timeout and
 * consistent error shapes.
 */

const express = require('express');
const axios = require('axios');

const router = express.Router();

const MODEL_API_URL = (process.env.MODEL_API_URL || 'http://localhost:8000').replace(/\/$/, '');
const MODEL_TIMEOUT_MS = Number(process.env.MODEL_TIMEOUT_MS || 15000);

const client = axios.create({
  baseURL: MODEL_API_URL,
  timeout: MODEL_TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json' },
});

/**
 * Translate a failure talking to the prediction service into a response the
 * dashboard can show, preserving the upstream status and detail where there is
 * one rather than flattening everything to a 500.
 */
function forwardError(err, res, action) {
  if (err.response) {
    const detail = err.response.data && (err.response.data.detail || err.response.data.error);
    return res.status(err.response.status).json({
      error: `Prediction service rejected the ${action} request.`,
      detail: detail || err.response.statusText,
    });
  }

  const timedOut = err.code === 'ECONNABORTED';
  return res.status(504).json({
    error: timedOut
      ? `Prediction service timed out after ${MODEL_TIMEOUT_MS} ms.`
      : 'Prediction service is unreachable.',
    detail: err.code || err.message,
  });
}

/** Wrap an async handler so rejections reach the error middleware. */
const proxy = (action, handler) => async (req, res) => {
  try {
    const { data } = await handler(req);
    return res.json(data);
  } catch (err) {
    return forwardError(err, res, action);
  }
};

router.get('/health', proxy('health', () => client.get('/health')));
router.get('/stats', proxy('stats', () => client.get('/stats')));
router.get('/model-card', proxy('model card', () => client.get('/model-card')));
router.get('/risk-distribution', proxy('risk distribution', () => client.get('/risk-distribution')));

router.get('/hotspots', proxy('hotspots', (req) =>
  client.get('/hotspots', { params: { limit: req.query.limit, min_probability: req.query.min_probability } })));

router.get('/grid', proxy('grid', (req) =>
  client.get('/grid', { params: { min_incidents: req.query.min_incidents } })));

router.post('/predict', (req, res, next) => {
  const lat = Number(req.body.lat);
  const lon = Number(req.body.lon);

  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    return res.status(400).json({ error: 'Latitude and longitude are required and must be numbers.' });
  }
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) {
    return res.status(400).json({ error: 'Coordinates are out of range.' });
  }

  req.body.lat = lat;
  req.body.lon = lon;
  return next();
}, proxy('prediction', (req) => client.post('/predict', req.body)));

router.post('/optimize-route', proxy('route optimisation', (req) =>
  client.post('/optimize-route', req.body)));

module.exports = router;
module.exports.MODEL_API_URL = MODEL_API_URL;
