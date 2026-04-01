const byId = (id) => document.getElementById(id);

const els = {
  sessionState: byId("sessionState"),
  btnStart: byId("btnStart"),
  btnUpload: byId("btnUpload"),
  resumeFile: byId("resumeFile"),
  jobId: byId("jobId"),
  btnPoll: byId("btnPoll"),
  rawResume: byId("rawResume"),
  btnConvertRaw: byId("btnConvertRaw"),
  structuredResume: byId("structuredResume"),
  vacancyText: byId("vacancyText"),
  btnMatch: byId("btnMatch"),
  btnLetter: byId("btnLetter"),
  vacancyOut: byId("vacancyOut"),
  chatLog: byId("chatLog"),
  chatInput: byId("chatInput"),
  btnSend: byId("btnSend"),
  bffUrl: byId("bffUrl"),
};

const guessBffFromOrigin = () => {
  // Avoid IPv6 localhost (::1) issues on Windows + Docker port publishing.
  if (location.hostname === "localhost") return "http://127.0.0.1:8010";
  if (location.hostname === "127.0.0.1") return "http://127.0.0.1:8010";
  // On VPS behind Nginx we serve BFF on same origin via /bff (no CORS).
  return `${location.origin}/bff`;
};

const defaultBff = localStorage.getItem("bffUrl") || guessBffFromOrigin();
els.bffUrl.value = defaultBff;
els.bffUrl.addEventListener("change", () => {
  localStorage.setItem("bffUrl", els.bffUrl.value.trim());
});

const bff = () => (els.bffUrl.value.trim() || defaultBff).replace(/\/$/, "");

async function api(path, opts = {}) {
  const url = `${bff()}${path}`;
  const res = await fetch(url, {
    credentials: "include",
    ...opts,
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error || data.raw)) || `HTTP ${res.status}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function pushMsg(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  els.chatLog.appendChild(div);
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}

async function ensureSession() {
  try {
    const me = await api("/session/me");
    els.sessionState.textContent = `session: ${me.owner_id.slice(0, 8)}…`;
    return me.owner_id;
  } catch {
    const started = await api("/session/start", { method: "POST" });
    els.sessionState.textContent = `session: ${started.owner_id.slice(0, 8)}…`;
    return started.owner_id;
  }
}

els.btnStart.addEventListener("click", async () => {
  await ensureSession();
});

els.btnUpload.addEventListener("click", async () => {
  await ensureSession();
  const f = els.resumeFile.files && els.resumeFile.files[0];
  if (!f) throw new Error("Выберите файл резюме");
  const fd = new FormData();
  fd.append("file", f);
  const out = await api("/documents/upload", { method: "POST", body: fd });
  els.jobId.value = out.job_id || "";
  els.vacancyOut.textContent = JSON.stringify(out, null, 2);
});

els.btnPoll.addEventListener("click", async () => {
  const jobId = els.jobId.value.trim();
  if (!jobId) throw new Error("Введите job_id");
  const out = await api(`/documents/tasks/${encodeURIComponent(jobId)}/status`);
  els.vacancyOut.textContent = JSON.stringify(out, null, 2);
});

els.btnConvertRaw.addEventListener("click", async () => {
  const raw = els.rawResume.value.trim();
  if (!raw) throw new Error("Вставьте raw резюме");
  const out = await api("/resume/convert", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw_text: raw }),
  });
  els.structuredResume.value = out.structured_text || "";
  els.vacancyOut.textContent = JSON.stringify(out, null, 2);
});

els.btnMatch.addEventListener("click", async () => {
  await ensureSession();
  const vacancy = els.vacancyText.value.trim();
  if (!vacancy) throw new Error("Вставьте текст вакансии");
  const out = await api("/match-vacancy", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vacancy_text: vacancy, summary: true, target_lang: "ru" }),
  });
  els.vacancyOut.textContent = JSON.stringify(out, null, 2);
});

els.btnLetter.addEventListener("click", async () => {
  await ensureSession();
  const vacancy = els.vacancyText.value.trim();
  if (!vacancy) throw new Error("Вставьте текст вакансии");
  const out = await api("/cover-letter", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vacancy_text: vacancy }),
  });
  els.vacancyOut.textContent = (out.letter || out.error || JSON.stringify(out, null, 2));
});

async function pollMessage(messageId) {
  for (let i = 0; i < 60; i++) {
    const st = await api(`/messages/${encodeURIComponent(messageId)}`);
    const status = st.status || "";
    if (status === "completed") return st;
    await new Promise((r) => setTimeout(r, 1500));
  }
  throw new Error("timeout");
}

els.btnSend.addEventListener("click", async () => {
  await ensureSession();
  const q = els.chatInput.value.trim();
  if (!q) return;
  els.chatInput.value = "";
  pushMsg("user", q);
  const enq = await api("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: q }),
  });
  const messageId = enq.message_id;
  const st = await pollMessage(messageId);
  const text = st.assistant_text_translated || st.assistant_text || "(empty)";
  pushMsg("assistant", text);
});

window.addEventListener("load", async () => {
  try {
    await ensureSession();
  } catch (e) {
    els.sessionState.textContent = "session: error";
    console.warn(e);
  }
});

