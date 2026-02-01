// Paste this into the browser devtools console on a StreetEasy listing page.
// It prints a JSON blob you can copy into the dataset.
(() => {
  const listingUrl = location.href.split("?")[0];
  const title = document.title || "";
  const bodyText = (document.body?.innerText || "").replace(/\u00a0/g, " ");

  const sqftMatch = bodyText.match(/(\d[\d,]*)\s*ft²/);
  const sqft = sqftMatch ? parseInt(sqftMatch[1].replace(/,/g, ""), 10) : null;
  const sqftText = sqftMatch ? sqftMatch[0] : bodyText.includes("- ft²") ? "- ft²" : null;

  const photoImgEls = Array.from(document.querySelectorAll('img[alt^="photo"], img[alt^="Photo"]'));
  const photoSrcs = photoImgEls
    .map((img) => img.currentSrc || img.src || "")
    .filter(Boolean);

  const idsFromAlt = [];
  for (const src of photoSrcs) {
    const m = src.match(/photos\.zillowstatic\.com\/fp\/([a-f0-9]{32})/i);
    if (m) idsFromAlt.push(m[1].toLowerCase());
  }

  let uniqueIds = Array.from(new Set(idsFromAlt));
  if (!uniqueIds.length) {
    const html = document.documentElement?.innerHTML || "";
    uniqueIds = Array.from(
      new Set(
        Array.from(html.matchAll(/photos\.zillowstatic\.com\/fp\/([a-f0-9]{32})/gi)).map((m) =>
          m[1].toLowerCase(),
        ),
      ),
    );
  }

  const maxPhotos = 30;
  const photoIds = uniqueIds.slice(0, maxPhotos);

  const payload = {
    source: "streeteasy",
    listingUrl,
    title,
    sqft,
    sqftText,
    photoIdCountDetected: uniqueIds.length,
    photoIdCountUsed: photoIds.length,
    photoIds,
  };

  console.log(JSON.stringify(payload, null, 2));
})();
