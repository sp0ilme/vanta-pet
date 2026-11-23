# Vanta pet app

A small consent-based web app. A visitor lands on a page that explains, clearly, that authorizing will change their X display name, bio, profile picture, and banner. They see a preview, then tap **Authorize with X**. X’s own permission screen is where they approve or cancel.

- If they approve, the app sets their display name to `VANTA's MUTT #` plus a unique random 4-digit number, sets the bio to `Property of Goddess Vanta | @simp_4_vanta Owned • Obey • $end`, and applies the bundled profile picture and banner.
- If they cancel on X, nothing is changed.

Issued numbers are stored in SQLite so two people never share the same number. A returning visitor keeps the number they were already given.

## What you need

1. An X developer account and an app with **Read and Write** user-authentication permissions.
2. A host for the site. These files are set up for [Render](https://render.com/) (free web service is enough to start).
3. Paid X API access. See [Which X API access is required?](#which-x-api-access-is-required) before you spend money or assume a free tier will work.

## 1. Create an X developer account and app

1. Open the [X developer sign-up](https://developer.x.com/en/apply-for-access) and the [Developer Console](https://console.x.com) (docs: [How to acquire an API Key and Secret](https://docs.x.com/resources/fundamentals/authentication/oauth-1-0a/api-key-and-secret)).
2. Create a Project, then a developer App inside it.
3. When the console shows your **API Key** and **API Secret** (also called Consumer Key and Secret), save them somewhere secure. X only shows the secret once.
4. Open the App’s **User authentication settings** (sometimes labelled “User authentication set up”):
   - **App permissions:** **Read and Write**. Read-only cannot change a profile.
   - **Type of App:** Web App.
   - **Callback URI / Redirect URL:** the public URL of this app plus `/callback`. Examples:
     - Production: `https://your-service.onrender.com/callback`
     - Local testing: `http://127.0.0.1:8000/callback`
   - Add a Website URL if the form requires one (your Render URL is fine).
5. Save. The callback URL you enter here **must** match the `CALLBACK_URL` environment variable you set later, character for character.

Official walkthrough of the 3-legged OAuth 1.0a flow this app uses: [Obtaining access tokens using 3-legged OAuth flow](https://docs.x.com/fundamentals/authentication/oauth-1-0a/obtaining-user-access-tokens).

## 2. Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `X_API_KEY` | Yes | API Key (consumer key) of your X app |
| `X_API_SECRET` | Yes | API Secret (consumer secret) |
| `CALLBACK_URL` | Yes in production | Exact callback URI registered on the X app, e.g. `https://your-service.onrender.com/callback` |
| `SECRET_KEY` | Yes in production | Random secret used to sign session cookies. Render can generate this for you. |
| `ADMIN_PASSWORD` | Yes for `/admin` | Password for the owner dashboard at `/admin`. Lists everyone who authorized. |
| `DATA_DIR` | No | Directory for the SQLite file. Defaults to `./data`. |
| `PORT` | No | Listen port. Render sets this automatically. Local default is `8000`. |

Copy `.env.example` if you run the app locally. Do not commit a real `.env` file.

The owner dashboard is at `/admin`. Sign in with `ADMIN_PASSWORD` to see everyone who authorized, including their assigned number, username, user id, display name, and when they opted in.

## 3. Deploy on Render

1. Push this project to a GitHub (or GitLab / Bitbucket) repository, or use Render’s “Public Git repository” option if you upload a repo that contains these files.
2. In the [Render dashboard](https://dashboard.render.com/), choose **New + → Web Service** and point it at that repo. You can also apply `render.yaml` with **New + → Blueprint**.
3. Runtime: **Python**. Build command: `pip install -r requirements.txt`. Start command: `gunicorn --bind 0.0.0.0:$PORT app:app` (this is also in `Procfile`).
4. Set the environment variables in the service’s **Environment** tab:
   - `X_API_KEY`
   - `X_API_SECRET`
   - `CALLBACK_URL` — `https://<your-service-name>.onrender.com/callback` after you know the service URL. You can deploy once, copy the URL, add the callback on both Render and the X app, then redeploy or just restart.
   - `SECRET_KEY` — a long random string, or leave Render’s Blueprint to generate one.
   - `ADMIN_PASSWORD` — a strong password for the owner dashboard at `/admin`.
5. Deploy. Open the site, read the consent text, and confirm the preview looks right. Do a test authorization with an account you control.

### Persistent numbers on Render

Render’s free web service filesystem is **ephemeral**. The SQLite file in `data/vanta.db` is wiped when the service is redeployed or restarted on a new instance. After a wipe, a new visitor might receive a number that someone else had before the reset (a returning visitor would also get a new number).

To keep numbers unique across deploys, add a [persistent disk](https://render.com/docs/disks) mounted at `/var/data` (or similar) and set `DATA_DIR=/var/data`. Persistent disks are a paid Render feature.

## 4. Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export FLASK_ENV=development
python app.py
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/).

Without `X_API_KEY` and `X_API_SECRET`, the landing page still renders. Tapping **Authorize with X** shows an error page and does not contact X — that is intentional.

To test the full OAuth flow locally, set the four environment variables, register `http://127.0.0.1:8000/callback` on your X app, and use a test account.

## Which X API access is required?

This is what official docs say as of 19 November 2026. Do not assume a free tier will work.

**Current plan model.** The official pricing page describes **pay-per-usage** pricing with no subscriptions: you buy credits in the Developer Console and they are deducted as requests are made. See [X API pay-per-usage pricing and credits](https://docs.x.com/x-api/getting-started/pricing). That page is the source of record for current rates; it also points to [developer.x.com/#pricing](https://developer.x.com/#pricing) and the [Developer Console](https://console.x.com).

**What the pricing page lists about profiles.** Under read operations it includes **Profile Update: Read** at **$0.005 per resource**. The write-operations table on the same page does **not** list a dedicated “Profile Update: Write” line. Under webhook events, `profile.update.*` is billed at $0.005 per event. The page does **not** explicitly say that the legacy v1.1 `account/update_profile*` endpoints are included in pay-per-use, nor what a single profile-name, avatar, or banner write costs.

**v1.1 vs v2.** Official versioning marks **v1.1 as Legacy** (“Limited support, minimal updates”) and tells new projects to use v2: [Versioning](https://docs.x.com/x-api/fundamentals/versioning). This app still calls the v1.1 endpoints because they are the documented way to set name, bio, avatar, and banner with OAuth 1.0a user context, and they are what a working reference of this flow uses:

- `POST https://api.twitter.com/1.1/account/update_profile.json`
- `POST https://api.twitter.com/1.1/account/update_profile_image.json`
- `POST https://api.twitter.com/1.1/account/update_profile_banner.json`

Those calls are made only after a successful [3-legged OAuth 1.0a](https://docs.x.com/fundamentals/authentication/oauth-1-0a/obtaining-user-access-tokens) approval (`oauth/request_token` → `oauth/authorize` → `oauth/access_token`), as described in [OAuth 1.0a](https://docs.x.com/resources/fundamentals/authentication/oauth-1-0a/overview).

Official docs also list v2 operations named [Update profile](https://docs.x.com/x-api/users/update-profile) (`PUT /2/users/me`), [Update profile image](https://docs.x.com/x-api/users/update-profile-image) (`PUT /2/users/me/profile_image`), and [Update profile banner](https://docs.x.com/x-api/users/update-profile-banner) (`PUT /2/users/me/profile_banner`). The markdown bodies of those pages were empty stubs when this README was written, and the public [OpenAPI spec](https://docs.x.com/openapi.json) included `GET /2/users/me` but not those PUT operations, so this app does not rely on them.

**Practical takeaway:** create the app, set Read and Write, buy whatever credits the Developer Console requires for your project, and confirm against the live pricing page and console that profile updates are allowed on your account **before** you share the link widely. Official docs do not currently spell out a Free/Basic/Pro matrix for these v1.1 endpoints (those subscription names are not how the [current pricing page](https://docs.x.com/x-api/getting-started/pricing) is structured). If the console or an API error says the endpoint is unavailable on your access level, check X’s current products in the console rather than guessing.

## Consent and account risk

Keep the landing-page wording honest: people must see that authorizing will change their display name, bio, picture, and banner, and that cancelling on X leaves the profile alone. If a lot of people report the app, X can restrict or suspend the developer app or the account that owns it.

## Project layout

```
app.py                 Flask app (OAuth 1.0a + profile updates)
requirements.txt
Procfile               Gunicorn start command for Render
render.yaml            Render Blueprint
templates/             Landing, success, cancel, error, and admin pages
static/style.css
static/assets/profile.jpg
static/assets/banner.jpg   Already 1500×500 (X’s banner size)
data/vanta.db          Created at runtime; issued numbers
```
