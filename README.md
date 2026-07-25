# AM95 Tracker

Tracks price/stock for your Air Max 95 watchlist and detects newly listed
AM95s on retailer category pages. Currently set up for one pair: **Fresh
Mint (HJ5996-005)** across JD Sports, size?, END, Footpatrol, Seven Store,
and Nike.

## How it works
- `scraper.py` checks each watchlist URL for price/stock, and each
  category page for new listings not seen before.
- Runs automatically every 30 minutes via GitHub Actions (`.github/workflows/scrape.yml`) — free, no server needed.
- Results are saved as JSON in `data/` and committed back to the repo.
- `index.html` is a dashboard that reads that JSON and displays it —
  hosted free via GitHub Pages.

## Setup (one-time, ~10 minutes)

1. **Create a GitHub repo** (e.g. `am95-tracker`) — can be private.
2. **Upload all these files** to the repo, keeping the folder structure
   (`.github/workflows/scrape.yml` must stay in that exact path).
3. **Enable Actions**: repo → Settings → Actions → General → allow
   "Read and write permissions" under Workflow permissions (needed so
   it can commit data back).
4. **Trigger the first run manually**: repo → Actions tab →
   "AM95 Tracker Scrape" → Run workflow. Wait ~1 min, check that
   `data/prices.json` and `data/new_releases.json` got created/updated.
5. **Enable GitHub Pages**: repo → Settings → Pages → Source: deploy
   from branch → select `main` / root. You'll get a URL like
   `https://yourusername.github.io/am95-tracker/` — that's your
   dashboard.

From then on it checks automatically every 30 minutes and the dashboard
always shows the latest data when you open it.

## Adding more pairs later
Edit `watchlist.json` — add another object to `"pairs"` with the name,
SKU, and each retailer URL. No code changes needed.

## Note on EU retailers
asphaltgold, Solebox, Footshop, and 43einhalb are currently set to
Sneakerjagers redirect links rather than direct product pages (couldn't
find the direct URLs). These will open fine in a browser, but the
scraper's price/stock detection may be less reliable on them since it's
reading a redirect page rather than the retailer's own site. Swap in
direct URLs when you have them for more reliable tracking.

## Known limitation
The scraper uses generic price/stock detection (regex for £/$/€ prices,
keyword matching for "sold out" etc.) rather than custom selectors per
site, since retailer page structures change often. If a retailer's price
or stock shows as wrong/missing, check `parse_product_page()` in
`scraper.py` — it may need a small site-specific tweak. Flag it to me
and I can fix the selector.
