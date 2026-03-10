# NewsScan - Deploy to Railway (Free, Permanent URL)

## What you get
- Permanent URL like `https://newsscan-production.up.railway.app`
- Accessible from ANY device, anywhere in the world
- 24/7 live news, prices, intelligence — no PC needs to be on
- Free tier: 500 hours/month (enough for always-on)

---

## Step 1 — GitHub (one time setup)

1. Go to **github.com** → sign up free if you don't have account
2. Click **New Repository** → name it `newsscan` → click Create
3. Upload these files to the repo (drag and drop on GitHub):
   - `cryptoscan.py`  ← rename from `cryptoscan_cloud.py`
   - `crypto_dashboard.html`
   - `requirements.txt`
   - `Procfile`

---

## Step 2 — Railway (free hosting)

1. Go to **railway.app** → sign up with your GitHub account
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your `newsscan` repo
4. Railway auto-detects Python and deploys in ~2 minutes

---

## Step 3 — Set your topics and API keys

In Railway dashboard → your project → **Variables** tab → add:

| Variable | Value | Required? |
|---|---|---|
| `NEWSSCAN_TOPICS` | `bitcoin,gold,oil,iran,israel,trump,federal reserve` | Optional (has defaults) |
| `NEWSSCAN_INTERVAL` | `15` | Optional |
| `GROQ_API_KEY` | your key from console.groq.com | Optional (for AI summary) |
| `NEWSAPI_KEY` | your key from newsapi.org | Optional |

---

## Step 4 — Get your permanent URL

In Railway dashboard → your project → **Settings** tab → **Domains**
→ Click **Generate Domain**
→ You get: `https://newsscan-xxxx.up.railway.app`

**Bookmark this. It never changes.**

Open it on your phone, add to home screen — done!

---

## Updating the app

When you want to update:
1. Upload new files to GitHub (drag and drop, same repo)
2. Railway auto-redeploys in ~1 minute

---

## Free tier limits

Railway free tier gives **$5 credit/month** which runs a small Python app
for ~500+ hours. NewsScan uses minimal memory (~50MB).

If you hit limits, upgrade to $5/month Hobby plan = unlimited hours.

---

## Troubleshooting

**App not loading?** Check Railway → Deployments → click latest → view logs

**No news showing?** Add topics in the sidebar and wait 15 seconds

**Prices not updating?** Binance WebSocket works on all networks
