import React from 'react';
import WhatsAppMockup from './WhatsAppMockup';

export default function Hero() {
  const handleStartClick = () => {
    const el = document.getElementById('phone-cta');
    if (el) el.focus();
  };

  const handleSeeHowClick = () => {
    const el = document.getElementById('how');
    if (el) {
      el.scrollIntoView({ behavior: 'smooth' });
    }
  };

  return (
    <section className="hero">
      <div className="hero-scan"></div>
      <div className="hero-content">
        <div className="hero-badge">
          <div className="hero-badge-dot"></div>
          WhatsApp-native &middot; AI-powered &middot; Multilingual
        </div>
        <h1>
          Your AI copilot for <em>every</em>
          <br />
          <span className="shimmer-text">opportunity</span>
        </h1>
        <p className="hero-sub">
          Discover jobs, internships, hackathons, and scholarships &mdash; tracked and managed for you, right inside WhatsApp.
        </p>
        <div className="hero-actions">
          <button className="btn-primary" onClick={handleStartClick}>
            Start on WhatsApp &rarr;
          </button>
          <button className="btn-ghost" onClick={handleSeeHowClick}>
            See how it works
          </button>
        </div>

        <WhatsAppMockup />
      </div>
    </section>
  );
}
