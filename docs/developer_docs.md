# OppTrax Developer Documentation

This document describes the local development setup for OppTrax.

---

## Local Development Setup

### Prerequisites
*   Python 3.10 or higher
*   Node.js 18 or higher
*   MongoDB instance (local or Atlas cloud)
*   Ngrok installed for local webhook routing

### Step-by-Step Execution

1.  **Backend Setup**:
    Navigate to the `Backend` directory, configure the environment variables inside a `.env` file, and start the development server:
    ```bash
    pip install -r requirements.txt
    python main.py
    ```

2.  **Ngrok Tunneling**:
    Route external webhook calls to your local instance:
    ```bash
    ngrok http 8000
    ```
    Update the `NGROK_BASE_URL` in your `.env` file with the generated HTTPS tunnel address.

3.  **Frontend Setup**:
    Navigate to the `Frontend` directory, install package dependencies, and run Vite:
    ```bash
    npm install
    npm run dev
    ```
