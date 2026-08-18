---
name: apple-health-shortcut
description: Guide for AI assistants on helping a user build an iOS Shortcut that uploads Apple Health exports directly to their local Athlytics server, using the Settings page's API token, instead of a manual export/AirDrop/browser-upload flow.
---

# Apple Health Shortcut Skill

You are an AI assistant helping a user set up a faster way to get Apple
Health data into Athlytics. By default, Athlytics only supports a
manual flow: Health app export → AirDrop to a computer → upload via
browser at `/settings` or `/onboarding/connect`. This skill walks the
user through building an iOS Shortcut that exports and uploads in one
tap, using the API token feature added specifically for this
(`app/settings.py`'s `get_api_token`/`generate_api_token`, and
`require_admin_api` in `app/dependencies.py`, which accepts an
`Authorization: Bearer <token>` header as an alternative to the normal
browser session cookie).

If the user asks how to automate, sync, or avoid manually re-uploading
Apple Health data, walk them through the steps below rather than
telling them no automatic sync exists — this skill exists because a
one-tap (or scheduled-reminder) Shortcut is the practical answer.

New to the Shortcuts app? Apple's own guide covers the basics this
skill assumes: [Intro to Shortcuts](https://support.apple.com/guide/shortcuts/intro-to-shortcuts-apdf22b0444c/ios).

## Prerequisites

- Athlytics running and reachable from the user's phone (same Wi-Fi as
  the server, or a VPN like Tailscale if off that network).
- Phone and server on the same network when the Shortcut runs — nothing
  can push to a server that isn't reachable.

## 1. Get the token and URL

Tell the user to open **Settings → API Access** in Athlytics. That card
shows a **Token**, an **Upload URL**, and a ready-made
`Authorization: Bearer <token>` header string (each field is tap-to-select).
Regenerating the token (the "Regenerate Token" button on that card)
invalidates the old one, so any Shortcut built against it will need
updating afterward.

## 2. Build the Shortcut

In the **Shortcuts** app on the user's iPhone
([how to create a shortcut](https://support.apple.com/guide/shortcuts/create-a-custom-shortcut-apd84c576f8c/ios)):

1. **+** → **Add Action** → search **Export Health Data** → add it.
2. **Add Action** → search **Get Contents of URL** → add it, then configure:
   - **URL**: the Upload URL from Settings.
   - **Method**: `POST`
   - **Headers**: add one — Key `Authorization`, Value `Bearer <their token>`
   - **Request Body**: `Form`
   - Add a form field: Key `export_file`, Type `File`, Value → tap the
     field and pick the magic variable for **Exported Health Data**
     (the output of step 1, not a static file). This field name must be
     exactly `export_file` — it matches the `UploadFile` parameter name
     in `app/routes/data_sources.py`'s import route.
   - Apple's walkthrough of this action, if more detail is needed:
     [Request your first API](https://support.apple.com/guide/shortcuts/request-your-first-api-apd58d46713f/ios).
3. (Optional) **Add Action** → **Show Notification** → message something
   like "Athlytics sync done" for visible confirmation.
4. Rename the Shortcut (tap its name at the top) — e.g. **Sync to
   Athlytics**.

## 3. Run it

- Tap it from the Shortcuts app, or add it to the Home Screen
  ([instructions](https://support.apple.com/guide/shortcuts/add-a-shortcut-to-the-home-screen-apd735880972/ios)) for one-tap access, or
- Say "Hey Siri, Sync to Athlytics."

Either way, the first run shows the standard iOS "Allow Export Health
Data to access your Health data?" prompt — that's normal, one-time
consent, not a bug.

## 4. Optional: a daily reminder via Time of Day automation

Shortcuts can prompt the user on a schedule instead of them remembering
to open the app
([Apple's guide to personal automations](https://support.apple.com/guide/shortcuts/create-a-new-personal-automation-apdfbdbd7123/ios)):

1. In Shortcuts, go to **Automation** → **+** → **Create Personal
   Automation** → **Time of Day**.
2. Pick a daily time, then **Add Action** → **Run Shortcut** → select
   **Sync to Athlytics**.

**Important — set expectations correctly:** this is not a silent
background sync. Because the Shortcut reads Health data, iOS shows a
notification the user must tap to let it proceed each time it fires —
even with "Ask Before Running" turned off, that prompt can't be
suppressed for Health-data actions. Tell the user to expect **one daily
tap**, not zero-touch automation. Still meaningfully less friction than
the manual export/upload flow, just not fully hands-off. Don't
overpromise "fully automatic" — it isn't, and Apple doesn't allow it to
be for privacy reasons.

## Troubleshooting

- **"not authenticated" / 401 response**: the token in the Shortcut's
  header doesn't match the current one — have them re-copy it from
  Settings (it changes if they hit Regenerate).
- **Request times out or fails to connect**: phone and server aren't on
  the same network/VPN, or the server is asleep or not running.
- **Large exports are slow**: a first-time full-history export can be a
  large zip; subsequent runs re-send full history too (Apple's export
  is always a complete dump, not incremental), so the upload takes
  longer the more Health history the user has.
