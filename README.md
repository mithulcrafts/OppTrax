# OppTrax

OppTrax is a multilingual WhatsApp-based AI Opportunity Copilot that discovers, summarizes, tracks, and manages opportunities from discovery to deadline completion. By combining headless crawling, AI intent routing, semantic profile scoring, and conversational RAG, OppTrax provides an end-to-end companion for opportunity tracking.

---

## 12 Core Features

1.  **Landing Website**: Submit phone numbers and complete OTP verification via WhatsApp.
2.  **AI Onboarding in WhatsApp**: Conversational onboarding asking for goals, skills, interests, and preferences via text or voice.
3.  **Personalized Opportunity Discovery**: Automatically generates search queries and scans web sources based on candidate profiles.
4.  **AI Opportunity Summary**: Summarizes opportunities into structured fields (Title, Deadline, Eligibility, Location, Rewards, Link).
5.  **Opportunity Actions**: Choose actions (Interested, Not Interested, Track, Ask Questions) directly on WhatsApp cards.
6.  **Opportunity Q&A**: Real-time Q&A on opportunities utilizing Google Search-grounded RAG.
7.  **Opportunity Tracking**: Save opportunities and manage statuses (Interested, Applied, Rejected, Completed).
8.  **Reminder System**: Automated countdown alerts for closing application deadlines and interview dates.
9.  **User-Sent Opportunities**: Extract tracking parameters from user-submitted links, text, or screenshots.
10. **On-Demand Search**: Initiate instant searches using conversational search queries (e.g. "Find AI internships").
11. **Daily Opportunity Digest**: Receives a single consolidated message containing the top 3-5 matching opportunities daily.
12. **Continuous Learning**: Learns preferences from interaction history to deliver increasingly personalized suggestions.

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
