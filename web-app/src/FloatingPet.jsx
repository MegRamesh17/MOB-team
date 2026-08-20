import { useEffect, useRef, useState } from "react";

// A tiny global event bus for the pet, so any button anywhere in the app can
// make it happy without prop-drilling a callback down to it or wrapping the
// app in a context provider. `cheerPet()` is the public API -- callers don't
// need to know the event name or construct the Event themselves.
export const PET_HAPPY_EVENT = "ascend:pet-happy";

export function cheerPet() {
  window.dispatchEvent(new Event(PET_HAPPY_EVENT));
}

const HAPPY_DURATION_MS = 2200;

// Colors mirror the Ascend palette (App.jsx's `C`) without importing it --
// this widget stays a self-contained, drop-in-anywhere component.
const BODY = "#147A4D";
const BELLY = "#88C7B7";
const INK = "#0F1214";
const CREAM = "#F0EAD8";

export default function FloatingPet() {
  const [mood, setMood] = useState("idle"); // "idle" | "happy"
  const revertTimer = useRef(null);

  useEffect(() => {
    function onHappy() {
      setMood("happy");
      clearTimeout(revertTimer.current);
      revertTimer.current = setTimeout(() => setMood("idle"), HAPPY_DURATION_MS);
    }
    window.addEventListener(PET_HAPPY_EVENT, onHappy);
    return () => {
      window.removeEventListener(PET_HAPPY_EVENT, onHappy);
      clearTimeout(revertTimer.current);
    };
  }, []);

  const happy = mood === "happy";

  return (
    <div className="fixed bottom-5 right-5 z-50 select-none" style={{ pointerEvents: "none" }}>
      <style>{`
        @keyframes pet-float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-6px); } }
        @keyframes pet-happy-bounce {
          0%, 100% { transform: translateY(0) scale(1); }
          30% { transform: translateY(-18px) scale(1.06); }
          55% { transform: translateY(0) scale(0.96); }
          75% { transform: translateY(-8px) scale(1.02); }
        }
        @keyframes pet-sparkle {
          0% { opacity: 0; transform: translateY(0) scale(0.4); }
          35% { opacity: 1; }
          100% { opacity: 0; transform: translateY(-24px) scale(1); }
        }
      `}</style>

      {happy && (
        <div className="absolute inset-0" aria-hidden="true">
          {SPARKLES.map((s, i) => (
            <span
              key={i}
              className="absolute text-sm"
              style={{ left: s.x, top: s.y, animation: `pet-sparkle 800ms ease-out ${s.delay}ms 1` }}
            >
              ✨
            </span>
          ))}
        </div>
      )}

      <button
        type="button"
        onClick={cheerPet}
        aria-label={happy ? "Ascend pet is happy" : "Say hi to the Ascend pet"}
        style={{
          pointerEvents: "auto",
          animation: happy ? "pet-happy-bounce 650ms ease-in-out" : "pet-float 2.6s ease-in-out infinite",
        }}
        className="block cursor-pointer"
      >
        <PetFace happy={happy} />
      </button>
    </div>
  );
}

const SPARKLES = [
  { x: -8, y: -6, delay: 0 },
  { x: 36, y: 0, delay: 120 },
  { x: 12, y: -20, delay: 220 },
];

function PetFace({ happy }) {
  return (
    <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
      <ellipse cx="32" cy="36" rx="22" ry="20" fill={BODY} />
      <ellipse cx="32" cy="42" rx="13" ry="10" fill={BELLY} />
      <ellipse cx="20" cy="16" rx="6" ry="10" fill={BODY} transform="rotate(-25 20 16)" />
      <ellipse cx="44" cy="16" rx="6" ry="10" fill={BODY} transform="rotate(25 44 16)" />
      {happy ? (
        <>
          <path d="M22 32 Q25 28 28 32" stroke={INK} strokeWidth="2.5" strokeLinecap="round" fill="none" />
          <path d="M36 32 Q39 28 42 32" stroke={INK} strokeWidth="2.5" strokeLinecap="round" fill="none" />
          <path d="M24 40 Q32 48 40 40" stroke={INK} strokeWidth="2.5" strokeLinecap="round" fill="none" />
        </>
      ) : (
        <>
          <circle cx="25" cy="31" r="2.6" fill={INK} />
          <circle cx="39" cy="31" r="2.6" fill={INK} />
          <path d="M27 41 Q32 44 37 41" stroke={INK} strokeWidth="2.2" strokeLinecap="round" fill="none" />
        </>
      )}
      <circle cx="17" cy="38" r="3.5" fill={CREAM} opacity="0.5" />
      <circle cx="47" cy="38" r="3.5" fill={CREAM} opacity="0.5" />
    </svg>
  );
}
