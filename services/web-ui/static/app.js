const byId = (id) => document.getElementById(id);

const els = {
  sessionState: byId("sessionState"),
  btnUpload: byId("btnUpload"),
  resumeFile: byId("resumeFile"),
  rawResume: byId("rawResume"),
  btnConvertRaw: byId("btnConvertRaw"),
  structuredResume: byId("structuredResume"),
  resumeLoader: byId("resumeLoader"),
  resumeLamps: byId("resumeLamps"),
  vacancyText: byId("vacancyText"),
  btnMatch: byId("btnMatch"),
  btnLetter: byId("btnLetter"),
  vacancyOut: byId("vacancyOut"),
  vacancyLoader: byId("vacancyLoader"),
  chatLog: byId("chatLog"),
  chatInput: byId("chatInput"),
  btnSend: byId("btnSend"),
  bffUrl: byId("bffUrl"),
};

const guessBffFromOrigin = () => {
  // Avoid IPv6 localhost (::1) issues on Windows + Docker port publishing.
  if (location.hostname === "localhost") return "http://127.0.0.1:8010";
  if (location.hostname === "127.0.0.1") return "http://127.0.0.1:8010";
  // If UI is opened directly on its published port (e.g. http://IP:3000),
  // there is no Nginx path routing, so use the BFF published port.
  if (location.port && location.port !== "80" && location.port !== "443") {
    return `http://${location.hostname}:8010`;
  }
  // Behind Nginx on port 80/443 we serve BFF on same origin via /bff (no CORS).
  return `${location.origin}/bff`;
};

const defaultBff = localStorage.getItem("bffUrl") || guessBffFromOrigin();
els.bffUrl.value = defaultBff;
els.bffUrl.addEventListener("change", () => {
  localStorage.setItem("bffUrl", els.bffUrl.value.trim());
});

const bff = () => (els.bffUrl.value.trim() || defaultBff).replace(/\/$/, "");

let chatInFlight = false;
let actionInFlight = false;
let sessionActive = false;
let resumeReady = false;
let thinkingEl = null;
let persistentThemeMode = "";

const LOADER_TEXT = {
  processing: "Спокойствие, только спокойствие! (идет анализ данных...)",
  convert: "Конвертирую резюме в наш формат...",
  upload: "Загружаю и индексирую резюме...",
  match: "Спокойствие, только спокойствие! (идет анализ вакансии...)",
  letter: "Спокойствие, только спокойствие! (формирую сопроводительное письмо...)",
  chat: "Спокойствие, только спокойствие! (идет анализ данных...)",
};

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

function pushThinking() {
  if (thinkingEl) return thinkingEl;
  const div = document.createElement("div");
  div.className = "msg assistant thinking";
  div.innerHTML = `Думаю <span class="constellation"><i></i><i></i><i></i><i></i></span>`;
  els.chatLog.appendChild(div);
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
  thinkingEl = div;
  return div;
}

function popThinking() {
  if (!thinkingEl) return;
  thinkingEl.remove();
  thinkingEl = null;
}

function setChatLocked(locked) {
  chatInFlight = locked;
  refreshUiState();
}

function setThemeMode(mode) {
  const next = mode || persistentThemeMode;
  if (!next) {
    document.body.removeAttribute("data-mode");
    return;
  }
  document.body.setAttribute("data-mode", next);
}

function refreshUiState() {
  const busy = chatInFlight || actionInFlight;
  const canUseActions = resumeReady && !busy;

  if (els.btnUpload) els.btnUpload.disabled = busy || resumeReady;
  if (els.resumeFile) els.resumeFile.disabled = busy || resumeReady;
  if (els.btnConvertRaw) els.btnConvertRaw.disabled = !canUseActions;
  if (els.btnMatch) els.btnMatch.disabled = !canUseActions;
  if (els.btnLetter) els.btnLetter.disabled = !canUseActions;
  if (els.btnSend) els.btnSend.disabled = !canUseActions;
  if (els.chatInput) els.chatInput.disabled = !canUseActions;
}

function setResumeLamp(stage) {
  if (!els.resumeLamps) return;
  const red = els.resumeLamps.querySelector('[data-rstep="1"]');
  const yellow = els.resumeLamps.querySelector('[data-rstep="2"]');
  const green = els.resumeLamps.querySelector('[data-rstep="3"]');
  [red, yellow, green].forEach((x) => x && x.classList.remove("active"));
  if (stage === "upload2") {
    yellow && yellow.classList.add("active");
    return;
  }
  if (stage === "ready") {
    green && green.classList.add("active");
    return;
  }
  red && red.classList.add("active");
}

function setInlineLoader(el, text = "", visible = false) {
  if (!el) return;
  if (!visible) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  el.innerHTML = `<span class="constellation"><i></i><i></i><i></i><i></i></span><span>${text || LOADER_TEXT.processing}</span>`;
}

function setActionThinking(label = "Думаю") {
  els.vacancyOut.value = `${label}...`;
}

function setActionOut(text) {
  els.vacancyOut.value = text;
  els.vacancyOut.scrollTop = 0;
}

function formatMatchOutput(payload) {
  const item = Array.isArray(payload) ? payload[0] : payload;
  if (!item || typeof item !== "object") return JSON.stringify(payload, null, 2);
  const score = typeof item.relevance_score === "number" ? item.relevance_score : null;
  const summary = typeof item.summary === "string" ? item.summary.trim() : "";
  if (summary) {
    return score == null ? summary : `Релевантность: ${score.toFixed(2)}\n\n${summary}`;
  }
  const snippets = Array.isArray(item.matched_snippets) ? item.matched_snippets : [];
  if (snippets.length) {
    const prefix = score == null ? "" : `Релевантность: ${score.toFixed(2)}\n\n`;
    return `${prefix}${snippets.slice(0, 5).join("\n\n")}`;
  }
  return JSON.stringify(payload, null, 2);
}

async function ensureSession() {
  try {
    const me = await api("/session/me");
    els.sessionState.textContent = `session: ${me.owner_id.slice(0, 8)}…`;
    sessionActive = true;
    return me.owner_id;
  } catch {
    const started = await api("/session/start", { method: "POST" });
    els.sessionState.textContent = `session: ${started.owner_id.slice(0, 8)}…`;
    sessionActive = true;
    return started.owner_id;
  }
}

els.btnUpload.addEventListener("click", async () => {
  if (chatInFlight || actionInFlight) return;
  actionInFlight = true;
  refreshUiState();
  setResumeLamp("idle");
  setInlineLoader(els.resumeLoader, LOADER_TEXT.upload, true);
  try {
    await ensureSession();
    const f = els.resumeFile.files && els.resumeFile.files[0];
    if (!f) throw new Error("Выберите файл резюме");
    const fd = new FormData();
    fd.append("file", f);
    setResumeLamp("upload2");
    setInlineLoader(els.resumeLoader, "Конвертирую и загружаю резюме...", true);
    const out = await api("/documents/upload", { method: "POST", body: fd });
    const jobId = out.job_id || "";
    if (!jobId) {
      setActionOut("Загрузка отправлена, но job_id не получен.");
      return;
    }
    for (let i = 0; i < 90; i++) {
      setInlineLoader(els.resumeLoader, LOADER_TEXT.upload, true);
      const st = await api(`/documents/tasks/${encodeURIComponent(jobId)}/status`);
      const status = String(st.status || "").toLowerCase();
      if (status === "completed") {
        resumeReady = true;
        setResumeLamp("ready");
        setInlineLoader(els.resumeLoader, "", false);
        setActionOut("Резюме успешно загружено и проиндексировано.");
        return;
      }
      if (status === "failed") {
        setResumeLamp(sessionActive ? "ready" : "idle");
        setInlineLoader(els.resumeLoader, "", false);
        setActionOut(`Ошибка индексации: ${st.error_message || "unknown_error"}`);
        return;
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    setResumeLamp(sessionActive ? "ready" : "idle");
    setInlineLoader(els.resumeLoader, "", false);
    setActionOut("Индексация занимает больше обычного. Попробуйте чуть позже.");
  } catch (e) {
    setResumeLamp(sessionActive ? "ready" : "idle");
    setInlineLoader(els.resumeLoader, "", false);
    setActionOut(`Ошибка upload: ${e && e.message ? e.message : String(e)}`);
  } finally {
    actionInFlight = false;
    refreshUiState();
  }
});

els.btnConvertRaw.addEventListener("click", async () => {
  if (chatInFlight || actionInFlight) return;
  if (!resumeReady) return;
  actionInFlight = true;
  refreshUiState();
  setInlineLoader(els.resumeLoader, LOADER_TEXT.convert, true);
  try {
    const raw = els.rawResume.value.trim();
    if (!raw) throw new Error("Вставьте raw резюме");
    const out = await api("/resume/convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ raw_text: raw }),
    });
    setInlineLoader(els.resumeLoader, "", false);
    els.structuredResume.value = out.structured_text || "";
    setActionOut(JSON.stringify(out, null, 2));
  } catch (e) {
    setInlineLoader(els.resumeLoader, "", false);
    setActionOut(`Ошибка convert: ${e && e.message ? e.message : String(e)}`);
  } finally {
    actionInFlight = false;
    refreshUiState();
  }
});

els.btnMatch.addEventListener("click", async () => {
  if (actionInFlight || chatInFlight) return;
  if (!resumeReady) return;
  actionInFlight = true;
  refreshUiState();
  setActionThinking("Думаю над match");
  setInlineLoader(els.vacancyLoader, LOADER_TEXT.match, true);
  setThemeMode("match");
  try {
    const vacancy = els.vacancyText.value.trim();
    if (!vacancy) throw new Error("Вставьте текст вакансии");
    const out = await api("/match-vacancy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vacancy_text: vacancy, summary: true, target_lang: "ru" }),
    });
    setInlineLoader(els.vacancyLoader, "", false);
    setActionOut(formatMatchOutput(out));
    persistentThemeMode = "match";
    setThemeMode();
  } catch (e) {
    setInlineLoader(els.vacancyLoader, "", false);
    setActionOut(`Ошибка match: ${e && e.message ? e.message : String(e)}`);
  } finally {
    actionInFlight = false;
    refreshUiState();
  }
});

els.btnLetter.addEventListener("click", async () => {
  if (actionInFlight || chatInFlight) return;
  if (!resumeReady) return;
  actionInFlight = true;
  refreshUiState();
  setActionThinking("Думаю над cover-letter");
  setInlineLoader(els.vacancyLoader, LOADER_TEXT.letter, true);
  try {
    const vacancy = els.vacancyText.value.trim();
    if (!vacancy) throw new Error("Вставьте текст вакансии");
    const out = await api("/cover-letter", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vacancy_text: vacancy }),
    });
    setInlineLoader(els.vacancyLoader, "", false);
    setActionOut(out.letter || out.error || JSON.stringify(out, null, 2));
  } catch (e) {
    setInlineLoader(els.vacancyLoader, "", false);
    setActionOut(`Ошибка cover-letter: ${e && e.message ? e.message : String(e)}`);
  } finally {
    actionInFlight = false;
    refreshUiState();
  }
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

async function sendChat() {
  if (chatInFlight || actionInFlight) return;
  if (!resumeReady) return;
  const q = els.chatInput.value.trim();
  if (!q) return;
  setChatLocked(true);
  pushThinking();
  try {
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
    popThinking();
    pushMsg("assistant", text);
  } catch (e) {
    popThinking();
    pushMsg("assistant", `Ошибка: ${e && e.message ? e.message : String(e)}`);
  } finally {
    setChatLocked(false);
    els.chatInput.focus();
  }
}

els.btnSend.addEventListener("click", sendChat);

els.chatInput.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  if (e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return;
  e.preventDefault();
  sendChat();
});

window.addEventListener("load", async () => {
  setActionOut("Здесь появится прокручиваемый результат match / cover-letter");
  els.sessionState.textContent = "session: начнется при загрузке резюме";
  setResumeLamp("idle");
  refreshUiState();
});

