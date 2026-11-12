export default function Navbar({ sessionUser, onOpenWorkspace, onLogout }) {
  const handleGetStarted = () => {
    if (sessionUser && onOpenWorkspace) {
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
        {sessionUser ? (
          <>
            <button className="nav-cta" onClick={handleGetStarted}>
              Workspace &middot; +{sessionUser.whatsapp_phone}
            </button>
            <button className="nav-link nav-logout-btn" onClick={onLogout} type="button">
              Disconnect
            </button>
          </>
        ) : (
          <button className="nav-cta" onClick={handleGetStarted}>
            Get started
          </button>
        )}
      </div>
    </nav>
  );
}
