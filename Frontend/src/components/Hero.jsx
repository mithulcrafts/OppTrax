import WhatsAppMockup from './WhatsAppMockup';

export default function Hero({ isAuthenticated, onOpenWorkspace }) {
  const handleStartClick = () => {
    if (isAuthenticated && onOpenWorkspace) {
      onOpenWorkspace();
      return;
    }
    const section = document.getElementById('connect');
    if (section) {
      section.scrollIntoView({ behavior: 'smooth' });
    }
    setTimeout(() => {
      const el = document.getElementById('phone-cta');
      if (el) el.focus();
    }, 300);
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
          WhatsApp-native &middot; LiveScout AI &middot; EN + हिंदी
        </div>
        <h1>
          Your AI copilot for <em>every</em>
          <br />
          <span className="shimmer-text">opportunity</span>
        </h1>
        <p className="hero-sub">
          Discover live personalized jobs, internships, hackathons, and scholarships with LiveScout &amp; Sarvam AI &mdash; delivered on WhatsApp in Hindi &amp; English with deadline alerts and AI Q&amp;A.
        </p>
        <div className="hero-actions">
          <button className="btn-primary" onClick={handleStartClick}>
            {isAuthenticated ? 'Open Workspace \u2192' : 'Connect WhatsApp \u2192'}
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
