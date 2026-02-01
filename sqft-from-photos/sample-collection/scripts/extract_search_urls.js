// Extract listing URLs from a StreetEasy search results page.
// Run this in the browser console on a search results page.
// Returns JSON array of listing URLs.
(() => {
  // Find all listing cards/links
  const links = Array.from(document.querySelectorAll('a[href*="/building/"], a[href*="/rental/"], a[href*="/sale/"]'));

  const urls = new Set();
  for (const link of links) {
    const href = link.href;
    // Filter to actual listing pages (not search filters, etc.)
    if (href.match(/streeteasy\.com\/(building|rental|sale)\/[^?]+$/)) {
      // Normalize URL (remove query params, trailing slashes)
      const normalized = href.split('?')[0].replace(/\/$/, '');
      urls.add(normalized);
    }
  }

  const urlList = Array.from(urls);
  console.log(`Found ${urlList.length} listing URLs`);
  console.log(JSON.stringify(urlList, null, 2));
  return urlList;
})();
