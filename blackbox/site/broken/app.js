/* SKYLINE dispatch — app.js (build 2026.11.04-cursed)
 * Every defect in here is deliberate and documented in docs/DEFECT-CATALOG.md.
 * The point: an agent should be able to find all of them through DevTools alone.
 */
(function () {
  'use strict';

  var ORIGIN = location.origin;

  // ── leak #1: an unbounded cache of class instances ───────────────────────
  function FareCacheEntry(route, payload) {
    this.route = route;
    this.payload = payload;
    this.createdAt = Date.now();
    this.hits = 0;
  }
  window.__skyline = { ledger: [], listeners: 0, orphans: [], searches: 0 };

  // ── clock ───────────────────────────────────────────────────────────────
  function tickClock() {
    var el = document.getElementById('clock');
    if (el) {
      var d = new Date();
      el.textContent =
        String(d.getUTCHours()).padStart(2, '0') + ':' +
        String(d.getUTCMinutes()).padStart(2, '0') + ':' +
        String(d.getUTCSeconds()).padStart(2, '0') + 'Z';
    }
  }
  setInterval(tickClock, 1000);
  tickClock();

  // ── defect: unhandled promise rejection on every load ───────────────────
  // (no .catch(); no status check; the UI silently shows stale pricing)
  function loadSurgePricing() {
    fetch(ORIGIN + '/api/flaky')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        document.querySelectorAll('.price .amount').forEach(function (n) {
          n.textContent = '$' + j.price;
        });
      });
  }

  // ── defect: 1,200 hidden rows → ~8,400 DOM nodes ────────────────────────
  function buildFareMatrix() {
    var tbody = document.getElementById('fare-matrix');
    if (!tbody) return;
    var html = '';
    for (var i = 0; i < 1200; i++) {
      html +=
        '<tr><td>SK' + (100 + i) + '</td><td>ATL</td><td>LHR</td>' +
        '<td>' + (199 + (i % 400)) + '</td><td>' + (i % 7 ? 'eco' : 'biz') + '</td><td>cached</td></tr>';
    }
    tbody.innerHTML = html;
  }

  // ── defect: layout thrashing on scroll (read → write → read → write) ────
  window.addEventListener('scroll', function () {
    var cards = document.querySelectorAll('.card');
    for (var i = 0; i < cards.length; i++) {
      var h = cards[i].offsetHeight;          // forced style/layout recalc
      cards[i].style.transform = 'translateY(' + (h * 0.0001 * (window.scrollY % 10)) + 'px)'; // invalidate
    }
    var feed = document.querySelector('.feed');
    if (feed) feed.style.minHeight = document.body.scrollHeight * 0.0 + 120 + 'px';
  }, { passive: true });

  // ── defect: leak #2 — a growing listener stack + retained detached DOM ──
  setInterval(function () {
    var payload = new Array(200).join('x').repeat(120); // ~24KB string
    for (var i = 0; i < 25; i++) {
      window.__skyline.ledger.push(new FareCacheEntry('ATL-LHR', payload));
    }
    // re-bind a listener every tick, closing over a fresh closure
    window.addEventListener('resize', function onResize() { return payload.length; });
    window.__skyline.listeners += 1;

    // detached DOM: replace the card list but keep references to the old nodes
    var cards = document.querySelector('.cards');
    if (cards && window.__skyline.searches > 0) {
      var clone = cards.cloneNode(true);
      window.__skyline.orphans.push(clone);
    }
  }, 1000);

  // ── fares ───────────────────────────────────────────────────────────────
  function renderFares(fares) {
    var wrap = document.querySelector('.cards');
    if (!wrap) return;
    wrap.innerHTML = fares.map(function (f, i) {
      return (
        '<article class="card">' +
          '<img src="/shared/img/card-' + (i + 1) + '.png" alt="">' +
          '<div class="card-body">' +
            '<span class="badge">' + f.badge + '</span>' +
            '<div class="route">' + f.from + ' <span class="arrow">→</span> ' + f.to + '</div>' +
            '<div class="meta">Departs ' + f.departs + ' · ' + f.duration + '</div>' +
            '<div class="price"><span class="amount">$' + f.price + '</span><span class="meta">from</span></div>' +
            '<button class="btn btn-ghost" data-route="' + f.from + '-' + f.to + '">Select fare</button>' +
          '</div>' +
        '</article>'
      );
    }).join('');
    wrap.querySelectorAll('button[data-route]').forEach(function (b) {
      b.addEventListener('click', function () {
        if (window.__skylineAnalytics) {
          window.__skylineAnalytics.track('fare_select');
        }
        toast('Fare ' + b.dataset.route + ' held for 10 minutes');
      });
    });
  }

  function loadFares() {
    fetch(ORIGIN + '/api/fares?delay=350')
      .then(function (r) { return r.json(); })
      .then(function (j) { renderFares(j.fares); })
      .catch(function (e) { console.error('fares blew up', e); });
  }

  // ── defect: a 260ms synchronous block on every search (INP/TBT) ─────────
  function search() {
    var t0 = Date.now();
    while (Date.now() - t0 < 260) { Math.sqrt(Math.random()); }

    window.__skyline.searches++;
    var from = document.getElementById('from').value || 'ATL';
    var to = document.getElementById('to').value || 'LHR';
    var feed = document.getElementById('ops-feed');
    if (feed) {
      var li = document.createElement('li');
      li.innerHTML = '<span class="t">' + new Date().toISOString().slice(11, 19) + '</span><span>search ' + from + '→' + to + ' resolved (main thread)</span>';
      feed.prepend(li);
    }
    document.getElementById('result-count').textContent =
      (3 + Math.floor(Math.random() * 5)) + ' fares · ' + from + ' → ' + to;
    toast('Searching ' + from + ' → ' + to + '…');
  }

  // ── defect: TypeError on click (dataset.flight is undefined) ────────────
  function trackFlight() {
    var box = document.getElementById('track-result');
    var leg = box.dataset.flight.length;   // ← blows up: undefined.length
    box.textContent = 'Tracking leg ' + leg;
  }

  function toast(msg) {
    var host = document.getElementById('toasts');
    var el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    host.appendChild(el);
    setTimeout(function () { el.remove(); }, 3200);
  }

  // ── defect: two blocks injected above the fold, long after first paint ──
  function injectLate(html, className, delay) {
    setTimeout(function () {
      var holder = document.querySelector('main');
      var el = document.createElement('div');
      el.className = className;
      el.innerHTML = html;
      holder.insertBefore(el, holder.firstChild);
    }, delay);
  }
  injectLate('<strong>Fleet-wide:</strong> double miles on overnight sectors through November.', 'promo', 1200);
  injectLate('<span class="promo"><strong>Weather advisory 04:20Z:</strong> headwinds on the LHR corridor. ' +
    'Expect +18 min block time on sectors SK112–SK140. Rebooking is free until rotation close.</span>', 'advisory', 2600);

  // ── boot ────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    buildFareMatrix();
    loadFares();
    loadSurgePricing();

    document.getElementById('search-btn').addEventListener('click', search);
    document.getElementById('track-btn').addEventListener('click', trackFlight);

    // defect: images that 404
    document.querySelectorAll('[data-needs-map]').forEach(function (img) {
      img.src = '/shared/img/fleet-map.png';
    });
  });
})();
