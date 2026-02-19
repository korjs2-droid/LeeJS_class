const passwordInput = document.getElementById("userPassword");
const questionInput = document.getElementById("question");
const askBtn = document.getElementById("ask");
const messagesEl = document.getElementById("messages");

askBtn.addEventListener("click", sendQuestion);
questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    sendQuestion();
  }
});

async function sendQuestion() {
  const password = passwordInput.value.trim();
  const question = questionInput.value.trim();

  if (!password) {
    appendBotMessage("비밀번호를 입력하세요.");
    return;
  }
  if (!question) {
    appendBotMessage("질문을 입력하세요.");
    return;
  }

  appendUserMessage(question);
  questionInput.value = "";
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

function appendUserMessage(text) {
  const row = document.createElement("div");
  row.className = "msg-row user";
  row.innerHTML = `<div class="bubble user-bubble"></div>`;
  row.querySelector(".bubble").textContent = text;
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
