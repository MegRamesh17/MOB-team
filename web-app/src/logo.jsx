/**
 * Ascend wordmark.
 *
 * The mark is a two-leaf sprout with a bud above it -- concept #8 from the brand board
 * -- rendered inline so the repo carries no binary asset and it stays crisp at any size.
 * `light` swaps the wordmark text for the dark sidebar; the leaf badge itself has enough
 * internal contrast (white leaf on green) to read the same on either background.
 *
 * This is a stand-in. To swap in real artwork later, drop the file at
 * web-app/public/logo.png and replace the return below with:
 *
 *     return <img src="/logo.png" alt="Ascend" style={{ height: size }} />;
 */
export function Logo({ size = 28, light = false }) {
  return (
    <svg height={size} viewBox="0 0 130 32" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="0" y="3" width="26" height="26" rx="8" fill="#147A4D" />
      <ellipse cx="9.5" cy="17" rx="2.6" ry="6.6" fill="#fff" transform="rotate(-38 9.5 17)" />
      <ellipse cx="16.5" cy="17" rx="2.6" ry="6.6" fill="#fff" transform="rotate(38 16.5 17)" />
      <circle cx="13" cy="6.8" r="2" fill="#fff" />
      <text
        x="32" y="22"
        fontFamily="Fraunces, Georgia, serif" fontSize="18" fontWeight="600"
        fill={light ? "#F0EAD8" : "#12201A"} letterSpacing="-0.3"
      >
        ascend
      </text>
    </svg>
  );
}
