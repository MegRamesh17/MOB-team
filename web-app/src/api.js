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

// Identifies the learner. A demo stand-in for Entra sign-in: the real deployment
// reads the platform-injected x-ms-client-principal header instead, and this header
// is ignored there.
let learnerId = "demo-learner";
export const setLearner = (id) => { learnerId = id || "demo-learner"; };
export const getLearner = () => learnerId;

async function call(path, { method = "GET", body } = {}) {
  const res = await fetch(BASE + "/api" + path, {
    method,
    headers: { "Content-Type": "application/json", "x-learner-id": learnerId },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  let payload = {};
  try { payload = await res.json(); } catch { /* empty or non-JSON body */ }

  if (!res.ok) {
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
export const lesson = (training) => call(`/lesson?training=${encodeURIComponent(training)}`);
export const certificates = () => call("/certificates");

export const startQuiz = ({ training, length = 8, role = "" }) =>
  call("/quiz/start", { method: "POST", body: { learnerId, training, length, role } });

/** Grade one question mid-quiz. The key stays on the server. */
export const gradeAnswer = ({ attemptId, questionId, selectedOptionIds, textAnswer }) =>
  call("/quiz/answer", {
    method: "POST",
    body: { attemptId, questionId, selectedOptionIds, textAnswer },
  });

/** Final score is computed server-side; the client's tally is never trusted. */
export const submitQuiz = ({ attemptId, answers }) =>
  call("/quiz/submit", { method: "POST", body: { attemptId, learnerId, answers } });

export const documents = () => call("/documents");
export const jobStatus = (jobId) => call(`/jobs/${encodeURIComponent(jobId)}`);

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
    headers: { "x-learner-id": learnerId },
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
