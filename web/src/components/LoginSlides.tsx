import { ReactNode, useEffect, useRef, useState } from 'react';

/** Calm login rotation: one real image + one short line per slide.
 *  ~5.5s per slide, 1.2s crossfade. Pauses on hover and when the tab is hidden.
 *  prefers-reduced-motion shows the first slide only. Images load one ahead. */
const SLIDE_MS = 5500;

type Slide = { src: string; widths: number[]; alt: string; line: string; pos?: string; frame?: boolean };

export const DISPATCH_SLIDES: Slide[] = [
  { src: '/login/jobs', widths: [720], frame: true, alt: 'Bricks Today screen with the next shoot and Navigate', line: 'Jobs dispatched to your phone.' },
  { src: '/login/shoots', widths: [960, 1600], pos: '40% 55%', alt: 'Row of brick homes, listing photo by a Bricks photographer', line: 'Listing shoots near you, at an exact time.' },
  { src: '/login/drone', widths: [960, 1600], pos: '50% 50%', alt: 'Drone view of a new development', line: 'Drone jobs for licensed pilots.' },
];

function useReducedMotion() {
  const q = '(prefers-reduced-motion: reduce)';
  const [r, setR] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.(q).matches);
  useEffect(() => {
    const m = window.matchMedia?.(q);
    if (!m) return;
    const on = () => setR(m.matches);
    m.addEventListener?.('change', on);
    return () => m.removeEventListener?.('change', on);
  }, []);
  return r;
}

export function LoginSlides({ slides = DISPATCH_SLIDES }: { slides?: Slide[] }) {
  const reduced = useReducedMotion();
  const [i, setI] = useState(0);
  const [loaded, setLoaded] = useState<Set<number>>(() => new Set([0, 1 % slides.length]));
  const hover = useRef(false);
  const [hidden, setHidden] = useState(typeof document !== 'undefined' && document.hidden);

  useEffect(() => {
    const on = () => setHidden(document.hidden);
    document.addEventListener('visibilitychange', on);
    return () => document.removeEventListener('visibilitychange', on);
  }, []);
  useEffect(() => {
    if (reduced || hidden || slides.length < 2) return;
    const t = setInterval(() => { if (!hover.current) setI((x) => (x + 1) % slides.length); }, SLIDE_MS);
    return () => clearInterval(t);
  }, [reduced, hidden, slides.length]);
  useEffect(() => {
    const next = (i + 1) % slides.length;
    setLoaded((s) => (s.has(next) ? s : new Set([...s, next])));
  }, [i, slides.length]);

  const cur = reduced ? 0 : i;
  return (
    <div className="ls" onMouseEnter={() => { hover.current = true; }} onMouseLeave={() => { hover.current = false; }} aria-roledescription="carousel" aria-label="What Bricks does for photographers">
      {slides.map((s, k) => (
        <div key={s.src} className={k === cur ? 'ls-slide is-on' : 'ls-slide'} aria-hidden={k !== cur}>
          {loaded.has(k) && (
            <img
              className={s.frame ? 'ls-frame' : 'ls-img'}
              src={`${s.src}-${s.widths[0]}.webp`}
              srcSet={s.widths.map((w) => `${s.src}-${w}.webp ${w}w`).join(', ')}
              sizes="(min-width: 1600px) 720px, 45vw"
              alt={s.alt}
              decoding="async"
              loading={k === 0 ? 'eager' : 'lazy'}
              style={s.pos ? { objectPosition: s.pos } : undefined}
            />
          )}
        </div>
      ))}
      <div className="ls-shade" />
      <div className="ls-copy">
        <div key={cur} className="ls-line" aria-live="polite">{slides[cur].line}</div>
        {!reduced && slides.length > 1 && (
          <div className="ls-ticks" aria-hidden="true">
            {slides.map((s, k) => <span key={s.src} className={k === cur ? 'is-on' : ''} />)}
          </div>
        )}
      </div>
    </div>
  );
}

export function LoginShell({ children }: { children: ReactNode }) {
  return (
    <div className="login-split">
      <div className="login-wrap">{children}</div>
      <aside className="login-aside"><LoginSlides /></aside>
    </div>
  );
}
