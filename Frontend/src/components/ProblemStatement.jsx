import React from 'react';

export default function ProblemStatement() {
  return (
    <section className="problem-section">
      <div className="problem-container">
        
        <h2 className="problem-quote reveal">
          &ldquo;90% of opportunities are missed not due to a lack of qualification, but a lack of <span className="highlight-text">timely discoverability</span>.&rdquo;
        </h2>
        
        <div className="problem-grid">
          <div className="problem-card reveal">
            <div className="problem-card-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/>
                <path d="M22 12H2M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/>
              </svg>
            </div>
            <h3>Scattered Sources</h3>
            <p>Opportunities are buried across hundreds of portals, newsletters, and Discord servers.</p>
          </div>
          
          <div className="problem-card reveal">
            <div className="problem-card-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/>
                <polyline points="12 6 12 12 16 14"/>
              </svg>
            </div>
            <h3>Silent Deadlines</h3>
            <p>Application windows open and close in days, leaving students unaware until it's too late.</p>
          </div>
          
          <div className="problem-card reveal">
            <div className="problem-card-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9"/>
                <path d="M13.73 21a2 2 0 01-3.46 0"/>
              </svg>
            </div>
            <h3>Information Noise</h3>
            <p>Filtering through endless irrelevant postings to find target roles is a full-time job.</p>
          </div>
        </div>
      </div>
    </section>
  );
}
