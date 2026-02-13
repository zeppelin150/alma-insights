# Alma Insights

**RCM Issue Analysis & AI-Assisted Reporting**

A desktop application for Alma's RCM Operations, Product, and Partner Support teams. Search and browse rebuilt ticket conversations, view TRC analytics, detect trending topics, and generate AI-powered reports using Gemini.

---

## Quick Start

### First-Time Setup

1. **Clone the repository:**
   ```
   git clone https://github.com/alma-health/alma-insights.git
   cd alma-insights
   ```

2. **Run the installer:**
   ```
   python setup_alma_insights.py
   ```
   This will:
   - Check that Python 3.10+ is installed
   - Install all required packages (PySide6, pandas, etc.)
   - Create a desktop shortcut
   - Initialize the local database
   - Offer to launch the app

3. **Or install manually:**
   ```
   pip install -r requirements.txt
   python main.py
   ```

### Launching After Setup

- **Desktop shortcut:** Double-click "Alma Insights" on your desktop
- **Command line:** `python main.py` from the project directory

---

## Requirements

- **Python 3.10+** — [Download](https://www.python.org/downloads/)
- **Windows 10/11, macOS 12+, or Linux** with a display server

Python packages (installed automatically by setup):
- PySide6 (Qt desktop framework)
- pandas (data processing)
- scikit-learn (TF-IDF text analysis)
- nltk (tokenization)
- pyyaml (configuration)

---

## Features

| Feature | Status | Description |
|---------|--------|-------------|
| 🔍 Conversation Search | ✅ Live | Search rebuilt ticket threads by keyword, TRC, date, CSAT |
| 📊 TRC Analytics | ✅ Live (Phase I) | Volume, resolution time, CSAT metrics by TRC |
| 📈 Trending Topics | ✅ Live (phase I) | TF-IDF trending terms detection |
| 🤖 AI Reports | 🚧 WIP | Gemini-powered theme/sentiment/trend reports |
| ⚙️ Settings | 🚧 WIP | Data source and Gemini CLI configuration |

---

## Configuration

Edit `config/settings.yaml` to configure:
- Zendesk API credentials (or Lightdash connection)
- Gemini CLI path and settings
- Google Drive export folder
- Guru and Typeform links

Sensitive values (API keys, tokens) should be set via environment variables, not in the config file.

---

## Project Structure

```
alma-insights/
├── main.py                          # Application entry point
├── setup_alma_insights.py           # First-run installer
├── requirements.txt
│
├── config/
│   ├── settings.yaml                # App configuration
│   └── prompts/                     # Gemini prompt templates
│
├── src/
│   ├── ui/
│   │   ├── theme.py                 # Alma brand stylesheet
│   │   ├── main_window.py           # Main window + sidebar
│   │   ├── pages/
│   │   │   ├── conversation_search.py
│   │   │   └── placeholders.py      # Dashboard, trending, reports (Session 2)
│   │   └── dialogs/
│   │       └── help_dialog.py       # Help, docs, feedback
│   │
│   ├── data/
│   │   ├── db_manager.py            # SQLite database layer
│   │   └── demo_data.py             # Sample data generator
│   │
│   ├── processing/                  # (Session 2: analytics, topic flagger)
│   ├── gemini/                      # (Session 2: CLI wrapper, prompts)
│   └── output/                      # (Session 2: PDF/CSV export)
│
├── data/                            # Local database (gitignored)
└── assets/                          # Icons, images
```

---

## Data & Security

- **All data is processed locally.** No external servers, no cloud storage.
- **SQLite database is ephemeral** — recreated on each ingestion run, gitignored.
- **Gemini access** uses Alma's approved secure CLI (no API keys in code).
- **PII redaction** is configurable before any data is sent to Gemini.
- See the Technical Specification document for full security details.

---

## Support

- **Documentation:** Click "Help & Docs" in the app → opens Guru knowledge base
- **Feedback:** Click "Feedback" in the app → opens Typeform
- **Issues:** File in this GitHub repository

---

*Internal tool — Alma Health, Inc. — RCM Operations*
