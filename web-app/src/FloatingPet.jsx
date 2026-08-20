import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import * as api from "./api.js";

// A tiny global event bus for the pet, so any button anywhere in the app can
// make it happy without prop-drilling a callback down to it or wrapping the
// app in a context provider. `cheerPet()` is the public API -- callers don't
// need to know the event name or construct the Event themselves.
export const PET_HAPPY_EVENT = "ascend:pet-happy";

export function cheerPet() {
  window.dispatchEvent(new Event(PET_HAPPY_EVENT));
}

const HAPPY_DURATION_MS = 2200;

// Robot palette. Lavender shell (the brief's "learning" accent) rather than the brand
// green, so the mascot reads as its own character instead of a second copy of the logo.
const SHELL = "#C9C4E8";
const SHELL_DARK = "#9C8FD9";
const VISOR = "#14161C";
const EYE = "#22C55E";
const ACCENT = "#FF9E4A";
const OUTLINE = "#241F3A";

// ---------------------------------------------------------------------------
// the robot itself -- exported so Profile's MyPetCard and the Team gallery can
// draw the same character the floating widget does, at whatever size they need.
// ---------------------------------------------------------------------------

export function PetRobotSVG({ size = 64, mood = "idle", equippedItemIds = [] }) {
  const happy = mood === "happy";
  const has = (id) => equippedItemIds.includes(id);
  const headItem = ["crown", "antenna_bow"].find(has);
  const neckItem = ["scarf", "bowtie"].find(has);
  const wearingGlasses = has("sunglasses");
  const wearingJetpack = has("jetpack");

  return (
    <svg width={size} height={size * 1.3} viewBox="0 0 120 156" xmlns="http://www.w3.org/2000/svg">
      {wearingJetpack && (
        <>
          <rect x="8" y="84" width="11" height="32" rx="4" fill="#5B7DB1" stroke={OUTLINE} strokeWidth="1.5" />
          <rect x="101" y="84" width="11" height="32" rx="4" fill="#5B7DB1" stroke={OUTLINE} strokeWidth="1.5" />
          <path d="M11 116 L13.5 128 L16 116 Z" fill={ACCENT} opacity={happy ? 1 : 0.6} />
          <path d="M104 116 L106.5 128 L109 116 Z" fill={ACCENT} opacity={happy ? 1 : 0.6} />
        </>
      )}

      <rect x="44" y="126" width="11" height="20" rx="5" fill={SHELL_DARK} />
      <rect x="65" y="126" width="11" height="20" rx="5" fill={SHELL_DARK} />

      <rect x="28" y="80" width="64" height="50" rx="16" fill={SHELL} stroke={OUTLINE} strokeWidth="2" />
      <circle cx="60" cy="104" r="6" fill={ACCENT} />
      <circle cx="58" cy="102" r="2" fill="#fff" opacity="0.7" />

      <rect x="14" y="86" width="14" height="36" rx="7" fill={SHELL_DARK} />
      <rect x="92" y="86" width="14" height="36" rx="7" fill={SHELL_DARK} />

      <rect x="50" y="68" width="20" height="16" rx="4" fill={SHELL_DARK} />

      {neckItem === "bowtie" && (
        <>
          <path d="M60 71 L46 65 L46 81 Z" fill="#E0524A" stroke={OUTLINE} strokeWidth="1.2" />
          <path d="M60 71 L74 65 L74 81 Z" fill="#E0524A" stroke={OUTLINE} strokeWidth="1.2" />
          <circle cx="60" cy="71" r="4" fill="#B83A33" />
        </>
      )}
      {neckItem === "scarf" && (
        <>
          <rect x="44" y="66" width="32" height="10" rx="5" fill="#14B8A6" stroke={OUTLINE} strokeWidth="1.2" />
          <path d="M48 75 L44 92 L54 88 Z" fill="#14B8A6" stroke={OUTLINE} strokeWidth="1.2" />
        </>
      )}

      <circle cx="26" cy="43" r="5" fill={SHELL_DARK} />
      <circle cx="94" cy="43" r="5" fill={SHELL_DARK} />
      <rect x="26" y="16" width="68" height="54" rx="18" fill={SHELL} stroke={OUTLINE} strokeWidth="2" />

      <line x1="60" y1="16" x2="60" y2="3" stroke={SHELL_DARK} strokeWidth="4" strokeLinecap="round" />
      <circle cx="60" cy="3" r="6" fill={ACCENT} className={happy ? "pet-antenna-pulse" : ""} />

      {/* Visor fills ~3/4 of the head (52 of 68 wide, 39 of 54 tall) so the face reads
          as the character rather than a small screen floating in a big shell. */}
      <rect x="34" y="24" width="52" height="39" rx="13" fill={VISOR} />
      {wearingGlasses ? (
        <>
          <rect x="33" y="27" width="54" height="25" rx="13" fill="#1B1E27" stroke={OUTLINE} strokeWidth="1.2" />
          <circle cx="49" cy="40" r="8.5" fill="#2A2E38" />
          <circle cx="71" cy="40" r="8.5" fill="#2A2E38" />
          <line x1="57.5" y1="40" x2="62.5" y2="40" stroke="#1B1E27" strokeWidth="4" />
          <path d="M43 34 Q49 30 55 34" stroke="#5C6270" strokeWidth="1.5" fill="none" />
        </>
      ) : happy ? (
        <>
          <path d="M45 43 Q49 35 53 43" stroke={EYE} strokeWidth="3.5" strokeLinecap="round" fill="none" />
          <path d="M67 43 Q71 35 75 43" stroke={EYE} strokeWidth="3.5" strokeLinecap="round" fill="none" />
          <path d="M46 51 Q60 60 74 51" stroke={EYE} strokeWidth="3" strokeLinecap="round" fill="none" />
        </>
      ) : (
        <>
          <circle cx="49" cy="41" r="8.5" fill={EYE} opacity="0.22" />
          <circle cx="71" cy="41" r="8.5" fill={EYE} opacity="0.22" />
          <circle cx="49" cy="41" r="5.5" fill={EYE} />
          <circle cx="71" cy="41" r="5.5" fill={EYE} />
        </>
      )}

      {headItem === "crown" && (
        <>
          <polygon points="38,18 46,4 54,15 60,2 66,15 74,4 82,18"
            fill="#FFC94A" stroke={OUTLINE} strokeWidth="1.2" strokeLinejoin="round" />
          <circle cx="46" cy="10" r="2" fill="#B8890E" />
          <circle cx="60" cy="8" r="2" fill="#B8890E" />
          <circle cx="74" cy="10" r="2" fill="#B8890E" />
        </>
      )}
      {headItem === "antenna_bow" && (
        <>
          <path d="M60 8 L48 1 L48 14 Z" fill="#FF6FA5" stroke={OUTLINE} strokeWidth="1" />
          <path d="M60 8 L72 1 L72 14 Z" fill="#FF6FA5" stroke={OUTLINE} strokeWidth="1" />
          <circle cx="60" cy="8" r="3.5" fill="#E0518B" />
        </>
      )}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// the shop -- reused by the floating widget's click handler and by
// Profile's "Customize" button, so there is one shop rather than two.
// ---------------------------------------------------------------------------

export function PetShopModal({ onClose, onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busyItem, setBusyItem] = useState(null);

  const load = () => {
    api.getPet().then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  };
  useEffect(load, []);

  const act = async (fn, itemId) => {
    setBusyItem(itemId);
    setError(null);
    try {
      const next = await fn(itemId);
      setData(next);
      onChanged?.(next);
      cheerPet();
    } catch (e) {
      setError(e.message || "That didn't work.");
    }
    setBusyItem(null);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(30,27,46,0.6)", pointerEvents: "auto" }} onClick={onClose}>
      <div style={{ fontFamily: "'Inter', system-ui, sans-serif" }} className="bg-white rounded-2xl p-5 max-w-lg w-full max-h-[85vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 style={{ fontFamily: "'Playfair Display', Georgia, serif", color: "#0F1214" }} className="font-bold text-lg">Customize your robot</h3>
          <button onClick={onClose} style={{ color: "#5C6B62" }}><X size={18} /></button>
        </div>

        {!data && !error && <p style={{ color: "#5C6B62" }} className="text-sm">Loading…</p>}
        {error && <p style={{ color: "#D8443C" }} className="text-sm mb-3">{error}</p>}

        {data && (
          <>
            <div className="flex items-center gap-4 mb-5 flex-wrap">
              <div className="rounded-2xl flex items-center justify-center shrink-0" style={{ width: 130, height: 150, background: "#E3F1EB" }}>
                <PetRobotSVG size={82} mood="idle" equippedItemIds={data.equippedItemIds} />
              </div>
              <div>
                <p style={{ color: "#0F1214" }} className="text-2xl font-bold">{data.pointsBalance} pts</p>
                <p style={{ color: "#5C6B62" }} className="text-xs">
                  {data.pointsEarned} earned from {data.trainingsCompleted} completed training{data.trainingsCompleted === 1 ? "" : "s"} · 100 pts each
                </p>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              {data.catalog.map((item) => {
                const owned = data.ownedItemIds.includes(item.id);
                const equipped = data.equippedItemIds.includes(item.id);
                const affordable = data.pointsBalance >= item.cost;
                const busy = busyItem === item.id;
                return (
                  <div key={item.id} style={{ borderColor: equipped ? "#22C55E" : "#E4E7E2" }}
                    className="border rounded-xl p-3 flex flex-col gap-2">
                    <div className="flex items-center justify-between">
                      <span style={{ color: "#0F1214" }} className="text-sm font-semibold">{item.name}</span>
                      {!owned && <span style={{ color: "#5C6B62" }} className="text-xs font-semibold">{item.cost} pts</span>}
                    </div>
                    {equipped ? (
                      <button disabled={busy} onClick={() => act(api.equipPetItem, item.id)}
                        style={{ background: "#DFF7F3", color: "#14B8A6" }}
                        className="text-xs font-semibold rounded-lg py-1.5">
                        {busy ? "…" : "Equipped · Take off"}
                      </button>
                    ) : owned ? (
                      <button disabled={busy} onClick={() => act(api.equipPetItem, item.id)}
                        style={{ background: "#147A4D", color: "#fff" }}
                        className="text-xs font-semibold rounded-lg py-1.5">
                        {busy ? "…" : "Wear"}
                      </button>
                    ) : (
                      <button disabled={busy || !affordable} onClick={() => act(api.purchasePetItem, item.id)}
                        style={{
                          background: affordable ? "#147A4D" : "#F3EDE1",
                          color: affordable ? "#fff" : "#9A93A8",
                        }}
                        className="text-xs font-semibold rounded-lg py-1.5">
                        {busy ? "…" : affordable ? `Buy for ${item.cost} pts` : `Need ${item.cost - data.pointsBalance} more pts`}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// the floating widget
// ---------------------------------------------------------------------------

export default function FloatingPet() {
  const [mood, setMood] = useState("idle"); // "idle" | "happy"
  const [equippedItemIds, setEquippedItemIds] = useState([]);
  const [showShop, setShowShop] = useState(false);
  const revertTimer = useRef(null);

  useEffect(() => {
    api.getPet().then((d) => setEquippedItemIds(d.equippedItemIds)).catch(() => {});
  }, []);

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
    <div className="fixed bottom-5 right-5 z-40 select-none" style={{ pointerEvents: "none" }}>
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
        @keyframes pet-antenna-pulse-kf { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
        .pet-antenna-pulse { animation: pet-antenna-pulse-kf 550ms ease-in-out infinite; transform-origin: 60px 3px; }
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
        onClick={() => setShowShop(true)}
        aria-label="Customize your Ascend robot"
        style={{
          pointerEvents: "auto",
          animation: happy ? "pet-happy-bounce 650ms ease-in-out" : "pet-float 2.6s ease-in-out infinite",
        }}
        className="block cursor-pointer"
      >
        <PetRobotSVG size={88} mood={mood} equippedItemIds={equippedItemIds} />
      </button>

      {showShop && (
        <PetShopModal
          onClose={() => setShowShop(false)}
          onChanged={(d) => setEquippedItemIds(d.equippedItemIds)}
        />
      )}
    </div>
  );
}

const SPARKLES = [
  { x: 4, y: -4, delay: 0 },
  { x: 60, y: 4, delay: 120 },
  { x: 30, y: -16, delay: 220 },
];
