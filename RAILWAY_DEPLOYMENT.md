# SecretaryAI — Deployment & Operations Guide

> **If you are the owner doing the deploy, use
> [`docs/DEPLOY_CHECKLIST.md`](docs/DEPLOY_CHECKLIST.md) instead.** It is the
> same deployment written as numbered clicks, with the exact environment
> variables, the order the database files must be applied in, and a script
> that proves the result is actually working. This file is the architectural
> overview.

This guide is tailored to your infrastructure choices: **Railway** for hosting, **QuickBooks Online** integration focus, and an **Internal Tool** deployment strategy for the Mobile App.

## 1. Hosting the Backend on Railway

Railway is an excellent choice for deploying Dockerized monorepos. SecretaryAI requires **five** services to run: a Postgres Database, a Redis instance, the FastAPI server, the Celery **worker**, and the Celery **beat scheduler**.

> The beat scheduler is not optional and was missing from an earlier version of
> this guide. The worker only executes work that something enqueues; beat is
> what enqueues it on a schedule. Without beat, nothing in
> `celery_app.py`'s `beat_schedule` ever fires — no 7 AM morning briefing, no
> 2 AM overnight scan, no 4-hourly sync check. The deployment looks healthy
> and silently does nothing, which defeats GA exit criterion 7.

### Setup Steps in Railway:
1. **Create the Databases**:
   - In your Railway project, click **New -> Database -> Redis**
   - In your Railway project, click **New -> Database -> PostgreSQL** (If you are not using Supabase's hosted DB)
2. **Deploy the API Service**:
   - Click **New -> GitHub Repo** and select `SecretaryAI`.
   - In the service settings, set the **Root Directory** to `/backend`.
   - Railway will automatically detect the `Dockerfile` and build it.
   - Go to the **Variables** tab and paste your `.env` variables. Ensure you map `REDIS_URL` to the internal Railway Redis URL (e.g., `${{Redis.REDIS_URL}}`).
   - In settings, generate a public domain for this service (e.g., `api-secretaryai.up.railway.app`). This will be your `EXPO_PUBLIC_API_URL` for the mobile app.
3. **Deploy the Celery Worker Service**:
   - Click **New -> GitHub Repo** and select `SecretaryAI` again.
   - Set the **Root Directory** to `/backend`.
   - Go to **Settings -> Deploy -> Custom Start Command** and set it to: 
     `celery -A celery_app worker --loglevel=info --concurrency=2`
   - Copy the same environment variables from the API service over to this one.
4. **Deploy the Celery Beat Scheduler**:
   - Click **New -> GitHub Repo** and select `SecretaryAI` a third time.
   - Set the **Root Directory** to `/backend`.
   - **Settings -> Deploy -> Custom Start Command**:
     `celery -A celery_app beat --loglevel=info`
   - Copy the same environment variables again. Do not generate a domain.
   - Run exactly **one** beat instance. Two schedulers mean every briefing is
     sent twice.

### A note on the port

The API container binds to `$PORT` when the platform provides one and falls
back to 8000 otherwise, so Railway's assigned port is honoured automatically.
Nothing needs configuring. (It used to hardcode 8000, which is unroutable on
Railway and fails the health check with no obvious cause.)

## 2. QuickBooks Online Integration (Production)
Since your focus is **QuickBooks Online**, you will bypass the Conductor setup entirely.
- Go to the [Intuit Developer Portal](https://developer.intuit.com/app/developer/myapps).
- Under your production app settings, set your **Redirect URI** to match your Railway deployment: `https://api-secretaryai.up.railway.app/auth/qbo/callback`.
- Copy your Production Client ID and Client Secret into your Railway API service environment variables (`INTUIT_CLIENT_ID`, `INTUIT_CLIENT_SECRET`).
- Set `INTUIT_ENVIRONMENT=production` in the Railway variables.

## 3. Mobile App (Internal Tool Strategy)
Since the mobile app is strictly for internal company use, you do **not** need to deal with the complexities of the Apple App Store or Google Play Store reviews. 

We recommend using **EAS (Expo Application Services) Internal Distribution**:
1. Run `npm install -g eas-cli` on your local machine.
2. Inside the `/mobile` directory, run `eas login` and `eas build:configure`.
3. Create an `eas.json` file to define an internal distribution profile:
   ```json
   {
     "build": {
       "preview": {
         "distribution": "internal",
         "android": { "buildType": "apk" },
         "ios": { "simulator": false }
       }
     }
   }
   ```
4. Run `eas build --profile preview --platform all`.
5. Expo will provide a QR code and a direct download link for the `.apk` (Android) and the Ad-Hoc provisioning profile link for iOS devices. Your team can install the app directly on their phones via this link.
