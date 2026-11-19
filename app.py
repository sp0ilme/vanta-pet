"""Consent-based profile rewrite app for X.

Visitors approve access on X's own OAuth 1.0a screen. Only then does the
app call the legacy v1.1 profile endpoints to update display name, bio,
avatar, and banner. Cancelling on X leaves the profile unchanged.
"""

from __future__ import annotations

import base64
import logging
import os
import random
import sqlite3
import secrets
from functools import lru_cache
from pathlib import Path

from flask import (
    Flask,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from requests_oauthlib import OAuth1Session

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "vanta.db"

DISPLAY_NAME_PREFIX = "VANTA's MUTT #"
BIO = "Property of Goddess Vanta | @simp_4_vanta Owned • Obey • $end"
PROFILE_IMAGE_PATH = BASE_DIR / "static" / "assets" / "profile.jpg"
BANNER_IMAGE_PATH = BASE_DIR / "static" / "assets" / "banner.jpg"
# "VANTA's MUTT #0000" is 18 characters; X's current display-name limit is 50.
MAX_DISPLAY_NAME_LEN = 50
NUMBER_MIN = 0
NUMBER_MAX = 9999

OAUTH_REQUEST_TOKEN_URL = "https://api.x.com/oauth/request_token"
OAUTH_AUTHORIZE_URL = "https://api.x.com/oauth/authorize"
OAUTH_ACCESS_TOKEN_URL = "https://api.x.com/oauth/access_token"
UPDATE_PROFILE_URL = "https://api.twitter.com/1.1/account/update_profile.json"
UPDATE_PROFILE_IMAGE_URL = (
    "https://api.twitter.com/1.1/account/update_profile_image.json"
)
UPDATE_PROFILE_BANNER_URL = (
    "https://api.twitter.com/1.1/account/update_profile_banner.json"
)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["X_API_KEY"] = os.environ.get("X_API_KEY", "").strip()
app.config["X_API_SECRET"] = os.environ.get("X_API_SECRET", "").strip()
app.config["CALLBACK_URL"] = os.environ.get("CALLBACK_URL", "").strip()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") != "development"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("vanta")


def configured() -> bool:
    return bool(app.config["X_API_KEY"] and app.config["X_API_SECRET"])


def callback_url() -> str:
    if app.config["CALLBACK_URL"]:
        return app.config["CALLBACK_URL"]
    return request.url_root.rstrip("/") + "/callback"


def get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS issued_numbers (
            number INTEGER PRIMARY KEY,
            twitter_user_id TEXT NOT NULL UNIQUE,
            screen_name TEXT,
            display_name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    return conn


def assign_display_name(twitter_user_id: str, screen_name: str | None) -> str:
    """Reuse a user's existing number, otherwise issue a unique 4-digit one."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT number, display_name FROM issued_numbers WHERE twitter_user_id = ?",
            (twitter_user_id,),
        ).fetchone()
        if row:
            return row["display_name"]

        used = {
            r[0] for r in conn.execute("SELECT number FROM issued_numbers").fetchall()
        }
        available = [n for n in range(NUMBER_MIN, NUMBER_MAX + 1) if n not in used]
        if not available:
            raise RuntimeError("All 4-digit numbers have already been issued.")

        for _ in range(25):
            number = random.choice(available)
            display_name = f"{DISPLAY_NAME_PREFIX}{number:04d}"
            if len(display_name) > MAX_DISPLAY_NAME_LEN:
                raise RuntimeError("Generated display name exceeds X's 50-character limit.")
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO issued_numbers
                            (number, twitter_user_id, screen_name, display_name)
                        VALUES (?, ?, ?, ?)
                        """,
                        (number, twitter_user_id, screen_name, display_name),
                    )
                return display_name
            except sqlite3.IntegrityError:
                available.remove(number)
                if not available:
                    break
        raise RuntimeError("Could not assign a unique display-name number.")
    finally:
        conn.close()


@lru_cache(maxsize=2)
def _b64_file(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def oauth_session(
    resource_owner_key: str | None = None,
    resource_owner_secret: str | None = None,
    verifier: str | None = None,
    callback_uri: str | None = None,
) -> OAuth1Session:
    return OAuth1Session(
        client_key=app.config["X_API_KEY"],
        client_secret=app.config["X_API_SECRET"],
        resource_owner_key=resource_owner_key,
        resource_owner_secret=resource_owner_secret,
        verifier=verifier,
        callback_uri=callback_uri,
    )


def update_x_profile(access: OAuth1Session, display_name: str) -> None:
    profile = access.post(
        UPDATE_PROFILE_URL,
        data={
            "name": display_name,
            "description": BIO,
            "skip_status": "true",
        },
        timeout=30,
    )
    if profile.status_code != 200:
        raise RuntimeError(
            f"Updating name and bio failed ({profile.status_code}): {profile.text[:500]}"
        )

    image = access.post(
        UPDATE_PROFILE_IMAGE_URL,
        data={
            "image": _b64_file(str(PROFILE_IMAGE_PATH)),
            "skip_status": "true",
        },
        timeout=60,
    )
    if image.status_code != 200:
        raise RuntimeError(
            f"Updating profile picture failed ({image.status_code}): {image.text[:500]}"
        )

    banner = access.post(
        UPDATE_PROFILE_BANNER_URL,
        data={"banner": _b64_file(str(BANNER_IMAGE_PATH))},
        timeout=60,
    )
    # Twitter historically returns 200 or 201 for a successful banner upload.
    if banner.status_code not in (200, 201):
        raise RuntimeError(
            f"Updating banner failed ({banner.status_code}): {banner.text[:500]}"
        )


@app.get("/")
def index():
    return render_template(
        "index.html",
        preview_name=f"{DISPLAY_NAME_PREFIX}????",
        bio=BIO,
    )


@app.get("/authorize")
def authorize():
    if not configured():
        return (
            render_template(
                "error.html",
                title="App not configured yet",
                message=(
                    "This site does not have X API keys yet, so it cannot send you "
                    "to X to authorize. The owner needs to set the X_API_KEY and "
                    "X_API_SECRET environment variables, plus a CALLBACK_URL that "
                    "matches the callback registered on the X developer app."
                ),
            ),
            503,
        )

    try:
        oauth = oauth_session(callback_uri=callback_url())
        token = oauth.fetch_request_token(OAUTH_REQUEST_TOKEN_URL, timeout=20)
    except Exception as exc:
        log.exception("Failed to obtain a request token")
        return (
            render_template(
                "error.html",
                title="Could not start authorization",
                message=(
                    "X did not issue a request token. Check that the API key and "
                    "secret are correct, the app has Read and Write permissions, "
                    "and the callback URL is registered on the developer app."
                ),
                detail=str(exc),
            ),
            502,
        )

    session.clear()
    session["request_token"] = token.get("oauth_token")
    session["request_token_secret"] = token.get("oauth_token_secret")
    session["oauth_callback_confirmed"] = token.get("oauth_callback_confirmed")
    auth_url = oauth.authorization_url(OAUTH_AUTHORIZE_URL)
    return redirect(auth_url)


@app.get("/callback")
def callback():
    if request.args.get("denied"):
        session.clear()
        return render_template("cancel.html")

    if not configured():
        return (
            render_template(
                "error.html",
                title="App not configured yet",
                message="This site is missing X API keys, so nothing was changed.",
            ),
            503,
        )

    oauth_token = request.args.get("oauth_token")
    verifier = request.args.get("oauth_verifier")
    stored_token = session.get("request_token")
    stored_secret = session.get("request_token_secret")

    if not verifier or not oauth_token or not stored_token or not stored_secret:
        session.clear()
        return (
            render_template(
                "error.html",
                title="Authorization expired",
                message=(
                    "The sign-in session was missing or expired. Please start again "
                    "from the beginning. Nothing on your profile was changed."
                ),
            ),
            400,
        )

    if oauth_token != stored_token:
        session.clear()
        return (
            render_template(
                "error.html",
                title="Authorization expired",
                message=(
                    "The sign-in session did not match this approval. Please start "
                    "again from the beginning. Nothing on your profile was changed."
                ),
            ),
            400,
        )

    try:
        oauth = oauth_session(
            resource_owner_key=stored_token,
            resource_owner_secret=stored_secret,
            verifier=verifier,
        )
        access_token = oauth.fetch_access_token(OAUTH_ACCESS_TOKEN_URL, timeout=20)
    except Exception as exc:
        log.exception("Failed to exchange the request token")
        session.clear()
        return (
            render_template(
                "error.html",
                title="Authorization failed",
                message=(
                    "X did not grant an access token. Nothing on your profile was changed."
                ),
                detail=str(exc),
            ),
            502,
        )

    # Drop the temporary request token before doing any writes.
    session.clear()

    user_id = str(access_token.get("user_id") or "")
    screen_name = access_token.get("screen_name")
    if not user_id:
        return (
            render_template(
                "error.html",
                title="Authorization failed",
                message=(
                    "X did not return a user id with the access token. "
                    "Nothing on your profile was changed."
                ),
            ),
            502,
        )

    try:
        display_name = assign_display_name(user_id, screen_name)
        access = oauth_session(
            resource_owner_key=access_token.get("oauth_token"),
            resource_owner_secret=access_token.get("oauth_token_secret"),
        )
        update_x_profile(access, display_name)
    except Exception as exc:
        log.exception("Profile update failed")
        return (
            render_template(
                "error.html",
                title="Profile update failed",
                message=(
                    "Authorization succeeded, but updating your profile did not "
                    "complete. Some fields may be unchanged. You can try again, "
                    "or change them back in your X settings."
                ),
                detail=str(exc),
            ),
            502,
        )

    return render_template(
        "success.html",
        display_name=display_name,
        bio=BIO,
        screen_name=screen_name,
    )


@app.get("/success")
def success_redirect():
    return redirect(url_for("index"))


@app.get("/healthz")
def healthz():
    return {"ok": True, "configured": configured()}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
