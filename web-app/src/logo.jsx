/**
 * Quizrant wordmark.
 *
 * This is a stand-in. The original design inlined the real logo as a base64 PNG;
 * to use it, drop the file at web-app/public/logo.png and swap the return below for:
 *
 *     return <img src="/logo.png" alt="Quizrant" style={{ height: size }} />;
 *
 * Drawn rather than embedded so the repo carries no large binary blob and the mark
 * stays crisp at any size.
 */
export function Logo({ size = 28 }) {
  return (
    <svg height={size} viewBox="0 0 148 32" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="0" y="3" width="26" height="26" rx="8" fill="#6423C9" />
      <circle cx="13" cy="16" r="7" fill="none" stroke="#fff" strokeWidth="2.6" />
      <line x1="16.5" y1="19.5" x2="20.5" y2="23.5" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" />
      <text
        x="34" y="22"
        fontFamily="Fraunces, Georgia, serif" fontSize="18" fontWeight="700"
        fill="#1E1B2E" letterSpacing="-0.4"
      >
        Quizrant
      </text>
    </svg>
  );
}
