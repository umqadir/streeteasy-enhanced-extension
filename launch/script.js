/* Launch page wiring (no build step). */

const CHROME_WEB_STORE_URL = ''; // Set this to your Chrome Web Store listing URL.

function wireStoreLinks() {
  const links = Array.from(document.querySelectorAll('[data-store-link]'));
  const storeBlocks = Array.from(document.querySelectorAll('[data-install-store]'));
  const devBlocks = Array.from(document.querySelectorAll('[data-install-dev]'));

  for (const link of links) {
    if (!CHROME_WEB_STORE_URL) {
      link.removeAttribute('target');
      link.removeAttribute('rel');
      // Keep the CTA functional: scroll to install section.
      link.href = '#install';
      link.textContent = 'Install';
    } else {
      link.href = CHROME_WEB_STORE_URL;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = 'Add to Chrome';
    }
  }

  // Hide developer-mode install when a store URL exists.
  for (const el of storeBlocks) el.style.display = CHROME_WEB_STORE_URL ? '' : 'none';
  for (const el of devBlocks) el.style.display = CHROME_WEB_STORE_URL ? 'none' : '';
}

function setYear() {
  const el = document.getElementById('year');
  if (el) el.textContent = String(new Date().getFullYear());
}

function main() {
  setYear();
  wireStoreLinks();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', main);
} else {
  main();
}
