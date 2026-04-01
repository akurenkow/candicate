const byId = (id) => document.getElementById(id);

const els = {
  sessionState: byId("sessionState"),
  btnStart: byId("btnStart"),
  btnUpload: byId("btnUpload"),
  resumeFile: byId("resumeFile"),
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
  globalLoader: byId("globalLoader"),
  globalLoaderText: byId("globalLoaderText"),
  progressLamps: byId("progressLamps"),
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
let thinkingEl = null;
let actionInFlight = false;
let busyCount = 0;

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
  div.innerHTML = `Думаю<span class="dots"></span>`;
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
  els.btnSend.disabled = locked;
  els.chatInput.disabled = locked;
}

function setActionLocked(locked) {
  actionInFlight = locked;
  els.btnMatch.disabled = locked;
  els.btnLetter.disabled = locked;
}

function setThemeMode(mode) {
  if (!mode) {
    document.body.removeAttribute("data-mode");
    return;
  }
  document.body.setAttribute("data-mode", mode);
}

function applyGlobalDisabled(disabled) {
  const controls = [
    els.btnStart,
    els.btnUpload,
    els.btnConvertRaw,
    els.btnMatch,
    els.btnLetter,
    els.btnSend,
    els.resumeFile,
    els.rawResume,
    els.vacancyText,
    els.chatInput,
    els.bffUrl,
  ];
  controls.forEach((el) => {
    if (!el) return;
    el.disabled = !!disabled;
  });
}

function beginBusy(text, mode = "") {
  busyCount += 1;
  if (els.globalLoaderText) {
    els.globalLoaderText.textContent = text || LOADER_TEXT.processing;
  }
  if (els.globalLoader) {
    els.globalLoader.classList.remove("hidden");
  }
  setThemeMode(mode);
  applyGlobalDisabled(true);
  setLoaderStep(1);
}

function endBusy() {
  busyCount = Math.max(0, busyCount - 1);
  if (busyCount > 0) return;
  if (els.globalLoader) {
    els.globalLoader.classList.add("hidden");
  }
  setThemeMode("");
  applyGlobalDisabled(false);
  setLoaderStep(0);
}

function setLoaderStep(step) {
  if (!els.progressLamps) return;
  const lamps = els.progressLamps.querySelectorAll(".lamp");
  lamps.forEach((lamp) => {
    const n = Number(lamp.getAttribute("data-step") || "0");
    lamp.classList.toggle("active", step >= n && step > 0);
  });
}

function setLoaderText(text) {
  if (els.globalLoaderText) {
    els.globalLoaderText.textContent = text || LOADER_TEXT.processing;
  }
}

function setActionThinking(label = "Думаю") {
  els.vacancyOut.value = `${label}...`;
}

function setActionOut(text) {
  els.vacancyOut.value = text;
  els.vacancyOut.scrollTop = 0;
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
  beginBusy(LOADER_TEXT.upload);
  try {
    await ensureSession();
    const f = els.resumeFile.files && els.resumeFile.files[0];
    if (!f) throw new Error("Выберите файл резюме");
    const fd = new FormData();
    fd.append("file", f);
    setLoaderStep(2);
    setLoaderText("Конвертирую и загружаю резюме...");
    const out = await api("/documents/upload", { method: "POST", body: fd });
    const jobId = out.job_id || "";
    if (!jobId) {
      setActionOut("Загрузка отправлена, но job_id не получен.");
      return;
    }
    for (let i = 0; i < 90; i++) {
      setLoaderText(LOADER_TEXT.upload);
      const st = await api(`/documents/tasks/${encodeURIComponent(jobId)}/status`);
      const status = String(st.status || "").toLowerCase();
      if (status === "completed") {
        setLoaderStep(3);
        setLoaderText("Готово. Резюме проиндексировано.");
        setActionOut("Резюме успешно загружено и проиндексировано.");
        return;
      }
      if (status === "failed") {
        setLoaderStep(3);
        setActionOut(`Ошибка индексации: ${st.error_message || "unknown_error"}`);
        return;
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    setActionOut("Индексация занимает больше обычного. Попробуйте чуть позже.");
  } catch (e) {
    setActionOut(`Ошибка upload: ${e && e.message ? e.message : String(e)}`);
  } finally {
    endBusy();
  }
});

els.btnConvertRaw.addEventListener("click", async () => {
  beginBusy(LOADER_TEXT.convert);
  try {
    const raw = els.rawResume.value.trim();
    if (!raw) throw new Error("Вставьте raw резюме");
    setLoaderStep(2);
    const out = await api("/resume/convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ raw_text: raw }),
    });
    setLoaderStep(3);
    setLoaderText("Готово. Конвертация завершена.");
    els.structuredResume.value = out.structured_text || "";
    setActionOut(JSON.stringify(out, null, 2));
  } catch (e) {
    setActionOut(`Ошибка convert: ${e && e.message ? e.message : String(e)}`);
  } finally {
    endBusy();
  }
});

els.btnMatch.addEventListener("click", async () => {
  if (actionInFlight || chatInFlight) return;
  beginBusy(LOADER_TEXT.match, "match");
  setActionLocked(true);
  setActionThinking("Думаю над match");
  try {
    await ensureSession();
    const vacancy = els.vacancyText.value.trim();
    if (!vacancy) throw new Error("Вставьте текст вакансии");
    setLoaderStep(2);
    const out = await api("/match-vacancy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vacancy_text: vacancy, summary: true, target_lang: "ru" }),
    });
    setLoaderStep(3);
    setLoaderText("Готово. Анализ вакансии завершен.");
    setActionOut(JSON.stringify(out, null, 2));
  } catch (e) {
    setActionOut(`Ошибка match: ${e && e.message ? e.message : String(e)}`);
  } finally {
    setActionLocked(false);
    endBusy();
  }
});

els.btnLetter.addEventListener("click", async () => {
  if (actionInFlight || chatInFlight) return;
  beginBusy(LOADER_TEXT.letter, "letter");
  setActionLocked(true);
  setActionThinking("Думаю над cover-letter");
  try {
    await ensureSession();
    const vacancy = els.vacancyText.value.trim();
    if (!vacancy) throw new Error("Вставьте текст вакансии");
    setLoaderStep(2);
    const out = await api("/cover-letter", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vacancy_text: vacancy }),
    });
    setLoaderStep(3);
    setLoaderText("Готово. Сопроводительное письмо сформировано.");
    setActionOut(out.letter || out.error || JSON.stringify(out, null, 2));
  } catch (e) {
    setActionOut(`Ошибка cover-letter: ${e && e.message ? e.message : String(e)}`);
  } finally {
    setActionLocked(false);
    endBusy();
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
  const q = els.chatInput.value.trim();
  if (!q) return;
  beginBusy(LOADER_TEXT.chat);
  setChatLocked(true);
  pushThinking();
  try {
    await ensureSession();
    els.chatInput.value = "";
    pushMsg("user", q);
    setLoaderStep(2);
    const enq = await api("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: q }),
    });
    const messageId = enq.message_id;
    const st = await pollMessage(messageId);
    setLoaderStep(3);
    setLoaderText("Готово. Ответ получен.");
    const text = st.assistant_text_translated || st.assistant_text || "(empty)";
    popThinking();
    pushMsg("assistant", text);
  } catch (e) {
    popThinking();
    pushMsg("assistant", `Ошибка: ${e && e.message ? e.message : String(e)}`);
  } finally {
    setChatLocked(false);
    endBusy();
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
  try {
    await ensureSession();
  } catch (e) {
    els.sessionState.textContent = "session: error";
    console.warn(e);
  }
});

