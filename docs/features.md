# OppTrax Features Guide

OppTrax is a multilingual WhatsApp-based AI Opportunity Copilot that discovers, summarizes, tracks, and manages opportunities from discovery to deadline completion. This document describes the functional behavior and technical implementation of its twelve core features.

---

## 1. Landing Website

### Description
The entry point for candidates. Users submit their phone number and receive a verification code via WhatsApp to authenticate their identity. Once verified, the user is registered and prompted on WhatsApp to begin the onboarding process.

### Technical Overview
*   **Frontend**: Built with React components that manage the user entry interface, including forms for phone submissions, OTP input validations, warning banners (e.g. Meta sandbox warning), and loading states.
*   **Backend**: Managed by a FastAPI authentication router. It exposes REST endpoints `/api/auth/send-otp` (generates and stores a 6-digit code in MongoDB with a 5-minute expiry) and `/api/auth/verify-otp` (validates the code, registers the user, and initiates standard onboarding states).
*   **WhatsApp Delivery**: OTPs are dispatched using Meta templates or text messages via the messaging utility system.

---

## 2. AI Onboarding in WhatsApp

### Description
A conversational onboarding questionnaire conducted entirely inside WhatsApp. The bot prompts the candidate for their goals (e.g. internships, hackathons, job tiers, scholarships), technical skills, domain interests, location constraints, and preferred language. The candidate can reply using text or voice in any language.

### Technical Overview
*   **State Machine**: Monitored by the `onboarding_state` field inside the user's document in the `users` MongoDB collection.
*   **Multi-Modal Ingestion**: Managed by the WhatsApp webhook handler. The bot handles resume ingestion through PDF uploads or conversational queries via text or audio voice notes.
*   **Language Translation**: Voice notes are processed via the Speech-to-Text translation engine (using the `saaras:v3` model) in the media processor layer to translate multi-language voice answers into structured English profiles.

---

## 3. Personalized Opportunity Discovery

### Description
OppTrax translates the user's structured profile metrics into targeted web search and scouting commands. It queries multiple web sources via automated headless scouters, then matches and ranks matching opportunities against the user's skill set.

### Technical Overview
*   **Query Generation**: The intent analysis routine in the AI services layer matches candidate criteria against requests to formulate target queries.
*   **Headless Crawling**: Managed by the crawler client module. The client invokes the scouting task endpoint, configuring target parameters, frequency timers, and the callback webhook URL `[NGROK_BASE_URL]/yutori-webhook`.
*   **Ranking**: Matches are saved in the `opportunities` collection and scored based on semantic relevance against the candidate's parsed skills.

---

## 4. AI Opportunity Summary

### Description
Discovered opportunity findings are parsed and summarized by the AI before delivery. Each opportunity is broken down into structured details: Title, Deadline, Eligibility, Location, Rewards/Stipend, and Application Link. The summary is sent directly as a text or interactive card on WhatsApp.

### Technical Overview
*   **Parsing**: The Gemini-based parsing template in the webhook routing layer processes raw HTML pages or JSON payloads from the crawler. It extracts the details and formats them into a standardized schema without conversational filler.
*   **Formatting**: The HTML elements are converted to WhatsApp formatting symbols for bold (`*`), italics (`_`), and list (`•`) tags using the text formatter helper.
*   **Delivery**: Opportunity cards are sent using Meta interactive button templates via the messaging delivery service.

---

## 5. Opportunity Actions

### Description
Every opportunity card sent to WhatsApp features interactive quick-response buttons. The user can select actions: Interested, Not Interested, Track (which saves the item to their personal board), or Ask Questions (initiating Chat Mode).

### Technical Overview
*   **Interactive Controls**: Buttons use Meta's callback events with custom payload prefixes (e.g. `track_{finding_id}`, `ask_{finding_id}`) mapped inside the WhatsApp message receiver.
*   **Action Mapping**: Tapping "Track" updates the user's database status and inserts a record in the tracked board collection. Tapping "Ask Questions" updates the chat context identifier to lock the session.

---

## 6. Opportunity Q&A

### Description
Candidates can chat directly with the opportunity content. The bot resolves questions like "Am I eligible?", "What is the compensation?", "Is this remote?", or "Generate a cover letter for me" by extracting facts from the crawled page source.

### Technical Overview
*   **Context Isolation**: When the candidate's active chat context ID matches an opportunity ID, standard instructions are bypassed.
*   **Retrieval-Augmented Generation (RAG)**: The Gemini client evaluates queries against the cached opportunity text. The engine uses the Google Search tool dynamically to verify details like application links or compensation tiers.
*   **Application Assets**: Requests containing keywords like `draft` or `cover letter` trigger an asset pipeline inside the webhook handler to generate a customized cover letter using the user's profile and opportunity specifications.

---

## 7. Opportunity Tracking

### Description
Candidates can manage saved opportunities on their personal dashboard. Opportunities are tracked through standard lifecycle phases: Interested, Applied, Rejected, and Completed.

### Technical Overview
*   **Board Collection**: Saved opportunities are recorded in the `tracked_board` collection, which stores candidate numbers, opportunity IDs, deadlines, and notification flags.
*   **Command Control**: Candidates can type `saved` or `board` on WhatsApp to query their dashboard. The bot returns a formatted overview listing active items, deadlines, and application links.

---

## 8. Reminder System

### Description
An autonomous notification system that alerts candidates before deadlines close or scheduled event dates arrive. Notifications are pushed directly as WhatsApp text alerts.

### Technical Overview
*   **Scheduler**: Managed by the hourly background task loop running in the task manager.
*   **Filtering**: The loop queries the tracked board collection for items where the deadline falls within the next 48 hours and the candidate has not been alerted.
*   **Alert Dispatch**: Pushes standard text reminder messages to the candidate's WhatsApp and updates the database record to prevent duplicate alerts.

---

## 9. User-Sent Opportunities

### Description
Candidates can submit custom opportunities to the bot by sending links, screenshots, text snippets, or poster files. The AI extracts the relevant details (title, compensation, deadlines, application link) and adds the opportunity to the candidate's tracking dashboard.

### Technical Overview
*   **Ingestion**: The webhook processes document uploads, images, and raw text entries.
*   **Parsing & Extraction**: Text files and inputs are routed to Gemini to extract structured opportunity fields. The generated item is stored in the `opportunities` collection, allowing the user to track it or start a Q&A session.

---

## 10. On-Demand Search

### Description
Allows users to perform immediate, manual searches by typing natural language prompts like "Find AI internships", "Find hackathons in Hyderabad", or "Find scholarships for CSE students". The AI parses the request, deploys a temporary scout, and returns matching opportunities.

### Technical Overview
*   **Intent Parsing**: Mapped via the Gatekeeper classification engine. The model refines the request into structured instructions.
*   **Scout Deployment**: If no active scout matches the refined query, the backend calls the crawler API to initiate a new crawling session.
*   **Verification**: The system checks the database to prevent duplicate search queues.

---

## 11. Daily Opportunity Digest

### Description
Rather than sending immediate alerts for every discovered finding, OppTrax consolidates matching listings into a daily digest. The bot delivers a single message containing the top 3-5 most relevant opportunities curated for the candidate.

### Technical Overview
*   **Consolidation**: Instead of immediate webhook pushes, findings are cached in the `opportunities` collection, referencing the active task ID.
*   **Relevance Filtering**: Opportunities are ranked by the Priority Evaluation Scoring Matrix. The top opportunities with the highest scores are formatted into a digest card.
*   **Dispatch Queue**: The digest is compiled and sent to the candidate's WhatsApp on a scheduled daily cycle.

---

## 12. Continuous Learning

### Description
OppTrax learns user preferences over time. By tracking "Interested" and "Not Interested" action callbacks, the priority routing engine updates the candidate profile, refining matching relevance scores for future opportunity queries.

### Technical Overview
*   **Feedback Ingestion**: Callback actions are recorded in the `users` and `intent_logs` collections.
*   **Profile Adaptation**: The interest weights and skills keywords inside the candidate profile JSON are dynamically updated based on the candidate's interaction history, tailoring future evaluation ratings.
