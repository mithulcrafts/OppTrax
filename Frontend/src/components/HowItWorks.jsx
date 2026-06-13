import React from 'react';

export default function HowItWorks() {
  return (
    <section className="section" id="how">
      <div className="section-eyebrow reveal">How it works</div>
      <div className="section-h reveal">
        Three steps to never miss
        <br />
        an opportunity again
      </div>
      
      {/* 3-Column Steps Grid */}
      <div className="how-grid">
        {/* CARD 1 */}
        <div className="how-card reveal">
          <div className="how-card-header">
            <div className="how-icon-wrapper">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5Z" />
              </svg>
            </div>
            <div className="how-badge-step">01</div>
          </div>
          <h3 className="how-title">Sign up with WhatsApp</h3>
          <p className="how-desc">
            Enter your number and verify via OTP. The assistant profiles your skill preferences, stack interests, and career goals immediately in plain chat.
          </p>
        </div>

        {/* CARD 2 */}
        <div className="how-card reveal">
          <div className="how-card-header">
            <div className="how-icon-wrapper">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z" />
                <path d="m5 5 1.5 1.5M19 5l-1.5 1.5M5 19l1.5-1.5M19 19l-1.5-1.5" strokeWidth="1" />
              </svg>
            </div>
            <div className="how-badge-step">02</div>
          </div>
          <h3 className="how-title">AI discovers and summarises</h3>
          <p className="how-desc">
            OppTrax monitors global boards 24/7. When a matching role, hackathon, or prize appears, it extracts location, deadline, eligibility, and pay rates instantly.
          </p>
        </div>

        {/* CARD 3 */}
        <div className="how-card reveal">
          <div className="how-card-header">
            <div className="how-icon-wrapper">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
            </div>
            <div className="how-badge-step">03</div>
          </div>
          <h3 className="how-title">Track, ask, get reminded</h3>
          <p className="how-desc">
            Save items straight to your personal checklist. Ask context-aware queries about application guidelines, and receive automated WhatsApp alerts before portals close.
          </p>
        </div>
      </div>

      {/* Featured Daily Digest Banner */}
      <div className="how-featured-banner reveal">
        <div className="how-featured-left">
          <div className="how-featured-badge-row">
            <span className="how-badge-step special">Daily Feature</span>
          </div>
          <h3 className="how-featured-title">Digest, not spam</h3>
          <p className="how-featured-desc">
            Get your top 3 curated picks delivered once every morning. No notification fatigue, no complex dashboards. OppTrax continuously sharpens recommendations as you interact.
          </p>
        </div>
        
        <div className="how-featured-right">
          <div className="how-featured-icon-wrapper">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
            </svg>
          </div>
        </div>
      </div>
    </section>
  );
}
