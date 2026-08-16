import React from 'react';

export default function Features() {
  return (
    <section className="section" id="features" style={{ paddingTop: 0, borderBottom: 'none' }}>
      <div style={{ paddingTop: '5rem' }}>
        <div className="section-eyebrow reveal">All features</div>
        <div className="section-h reveal">Everything your copilot does</div>
        <div className="feat-grid">
          <div className="feat-card reveal">
            <div className="feat-icon">
              <i className="ti ti-search" aria-hidden="true"></i>
            </div>
            <div className="feat-title">On-demand search</div>
            <div className="feat-desc">
              Ask &ldquo;Find AI internships in Bengaluru&rdquo; and get real-time ranked results instantly.
            </div>
          </div>
          <div className="feat-card reveal">
            <div className="feat-icon">
              <i className="ti ti-message-circle" aria-hidden="true"></i>
            </div>
            <div className="feat-title">Opportunity Q&amp;A</div>
            <div className="feat-desc">
              &ldquo;Am I eligible?&rdquo; &ldquo;Is this remote?&rdquo; AI answers directly from the opportunity details.
            </div>
          </div>
          <div className="feat-card reveal">
            <div className="feat-icon">
              <i className="ti ti-bell" aria-hidden="true"></i>
            </div>
            <div className="feat-title">Smart reminders</div>
            <div className="feat-desc">
              Deadline and interview reminders sent directly to your WhatsApp, never missed.
            </div>
          </div>
          <div className="feat-card reveal">
            <div className="feat-icon">
              <i className="ti ti-photo" aria-hidden="true"></i>
            </div>
            <div className="feat-title">Screenshot to track</div>
            <div className="feat-desc">
              Send a poster, link, or screenshot. AI extracts the details and starts tracking it.
            </div>
          </div>
          <div className="feat-card reveal">
            <div className="feat-icon">
              <i className="ti ti-microphone" aria-hidden="true"></i>
            </div>
            <div className="feat-title">Voice and multilingual</div>
            <div className="feat-desc">
              Interact in any language by text or voice. Zero friction, zero language barriers.
            </div>
          </div>
          <div className="feat-card reveal">
            <div className="feat-icon">
              <i className="ti ti-brain" aria-hidden="true"></i>
            </div>
            <div className="feat-title">Gets smarter over time</div>
            <div className="feat-desc">
              Learns from your Interested / Not Interested actions. Recommendations sharpen continuously.
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
