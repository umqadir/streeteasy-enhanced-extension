/* Launch page wiring (no build step). */

function wireInstallLinks() {
  for (const link of document.querySelectorAll('[data-store-link]')) {
    link.href = '#install';
    link.removeAttribute('target');
    link.removeAttribute('rel');
    link.textContent = 'Install';
  }
}

function setYear() {
  const el = document.getElementById('year');
  if (el) el.textContent = String(new Date().getFullYear());
}

function main() {
  setYear();
  wireInstallLinks();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', main);
} else {
  main();
}
