# Athlytics 🏃‍♂️⚡

> **Your personal health data, 100% self-hosted and private — paired with an evidence-based AI sports coach that speaks MCP.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Docker Ready](https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com/)
[![MCP Server](https://img.shields.io/badge/MCP-Enabled-8A2BE2.svg)](https://modelcontextprotocol.io/)

---

Athlytics brings together your health, recovery, and workout data from your favorite wearables (**Garmin Connect**, **Strava**, **Apple Health**, **Mi Fitness**, and **Tonal**), stores it securely in a local database on your own computer, and powers both a local web dashboard and a bidirectional **AI Coach** using the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/).

With Athlytics, you get complete data privacy, zero subscription lock-in, and an intelligent AI coach capable of checking your recovery, detecting fatigue, and writing structured training plans directly to your dashboard.

<div align="center">

<img width="900" alt="Athlytics Dashboard Overview" src="https://github.com/user-attachments/assets/a2d624fc-043b-453e-b4f1-080f2c411afa" />

<br/><br/>

<img width="445" alt="Metric Detail & Analytics" src="https://github.com/user-attachments/assets/ed71a318-1858-43aa-97dc-92aa3831309d" />
<img width="445" alt="AI Coach & Training Plans" src="https://github.com/user-attachments/assets/9434db06-906b-4c18-a992-7458c7fdbbda" />

</div>

---

## ⚡ Quick Start with Your AI Agent

Don't want to fiddle with terminal commands or manual configuration? You can hand this prompt directly to your favorite AI assistant (Claude Desktop, Claude Code, Cursor, Google Gemini, or ChatGPT):

> [!TIP]
> **Copy & paste this prompt to your AI Assistant:**
> ```text
> I want to set up and run Athlytics on my computer.
> Please help me:
> 1. Start the application using Docker Compose (`docker compose up -d --build`).
> 2. Verify that the web dashboard is running at http://localhost:8000.
> 3. Connect my AI client to the Athlytics MCP server so you can act as my personal health and training coach.
> Walk me through each step and help me connect my wearable data!
> ```

---

## 🚀 One-Command Launch (For Humans)

If you have Docker installed, you can start Athlytics with a single command:

```bash
docker compose up -d --build
```

Then open **[http://localhost:8000](http://localhost:8000)** in your browser to create your admin account, choose your athlete persona, and connect your data sources!

---

## ✨ Why Athlytics?

- 🔒 **100% Private & Self-Hosted**: All metrics reside in a local SQLite database. Passwords and API tokens are encrypted at rest with Fernet (AES-128-CBC / HMAC-SHA256). Zero cloud telemetry or data harvesting.
- ⌚ **Multi-Wearable Support**: Headless, resumable synchronization with Garmin Connect, Strava, Apple Health, Mi Fitness, and Tonal smart gym.
- 📈 **Sports-Science Intelligence**: Trailing rolling baselines (7d, 14d, 30d), period-over-period deltas, and statistical anomaly detection ($|z| \ge 2.0$) for early fatigue and sickness warnings.
- 🤖 **Actionable AI Coach via MCP**: Gives AI assistants (Claude, Gemini, ChatGPT) living health context (`athlytics://`) and bidirectional tools to query trends, set goals, log coach notes, and build periodized training plans.
- 🎨 **Local Web Dashboard**: Clean interface with Dark, Light, and System themes, athlete personas (*Endurance Runner*, *Strength & General Fitness*, *Sleep & Recovery Focus*), and interactive progress charts.

---

## 📚 Documentation Hub

Explore our guides to get the most out of Athlytics:

| Guide | Description |
| :--- | :--- |
| 🛠️ **[Developer & Setup Guide](docs/development.md)** | Local Python setup, running with Docker, MCP server usage, running the test suite, and managing the dev sandbox database. |
| 🔌 **[Data Sources & Integrations](docs/integrations.md)** | Step-by-step guides for connecting Garmin, Strava, Apple Health (with iOS Shortcut), Mi Fitness (QR login), and Tonal. |
| 🤖 **[AI Coach & MCP Guide](docs/ai-coach.md)** | How to connect your AI assistant, browse built-in coaching skills, and copy-paste prompt templates for daily check-ins. |
| 🏃 **[Training Plans & Adaptive Coaching](docs/training-plans.md)** | Evidence-based training principles (recovery gating, 10% volume rule, deloads) and how to adapt plans over time. |

---

## 🧭 Project Structure

```text
athlytics/
├── core/             # Pure sports-science logic, storage, and provider adapters
├── app/              # FastAPI local web dashboard, UI templates, and auth
├── mcp_server/       # Model Context Protocol server (bidirectional AI tools & living context)
├── skills/           # Bundled playbooks for Claude, Gemini, and OpenAI
├── docs/             # Full user, integration, and developer documentation
├── scripts/          # Helper utilities (interactive MFA login, database backup/restore)
├── tests/            # 600+ unit, integration, and MCP contract tests
└── docker-compose.yml# Single-command Docker orchestration
```

---

## 📄 License & Open Source

Athlytics is free and open-source software licensed under the **[MIT License](LICENSE)**. You are free to use, modify, and distribute it for personal or commercial use.
