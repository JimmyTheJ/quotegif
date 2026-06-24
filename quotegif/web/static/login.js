const $ = (id) => document.getElementById(id);

async function tryExistingSession() {
  const res = await fetch("/api/auth/me", { credentials: "same-origin" });
  if (res.ok) {
    window.location.href = "/";
  }
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = $("login-error");
  errEl.classList.add("hidden");
  $("login-btn").disabled = true;

  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: $("username").value.trim(),
        password: $("password").value,
      }),
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || res.statusText);
    }

    window.location.href = "/";
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  } finally {
    $("login-btn").disabled = false;
  }
});

tryExistingSession();
