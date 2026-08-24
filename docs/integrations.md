# Data Sources & Integrations

Athlytics brings together your wellness, recovery, cardio, and strength training data from multiple wearable and fitness providers into a single, private SQLite database.

---

## 📋 Table of Contents

1. [Supported Providers Overview](#supported-providers-overview)
2. [Garmin Connect](#garmin-connect)
3. [Strava](#strava)
4. [Apple Health (Manual & iOS Shortcut)](#apple-health-manual--ios-shortcut)
5. [Mi Fitness (Xiaomi / Amazfit)](#mi-fitness-xiaomi--amazfit)
6. [Tonal (Smart Cable Home Gym)](#tonal-smart-cable-home-gym)
7. [Security & Encryption Architecture](#security--encryption-architecture)
8. [Deduplication & Synchronization Mechanics](#deduplication--synchronization-mechanics)

---

## 📊 Supported Providers Overview

| Provider | Supported Data | Connection Flow | MCP Sync Tool |
| :--- | :--- | :--- | :--- |
| **Garmin Connect** | 18 canonical metrics (HRV, RHR, Sleep, Body Battery, Stress, VO2 Max, Training Load, Race Predictions, Activities) | Email & Password (+ Interactive MFA script if needed) | `sync_garmin_data` |
| **Strava** | Workout activities (Run, Ride, Swim, Gym), Distance, Duration, Elevation, Heart Rate | Bring-Your-Own-Key OAuth or Bulk Archive Zip Import | `sync_strava_data` |
| **Apple Health** | Daily wellness metrics, steps, resting HR, HRV, workouts | Export Zip File Upload or 1-Tap iOS Shortcut Automation | N/A (Push API) |
| **Mi Fitness** | Steps, sleep duration, resting HR, SpO2, stress, daily calories, activities | In-Dashboard QR Code Scan | `sync_mi_fitness_data` |
| **Tonal** | Composite Strength Score, Per-Muscle Readiness, Set-by-Set Workout History, Movement Library | Direct Email & Password | `sync_tonal_data` |

---

## ⌚ Garmin Connect

### How It Works

Athlytics connects directly to Garmin Connect to pull health and activity metrics. Synchronization is headless, resumable, and rate-limit aware.

### Connection Steps

1. Go to **Onboarding → Connect** (or **Settings → Connections**).
2. Enter your Garmin Connect account email and password.
3. Athlytics securely verifies the login and establishes encrypted credentials.

### Resolving Multi-Factor Authentication (MFA)

If your Garmin account has MFA enabled, the first connection may trigger an MFA challenge. Complete it using the interactive helper script:

```bash
# When running with Docker:
docker exec -it athlytics python scripts/login_garmin.py

# When running locally:
python scripts/login_garmin.py
```

Enter the SMS or Authenticator code when prompted. The resulting session tokens are cached in the volume at `/data/garmin_tokens/`.

### Supported Metrics

- **Recovery & Wellness**: `resting_hr`, `hrv`, `sleep_score`, `sleep_duration`, `body_battery`, `stress`, `respiration`, `spo2`, `weight`.
- **Training & Performance**: `vo2_max`, `training_load`, `race_prediction_5k`, `race_prediction_10k`, `race_prediction_half_marathon`, `race_prediction_marathon`.
- **Activity & Volume**: `steps`, `activity_distance`, `activity_duration`, `activity_calories`.

---

## 🚴 Strava

Athlytics supports both automated live synchronization via Strava's OAuth API and manual historical imports via Strava bulk archive files.

### 1. OAuth Live Sync (Bring Your Own Key)

Athlytics uses a "Bring Your Own Key" model so your Strava API keys remain 100% private to your server.

1. Create a Strava API Application at [strava.com/settings/api](https://www.strava.com/settings/api):
   - **Website**: `http://localhost:8000` (or your public server URL).
   - **Authorization Callback Domain**: `localhost` (or your domain/IP).
2. Note your **Client ID** and **Client Secret**.
3. In Athlytics, open **Settings → Connections → Strava**, enter your Client ID and Client Secret, and click **Connect**.
4. Authorize Athlytics on Strava. Athlytics will automatically store your encrypted OAuth tokens in `/data/strava_credentials.enc`.

### 2. Bulk Archive Zip Import (No API Key Required)

If you don't want to create a Strava API application, you can export your complete history:
1. In Strava, go to **Settings → My Account → Download or Delete Your Account → Request Your Archive**.
2. Download the resulting `.zip` file from your email.
3. In Athlytics, navigate to **Settings → Connections → Strava**, click **Import Strava Archive**, and upload the zip file.

---

## 🍎 Apple Health (Manual & iOS Shortcut)

Because Apple Health data is stored locally on iOS devices without a cloud API, Athlytics provides two integration methods:

### Method A: Automated 1-Tap iOS Shortcut (Recommended)

You can build a simple iOS Shortcut that exports your health data and pushes it directly to Athlytics with a single tap or daily reminder.

1. In Athlytics, go to **Settings → API Access**.
2. Note your **Personal API Token** and the **Upload URL** (`POST /api/apple-health/import`).
3. Open the **Shortcuts** app on your iPhone:
   - Tap **+** to create a new shortcut named **Sync to Athlytics**.
   - Add action: **Export Health Data**.
   - Add action: **Get Contents of URL**:
     - **URL**: Paste your Upload URL.
     - **Method**: `POST`.
     - **Headers**: Key `Authorization`, Value `Bearer <your_api_token>`.
     - **Request Body**: `Form`.
     - **Field**: Key `export_file`, Type `File`, Value `Exported Health Data`.
4. Run the Shortcut! You can add it to your iOS Home Screen or trigger it via Siri ("*Hey Siri, Sync to Athlytics*").
5. *(Optional)* Set up a daily prompt under **Shortcuts → Automation → Time of Day** to remind you to sync.

### Method B: Manual Web Upload

1. Open Apple **Health app** on iPhone → Tap profile icon → **Export All Health Data**.
2. AirDrop or transfer the `export.zip` file to your computer.
3. Upload it in Athlytics under **Settings → Connections → Apple Health**.

---

## 📱 Mi Fitness (Xiaomi / Amazfit)

Athlytics integrates with Xiaomi Mi Fitness through an effortless QR code login workflow without needing to expose your raw password.

### Connection Flow

1. Go to **Settings → Connections → Mi Fitness** (or during onboarding).
2. Click **Connect with QR Code**.
3. A QR code is generated dynamically in the Athlytics dashboard.
4. Open the **Mi Fitness** app on your phone, tap the **QR Scanner icon**, and scan the code.
5. Tap **Confirm Login** on your phone.
6. Athlytics automatically detects the approval, saves your session tokens, and starts the initial background data sync!

---

## 🏋️ Tonal (Smart Cable Home Gym)

Athlytics provides rich, two-way integration with Tonal smart home gym machines.

### Connection Steps

1. In Athlytics, go to **Settings → Connections → Tonal**.
2. Enter your Tonal account email and password (the same credentials you use for the Tonal mobile app).
3. Athlytics authenticates directly against Tonal's secure API and begins backfilling your data.

### Features & Metrics Ingested

- **Strength Score**: Daily overall and body-region strength ratings (Upper Body, Lower Body, Core).
- **Per-Muscle Readiness**: Live snapshot scores (0–100) for individual muscle groups (`tonal_readiness_chest`, `tonal_readiness_quads`, `tonal_readiness_biceps`, etc.).
- **Set-by-Set Detail**: Every rep, weight (lbs), total volume (lbs), power output, and estimated One-Rep Max (`1RM`).
- **AI Movement Search & Workout Creator**: Search over 300+ movements and assemble custom workouts.

> [!IMPORTANT]
> **Write Protection (Estimate-Before-Create)**: When using the AI Coach to design Tonal workouts, the coach must always call `estimate_tonal_workout` to calculate duration and set counts and obtain your explicit confirmation before calling `create_tonal_workout`. Nothing is ever sent to your physical machine without your approval.

---

## 🔒 Security & Encryption Architecture

Your health data and credentials belong to you. Athlytics is engineered with a strict privacy-first model:

- **Local Encryption**: All passwords, API secrets, and OAuth refresh tokens are encrypted at rest using **Fernet symmetric encryption** (AES-128-CBC with HMAC-SHA256).
- **Automated Key Management**: On first launch, Athlytics generates a cryptographically secure 32-byte key stored with restricted permissions (`0600`) at `/data/.env` inside the persistent volume.
- **Isolated Credential Stores**: Each provider stores its credentials in a separate `.enc` file (e.g. `garmin_credentials.enc`, `strava_credentials.enc`, `tonal_credentials.enc`).
- **Zero Third-Party Telemetry**: Athlytics contains no analytics trackers, advertising beacons, or third-party cloud connections. All data resides entirely on your machine.

---

## 🔄 Deduplication & Synchronization Mechanics

### Incremental Resumable Checkpoints

Every metric maintains an independent sync checkpoint in SQLite (`sync_checkpoint` table). When syncing:
- Normal syncs query data only from the last successful checkpoint date forward.
- You can force a full historical re-fetch anytime by passing `force_full_history=True` in MCP sync tools or triggering a Full Sync in the dashboard.

### Cross-Source Activity Deduplication

If you record a workout on a Garmin watch that automatically syncs to Strava, both providers will report the same session. Athlytics handles this seamlessly:
- Activities are deduplicated across sources based on activity type, start timestamp proximity, and duration.
- **Source Priority**: If a duplicate is detected, the device-direct source wins (`ACTIVITY_SOURCE_PRIORITY = ["garmin", "strava"]`).
