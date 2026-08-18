import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  LogOut, BookOpen, Award, Users, CheckCircle2, Circle, Lock,
  ChevronRight, X, AlertCircle, Clock, ArrowLeft, User, Star,
  Trophy, Flame, Target, Mail, Briefcase, Share2, Download, Copy,
  Loader2, RefreshCw, Upload, FileText,
} from "lucide-react";
import * as api from "./api";
import { Logo } from "./logo.jsx";

/**
 * WHAT IS REAL AND WHAT IS NOT.
 *
 * Wired to the backend — these reflect the actual question bank and this learner's
 * actual answers:
 *   trainings, modules, lesson text, quiz questions, grading, scores,
 *   certificates, Q score, mastery breakdown
 *
 * Still mock — no backend exists for them yet, and they are marked in the UI rather
 * than left to look real:
 *   badges, the companion pet, focus timer, teammates, the manager's team view
 *
 * The mock parts are kept because they are the product's design direction. They are
 * not kept quiet: pretending a number is measured when it is invented is how a demo
 * turns into a wrong decision.
 */

// ---------- design tokens ----------
const C = {
  ink: "#1E1B2E",
  sub: "#6B6480",
  violet900: "#2E1152",
  violet700: "#6423C9",
  violet600: "#7A35E0",
  violet500: "#9459EE",
  violet300: "#C9AEF5",
  lavender: "#F1EBFB",
  paper: "#FBFAFE",
  line: "#E4DCF5",
  amber: "#C9971D",
  amberBg: "#FBF3DF",
  success: "#1F9D55",
  successBg: "#E7F7EE",
  danger: "#D8443C",
  dangerBg: "#FCEBEA",
};

const font = { fontFamily: "'Inter', system-ui, sans-serif" };
const display = { fontFamily: "'Sora', system-ui, sans-serif" };

// ---------- static (design-only) data ----------
const FOCUS_PRIORITIES = [
  { id: "urgent", label: "Urgent", color: "#D8443C", bg: "#FCEBEA" },
  { id: "deep", label: "Deep Work", color: "#6423C9", bg: "#F1EBFB" },
  { id: "quick", label: "Quick Task", color: "#C9971D", bg: "#FBF3DF" },
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

const PET_STAGES = [
  { level: 1, name: "Qibble", min: 0, size: 56 },
  { level: 2, name: "Qip", min: 1, size: 74 },
  { level: 3, name: "Quill", min: 3, size: 92 },
  { level: 4, name: "Quorra", min: 5, size: 110 },
  { level: 5, name: "Quasar", min: 8, size: 128 },
  { level: 6, name: "Qrown", min: 12, size: 146 },
];

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
    "not-started": { bg: C.lavender, fg: C.violet700, label: "Not started" },
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
  const id = `grad-${size}-${pct}`;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <defs>
        <linearGradient id={id} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor={C.violet500} />
          <stop offset="100%" stopColor={C.violet900} />
        </linearGradient>
      </defs>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={C.line} strokeWidth={stroke} />
      <circle
        cx={size / 2} cy={size / 2} r={r} fill="none"
        stroke={`url(#${id})`} strokeWidth={stroke} strokeLinecap="round"
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
    primary: { background: C.violet700, color: "#fff" },
    ghost: { background: "transparent", color: C.violet700, border: `1px solid ${C.line}` },
    subtle: { background: C.lavender, color: C.violet700 },
  };
  const [hover, setHover] = useState(false);
  const hoverBg = variant === "primary" ? C.violet900 : variant === "subtle" ? "#E4D7F7" : C.lavender;
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
    <div className="min-h-screen flex items-center justify-center" style={{ ...font, background: `linear-gradient(160deg, ${C.violet900} 0%, ${C.violet700} 45%, ${C.violet500} 100%)` }}>
      <div className="w-full max-w-md mx-4 bg-white rounded-2xl shadow-2xl overflow-hidden">
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
function Shell({ name, department, title, manages, active, setActive, onLogout, children }) {
  // One nav for everyone. Managing people ADDS a tab; it does not replace the rest.
  //
  // This used to be two lists, with managerNav substituted for employeeNav — so a
  // manager got Team, Documents and Profile and had no way to reach their own training
  // at all. A manager is also an employee with training of their own, and the old split
  // made that unreachable.
  const nav = [
    { id: "dashboard", label: "Dashboard", icon: BookOpen },
    { id: "path", label: "My Training", icon: CheckCircle2 },
    ...(manages ? [{ id: "team", label: "My Team", icon: Users }] : []),
    { id: "documents", label: "Documents", icon: FileText },
    { id: "certificates", label: "Certificates", icon: Award },
    { id: "teammates", label: "Teammates", icon: Users },
    { id: "profile", label: "Profile", icon: User },
  ];

  return (
    <div style={{ ...font, background: C.paper, minHeight: "100vh" }} className="flex">
      <aside style={{ borderColor: C.line }} className="w-60 border-r flex flex-col shrink-0">
        <div className="px-5 py-5 flex items-center"><Logo size={26} /></div>
        <nav className="flex-1 px-3 py-4 space-y-1">
          {nav.map((n) => {
            const Icon = n.icon;
            const isActive = active === n.id;
            return (
              <button
                key={n.id}
                onClick={() => setActive(n.id)}
                style={{ background: isActive ? C.violet700 : "transparent", color: isActive ? "#fff" : C.sub }}
                className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-semibold transition-colors"
              >
                <Icon size={16} /> {n.label}
              </button>
            );
          })}
        </nav>
        <div style={{ borderColor: C.line }} className="border-t px-5 py-4">
          <div className="flex items-center gap-2 mb-3">
            <div style={{ background: C.lavender, color: C.violet700 }} className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold">
              {name.split(" ").map((p) => p[0]).join("")}
            </div>
            <div>
              <div style={{ color: C.ink }} className="text-sm font-semibold leading-tight">{name}</div>
              <div style={{ color: C.sub }} className="text-xs">{department || "—"}</div>
              {title && <div style={{ color: C.sub }} className="text-[11px] opacity-80">{title}</div>}
            </div>
          </div>
          <button onClick={onLogout} style={{ color: C.sub }} className="flex items-center gap-2 text-xs font-semibold hover:opacity-80">
            <LogOut size={13} /> Sign out
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">{children}</main>
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
function Dashboard({ name, onOpenPath, onOpenTraining }) {
  const { data, loading, error, reload } = useAsync(() => api.trainings(), []);
  const trainings = data?.trainings || [];
  // Resume the one in progress; failing that, whatever hasn't been started.
  const focus = trainings.find((t) => t.status === "in-progress")
    || trainings.find((t) => t.status === "not-started")
    || trainings[0];

  return (
    <div className="p-8 max-w-4xl">
      <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">Welcome back, {name.split(" ")[0]}</h1>
      <p style={{ color: C.sub }} className="text-sm mb-8">Here's where your compliance training stands.</p>

      {loading && <Loading label="Loading your trainings…" />}
      {error && <ErrorBox error={error} onRetry={reload} />}

      {focus && (
        <div style={{ background: `linear-gradient(120deg, ${C.violet900}, ${C.violet700})` }}
          className="rounded-2xl p-6 flex items-center justify-between gap-6 mb-8 text-white">
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-wide opacity-80 mb-1 font-semibold">
              {focus.status === "not-started" ? "Start here" : "Continue where you left off"}
            </p>
            <h2 style={display} className="text-lg font-bold mb-1">{focus.title}</h2>
            <p className="text-sm opacity-90 mb-4">
              {focus.modules.length} modules · {focus.questionCount} questions
              {focus.answered > 0 ? ` · ${focus.answered} answered` : ""}
            </p>
            <button onClick={() => onOpenTraining(focus)} style={{ color: C.violet700 }}
              className="bg-white px-4 py-2 rounded-xl text-sm font-semibold hover:opacity-90">
              {focus.status === "not-started" ? "Start training" : "Continue training"}
            </button>
          </div>
          <MasteryRing value={focus.mastery} size={84} />
        </div>
      )}

      {!loading && !error && trainings.length > 0 && (
        <>
          <div className="flex items-center justify-between mb-3">
            <h3 style={{ ...display, color: C.ink }} className="font-bold">Your trainings</h3>
            <button onClick={onOpenPath} style={{ color: C.violet700 }} className="text-sm font-semibold flex items-center gap-1">
              View full path <ChevronRight size={14} />
            </button>
          </div>
          <div className="space-y-2">
            {trainings.map((t) => (
              <button key={t.id} onClick={() => onOpenTraining(t)} style={{ borderColor: C.line }}
                className="w-full text-left border rounded-xl p-4 flex items-center justify-between gap-3 bg-white hover:shadow-sm transition-shadow">
                <div className="flex items-center gap-3 min-w-0">
                  {t.status === "completed"
                    ? <CheckCircle2 size={16} color={C.success} className="shrink-0" />
                    : <Circle size={16} color={C.violet500} className="shrink-0" />}
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
        </>
      )}

      {!loading && !error && trainings.length === 0 && (
        <div style={{ borderColor: C.line }} className="border rounded-xl p-6 bg-white text-center">
          <p style={{ color: C.ink }} className="text-sm font-semibold mb-1">No trainings yet</p>
          <p style={{ color: C.sub }} className="text-xs">
            Nothing has been assigned to your role yet. Check back soon, or ask your manager if you think this is unexpected.
          </p>
        </div>
      )}

      <div className="mt-8"><FocusSession /></div>
    </div>
  );
}

// ---------- Learning path ----------
function LearningPath({ onBack, onOpenTraining }) {
  const { data, loading, error, reload } = useAsync(() => api.trainings(), []);
  const trainings = data?.trainings || [];

  return (
    <div className="p-8 max-w-3xl">
      <button onClick={onBack} style={{ color: C.sub }} className="flex items-center gap-1 text-sm font-semibold mb-4">
        <ArrowLeft size={14} /> Back to dashboard
      </button>
      <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-6">My training path</h1>

      {loading && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}

      <div className="relative pl-8">
        {trainings.length > 0 && <div style={{ background: C.line }} className="absolute left-[15px] top-2 bottom-2 w-0.5" />}
        {trainings.map((t) => (
          <div key={t.id} className="relative mb-6 last:mb-0">
            <div style={{ background: t.status === "completed" ? C.success : C.violet700 }}
              className="absolute -left-8 top-1 w-4 h-4 rounded-full border-2 border-white ring-2" />
            <button onClick={() => onOpenTraining(t)} style={{ borderColor: C.line }}
              className="w-full text-left border rounded-xl p-4 bg-white flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p style={{ color: C.ink }} className="text-sm font-semibold truncate">{t.title}</p>
                <p style={{ color: C.sub }} className="text-xs mt-0.5">{t.modules.length} modules · {t.questionCount} questions</p>
              </div>
              <StatusPill status={t.status} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------- Training detail ----------
function TrainingDetail({ training, onBack, onStartLesson }) {
  return (
    <div className="p-8 max-w-3xl">
      <button onClick={onBack} style={{ color: C.sub }} className="flex items-center gap-1 text-sm font-semibold mb-4">
        <ArrowLeft size={14} /> Back
      </button>
      <div className="flex items-start justify-between gap-6 mb-6">
        <div className="min-w-0">
          <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">{training.title}</h1>
          <p style={{ color: C.sub }} className="text-sm">
            {training.modules.length} modules · {training.questionCount} questions in the bank
          </p>
        </div>
        <MasteryRing value={training.mastery} />
      </div>

      <h3 style={{ ...display, color: C.ink }} className="font-bold mb-3">Modules</h3>
      <div className="space-y-2 mb-8">
        {training.modules.map((m) => (
          <div key={m} style={{ borderColor: C.line }} className="border rounded-xl p-4 flex items-center gap-3 bg-white">
            <Circle size={16} color={C.violet500} className="shrink-0" />
            <span style={{ color: C.ink }} className="text-sm font-medium flex-1 min-w-0">{m}</span>
          </div>
        ))}
      </div>
      <Button onClick={onStartLesson}>Start lesson</Button>
    </div>
  );
}

// ---------- Lesson ----------
function LessonScreen({ training, onContinue, onBack }) {
  const { data, loading, error, reload } = useAsync(() => api.lesson(training.title), [training.title]);
  const [scrolledToEnd, setScrolledToEnd] = useState(false);
  const boxRef = useRef(null);

  // Short lessons may not overflow at all, in which case there is nothing to scroll
  // and the gate would never open. Unlock immediately when everything already fits.
  useEffect(() => {
    const el = boxRef.current;
    if (el && el.scrollHeight <= el.clientHeight + 8) setScrolledToEnd(true);
  }, [data]);

  const handleScroll = (e) => {
    const el = e.target;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 40) setScrolledToEnd(true);
  };

  return (
    <div className="p-8 max-w-2xl">
      <button onClick={onBack} style={{ color: C.sub }} className="flex items-center gap-1 text-sm font-semibold mb-4">
        <ArrowLeft size={14} /> Back
      </button>

      <div className="flex items-center justify-between gap-3 mb-1">
        <h1 style={{ ...display, color: C.ink }} className="text-xl font-bold min-w-0">{data?.title || training.title}</h1>
        {data && (
          <span style={{ color: C.sub, background: C.lavender }} className="text-xs font-semibold px-2.5 py-1 rounded-full flex items-center gap-1 shrink-0">
            <Clock size={12} /> {data.readTime}
          </span>
        )}
      </div>
      <p style={{ color: C.sub }} className="text-sm mb-4">
        Read this before you take the quiz — the questions are generated from these passages.
      </p>

      {loading && <Loading label="Loading lesson…" />}
      {error && <ErrorBox error={error} onRetry={reload} />}

      {data && (
        <>
          <div ref={boxRef} onScroll={handleScroll} style={{ borderColor: C.line, maxHeight: 420 }}
            className="border rounded-xl bg-white p-6 overflow-y-auto mb-4 space-y-5">
            {data.sections.map((s, i) => (
              <div key={i}>
                <h3 style={{ ...display, color: C.ink }} className="text-sm font-bold mb-1.5">{s.heading}</h3>
                <p style={{ color: C.sub }} className="text-sm leading-relaxed">{s.body}</p>
                {s.sourceUrl && (
                  <a href={s.sourceUrl} target="_blank" rel="noopener noreferrer"
                    style={{ color: C.violet700 }} className="text-xs font-semibold mt-1 inline-block">
                    Source ↗
                  </a>
                )}
              </div>
            ))}
            <p style={{ color: C.sub }} className="text-xs text-center pt-2 pb-1 italic">— end of reading —</p>
          </div>

          {!scrolledToEnd && (
            <p style={{ color: C.sub }} className="text-xs mb-3 flex items-center gap-1.5">
              <ChevronRight size={12} className="rotate-90" /> Scroll to the end to unlock the quiz
            </p>
          )}
          <Button onClick={onContinue} disabled={!scrolledToEnd}>I've read this — continue</Button>
        </>
      )}
    </div>
  );
}

// ---------- Quiz ----------
function QuizPreScreen({ training, onStart, onBack, starting, error }) {
  return (
    <div className="p-8 max-w-xl">
      <button onClick={onBack} style={{ color: C.sub }} className="flex items-center gap-1 text-sm font-semibold mb-4">
        <ArrowLeft size={14} /> Back
      </button>
      <div style={{ borderColor: C.line }} className="border rounded-2xl p-8 bg-white text-center">
        <div style={{ background: C.lavender }} className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4">
          <Clock size={22} color={C.violet700} />
        </div>
        <h1 style={{ ...display, color: C.ink }} className="text-xl font-bold mb-2">Ready for the quiz?</h1>
        <p style={{ color: C.sub }} className="text-sm mb-2">8 questions · No time limit · You need 80% to pass</p>
        <p style={{ color: C.sub }} className="text-xs mb-6">
          Questions are picked for you — weak areas first, then whatever there's least evidence on.
        </p>
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
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState({});     // questionId -> answer payload
  const [verdicts, setVerdicts] = useState({});   // questionId -> server verdict
  const [pending, setPending] = useState(false);
  const [draft, setDraft] = useState(undefined);  // selection before it is committed
  const [error, setError] = useState(null);

  const q = quiz.questions[index];
  const isMulti = false;                     // backend emits one correct option per question
  const isFree = q.type === "FillInBlank";
  const isLast = index === quiz.questions.length - 1;
  const verdict = verdicts[q.questionId];
  const checked = Boolean(verdict);

  const commit = async (payload) => {
    setPending(true);
    setError(null);
    try {
      const v = await api.gradeAnswer({ attemptId: quiz.attemptId, questionId: q.questionId, ...payload });
      setAnswers((a) => ({ ...a, [q.questionId]: payload }));
      setVerdicts((s) => ({ ...s, [q.questionId]: v }));
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  };

  const selectOption = (optionId) => {
    if (checked || pending) return;
    setDraft(optionId);
    commit({ selectedOptionIds: [optionId] });
  };

  const next = async () => {
    if (!isLast) {
      setIndex((i) => i + 1);
      setDraft(undefined);
      return;
    }
    const payload = quiz.questions.map((qq) => ({ questionId: qq.questionId, ...(answers[qq.questionId] || {}) }));
    try {
      const result = await api.submitQuiz({ attemptId: quiz.attemptId, answers: payload });
      onSubmit(result, verdicts);
    } catch (e) {
      setError(e);
    }
  };

  const optionStyle = (optionId) => {
    const selected = draft === optionId || (answers[q.questionId]?.selectedOptionIds || []).includes(optionId);
    if (!checked) {
      return {
        state: "default",
        selected,
        style: { borderColor: selected ? C.violet700 : C.line, background: selected ? C.lavender : "#fff", color: C.ink },
      };
    }
    const isRight = (verdict.correctOptionIds || []).includes(optionId);
    if (isRight) return { state: "correct", selected, style: { borderColor: C.success, background: C.successBg, color: C.ink } };
    if (selected) return { state: "incorrect", selected, style: { borderColor: C.danger, background: C.dangerBg, color: C.ink } };
    return { state: "muted", selected, style: { borderColor: C.line, background: "#fff", color: C.sub, opacity: 0.7 } };
  };

  return (
    <div className="p-8 max-w-2xl">
      <button onClick={onBack} style={{ color: C.sub }} className="flex items-center gap-1 text-sm font-semibold mb-4">
        <ArrowLeft size={14} /> Exit quiz
      </button>
      <div className="flex items-center justify-between gap-3 mb-2">
        <h1 style={{ ...display, color: C.ink }} className="text-xl font-bold min-w-0 truncate">{training.title} — Quiz</h1>
        <span style={{ color: C.sub }} className="text-xs font-semibold shrink-0">
          Question {index + 1} of {quiz.questions.length}
        </span>
      </div>
      <div style={{ background: C.line }} className="w-full h-1.5 rounded-full overflow-hidden mb-6">
        <div style={{ width: `${((index + (checked ? 1 : 0)) / quiz.questions.length) * 100}%`, background: C.violet700 }}
          className="h-full rounded-full transition-all" />
      </div>

      <div style={{ borderColor: C.line }} className="border rounded-xl p-5 bg-white">
        <div className="flex items-start justify-between gap-3 mb-3">
          <p style={{ color: C.ink }} className="text-sm font-semibold">{q.prompt}</p>
          <ProvenanceBadge provenance={verdict?.provenance} sourceTitle={verdict?.sourceTitle} />
        </div>
        <div className="flex items-center gap-2 mb-3">
          <span style={{ background: C.lavender, color: C.violet700 }} className="text-[11px] font-semibold px-2 py-0.5 rounded-full">{q.topic}</span>
          <span style={{ borderColor: C.line, color: C.sub }} className="text-[11px] font-semibold px-2 py-0.5 rounded-full border">{q.difficulty}</span>
        </div>

        {isFree ? (
          <div>
            <input
              value={draft ?? ""}
              onChange={(e) => setDraft(e.target.value)}
              disabled={checked || pending}
              placeholder="Type your answer"
              style={{ borderColor: C.line, color: C.ink }}
              className="w-full border rounded-lg px-3 py-2.5 text-sm mb-3"
            />
            {!checked && (
              <Button onClick={() => commit({ textAnswer: draft || "" })} disabled={!draft || pending}>
                {pending ? "Checking…" : "Check answer"}
              </Button>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            {q.options.map((opt) => {
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
                    borderColor: state === "correct" ? C.success : state === "incorrect" ? C.danger : selected ? C.violet700 : "#C9C2DB",
                    background: state === "correct" ? C.success : state === "incorrect" ? C.danger : selected ? C.violet700 : "transparent",
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
          </div>
        )}

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
              {isFree && !verdict.correct && verdict.acceptedAnswers?.length > 0 && (
                <p style={{ color: C.ink }} className="text-sm leading-snug opacity-90 mt-1">
                  Accepted: {verdict.acceptedAnswers.join(", ")}
                </p>
              )}
              {verdict.sourceQuote && (
                <p style={{ color: C.sub }} className="text-xs italic mt-1.5">“{verdict.sourceQuote}”</p>
              )}
            </div>
          </div>
        )}
      </div>

      {checked && (
        <div className="mt-6">
          <Button onClick={next}>{isLast ? "See results" : "Next question"}</Button>
        </div>
      )}
    </div>
  );
}

function QuizResults({ result, onRetake, onDone }) {
  const pass = result.passed;
  const right = result.results.filter((r) => r.correct).length;
  return (
    <div className="p-8 max-w-2xl">
      <div style={{ borderColor: C.line }} className="border rounded-2xl p-8 bg-white text-center mb-6">
        <div className="flex justify-center mb-4">
          <MasteryRing value={result.scorePercent} size={100} stroke={9} />
        </div>
        {pass ? (
          <>
            <h1 style={{ ...display, color: C.ink }} className="text-xl font-bold mb-2">Nice work — you passed</h1>
            <p style={{ color: C.sub }} className="text-sm mb-6">
              {right} of {result.results.length} correct · pass mark {result.passingScore}%
            </p>
            <div style={{ background: C.successBg, color: C.success }} className="rounded-xl px-4 py-3 text-sm font-semibold mb-6 flex items-center justify-center gap-2">
              <Award size={16} /> Certificate earned
            </div>
            <Button onClick={onDone}>Back to dashboard</Button>
          </>
        ) : (
          <>
            <h1 style={{ ...display, color: C.ink }} className="text-xl font-bold mb-2">Not quite — you can retake it</h1>
            <p style={{ color: C.sub }} className="text-sm mb-6">
              {right} of {result.results.length} correct · you need {result.passingScore}% to pass
            </p>
            <div className="flex gap-2 justify-center">
              <Button onClick={onRetake}>Retake quiz</Button>
              <Button variant="ghost" onClick={onDone}>Back to dashboard</Button>
            </div>
          </>
        )}
      </div>

      {result.weakTopics?.length > 0 && (
        <div style={{ background: C.amberBg, borderColor: C.amber }} className="border rounded-xl p-4 mb-6">
          <p style={{ color: C.amber }} className="text-sm font-semibold mb-1">What the next quiz will focus on</p>
          <p style={{ color: C.ink }} className="text-sm opacity-90">
            {result.weakTopics.map((w) => `${w.topic} (${w.accuracyPercent}%)`).join(", ")}
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
              <span style={{ background: C.lavender, color: C.violet700 }} className="text-[11px] font-semibold px-2 py-0.5 rounded-full">{r.topic}</span>
              <span className="flex-1" />
              <ProvenanceBadge provenance={r.provenance} sourceTitle={r.sourceTitle} />
            </div>
            <p style={{ color: C.ink }} className="text-sm mb-2">{r.prompt}</p>
            {r.explanation && <p style={{ color: C.sub }} className="text-sm leading-snug">{r.explanation}</p>}
            {r.sourceQuote && (
              <div style={{ borderLeft: `3px solid ${C.line}`, background: C.paper }} className="rounded-r-lg px-3 py-2 mt-2">
                <p style={{ color: C.sub }} className="text-xs">
                  {r.sourceTitle && <span className="font-semibold">{r.sourceTitle}: </span>}“{r.sourceQuote}”
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

  return (
    <div className="p-8 max-w-3xl">
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
            {/* No certificate artefact yet. Saying so beats a button that does nothing. */}
            {!c.certificateUrl && (
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
              <span key={r.role_code} style={{ background: C.lavender, color: C.violet700 }}
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

/** The manager reviews the AI's proposed mapping before anything is generated. */
function MappingReview({ analysis, roles, onConfirmed, onCancel }) {
  // The AI proposes against every role the company has; this manager may only publish to
  // the roles their reports hold (analysis.permittedRoles, from the server). Offering the
  // full list would let them pick something the confirm call then refuses with a 403 —
  // and, worse, imply they had a say over another team's training.
  const permitted = new Set(analysis.permittedRoles || []);
  const selectable = roles.filter((r) => permitted.has(r.role_code));
  const canPublishCompanyWide = permitted.has("ALL");

  const knownCodes = new Set(selectable.map((r) => r.role_code));
  const [assignments, setAssignments] = useState(() => {
    const init = {};
    for (const [topic, role] of Object.entries(analysis.proposedRoles || {})) {
      // Proposals naming a role the company hasn't defined start unresolved: the
      // manager must place them before confirm unlocks.
      init[topic] = knownCodes.has(String(role).toUpperCase()) || role === "ALL"
        ? String(role).toUpperCase() : "";
    }
    return init;
  });
  const [newRoles, setNewRoles] = useState([]); // roles the manager adds inline
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const allCodes = [...selectable.map((r) => r.role_code), ...newRoles.map((r) => r.roleCode)];
  const unresolved = Object.entries(assignments).filter(([, v]) => !v);
  const nobodyToPublishTo = selectable.length === 0 && !canPublishCompanyWide;

  const addInlineRole = (name) => {
    const roleCode = name.toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_|_$/g, "");
    if (!roleCode || allCodes.includes(roleCode)) return roleCode;
    setNewRoles((n) => [...n, { roleCode, title: name, description: "" }]);
    return roleCode;
  };

  const confirm = async () => {
    setBusy(true); setErr(null);
    try {
      const result = await api.confirmDocument({
        title: analysis.title, assignments, newRoles, supersede: "",
      });
      onConfirmed(result);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };

  return (
    <div style={{ borderColor: C.violet300 }} className="border-2 rounded-xl p-5 bg-white mb-5">
      <h3 style={{ ...display, color: C.ink }} className="font-bold mb-1">Confirm who trains on what</h3>
      <p style={{ color: C.sub }} className="text-xs mb-1">
        The AI read “{analysis.title}” and proposed this. Nothing is generated until you confirm.
      </p>
      {analysis.summary && <p style={{ color: C.sub }} className="text-xs italic mb-4">“{analysis.summary}”</p>}

      {nobodyToPublishTo && (
        <div style={{ background: C.amberBg, color: C.amber }} className="rounded-lg px-3 py-2.5 text-xs mb-4">
          <strong>You have no roles to publish to.</strong> Training goes to the roles held
          by people who report to you, and nobody does yet. The document is saved — nothing
          is generated from it until it can be assigned.
        </div>
      )}

      {analysis.thinTopics?.length > 0 && (
        <div style={{ background: C.amberBg, color: C.amber }} className="rounded-lg px-3 py-2.5 text-xs mb-4">
          <strong>Too thin to teach from:</strong> {analysis.thinTopics.join(", ")}.
          These sections don't have enough material for real questions — consider
          uploading a fuller document for them. (Nothing is pulled from the internet.)
        </div>
      )}

      <div className="space-y-2 mb-4">
        {Object.entries(analysis.proposedRoles || {}).map(([topic, proposed]) => {
          const isUnknown = !knownCodes.has(String(proposed).toUpperCase()) && proposed !== "ALL";
          return (
            <div key={topic} className="flex items-center gap-3 flex-wrap">
              <span style={{ color: C.ink }} className="text-sm font-medium flex-1 min-w-[200px]">{topic}</span>
              {isUnknown && !assignments[topic] && (
                <span style={{ background: C.dangerBg, color: C.danger }} className="text-[11px] font-semibold px-2 py-0.5 rounded-full">
                  document says “{proposed}” — not a company role
                </span>
              )}
              <select
                value={assignments[topic] || ""}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === "__new__") {
                    const name = window.prompt("New role name:", String(proposed));
                    if (name) {
                      const code = addInlineRole(name);
                      setAssignments((a) => ({ ...a, [topic]: code }));
                    }
                    return;
                  }
                  setAssignments((a) => ({ ...a, [topic]: v }));
                }}
                style={{ borderColor: assignments[topic] ? C.line : C.danger, color: C.ink }}
                className="border rounded-lg px-2.5 py-1.5 text-xs"
              >
                <option value="">— choose role —</option>
                {canPublishCompanyWide && <option value="ALL">Everyone (company-wide)</option>}
                {selectable.map((r) => <option key={r.role_code} value={r.role_code}>{r.title}</option>)}
                {newRoles.map((r) => <option key={r.roleCode} value={r.roleCode}>{r.title} (new)</option>)}
                <option value="__new__">+ Add as new role…</option>
              </select>
            </div>
          );
        })}
      </div>

      {newRoles.length > 0 && (
        <p style={{ color: C.violet700 }} className="text-xs font-semibold mb-3">
          Will be added to the company list: {newRoles.map((r) => r.title).join(", ")}
        </p>
      )}
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

function DocumentsScreen({ team, onDone }) {
  const { data, loading, error, reload } = useAsync(() => api.documents(), []);
  const rolesQ = useAsync(() => api.roles(), []);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [analysis, setAnalysis] = useState(null);   // awaiting manager confirmation
  const [job, setJob] = useState(null);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef(null);
  const pollRef = useRef(null);

  const generator = data?.generator || "mock";
  const billed = generator !== "mock";
  const roles = rolesQ.data?.roles || [];

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

  const pct = job && job.total ? Math.round((job.done / job.total) * 100) : 0;

  return (
    <div className="p-8 max-w-3xl">
      <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">Documents</h1>
      <p style={{ color: C.sub }} className="text-sm mb-6">
        Upload a training document. The AI maps each section to the role it trains,
        you confirm, and employees in those roles owe the module — renewed yearly.
      </p>

      <RoleManager roles={roles} onChanged={rolesQ.reload} />

      <div style={{ background: billed ? C.amberBg : C.lavender, borderColor: billed ? C.amber : C.line }}
        className="border rounded-xl px-4 py-3 mb-5 flex items-start gap-2.5">
        <AlertCircle size={16} color={billed ? C.amber : C.violet700} className="shrink-0 mt-0.5" />
        <div>
          <p style={{ color: billed ? C.amber : C.violet700 }} className="text-sm font-semibold">
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
        style={{ borderColor: dragging ? C.violet700 : C.line, background: dragging ? C.lavender : "#fff" }}
        className="border-2 border-dashed rounded-2xl p-10 text-center cursor-pointer transition-colors mb-5"
      >
        <input ref={fileRef} type="file" accept=".pdf,.txt,.md" className="hidden"
          onChange={(e) => handleFiles(e.target.files)} />
        <div style={{ background: C.lavender }} className="w-12 h-12 rounded-2xl flex items-center justify-center mx-auto mb-3">
          {uploading ? <Loader2 size={20} color={C.violet700} className="animate-spin" /> : <Upload size={20} color={C.violet700} />}
        </div>
        <p style={{ color: C.ink }} className="text-sm font-semibold mb-1">
          {uploading ? "Reading and mapping roles… (~10-30s)" : "Drop a PDF here, or click to choose"}
        </p>
        <p style={{ color: C.sub }} className="text-xs">PDF, TXT or MD · up to 25 MB</p>
      </div>

      {uploadError && <ErrorBox error={uploadError} />}

      {analysis && (
        <MappingReview
          analysis={analysis}
          roles={roles}
          onCancel={() => setAnalysis(null)}
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
              {job.state === "running" ? "Writing questions…" : job.state === "error" ? "Generation failed" : "Questions ready"}
            </p>
            <span style={{ color: C.sub }} className="text-xs font-semibold">
              {job.total ? `${job.done}/${job.total} sections` : ""}
            </span>
          </div>
          <div style={{ background: C.line }} className="w-full h-2 rounded-full overflow-hidden mb-2">
            <div style={{
              width: `${job.state === "done" ? 100 : pct}%`,
              background: job.state === "error" ? C.danger : `linear-gradient(90deg, ${C.violet500}, ${C.violet700})`,
            }} className="h-full rounded-full transition-all" />
          </div>
          <p style={{ color: job.state === "error" ? C.danger : C.sub }} className="text-xs">{job.message}</p>
          {job.state === "done" && (
            <div className="mt-3"><Button onClick={onDone}>Done</Button></div>
          )}
        </div>
      )}

      <div className="flex items-center justify-between mb-3">
        <h3 style={{ ...display, color: C.ink }} className="font-bold">Documents in the bank</h3>
        <button onClick={reload} style={{ color: C.violet700 }} className="text-xs font-semibold flex items-center gap-1">
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {loading && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}

      <div className="space-y-2">
        {(data?.documents || []).map((d) => (
          <div key={d.title} style={{ borderColor: C.line }} className="border rounded-xl p-4 bg-white flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <div style={{ background: d.ready ? C.successBg : C.amberBg }} className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0">
                <FileText size={16} color={d.ready ? C.success : C.amber} />
              </div>
              <div className="min-w-0">
                <p style={{ color: C.ink }} className="text-sm font-semibold truncate">{d.title}</p>
                <p style={{ color: C.sub }} className="text-xs">
                  {d.chunks} section{d.chunks === 1 ? "" : "s"} · {d.questions} question{d.questions === 1 ? "" : "s"}
                </p>
              </div>
            </div>
            <StatusPill status={d.ready ? "completed" : "in-progress"} />
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------- Manager team (design-only) ----------
function ManagerTeam({ team }) {
  const people = team?.people || [];
  const targets = team?.uploadTargets || [];
  const direct = people.filter((p) => p.direct);
  const indirect = people.filter((p) => !p.direct);

  if (!people.length) {
    return (
      <div className="p-8 max-w-4xl">
        <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">My team</h1>
        <p style={{ color: C.sub }} className="text-sm">Nobody reports to you yet.</p>
      </div>
    );
  }

  const Row = ({ p }) => (
    <tr key={p.employeeId} style={{ borderTop: `1px solid ${C.line}` }}>
      <td className="px-4 py-3" style={{ color: C.ink }}>{p.name}</td>
      <td className="px-4 py-3" style={{ color: C.sub }}>{p.title || p.roleCode}</td>
      <td className="px-4 py-3">
        <span style={{ background: p.direct ? C.lavender : "#F1F0F3", color: p.direct ? C.violet700 : C.sub }}
              className="text-[11px] font-semibold px-2 py-0.5 rounded-full">
          {p.direct ? "direct report" : "reports to " + (people.find((x) => x.employeeId === p.managerId)?.name || "a manager")}
        </span>
      </td>
    </tr>
  );

  return (
    <div className="p-8 max-w-4xl">
      <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">My team</h1>
      <p style={{ color: C.sub }} className="text-sm mb-6">
        Everyone who reports to you, and the roles you can upload training for.
      </p>

      <div className="grid grid-cols-3 gap-4 mb-6">
        {[["Direct reports", direct.length],
          ["Further down", indirect.length],
          ["Roles you can upload for", targets.length]].map(([label, value]) => (
          <div key={label} style={{ borderColor: C.line }} className="border rounded-xl p-4 bg-white">
            <p style={{ color: C.sub }} className="text-xs font-semibold mb-1">{label}</p>
            <p style={{ ...display, color: C.ink }} className="text-2xl font-bold">{value}</p>
          </div>
        ))}
      </div>

      <div style={{ borderColor: C.line }} className="border rounded-xl bg-white overflow-hidden mb-6">
        <table className="w-full text-sm">
          <thead>
            <tr style={{ background: C.lavender, color: C.violet700 }} className="text-left text-xs uppercase tracking-wide">
              <th className="px-4 py-3 font-semibold">Name</th>
              <th className="px-4 py-3 font-semibold">Role</th>
              <th className="px-4 py-3 font-semibold">Reporting line</th>
            </tr>
          </thead>
          <tbody>
            {direct.map((p) => <Row key={p.employeeId} p={p} />)}
            {indirect.map((p) => <Row key={p.employeeId} p={p} />)}
          </tbody>
        </table>
      </div>

      {/* Progress and overdue counts are deliberately absent. There is no per-employee
          completion model behind this yet, and the previous version filled the gap with
          invented figures under a "sample data" label. An empty column is honest; a
          fabricated percentage next to a real name is not. */}
      <div style={{ borderColor: C.line }} className="border rounded-xl p-5 bg-white">
        <p style={{ color: C.ink }} className="text-sm font-semibold mb-1">Upload training for</p>
        <p style={{ color: C.sub }} className="text-xs mb-3">
          Roles held by your reports. The ones your direct reports hold are marked; you can
          also upload for roles further down if you need to.
        </p>
        <div className="flex flex-wrap gap-2">
          {targets.map((t) => (
            <span key={t.roleCode}
                  style={{ borderColor: t.direct ? C.violet700 : C.line,
                           color: t.direct ? C.violet700 : C.sub,
                           background: t.direct ? C.lavender : "#fff" }}
                  className="border rounded-full px-3 py-1 text-xs font-semibold">
              {t.title} · {t.headcount}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function getPetStageIdx(trainingsCompleted) {
  let idx = 0;
  for (let i = 0; i < PET_STAGES.length; i++) {
    if (trainingsCompleted >= PET_STAGES[i].min) idx = i;
  }
  return idx;
}

function PetCreature({ stageIdx, size }) {
  const s = size;
  const cx = s / 2, cy = s / 2 + s * 0.06;
  const bodyR = s * 0.34;
  const uid = `${size}-${stageIdx}`;
  return (
    <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`}>
      <defs>
        <linearGradient id={`pet-grad-${uid}`} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor={C.violet500} />
          <stop offset="100%" stopColor={C.violet700} />
        </linearGradient>
        <linearGradient id={`pet-crown-${uid}`} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor={C.violet300} />
          <stop offset="100%" stopColor={C.violet700} />
        </linearGradient>
      </defs>

      {stageIdx >= 4 && (
        <ellipse cx={cx} cy={cy} rx={bodyR * 1.55} ry={bodyR * 0.5} fill="none" stroke={C.violet300} strokeWidth="2" opacity="0.7" />
      )}

      {stageIdx >= 3 && (
        <>
          <ellipse cx={cx - bodyR * 1.05} cy={cy} rx={bodyR * 0.5} ry={bodyR * 0.72}
            fill={C.lavender} stroke={C.violet500} strokeWidth="1.5" transform={`rotate(-18 ${cx - bodyR} ${cy})`} />
          <ellipse cx={cx + bodyR * 1.05} cy={cy} rx={bodyR * 0.5} ry={bodyR * 0.72}
            fill={C.lavender} stroke={C.violet500} strokeWidth="1.5" transform={`rotate(18 ${cx + bodyR} ${cy})`} />
        </>
      )}

      {stageIdx >= 2 && [-1, 0, 1].map((i) => (
        <polygon key={i}
          points={`${cx + i * bodyR * 0.42},${cy - bodyR * 0.95} ${cx + i * bodyR * 0.42 - 5},${cy - bodyR * 0.55} ${cx + i * bodyR * 0.42 + 5},${cy - bodyR * 0.55}`}
          fill={C.violet500} />
      ))}

      {stageIdx >= 1 && (
        <>
          <ellipse cx={cx - bodyR * 0.72} cy={cy - bodyR * 0.85} rx={bodyR * 0.24} ry={bodyR * 0.34}
            fill={`url(#pet-grad-${uid})`} transform={`rotate(-25 ${cx - bodyR * 0.72} ${cy - bodyR * 0.85})`} />
          <ellipse cx={cx + bodyR * 0.72} cy={cy - bodyR * 0.85} rx={bodyR * 0.24} ry={bodyR * 0.34}
            fill={`url(#pet-grad-${uid})`} transform={`rotate(25 ${cx + bodyR * 0.72} ${cy - bodyR * 0.85})`} />
        </>
      )}

      <path d={`M ${cx + bodyR * 0.48} ${cy + bodyR * 0.58} L ${cx + bodyR * 1.02} ${cy + bodyR * 1.12}`}
        stroke={C.violet900} strokeWidth={Math.max(2.5, bodyR * 0.16)} strokeLinecap="round" />

      <circle cx={cx} cy={cy} r={bodyR} fill={`url(#pet-grad-${uid})`} />
      <ellipse cx={cx} cy={cy + bodyR * 0.32} rx={bodyR * 0.62} ry={bodyR * 0.42} fill={C.lavender} opacity="0.85" />

      <circle cx={cx - bodyR * 0.32} cy={cy - bodyR * 0.05} r={bodyR * 0.15} fill="#fff" />
      <circle cx={cx + bodyR * 0.32} cy={cy - bodyR * 0.05} r={bodyR * 0.15} fill="#fff" />
      <circle cx={cx - bodyR * 0.29} cy={cy - bodyR * 0.02} r={bodyR * 0.07} fill={C.ink} />
      <circle cx={cx + bodyR * 0.35} cy={cy - bodyR * 0.02} r={bodyR * 0.07} fill={C.ink} />

      <path d={`M ${cx - bodyR * 0.2} ${cy + bodyR * 0.28} Q ${cx} ${cy + bodyR * 0.42} ${cx + bodyR * 0.2} ${cy + bodyR * 0.28}`}
        fill="none" stroke={C.ink} strokeWidth={Math.max(1.5, bodyR * 0.05)} strokeLinecap="round" />

      <circle cx={cx - bodyR * 0.55} cy={cy + bodyR * 0.15} r={bodyR * 0.12} fill={C.violet300} opacity="0.6" />
      <circle cx={cx + bodyR * 0.55} cy={cy + bodyR * 0.15} r={bodyR * 0.12} fill={C.violet300} opacity="0.6" />

      {stageIdx >= 5 && (
        <polygon
          points={`${cx - bodyR * 0.5},${cy - bodyR * 0.95} ${cx - bodyR * 0.28},${cy - bodyR * 1.25} ${cx},${cy - bodyR * 0.98} ${cx + bodyR * 0.28},${cy - bodyR * 1.25} ${cx + bodyR * 0.5},${cy - bodyR * 0.95}`}
          fill={`url(#pet-crown-${uid})`} stroke={C.violet900} strokeWidth="1" strokeLinejoin="round" />
      )}
    </svg>
  );
}

function polarPoint(cx, cy, r, angleDeg) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function TeamHabitat({ members, highlightName }) {
  const [selected, setSelected] = useState(null);
  const width = 720, height = 420;
  const cx = width / 2, cy = height / 2 + 6;
  const radius = Math.min(width, height) / 2 - 92;
  const n = members.length;

  const nodes = members.map((m, i) => {
    const angle = -90 + (360 / n) * i;
    const pos = polarPoint(cx, cy, radius, angle);
    const stageIdx = getPetStageIdx(m.trainingsCompleted);
    const stage = PET_STAGES[stageIdx];
    const size = Math.min(88, Math.max(52, stage.size * 0.62));
    return { ...m, pos, stageIdx, stage, size };
  });

  const selectedNode = nodes.find((nd) => nd.name === selected);

  return (
    <div>
      <div style={{ borderColor: C.line, background: `linear-gradient(160deg, ${C.lavender}, #ffffff)` }} className="border rounded-2xl p-3 overflow-hidden">
        <svg viewBox={`0 0 ${width} ${height}`} width="100%" style={{ display: "block" }}>
          <style>{`
            @keyframes qhub-pulse { 0%, 100% { opacity: 0.55; } 50% { opacity: 0.95; } }
            .qhub-pulse { animation: qhub-pulse 3s ease-in-out infinite; }
          `}</style>

          {n >= 3 && nodes.map((nd, i) => {
            const nxt = nodes[(i + 1) % n];
            return <line key={`edge-${i}`} x1={nd.pos.x} y1={nd.pos.y} x2={nxt.pos.x} y2={nxt.pos.y}
              stroke={C.violet300} strokeWidth="1.5" strokeDasharray="3 5" opacity="0.5" />;
          })}

          {nodes.map((nd, i) => {
            const isSelected = selected === nd.name;
            return <line key={`spoke-${i}`} x1={cx} y1={cy} x2={nd.pos.x} y2={nd.pos.y}
              stroke={isSelected ? C.violet700 : C.violet300} strokeWidth={isSelected ? 2.5 : 1.5} opacity={isSelected ? 0.9 : 0.45} />;
          })}

          <circle cx={cx} cy={cy} r="28" fill={C.violet700} className="qhub-pulse" />
          <circle cx={cx} cy={cy} r="28" fill="none" stroke={C.violet300} strokeWidth="1.5" />
          <text x={cx} y={cy + 6} textAnchor="middle" fill="#fff" fontSize="17" fontWeight="700" fontFamily="Sora, sans-serif">Q</text>

          {nodes.map((nd) => {
            const isYou = nd.name === highlightName;
            const isSelected = selected === nd.name;
            return (
              <g key={nd.name} onClick={() => setSelected(isSelected ? null : nd.name)} style={{ cursor: "pointer" }}>
                <circle cx={nd.pos.x} cy={nd.pos.y} r={nd.size / 2 + 9}
                  fill="#fff" stroke={isYou ? C.violet700 : isSelected ? C.violet500 : C.line}
                  strokeWidth={isYou || isSelected ? 2.5 : 1.5} />
                <svg x={nd.pos.x - nd.size / 2} y={nd.pos.y - nd.size / 2} width={nd.size} height={nd.size} viewBox={`0 0 ${nd.size} ${nd.size}`}>
                  <PetCreature stageIdx={nd.stageIdx} size={nd.size} />
                </svg>
                <text x={nd.pos.x} y={nd.pos.y + nd.size / 2 + 19} textAnchor="middle" fill={C.ink} fontSize="11" fontWeight="700" fontFamily="Inter, sans-serif">
                  {nd.name.split(" ")[0]}{isYou ? " (you)" : ""}
                </text>
                <text x={nd.pos.x} y={nd.pos.y + nd.size / 2 + 32} textAnchor="middle" fill={C.violet700} fontSize="9" fontWeight="600" fontFamily="Inter, sans-serif">
                  Lv {nd.stage.level} · {nd.stage.name}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {selectedNode ? (
        <div style={{ borderColor: C.line }} className="border rounded-xl p-4 bg-white mt-4 flex items-center gap-4">
          <div className="rounded-xl flex items-center justify-center shrink-0" style={{ width: 60, height: 60, background: C.lavender }}>
            <PetCreature stageIdx={selectedNode.stageIdx} size={44} />
          </div>
          <div>
            <p style={{ color: C.ink }} className="text-sm font-semibold">
              {selectedNode.name}{selectedNode.name === highlightName ? " (you)" : ""}
            </p>
            <p style={{ color: C.sub }} className="text-xs">
              {selectedNode.role} · {selectedNode.stage.name}, Level {selectedNode.stage.level} · {selectedNode.trainingsCompleted} training{selectedNode.trainingsCompleted === 1 ? "" : "s"} completed
            </p>
          </div>
        </div>
      ) : (
        <p style={{ color: C.sub }} className="text-xs mt-3 text-center">Tap a character to see who they are.</p>
      )}
    </div>
  );
}

// Your reporting subtree, from GET /team — everyone below you in the Employees.manager_id
// chain, however deep, so a director sees their managers' reports too. The server decides
// who is in here; this only draws what it returns.
//
// It used to filter a hardcoded ROSTER by department and say "the backend has no org chart
// yet". That was true when it was written and is not any more, which made it worse than a
// blank screen: it showed invented colleagues to someone who reads them as real.
function TeammatesGallery({ team, name }) {
  // team is null while the fetch is in flight, and also if it failed — signIn() catches
  // to null. Distinguishing the two would need a third state; "not loaded" covers both
  // honestly and neither is worth a different screen.
  if (!team) {
    return (
      <div className="p-8 max-w-4xl">
        <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">Teammates</h1>
        <p style={{ color: C.sub }} className="text-sm">Loading your team…</p>
      </div>
    );
  }

  const people = team.people || [];

  // Nobody below you is a fact about the org chart, not an error — the endpoint returns
  // 200 with empty lists rather than 403. TeamHabitat divides by members.length to place
  // nodes on a circle, so it must not be handed an empty list either way.
  if (people.length === 0) {
    return (
      <div className="p-8 max-w-4xl">
        <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">Teammates</h1>
        <p style={{ color: C.sub }} className="text-sm">
          Nobody reports to you, so there is no team to show. If that looks wrong, it means
          reporting lines have not been set for your organisation yet.
        </p>
      </div>
    );
  }

  const directs = people.filter((p) => p.direct).length;
  // trainingsCompleted drives the character stage. GET /team does not return it — it
  // answers who reports to you, not how far along each of them is — so it is passed as 0
  // rather than invented. Everyone renders at the first stage until there is a real
  // per-person figure to use; a plausible-looking fake number is the one thing this
  // screen must not go back to.
  const members = people.map((p) => ({
    name: p.name,
    role: p.title || p.roleCode,
    trainingsCompleted: 0,
  }));

  return (
    <div className="p-8 max-w-4xl">
      <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">Teammates</h1>
      <p style={{ color: C.sub }} className="text-sm mb-6">
        Everyone who reports to you — {people.length} {people.length === 1 ? "person" : "people"}
        {directs > 0 && `, ${directs} directly`}.
      </p>
      <TeamHabitat members={members} highlightName={name} />
    </div>
  );
}

function CompanionCard({ trainingsCompleted, name, qScore }) {
  const stageIdx = getPetStageIdx(trainingsCompleted);
  const stage = PET_STAGES[stageIdx];
  const next = PET_STAGES[stageIdx + 1];
  const progressPct = next
    ? Math.round(((trainingsCompleted - stage.min) / (next.min - stage.min)) * 100)
    : 100;
  const [showShare, setShowShare] = useState(false);

  return (
    <div style={{ borderColor: C.line }} className="border rounded-xl p-5 bg-white mb-8">
      <div className="flex items-center justify-between mb-4">
        <h3 style={{ ...display, color: C.ink }} className="font-bold">Your Q character</h3>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowShare(true)} style={{ borderColor: C.line, color: C.violet700 }}
            className="border text-xs font-semibold px-3 py-1.5 rounded-full flex items-center gap-1.5">
            <Share2 size={12} /> Share
          </button>
          <span style={{ background: C.lavender, color: C.violet700 }} className="text-xs font-semibold px-2.5 py-1 rounded-full">
            Level {stage.level}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-6 mb-5 flex-wrap">
        <div className="rounded-2xl flex items-center justify-center shrink-0" style={{ width: 160, height: 160, background: C.lavender }}>
          <PetCreature stageIdx={stageIdx} size={stage.size} />
        </div>
        <div className="flex-1 min-w-[200px]">
          <p style={{ ...display, color: C.ink }} className="text-lg font-bold mb-0.5">{stage.name}</p>
          <p style={{ color: C.sub }} className="text-sm mb-3">
            {trainingsCompleted} training{trainingsCompleted === 1 ? "" : "s"} completed
          </p>
          {next ? (
            <>
              <div className="flex items-center justify-between mb-1.5">
                <span style={{ color: C.sub }} className="text-xs font-semibold">
                  {next.min - trainingsCompleted} more to reach {next.name}
                </span>
                <span style={{ color: C.sub }} className="text-xs font-semibold">{progressPct}%</span>
              </div>
              <div style={{ background: C.line }} className="w-full h-2.5 rounded-full overflow-hidden">
                <div style={{ width: `${progressPct}%`, background: `linear-gradient(90deg, ${C.violet500}, ${C.violet700})` }} className="h-full rounded-full transition-all" />
              </div>
            </>
          ) : (
            <p style={{ color: C.violet700 }} className="text-xs font-semibold flex items-center gap-1.5">
              <Trophy size={13} /> Max level reached — {stage.name} is fully grown
            </p>
          )}
        </div>
      </div>

      <div className="flex items-center">
        {PET_STAGES.map((st, i) => {
          const reached = i <= stageIdx;
          const isLast = i === PET_STAGES.length - 1;
          return (
            <React.Fragment key={st.level}>
              <div className="flex flex-col items-center" style={{ width: 56 }}>
                <div style={{ background: reached ? C.violet700 : "#F1F0F3", color: reached ? "#fff" : "#9A93A8" }}
                  className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0">
                  {reached ? (i === stageIdx ? st.level : <CheckCircle2 size={14} />) : <Lock size={11} />}
                </div>
                <span style={{ color: reached ? C.ink : C.sub }} className="text-[10px] font-semibold mt-1 text-center leading-tight">{st.name}</span>
              </div>
              {!isLast && <div style={{ background: i < stageIdx ? C.violet700 : C.line }} className="flex-1 h-0.5 -mt-4" />}
            </React.Fragment>
          );
        })}
      </div>

      {showShare && (
        <ShareCharacterModal stage={stage} stageIdx={stageIdx} name={name} qScore={qScore}
          trainingsCompleted={trainingsCompleted} onClose={() => setShowShare(false)} />
      )}
    </div>
  );
}

function ShareCharacterModal({ stage, stageIdx, name, qScore, trainingsCompleted, onClose }) {
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
      a.download = `${stage.name.toLowerCase()}-quizrant-card.png`;
      a.click();
    };
    img.src = url;
  };

  const caption = `I just reached Level ${stage.level} with ${stage.name} on Quizrant! ${trainingsCompleted} trainings completed, Q Score ${qScore}.`;
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
          <h3 style={{ ...display, color: C.ink }} className="font-bold">Share your character</h3>
          <button onClick={onClose} style={{ color: C.sub }}><X size={18} /></button>
        </div>

        <div className="flex justify-center mb-4">
          <svg ref={svgRef} width="240" height="340" viewBox="0 0 240 340" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <linearGradient id="share-bg" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stopColor={C.violet900} />
                <stop offset="100%" stopColor={C.violet700} />
              </linearGradient>
            </defs>
            <rect x="0" y="0" width="240" height="340" rx="20" fill="url(#share-bg)" />
            <text x="20" y="32" fill="#fff" fontSize="13" fontWeight="700" fontFamily="Sora, sans-serif" letterSpacing="1">QUIZRANT</text>
            <svg x="45" y="52" width="150" height="150" viewBox="0 0 150 150">
              <PetCreature stageIdx={stageIdx} size={150} />
            </svg>
            <text x="120" y="228" textAnchor="middle" fill="#fff" fontSize="20" fontWeight="700" fontFamily="Sora, sans-serif">{stage.name}</text>
            <text x="120" y="250" textAnchor="middle" fill="#E4D7F7" fontSize="12" fontWeight="600" fontFamily="Inter, sans-serif">Level {stage.level} · {name}</text>
            <line x1="30" y1="268" x2="210" y2="268" stroke="rgba(255,255,255,0.2)" />
            <text x="70" y="292" textAnchor="middle" fill="#fff" fontSize="16" fontWeight="700" fontFamily="Sora, sans-serif">{qScore}</text>
            <text x="70" y="308" textAnchor="middle" fill="#C9AEF5" fontSize="9" fontFamily="Inter, sans-serif">Q SCORE</text>
            <text x="170" y="292" textAnchor="middle" fill="#fff" fontSize="16" fontWeight="700" fontFamily="Sora, sans-serif">{trainingsCompleted}</text>
            <text x="170" y="308" textAnchor="middle" fill="#C9AEF5" fontSize="9" fontFamily="Inter, sans-serif">TRAININGS</text>
            <text x="120" y="325" textAnchor="middle" fill="rgba(255,255,255,0.5)" fontSize="9" fontFamily="Inter, sans-serif">quizrant.app</text>
          </svg>
        </div>

        <p style={{ color: C.sub }} className="text-xs text-center mb-4">Download the card, or copy a caption to post alongside a screenshot.</p>

        <div className="flex gap-2">
          <button onClick={handleDownload} style={{ background: C.violet700 }} className="flex-1 flex items-center justify-center gap-1.5 text-white text-sm font-semibold rounded-xl py-2.5">
            <Download size={14} /> Download
          </button>
          <button onClick={handleCopy} style={{ borderColor: C.line, color: C.violet700 }} className="flex-1 border flex items-center justify-center gap-1.5 text-sm font-semibold rounded-xl py-2.5">
            <Copy size={14} /> {copied ? "Copied!" : "Copy caption"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------- Profile ----------
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
    <div className="p-8 max-w-4xl">
      <h1 style={{ ...display, color: C.ink }} className="text-2xl font-bold mb-1">My profile</h1>
      <p style={{ color: C.sub }} className="text-sm mb-6">Your progress and Q score at a glance.</p>

      {loading && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}

      <div style={{ background: `linear-gradient(120deg, ${C.violet900}, ${C.violet700})` }}
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

      <CompanionCard trainingsCompleted={completed} name={p.name} qScore={qScore} />

      <div className="grid grid-cols-3 gap-4 mb-8">
        <div style={{ borderColor: C.line }} className="border rounded-xl p-4 bg-white flex items-center gap-3">
          <div style={{ background: C.lavender }} className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0">
            <Flame size={16} color={C.violet700} />
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
              <div style={{ width: `${b.accuracyPercent}%`, background: `linear-gradient(90deg, ${C.violet500}, ${C.violet700})` }} className="h-full rounded-full" />
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
              <div style={{ background: b.earned ? C.lavender : "#F1F0F3" }} className="w-10 h-10 rounded-xl flex items-center justify-center mb-3">
                <Icon size={18} color={b.earned ? C.violet700 : "#9A93A8"} />
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
  const [view, setView] = useState("dashboard");
  const [training, setTraining] = useState(null);
  const [quiz, setQuiz] = useState(null);
  const [result, setResult] = useState(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState(null);

  const signIn = useCallback((principal) => {
    setAuth(principal);
    setView("dashboard");
    api.team().then(setTeam).catch(() => setTeam(null));
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
           style={{ ...font, background: `linear-gradient(160deg, ${C.violet900} 0%, ${C.violet700} 45%, ${C.violet500} 100%)` }}>
        <Logo size={36} />
      </div>
    );
  }

  if (!auth) return <Login onLogin={signIn} />;

  const manages = Boolean(team?.manages);
  const goto = (v) => setView(v);
  const openTraining = (t) => { setTraining(t); setView("trainingDetail"); };

  const beginQuiz = async () => {
    setStarting(true);
    setStartError(null);
    try {
      const q = await api.startQuiz({ training: training.title, length: 8 });
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
    content = <DocumentsScreen team={team} onDone={() => goto("dashboard")} />;
  } else if (view === "team") {
    content = <ManagerTeam team={team} />;
  } else if (view === "dashboard") {
    content = <Dashboard name={auth.name || auth.email} onOpenPath={() => goto("path")} onOpenTraining={openTraining} />;
  } else if (view === "path") {
    content = <LearningPath onBack={() => goto("dashboard")} onOpenTraining={openTraining} />;
  } else if (view === "trainingDetail") {
    content = <TrainingDetail training={training} onBack={() => goto("dashboard")} onStartLesson={() => goto("lesson")} />;
  } else if (view === "lesson") {
    content = <LessonScreen training={training} onBack={() => goto("trainingDetail")} onContinue={() => goto("quizPre")} />;
  } else if (view === "quizPre") {
    content = <QuizPreScreen training={training} onStart={beginQuiz} onBack={() => goto("lesson")} starting={starting} error={startError} />;
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
    content = <QuizResults result={result} onRetake={() => goto("quizPre")} onDone={() => goto("dashboard")} />;
  } else if (view === "certificates") {
    content = <Certificates />;
  } else if (view === "teammates") {
    content = <TeammatesGallery team={team} name={auth.name || auth.email} />;
  }

  const quizViews = ["trainingDetail", "lesson", "quizPre", "quizRunner", "quizResults"];

  return (
    <Shell
      name={auth.name || auth.email}
      department={auth.department}
      title={auth.title}
      manages={manages}
      active={quizViews.includes(view) ? "dashboard" : view}
      setActive={goto}
      onLogout={async () => {
        await api.logout();
        setAuth(null);
        setTeam(null);
        setView("dashboard");
      }}
    >
      {content}
    </Shell>
  );
}
