const passwordInput = document.getElementById("userPassword");
const attachImageBtn = document.getElementById("attachImage");
const imageFileInput = document.getElementById("imageFile");
const questionInput = document.getElementById("question");
const askBtn = document.getElementById("ask");
const messagesEl = document.getElementById("messages");
let selectedImageDataUrl = "";
let selectedImageName = "";
const conversationHistory = [];
const MAX_HISTORY_MESSAGES = 20;
let isComposing = false;

askBtn.addEventListener("click", sendQuestion);
attachImageBtn.addEventListener("click", () => imageFileInput.click());
imageFileInput.addEventListener("change", async () => {
  const file = imageFileInput.files?.[0];
  if (!file) {
    return;
  }
  try {
    selectedImageDataUrl = await fileToDataUrl(file);
    selectedImageName = file.name;
    appendBotMessage(`이미지 첨부됨: ${file.name}`);
  } catch (error) {
    selectedImageDataUrl = "";
    selectedImageName = "";
    appendBotMessage(`이미지 처리 실패: ${error.message}`);
  }
});

questionInput.addEventListener("compositionstart", () => {
  isComposing = true;
});
questionInput.addEventListener("compositionend", () => {
  isComposing = false;
});

questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !isComposing && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    sendQuestion();
  }
});

async function sendQuestion() {
  const password = passwordInput.value.trim();
  const typedQuestion = questionInput.value.trim();
  const question = typedQuestion || (selectedImageDataUrl ? "Describe this image." : "");

  if (!password) {
    appendBotMessage("비밀번호를 입력하세요.");
    return;
  }
  if (!question) {
    appendBotMessage("질문을 입력하세요.");
    return;
  }

  appendUserMessage(question, selectedImageDataUrl);
  questionInput.value = "";
  imageFileInput.value = "";
  const imageDataUrlToSend = selectedImageDataUrl;
  selectedImageDataUrl = "";
  selectedImageName = "";
  const typingNode = appendTyping();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User-Password": password,
      },
      body: JSON.stringify({
        user: question,
        kb: { enabled: true, query: question, topK: 6 },
        imageDataUrl: imageDataUrlToSend || undefined,
        history: conversationHistory,
      }),
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${res.status} ${text}`);
    }

    const json = await res.json();
    pushHistory("user", question);
    pushHistory("assistant", json.content || "No response.");
    removeTyping(typingNode);
    appendBotMessage(json.content || "No response.", {
      allowFeedback: true,
      question,
    });
  } catch (error) {
    removeTyping(typingNode);
    appendBotMessage(`Failed: ${error.message}`);
  }
}

function appendUserMessage(text, imageDataUrl) {
  const row = document.createElement("div");
  row.className = "msg-row user";
  row.innerHTML = `<div class="bubble user-bubble"></div>`;
  const bubble = row.querySelector(".bubble");
  bubble.textContent = text;
  if (imageDataUrl) {
    const img = document.createElement("img");
    img.src = imageDataUrl;
    img.alt = selectedImageName || "첨부 이미지";
    img.className = "chat-image";
    bubble.appendChild(document.createElement("br"));
    bubble.appendChild(img);
  }
  messagesEl.appendChild(row);
  scrollToBottom();
}

function appendBotMessage(text, options = {}) {
  const { allowFeedback = false, question = "" } = options;
  const row = document.createElement("div");
  row.className = "msg-row bot";
  row.innerHTML =
    `<img class="avatar-img" src="lee.JPG" alt="챗봇 아바타" />` +
    `<div class="bot-wrap"><div class="bubble bot-bubble"></div></div>`;
  const bubble = row.querySelector(".bubble");
  bubble.textContent = text;
  if (allowFeedback) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "feedback-btn";
    btn.textContent = "수정요청";
    btn.addEventListener("click", () => submitFeedback(question, text));
    row.querySelector(".bot-wrap").appendChild(btn);
  }
  messagesEl.appendChild(row);
  scrollToBottom();
}

function appendTyping() {
  const row = document.createElement("div");
  row.className = "typing-row";
  row.textContent = "이준서 교수 고민중...";
  messagesEl.appendChild(row);
  scrollToBottom();
  return row;
}

function removeTyping(node) {
  if (node && node.parentNode) {
    node.parentNode.removeChild(node);
  }
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function pushHistory(role, content) {
  conversationHistory.push({ role, content });
  if (conversationHistory.length > MAX_HISTORY_MESSAGES) {
    conversationHistory.splice(0, conversationHistory.length - MAX_HISTORY_MESSAGES);
  }
}

async function submitFeedback(question, badAnswer) {
  const password = passwordInput.value.trim();
  if (!password) {
    appendBotMessage("수정요청 전 비밀번호를 입력하세요.");
    return;
  }
  const correction = window.prompt("어떤 내용이 틀렸는지, 정답을 입력해 주세요.");
  if (!correction || !correction.trim()) {
    return;
  }
  try {
    const res = await fetch("/api/feedback", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User-Password": password,
      },
      body: JSON.stringify({
        question,
        badAnswer,
        correction: correction.trim(),
      }),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${res.status} ${text}`);
    }
    appendBotMessage("수정요청이 저장되었습니다. 다음 답변부터 반영됩니다.");
  } catch (error) {
    appendBotMessage(`수정요청 저장 실패: ${error.message}`);
  }
}

async function fileToDataUrl(file) {
  if (!file.type.startsWith("image/")) {
    throw new Error("이미지 파일만 업로드할 수 있습니다.");
  }
  const maxSize = 4 * 1024 * 1024;
  if (file.size > maxSize) {
    throw new Error("이미지는 4MB 이하만 가능합니다.");
  }
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("이미지를 읽을 수 없습니다."));
    reader.readAsDataURL(file);
  });
}
