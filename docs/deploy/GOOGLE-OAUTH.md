# Google sign-in — setup

The code is complete (`auth/oidc.py`, `web/routes/auth.py`): PKCE, signature + `aud`/`iss`/`exp`
checks, `email_verified` enforced, identity keyed on Google `sub`. A first Google sign-in
**creates** the account, so the same button serves sign-up (`/signup`) and sign-in (`/login`).
Until the two credentials below are set, `/login` shows a "not configured" notice and `/signup`
hides the button.

## 1. Google Cloud Console (once)

1. https://console.cloud.google.com → create or pick a project (e.g. "MiHomes").
2. **APIs & Services → OAuth consent screen** (Google Auth Platform → Branding / Audience):
   - User type **External**, app name "MiHomes", support email, developer email.
   - Scopes: `openid`, `email`, `profile` only — these are non-sensitive, so no Google review.
   - While **Publishing status = Testing**, only listed **Test users** can sign in (max 100).
     Add every address that should work. Press **Publish app** when opening to the public.
3. **Credentials → Create credentials → OAuth client ID**:
   - Application type **Web application**.
   - **Authorized redirect URIs** — must match `LANDING_BASE_URL` + `/auth/google/callback`
     byte for byte (scheme, host, no trailing slash):
     - `https://evo-dev.tail6765ef.ts.net/auth/google/callback`
     - `http://localhost:5000/auth/google/callback` (local dev, optional)
   - Authorized JavaScript origins: not needed (server-side flow).
4. Copy the **Client ID** and **Client secret**.

## 2. Environment

| Variable | Value |
|---|---|
| `GOOGLE_CLIENT_ID` | `….apps.googleusercontent.com` |
| `GOOGLE_CLIENT_SECRET` | `GOCSPX-…` — a secret; never commit it or paste it into chat |
| `LANDING_BASE_URL` | `https://evo-dev.tail6765ef.ts.net` — builds the redirect URI; defaults to `http://localhost:8080`, which Google will reject |

On evo-dev these go in `~/.mihomes/run/web.env`, then restart the app with the `mapfile`
pattern (never `env -i $(cat web.env)` — it word-splits values with spaces).

## 3. Verify

Open `https://evo-dev.tail6765ef.ts.net/login` → **Sign in with Google** → pick an account →
land on the dashboard (an existing user) or `/onboarding/` (a new one).

| Symptom | Cause |
|---|---|
| Google says `redirect_uri_mismatch` | Console URI ≠ `LANDING_BASE_URL/auth/google/callback` |
| Google says "access blocked … has not completed verification" | Address not in Test users |
| `/login?error=That email has a MiHomes password…` | Expected: see below |
| 400 "Invalid or expired sign-in request" | State cookie lost — took >10 min, or switched host mid-flow |

## Accounts with both methods

**Settings → Sign-in methods** (every role) lets a signed-in person add the other method:

| Starting from | Option | Rule |
|---|---|---|
| Google account | Add a password | Signed in within 10 minutes, else "Confirm with Google" with the *same* Google account |
| Password account | Connect Google | The Google account's email must match the MiHomes email |
| Password account | Change password | Needs the current password (throttled like login); signs out other devices |

Each change emails the account a security notice. Signing in with Google **without** being
signed in still refuses an address that has a password-only account, and says to sign in with
the password and connect Google from Settings. Linking there, rather than in the sign-in flow,
is what stops pre-hijacking: someone who registered your address with a password first cannot
have your later Google sign-in merge into their account.
