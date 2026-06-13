import React from 'react';

export default function OpportunityTracking() {
  return (
    <section className="section" style={{ paddingTop: 0 }}>
      <div style={{ paddingTop: '5rem' }}>
        <div className="section-eyebrow reveal">Opportunity tracking</div>
        <div className="section-h reveal">From discovery to deadline</div>
        
        <div className="pipeline-row">
          {/* STEP 1 */}
          <div className="pipeline-step reveal">
            <div className="pipeline-icon-wrapper">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
                <path d="M11 8a3 3 0 0 0-3 3" />
              </svg>
            </div>
            <div className="pipeline-num">01</div>
            <h3>Discovery</h3>
            <div className="pipeline-label">AI Matches Discovered</div>
            <p>OppTrax scans 50k+ web sources to find hackathons, scholarships, and internships tailored strictly to your skill profile.</p>
          </div>

          <div className="pipeline-connector">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M5 12h14M13 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>

          {/* STEP 2 */}
          <div className="pipeline-step reveal">
            <div className="pipeline-icon-wrapper">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                <path d="m9 10 2 2 4-4" />
              </svg>
            </div>
            <div className="pipeline-num">02</div>
            <h3>Evaluation</h3>
            <div className="pipeline-label">Ask AI Eligibility</div>
            <p>Directly chat with the copilot to resolve questions about location, stipend rates, or requirements without reading long docs.</p>
          </div>

          <div className="pipeline-connector">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M5 12h14M13 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>

          {/* STEP 3 */}
          <div className="pipeline-step reveal">
            <div className="pipeline-icon-wrapper">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
                <path d="M13.73 21a2 2 0 0 1-3.46 0" />
              </svg>
            </div>
            <div className="pipeline-num">03</div>
            <h3>Active Tracking</h3>
            <div className="pipeline-label">Smart Reminders</div>
            <p>Mark opportunities as tracked. Receive custom countdown pings directly on WhatsApp 48 hours before applications close.</p>
          </div>

          <div className="pipeline-connector">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M5 12h14M13 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>

          {/* STEP 4 */}
          <div className="pipeline-step reveal">
            <div className="pipeline-icon-wrapper">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="m22 2-7 20-4-9-9-4Z" />
                <path d="M22 2 11 13" />
              </svg>
            </div>
            <div className="pipeline-num">04</div>
            <h3>Applied</h3>
            <div className="pipeline-label">Status Updated</div>
            <p>Confirm your submission in one tap inside WhatsApp. OppTrax moves it to your active dashboard so you can focus on prep.</p>
          </div>
        </div>
      </div>
    </section>
  );
}
