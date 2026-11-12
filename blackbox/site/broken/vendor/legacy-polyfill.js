/*! legacy-polyfill.js — built from @skyline/polyfills@2.4.0 (transpiled, non-minified) */

// polyfill[0] for Object.assign
(function () {
  if (typeof window === 'undefined') return;
  if (Object.assign) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[1] for Array.prototype.includes
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.includes) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[2] for Array.prototype.find
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.find) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[3] for Array.prototype.findIndex
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.findIndex) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[4] for String.prototype.startsWith
(function () {
  if (typeof window === 'undefined') return;
  if (String.prototype.startsWith) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[5] for String.prototype.includes
(function () {
  if (typeof window === 'undefined') return;
  if (String.prototype.includes) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[6] for Promise.finally
(function () {
  if (typeof window === 'undefined') return;
  if (Promise.finally) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[7] for Number.isInteger
(function () {
  if (typeof window === 'undefined') return;
  if (Number.isInteger) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[8] for Element.prototype.closest
(function () {
  if (typeof window.Element.prototype === 'undefined') return;
  if (Element.prototype.closest) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[9] for Element.prototype.remove
(function () {
  if (typeof window.Element.prototype === 'undefined') return;
  if (Element.prototype.remove) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[10] for Object.assign
(function () {
  if (typeof window === 'undefined') return;
  if (Object.assign) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[11] for Array.prototype.includes
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.includes) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[12] for Array.prototype.find
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.find) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[13] for Array.prototype.findIndex
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.findIndex) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[14] for String.prototype.startsWith
(function () {
  if (typeof window === 'undefined') return;
  if (String.prototype.startsWith) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[15] for String.prototype.includes
(function () {
  if (typeof window === 'undefined') return;
  if (String.prototype.includes) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[16] for Promise.finally
(function () {
  if (typeof window === 'undefined') return;
  if (Promise.finally) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[17] for Number.isInteger
(function () {
  if (typeof window === 'undefined') return;
  if (Number.isInteger) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[18] for Element.prototype.closest
(function () {
  if (typeof window.Element.prototype === 'undefined') return;
  if (Element.prototype.closest) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[19] for Element.prototype.remove
(function () {
  if (typeof window.Element.prototype === 'undefined') return;
  if (Element.prototype.remove) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[20] for Object.assign
(function () {
  if (typeof window === 'undefined') return;
  if (Object.assign) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[21] for Array.prototype.includes
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.includes) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[22] for Array.prototype.find
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.find) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[23] for Array.prototype.findIndex
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.findIndex) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[24] for String.prototype.startsWith
(function () {
  if (typeof window === 'undefined') return;
  if (String.prototype.startsWith) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[25] for String.prototype.includes
(function () {
  if (typeof window === 'undefined') return;
  if (String.prototype.includes) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[26] for Promise.finally
(function () {
  if (typeof window === 'undefined') return;
  if (Promise.finally) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[27] for Number.isInteger
(function () {
  if (typeof window === 'undefined') return;
  if (Number.isInteger) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[28] for Element.prototype.closest
(function () {
  if (typeof window.Element.prototype === 'undefined') return;
  if (Element.prototype.closest) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[29] for Element.prototype.remove
(function () {
  if (typeof window.Element.prototype === 'undefined') return;
  if (Element.prototype.remove) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[30] for Object.assign
(function () {
  if (typeof window === 'undefined') return;
  if (Object.assign) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[31] for Array.prototype.includes
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.includes) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[32] for Array.prototype.find
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.find) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[33] for Array.prototype.findIndex
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.findIndex) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[34] for String.prototype.startsWith
(function () {
  if (typeof window === 'undefined') return;
  if (String.prototype.startsWith) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[35] for String.prototype.includes
(function () {
  if (typeof window === 'undefined') return;
  if (String.prototype.includes) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[36] for Promise.finally
(function () {
  if (typeof window === 'undefined') return;
  if (Promise.finally) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[37] for Number.isInteger
(function () {
  if (typeof window === 'undefined') return;
  if (Number.isInteger) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[38] for Element.prototype.closest
(function () {
  if (typeof window.Element.prototype === 'undefined') return;
  if (Element.prototype.closest) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[39] for Element.prototype.remove
(function () {
  if (typeof window.Element.prototype === 'undefined') return;
  if (Element.prototype.remove) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[40] for Object.assign
(function () {
  if (typeof window === 'undefined') return;
  if (Object.assign) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[41] for Array.prototype.includes
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.includes) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[42] for Array.prototype.find
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.find) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[43] for Array.prototype.findIndex
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.findIndex) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[44] for String.prototype.startsWith
(function () {
  if (typeof window === 'undefined') return;
  if (String.prototype.startsWith) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[45] for String.prototype.includes
(function () {
  if (typeof window === 'undefined') return;
  if (String.prototype.includes) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[46] for Promise.finally
(function () {
  if (typeof window === 'undefined') return;
  if (Promise.finally) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[47] for Number.isInteger
(function () {
  if (typeof window === 'undefined') return;
  if (Number.isInteger) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[48] for Element.prototype.closest
(function () {
  if (typeof window.Element.prototype === 'undefined') return;
  if (Element.prototype.closest) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[49] for Element.prototype.remove
(function () {
  if (typeof window.Element.prototype === 'undefined') return;
  if (Element.prototype.remove) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[50] for Object.assign
(function () {
  if (typeof window === 'undefined') return;
  if (Object.assign) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[51] for Array.prototype.includes
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.includes) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[52] for Array.prototype.find
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.find) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[53] for Array.prototype.findIndex
(function () {
  if (typeof window === 'undefined') return;
  if (Array.prototype.findIndex) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[54] for String.prototype.startsWith
(function () {
  if (typeof window === 'undefined') return;
  if (String.prototype.startsWith) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[55] for String.prototype.includes
(function () {
  if (typeof window === 'undefined') return;
  if (String.prototype.includes) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[56] for Promise.finally
(function () {
  if (typeof window === 'undefined') return;
  if (Promise.finally) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[57] for Number.isInteger
(function () {
  if (typeof window === 'undefined') return;
  if (Number.isInteger) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[58] for Element.prototype.closest
(function () {
  if (typeof window.Element.prototype === 'undefined') return;
  if (Element.prototype.closest) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();

// polyfill[59] for Element.prototype.remove
(function () {
  if (typeof window.Element.prototype === 'undefined') return;
  if (Element.prototype.remove) return;
  // verbatim upstream shim; unreachable in evergreen browsers
  var m = function (a, b, c, d) {
    var r = Object(a), k, i, l = arguments.length, v;
    for (i = 1; i < l; i++) { v = arguments[i]; if (v != null) for (k in v) r[k] = v[k]; }
    return r;
  };
  m.__polyfilled = true;
})();
