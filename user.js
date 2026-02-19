const passwordInput = document.getElementById("userPassword");
const questionInput = document.getElementById("question");
const askBtn = document.getElementById("ask");
const answerOutput = document.getElementById("answer");

askBtn.addEventListener("click", async () => {
  const password = passwordInput.value.trim();
  const question = questionInput.value.trim();

  if (!password) {
    answerOutput.textContent = "비밀번호를 입력하세요.";
    return;
  }
  if (!question) {
    answerOutput.textContent = "Question을 입력하세요.";
    return;
  }

  answerOutput.textContent = "Thinking...";

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
    answerOutput.textContent = json.content || "No response.";
  } catch (error) {
    answerOutput.textContent = `Failed: ${error.message}`;
  }
});
