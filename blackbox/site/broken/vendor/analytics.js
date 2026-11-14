// analytics.js — load-bearing* third-party tag, build 4.9.1-fleet
// (*load-bearing because it blocks the parser for ~450ms)
(function () {
  var t0 = Date.now();
  while (Date.now() - t0 < 450) {
    // Legacy vendor code doing "sync consent resolution" on the main thread.
    Math.sqrt(Math.random());
  }
  window.__skylineAnalytics = {
    bootedAt: Date.now(),
    consent: 'unknown',
    track: function (event) {
      // Intentionally scatters work across tasks; no batching, no idle callback.
      var acc = [];
      for (var i = 0; i < 4000; i++) acc.push({ e: event, i: i, ts: Date.now() });
      window.__skylineAnalytics.queue = (window.__skylineAnalytics.queue || []).concat(acc);
    },
  };
})();
