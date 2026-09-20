# Setup Instructions

This checklist gets the app deployed so you have a working starting point.
Budget ~15 minutes. **The actual take-home task is in [EXERCISE.md](./EXERCISE.md)**
— read it before you start coding.

## Prerequisites

- GitHub account
- Render account (free Hobby plan): [render.com](https://render.com)
- An LLM provider API key (e.g. OpenAI) for the exercise — a few cents of usage
  is plenty

You do not need a credit card for Render. You do not need a separate database
provider.

---

## Step 1: Fork the Repository

You will receive access to the template repository. Fork it to your personal GitHub account.

---

## Step 2: Deploy to Render

1. Go to [dashboard.render.com](https://dashboard.render.com)
2. Sign up / Log in with your **GitHub account**
3. Click **New** → **Blueprint**
4. Select your **forked** repository
5. Render reads `render.yaml` and will create:
   - a Free web service (the app)
   - a Free Postgres database
   - `DATABASE_URL` already connected
6. Confirm both resources are on the **Free** plan
7. Click **Apply** / **Deploy**
8. Wait for the first deploy (~3–5 minutes). Your app will be at `https://something.onrender.com`

Render’s free Postgres expires **30 days** after creation. That is enough for this interview.

If Blueprint is unavailable, create **Postgres** (Free) and a **Web Service** (Docker, Free) separately, then set `DATABASE_URL` on the web service to the database’s **Internal Database URL**.

---

## Step 3: Verify Everything Works

1. Visit your deployed URL
2. The first request on a free instance can take about **one minute** (Render may show a loading page while the service wakes up)
3. Check the **bottom status bar** shows Database connected (green)
4. Click **Initialize Database** to seed sample data
5. Select a clinician to log in
6. You should see a list of patients
7. Click a patient → see their visits
8. Click a visit → see the visit notes, rendered as plain text. Making the
   medications in them light up is the exercise.
9. Back on the dashboard, **RxNorm Catalog** shows the reference concepts your
   detection resolves against. Click **Import full RxNorm** to pull the whole
   ~15k-concept ingredient set from RxNav (a couple of minutes, runs in the
   background) — that is the catalog your matching has to work against.

---

## Step 4: Read the Exercise

Open **[EXERCISE.md](./EXERCISE.md)**. You'll use an LLM to detect medications in
visit notes and fuzzy-match them to RxNorm. The LLM path needs an API key — add
your own to `backend/.env` (see `backend/.env.example`):

```bash
LLM_PROVIDER=openai
LLM_MODEL=gpt-4.1-mini
LLM_API_KEY=sk-...
```

For deployment, set the same variables in the Render web service's **Environment**
settings.

---

## Step 5: Submit

Reply to your interview email with:

- Your deployed Render URL (with the feature working)
- A link to your forked repo
- A short video recording explaining your decisions and approach

See **Deliverables** in [EXERCISE.md](./EXERCISE.md). A coding-agent transcript is a bonus.

---

## Troubleshooting

**First load is slow or a blank loading page:**
- Free Render web services sleep after 15 minutes idle and take about a minute to wake. Wait, then refresh.

**DB Connection fails (red status bar):**
- Wait until the Blueprint has finished creating both the web service and Postgres
- Expand the status bar and click **Retry Connection**
- If you created resources by hand, `DATABASE_URL` must be the **internal** URL from the Render Postgres dashboard, same region as the web service

**Blueprint fails (already have a free database):**
- Render allows one free Postgres per workspace. Delete or upgrade the existing free database, then retry

**Build fails:**
- Check Render deploy logs
- Ensure you have not deleted the `Dockerfile` or `frontend/` / `backend/` folders

**"Initialize Database" fails:**
- Expand the status bar and click Retry Connection
- Confirm the Postgres instance is running in the Render dashboard

**Refreshing `/dashboard` or a patient URL 404s:**
- You should get the app, not a 404. If you do, the Docker image may not have copied the frontend build — check that `npm run build` succeeded in the Render logs.

---

## Ready for Interview

Once setup is complete:

- Push changes to your repo
- Render auto-deploys on every push
- We will review your work at the same URL

Good luck!
