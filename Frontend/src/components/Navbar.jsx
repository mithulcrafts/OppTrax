import React from 'react';

export default function Navbar() {
  const handleGetStarted = () => {
    const el = document.getElementById('phone-cta');
    if (el) el.focus();
  };

  return (
    <nav>
      <div className="nav-logo">
        Opp<span>Trax</span>
      </div>
      <div className="nav-links">
        <a className="nav-link" href="#features">
          Features
        </a>
        <a className="nav-link" href="#how">
          How it works
        </a>
        <button className="nav-cta" onClick={handleGetStarted}>
          Get started
        </button>
      </div>
    </nav>
  );
}
