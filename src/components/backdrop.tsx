/**
 * Decorative layer. Pure ornament -- a fixed sheet of graph paper with
 * hand-drawn marginalia scribbled over it, the way an analyst annotates a
 * printout while working through a release.
 *
 * Everything here is aria-hidden and pointer-events:none. It must never
 * intercept a click, never enter the accessibility tree, and never carry
 * meaning: the numbers on this screen are the record, not the drawings.
 */

export function Backdrop() {
  return (
    <div className="backdrop" aria-hidden="true">
      <div className="backdrop-grid" />

      {/* Squiggle -- the underline you draw when a figure matters. */}
      <div className="doodle doodle--1">
        <svg viewBox="0 0 140 46" fill="none">
          <path className="stroke" d="M3 30c10-16 20 12 30-2s20 14 30-1 20 12 30-3 20 10 27 2" />
          <path className="stroke" d="M14 42c24 4 66 3 96-6" strokeWidth="1.6" />
        </svg>
      </div>

      {/* Concentric orbit -- the privacy unit and its contribution bound. */}
      <div className="doodle doodle--2">
        <svg viewBox="0 0 120 120" fill="none">
          <circle className="stroke" cx="60" cy="60" r="52" strokeDasharray="9 11" />
          <circle className="stroke" cx="60" cy="60" r="31" />
          <circle className="stroke" cx="60" cy="60" r="8" strokeWidth="4" />
          <path className="stroke" d="M60 2v14M60 104v14M2 60h14M104 60h14" strokeWidth="1.8" />
        </svg>
      </div>

      {/* Noise distribution -- a Laplace curve sketched freehand. */}
      <div className="doodle doodle--3">
        <svg viewBox="0 0 180 96" fill="none">
          <path className="stroke" d="M6 88c34 0 44-78 84-78s50 78 84 78" />
          <path className="stroke" d="M6 88h168" strokeWidth="1.6" />
          <path className="stroke" d="M90 12v76" strokeDasharray="5 7" strokeWidth="1.6" />
          <path className="stroke" d="M54 88c0-26 12-46 36-46s36 20 36 46" strokeWidth="1.4" opacity=".7" />
        </svg>
      </div>

      {/* Asterisk -- the footnote mark, because every number here has one. */}
      <div className="doodle doodle--4">
        <svg viewBox="0 0 60 60" fill="none">
          <path className="stroke" d="M30 6v48M9 18l42 24M51 18L9 42" strokeWidth="3" />
        </svg>
      </div>

      {/* Arrow -- pointing at the thing you are about to spend budget on. */}
      <div className="doodle doodle--5">
        <svg viewBox="0 0 100 78" fill="none">
          <path className="stroke" d="M6 8c30 2 54 18 62 52" />
          <path className="stroke" d="M52 52l16 10 4-19" />
        </svg>
      </div>
    </div>
  );
}

/**
 * Inline marginalia a section can carry next to a heading. Small, quiet, and
 * decorative only -- the label beside it always carries the actual meaning.
 */
export function Mark({ shape, size = 18 }: { shape: "star" | "cross" | "wave" | "corner"; size?: number }) {
  const paths: Record<typeof shape, React.ReactNode> = {
    star: <path d="M12 2v20M4 6l16 12M20 6L4 18" />,
    cross: <path d="M4 4l16 16M20 4L4 20" />,
    wave: <path d="M2 16c4-10 8 6 12-2s5 5 8 1" />,
    corner: <path d="M3 21V3h18" />,
  };
  return (
    <span className="mark" aria-hidden="true">
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        {paths[shape]}
      </svg>
    </span>
  );
}
