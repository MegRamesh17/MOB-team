/**
 * The only place this app talks to a server.
 *
 * Everything goes through `call()`, so pointing the UI at the deployed Azure
 * Functions host is one change: set VITE_API_BASE. In development the Vite proxy
 * forwards /api to the Python dev server, so requests are same-origin.
 *
 * THE FRONTEND HAS NO KEYS AND NEEDS NONE. Azure credentials live server-side; the
 * browser only ever calls its own origin. If anything here ever needs an API key,
 * that logic belongs on the server instead.
 *
 * The answer key is never in the browser. /quiz/start returns option ids and text
 * only. Grading a question — even mid-quiz for instant feedback — is a round trip to
 * gradeAnswer(), so the correct answer is revealed for one question, only after it has
 * been answered. That is why a score from this app can be trusted.
 */

const BASE = import.meta.env.VITE_API_BASE || "";

/**
 * The session token.
 *
 * Replaces the x-learner-id / x-learner-role pair, which let the browser declare who it
 * was and what role it held. Both now travel inside a token the server signed, so editing
 * them invalidates the signature rather than changing what you are served.
 *
 * localStorage so a refresh does not sign you out. Any script on this origin can read it;
 * the mitigation is not running untrusted scripts here, and an httpOnly cookie once the
 * API and app share an origin in deployment.
 */
const TOKEN_KEY = "quizgen.session";

let token = null;
try {
  token = localStorage.getItem(TOKEN_KEY);
} catch {
  /* Safari private mode throws on access. In-memory only: sign-in still works, it
     just does not survive a refresh. */
}

function setToken(value) {
  token = value || null;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* see above */ }
}

const authHeaders = () => (token ? { Authorization: `Bearer ${token}` } : {});

/** Sign in. Returns the principal: role_code, access_role, manager_id, company_id. */
export async function login(email, password) {
  const res = await fetch(BASE + "/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  let payload = {};
  try { payload = await res.json(); } catch { /* non-JSON error body */ }
  if (!res.ok) {
    const err = new Error(payload.detail || payload.title || "Sign-in failed");
    err.status = res.status;
    throw err;
  }
  setToken(payload.token);
  return payload.principal;
}

/**
 * Sign out. Honest about the limit: this drops the token here. It stays valid on the
 * server until it expires — revoking it needs shared state that does not exist yet.
 */
export async function logout() {
  try {
    await fetch(BASE + "/api/auth/logout", { method: "POST", headers: authHeaders() });
  } catch { /* signing out must work even when the API is unreachable */ }
  setToken(null);
}

/** Who the server thinks we are. Used on load to restore a session after a refresh. */
export async function currentUser() {
  if (!token) return null;
  const res = await fetch(BASE + "/api/auth/me", { headers: authHeaders() });
  if (!res.ok) {
    setToken(null);   // expired: drop it rather than fail every later call with it
    return null;
  }
  return (await res.json()).principal;
}

async function call(path, { method = "GET", body } = {}) {
  const res = await fetch(BASE + "/api" + path, {
    method,
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  let payload = {};
  try { payload = await res.json(); } catch { /* empty or non-JSON body */ }

  if (!res.ok) {
    // 401 means the token expired or was revoked. Clear it so the app falls back to
    // sign-in rather than retrying every call with one that cannot work.
    if (res.status === 401) setToken(null);
    // The server sends an actionable message (which CLI command to run when the
    // bank is empty, for instance). Prefer it over a generic HTTP error.
    const err = new Error(payload.detail || payload.title || `Request failed (${res.status})`);
    err.status = res.status;
    err.title = payload.title;
    throw err;
  }
  return payload;
}

export const health = () => call("/health");
export const me = () => call("/me");
export const trainings = () => call("/trainings");
export const pathway = (training) =>
  call(`/pathway?training=${encodeURIComponent(training)}`);
export const lesson = (training, moduleId) => {
  const module = moduleId ? `&moduleId=${encodeURIComponent(moduleId)}` : "";
  return call(`/lesson?training=${encodeURIComponent(training)}${module}`);
};
export const completeLessonPage = ({ moduleId, pageId }) =>
  call("/lesson/page/complete", { method: "POST", body: { moduleId, pageId } });
export const certificates = () => call("/certificates");
export const skillOptions = () => call("/skills/options");
export const setSkillInterest = (skills) =>
  call("/skills/interest", { method: "POST", body: { skills } });

/**
 * The floating pet: points (derived from certificates earned, never a stored balance),
 * the shop catalog, and what this employee owns/wears.
 */
export const getPet = () => call("/pet");
export const purchasePetItem = (itemId) =>
  call("/pet/purchase", { method: "POST", body: { itemId } });
export const equipPetItem = (itemId) =>
  call("/pet/equip", { method: "POST", body: { itemId } });

/**
 * This learner's own preferences. Real, persisted server-side -- not a local toggle.
 * updateSettings takes a partial update ({ notificationsEnabled } and/or
 * { petVisible }) -- whichever field is omitted is left exactly as stored.
 */
export const getSettings = () => call("/settings");
export const updateSettings = (updates) =>
  call("/settings", { method: "POST", body: updates });

export async function downloadCertificate(certificateUrl) {
  const url = certificateUrl.startsWith("http") ? certificateUrl : BASE + certificateUrl;
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) {
    let payload = {};
    try { payload = await res.json(); } catch { /* PDF endpoint may return plain text */ }
    throw new Error(payload.detail || payload.title || "Certificate download failed");
  }
  return res.blob();
}

export const startPathwayAssessment = ({ training, kind, moduleId }) =>
  call("/pathway/start", {
    method: "POST",
    body: { training, kind, moduleId: moduleId || undefined },
  });

export const answerPathwayQuestion = ({ attemptId, questionId, selectedOptionIds, textAnswer }) =>
  call("/pathway/answer", {
    method: "POST",
    body: { attemptId, questionId, selectedOptionIds, textAnswer },
  });

export const completePathwayAssessment = (attemptId) =>
  call("/pathway/complete", { method: "POST", body: { attemptId } });

/** The team you manage, and the roles you may upload for. Empty for most people. */
export const team = () => call("/team");

/**
 * Real coverage numbers (required/current/coverage/missing/expired/renewalDueCount) for
 * everyone in your reporting subtree, keyed by employeeId. Join with team().people for
 * name/email/reporting line -- this endpoint only knows completion, not identity.
 */
export const teamCompletion = () => call("/team/completion");

/**
 * Everyone in your department, ranked by points earned (real, derived from trainings
 * actually completed -- see api/shared/pet_shop.py). Department-wide, not just the
 * peers team() returns.
 */
export const teamLeaderboard = () => call("/team/leaderboard");

/**
 * Nudge one person in your reporting subtree about their outstanding training. Real
 * endpoint, real computed missing/expired list -- whether it actually sends an email
 * depends on RESEND_API_KEY being configured for this environment, reflected honestly
 * in the response's `sent` flag rather than always claiming success.
 */
export const sendReminder = (employeeId) =>
  call("/team/remind", { method: "POST", body: { employeeId } });

/**
 * Q Score. Pass an email to read a report's — permitted only inside your reporting
 * subtree, and a 404 otherwise, so this cannot be used to discover who exists.
 */
export const qscore = (employeeEmail) =>
  call("/qscore" + (employeeEmail ? `?employee=${encodeURIComponent(employeeEmail)}` : ""));

// Neither learner nor role is sent: the server reads both from the token and ignores
// anything the body claims. Sending them would only imply they were trusted.
export const startQuiz = ({ training, length = 8 }) =>
  call("/quiz/start", { method: "POST", body: { training, length } });

/** Grade one question mid-quiz. The key stays on the server. */
export const gradeAnswer = ({ attemptId, questionId, selectedOptionIds, textAnswer }) =>
  call("/quiz/answer", {
    method: "POST",
    body: { attemptId, questionId, selectedOptionIds, textAnswer },
  });

/** Final score is computed server-side; the client's tally is never trusted. */
export const submitQuiz = ({ attemptId, answers }) =>
  call("/quiz/submit", { method: "POST", body: { attemptId, answers } });

export const documents = () => call("/documents");
export const jobStatus = (jobId) => call(`/jobs/${encodeURIComponent(jobId)}`);
export const coursePreview = (training) =>
  call(`/courses/preview?training=${encodeURIComponent(training)}`);

/**
 * Upload a document. The server extracts it inline and returns straight away with the
 * section count, then generates questions on a background thread — so the caller gets
 * a jobId to poll rather than a request held open for minutes.
 *
 * Content-Type is deliberately not set: the browser must add its own multipart
 * boundary, and setting the header manually omits it and breaks parsing server-side.
 */
export async function uploadDocument(file) {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(BASE + "/api/documents", {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });

  let payload = {};
  try { payload = await res.json(); } catch { /* non-JSON error body */ }
  if (!res.ok) {
    const err = new Error(payload.detail || payload.title || `Upload failed (${res.status})`);
    err.status = res.status;
    err.title = payload.title;
    throw err;
  }
  return payload;
}

export const roles = () => call("/roles");
export const addRole = ({ roleCode, title, description }) =>
  call("/roles", { method: "POST", body: { roleCode, title, description } });
export const removeRole = (roleCode) =>
  call(`/roles/${encodeURIComponent(roleCode)}/delete`, { method: "POST" });

export const trustedLinks = () => call("/links");
/**
 * Submits a trusted URL. Response shape matches uploadDocument's exactly (file, title,
 * proposedRoles, permittedRoles, ...) -- the server runs it through the same
 * extraction/grounding/confirm-before-generate pipeline, so the same MappingReview
 * component that handles an upload's response handles this one unchanged.
 */
export const addTrustedLink = ({ url, scope, roleCode, crawl, maxPages }) =>
  call("/links/add", {
    method: "POST",
    body: { url, scope, roleCode, crawl: crawl !== false, maxPages: maxPages || 25 },
  });

/**
 * The manager's decision on an upload: the confirmed section->role mapping, any
 * roles they chose to create, and (if the AI judged this an update) which existing
 * module it supersedes. Generation starts only after this call.
 */
export const confirmDocument = ({ title, assignments, newRoles, supersede, makeRequired }) =>
  call("/documents/confirm", {
    method: "POST",
    body: {
      title, assignments, newRoles: newRoles || [], supersede: supersede || "",
      // Defaults true server-side too if omitted; passed explicitly so the
      // checkbox in the confirm screen is the actual source of truth.
      makeRequired: makeRequired !== false,
    },
  });

/**
 * Permanently delete a course: the source, its modules, questions, and every learner
 * attempt/certificate derived from it. Also doubles as "cancel this upload" -- calling
 * it on a document whose generation is still running stops that job too, since deleting
 * its GenerationJobs row is the only signal the background worker has to stop. Only the
 * person who added it, or an admin/executive, may call this -- enforced server-side,
 * not just hidden here.
 */
export const deleteDocument = (documentId) =>
  call(`/documents/${encodeURIComponent(documentId)}/delete`, { method: "POST" });
