# OppTrax

OppTrax is a bilingual (English + Hindi) WhatsApp and Web AI Opportunity Copilot that discovers live hackathons, internships, fellowships, and remote tech jobs via **LiveScout**, summarizes & translates them using **Sarvam AI** (with optional Gemini support), tracks deadlines, and answers follow-up eligibility/compensation questions in English or Hindi.

---
## Live Demo Video
[![View Product Demo Video](https://img.shields.io/badge/View_Product_Demo_Video-34A853?style=for-the-badge&logo=googledrive&logoColor=white)](https://drive.google.com/file/d/1NvuM3wm_IEOeaqhIG_Gyone6sVCOjzrA/view?usp=sharing)

## Key Architecture & Stack

- **LiveScout Real-Time Discovery (`Backend/services/live_scout.py`)**: Zero-key concurrent opportunity search and continuous scouting across Unstop (hackathons, internships, jobs, conferences), Devpost, Remotive, Arbeitnow, and web feeds with bilingual Hindi/English query understanding and profile scoring.
- **Sarvam AI Bilingual Intelligence (`Backend/services/ai_engine.py`, `Backend/config.py`)**: Live English/Hindi Q&A, voice-note transcription, and natural Hindi translation via Sarvam AI (with automatic Gemini support when configured and deterministic bilingual fallbacks).
- **Meta WhatsApp Cloud API (`Backend/routers/whatsapp_webhook.py`, `Backend/services/whatsapp_api.py`)**: Interactive opportunity cards, OTP verification, one-tap tracking, Q&A mode, and automated deadline countdown reminders.
- **Zero-Config Persistence (`Backend/database.py`)**: Built-in persistent SQLite store (`Backend/data`) out-of-the-box (`DATABASE_BACKEND=sqlite` or `auto`), plus optional MongoDB Atlas support.

## 12 Core Features

1.  **Landing Website & Live Workspace**: Verify via WhatsApp OTP or launch the interactive Live Workspace directly to search, inspect, track, and test Q&A in English or Hindi.
2.  **AI Onboarding in WhatsApp**: Conversational onboarding asking for goals, skills, interests, and preferred language (`EN`, `HI`, or `BOTH`) via text or voice.
3.  **Personalized Opportunity Discovery**: LiveScout scans live hackathon, internship, scholarship, and job feeds tailored to candidate skills and learned preferences.
4.  **AI Opportunity Summary**: Summarizes opportunities into structured bilingual fields (Title, Deadline, Eligibility, Location, Rewards/Stipend, Application Steps, Link).
5.  **Opportunity Actions**: Choose actions (Interested, Not Interested, Track, Ask Questions) directly on WhatsApp cards or the Web Inspector.
6.  **Opportunity Q&A**: Real-time Q&A in English or Hindi grounded in live source pages (such as Unstop competition briefs) and verified details.
7.  **Opportunity Tracking**: Save opportunities and manage statuses (Interested, Applied, Rejected, Completed).
8.  **Reminder System**: Automated countdown alerts for closing application deadlines before they expire.
9.  **User-Sent Opportunities**: Extract tracking parameters from user-submitted links, text, or screenshots.
10. **On-Demand Search**: Initiate instant live searches using conversational English or Hindi prompts (e.g. `"Find Python internships"`, `"मेरे लिए AI हैकाथॉन खोजो"`).
11. **Daily Opportunity Digest**: Receives a consolidated digest containing the top matching opportunities daily.
12. **Continuous Learning**: Learns preferences from interaction history to refine personalization scores over time.

<img width="1896" height="977" alt="image" src="https://github.com/user-attachments/assets/46285e6a-6624-4c57-9257-c9b433c52f71" />


---

## Getting Started

To get started running the project, consult the following guides:

*   [Developer Setup Guide](docs/developer_docs.md)
    Instructions for running the Backend, Frontend, and Ngrok local servers.

*   [Core Features Guide](docs/features.md)
    Functional overview and details for each of the 12 core features of OppTrax.

---

## Directory Layout

*   `Backend/`: FastAPI server source code (routers, database connections, background tasks, external service integrations).
*   `Frontend/`: React and Vite-based web interface for verification and number onboarding.
*   `docs/`: Full developer documentation and consolidated feature guides.
