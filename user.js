const passwordInput = document.getElementById("userPassword");
const attachImageBtn = document.getElementById("attachImage");
const imageFileInput = document.getElementById("imageFile");
const questionInput = document.getElementById("question");
const askBtn = document.getElementById("ask");
const messagesEl = document.getElementById("messages");
let selectedImageDataUrl = "";
let selectedImageName = "";

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

questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
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
      }),
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${res.status} ${text}`);
    }

    const json = await res.json();
    removeTyping(typingNode);
    appendBotMessage(json.content || "No response.");
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

function appendBotMessage(text) {
  const row = document.createElement("div");
  row.className = "msg-row bot";
  row.innerHTML = `<img class="avatar-img" src="lee.JPG" alt="챗봇 아바타" /><div class="bubble bot-bubble"></div>`;
  row.querySelector(".bubble").textContent = text;
  messagesEl.appendChild(row);
  scrollToBottom();
}

function appendTyping() {
  const row = document.createElement("div");
  row.className = "typing-row";
  row.textContent = "답변 생성 중...";
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
