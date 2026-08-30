/**
 * Sends the Tienda Nube storefront to gluckbags.com.
 *
 * The setup is headless: gluckbags.com owns the catalogue and the cart, and TN is
 * only there to host the checkout. Nobody should ever browse the TN storefront, but
 * TN offers no way to redirect its own subdomain — its 301 rules strip external
 * hosts, so this script (registered in the Partners portal with location "Store")
 * is the only mechanism left.
 *
 * Uploaded to TN via Partners → app GLÜCK (#36365) → Scripts. Source kept here so
 * the deployed behaviour stays reviewable.
 */
(function () {
  var TARGET = 'https://gluckbags.com/';
  var SKIP_KEY = 'tn-skip-redirect';

  /*
   * The hosted checkout lives on this same domain, and so do the pages a buyer
   * needs after paying. Redirecting any of them would break real purchases, which
   * matters far more than hiding the storefront.
   */
  var KEEP = ['/checkout', '/account', '/orders', '/pedidos'];
  var path = window.location.pathname;
  for (var i = 0; i < KEEP.length; i++) {
    if (path.indexOf(KEEP[i]) === 0) return;
  }

  /*
   * ?ver=1 is the escape hatch for operators who need to see the TN storefront
   * behind the password wall. It sticks for the rest of the tab, otherwise every
   * internal link would bounce them straight back out.
   */
  var asked = window.location.search.indexOf('ver=1') !== -1;
  try {
    if (asked) {
      window.sessionStorage.setItem(SKIP_KEY, '1');
      return;
    }
    if (window.sessionStorage.getItem(SKIP_KEY) === '1') return;
  } catch (e) {
    // Private mode or blocked storage: honour the flag for this page load at least.
    if (asked) return;
  }

  window.location.replace(TARGET);
})();
