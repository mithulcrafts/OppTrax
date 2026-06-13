import { useEffect } from 'react';

export default function useIntersectionObserver() {
  useEffect(() => {
    const revealObs = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add('visible');
          }
        });
      },
      { threshold: 0.1 }
    );

    const elements = document.querySelectorAll('.reveal');
    elements.forEach((el, i) => {
      el.style.transitionDelay = `${(i % 4) * 0.07}s`;
      revealObs.observe(el);
    });

    return () => {
      elements.forEach((el) => {
        try {
          revealObs.unobserve(el);
        } catch (err) {
          // Element might not exist anymore
        }
      });
      revealObs.disconnect();
    };
  }, []);
}
