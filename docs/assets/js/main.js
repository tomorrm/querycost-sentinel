(() => {
  const STORAGE_KEY = 'qcs-theme';
  const LANGUAGE_STORAGE_KEY = 'qcs-language';
  const systemDark = window.matchMedia('(prefers-color-scheme: dark)');

  function getTheme() {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'dark' || saved === 'light') return saved;
    return systemDark.matches ? 'dark' : 'light';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
  }

  function applyLanguage(language) {
    document.documentElement.lang = language;
    document.documentElement.dataset.language = language;

    const toggleLabel = document.getElementById('languageToggleLabel');
    const toggle = document.getElementById('languageToggle');
    const themeToggle = document.getElementById('themeToggle');
    if (toggleLabel) toggleLabel.textContent = language === 'en' ? 'Japanese' : 'English';
    if (toggle) {
      toggle.setAttribute(
        'aria-label',
        language === 'en' ? 'Switch language to Japanese' : '言語を英語に切り替え'
      );
    }
    if (themeToggle) {
      themeToggle.setAttribute('aria-label', language === 'en' ? 'Toggle theme' : 'テーマを切り替え');
    }

    const description = language === 'en'
      ? 'A serverless foundation for monitoring CloudWatch Logs Insights costs and sending Slack alerts.'
      : 'CloudWatch Logs Insights のコストを可視化し、Slack で通知するサーバーレス運用基盤。';
    document.querySelector('meta[name="description"]')?.setAttribute('content', description);
    document.querySelector('meta[property="og:description"]')?.setAttribute('content', description);
    document.querySelector('nav')?.setAttribute(
      'aria-label',
      language === 'en' ? 'Main navigation' : 'メインナビゲーション'
    );
    document.querySelector('.architecture__diagram')?.setAttribute(
      'aria-label',
      language === 'en' ? 'Architecture diagram' : 'アーキテクチャ図'
    );
  }

  applyTheme(getTheme());
  applyLanguage(localStorage.getItem(LANGUAGE_STORAGE_KEY) === 'ja' ? 'ja' : 'en');

  document.addEventListener('DOMContentLoaded', () => {
    const toggle = document.getElementById('themeToggle');
    const languageToggle = document.getElementById('languageToggle');

    toggle?.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme');
      const next = current === 'dark' ? 'light' : 'dark';
      localStorage.setItem(STORAGE_KEY, next);
      applyTheme(next);
    });

    languageToggle?.addEventListener('click', () => {
      const next = document.documentElement.lang === 'en' ? 'ja' : 'en';
      localStorage.setItem(LANGUAGE_STORAGE_KEY, next);
      applyLanguage(next);
    });

    systemDark.addEventListener('change', (e) => {
      if (!localStorage.getItem(STORAGE_KEY)) {
        applyTheme(e.matches ? 'dark' : 'light');
      }
    });

    // Scroll reveal
    const revealTargets = document.querySelectorAll(
      '.feature-card, .flow__step, .architecture, .code-window, .section__title, .section__subtitle'
    );

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible');
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: '0px 0px -40px 0px' }
    );

    revealTargets.forEach((el) => {
      el.classList.add('reveal');
      observer.observe(el);
    });
  });
})();
