# SecretaryAI — Deployment & Operations Guide

This guide is tailored to your infrastructure choices: **Railway** for hosting, **QuickBooks Online** integration focus, and an **Internal Tool** deployment strategy for the Mobile App.

## 1. Hosting the Backend on Railway

Railway is an excellent choice for deploying Dockerized monorepos. SecretaryAI requires four services to run: a Postgres Database, a Redis instance, the FastAPI server, and the Celery Background Workers.

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
