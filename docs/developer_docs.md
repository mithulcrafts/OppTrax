# OppTrax Developer Documentation

This document describes the local development setup for OppTrax.

---

## Local Development Setup

### Prerequisites
*   Python 3.11 or higher
*   Node.js 18 or higher
*   Optional: MongoDB instance (defaults to persistent local SQLite in `Backend/data` when `DATABASE_BACKEND=sqlite` or `MONGO_URI` is blank)
*   Optional: Ngrok for live Meta WhatsApp webhook routing

### Step-by-Step Execution

1.  **Backend Setup**:
    Copy `Backend/.env.example` to `Backend/.env`, fill in your keys (`SARVAM_KEY`, Meta WhatsApp credentials; LiveScout works with zero API keys out-of-the-box with `SCOUT_PROVIDER=livescout`), and start the backend:
    ```bash
    pip install -r Backend/requirements.txt
    python Backend/main.py
    ```

2.  **Ngrok Tunneling (WhatsApp Webhook)**:
    Route external Meta webhook calls to your local instance:
    ```bash
    ngrok http 8000
    ```
    Update `NGROK_BASE_URL` in `Backend/.env` with the generated HTTPS tunnel address and point your Meta WhatsApp webhook to `${NGROK_BASE_URL}/whatsapp-webhook`.

3.  **Frontend Setup**:
    Navigate to the `Frontend` directory, install dependencies, and run Vite:
    ```bash
    cd Frontend
    npm install
    npm run dev
    ```

4.  **Verification Tests**:
    ```bash
    pytest Backend/tests -v
    ```
