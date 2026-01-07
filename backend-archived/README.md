# Backend (ARCHIVED)

**This backend is no longer required for the extension to work.**

The extension has been redesigned to use a fully client-side architecture with precompiled static data. All crime statistics and NTA boundary lookups now happen locally in the browser.

## Why Was This Archived?

1. **Cost**: Running a backend server to serve all users is expensive and unnecessary
2. **Privacy**: User locations no longer need to be sent to any server
3. **Speed**: Local lookups are faster than API calls
4. **Simplicity**: No infrastructure to maintain

## What Replaced It?

- `scripts/compile-data.js` - A standalone Node.js script that fetches data from NYC Open Data and compiles it into static JSON files
- `extension/data/*.json` - Precompiled NTA boundaries and crime statistics
- `extension/lib/geo-utils.js` - Client-side point-in-polygon NTA lookup

## If You Still Want to Use This

The backend code still works if you want to run your own instance:

```bash
cd backend-archived
npm install
cp .env.example .env
# Configure .env with your PostgreSQL/PostGIS credentials
npm run db:init
npm run pipeline
npm start
```

However, for most use cases, the precompiled static data approach is recommended.
