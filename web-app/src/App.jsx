import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  LogOut, BookOpen, Award, Users, CheckCircle2, Circle, Lock,
  ChevronRight, X, AlertCircle, Clock, ArrowLeft, User, Star,
  Trophy, Flame, Target, Mail, Briefcase, Share2, Download, Copy,
  Loader2, RefreshCw, Upload, FileText, Link2, Search, Send,
  Settings as SettingsIcon, Box, Calendar, ShieldCheck, LayoutGrid, Trash2,
} from "lucide-react";
import * as api from "./api";
import { Logo } from "./logo.jsx";
import FloatingPet, { cheerPet, PetRobotSVG, PetShopModal } from "./FloatingPet.jsx";

/**
 * WHAT IS REAL AND WHAT IS NOT.
 *
 * Wired to the backend — these reflect the actual question bank and this learner's
 * actual answers:
 *   trainings, modules, lesson text, quiz questions, grading, scores,
 *   certificates, Q score, mastery breakdown, the manager's team roster and
 *   completion numbers (api.teamCompletion(), the same qscore.standing() arithmetic
 *   /qscore uses), and reminder sends (api.sendReminder() -- real, though whether it
 *   actually delivers depends on RESEND_API_KEY being set for the environment)
 *
 * Still mock — no backend exists for them yet, and they are marked in the UI rather
 * than left to look real:
 *   badges, the companion pet, focus timer, teammates
 *
 * The mock parts are kept because they are the product's design direction. They are
 * not kept quiet: pretending a number is measured when it is invented is how a demo
 * turns into a wrong decision.
 */

// ---------- design tokens ----------
// Exact values from the brand brief: forest green primary, fresh green + sage as
// lighter green steps, teal/coral doing semantic work (compliant / needs-attention),
// lavender reserved for learning-path accents. Token KEYS keep their older names
// (green900/700/600/500/300, "mint" for the pale tint) so the ~100 call sites that
// reference them did not need to change one at a time -- only the values did.
const C = {
  ink: "#0F1214",       // brief's "Charcoal"
  sub: "#5C6B62",
  green900: "#0E5536",  // derived deep shade, no brief value for this step
  green700: "#147A4D",  // brief: Forest green
  green600: "#1AA05C",  // derived mid step
  green500: "#22C55E",  // brief: Fresh green
  green300: "#88C7B7",  // brief: Sage
  mint: "#E3F1EB",       // derived pale tint of sage/forest
  paper: "#FFFFFF",      // white, not the brief's cream -- per direct request
  sand: "#F3EDE1",       // brief: Soft sand -- subtle section backgrounds
  line: "#E4E7E2",       // cooled from the brief's warm tan now the ground is white, not cream
  amber: "#FF9E4A",      // brief: Coral/orange
  amberBg: "#FFEFDD",
  success: "#14B8A6",    // brief: Teal
  successBg: "#DFF7F3",
  danger: "#D8443C",
  dangerBg: "#FCEBEA",
  purple: "#6D5CE7",     // brief: Lavender
  purpleBg: "#EAE7FC",
  rail: "#0F1214",       // brief: Charcoal/sidebar
};

const font = { fontFamily: "'Inter', system-ui, sans-serif" };
const display = { fontFamily: "'Playfair Display', Georgia, serif" };

// ---------- static (design-only) data ----------
const FOCUS_PRIORITIES = [
  { id: "urgent", label: "Urgent", color: "#D8443C", bg: "#FCEBEA" },
  { id: "deep", label: "Deep Work", color: "#6D5CE7", bg: "#EAE7FC" },
  { id: "quick", label: "Quick Task", color: "#E07A1F", bg: "#FDECD9" },
  { id: "learning", label: "Learning", color: "#1F9D55", bg: "#E7F7EE" },
];

const FOCUS_DURATIONS = [
  { label: "25 min", sec: 1500 },
  { label: "15 min", sec: 900 },
  { label: "5 min", sec: 300 },
];

const TEAM = [
  { name: "Priya Nair", role: "Software Engineer II", department: "Engineering", completion: 100, status: "completed", overdue: 0, trainingsCompleted: 12 },
  { name: "Marcus Webb", role: "Support Specialist", department: "Customer Support", completion: 40, status: "in-progress", overdue: 1, trainingsCompleted: 2 },
  { name: "Aisha Rahman", role: "Data Analyst", department: "Data & Analytics", completion: 0, status: "not-started", overdue: 0, trainingsCompleted: 0 },
  { name: "Daniel Cho", role: "Software Engineer I", department: "Engineering", completion: 75, status: "in-progress", overdue: 0, trainingsCompleted: 3 },
];

const ROSTER = [
  { name: "Daniel Cho", role: "Software Engineer I", department: "Engineering", trainingsCompleted: 3 },
  { name: "Emily Zhang", role: "Software Engineer I", department: "Engineering", trainingsCompleted: 5 },
  { name: "Jordan Lee", role: "Software Engineer I", department: "Engineering", trainingsCompleted: 1 },
  { name: "Ravi Patel", role: "Software Engineer I", department: "Engineering", trainingsCompleted: 8 },
  { name: "Priya Nair", role: "Engineering Manager", department: "Engineering", trainingsCompleted: 9 },
  { name: "Sofia Martinez", role: "Software Engineer II", department: "Engineering", trainingsCompleted: 12 },
  { name: "Marcus Webb", role: "Support Specialist", department: "Customer Support", trainingsCompleted: 2 },
  { name: "Aisha Rahman", role: "Data Analyst", department: "Data & Analytics", trainingsCompleted: 0 },
];

const PROFILES = {
  employee: { name: "Daniel Cho", role: "Software Engineer I", email: "d.cho@quadranttechnologies.com", joined: "Feb 2025" },
  manager: { name: "Priya Nair", role: "Engineering Manager", email: "p.nair@quadranttechnologies.com", joined: "Nov 2022" },
};

// The catalog only -- title/description/icon. Whether each one is actually earned
// comes from GET /api/me's badges field (see Profile below); ids 2 and 6 have no
// server-side criterion yet (no "privacy" question category, no assignment due-date
// concept) and so are always locked rather than showing a made-up earned date.
const BADGES = [
  { id: 1, title: "First Steps", desc: "Completed your first training", icon: Star },
  { id: 2, title: "Privacy Pro", desc: "Scored 90%+ on a privacy quiz", icon: Award },
  { id: 3, title: "On a Roll", desc: "6-training completion streak", icon: Flame },
  { id: 4, title: "Sharpshooter", desc: "Passed a quiz with a perfect score", icon: Target },
  { id: 5, title: "Top of the Class", desc: "Reached a 90+ Q score", icon: Trophy },
  { id: 6, title: "Early Bird", desc: "Finished a training before its due date", icon: CheckCircle2 },
];

// ---------- data loading ----------
function useAsync(fn, deps = []) {
  const [state, setState] = useState({ loading: true, error: null, data: null });
  const run = useCallback(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn()
      .then((data) => alive && setState({ loading: false, error: null, data }))
      .catch((error) => alive && setState({ loading: false, error, data: null }));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(run, [run]);
  return { ...state, reload: run };
}

function Loading({ label = "Loading…" }) {
  return (
    <div className="flex items-center gap-2 py-10" style={{ color: C.sub }}>
      <Loader2 size={16} className="animate-spin" />
      <span className="text-sm">{label}</span>
    </div>
  );
}

/**
 * Errors are shown with the server's own message, which is written to be actionable
 * ("run quizgen generate, then quizgen review --approve-all"). Replacing it with
 * "Something went wrong" would throw away the one thing that tells you what to do.
 */
function ErrorBox({ error, onRetry }) {
  return (
    <div style={{ background: C.dangerBg, borderColor: C.danger }} className="border rounded-xl p-4 my-4">
      <div className="flex items-start gap-2.5">
        <AlertCircle size={17} color={C.danger} className="shrink-0 mt-0.5" />
        <div className="flex-1">
          <p style={{ color: C.danger }} className="text-sm font-semibold mb-0.5">
            {error?.title || "Couldn't reach the API"}
          </p>
          <p style={{ color: C.ink }} className="text-sm leading-snug opacity-90">{error?.message}</p>
          {onRetry && (
            <button onClick={onRetry} style={{ color: C.danger }} className="text-xs font-semibold mt-2 flex items-center gap-1">
              <RefreshCw size={12} /> Try again
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function MockNote({ children }) {
  return (
    <span
      title="Not backed by real data yet"
      style={{ borderColor: C.line, color: C.sub }}
      className="text-[10px] font-semibold px-2 py-0.5 rounded-full border shrink-0"
    >
      {children || "sample data"}
    </span>
  );
}

// ---------- small UI pieces ----------
function StatusPill({ status }) {
  const map = {
    completed: { bg: C.successBg, fg: C.success, label: "Completed" },
    "in-progress": { bg: C.amberBg, fg: C.amber, label: "In progress" },
    "not-started": { bg: C.mint, fg: C.green700, label: "Not started" },
    locked: { bg: "#F1F0F3", fg: "#9A93A8", label: "Locked" },
  };
  const s = map[status] || map["not-started"];
  return (
    <span style={{ background: s.bg, color: s.fg, fontWeight: 600 }}
      className="text-xs px-2.5 py-1 rounded-full inline-block whitespace-nowrap">
      {s.label}
    </span>
  );
}

/**
 * Where a question came from. Documented means it quotes an indexed passage;
 * RoleKnowledge means the model wrote it from its own knowledge and may not state
 * company policy. Showing this lets a learner disputing a question see its basis
 * without asking anyone.
 */
function ProvenanceBadge({ provenance, sourceTitle }) {
  // Provenance arrives with the grade, so before a question is answered there is
  // nothing to say. Falling through to a default here asserted "Role knowledge" on
  // every unanswered question — including ones that turn out to be Documented.
  if (!provenance) return null;
  const documented = provenance === "Documented";
  const label = documented
    ? `See: ${sourceTitle ? sourceTitle.slice(0, 28) : "policy doc"}`
    : provenance === "ExternalSource" ? "Vetted source" : "Role knowledge";
  return (
    <span className="text-[11px] px-2 py-0.5 rounded-full border whitespace-nowrap shrink-0"
      style={{ borderColor: C.line, color: C.sub }}>
      {label}
    </span>
  );
}

function MasteryRing({ value, size = 64, stroke = 7 }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, Math.round(value || 0)));
  const dash = (pct / 100) * c;
  const gradId = `mastery-ring-grad-${size}`;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <defs>
        <linearGradient id={gradId} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor={C.green500} />
          <stop offset="100%" stopColor={C.green700} />
        </linearGradient>
      </defs>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={C.mint} strokeWidth={stroke} />
      <circle
        cx={size / 2} cy={size / 2} r={r} fill="none"
        stroke={`url(#${gradId})`} strokeWidth={stroke} strokeLinecap="round"
        strokeDasharray={`${dash} ${c - dash}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text x="50%" y="52%" textAnchor="middle" dominantBaseline="middle"
        style={{ ...display, fontWeight: 700, fontSize: size * 0.26, fill: C.ink, fontVariantNumeric: "tabular-nums" }}>
        {pct}
      </text>
    </svg>
  );
}

function TimerRing({ progress, color, size = 160, stroke = 12, label, sublabel }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(1, progress));
  const dash = pct * c;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={C.line} strokeWidth={stroke} />
      <circle
        cx={size / 2} cy={size / 2} r={r} fill="none"
        stroke={color} strokeWidth={stroke} strokeLinecap="round"
        strokeDasharray={`${dash} ${c - dash}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: "stroke-dasharray 0.3s linear" }}
      />
      <text x="50%" y="47%" textAnchor="middle" dominantBaseline="middle"
        style={{ ...display, fontWeight: 700, fontSize: size * 0.19, fill: C.ink, fontVariantNumeric: "tabular-nums" }}>
        {label}
      </text>
      {sublabel && (
        <text x="50%" y="63%" textAnchor="middle" dominantBaseline="middle"
          style={{ ...font, fontWeight: 600, fontSize: size * 0.075, fill: C.sub }}>
          {sublabel}
        </text>
      )}
    </svg>
  );
}

function Button({ children, onClick, variant = "primary", disabled, className = "", ...rest }) {
  const styles = {
    primary: { background: C.green700, color: "#fff" },
    ghost: { background: "transparent", color: C.green700, border: `1px solid ${C.line}` },
    subtle: { background: C.mint, color: C.green700 },
  };
  const [hover, setHover] = useState(false);
  const hoverBg = variant === "primary" ? C.green900 : variant === "subtle" ? C.green300 : C.mint;
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        ...styles[variant],
        background: hover && !disabled ? hoverBg : styles[variant].background,
        transition: "background 120ms ease",
        opacity: disabled ? 0.5 : 1,
        cursor: disabled ? "not-allowed" : "pointer",
      }}
      className={`px-4 py-2.5 rounded-xl font-semibold text-sm ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

// ---------- Login ----------
/**
 * One sign-in. No persona toggle, no role picker.
 *
 * The old screen asked you to choose "employee or manager" and then pick your own role
 * from a dropdown, and the server believed both. That is not a login, it is a costume
 * change: an employee could see another role's material by selecting it. Role and
 * permission tier now come from the account, inside a signed token, and there is nothing
 * here to choose.
 */
function Login({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const { data: h } = useAsync(() => api.health().catch(() => null), []);

  const submit = async (e) => {
    e?.preventDefault();
    if (!email || !password || busy) return;
    setBusy(true);
    setError(null);
    try {
      onLogin(await api.login(email.trim(), password));
    } catch (err) {
      setError(err.message || "Sign-in failed");
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center" style={{ ...font, background: C.paper }}>
      <div className="w-full max-w-md mx-4 bg-white rounded-2xl shadow-xl overflow-hidden" style={{ border: `1px solid ${C.line}` }}>
        <div style={{ background: C.green700, height: 4 }} />
        <div className="px-8 pt-8 pb-6">
          <div className="flex items-center mb-8"><Logo size={30} /></div>
          <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">Sign in</h1>
          <p style={{ color: C.sub }} className="text-sm mb-6">Quiz-based compliance training</p>

          <form onSubmit={submit}>
            <div className="mb-4">
              <label style={{ color: C.ink }} className="text-sm font-semibold block mb-2">Email</label>
              <input
                type="email" autoComplete="username" autoFocus
                value={email} onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                style={{ borderColor: error ? C.danger : C.line, color: C.ink }}
                className="w-full border rounded-xl px-3 py-2.5 text-sm bg-white"
              />
            </div>
            <div className="mb-6">
              <label style={{ color: C.ink }} className="text-sm font-semibold block mb-2">Password</label>
              <input
                type="password" autoComplete="current-password"
                value={password} onChange={(e) => setPassword(e.target.value)}
                style={{ borderColor: error ? C.danger : C.line, color: C.ink }}
                className="w-full border rounded-xl px-3 py-2.5 text-sm bg-white"
              />
            </div>

            {error && <p style={{ color: C.danger }} className="text-sm mb-4 text-center">{error}</p>}

            <Button className="w-full" type="submit" disabled={!email || !password || busy}>
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>

          <p style={{ color: C.sub }} className="text-xs text-center mt-4">
            Your training and your team come from your account.
          </p>
          {h && (
            <p style={{ color: C.sub }} className="text-xs text-center mt-2">
              {h.questionsApproved} questions ready · {h.database}
            </p>
          )}
          {h === null && (
            <p style={{ color: C.danger }} className="text-xs text-center mt-2">
              API unreachable — start it with <code>python scripts/devserver.py</code>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------- Shell ----------
function Shell({ name, department, title, manages, active, setActive, onLogout, petVisible, children }) {
  // One nav for everyone. Managing people ADDS a tab; it does not replace the rest.
  //
  // This used to be two lists, with managerNav substituted for employeeNav — so a
  // manager got Team, Documents and Profile and had no way to reach their own training
  // at all. A manager is also an employee with training of their own, and the old split
  // made that unreachable.
  //
  // Labels follow the brand brief's sidebar list, mapped onto the pages that actually
  // exist rather than adding dead links for the ones that don't: "Learning Paths" is
  // the existing roadmap, "Team" is the always-visible peer gallery, "Reports" is the
  // manager-only completion roster (still gated the same as it always was), "Resources"
  // is Documents. The brief's "Compliance" isn't a separate item -- Certificates already
  // shows exactly that (compliant / expired / renewing) per document, so a second nav
  // entry pointing at the same page would just be the same link twice under two names.
  // "Messages" has no backend anywhere in the app and is left out rather than shipped
  // as a page with nothing behind it.
  const nav = [
    { id: "dashboard", label: "Dashboard", icon: BookOpen },
    { id: "path", label: "My Courses", icon: LayoutGrid },
    { id: "teammates", label: "Team", icon: Users },
    ...(manages ? [{ id: "team", label: "Reports", icon: Target }] : []),
    { id: "certificates", label: "Certificates", icon: Award },
    { id: "documents", label: "Resources", icon: FileText },
    { id: "settings", label: "Settings", icon: SettingsIcon },
  ];

  return (
    <div style={{ ...font, background: C.paper, height: "100vh" }} className="flex flex-col md:flex-row overflow-hidden">
      {/* Locked to the viewport height (not min-height) with overflow-hidden, so this
          row never grows past 100vh: the sidebar -- profile chip and sign-out included
          -- stays put while a long page like Profile scrolls inside <main> only.
          min-height let the whole row grow with the page's content instead, which
          dragged the sidebar along with it and buried sign-out below the fold. */}
      <aside style={{ background: C.rail }} className="w-full md:w-[252px] flex flex-row md:flex-col shrink-0 h-auto md:h-full overflow-x-auto md:overflow-y-auto">
        <div className="hidden md:flex px-5 py-6 items-center"><Logo size={38} light /></div>
        <nav className="flex md:flex-col flex-1 px-3 py-2 md:py-4 gap-1 md:space-y-1">
          {nav.map((n) => {
            const Icon = n.icon;
            const isActive = active === n.id;
            return (
              <button
                key={n.id}
                onClick={() => setActive(n.id)}
                style={{
                  background: isActive ? "rgba(34,197,94,0.18)" : "transparent",
                  color: isActive ? "#fff" : "rgba(240,234,216,0.68)",
                }}
                title={n.label}
                className="min-w-[62px] md:min-w-0 md:w-full flex flex-col md:flex-row items-center gap-1 md:gap-3 px-2 md:px-3 py-2 md:py-2.5 rounded-xl text-[10px] md:text-sm font-semibold transition-colors hover:text-white"
              >
                <Icon size={16} /> {n.label}
              </button>
            );
          })}
        </nav>
        <div style={{ borderColor: "rgba(240,234,216,0.14)" }} className="hidden md:block border-t px-4 py-5">
          <button
            onClick={() => setActive("profile")}
            style={{ background: active === "profile" ? "rgba(34,197,94,0.18)" : "transparent" }}
            className="w-full flex items-center gap-3 mb-3 p-2 -m-2 rounded-xl text-left hover:bg-white/5"
          >
            <div style={{ background: "rgba(240,234,216,0.14)", color: "#F0EAD8" }} className="w-11 h-11 rounded-full flex items-center justify-center text-sm font-bold shrink-0">
              {name.split(" ").map((p) => p[0]).join("")}
            </div>
            <div className="min-w-0">
              <div style={{ color: "#F0EAD8" }} className="text-base font-semibold leading-tight">{name}</div>
              <div style={{ color: "rgba(240,234,216,0.65)" }} className="text-sm truncate">{department || "—"}</div>
              {title && <div style={{ color: "rgba(240,234,216,0.5)" }} className="text-xs truncate">{title}</div>}
            </div>
          </button>
          <button onClick={onLogout} style={{ color: "rgba(240,234,216,0.6)" }} className="flex items-center gap-2 text-xs font-semibold hover:text-white">
            <LogOut size={13} /> Sign out
          </button>
        </div>
      </aside>
      <main className="flex-1 min-h-0 overflow-y-auto">{children}</main>
      {petVisible !== false && <FloatingPet />}
    </div>
  );
}

// ---------- Focus session ----------
function FocusSession() {
  const [priority, setPriority] = useState(FOCUS_PRIORITIES[1]);
  const [duration, setDuration] = useState(FOCUS_DURATIONS[0]);
  const [remaining, setRemaining] = useState(FOCUS_DURATIONS[0].sec);
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(false);
  const intervalRef = useRef(null);

  useEffect(() => {
    if (running) {
      intervalRef.current = setInterval(() => {
        setRemaining((r) => {
          if (r <= 1) {
            clearInterval(intervalRef.current);
            setRunning(false);
            setDone(true);
            return 0;
          }
          return r - 1;
        });
      }, 1000);
    }
    return () => clearInterval(intervalRef.current);
  }, [running]);

  const selectDuration = (d) => {
    if (running) return;
    setDuration(d); setRemaining(d.sec); setDone(false);
  };
  const reset = () => { setRunning(false); setDone(false); setRemaining(duration.sec); };
  const toggle = () => { if (done) { reset(); return; } setRunning((r) => !r); };

  const mm = String(Math.floor(remaining / 60)).padStart(2, "0");
  const ss = String(remaining % 60).padStart(2, "0");
  const progress = 1 - remaining / duration.sec;

  return (
    <div style={{ borderColor: C.line }} className="border rounded-xl p-5 bg-white">
      <div className="flex items-center justify-between mb-1">
        <h3 style={{ ...display, color: C.ink }} className="font-bold">Focus session</h3>
        <MockNote>local only</MockNote>
      </div>
      <p style={{ color: C.sub }} className="text-xs mb-4">Pick what you're working on, set a timer, and go heads-down.</p>

      <div className="flex flex-wrap gap-2 mb-4">
        {FOCUS_PRIORITIES.map((p) => {
          const active = priority.id === p.id;
          return (
            <button key={p.id} onClick={() => { setPriority(p); setDone(false); }}
              style={{ background: active ? p.color : p.bg, color: active ? "#fff" : p.color }}
              className="px-3 py-1.5 rounded-full text-xs font-semibold transition-colors">
              {p.label}
            </button>
          );
        })}
      </div>

      <div className="flex items-center gap-6 flex-wrap">
        <TimerRing progress={progress} color={priority.color} label={`${mm}:${ss}`}
          sublabel={done ? "Session complete" : running ? "Focusing…" : "Ready"} />
        <div className="flex-1 min-w-[200px]">
          <p style={{ color: C.sub }} className="text-xs font-semibold mb-2">Duration</p>
          <div className="flex gap-2 mb-4">
            {FOCUS_DURATIONS.map((d) => {
              const active = duration.label === d.label;
              return (
                <button key={d.label} onClick={() => selectDuration(d)} disabled={running}
                  style={{
                    borderColor: active ? priority.color : C.line,
                    color: active ? priority.color : C.sub,
                    background: active ? priority.bg : "#fff",
                    opacity: running ? 0.6 : 1,
                  }}
                  className="border rounded-lg px-3 py-1.5 text-xs font-semibold">
                  {d.label}
                </button>
              );
            })}
          </div>
          <div className="flex gap-2">
            <button onClick={toggle} style={{ background: priority.color }}
              className="px-4 py-2 rounded-xl text-sm font-semibold text-white">
              {done ? "Start new session" : running ? "Pause" : remaining < duration.sec ? "Resume" : "Start"}
            </button>
            {(running || remaining < duration.sec) && !done && (
              <button onClick={reset} style={{ borderColor: C.line, color: C.sub }} className="border px-4 py-2 rounded-xl text-sm font-semibold">
                Reset
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------- Dashboard ----------
// Four cards. The brief's "Due soon" turned out to be real, not invented: /certificates
// already returns renewalsDue (qscore.renewal_candidates -- certificates valid today but
// expiring within 30 days), the same data the Certificates page uses. Counting the
// not-yet-expired ones is exactly "due soon"; the already-expired ones are counted
// separately below, since "expired" and "about to expire" are different facts.
function DashboardStats({ trainings, renewalsDue, onOpenPath, onOpenCertificates }) {
  if (trainings.length === 0) return null;
  const total = trainings.length;
  const completed = trainings.filter((t) => t.status === "completed").length;
  const compliant = trainings.filter((t) => t.compliant && !t.expired).length;
  const dueSoon = renewalsDue.filter((r) => !r.expired).length;
  const pct = Math.round((completed / total) * 100);

  const Link = ({ onClick, children }) => (
    <button onClick={onClick} style={{ color: C.green700 }}
      className="text-xs font-semibold flex items-center gap-0.5 mt-2 hover:opacity-75">
      {children} <ChevronRight size={12} />
    </button>
  );

  return (
    <div className="grid grid-cols-4 gap-4 mb-8">
      <div style={{ borderColor: C.line }} className="border rounded-2xl bg-white p-6 flex items-center gap-4">
        <MasteryRing value={pct} size={72} stroke={7} />
        <div>
          <div style={{ ...display, color: C.ink }} className="text-2xl font-bold leading-none mb-1">{pct}%</div>
          <div style={{ color: C.sub }} className="text-xs font-semibold">Overall Progress</div>
          <Link onClick={onOpenPath}>View progress</Link>
        </div>
      </div>
      <div style={{ borderColor: C.line }} className="border rounded-2xl bg-white p-6 flex items-center gap-4">
        <div style={{ background: C.mint }} className="w-14 h-14 rounded-2xl flex items-center justify-center shrink-0">
          <Box size={26} color={C.green700} />
        </div>
        <div>
          <div style={{ ...display, color: C.ink }} className="text-2xl font-bold leading-none mb-1">{completed}</div>
          <div style={{ color: C.sub }} className="text-xs font-semibold">Trainings Completed</div>
          <Link onClick={onOpenPath}>View all</Link>
        </div>
      </div>
      <div style={{ borderColor: C.line }} className="border rounded-2xl bg-white p-6 flex items-center gap-4">
        <div style={{ background: C.amberBg }} className="w-14 h-14 rounded-2xl flex items-center justify-center shrink-0">
          <Calendar size={26} color={C.amber} />
        </div>
        <div>
          <div style={{ ...display, color: C.ink }} className="text-2xl font-bold leading-none mb-1">{dueSoon}</div>
          <div style={{ color: C.sub }} className="text-xs font-semibold">Due Soon</div>
          <Link onClick={onOpenCertificates}>View all</Link>
        </div>
      </div>
      <div style={{ borderColor: C.line }} className="border rounded-2xl bg-white p-6 flex items-center gap-4">
        <div style={{ background: C.successBg }} className="w-14 h-14 rounded-2xl flex items-center justify-center shrink-0">
          <ShieldCheck size={26} color={C.success} />
        </div>
        <div>
          <div style={{ ...display, color: C.ink }} className="text-2xl font-bold leading-none mb-1">
            {Math.round((compliant / total) * 100)}%
          </div>
          <div style={{ color: C.sub }} className="text-xs font-semibold">Compliant</div>
          {compliant === total
            ? <p style={{ color: C.success }} className="text-xs font-semibold mt-2">All caught up</p>
            : <Link onClick={onOpenCertificates}>View all</Link>}
        </div>
      </div>
    </div>
  );
}

// Manager-only, real: the same qscore coverage numbers My Team's roster uses, just the
// worst-off few direct reports at a glance rather than the full table. Nobody here is
// asked to trust an invented percentage next to a real name.
function TeamProgressCard({ team }) {
  const { data } = useAsync(() => api.teamCompletion(), []);
  const direct = (team?.people || []).filter((p) => p.direct);
  if (!direct.length) return null;

  const byId = new Map((data?.people || []).map((p) => [p.employeeId, p]));
  const rows = direct
    .map((p) => ({ ...p, stat: byId.get(p.employeeId) }))
    .filter((p) => p.stat)
    .sort((a, b) => a.stat.coverage - b.stat.coverage)
    .slice(0, 5);

  return (
    <div style={{ borderColor: C.line }} className="border rounded-xl bg-white p-5 h-full flex flex-col">
      <h3 style={{ ...display, color: C.ink }} className="font-bold mb-4">Team progress</h3>
      {!rows.length ? (
        <p style={{ color: C.sub }} className="text-xs">Completion numbers are still loading.</p>
      ) : (
        <div className="flex-1 flex flex-col justify-between gap-3.5">
          {rows.map((p) => (
            <div key={p.employeeId}>
              <div className="flex items-center gap-2 mb-1">
                <div style={{ background: C.mint, color: C.green700 }} className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0">
                  {p.name.split(" ").map((x) => x[0]).join("")}
                </div>
                <p style={{ color: C.ink }} className="text-xs font-semibold truncate flex-1">{p.name}</p>
                <span style={{ color: C.sub }} className="text-xs font-semibold shrink-0">{Math.round(p.stat.coverage)}%</span>
              </div>
              <div style={{ background: C.line }} className="w-full h-1.5 rounded-full overflow-hidden">
                <div style={{ width: `${p.stat.coverage}%`, background: C.green500 }} className="h-full rounded-full" />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Dashboard({ name, team, onOpenPath, onOpenTraining, onOpenCertificates }) {
  const { data, loading, error, reload } = useAsync(() => api.trainings(), []);
  const { data: certData } = useAsync(() => api.certificates(), []);
  const trainings = data?.trainings || [];
  const renewalsDue = certData?.renewalsDue || [];
  const upcoming = renewalsDue.filter((r) => !r.expired);
  const manages = Boolean(team?.manages);
  // Resume the one in progress; failing that, whatever hasn't been started.
  const focus = trainings.find((t) => t.status === "in-progress")
    || trainings.find((t) => t.status === "not-started")
    || trainings[0];

  return (
    <div className="p-8 max-w-6xl">
      <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">Good morning, {name.split(" ")[0]}</h1>
      <p style={{ color: C.sub }} className="text-sm mb-8">Here's where your training stands.</p>

      {loading && <Loading label="Loading your trainings…" />}
      {error && <ErrorBox error={error} onRetry={reload} />}

      {!loading && !error && (
        <DashboardStats trainings={trainings} renewalsDue={renewalsDue}
          onOpenPath={onOpenPath} onOpenCertificates={onOpenCertificates} />
      )}

      {focus && (
        <div style={{ borderColor: C.line }}
          className="border rounded-2xl p-6 flex items-center gap-5 mb-8 bg-white">
          <div style={{ background: C.mint }} className="w-16 h-16 rounded-xl flex items-center justify-center shrink-0">
            <BookOpen size={26} color={C.green700} />
          </div>
          <div className="min-w-0 flex-1">
            <p style={{ color: C.green700 }} className="text-xs uppercase tracking-wide mb-1 font-semibold">
              {focus.status === "not-started" ? "Start here" : "Continue where you left off"}
            </p>
            <h2 style={{ ...display, color: C.ink }} className="text-lg font-bold mb-1">{focus.title}</h2>
            <p style={{ color: C.sub }} className="text-sm mb-4">
              {focus.modules.length} modules · {focus.questionCount} questions
              {focus.answered > 0 ? ` · ${focus.answered} answered` : ""}
            </p>
            <Button onClick={() => onOpenTraining(focus)}>
              {focus.status === "not-started" ? "Start training" : "Continue training"}
            </Button>
          </div>
          <MasteryRing value={focus.mastery} size={72} />
        </div>
      )}

      {!loading && !error && trainings.length > 0 && (
        <div className={manages ? "grid grid-cols-3 gap-6 items-stretch mb-8" : "mb-8"}>
          <div className={manages ? "col-span-2" : ""}>
            <div className="flex items-center justify-between mb-3">
              <h3 style={{ ...display, color: C.ink }} className="font-bold">Your courses</h3>
              <button onClick={onOpenPath} style={{ color: C.green700 }} className="text-sm font-semibold flex items-center gap-1">
                View all courses <ChevronRight size={14} />
              </button>
            </div>
            <div className="space-y-2">
              {trainings.map((t) => (
                <button key={t.id} onClick={() => onOpenTraining(t)} style={{ borderColor: C.line }}
                  className="w-full text-left border rounded-xl p-4 flex items-center justify-between gap-3 bg-white hover:shadow-sm transition-shadow">
                  <div className="flex items-center gap-3 min-w-0">
                    {t.status === "completed"
                      ? <CheckCircle2 size={16} color={C.success} className="shrink-0" />
                      : <Circle size={16} color={C.green500} className="shrink-0" />}
                    <div className="min-w-0">
                      <p style={{ color: C.ink }} className="text-sm font-semibold truncate">{t.title}</p>
                      <p style={{ color: C.sub }} className="text-xs">
                        {t.modules.length} modules · {t.questionCount} questions
                        {t.compliant && t.expiresAt ? ` · renews ${String(t.expiresAt).slice(0, 10)}` : ""}
                      </p>
                    </div>
                  </div>
                  {t.expired ? (
                    <span style={{ background: C.dangerBg, color: C.danger, fontWeight: 600 }}
                      className="text-xs px-2.5 py-1 rounded-full whitespace-nowrap">Expired — retake</span>
                  ) : t.compliant ? (
                    <span style={{ background: C.successBg, color: C.success, fontWeight: 600 }}
                      className="text-xs px-2.5 py-1 rounded-full whitespace-nowrap">Compliant</span>
                  ) : (
                    <StatusPill status={t.status} />
                  )}
                </button>
              ))}
            </div>
          </div>
          {manages && <TeamProgressCard team={team} />}
        </div>
      )}

      {!loading && !error && trainings.length === 0 && (
        <div style={{ borderColor: C.line }} className="border rounded-xl p-6 bg-white text-center mb-8">
          <p style={{ color: C.ink }} className="text-sm font-semibold mb-1">No trainings yet</p>
          <p style={{ color: C.sub }} className="text-xs">
            Nothing has been assigned to your role yet. Check back soon, or ask your manager if you think this is unexpected.
          </p>
        </div>
      )}

      {upcoming.length > 0 && (
        <div style={{ borderColor: C.line }} className="border rounded-xl bg-white p-5 mb-8">
          <h3 style={{ ...display, color: C.ink }} className="font-bold mb-4">Upcoming deadlines</h3>
          <div className="space-y-2">
            {upcoming.map((r) => (
              <div key={r.doc_title} style={{ borderColor: C.line }}
                className="flex items-center justify-between gap-3 border-t pt-2.5 first:border-t-0 first:pt-0">
                <div className="flex items-center gap-2.5 min-w-0">
                  <Clock size={14} color={C.amber} className="shrink-0" />
                  <p style={{ color: C.ink }} className="text-sm font-medium truncate">{r.doc_title}</p>
                </div>
                <span style={{ color: C.amber }} className="text-xs font-semibold shrink-0">
                  {r.daysUntilExpiry === 0 ? "expires today" : `expires in ${r.daysUntilExpiry} day${r.daysUntilExpiry === 1 ? "" : "s"}`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {!loading && !error && trainings.length > 0 && (
        <div style={{ background: C.green700 }}
          className="rounded-2xl p-6 flex items-center justify-between gap-6 text-white">
          <div>
            <h3 style={{ ...display }} className="text-lg font-bold mb-1">Keep growing, keep leading.</h3>
            <p className="text-sm opacity-90">
              My Courses shows everything assigned to you, what's optional, and what's done.
            </p>
          </div>
          <button onClick={onOpenPath} style={{ color: C.green700 }}
            className="bg-white px-4 py-2 rounded-xl text-sm font-semibold hover:opacity-90 shrink-0">
            View all courses
          </button>
        </div>
      )}

      <div className="mt-8"><FocusSession /></div>
    </div>
  );
}

// ---------- My Courses ----------
// Four tabs, all computed from data /api/trainings already returns -- "required"
// reflects the same dbo.RoleRequirements table Q Score reads (added there so the two
// can never disagree), so "Mandatory" vs "Recommended" is a real, current fact, not a
// guess. Today "Recommended" will usually be empty: it's every course visible to this
// role that a manager confirmed WITHOUT making it required (POST /documents/confirm,
// makeRequired unchecked) -- open-to-anyone material, not yet a feature with its own
// upload flow, but the split it needs already exists.
const COURSE_TABS = [
  { id: "all", label: "All" },
  { id: "mandatory", label: "Mandatory" },
  { id: "completed", label: "Completed" },
  { id: "recommended", label: "Recommended" },
];

const COURSE_EMPTY_COPY = {
  all: "Nothing has been assigned to your role yet. Check back soon, or ask your manager if you think this is unexpected.",
  mandatory: "Nothing required right now.",
  completed: "Nothing completed yet -- pass a quiz and it'll show up here.",
  recommended: "No open courses yet. A document a manager confirms without requiring it shows up here, for anyone to take.",
};

function CourseBadge({ t }) {
  if (t.expired) {
    return (
      <span style={{ background: "rgba(216,68,60,0.22)", color: "#F3A9A4" }}
        className="text-[11px] font-semibold px-2 py-1 rounded-lg flex items-center gap-1">
        <AlertCircle size={11} /> Expired — retake
      </span>
    );
  }
  if (t.compliant && t.expiresAt) {
    return (
      <span style={{ background: "rgba(20,184,166,0.2)", color: "#7FE0D2" }}
        className="text-[11px] font-semibold px-2 py-1 rounded-lg flex items-center gap-1">
        <Clock size={11} /> Renews {String(t.expiresAt).slice(0, 10)}
      </span>
    );
  }
  if (t.required) {
    return (
      <span style={{ background: "rgba(255,158,74,0.22)", color: "#FFC48A" }}
        className="text-[11px] font-semibold px-2 py-1 rounded-lg">Required</span>
    );
  }
  return (
    <span style={{ background: "rgba(240,234,216,0.16)", color: "rgba(240,234,216,0.8)" }}
      className="text-[11px] font-semibold px-2 py-1 rounded-lg">Optional</span>
  );
}

function CourseCard({ t, onOpenTraining }) {
  return (
    <button onClick={() => onOpenTraining(t)} style={{ background: C.rail }}
      className="text-left rounded-2xl overflow-hidden flex flex-col hover:opacity-90 transition-opacity">
      <div style={{ background: C.green900 }} className="h-28 flex items-center justify-center relative shrink-0">
        <BookOpen size={30} color="rgba(240,234,216,0.35)" />
        <div className="absolute bottom-2 left-2"><CourseBadge t={t} /></div>
      </div>
      <div className="p-4 flex-1 flex flex-col">
        <div className="flex items-center gap-2 mb-3">
          <div style={{ background: "rgba(240,234,216,0.16)" }} className="flex-1 h-1.5 rounded-full overflow-hidden">
            <div style={{ width: `${t.mastery}%`, background: C.green500 }} className="h-full rounded-full" />
          </div>
          <span style={{ color: "rgba(240,234,216,0.75)" }} className="text-xs font-semibold shrink-0">{t.mastery}%</span>
        </div>
        <p style={{ color: "#F0EAD8" }} className="text-sm font-semibold leading-snug mb-1">{t.title}</p>
        <p style={{ color: "rgba(240,234,216,0.55)" }} className="text-xs">
          {t.modules.length} module{t.modules.length === 1 ? "" : "s"} · {t.questionCount} questions
        </p>
      </div>
    </button>
  );
}

function MyCourses({ onBack, onOpenTraining }) {
  const { data, loading, error, reload } = useAsync(() => api.trainings(), []);
  const all = data?.trainings || [];
  const [tab, setTab] = useState("all");

  const byTab = {
    all,
    mandatory: all.filter((t) => t.required),
    completed: all.filter((t) => t.status === "completed"),
    recommended: all.filter((t) => t.recommended),
  };
  const shown = byTab[tab];

  return (
    <div className="p-8 max-w-5xl">
      <button onClick={onBack} style={{ color: C.sub }} className="flex items-center gap-1 text-sm font-semibold mb-4">
        <ArrowLeft size={14} /> Back to dashboard
      </button>
      <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-6">My Courses</h1>

      {loading && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}

      {!loading && !error && (
        <>
          <div style={{ background: C.mint }} className="inline-flex flex-wrap rounded-xl p-1 mb-6 gap-1">
            {COURSE_TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                style={{ background: tab === t.id ? C.green700 : "transparent", color: tab === t.id ? "#fff" : C.ink }}
                className="px-4 py-2 rounded-lg text-sm font-semibold transition-colors"
              >
                {t.label} <span style={{ opacity: 0.75 }}>({byTab[t.id].length})</span>
              </button>
            ))}
          </div>

          {shown.length === 0 ? (
            <div style={{ borderColor: C.line }} className="border rounded-xl p-6 bg-white text-center">
              <p style={{ color: C.sub }} className="text-sm">{COURSE_EMPTY_COPY[tab]}</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
              {shown.map((t) => <CourseCard key={t.id} t={t} onOpenTraining={onOpenTraining} />)}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------- Training detail ----------
function TrainingDetail({ training, onBack, onStartDiagnostic, onOpenModule, onStartFinal }) {
  const { data, loading, error, reload } = useAsync(() => api.pathway(training.title), [training.title]);
  const path = data?.training;

  return (
    <div className="p-8">
      <button onClick={onBack} style={{ color: C.sub }} className="flex items-center gap-1 text-sm font-semibold mb-4">
        <ArrowLeft size={14} /> Back
      </button>
      <div className="flex items-start justify-between gap-6 mb-6">
        <div className="min-w-0">
          <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">{training.title}</h1>
          <p style={{ color: C.sub }} className="text-sm">
            {path?.modules.length ?? training.modules.length} required modules
          </p>
        </div>
        <MasteryRing value={training.mastery} />
      </div>

      {loading && <Loading label="Building your pathway…" />}
      {error && <ErrorBox error={error} onRetry={reload} />}

      {path && !path.diagnostic.completed && (
        <div style={{ borderColor: C.line }} className="border rounded-lg p-5 bg-white mb-6">
          <div className="flex items-start gap-3">
            <Target size={18} color={C.green700} className="shrink-0 mt-0.5" />
            <div className="min-w-0 flex-1">
              <h2 style={{ ...display, color: C.ink }} className="text-sm font-bold mb-1">Start with a diagnostic</h2>
              <p style={{ color: C.sub }} className="text-sm mb-4">
                {path.diagnostic.questionCount} questions: a quick check for every module.
                Your results set the order, but every module remains required.
              </p>
              {!path.diagnostic.ready && (
                <p style={{ color: C.amber }} className="text-xs font-semibold mb-3">
                  This document still needs at least one question per module.
                </p>
              )}
              <Button onClick={onStartDiagnostic} disabled={!path.diagnostic.ready}>Take diagnostic</Button>
            </div>
          </div>
        </div>
      )}

      {path && (
        <>
          <h3 style={{ ...display, color: C.ink }} className="font-bold mb-3">Training pathway</h3>
          <div className="space-y-2 mb-6">
            {path.modules.map((module) => {
              const locked = module.status === "locked";
              const passed = module.status === "passed";
              const needsReview = module.status === "needs-review";
              return (
                <button key={module.moduleId} onClick={() => !locked && onOpenModule(module)} disabled={locked}
                  style={{ borderColor: needsReview ? C.amber : C.line, opacity: locked ? 0.62 : 1 }}
                  className="w-full border rounded-lg p-4 flex items-center gap-3 bg-white text-left disabled:cursor-not-allowed">
                  {passed ? <CheckCircle2 size={17} color={C.success} className="shrink-0" />
                    : locked ? <Lock size={16} color={C.sub} className="shrink-0" />
                      : <Circle size={17} color={needsReview ? C.amber : C.green700} className="shrink-0" />}
                  <div className="min-w-0 flex-1">
                    <p style={{ color: C.ink }} className="text-sm font-semibold">
                      {module.pathwayOrder}. {module.title || module.topic}
                    </p>
                    <p style={{ color: C.sub }} className="text-xs mt-0.5">
                      {module.questionCount} questions available
                      {module.attemptCount > 0 ? ` · best ${Math.round(module.bestScore)}%` : ""}
                    </p>
                  </div>
                  <span style={{ color: needsReview ? C.amber : passed ? C.success : C.sub }} className="text-xs font-semibold">
                    {needsReview ? "Review required" : passed ? "Passed" : locked ? "Locked" : "Available"}
                  </span>
                </button>
              );
            })}
          </div>

          <div style={{ borderColor: C.line }} className="border-t pt-5 flex items-center justify-between gap-4">
            <div>
              <h3 style={{ ...display, color: C.ink }} className="text-sm font-bold">Final assessment</h3>
              <p style={{ color: C.sub }} className="text-xs mt-0.5">
                {path.finalAssessment.questionCount} balanced questions · 80% to earn your certificate
              </p>
            </div>
            <Button onClick={onStartFinal} disabled={path.finalAssessment.locked}>
              {path.finalAssessment.locked ? "Locked" : "Start final"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

// ---------- Lesson ----------
function LessonScreen({ training, module, onContinue, onBack }) {
  const { data, loading, error, reload } = useAsync(
    () => api.lesson(training.title, module.moduleId), [training.title, module.moduleId]);
  const [pageIndex, setPageIndex] = useState(0);
  const [saving, setSaving] = useState(false);
  const [pageError, setPageError] = useState(null);
  const pages = data?.pages?.length ? data.pages : (data?.sections || []);
  const generatedPages = Boolean(data?.pages?.length);

  useEffect(() => {
    if (!pages.length) return;
    const resumeAt = pages.findIndex((page) => !page.completed);
    setPageIndex(resumeAt >= 0 ? resumeAt : 0);
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  const finishPage = async () => {
    const page = pages[pageIndex];
    setPageError(null);
    if (generatedPages && !page.completed) {
      setSaving(true);
      try {
        await api.completeLessonPage({ moduleId: module.moduleId, pageId: page.id });
        page.completed = true;
      } catch (e) {
        setPageError(e);
        setSaving(false);
        return;
      }
      setSaving(false);
    }
    if (pageIndex < pages.length - 1) setPageIndex((index) => index + 1);
    else onContinue();
  };

  const current = pages[pageIndex];
  const weak = current && (module.weakSections || []).includes(current.heading || current.title);

  return (
    <div className="p-8 max-w-2xl">
      <button onClick={onBack} style={{ color: C.sub }} className="flex items-center gap-1 text-sm font-semibold mb-4">
        <ArrowLeft size={14} /> Back
      </button>

      <div className="flex items-center justify-between gap-3 mb-1">
        <h1 style={{ ...display, color: C.ink }} className="text-xl font-bold min-w-0">{module.title || module.topic}</h1>
        {data && (
          <span style={{ color: C.sub, background: C.mint }} className="text-xs font-semibold px-2.5 py-1 rounded-full flex items-center gap-1 shrink-0">
            <Clock size={12} /> {data.readTime}
          </span>
        )}
      </div>
      <p style={{ color: C.sub }} className="text-sm mb-4">
        {module.status === "passed"
          ? "Review the source material from this completed module."
          : "Complete this module before its 10-question adaptive checkpoint."}
      </p>

      {loading && <Loading label="Loading lesson…" />}
      {error && <ErrorBox error={error} onRetry={reload} />}

      {data && (
        <>
          <div className="flex gap-1.5 mb-4" aria-label="Lesson progress">
            {pages.map((page, index) => (
              <button key={page.id} onClick={() => setPageIndex(index)}
                title={`Page ${index + 1}: ${page.title || page.heading}`}
                style={{ background: index === pageIndex ? C.purple : page.completed ? C.success : C.line }}
                className="h-2 flex-1 min-w-0 rounded-full" />
            ))}
          </div>

          {current && (
            <article style={{ borderColor: weak ? C.amber : C.line }}
              className="border rounded-lg bg-white p-6 mb-4 min-h-[340px]">
              <div className="flex items-center justify-between gap-3 mb-4">
                <p style={{ color: C.sub }} className="text-xs font-semibold">
                  Page {pageIndex + 1} of {pages.length}
                </p>
                {current.completed && (
                  <span style={{ color: C.success }} className="text-xs font-semibold flex items-center gap-1">
                    <CheckCircle2 size={13} /> Complete
                  </span>
                )}
              </div>
              <h2 style={{ ...display, color: C.ink }} className="text-lg font-bold mb-3">
                {current.title || current.heading}
              </h2>
              {weak && <p style={{ color: C.amber }} className="text-xs font-semibold mb-2">Review this section carefully</p>}
              <div style={{ color: C.sub, whiteSpace: "pre-wrap" }} className="text-sm leading-7">
                {current.body}
              </div>
              {(current.citations || []).filter((citation) => citation.url).length > 0 && (
                <div style={{ borderColor: C.line }} className="border-t mt-5 pt-3">
                  {(current.citations || []).filter((citation) => citation.url).map((citation, index) => (
                    <a key={`${citation.url}-${index}`} href={citation.url} target="_blank" rel="noopener noreferrer"
                      style={{ color: C.green700 }} className="text-xs font-semibold block truncate">
                      {citation.title || citation.url} <span aria-hidden="true">↗</span>
                    </a>
                  ))}
                </div>
              )}
            </article>
          )}

          {pageError && <ErrorBox error={pageError} />}
          <div className="flex items-center justify-between gap-3">
            <Button variant="ghost" onClick={() => setPageIndex((index) => Math.max(0, index - 1))}
              disabled={pageIndex === 0}>Previous</Button>
            <Button onClick={finishPage} disabled={saving || !current}>
              {saving ? "Saving…" : pageIndex < pages.length - 1 ? "Complete & next"
                : module.status === "passed" ? "Back to pathway" : "Complete lesson"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

// ---------- Quiz ----------
function QuizPreScreen({ training, assessment, onStart, onBack, starting, error }) {
  const isDiagnostic = assessment.kind === "diagnostic";
  const isModule = assessment.kind === "module";
  const title = isDiagnostic ? "Ready for your diagnostic?"
    : isModule ? "Ready for the module checkpoint?" : "Ready for the final assessment?";
  const detail = isDiagnostic
    ? "Three difficulty checks per module · No content can be skipped"
    : isModule
      ? "10 adaptive questions · You need 90% to pass"
      : "Balanced shared and variable questions · You need 80% to earn a certificate";
  const note = isDiagnostic
    ? "Your results personalize the order and emphasis of the required modules."
    : isModule
      ? "The next question becomes harder after a correct answer and easier after a mistake."
      : "Every form follows the same topic and difficulty blueprint for fairness.";
  return (
    <div className="p-8">
      <button onClick={onBack} style={{ color: C.sub }} className="flex items-center gap-1 text-sm font-semibold mb-4">
        <ArrowLeft size={14} /> Back
      </button>
      <div style={{ borderColor: C.line }} className="border rounded-2xl p-8 bg-white text-center">
        <div style={{ background: C.mint }} className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4">
          <Clock size={22} color={C.green700} />
        </div>
        <h1 style={{ ...display, color: C.ink }} className="text-xl font-bold mb-2">{title}</h1>
        <p style={{ color: C.sub }} className="text-sm mb-2">{detail}</p>
        <p style={{ color: C.sub }} className="text-xs mb-6">{note}</p>
        {error && <ErrorBox error={error} />}
        <Button onClick={onStart} disabled={starting}>{starting ? "Building your quiz…" : "Begin quiz"}</Button>
      </div>
    </div>
  );
}

/**
 * Runs one attempt.
 *
 * Grading is a round trip per question. The browser never holds the answer key, so
 * "correct" here is the server's verdict, not a client-side comparison. The running
 * tally drives the progress bar only — the score shown at the end is recomputed
 * server-side from the full submission.
 */
function QuizRunner({ training, quiz, onSubmit, onBack }) {
  const [q, setQuestion] = useState(quiz.currentQuestion);
  const [answeredCount, setAnsweredCount] = useState(quiz.answeredCount || 0);
  const [verdict, setVerdict] = useState(null);
  const [pending, setPending] = useState(false);
  const [draft, setDraft] = useState(undefined);
  const [error, setError] = useState(null);
  const [fallbackNotice, setFallbackNotice] = useState(false);

  const isMulti = q?.type === "MultiSelect";
  const checked = Boolean(verdict);
  const position = Math.min(checked ? answeredCount : answeredCount + 1, quiz.questionTarget);

  const commit = async (payload) => {
    setPending(true);
    setError(null);
    try {
      const response = await api.answerPathwayQuestion({
        attemptId: quiz.attemptId, questionId: q.questionId, ...payload,
      });
      setAnsweredCount(response.answeredCount);
      if (response.fallbackQuestion) {
        setQuestion(response.fallbackQuestion);
        setDraft(undefined);
        setVerdict(null);
        setFallbackNotice(true);
        return;
      }
      setVerdict(response);
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  };

  const selectOption = (optionId) => {
    if (checked || pending) return;
    if (isMulti) {
      setDraft((current) => {
        const values = Array.isArray(current) ? current : [];
        return values.includes(optionId)
          ? values.filter((value) => value !== optionId)
          : [...values, optionId];
      });
      return;
    }
    setDraft(optionId);
    commit({ selectedOptionIds: [optionId] });
  };

  const next = async () => {
    if (verdict?.nextQuestion) {
      setQuestion(verdict.nextQuestion);
      setVerdict(null);
      setDraft(undefined);
      setError(null);
      setFallbackNotice(false);
      return;
    }
    setPending(true);
    setError(null);
    try {
      const result = await api.completePathwayAssessment(quiz.attemptId);
      onSubmit(result);
    } catch (e) {
      setError(e);
      setPending(false);
    }
  };

  const optionStyle = (optionId) => {
    const selected = isMulti
      ? (Array.isArray(draft) ? draft : []).includes(optionId)
      : draft === optionId;
    if (!checked) {
      return {
        state: "default",
        selected,
        style: { borderColor: selected ? C.green700 : C.line, background: selected ? C.mint : "#fff", color: C.ink },
      };
    }
    const isRight = (verdict.correctOptionIds || []).includes(optionId);
    if (isRight) return { state: "correct", selected, style: { borderColor: C.success, background: C.successBg, color: C.ink } };
    if (selected) return { state: "incorrect", selected, style: { borderColor: C.danger, background: C.dangerBg, color: C.ink } };
    return { state: "muted", selected, style: { borderColor: C.line, background: "#fff", color: C.sub, opacity: 0.7 } };
  };

  return (
    <div className="p-8">
      <button onClick={onBack} style={{ color: C.sub }} className="flex items-center gap-1 text-sm font-semibold mb-4">
        <ArrowLeft size={14} /> Exit quiz
      </button>
      <div className="flex items-center justify-between gap-3 mb-2">
        <h1 style={{ ...display, color: C.ink }} className="text-xl font-bold min-w-0 truncate">
          {training.title} · {quiz.kind === "diagnostic" ? "Diagnostic" : quiz.kind === "module" ? "Checkpoint" : "Final"}
        </h1>
        <span style={{ color: C.sub }} className="text-xs font-semibold shrink-0">
          Question {position} of {quiz.questionTarget}
        </span>
      </div>
      <div style={{ background: C.line }} className="w-full h-1.5 rounded-full overflow-hidden mb-6">
        <div style={{ width: `${((answeredCount) / quiz.questionTarget) * 100}%`, background: C.green700 }}
          className="h-full rounded-full transition-all" />
      </div>

      {fallbackNotice && (
        <div style={{ background: C.amberBg, color: C.amber }} className="rounded-lg px-4 py-3 text-sm mb-4">
          Your written answer was genuinely ambiguous to the grader. This equivalent
          multiple-choice check will decide the same question slot.
        </div>
      )}

      <div style={{ borderColor: C.line }} className="border rounded-xl p-5 bg-white">
        <div className="flex items-start justify-between gap-3 mb-3">
          <p style={{ color: C.ink }} className="text-sm font-semibold">{q.prompt}</p>
          <ProvenanceBadge provenance={verdict?.provenance} sourceTitle={verdict?.sourceTitle} />
        </div>
        <div className="flex items-center gap-2 mb-3">
          <span style={{ background: C.mint, color: C.green700 }} className="text-[11px] font-semibold px-2 py-0.5 rounded-full">{q.topic}</span>
          <span style={{ borderColor: C.line, color: C.sub }} className="text-[11px] font-semibold px-2 py-0.5 rounded-full border">{q.difficulty}</span>
        </div>

        <div className="space-y-2">
          {(q.options || []).map((opt) => {
            const { state, selected, style } = optionStyle(opt.optionId);
            return (
              <button
                key={opt.optionId}
                onClick={() => selectOption(opt.optionId)}
                disabled={checked || pending}
                style={style}
                className="w-full text-left border rounded-lg px-3 py-2.5 text-sm flex items-center gap-2"
              >
                <span style={{
                  borderColor: state === "correct" ? C.success : state === "incorrect" ? C.danger : selected ? C.green700 : "#C9C2DB",
                  background: state === "correct" ? C.success : state === "incorrect" ? C.danger : selected ? C.green700 : "transparent",
                  borderRadius: isMulti ? 4 : 999,
                }} className="w-4 h-4 border-2 shrink-0 flex items-center justify-center">
                  {state === "correct" ? <CheckCircle2 size={11} color="#fff" />
                    : state === "incorrect" ? <X size={11} color="#fff" />
                    : selected ? <div className="w-1.5 h-1.5 rounded-full bg-white" /> : null}
                </span>
                <span className="min-w-0">{opt.text}</span>
              </button>
            );
          })}
          {isMulti && !checked && (
            <div className="pt-1">
              <Button
                onClick={() => commit({ selectedOptionIds: Array.isArray(draft) ? draft : [] })}
                disabled={!Array.isArray(draft) || draft.length === 0 || pending}
              >
                {pending ? "Checking…" : "Check answers"}
              </Button>
            </div>
          )}
        </div>

        {error && <ErrorBox error={error} />}

        {checked && (
          <div style={{ background: verdict.correct ? C.successBg : C.dangerBg, color: verdict.correct ? C.success : C.danger }}
            className="mt-4 rounded-xl px-4 py-3 text-sm flex items-start gap-2.5">
            {verdict.correct ? <CheckCircle2 size={17} className="shrink-0 mt-0.5" /> : <AlertCircle size={17} className="shrink-0 mt-0.5" />}
            <div className="min-w-0">
              <p className="font-semibold mb-0.5">{verdict.correct ? "Correct" : "Not quite"}</p>
              {verdict.explanation && (
                <p style={{ color: C.ink }} className="text-sm leading-snug opacity-90">{verdict.explanation}</p>
              )}
              {verdict.sourceQuote && (
                <p style={{ color: C.sub }} className="text-xs italic mt-1.5">"{verdict.sourceQuote}"</p>
              )}
            </div>
          </div>
        )}
      </div>

      {checked && (
        <div className="mt-6">
          <Button onClick={next} disabled={pending}>
            {pending ? "Finishing…" : verdict.readyToComplete ? "See results" : "Next question"}
          </Button>
        </div>
      )}
    </div>
  );
}

function QuizResults({ result, onRetake, onDone }) {
  const pass = result.passed;
  const right = result.results.filter((r) => r.correct).length;
  const diagnostic = result.kind === "diagnostic";
  const checkpoint = result.kind === "module";
  return (
    <div className="p-8">
      <div style={{ borderColor: C.line }} className="border rounded-2xl p-8 bg-white text-center mb-6">
        <div className="flex justify-center mb-4">
          <MasteryRing value={result.scorePercent} size={100} stroke={9} />
        </div>
        {diagnostic ? (
          <>
            <h1 style={{ ...display, color: C.ink }} className="text-xl font-bold mb-2">Your pathway is ready</h1>
            <p style={{ color: C.sub }} className="text-sm mb-6">
              {right} of {result.results.length} correct. Every module is still required; weaker areas now come first.
            </p>
            <Button onClick={onDone}>View training pathway</Button>
          </>
        ) : pass ? (
          <>
            <h1 style={{ ...display, color: C.ink }} className="text-xl font-bold mb-2">
              {checkpoint ? "Module checkpoint passed" : "Final assessment passed"}
            </h1>
            <p style={{ color: C.sub }} className="text-sm mb-6">
              {right} of {result.results.length} correct · pass mark {result.passingScore}%
            </p>
            {checkpoint ? (
              <div style={{ background: C.successBg, color: C.success }} className="rounded-lg px-4 py-3 text-sm font-semibold mb-6">
                The next required module is now available.
              </div>
            ) : (
              <div style={{ background: C.successBg, color: C.success }} className="rounded-lg px-4 py-3 text-sm font-semibold mb-6 flex items-center justify-center gap-2">
                <Award size={16} /> Certificate earned
              </div>
            )}
            <Button onClick={() => { cheerPet(); onDone(); }}>View training pathway</Button>
          </>
        ) : (
          <>
            <h1 style={{ ...display, color: C.ink }} className="text-xl font-bold mb-2">
              {checkpoint ? "Review this module and try again" : "Final assessment not passed"}
            </h1>
            <p style={{ color: C.sub }} className="text-sm mb-6">
              {right} of {result.results.length} correct · you need {result.passingScore}% to pass
            </p>
            <div className="flex gap-2 justify-center">
              <Button onClick={onRetake}>{checkpoint ? "Review module" : "Try final again"}</Button>
              <Button variant="ghost" onClick={onDone}>View pathway</Button>
            </div>
          </>
        )}
      </div>

      {result.weakSections?.length > 0 && (
        <div style={{ background: C.amberBg, borderColor: C.amber }} className="border rounded-xl p-4 mb-6">
          <p style={{ color: C.amber }} className="text-sm font-semibold mb-1">Sections to review</p>
          <p style={{ color: C.ink }} className="text-sm opacity-90">
            {result.weakSections.join(", ")}
          </p>
        </div>
      )}

      <h3 style={{ ...display, color: C.ink }} className="font-bold mb-3">Review</h3>
      <div className="space-y-3">
        {result.results.map((r) => (
          <div key={r.questionId} style={{ borderColor: C.line }} className="border rounded-xl p-4 bg-white">
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span style={{ background: r.correct ? C.successBg : C.dangerBg, color: r.correct ? C.success : C.danger }}
                className="text-xs font-semibold px-2.5 py-1 rounded-full">
                {r.correct ? "Correct" : "Incorrect"}
              </span>
              <span style={{ background: C.mint, color: C.green700 }} className="text-[11px] font-semibold px-2 py-0.5 rounded-full">{r.topic}</span>
              <span className="flex-1" />
              <ProvenanceBadge provenance={r.provenance} sourceTitle={r.sourceTitle} />
            </div>
            <p style={{ color: C.ink }} className="text-sm mb-2">{r.prompt}</p>
            {r.explanation && <p style={{ color: C.sub }} className="text-sm leading-snug">{r.explanation}</p>}
            {r.sourceQuote && (
              <div style={{ borderLeft: `3px solid ${C.line}`, background: C.paper }} className="rounded-r-lg px-3 py-2 mt-2">
                <p style={{ color: C.sub }} className="text-xs">
                  {r.sourceTitle && <span className="font-semibold">{r.sourceTitle}: </span>}"{r.sourceQuote}"
                </p>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------- Certificates ----------
function Certificates() {
  const { data, loading, error, reload } = useAsync(() => api.certificates(), []);
  const certs = data?.certificates || [];
  const due = data?.renewalsDue || [];
  const [downloading, setDownloading] = useState(null);
  const [downloadError, setDownloadError] = useState(null);

  const download = async (certificate) => {
    setDownloading(certificate.certificateId);
    setDownloadError(null);
    try {
      const blob = await api.downloadCertificate(certificate.certificateUrl);
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = `quizrant-certificate-${certificate.certificateId}.pdf`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(href);
    } catch (e) {
      setDownloadError(e);
    } finally {
      setDownloading(null);
    }
  };

  return (
    <div className="p-8">
      <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">Certificates</h1>
      <p style={{ color: C.sub }} className="text-sm mb-6">Everything you've completed and passed.</p>

      {due.length > 0 && (
        <div style={{ background: C.amberBg, color: C.amber }} className="rounded-xl px-4 py-3 text-sm mb-5">
          <strong>{due.length === 1 ? "1 certificate needs" : `${due.length} certificates need`} renewing.</strong>{" "}
          {due.map((d) => d.doc_title).join(", ")}. An expired certificate stops counting
          towards your Q Score until you retake it.
        </div>
      )}

      {loading && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {downloadError && <ErrorBox error={downloadError} />}

      {!loading && !error && certs.length === 0 && (
        <div style={{ borderColor: C.line }} className="border rounded-xl p-6 bg-white text-center">
          <Award size={22} color={C.sub} className="mx-auto mb-2" />
          <p style={{ color: C.ink }} className="text-sm font-semibold mb-1">No certificates yet</p>
          <p style={{ color: C.sub }} className="text-xs">Pass a quiz and it'll show up here.</p>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        {certs.map((c, i) => (
          <div key={i} style={{ borderColor: C.line }} className="border rounded-xl p-5 bg-white">
            <div style={{ background: C.amberBg }} className="w-10 h-10 rounded-xl flex items-center justify-center mb-3">
              <Award size={18} color={C.amber} />
            </div>
            <div className="flex items-start justify-between gap-2 mb-1">
              <p style={{ color: C.ink }} className="text-sm font-semibold">{c.title}</p>
              {/* Several passes for one training can exist; only one counts towards
                  Q Score. Saying which avoids "why is my 95 not showing?". */}
              {!c.ofRecord && (
                <span style={{ color: C.sub }} className="text-[10px] font-semibold shrink-0 mt-0.5">
                  superseded
                </span>
              )}
            </div>
            <p style={{ color: C.sub }} className="text-xs">
              Issued {c.date} · Score {c.score}
              {c.category ? ` · ${c.category}` : ""}
            </p>
            {c.expiresAt && (
              <p style={{ color: c.expired ? C.danger : C.sub }} className="text-xs font-semibold mt-1">
                {c.expired
                  ? `Expired ${c.expiresAt} — retake required`
                  : c.daysUntilExpiry !== null && c.daysUntilExpiry <= 30
                    ? `Expires in ${c.daysUntilExpiry} days — ${c.expiresAt}`
                    : `Valid until ${c.expiresAt}`}
              </p>
            )}
            {c.certificateUrl ? (
              <button onClick={() => download(c)} disabled={downloading === c.certificateId}
                style={{ color: C.green700 }} className="text-xs font-semibold mt-3 flex items-center gap-1.5 disabled:opacity-60">
                {downloading === c.certificateId
                  ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
                Download PDF
              </button>
            ) : (
              <p style={{ color: C.sub }} className="text-[10px] mt-2">
                Downloadable certificate not generated yet
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------- Documents: upload PDFs that become role-scoped training ----------

/**
 * Manager flow: upload -> AI proposes section->role mapping -> manager confirms ->
 * questions generate -> employees in those roles owe the module.
 *
 * The AI only proposes. Sections it cannot place land as "unknown roles" for the
 * manager to resolve — assign to an existing role, or add a new role to the company
 * list first. Nothing is fetched online; a section too thin to teach from is flagged
 * for the manager to fix with more material.
 */
function RoleManager({ roles, onChanged }) {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const add = async () => {
    setBusy(true); setErr(null);
    try {
      await api.addRole({ roleCode: code, title: title || code, description: "" });
      setCode(""); setTitle(""); onChanged();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const remove = async (rc) => {
    if (!window.confirm(`Remove role ${rc}? Employees with this role will only see the everyone-modules.`)) return;
    try { await api.removeRole(rc); onChanged(); } catch (e) { setErr(e); }
  };

  return (
    <div style={{ borderColor: C.line }} className="border rounded-xl bg-white mb-5">
      <button onClick={() => setOpen(!open)} className="w-full flex items-center justify-between p-4">
        <span style={{ ...display, color: C.ink }} className="font-bold text-sm">
          Company roles ({roles.length})
        </span>
        <ChevronRight size={15} color={C.sub} style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform 120ms" }} />
      </button>
      {open && (
        <div className="px-4 pb-4">
          <p style={{ color: C.sub }} className="text-xs mb-3">
            The AI maps documents onto this list and never invents a role. Adding and removing is yours.
          </p>
          <div className="flex flex-wrap gap-2 mb-3">
            {roles.map((r) => (
              <span key={r.role_code} style={{ background: C.mint, color: C.green700 }}
                className="text-xs font-semibold px-2.5 py-1 rounded-full flex items-center gap-1.5">
                {r.title}
                <button onClick={() => remove(r.role_code)} title="Remove role"><X size={11} /></button>
              </span>
            ))}
          </div>
          <div className="flex gap-2 flex-wrap">
            <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="ROLE_CODE"
              style={{ borderColor: C.line, color: C.ink }} className="border rounded-lg px-3 py-2 text-xs w-40" />
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Display name"
              style={{ borderColor: C.line, color: C.ink }} className="border rounded-lg px-3 py-2 text-xs flex-1 min-w-[160px]" />
            <Button onClick={add} disabled={!code.trim() || busy} className="!py-2 text-xs">Add role</Button>
          </div>
          {err && <ErrorBox error={err} />}
        </div>
      )}
    </div>
  );
}

/**
 * Manager-only. Submits a vetted URL through the exact same pipeline an upload goes
 * through server-side (POST /links/add returns the same shape uploadDocument's response
 * does), so onSubmitted just hands the result to the same MappingReview the dropzone
 * already uses -- no separate confirmation flow to build.
 */
function TrustedLinkForm({ roles, canPublishCompanyWide, onSubmitted }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [scope, setScope] = useState("team");
  const [roleCode, setRoleCode] = useState("");
  const [crawl, setCrawl] = useState(true);
  const [maxPages, setMaxPages] = useState(25);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const canSubmit = url.trim() && (scope === "company_wide" || roleCode) && !busy;

  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      const res = await api.addTrustedLink({
        url: url.trim(), scope, roleCode: scope === "company_wide" ? "ALL" : roleCode,
        crawl, maxPages,
      });
      setUrl(""); setRoleCode("");
      onSubmitted(res);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };

  return (
    <div style={{ borderColor: C.line }} className="border rounded-xl bg-white mb-5">
      <button onClick={() => setOpen(!open)} className="w-full flex items-center justify-between p-4">
        <span style={{ ...display, color: C.ink }} className="font-bold text-sm">Add a trusted link</span>
        <ChevronRight size={15} color={C.sub} style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform 120ms" }} />
      </button>
      {open && (
        <div className="px-4 pb-4">
          <p style={{ color: C.sub }} className="text-xs mb-3">
            A vetted vendor, standards, or company website.
          </p>
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…"
            style={{ borderColor: C.line, color: C.ink }}
            className="border rounded-lg px-3 py-2 text-xs w-full mb-2" />
          <div className="flex gap-3 flex-wrap items-center mb-2">
            <label className="flex items-center gap-2 cursor-pointer text-xs" style={{ color: C.ink }}>
              <input type="checkbox" checked={crawl} onChange={(e) => setCrawl(e.target.checked)} />
              Include same-site subpages
            </label>
            {crawl && (
              <select value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value))}
                aria-label="Maximum pages"
                style={{ borderColor: C.line, color: C.ink }} className="border rounded-lg px-3 py-2 text-xs">
                <option value={10}>Up to 10 pages</option>
                <option value={25}>Up to 25 pages</option>
                <option value={50}>Up to 50 pages</option>
              </select>
            )}
          </div>
          <div className="flex gap-2 flex-wrap items-center">
            <select value={scope} onChange={(e) => { setScope(e.target.value); setRoleCode(""); }}
              style={{ borderColor: C.line, color: C.ink }} className="border rounded-lg px-3 py-2 text-xs">
              <option value="team">My team</option>
              {canPublishCompanyWide && <option value="company_wide">Company-wide</option>}
            </select>
            {scope === "team" && (
              <select value={roleCode} onChange={(e) => setRoleCode(e.target.value)}
                style={{ borderColor: C.line, color: C.ink }}
                className="border rounded-lg px-3 py-2 text-xs flex-1 min-w-[160px]">
                <option value="">Choose a role…</option>
                {roles.map((r) => <option key={r.role_code} value={r.role_code}>{r.title}</option>)}
              </select>
            )}
            <Button onClick={submit} disabled={!canSubmit} className="!py-2 text-xs">
              {busy ? (crawl ? "Crawling site…" : "Fetching page…") : "Add link"}
            </Button>
          </div>
          {scope === "company_wide" && (
            <p style={{ color: C.sub }} className="text-xs mt-2">
              Replaces the company's current active company-wide link — there is only ever one.
            </p>
          )}
          {err && <ErrorBox error={err} />}
        </div>
      )}
    </div>
  );
}

/** The manager reviews the AI's proposed mapping before anything is generated. */
function MappingReview({ analysis, roles, onConfirmed, onCancel }) {
  // The AI proposes against every role the company has; this manager may only publish to
  // the roles their reports hold (analysis.permittedRoles, from the server). Offering the
  // full list would let them pick something the confirm call then refuses with a 403 —
  // and, worse, imply they had a say over another team's training.
  const permitted = new Set(analysis.permittedRoles || []);
  const selectable = roles.filter((r) => permitted.has(r.role_code));
  const canPublishCompanyWide = permitted.has("ALL");

  // Grouped by org-chart team (r.team, from GET /roles — null for a role with no
  // org-chart mapping, and always null in local dev, which has no Teams table to
  // join against). Someone whose reporting subtree spans several teams -- a CTO
  // over Cybersecurity, Software Engineering and DevOps, say -- would otherwise see
  // every role_code across all of them mixed into one flat list. Falls back to that
  // same flat list when no role in scope has team info, so this is a pure
  // enhancement, not a dependency the picker breaks without.
  const teamGroups = {};
  const ungroupedRoles = [];
  for (const r of selectable) {
    if (r.team) (teamGroups[r.team] ||= []).push(r);
    else ungroupedRoles.push(r);
  }
  const hasTeamGroups = Object.keys(teamGroups).length > 0;

  const knownCodes = new Set(selectable.map((r) => r.role_code));
  const [assignments, setAssignments] = useState(() => {
    const init = {};
    for (const [topic, role] of Object.entries(analysis.proposedRoles || {})) {
      const proposed = Array.isArray(role) ? role : [role];
      // Proposals naming a role the company hasn't defined start unresolved: the
      // manager must place them before confirm unlocks.
      init[topic] = proposed.map((value) => String(value).toUpperCase())
        .filter((value) => knownCodes.has(value) || value === "ALL");
    }
    return init;
  });
  const [newRoles, setNewRoles] = useState([]); // roles the manager adds inline
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  // Checked by default: assigning training to a role and having it count toward
  // that role's Q Score are the same decision in a manager's head. Still a real
  // checkbox a human can uncheck, not something inferred silently after the fact.
  const [makeRequired, setMakeRequired] = useState(true);

  const allCodes = [...selectable.map((r) => r.role_code), ...newRoles.map((r) => r.roleCode)];
  const unresolved = Object.entries(assignments).filter(([, values]) => !values.length);
  const nobodyToPublishTo = selectable.length === 0 && !canPublishCompanyWide;

  const addInlineRole = (name) => {
    const roleCode = name.toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_|_$/g, "");
    if (!roleCode || allCodes.includes(roleCode)) return roleCode;
    setNewRoles((n) => [...n, { roleCode, title: name, description: "" }]);
    return roleCode;
  };

  const toggleRole = (topic, roleCode) => {
    setAssignments((current) => {
      const selected = current[topic] || [];
      let next;
      if (roleCode === "ALL") {
        next = selected.includes("ALL") ? [] : ["ALL"];
      } else if (selected.includes(roleCode)) {
        next = selected.filter((code) => code !== roleCode);
      } else {
        next = [...selected.filter((code) => code !== "ALL"), roleCode];
      }
      return { ...current, [topic]: next };
    });
  };

  const applyFirstToAll = () => {
    const first = Object.values(assignments).find((values) => values.length) || [];
    if (!first.length) return;
    setAssignments(Object.fromEntries(
      Object.keys(analysis.proposedRoles || {}).map((topic) => [topic, [...first]])));
  };

  const roleTitle = (code) => {
    if (code === "ALL") return "Everyone";
    return [...selectable, ...newRoles.map((role) => ({ role_code: role.roleCode, title: role.title }))]
      .find((role) => role.role_code === code)?.title || code;
  };

  const confirm = async () => {
    setBusy(true); setErr(null);
    try {
      const result = await api.confirmDocument({
        title: analysis.title, assignments, newRoles, supersede: "", makeRequired,
      });
      onConfirmed(result);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };

  return (
    <div style={{ borderColor: C.green300 }} className="border-2 rounded-xl p-5 bg-white mb-5">
      <h3 style={{ ...display, color: C.ink }} className="font-bold mb-1">Confirm who trains on what</h3>
      <p style={{ color: C.sub }} className="text-xs mb-1">
        The AI read "{analysis.title}" and proposed this. Nothing is generated until you confirm.
      </p>
      {analysis.summary && <p style={{ color: C.sub }} className="text-xs italic mb-4">"{analysis.summary}"</p>}

      {nobodyToPublishTo && (
        <div style={{ background: C.amberBg, color: C.amber }} className="rounded-lg px-3 py-2.5 text-xs mb-4">
          <strong>You have no roles to publish to.</strong> Training goes to the roles held
          by people who report to you, and nobody does yet. The document is saved — nothing
          is generated from it until it can be assigned.
        </div>
      )}

      {analysis.thinTopics?.length > 0 && (
        <div style={{ background: C.amberBg, color: C.amber }} className="rounded-lg px-3 py-2.5 text-xs mb-4">
          <strong>Thin source sections:</strong> {analysis.thinTopics.join(", ")}.
          Related sections will be merged first. When enabled, general professional
          material may be supplemented from independently cited web sources.
        </div>
      )}

      {analysis.crawl?.pages?.length > 0 && (
        <details style={{ borderColor: C.line }} className="border rounded-lg px-3 py-2.5 mb-4">
          <summary style={{ color: C.ink }} className="text-xs font-semibold cursor-pointer">
            {analysis.crawl.pageCount} source page{analysis.crawl.pageCount === 1 ? "" : "s"} included
            {analysis.crawl.truncated ? ` (limit ${analysis.crawl.pageLimit})` : ""}
          </summary>
          <div className="mt-2 max-h-44 overflow-auto space-y-1.5">
            {analysis.crawl.pages.map((page) => (
              <div key={page.url} className="text-xs min-w-0">
                <p style={{ color: C.ink }} className="font-medium truncate">{page.title}</p>
                <p style={{ color: C.sub }} className="truncate">{page.url}</p>
              </div>
            ))}
          </div>
        </details>
      )}

      <div className="space-y-2 mb-4">
        {Object.entries(analysis.proposedRoles || {}).map(([topic, proposed]) => {
          const proposedCodes = (Array.isArray(proposed) ? proposed : [proposed])
            .map((value) => String(value).toUpperCase());
          const isUnknown = proposedCodes.some((code) => !knownCodes.has(code) && code !== "ALL");
          const selected = assignments[topic] || [];
          return (
            <div key={topic} className="flex items-start gap-3 flex-wrap">
              <span style={{ color: C.ink }} className="text-sm font-medium flex-1 min-w-[200px]">{topic}</span>
              {isUnknown && !selected.length && (
                <span style={{ background: C.dangerBg, color: C.danger }} className="text-[11px] font-semibold px-2 py-0.5 rounded-full">
                  document says "{proposed}" — not a company role
                </span>
              )}
              <details style={{ borderColor: selected.length ? C.line : C.danger }}
                className="border rounded-lg bg-white w-full sm:w-[300px] relative">
                <summary style={{ color: selected.length ? C.ink : C.danger }}
                  className="list-none cursor-pointer px-3 py-2 text-xs font-semibold flex items-center justify-between gap-2">
                  <span className="truncate">
                    {selected.length ? selected.map(roleTitle).join(", ") : "Choose one or more roles"}
                  </span>
                  <ChevronRight size={14} className="rotate-90 shrink-0" />
                </summary>
                <div style={{ borderColor: C.line }} className="border-t px-3 py-2 max-h-56 overflow-auto space-y-1.5">
                  {canPublishCompanyWide && (
                    <label className="flex items-center gap-2 text-xs cursor-pointer py-1">
                      <input type="checkbox" checked={selected.includes("ALL")}
                        onChange={() => toggleRole(topic, "ALL")} />
                      Everyone (company-wide)
                    </label>
                  )}
                  {hasTeamGroups && Object.keys(teamGroups).sort().map((team) => (
                    <div key={team}>
                      <p style={{ color: C.sub }} className="text-[11px] font-semibold mt-2 mb-1">{team}</p>
                      {teamGroups[team].map((role) => (
                        <label key={role.role_code} className="flex items-center gap-2 text-xs cursor-pointer py-1">
                          <input type="checkbox" checked={selected.includes(role.role_code)}
                            onChange={() => toggleRole(topic, role.role_code)} />
                          {role.title}
                        </label>
                      ))}
                    </div>
                  ))}
                  {(hasTeamGroups ? ungroupedRoles : selectable).map((role) => (
                    <label key={role.role_code} className="flex items-center gap-2 text-xs cursor-pointer py-1">
                      <input type="checkbox" checked={selected.includes(role.role_code)}
                        onChange={() => toggleRole(topic, role.role_code)} />
                      {role.title}
                    </label>
                  ))}
                  {newRoles.map((role) => (
                    <label key={role.roleCode} className="flex items-center gap-2 text-xs cursor-pointer py-1">
                      <input type="checkbox" checked={selected.includes(role.roleCode)}
                        onChange={() => toggleRole(topic, role.roleCode)} />
                      {role.title} (new)
                    </label>
                  ))}
                  <button type="button" onClick={() => {
                    const name = window.prompt("New role name:", String(proposedCodes[0] || ""));
                    if (name) {
                      const code = addInlineRole(name);
                      setAssignments((current) => ({
                        ...current, [topic]: [...(current[topic] || []), code],
                      }));
                    }
                  }} style={{ color: C.green700 }} className="text-xs font-semibold py-1">
                    + Add a new role
                  </button>
                </div>
              </details>
            </div>
          );
        })}
      </div>

      {Object.keys(assignments).length > 1 && (
        <button type="button" onClick={applyFirstToAll} style={{ color: C.green700 }}
          className="text-xs font-semibold mb-4">
          Apply the first selection to every section
        </button>
      )}

      {newRoles.length > 0 && (
        <p style={{ color: C.green700 }} className="text-xs font-semibold mb-3">
          Will be added to the company list: {newRoles.map((r) => r.title).join(", ")}
        </p>
      )}
      <label className="flex items-start gap-2 mb-4 cursor-pointer">
        <input type="checkbox" checked={makeRequired}
          onChange={(e) => setMakeRequired(e.target.checked)}
          className="mt-0.5" />
        <span style={{ color: C.ink }} className="text-xs">
          <span className="font-semibold">Also make this required</span>
          <span style={{ color: C.sub }}> — counts toward Q Score for the roles above.
            Leave checked unless this is optional reading.</span>
        </span>
      </label>

      {err && <ErrorBox error={err} />}
      <div className="flex gap-2">
        <Button onClick={confirm} disabled={busy || unresolved.length > 0}>
          {busy ? "Starting generation…" : unresolved.length > 0
            ? `Resolve ${unresolved.length} section(s) first` : "Confirm & generate"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

function CoursePreview({ training }) {
  const { data, loading, error, reload } = useAsync(
    () => api.coursePreview(training), [training]);
  if (loading) return <Loading label="Loading lesson preview…" />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <div style={{ borderColor: C.line }} className="border-t mt-4 pt-4">
      <h4 style={{ ...display, color: C.ink }} className="text-sm font-bold mb-2">Lesson preview</h4>
      <div className="space-y-2">
        {(data?.modules || []).map((module) => (
          <details key={module.moduleId} style={{ borderColor: C.line }} className="border-b last:border-b-0">
            <summary className="cursor-pointer list-none px-3 py-2.5 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p style={{ color: C.ink }} className="text-xs font-semibold truncate">{module.title}</p>
                <p style={{ color: module.status === "ready" ? C.sub : C.amber }} className="text-[11px] mt-0.5">
                  {module.status === "ready"
                    ? `${module.pages.length} pages · ${module.wordCount} words · ${module.learningPointCount} learning points`
                    : "Withheld: source quality requirements were not met"}
                </p>
              </div>
              <ChevronRight size={14} color={C.sub} className="rotate-90 shrink-0" />
            </summary>
            <div className="px-3 pb-3 space-y-2">
              {module.qualityNotes?.length > 0 && (
                <p style={{ color: C.amber }} className="text-xs">{module.qualityNotes.join(" · ")}</p>
              )}
              {module.pages.map((page) => (
                <details key={page.id} className="px-1 py-1">
                  <summary style={{ color: C.ink }} className="cursor-pointer text-xs font-semibold">
                    {page.order}. {page.title}
                  </summary>
                  <p style={{ color: C.sub, whiteSpace: "pre-wrap" }} className="text-xs leading-6 mt-2">
                    {page.body}
                  </p>
                </details>
              ))}
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}

function DocumentsScreen({ team, principal, onDone }) {
  const { data, loading, error, reload } = useAsync(() => api.documents(), []);
  const rolesQ = useAsync(() => api.roles(), []);
  const linksQ = useAsync(() => api.trustedLinks(), []);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [analysis, setAnalysis] = useState(null);   // awaiting manager confirmation
  const [job, setJob] = useState(null);
  const [deletingDocumentId, setDeletingDocumentId] = useState(null);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef(null);
  const pollRef = useRef(null);

  const generator = data?.generator || "mock";
  const billed = generator !== "mock";
  const roles = rolesQ.data?.roles || [];
  // Same tier POST /links/add itself checks server-side -- this only decides whether to
  // offer the option at all, so nobody picks "Company-wide" only to have it 403.
  const canPublishCompanyWide = ["admin", "executive"].includes(principal?.access_role);

  useEffect(() => {
    if (!job || job.state !== "running") return;
    pollRef.current = setInterval(async () => {
      try {
        const next = await api.jobStatus(job.jobId);
        setJob(next);
        if (next.state !== "running") { clearInterval(pollRef.current); reload(); }
      } catch { clearInterval(pollRef.current); }
    }, 1200);
    return () => clearInterval(pollRef.current);
  }, [job, reload]);

  // Rediscovers a job this component didn't start -- generation runs on the server
  // regardless of whether anyone's tab is open to watch it, so a job already `job`
  // knows about here is real state to resume, not something to invent. Runs whenever
  // the document list reloads (including the mount after navigating back to this
  // screen), which is exactly when a job started before navigating away needs to be
  // found again. Guarded on `!job` so it never clobbers a job this tab already knows
  // about with a slightly-stale copy of the same job from the list response.
  useEffect(() => {
    if (job || !data?.documents) return;
    const active = data.documents.find((d) => d.activeJob)?.activeJob;
    if (active) {
      setJob({ jobId: active.jobId, state: "running", total: active.total,
                done: active.done, kept: 0, message: active.message });
    }
  }, [data, job]);

  // Same idea as the job-resume effect above, for the step before a job even exists:
  // the AI's proposed mapping was pure React state in `analysis` and vanished the
  // moment this component unmounted, even though the chunks it describes were already
  // durable on the server. Now that proposal is saved server-side too (see
  // _ingest_and_propose), so a remount can put the manager right back in front of the
  // same MappingReview instead of an orphaned, unconfirmable document.
  useEffect(() => {
    if (analysis || !data?.documents) return;
    const pending = data.documents.find((d) => d.pendingAnalysis)?.pendingAnalysis;
    if (pending) setAnalysis(pending);
  }, [data, analysis]);

  const handleDeleteDocument = async (document) => {
    const confirmed = window.confirm(
      `Permanently delete "${document.title}"?\n\nThis removes the training from every ` +
      "employee's board and Q Score, including progress, quiz attempts, and " +
      "certificates. If it's still generating, this stops that too. This cannot be undone."
    );
    if (!confirmed) return;

    setDeletingDocumentId(document.documentId);
    setUploadError(null);
    try {
      await api.deleteDocument(document.documentId);
      if (job && document.title === job.title) setJob(null);
      if (analysis && document.documentId === analysis.documentId) setAnalysis(null);
      await Promise.all([reload(), linksQ.reload()]);
    } catch (e) {
      setUploadError(e);
    } finally {
      setDeletingDocumentId(null);
    }
  };

  const handleFiles = async (files) => {
    const file = files?.[0];
    if (!file) return;
    setUploading(true); setUploadError(null); setAnalysis(null); setJob(null);
    try {
      const res = await api.uploadDocument(file);
      setAnalysis(res);          // manager confirms before anything generates
      rolesQ.reload();
    } catch (e) { setUploadError(e); } finally { setUploading(false); }
  };

  // POST /links/add returns the exact same shape uploadDocument does (server runs both
  // through the same _ingest_and_propose), so a submitted link joins the identical
  // MappingReview flow below rather than needing its own confirmation UI.
  const handleLinkSubmitted = (res) => {
    setUploadError(null); setAnalysis(res); setJob(null);
    rolesQ.reload();
    linksQ.reload();
  };

  const pct = job && job.total ? Math.round((job.done / job.total) * 100) : 0;

  return (
    <div className="p-8">
      <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">Resources</h1>
      <p style={{ color: C.sub }} className="text-sm mb-6">
        Upload a training document. The AI maps each section to the role it trains,
        you confirm, and employees in those roles owe the module — renewed yearly.
      </p>

      <RoleManager roles={roles} onChanged={rolesQ.reload} />

      <div style={{ background: billed ? C.amberBg : C.mint, borderColor: billed ? C.amber : C.line }}
        className="border rounded-xl px-4 py-3 mb-5 flex items-start gap-2.5">
        <AlertCircle size={16} color={billed ? C.amber : C.green700} className="shrink-0 mt-0.5" />
        <div>
          <p style={{ color: billed ? C.amber : C.green700 }} className="text-sm font-semibold">
            Role mapping uses gpt-5 (a few cents per upload).
            {billed ? ` Question generation also uses ${generator} — billed.` : " Question generation is on the free mock provider."}
          </p>
        </div>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); handleFiles(e.dataTransfer.files); }}
        onClick={() => fileRef.current?.click()}
        style={{ borderColor: dragging ? C.green700 : C.line, background: dragging ? C.mint : "#fff" }}
        className="border-2 border-dashed rounded-2xl p-10 text-center cursor-pointer transition-colors mb-5"
      >
        <input ref={fileRef} type="file" accept=".pdf,.txt,.md" className="hidden"
          onChange={(e) => handleFiles(e.target.files)} />
        <div style={{ background: C.mint }} className="w-12 h-12 rounded-2xl flex items-center justify-center mx-auto mb-3">
          {uploading ? <Loader2 size={20} color={C.green700} className="animate-spin" /> : <Upload size={20} color={C.green700} />}
        </div>
        <p style={{ color: C.ink }} className="text-sm font-semibold mb-1">
          {uploading ? "Reading and mapping roles… (~10-30s)" : "Drop a PDF here, or click to choose"}
        </p>
        <p style={{ color: C.sub }} className="text-xs">PDF, TXT or MD · up to 25 MB</p>
      </div>

      <TrustedLinkForm roles={roles} canPublishCompanyWide={canPublishCompanyWide}
        onSubmitted={handleLinkSubmitted} />

      {uploadError && <ErrorBox error={uploadError} />}

      {analysis && (
        <MappingReview
          analysis={analysis}
          roles={roles}
          onCancel={async () => {
            // The proposal is now saved server-side (pending_analysis_json), not just
            // in this component's state -- clearing local state alone would leave it
            // there to reappear on the next reload. Deleting the document is the
            // actual cancel: it clears the proposal, the orphaned chunks, and the
            // registry row together, the same one action "cancel any upload" needs.
            setAnalysis(null);
            if (analysis.documentId) {
              try { await api.deleteDocument(analysis.documentId); } catch { /* best effort */ }
              reload();
            }
          }}
          onConfirmed={(result) => {
            setAnalysis(null);
            if (result.jobId) setJob({ jobId: result.jobId, state: "running", done: 0, total: 0, kept: 0, message: "Starting…" });
            rolesQ.reload();
          }}
        />
      )}

      {job && (
        <div style={{ borderColor: C.line }} className="border rounded-xl p-4 bg-white mb-5">
          <div className="flex items-center justify-between gap-3 mb-2">
            <p style={{ color: C.ink }} className="text-sm font-semibold flex items-center gap-2">
              {job.state === "running" && <Loader2 size={14} className="animate-spin" />}
              {job.state === "running" ? "Building course…" : job.state === "error" ? "Generation failed" : "Course ready"}
            </p>
            <span style={{ color: C.sub }} className="text-xs font-semibold">
              {job.total ? `${job.done}/${job.total}` : ""}
            </span>
          </div>
          <div style={{ background: C.line }} className="w-full h-2 rounded-full overflow-hidden mb-2">
            <div style={{
              width: `${job.state === "done" ? 100 : pct}%`,
              background: job.state === "error" ? C.danger : C.green700,
            }} className="h-full rounded-full transition-all" />
          </div>
          <p style={{ color: job.state === "error" ? C.danger : C.sub }} className="text-xs">{job.message}</p>
          {job.state === "done" && (
            <>
              {job.title && <CoursePreview training={job.title} />}
              <div className="mt-3"><Button onClick={onDone}>Done</Button></div>
            </>
          )}
        </div>
      )}

      <div className="flex items-center justify-between mb-3">
        <h3 style={{ ...display, color: C.ink }} className="font-bold">Documents in the bank</h3>
        <button onClick={reload} style={{ color: C.green700 }} className="text-xs font-semibold flex items-center gap-1">
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {loading && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}

      <div className="space-y-2">
        {(data?.documents || []).map((d) => (
          <div key={d.documentId || d.title} style={{ borderColor: C.line }} className="border rounded-xl p-4 bg-white flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <div style={{ background: d.ready ? C.successBg : C.amberBg }} className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0">
                <FileText size={16} color={d.ready ? C.success : C.amber} />
              </div>
              <div className="min-w-0">
                <p style={{ color: C.ink }} className="text-sm font-semibold truncate">{d.title}</p>
                <p style={{ color: C.sub }} className="text-xs">
                  {d.chunks} section{d.chunks === 1 ? "" : "s"} · {d.questions} question{d.questions === 1 ? "" : "s"}
                </p>
                <p style={{ color: C.sub }} className="text-xs truncate">Added by {d.uploadedBy}</p>
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <StatusPill status={d.activeJob ? "in-progress" : d.ready ? "completed" : "in-progress"} />
              {d.canDelete && (
                <button
                  type="button"
                  title={d.activeJob ? `Cancel and delete ${d.title}` : `Delete ${d.title}`}
                  aria-label={`Delete ${d.title}`}
                  disabled={deletingDocumentId === d.documentId}
                  onClick={() => handleDeleteDocument(d)}
                  style={{ color: C.danger }}
                  className="w-9 h-9 flex items-center justify-center disabled:opacity-40"
                >
                  {deletingDocumentId === d.documentId
                    ? <Loader2 size={16} className="animate-spin" />
                    : <Trash2 size={16} />}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between mb-3 mt-8">
        <h3 style={{ ...display, color: C.ink }} className="font-bold">Trusted links</h3>
        <button onClick={linksQ.reload} style={{ color: C.green700 }} className="text-xs font-semibold flex items-center gap-1">
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {linksQ.loading && <Loading />}
      {linksQ.error && <ErrorBox error={linksQ.error} onRetry={linksQ.reload} />}

      <div className="space-y-2">
        {(linksQ.data?.links || []).map((l) => (
          <div key={l.id} style={{ borderColor: C.line }} className="border rounded-xl p-4 bg-white flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <div style={{ background: l.isActive ? C.successBg : "#F1F0F3" }} className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0">
                <Link2 size={16} color={l.isActive ? C.success : "#9A93A8"} />
              </div>
              <div className="min-w-0">
                <p style={{ color: C.ink }} className="text-sm font-semibold truncate">{l.url}</p>
                <p style={{ color: C.sub }} className="text-xs">
                  {l.scope === "company_wide" ? "Company-wide" : l.roleCode}
                  {l.addedBy ? ` · added by ${l.addedBy}` : ""}
                </p>
              </div>
            </div>
            <span style={{ background: l.isActive ? C.successBg : "#F1F0F3", color: l.isActive ? C.success : "#9A93A8" }}
              className="text-[11px] font-semibold px-2.5 py-1 rounded-full shrink-0">
              {l.isActive ? "Active" : "Retired"}
            </span>
          </div>
        ))}
        {!linksQ.loading && !(linksQ.data?.links || []).length && (
          <p style={{ color: C.sub }} className="text-xs">No trusted links yet.</p>
        )}
      </div>
    </div>
  );
}

// ---------- Manager team (design-only) ----------
/** Who this person reports to — shown on My Team above the reports table, so the
    chain reads both directions instead of only downward. Its own component: shown
    whether or not there's anyone below you, so it can't get lost inside the
    "nobody reports to you yet" early return below. */
function ReportsToCard({ manager }) {
  if (!manager) return null;
  return (
    <div style={{ borderColor: C.line }} className="border rounded-xl p-4 bg-white flex items-center gap-3 mb-6">
      <div style={{ background: C.mint, color: C.green700 }}
        className="w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold shrink-0">
        {manager.name.split(" ").map((p) => p[0]).join("")}
      </div>
      <div className="min-w-0">
        <p style={{ color: C.sub }} className="text-xs font-semibold">You report to</p>
        <p style={{ color: C.ink }} className="text-sm font-semibold truncate">
          {manager.name} <span style={{ color: C.sub }} className="font-normal">— {manager.title || manager.roleCode}</span>
        </p>
      </div>
    </div>
  );
}

/**
 * One row per direct report, rolling up everyone who reports through them (their own
 * subtree, however deep) into one line: team size, how many of that team are missing
 * required training right now, how many are still compliant but have something expiring
 * soon, and the team's average completion. Real numbers only -- every count here comes
 * from api.teamCompletion(), which is qscore.standing() run for each person, the same
 * arithmetic /qscore uses for one person's own score page.
 */
function buildTeamRows(people, completionByEmployeeId) {
  const direct = people.filter((p) => p.direct);
  const childrenByManager = new Map();
  for (const p of people) {
    const key = p.managerId;
    if (!childrenByManager.has(key)) childrenByManager.set(key, []);
    childrenByManager.get(key).push(p);
  }
  const subtreeOf = (root) => {
    const out = [root];
    const queue = [root];
    while (queue.length) {
      const current = queue.shift();
      for (const kid of childrenByManager.get(current.employeeId) || []) {
        out.push(kid);
        queue.push(kid);
      }
    }
    return out;
  };

  return direct.map((rep) => {
    const members = subtreeOf(rep);
    const stats = members
      .map((m) => completionByEmployeeId.get(m.employeeId))
      .filter(Boolean);
    let incomplete = 0, withinDeadline = 0, coverageSum = 0;
    for (const s of stats) {
      const compliant = s.current >= s.required;
      if (!compliant) incomplete += 1;
      else if (s.renewalDueCount > 0) withinDeadline += 1;
      coverageSum += s.coverage;
    }
    return {
      employeeId: rep.employeeId,
      name: rep.name,
      email: rep.email,
      teamSize: members.length,
      incomplete,
      withinDeadline,
      completionPercent: stats.length ? Math.round(coverageSum / stats.length) : null,
      statsKnown: stats.length === members.length,
    };
  });
}

function downloadTeamCsv(rows, showTeamSize) {
  const header = ["Name", "Email", ...(showTeamSize ? ["Team size"] : []),
    "Incomplete", "Within deadline", "Completion %"];
  const lines = [header, ...rows.map((r) => [
    r.name, r.email, ...(showTeamSize ? [r.teamSize] : []), r.incomplete, r.withinDeadline,
    r.completionPercent == null ? "" : r.completionPercent,
  ])];
  const csv = lines
    .map((line) => line.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(","))
    .join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "my-team.csv";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function ReminderCell({ employeeId }) {
  // "idle" -> "sending" -> a short-lived result string ("Sent" or the server's own
  // reason, e.g. "Already compliant" or "Email isn't configured"). Never assumes
  // success client-side -- the label always reflects what POST /team/remind actually
  // returned.
  const [state, setState] = useState("idle");
  const [note, setNote] = useState({ label: "", full: "" });

  // The server's `reason` is a full sentence (it has to be honest about exactly why
  // nothing was delivered) but a table cell is not the place for one -- show a short
  // label and put the whole thing in a tooltip rather than reflowing the row.
  const shortLabel = (reason) => {
    if (!reason) return "Not sent";
    const r = reason.toLowerCase();
    if (r.includes("already compliant")) return "Already compliant";
    if (r.includes("does not send email") || r.includes("not configured") || r.includes("resend_api_key")) {
      return "Email not configured";
    }
    return reason.length > 28 ? reason.slice(0, 27) + "…" : reason;
  };

  const send = async () => {
    setState("sending");
    try {
      const res = await api.sendReminder(employeeId);
      setNote(res.sent
        ? { label: "Sent", full: "Reminder email sent." }
        : { label: shortLabel(res.reason), full: res.reason || "Not sent." });
      setState("done");
    } catch (err) {
      const msg = err.message || "Failed";
      setNote({ label: shortLabel(msg), full: msg });
      setState("done");
    }
  };

  if (state === "sending") return <Loader2 size={14} className="animate-spin" color={C.green700} />;
  if (state === "done") {
    return (
      <span style={{ color: C.sub }} className="text-xs whitespace-nowrap" title={note.full}>
        {note.label}
      </span>
    );
  }
  return (
    <button
      onClick={send}
      style={{ background: C.green700, color: "#fff" }}
      className="opacity-0 group-hover:opacity-100 transition-opacity text-xs font-semibold px-3 py-1.5 rounded-full inline-flex items-center gap-1.5 whitespace-nowrap"
    >
      <Send size={12} /> Send reminder
    </button>
  );
}

function ManagerTeam({ team }) {
  const people = team?.people || [];
  const targets = team?.uploadTargets || [];
  const { data: completion, loading: completionLoading, error: completionError } =
    useAsync(() => api.teamCompletion(), []);
  const [query, setQuery] = useState("");

  if (!people.length) {
    return (
      <div className="p-8">
        <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">Reports</h1>
        <ReportsToCard manager={team?.manager} />
        <p style={{ color: C.sub }} className="text-sm">Nobody reports to you yet.</p>
      </div>
    );
  }

  const completionByEmployeeId = new Map(
    (completion?.people || []).map((p) => [p.employeeId, p])
  );
  const rows = buildTeamRows(people, completionByEmployeeId);
  const q = query.trim().toLowerCase();
  const filteredRows = rows.filter(
    (r) => !q || r.name.toLowerCase().includes(q) || r.email.toLowerCase().includes(q)
  );

  // "Team size" only means something once a row can BE more than one person. A
  // manager whose direct reports are individual contributors (SDE1s, SDE2s, ...) gets
  // rows that are always size 1 -- the column would just repeat "1" down the whole
  // table. It only earns its place once at least one direct report is themselves a
  // manager, which is also when this reads more like "my managers" than "my team".
  const showTeamSize = rows.some((r) => r.teamSize > 1);
  const tableLabel = showTeamSize ? "Managers" : "My team";

  return (
    <div className="p-8">
      <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">Reports</h1>
      <p style={{ color: C.sub }} className="text-sm mb-6">
        Everyone who reports to you, and how their training is going.
      </p>

      <ReportsToCard manager={team?.manager} />

      {completionError && <ErrorBox error={completionError} />}

      <div style={{ borderColor: C.line }} className="border rounded-xl bg-white overflow-hidden mb-6">
        <div style={{ borderColor: C.line }} className="border-b px-5 py-4 flex items-center justify-between gap-4 flex-wrap">
          <h2 style={{ ...display, color: C.ink }} className="font-bold">
            {tableLabel} <span style={{ color: C.sub }} className="font-normal">({rows.length})</span>
          </h2>
          <div className="flex items-center gap-3">
            <div style={{ borderColor: C.line }} className="border rounded-xl px-3 py-1.5 flex items-center gap-2">
              <Search size={14} color={C.sub} />
              <input
                value={query} onChange={(e) => setQuery(e.target.value)}
                placeholder="Search team"
                style={{ color: C.ink }}
                className="text-sm outline-none w-32 bg-transparent"
              />
            </div>
            <button onClick={() => downloadTeamCsv(rows, showTeamSize)} style={{ color: C.green700 }}
              className="text-sm font-semibold flex items-center gap-1.5 whitespace-nowrap">
              <Download size={14} /> CSV file
            </button>
          </div>
        </div>

        <table className="w-full text-sm">
          <thead>
            <tr style={{ background: C.mint, color: C.green700 }} className="text-left text-xs uppercase tracking-wide">
              <th className="px-5 py-3 font-semibold whitespace-nowrap">Name</th>
              {showTeamSize && <th className="px-5 py-3 font-semibold whitespace-nowrap">Team size</th>}
              <th className="px-5 py-3 font-semibold whitespace-nowrap">Incomplete</th>
              <th className="px-5 py-3 font-semibold whitespace-nowrap">Within deadline</th>
              <th className="px-5 py-3 font-semibold whitespace-nowrap">Completion</th>
              <th className="px-5 py-3 font-semibold text-right whitespace-nowrap min-w-[160px]">&nbsp;</th>
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((r) => (
              <tr key={r.employeeId} style={{ borderTop: `1px solid ${C.line}` }} className="group">
                <td className="px-5 py-3.5">
                  <p style={{ color: C.ink }} className="font-semibold">{r.name}</p>
                  <p style={{ color: C.sub }} className="text-xs">{r.email}</p>
                </td>
                {showTeamSize && <td className="px-5 py-3.5" style={{ color: C.ink }}>{r.teamSize}</td>}
                <td className="px-5 py-3.5" style={{ color: C.ink }}>
                  {completionLoading && !r.statsKnown ? "…" : r.incomplete}
                </td>
                <td className="px-5 py-3.5" style={{ color: C.ink }}>
                  {completionLoading && !r.statsKnown ? "…" : r.withinDeadline}
                </td>
                <td className="px-5 py-3.5" style={{ color: C.ink }}>
                  {r.completionPercent == null ? (completionLoading ? "…" : "—") : `${r.completionPercent}%`}
                </td>
                <td className="px-5 py-3.5 text-right">
                  <ReminderCell employeeId={r.employeeId} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {targets.length > 0 && (
        <div style={{ borderColor: C.line }} className="border rounded-xl p-5 bg-white">
          <p style={{ color: C.ink }} className="text-sm font-semibold mb-1">Upload training for</p>
          <p style={{ color: C.sub }} className="text-xs mb-3">
            Roles held by your reports. The ones your direct reports hold are marked; you can
            also upload for roles further down if you need to.
          </p>
          <div className="flex flex-wrap gap-2">
            {targets.map((t) => (
              <span key={t.roleCode}
                    style={{ borderColor: t.direct ? C.green700 : C.line,
                             color: t.direct ? C.green700 : C.sub,
                             background: t.direct ? C.mint : "#fff" }}
                    className="border rounded-full px-3 py-1 text-xs font-semibold">
                {t.title} · {t.headcount}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// The department-wide leaderboard is the whole page now -- TeamLeaderboard fetches
// its own data (GET /team/leaderboard), so this is just the page frame around it.
function TeammatesGallery() {
  return (
    <div className="p-8">
      <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">Team</h1>
      <p style={{ color: C.sub }} className="text-sm mb-6">Ranked by points earned from trainings completed.</p>
      <TeamLeaderboard />
    </div>
  );
}

function LeaderboardRankBadge({ rank }) {
  const top = rank === 1;
  const podium = rank <= 3;
  return (
    <div
      style={{
        background: top ? C.green700 : podium ? C.mint : "transparent",
        color: top ? "#fff" : podium ? C.green700 : C.sub,
        borderColor: podium ? "transparent" : C.line,
      }}
      className="w-7 h-7 rounded-full border flex items-center justify-center text-xs font-bold shrink-0"
    >
      {rank}
    </div>
  );
}

function TeamLeaderboard() {
  const { data, loading, error, reload } = useAsync(() => api.teamLeaderboard(), []);

  return (
    <div>
      {loading && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}

      {data && data.leaderboard.length === 0 && (
        <p style={{ color: C.sub }} className="text-sm">No department is set up for your role yet.</p>
      )}

      {data && data.leaderboard.length > 0 && (
        <div style={{ borderColor: C.line }} className="border rounded-xl bg-white overflow-hidden">
          {data.leaderboard.map((row, i) => (
            <div
              key={row.employeeId}
              style={{
                borderColor: C.line,
                background: row.isYou ? C.mint : "transparent",
              }}
              className={`flex items-center gap-4 px-5 py-3 ${i > 0 ? "border-t" : ""}`}
            >
              <LeaderboardRankBadge rank={i + 1} />
              <div className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0" style={{ background: C.mint }}>
                <PetRobotSVG size={24} mood="idle" equippedItemIds={row.equippedItemIds} />
              </div>
              <div className="flex-1 min-w-0">
                <p style={{ color: C.ink }} className="text-sm font-semibold truncate">
                  {row.name}{row.isYou ? " (you)" : ""}
                </p>
                <p style={{ color: C.sub }} className="text-xs truncate">{row.title}</p>
              </div>
              <div className="text-right shrink-0">
                <p style={{ ...display, color: C.ink }} className="text-sm font-bold">{row.pointsEarned}</p>
                <p style={{ color: C.sub }} className="text-[10px] uppercase tracking-wide">points</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MyPetCard({ name, qScore }) {
  const { data, loading, error, reload } = useAsync(() => api.getPet(), []);
  const [showShop, setShowShop] = useState(false);
  const [showShare, setShowShare] = useState(false);

  return (
    <div style={{ borderColor: C.line }} className="border rounded-xl p-5 bg-white mb-8">
      <div className="flex items-center justify-between mb-4">
        <h3 style={{ ...display, color: C.ink }} className="font-bold">Your robot</h3>
        {data && (
          <div className="flex items-center gap-2">
            <button onClick={() => setShowShare(true)} style={{ borderColor: C.line, color: C.green700 }}
              className="border text-xs font-semibold px-3 py-1.5 rounded-full flex items-center gap-1.5">
              <Share2 size={12} /> Share
            </button>
            <button onClick={() => setShowShop(true)} style={{ background: C.green700 }}
              className="text-white text-xs font-semibold px-3 py-1.5 rounded-full">
              Customize
            </button>
          </div>
        )}
      </div>

      {loading && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}

      {data && (
        <div className="flex items-center gap-6 flex-wrap">
          <div className="rounded-2xl flex items-center justify-center shrink-0" style={{ width: 170, height: 190, background: C.mint }}>
            <PetRobotSVG size={104} mood="idle" equippedItemIds={data.equippedItemIds} />
          </div>
          <div className="flex-1 min-w-[200px]">
            <p style={{ ...display, color: C.ink }} className="text-lg font-bold mb-0.5">{data.pointsBalance} points</p>
            <p style={{ color: C.sub }} className="text-sm mb-3">
              {data.pointsEarned} earned from {data.trainingsCompleted} training{data.trainingsCompleted === 1 ? "" : "s"} completed
              {data.ownedItemIds.length > 0 && ` · ${data.ownedItemIds.length} item${data.ownedItemIds.length === 1 ? "" : "s"} owned`}
            </p>
            <p style={{ color: C.sub }} className="text-xs">
              Finish a training to earn 100 points, then spend them on the shop to dress up your robot.
            </p>
          </div>
        </div>
      )}

      {showShop && (
        <PetShopModal onClose={() => setShowShop(false)} onChanged={() => reload()} />
      )}
      {showShare && data && (
        <ShareCharacterModal equippedItemIds={data.equippedItemIds} name={name} qScore={qScore}
          trainingsCompleted={data.trainingsCompleted} onClose={() => setShowShare(false)} />
      )}
    </div>
  );
}

function ShareCharacterModal({ equippedItemIds, name, qScore, trainingsCompleted, onClose }) {
  const svgRef = useRef(null);
  const [copied, setCopied] = useState(false);

  const handleDownload = () => {
    const svgEl = svgRef.current;
    if (!svgEl) return;
    const svgStr = new XMLSerializer().serializeToString(svgEl);
    const svgBlob = new Blob([svgStr], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(svgBlob);
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = 480; canvas.height = 680;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      const a = document.createElement("a");
      a.href = canvas.toDataURL("image/png");
      a.download = "my-ascend-robot-card.png";
      a.click();
    };
    img.src = url;
  };

  const caption = `Meet my Ascend robot! ${trainingsCompleted} trainings completed, Q Score ${qScore}.`;
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(caption);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard unavailable */ }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(30,27,46,0.6)" }} onClick={onClose}>
      <div style={font} className="bg-white rounded-2xl p-5 max-w-sm w-full" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 style={{ ...display, color: C.ink }} className="font-bold">Share your robot</h3>
          <button onClick={onClose} style={{ color: C.sub }}><X size={18} /></button>
        </div>

        <div className="flex justify-center mb-4">
          <svg ref={svgRef} width="240" height="340" viewBox="0 0 240 340" xmlns="http://www.w3.org/2000/svg">
            <rect x="0" y="0" width="240" height="340" rx="20" fill={C.green900} />
            <text x="20" y="32" fill="#fff" fontSize="13" fontWeight="700" fontFamily="'Playfair Display', serif" letterSpacing="1">ASCEND</text>
            <g transform="translate(60, 50)">
              <PetRobotSVG size={120} mood="happy" equippedItemIds={equippedItemIds} />
            </g>
            <text x="120" y="250" textAnchor="middle" fill="#CFE9D9" fontSize="12" fontWeight="600" fontFamily="'Inter', sans-serif">{name}</text>
            <line x1="30" y1="268" x2="210" y2="268" stroke="rgba(255,255,255,0.2)" />
            <text x="70" y="292" textAnchor="middle" fill="#fff" fontSize="16" fontWeight="700" fontFamily="'Playfair Display', serif">{qScore}</text>
            <text x="70" y="308" textAnchor="middle" fill="#A9DFC0" fontSize="9" fontFamily="'Inter', sans-serif">Q SCORE</text>
            <text x="170" y="292" textAnchor="middle" fill="#fff" fontSize="16" fontWeight="700" fontFamily="'Playfair Display', serif">{trainingsCompleted}</text>
            <text x="170" y="308" textAnchor="middle" fill="#A9DFC0" fontSize="9" fontFamily="'Inter', sans-serif">TRAININGS</text>
            <text x="120" y="325" textAnchor="middle" fill="rgba(255,255,255,0.5)" fontSize="9" fontFamily="'Inter', sans-serif">ascend.app</text>
          </svg>
        </div>

        <p style={{ color: C.sub }} className="text-xs text-center mb-4">Download the card, or copy a caption to post alongside a screenshot.</p>

        <div className="flex gap-2">
          <button onClick={handleDownload} style={{ background: C.green700 }} className="flex-1 flex items-center justify-center gap-1.5 text-white text-sm font-semibold rounded-xl py-2.5">
            <Download size={14} /> Download
          </button>
          <button onClick={handleCopy} style={{ borderColor: C.line, color: C.green700 }} className="flex-1 border flex items-center justify-center gap-1.5 text-sm font-semibold rounded-xl py-2.5">
            <Copy size={14} /> {copied ? "Copied!" : "Copy caption"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------- Skill interest popup ----------
// GET /skills/options reports `prompted` from Employees.skills_prompted_at against a
// rolling cooldown, so this can show again -- but only when there's something new the
// employee hasn't already been offered or picked. Offers only trainings already visible
// to this person's role and not already required for it (enforced server-side, not just
// hidden here) -- there is deliberately no free-text option, so every choice maps to
// something that already exists in the bank and can be recommended immediately, not
// something the AI has to go build from nothing.
function SkillInterestPopup({ onRecorded }) {
  const [state, setState] = useState({ loading: true, options: null });
  const [selected, setSelected] = useState(new Set());
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.skillOptions()
      .then((res) => {
        if (cancelled) return;
        setState({ loading: false, options: !res.prompted && res.options.length > 0 ? res.options : null });
      })
      .catch(() => { if (!cancelled) setState({ loading: false, options: null }); });
    return () => { cancelled = true; };
  }, []);

  if (state.loading || !state.options) return null;

  const toggle = (title) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(title) ? next.delete(title) : next.add(title);
      return next;
    });
  };

  const submit = async (skills) => {
    setSubmitting(true);
    try {
      await api.setSkillInterest(skills);
    } catch {
      // The popup closing either way is more honest than pretending a retry loop here
      // would help -- worst case it asks again a later session, which is a mild
      // inconvenience, not a broken feature.
    } finally {
      setSubmitting(false);
      setState({ loading: false, options: null });
      onRecorded && onRecorded();
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(30,27,46,0.6)" }}>
      <div style={font} className="bg-white rounded-2xl p-6 max-w-md w-full">
        <div className="flex items-start justify-between gap-3 mb-1">
          <div style={{ background: C.lavender }} className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0">
            <Target size={18} color={C.violet700} />
          </div>
          <button onClick={() => submit([])} disabled={submitting} style={{ color: C.sub }} className="shrink-0">
            <X size={18} />
          </button>
        </div>
        <h3 style={{ ...display, color: C.ink }} className="font-bold text-lg mt-3 mb-1">Anything you'd like to learn?</h3>
        <p style={{ color: C.sub }} className="text-sm mb-4">
          These are already in the training bank, beyond what your role requires. Pick any
          that interest you and they'll show up under Recommended.
        </p>

        <div className="space-y-2 mb-5 max-h-64 overflow-y-auto">
          {state.options.map((title) => {
            const isChecked = selected.has(title);
            return (
              <button key={title} onClick={() => toggle(title)}
                style={{ borderColor: isChecked ? C.violet700 : C.line, background: isChecked ? C.lavender : "#fff" }}
                className="w-full text-left border rounded-lg px-3 py-2.5 text-sm flex items-center gap-2.5">
                {isChecked
                  ? <CheckCircle2 size={16} color={C.violet700} className="shrink-0" />
                  : <Circle size={16} color="#C9C2DB" className="shrink-0" />}
                <span style={{ color: C.ink }} className="min-w-0">{title}</span>
              </button>
            );
          })}
        </div>

        <div className="flex gap-2">
          <Button onClick={() => submit([...selected])} disabled={submitting || selected.size === 0}>
            {submitting ? "Saving…" : selected.size > 0 ? `Save ${selected.size} pick${selected.size === 1 ? "" : "s"}` : "Pick at least one"}
          </Button>
          <button onClick={() => submit([])} disabled={submitting} style={{ color: C.sub }} className="text-sm font-semibold px-3">
            None of these
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------- Profile ----------
function SettingsToggleRow({ title, description, value, saving, onToggle }) {
  return (
    <div style={{ borderColor: C.line }} className="border rounded-xl bg-white p-5 flex items-center justify-between gap-6">
      <div className="min-w-0">
        <p style={{ color: C.ink }} className="text-sm font-semibold mb-1">{title}</p>
        <p style={{ color: C.sub }} className="text-xs">{description}</p>
      </div>
      <button
        onClick={onToggle} disabled={saving} aria-pressed={value}
        style={{ background: value ? C.green700 : C.line }}
        className="w-11 h-6 rounded-full relative shrink-0 transition-colors disabled:opacity-60"
      >
        <span
          style={{ background: "#fff", left: value ? 22 : 2 }}
          className="w-5 h-5 rounded-full absolute top-0.5 transition-all"
        />
      </button>
    </div>
  );
}

// settings/onSettingsChange are lifted to App rather than fetched here, so toggling
// petVisible takes effect on the floating pet immediately -- Shell reads the same
// state this page writes.
function Settings({ settings, onSettingsChange }) {
  const [saving, setSaving] = useState(null); // null | "notifications" | "pet"
  const [saveError, setSaveError] = useState(null);

  const toggle = async (field, key) => {
    setSaving(field);
    setSaveError(null);
    try {
      const next = await api.updateSettings({ [key]: !settings[key] });
      onSettingsChange(next);
    } catch (err) {
      setSaveError(err.message || "Could not save.");
    }
    setSaving(null);
  };

  return (
    <div className="p-8">
      <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">Settings</h1>
      <p style={{ color: C.sub }} className="text-sm mb-8">Your account preferences.</p>

      {!settings && <Loading />}

      {settings && (
        <div className="space-y-4">
          <SettingsToggleRow
            title="Email notifications"
            description="Reminders about training that's due soon, and a notice when something new is assigned to your role."
            value={settings.notificationsEnabled}
            saving={saving === "notifications"}
            onToggle={() => toggle("notifications", "notificationsEnabled")}
          />
          <SettingsToggleRow
            title="Desk pet"
            description="The floating character in the corner of the screen. Turn this off if you'd rather it not be there -- while taking a quiz, or ever."
            value={settings.petVisible}
            saving={saving === "pet"}
            onToggle={() => toggle("pet", "petVisible")}
          />
        </div>
      )}
      {saveError && <p style={{ color: C.danger }} className="text-xs font-semibold mt-3">{saveError}</p>}
    </div>
  );
}

function Profile({ principal }) {
  // Identity from the signed-in principal. PROFILES is a fallback ONLY for the fields
  // that still have no backend (joined date) — using it for name or email would show
  // one hardcoded persona to whoever actually signed in.
  const persona = PROFILES.employee;
  const p = {
    ...persona,
    name: principal?.name || principal?.email || persona.name,
    email: principal?.email || persona.email,
    role: principal?.department || persona.role,
    title: principal?.title || "",
  };
  const { data: meData, loading, error, reload } = useAsync(() => api.me(), []);
  const { data: certData } = useAsync(() => api.certificates().catch(() => ({ certificates: [] })), []);
  const { data: trainData } = useAsync(() => api.trainings().catch(() => ({ trainings: [] })), []);

  const topics = meData?.topics || [];
  const certs = certData?.certificates || [];
  const completed = (trainData?.trainings || []).filter((t) => t.status === "completed").length;

  // Q Score comes from the server (docs/q-score.md): Coverage x Quality, where Coverage
  // is unexpired certificates over the ones your role requires.
  //
  // This used to be computed here as raw accuracy across every question answered — a
  // THIRD definition of "Q Score", alongside the per-attempt score and the compliance
  // rollup. It also could not fall when a certificate expired, because nothing about
  // answering questions changes when time passes.
  const { data: qData } = useAsync(() => api.qscore().catch(() => null), []);
  const standing = qData?.overall;
  const qScore = standing ? Math.round(standing.qScore) : 0;

  const streak = meData?.streak ?? 0;

  // meData.badges is {badgeId: earnedAtIsoOrNull}, keyed by string over JSON. A badge
  // id present has been earned (the value is the date for one-time badges, null for
  // the two "live" ones that track current state); an id absent -- Privacy Pro and
  // Early Bird, see quizgen.qscore.earned_badges -- has no real criterion behind it
  // yet and stays permanently locked rather than showing a fake earned date.
  const badgeStatus = meData?.badges || {};
  const badges = BADGES.map((b) => {
    const key = String(b.id);
    const earned = Object.prototype.hasOwnProperty.call(badgeStatus, key);
    const earnedAt = badgeStatus[key];
    return { ...b, earned, date: earnedAt ? String(earnedAt).slice(0, 10) : null };
  });
  const earnedCount = badges.filter((b) => b.earned).length;

  return (
    <div className="p-8">
      <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">My profile</h1>
      <p style={{ color: C.sub }} className="text-sm mb-6">Your progress and Q score at a glance.</p>

      {loading && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}

      <div style={{ background: C.green700 }}
        className="rounded-2xl p-6 mb-6 text-white flex items-center justify-between gap-6 flex-wrap">
        <div className="flex items-center gap-4">
          <div style={{ background: "rgba(255,255,255,0.15)" }} className="w-16 h-16 rounded-2xl flex items-center justify-center text-xl font-bold shrink-0">
            {p.name.split(" ").map((n) => n[0]).join("")}
          </div>
          <div>
            <h2 style={display} className="text-xl font-bold mb-1">{p.name}</h2>
            <p className="text-sm opacity-90 flex items-center gap-1.5 mb-0.5"><Briefcase size={13} /> {p.role}</p>
            {p.title && <p className="text-sm opacity-90 mb-0.5 pl-[19px]">{p.title}</p>}
            <p className="text-sm opacity-90 flex items-center gap-1.5"><Mail size={13} /> {p.email}</p>
          </div>
        </div>
        <div className="text-center shrink-0">
          <MasteryRing value={qScore} size={92} stroke={8} />
          <p className="text-xs opacity-90 mt-1 font-semibold uppercase tracking-wide">Q Score</p>
          {/* The two numbers behind it. A single composite is ambiguous — 40 could be
              "half done, perfect scores" or "all done, poor scores" — and those call for
              completely different things from the person reading it. */}
          {standing ? (
            <p className="text-[11px] opacity-70">
              {standing.current}/{standing.required} current · avg {Math.round(standing.quality)}
            </p>
          ) : (
            <p className="text-[11px] opacity-70">—</p>
          )}
        </div>
      </div>

      {qData && !qData.requirementsConfigured && (
        <div style={{ background: C.amberBg, color: C.amber }} className="rounded-xl px-4 py-3 text-xs mb-6">
          No required training has been set for your role yet, so there is nothing to
          measure your Q Score against. It will stay at 0 until an admin sets one — that
          is a missing configuration, not a reflection of your work.
        </div>
      )}

      {qData && qData.requirementsConfigured && (
        <div className="grid grid-cols-2 gap-4 mb-6">
          {[["Behavioural", qData.behavioural], ["Technical", qData.technical]].map(([label, s]) => (
            <div key={label} style={{ borderColor: C.line }} className="border rounded-xl p-4 bg-white">
              <p style={{ color: C.sub }} className="text-xs font-semibold mb-1">{label}</p>
              <p style={{ ...display, color: C.ink }} className="text-2xl font-bold">
                {s.required ? Math.round(s.qScore) : "—"}
              </p>
              <p style={{ color: C.sub }} className="text-[11px]">
                {s.required ? `${s.current}/${s.required} current` : "nothing required"}
              </p>
            </div>
          ))}
        </div>
      )}

      <MyPetCard name={p.name} qScore={qScore} />

      <div className="grid grid-cols-3 gap-4 mb-8">
        <div style={{ borderColor: C.line }} className="border rounded-xl p-4 bg-white flex items-center gap-3">
          <div style={{ background: C.mint }} className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0">
            <Flame size={16} color={C.green700} />
          </div>
          <div className="min-w-0">
            <p style={{ ...display, color: C.ink }} className="text-lg font-bold leading-tight">{streak}</p>
            <p style={{ color: C.sub }} className="text-xs">Training streak</p>
          </div>
        </div>
        <div style={{ borderColor: C.line }} className="border rounded-xl p-4 bg-white flex items-center gap-3">
          <div style={{ background: C.amberBg }} className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0">
            <Award size={16} color={C.amber} />
          </div>
          <div>
            <p style={{ ...display, color: C.ink }} className="text-lg font-bold leading-tight">{certs.length}</p>
            <p style={{ color: C.sub }} className="text-xs">Certificates earned</p>
          </div>
        </div>
        <div style={{ borderColor: C.line }} className="border rounded-xl p-4 bg-white flex items-center gap-3">
          <div style={{ background: C.successBg }} className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0">
            <Star size={16} color={C.success} />
          </div>
          <div className="min-w-0">
            <p style={{ ...display, color: C.ink }} className="text-lg font-bold leading-tight">{earnedCount}/{BADGES.length}</p>
            <p style={{ color: C.sub }} className="text-xs">Badges earned</p>
          </div>
        </div>
      </div>

      <h3 style={{ ...display, color: C.ink }} className="font-bold mb-3">Q score breakdown</h3>
      <div style={{ borderColor: C.line }} className="border rounded-xl p-5 bg-white mb-8 space-y-4">
        {topics.length === 0 && (
          <p style={{ color: C.sub }} className="text-sm">Nothing answered yet — take a quiz and this fills in.</p>
        )}
        {topics.map((b) => (
          <div key={b.topic}>
            <div className="flex items-center justify-between gap-3 mb-1.5">
              <span style={{ color: C.ink }} className="text-sm font-semibold min-w-0 truncate">{b.topic}</span>
              <span style={{ color: C.sub }} className="text-xs font-semibold shrink-0">
                {b.accuracyPercent}% · {b.correct}/{b.answered}
              </span>
            </div>
            <div style={{ background: C.line }} className="w-full h-2 rounded-full overflow-hidden">
              <div style={{ width: `${b.accuracyPercent}%`, background: C.green700 }} className="h-full rounded-full" />
            </div>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between mb-3">
        <h3 style={{ ...display, color: C.ink }} className="font-bold">Badges</h3>
        <span style={{ color: C.sub }} className="text-xs">{earnedCount} of {BADGES.length} earned</span>
      </div>
      <div className="grid grid-cols-3 gap-4">
        {badges.map((b) => {
          const Icon = b.icon;
          return (
            <div key={b.id} style={{ borderColor: C.line, opacity: b.earned ? 1 : 0.55 }} className="border rounded-xl p-4 bg-white relative">
              {!b.earned && (
                <div style={{ background: "#F1F0F3" }} className="absolute top-3 right-3 w-6 h-6 rounded-full flex items-center justify-center">
                  <Lock size={11} color="#9A93A8" />
                </div>
              )}
              <div style={{ background: b.earned ? C.mint : "#F1F0F3" }} className="w-10 h-10 rounded-xl flex items-center justify-center mb-3">
                <Icon size={18} color={b.earned ? C.green700 : "#9A93A8"} />
              </div>
              <p style={{ color: C.ink }} className="text-sm font-semibold mb-0.5">{b.title}</p>
              <p style={{ color: C.sub }} className="text-xs mb-2">{b.desc}</p>
              {b.earned
                ? <span style={{ color: C.success }} className="text-[11px] font-semibold">
                    {b.date ? `Earned ${b.date}` : "Currently active"}
                  </span>
                : <span style={{ color: C.sub }} className="text-[11px] font-semibold">Locked</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------- App ----------
export default function App() {
  const [auth, setAuth] = useState(null);
  // Distinct from signed-out: on load we may hold a token that still needs checking.
  // Showing sign-in during that check makes a valid session flicker to a login form on
  // every refresh.
  const [restoring, setRestoring] = useState(true);
  // Whether this person has anyone reporting to them. Comes from the org chart, not from
  // access_role: the tier says what you may DO, the reporting line says whose training
  // you are responsible for, and they are not the same question.
  const [team, setTeam] = useState(null);
  // Lifted here (not fetched inside Settings) so the floating pet's visibility can
  // react the instant someone toggles it, without needing Shell and Settings to
  // share state any other way.
  const [settings, setSettings] = useState(null);
  const [view, setView] = useState("dashboard");
  const [training, setTraining] = useState(null);
  const [module, setModule] = useState(null);
  const [assessment, setAssessment] = useState(null);
  const [quiz, setQuiz] = useState(null);
  const [result, setResult] = useState(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(null);

  const signIn = useCallback((principal) => {
    setAuth(principal);
    setView("dashboard");
    api.team().then(setTeam).catch(() => setTeam(null));
    api.getSettings().then(setSettings).catch(() => setSettings(null));
  }, []);

  // Restore a session from the stored token. api.currentUser() clears an expired token
  // and returns null, so a stale one signs you out cleanly instead of failing every call.
  useEffect(() => {
    let cancelled = false;
    api.currentUser()
      .then((principal) => { if (!cancelled && principal) signIn(principal); })
      .catch(() => { /* unreachable API means signed out, not broken */ })
      .finally(() => { if (!cancelled) setRestoring(false); });
    return () => { cancelled = true; };
  }, [signIn]);

  if (restoring) {
    return (
      <div className="min-h-screen flex items-center justify-center"
           style={{ ...font, background: C.paper }}>
        <Logo size={36} />
      </div>
    );
  }

  if (!auth) return <Login onLogin={signIn} />;

  const manages = Boolean(team?.manages);
  const goto = (v) => setView(v);
  const openTraining = (t) => {
    setTraining(t);
    setModule(null);
    setAssessment(null);
    setView("trainingDetail");
  };

  const prepareAssessment = (kind, selectedModule = null) => {
    setModule(selectedModule);
    setAssessment({ kind, module: selectedModule });
    setQuiz(null);
    setStartError(null);
    goto("quizPre");
  };

  const beginQuiz = async () => {
    setStarting(true);
    setStartError(null);
    try {
      const q = await api.startPathwayAssessment({
        training: training.title,
        kind: assessment.kind,
        moduleId: assessment.module?.moduleId,
      });
      setQuiz(q);
      goto("quizRunner");
    } catch (e) {
      setStartError(e);
    } finally {
      setStarting(false);
    }
  };

  // Routed on the VIEW, never on the role. The old version had
  // `else if (auth.role === "manager") content = <ManagerTeam />`, which swallowed every
  // other view — a manager could not open their own training however they navigated.
  let content;
  if (view === "profile") {
    content = <Profile principal={auth} />;
  } else if (view === "documents") {
    content = <DocumentsScreen team={team} principal={auth} onDone={() => goto("dashboard")} />;
  } else if (view === "team") {
    content = <ManagerTeam team={team} />;
  } else if (view === "dashboard") {
    content = <Dashboard name={auth.name || auth.email} team={team} onOpenPath={() => goto("path")} onOpenTraining={openTraining} onOpenCertificates={() => goto("certificates")} />;
  } else if (view === "path") {
    content = <MyCourses onBack={() => goto("dashboard")} onOpenTraining={openTraining} />;
  } else if (view === "trainingDetail") {
    content = <TrainingDetail
      training={training}
      onBack={() => goto("dashboard")}
      onStartDiagnostic={() => prepareAssessment("diagnostic")}
      onOpenModule={(selectedModule) => { setModule(selectedModule); goto("lesson"); }}
      onStartFinal={() => prepareAssessment("final")}
    />;
  } else if (view === "lesson") {
    content = <LessonScreen
      training={training}
      module={module}
      onBack={() => goto("trainingDetail")}
      onContinue={() => module.status === "passed"
        ? goto("trainingDetail") : prepareAssessment("module", module)}
    />;
  } else if (view === "quizPre") {
    content = <QuizPreScreen
      training={training}
      assessment={assessment}
      onStart={beginQuiz}
      onBack={() => goto(assessment.kind === "module" ? "lesson" : "trainingDetail")}
      starting={starting}
      error={startError}
    />;
  } else if (view === "quizRunner") {
    content = (
      <QuizRunner
        training={training}
        quiz={quiz}
        onBack={() => goto("trainingDetail")}
        onSubmit={(r) => { setResult(r); goto("quizResults"); }}
      />
    );
  } else if (view === "quizResults") {
    content = <QuizResults
      result={result}
      onRetake={() => goto(result.kind === "module" ? "lesson" : "quizPre")}
      onDone={() => goto("trainingDetail")}
    />;
  } else if (view === "certificates") {
    content = <Certificates />;
  } else if (view === "teammates") {
    content = <TeammatesGallery />;
  } else if (view === "settings") {
    content = <Settings settings={settings} onSettingsChange={setSettings} />;
  }

  const quizViews = ["trainingDetail", "lesson", "quizPre", "quizRunner", "quizResults"];

  return (
    <>
      <Shell
        name={auth.name || auth.email}
        department={auth.department}
        title={auth.title}
        manages={manages}
        active={quizViews.includes(view) ? "dashboard" : view}
        setActive={goto}
        petVisible={settings ? settings.petVisible : true}
        onLogout={async () => {
          await api.logout();
          setAuth(null);
          setTeam(null);
          setView("dashboard");
        }}
      >
        {content}
      </Shell>
      {/* Manages its own visibility -- fetches /skills/options and renders nothing if
          already prompted or nothing to offer, so mounting it unconditionally here is
          correct rather than something that needs its own loading gate in App. */}
      <SkillInterestPopup />
    </>
  );
}
