# GayatriBot - HR Automation System

> **Note:** This project has been developed under the [Dbert online internship program](https://dbert.online). We thank the Dbert team for their guidance and support.

GayatriBot is an automated HR and Recruitment system built to streamline the hiring process for the Gayatri Education Project. The system consists of three independent background processes that work together to poll application data, process CVs using AI, and provide a dashboard for administrators.

## System Architecture

The system runs as three independent processes:

1. **Email Bot (`bot.py`)**: Polls a Google Sheet every 5 minutes, sending acknowledgement & confirmation emails via Outlook.
2. **CV Processor (`cv_processor.py`)**: Downloads CVs from Google Drive, extracts text, and analyzes them via a local Ollama LLM.
3. **Flask Dashboard (`flask_app.py`)**: An admin web interface for managing applicants and skills.

## Prerequisites

- Windows 10/11 machine
- Outlook installed and logged in as your sender email (e.g., `careers@dbert.online`)
- Python 3.9+ installed
- [Ollama](https://ollama.ai) installed locally (for CV analysis)
- Google Cloud Service Account JSON key (`service_account.json`)
- Google Sheet connected to your Google Form

## Setup Instructions

### 1. Install Python Dependencies
Open your terminal in the project directory and run:
```bash
# Create virtual environment (recommended)
python -m venv venv
venv\Scripts\activate

# Install packages
pip install -r requirements.txt
```

### 2. Configure Credentials
- Copy your Google Service Account JSON key file to the project folder as `service_account.json`.
- Share your Google Sheet with the service account email address found inside the JSON key.

### 3. Install and Run Ollama
The system uses the `llama3:8b` model for CV analysis.
```bash
ollama pull llama3:8b
```

### 4. Configure `bot_config.py`
Open `bot_config.py` and verify your settings, ensuring `SENDER_EMAIL` matches the Outlook account you will send from.

## Running the System
You need to open three separate terminal windows and activate the virtual environment (`venv\Scripts\activate`) in each. Then, run the following scripts:

- **Terminal 1**: `python bot.py`
- **Terminal 2**: `python cv_processor.py`
- **Terminal 3**: `python flask_app.py`

Once everything is running, access the admin dashboard at `http://localhost:5000`.

## Features
- **Automated Communication**: Send instant acknowledgement and approval emails.
- **AI CV Analysis**: Uses Ollama to extract text and analyze skills based on specific domain requirements.
- **Admin Dashboard**: Manage applicant status, track processing queues, and edit skill requirements.
