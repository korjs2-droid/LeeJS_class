const tokenInput = document.getElementById("adminToken");
const filesInput = document.getElementById("files");
const uploadBtn = document.getElementById("upload");
const refreshBtn = document.getElementById("refresh");
const statusOutput = document.getElementById("status");
const tabUploadBtn = document.getElementById("tabUpload");
const tabLimitBtn = document.getElementById("tabLimit");
const tabPromptBtn = document.getElementById("tabPrompt");
const panelUpload = document.getElementById("panelUpload");
const panelLimit = document.getElementById("panelLimit");
const panelPrompt = document.getElementById("panelPrompt");
const maxAnswerCharsInput = document.getElementById("maxAnswerChars");
const saveLimitBtn = document.getElementById("saveLimit");
const defaultPromptInput = document.getElementById("defaultPrompt");
const savePromptBtn = document.getElementById("savePrompt");
const downloadFeedbackBtn = document.getElementById("downloadFeedback");

refreshBtn.addEventListener("click", loadStatus);
uploadBtn.addEventListener("click", uploadFiles);
tabUploadBtn.addEventListener("click", () => setTab("upload"));
tabLimitBtn.addEventListener("click", () => setTab("limit"));
tabPromptBtn.addEventListener("click", () => setTab("prompt"));
saveLimitBtn.addEventListener("click", saveLimit);
savePromptBtn.addEventListener("click", savePrompt);
downloadFeedbackBtn.addEventListener("click", downloadFeedback);

loadStatus();
loadConfig();

async function loadStatus() {
  const token = tokenInput.value.trim();
  if (!token) {
    statusOutput.textContent = "Admin Token을 입력한 뒤 Refresh를 누르세요.";
    return;
  }

  try {
    const res = await fetch("/api/admin/kb-status", {
      headers: { "X-Admin-Token": token },
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${res.status} ${text}`);
    }
    const json = await res.json();
    statusOutput.textContent = `Chunks: ${json.chunks}\nFiles:\n- ${json.files.join("\n- ") || "(none)"}`;
  } catch (error) {
    statusOutput.textContent = `Failed: ${error.message}`;
  }
}

async function loadConfig() {
  const token = tokenInput.value.trim();
  if (!token) {
    return;
  }
  try {
    const res = await fetch("/api/admin/config", {
      headers: { "X-Admin-Token": token },
    });
    if (!res.ok) {
      return;
    }
    const json = await res.json();
    maxAnswerCharsInput.value = Number(json.maxAnswerChars || 0);
    defaultPromptInput.value = String(json.defaultPrompt || "");
  } catch (_error) {
    // Keep UI usable even if config fetch fails.
  }
}

async function uploadFiles() {
  const token = tokenInput.value.trim();
  const files = [...filesInput.files];

  if (!token) {
    statusOutput.textContent = "Admin Token을 먼저 입력하세요.";
    return;
  }
  if (!files.length) {
    statusOutput.textContent = "업로드할 파일을 선택하세요.";
    return;
  }

  statusOutput.textContent = `Uploading ${files.length} file(s)...`;

  let done = 0;
  const failures = [];

  for (const file of files) {
    try {
      await uploadSingleFile(token, file);
      done += 1;
    } catch (error) {
      failures.push(`${file.name}: ${error.message}`);
    }
  }

  filesInput.value = "";
  await loadStatus();

  if (failures.length) {
    statusOutput.textContent += `\n\nUploaded: ${done}/${files.length}\nFailed:\n- ${failures.join("\n- ")}`;
  }
}

async function uploadSingleFile(token, file) {
  try {
    const res = await fetch("/api/admin/upload-binary", {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Admin-Token": token,
        "X-Filename": encodeURIComponent(file.name),
      },
      body: file,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${res.status} ${text}`);
    }
    return;
  } catch (_binaryError) {
    // Some environments intermittently fail with stream uploads.
    const contentBase64 = await fileToBase64(file);
    const res = await fetch("/api/admin/upload", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Token": token,
      },
      body: JSON.stringify({
        filename: file.name,
        contentBase64,
      }),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${res.status} ${text}`);
    }
  }
}

async function saveLimit() {
  const token = tokenInput.value.trim();
  if (!token) {
    statusOutput.textContent = "Admin Token을 먼저 입력하세요.";
    return;
  }

  const value = Number(maxAnswerCharsInput.value || "0");
  if (!Number.isInteger(value) || value < 0 || value > 20000) {
    statusOutput.textContent = "MAX_ANSWER_CHARS는 0~20000 정수만 가능합니다.";
    return;
  }

  try {
    const res = await fetch("/api/admin/config", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Token": token,
      },
      body: JSON.stringify({ maxAnswerChars: value }),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${res.status} ${text}`);
    }
    const json = await res.json();
    statusOutput.textContent = `MAX_ANSWER_CHARS updated: ${json.maxAnswerChars}`;
  } catch (error) {
    statusOutput.textContent = `Failed: ${error.message}`;
  }
}

async function savePrompt() {
  const token = tokenInput.value.trim();
  const prompt = defaultPromptInput.value.trim();

  if (!token) {
    statusOutput.textContent = "Admin Token을 먼저 입력하세요.";
    return;
  }
  if (!prompt) {
    statusOutput.textContent = "Prompt를 입력하세요.";
    return;
  }

  try {
    const res = await fetch("/api/admin/config", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Token": token,
      },
      body: JSON.stringify({ defaultPrompt: prompt }),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${res.status} ${text}`);
    }
    statusOutput.textContent = "Default prompt updated.";
  } catch (error) {
    statusOutput.textContent = `Failed: ${error.message}`;
  }
}

async function downloadFeedback() {
  const token = tokenInput.value.trim();
  if (!token) {
    statusOutput.textContent = "Admin Token을 먼저 입력하세요.";
    return;
  }
  try {
    const res = await fetch("/api/admin/feedback-download", {
      headers: { "X-Admin-Token": token },
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${res.status} ${text}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "feedback.jsonl";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    statusOutput.textContent = "feedback.jsonl 다운로드 완료";
  } catch (error) {
    statusOutput.textContent = `다운로드 실패: ${error.message}`;
  }
}

function setTab(tab) {
  const uploadActive = tab === "upload";
  const limitActive = tab === "limit";
  const promptActive = tab === "prompt";
  tabUploadBtn.classList.toggle("active", uploadActive);
  tabLimitBtn.classList.toggle("active", limitActive);
  tabPromptBtn.classList.toggle("active", promptActive);
  panelUpload.classList.toggle("hidden", !uploadActive);
  panelLimit.classList.toggle("hidden", !limitActive);
  panelPrompt.classList.toggle("hidden", !promptActive);
}

async function fileToBase64(file) {
  const arrayBuffer = await file.arrayBuffer();
  const bytes = new Uint8Array(arrayBuffer);
  let binary = "";
  const chunkSize = 0x8000;

  for (let i = 0; i < bytes.length; i += chunkSize) {
    const sub = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode(...sub);
  }

  return btoa(binary);
}
