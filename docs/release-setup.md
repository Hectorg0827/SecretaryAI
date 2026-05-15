# Setting up GitHub Releases (one-time)

This is the only setup needed before the automated installers will work.
Do this once, then every `git tag v1.x.x` produces Mac + Windows installers automatically.

---

## 1. Generate the Tauri signing key (2 minutes)

The signing key lets the auto-updater verify that updates come from you.

```bash
# Install the Tauri CLI if you don't have it
cargo install tauri-cli --version "^2"

# Generate a key pair (save the output somewhere safe)
cargo tauri signer generate -w ~/.tauri/secretaryai.key
```

It prints two values — copy them somewhere safe:
- **Private key** (long base64 string)
- **Public key** (shorter base64 string)

---

## 2. Add GitHub repository secrets

Go to: **GitHub repo → Settings → Secrets and variables → Actions → Secrets**

Add these secrets:

| Secret name | Value |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | The private key from step 1 |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | The password you chose (or blank if none) |

**Optional — for code-signing (removes "untrusted app" warnings):**

| Secret name | Value |
|---|---|
| `APPLE_CERTIFICATE` | Base64-encoded `.p12` Apple Developer certificate |
| `APPLE_CERTIFICATE_PASSWORD` | Certificate password |
| `APPLE_SIGNING_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_ID` | Your Apple ID email |
| `APPLE_PASSWORD` | App-specific password from appleid.apple.com |
| `APPLE_TEAM_ID` | Your Apple Developer Team ID |

---

## 3. Add GitHub repository variables

Go to: **GitHub repo → Settings → Secrets and variables → Actions → Variables**

| Variable name | Value |
|---|---|
| `API_BASE_URL` | `https://api.secretaryai.com` (or your Railway URL) |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_ANON_KEY` | Your Supabase anon key |

---

## 4. Paste the public key into tauri.conf.json

Open `desktop/src-tauri/tauri.conf.json` and replace the placeholder:

```json
"updater": {
  "pubkey": "PASTE_YOUR_PUBLIC_KEY_HERE",
  ...
}
```

Commit and push.

---

## 5. Publish your first release

```bash
git tag v1.0.0
git push origin v1.0.0
```

GitHub Actions will:
1. Build the Mac and Windows installers (~15 min)
2. Create a GitHub Release with the files attached
3. Upload `latest.json` so the in-app updater knows there's a new version

Future releases: just push a new tag. Users with the app installed get a notification and one-click update.
