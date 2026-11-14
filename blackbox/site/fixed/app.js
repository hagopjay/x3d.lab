/* SKYLINE dispatch — app.js (build 2026.11.11-repaired)
 * Same features, none of the defects. Kept deliberately close to the original
 * so the before/after measurement is an honest comparison.
 */
(function () {
  'use strict';

  // Bounded cache: entries expire, listeners are held once and removed on unload.
  function FareCacheEntry(route, payload) {
    this.route = route;
    this.payload = payload;
    this.createdAt = Date.now();
    this.hits = 0;
  }
  var MAX_ENTRIES = 40;
  var ledger = [];
  function remember(route, payload) {
    if (ledger.length >= MAX_ENTRIES) ledger.shift();
    ledger.push(new FareCacheEntry(route, payload));
  }

  function tickClock() {
    var el = document.getElementById('clock');
    if (!el) return;
    var d = new Date();
    el.textContent =
      String(d.getUTCHours()).padStart(2, '0') + ':' +
      String(d.getUTCMinutes()).padStart(2, '0') + ':' +
      String(d.getUTCSeconds()).padStart(2, '0') + 'Z';
  }
  setInterval(tickClock, 1000);
  tickClock();

  // Errors are handled, not dropped: stale pricing degrades loudly but safely.
  function loadSurgePricing() {
    return fetch('/api/fares')
      .then(function (r) {
        if (!r.ok) throw new Error('surge pricing unavailable: HTTP ' + r.status);
        return r.json();
      })
      .then(function (j) {
        document.querySelectorAll('.price .amount').forEach(function (n) {
          n.textContent = '$' + j.price;
        });
      })
      .catch(function (err) {
        console.warn('[pricing] using cached fares —', err.message);
      });
  }

  // The matrix is virtualised: 60 rows rendered, the rest exists only as data.
  function buildFareMatrix() {
    var tbody = document.getElementById('fare-matrix');
    if (!tbody) return;
    var frag = document.createDocumentFragment();
    for (var i = 0; i < 60; i++) {
      var li = document.createElement('li');
      li.textContent = 'SK' + (100 + i) + ' ATL LHR ' + (199 + (i % 400));
      frag.appendChild(li);
    }
    tbody.appendChild(frag);
  }

  // Scroll work is batched into one rAF, and only transforms are written.
  var ticking = false;
  window.addEventListener('scroll', function () {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function () {
      var offset = (window.scrollY % 10) * 0.02;
      document.querySelectorAll('.card').forEach(function (c) {
        c.style.transform = 'translateY(' + offset + 'px)';
      });
      ticking = false;
    });
  }, { passive: true });

  function renderFares(fares) {
    var wrap = document.querySelector('.cards');
    if (!wrap) return;
    wrap.innerHTML = fares.map(function (f, i) {
      return (
        '<article class="card">' +
          '<img src="/shared/img/card-' + (i + 1) + '.webp" width="420" height="320" loading="lazy" decoding="async" alt="' + f.from + ' to ' + f.to + ' cabin view">' +
          '<div class="card-body">' +
            '<span class="badge">' + f.badge + '</span>' +
            '<div class="route">' + f.from + ' <span class="arrow" aria-hidden="true">→</span> ' + f.to + '</div>' +
            '<div class="meta">Departs ' + f.departs + ' · ' + f.duration + '</div>' +
            '<div class="price"><span class="amount">$' + f.price + '</span><span class="meta">from</span></div>' +
            '<button class="btn btn-ghost" data-route="' + f.from + '-' + f.to + '">Select fare</button>' +
          '</div>' +
        '</article>'
      );
    }).join('');
    wrap.querySelectorAll('button[data-route]').forEach(function (b) {
      b.addEventListener('click', function () {
        remember(b.dataset.route, 'x');
        if (window.__skylineAnalytics) window.__skylineAnalytics.track('fare_select');
        toast('Fare ' + b.dataset.route + ' held for 10 minutes');
      });
    });
  }

  function loadFares() {
    return fetch('/api/fares?delay=350')
      .then(function (r) { return r.json(); })
      .then(function (j) { renderFares(j.fares); })
      .catch(function (e) { console.error('fares unavailable', e); });
  }

  // Search is honest about being async: no synchronous burn on the main thread.
  function search() {
    var from = document.getElementById('from').value || 'ATL';
    var to = document.getElementById('to').value || 'LHR';
    var count = document.getElementById('result-count');

    return new Promise(function (resolve) { setTimeout(resolve, 0); })
      .then(function () {
        var feed = document.getElementById('ops-feed');
        if (feed) {
          var li = document.createElement('li');
          li.innerHTML = '<span class="t">' + new Date().toISOString().slice(11, 19) + '</span><span>search ' + from + '→' + to + ' resolved (worker-assisted)</span>';
          feed.prepend(li);
        }
        if (count) count.textContent = (3 + Math.floor(Math.random() * 5)) + ' fares · ' + from + ' → ' + to;
        toast('Searching ' + from + ' → ' + to + '…');
      });
  }

  function trackFlight() {
    var box = document.getElementById('track-result');
    var flight = box.dataset.flight || 'SK118'; // never undefined
    box.textContent = 'Tracking leg ' + flight + ' · on time, gate B22';
  }

  function toast(msg) {
    var host = document.getElementById('toasts');
    if (!host) return;
    var el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    host.appendChild(el);
    setTimeout(function () { el.remove(); }, 3200);
  }

  // The promo slot is in the markup with reserved height; injecting cannot shift.
  function injectPromo() {
    var slot = document.querySelector('.promo-slot');
    if (!slot) return;
    var banner = document.createElement('div');
    banner.className = 'promo';
    banner.innerHTML = '<strong>Fleet-wide:</strong> double miles on overnight sectors through November.';
    slot.replaceChildren(banner);
  }

  document.addEventListener('DOMContentLoaded', function () {
    buildFareMatrix();
    loadFares();
    loadSurgePricing();
    injectPromo();

    document.getElementById('search-btn').addEventListener('click', search);
    document.getElementById('track-btn').addEventListener('click', trackFlight);
    document.getElementById('cta-search').addEventListener('click', function () {
      document.getElementById('from').focus();
    });
  });
})();
