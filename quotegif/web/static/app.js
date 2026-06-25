const $ = (id) => document.getElementById(id);

let currentJobId = null;
let pollTimer = null;
let currentUser = null;
let trimState = null;
let trimPreviewToken = null;

const fetchOpts = { credentials: "same-origin" };

function formatDuration(seconds) {
  if (seconds < 1) return `${(seconds * 1000).toFixed(0)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s.toFixed(0)}s`;
}

function hideAllResultStates() {
  $("progress").classList.add("hidden");
  $("input-prompt").classList.add("hidden");
  $("error").classList.add("hidden");
  $("result").classList.add("hidden");
  $("idle-hint").classList.add("hidden");
  $("log-tail").classList.add("hidden");
}

function showIdle() {
  hideAllResultStates();
  $("idle-hint").classList.remove("hidden");
}

function setLoading(active) {
  $("submit-btn").disabled = active;
}

async function requireAuth() {
  const res = await fetch("/api/auth/me", fetchOpts);
  if (!res.ok) {
    window.location.href = "/login.html";
    return false;
  }
  const data = await res.json();
  currentUser = data.username;
  $("user-label").textContent = data.username;
  return true;
}

function buildPayload() {
  const payload = {
    quote: $("quote").value.trim(),
    show: $("show").value.trim() || null,
    episode: $("episode").value.trim() || null,
    movie: $("movie").checked,
    output_format: $("output_format").value,
    candidates: parseInt($("candidates").value, 10) || 5,
    auto_confirm: $("auto_confirm").checked,
    verbose: $("verbose").checked,
  };

  const padBefore = $("pad_before").value;
  const padAfter = $("pad_after").value;
  const maxDuration = $("max_duration").value;
  const fps = $("fps").value;
  const width = $("width").value;
  const around = $("around").value.trim();
  const provider = $("provider").value;
  const model = $("model").value.trim();
  const configPath = $("config_path").value.trim();

  if (padBefore !== "") payload.pad_before = parseFloat(padBefore);
  if (padAfter !== "") payload.pad_after = parseFloat(padAfter);
  if (maxDuration !== "") payload.max_duration = parseFloat(maxDuration);
  if (fps !== "") payload.fps = parseInt(fps, 10);
  if (width !== "") payload.width = parseInt(width, 10);
  if (around) payload.around = around;
  if (provider) payload.provider = provider;
  if (model) payload.model = model;
  if (configPath) payload.config_path = configPath;

  return payload;
}

async function loadConfig() {
  const pill = $("status-pill");
  try {
    const res = await fetch("/api/config", fetchOpts);
    if (res.status === 401) {
      window.location.href = "/login.html";
      return;
    }
    const cfg = await res.json();

    $("pad_before").placeholder = String(cfg.pad_before);
    $("pad_after").placeholder = String(cfg.pad_after);
    $("max_duration").placeholder = String(cfg.max_duration);
    $("fps").placeholder = String(cfg.gif.fps);
    $("width").placeholder = String(cfg.gif.width);

    const providerSelect = $("provider");
    providerSelect.innerHTML = "";
    const defaultOpt = document.createElement("option");
    defaultOpt.value = "";
    defaultOpt.textContent = `Default (${cfg.provider})`;
    providerSelect.appendChild(defaultOpt);

    for (const p of cfg.providers) {
      const opt = document.createElement("option");
      opt.value = p.name;
      const tag = p.configured ? p.model : "not configured";
      opt.textContent = `${p.name} — ${tag}`;
      opt.disabled = !p.configured && p.name !== "ollama";
      providerSelect.appendChild(opt);
    }

    if (cfg.ffmpeg_ok && cfg.media_folders.length > 0) {
      pill.textContent = `Ready · ${cfg.media_folders.length} media folder(s)`;
      pill.dataset.state = "ok";
    } else if (!cfg.ffmpeg_ok) {
      pill.textContent = "ffmpeg missing";
      pill.dataset.state = "err";
    } else {
      pill.textContent = "No media folders configured";
      pill.dataset.state = "warn";
    }
  } catch (e) {
    pill.textContent = "API unreachable";
    pill.dataset.state = "err";
  }
}

function updateGifFields() {
  const isGif = $("output_format").value === "gif";
  document.querySelectorAll(".gif-only").forEach((el) => {
    el.style.display = isGif ? "" : "none";
  });
}

async function startFind(payload) {
  setLoading(true);
  hideAllResultStates();
  $("progress").classList.remove("hidden");
  $("progress-step").textContent = "Starting CLI…";
  $("progress-detail").textContent = "";

  const res = await fetch("/api/find", {
    method: "POST",
    ...fetchOpts,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (res.status === 401) {
    window.location.href = "/login.html";
    return;
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }

  const job = await res.json();
  currentJobId = job.id;
  if (job.cli_command) {
    $("progress-detail").textContent = job.cli_command;
  }
  pollJob(job.id);
}

async function continueJob(body) {
  setLoading(true);
  $("input-prompt").classList.add("hidden");
  $("progress").classList.remove("hidden");

  const res = await fetch(`/api/jobs/${currentJobId}/continue`, {
    method: "POST",
    ...fetchOpts,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }

  pollJob(currentJobId);
}

function pollJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);

  const tick = async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`, fetchOpts);
      if (!res.ok) throw new Error("Job not found");
      const job = await res.json();
      handleJobUpdate(job);
    } catch (e) {
      clearInterval(pollTimer);
      setLoading(false);
      showError(e.message);
    }
  };

  tick();
  pollTimer = setInterval(tick, 1200);
}

function handleJobUpdate(job) {
  if (job.status === "queued" || job.status === "running") {
    $("progress-step").textContent = job.progress_step || job.status;
    const detail = job.cli_command || job.progress_detail || "";
    $("progress-detail").textContent = detail;
    if (job.log_tail?.length) {
      $("log-tail").classList.remove("hidden");
      $("log-tail").textContent = job.log_tail.slice(-8).join("\n");
    }
    return;
  }

  clearInterval(pollTimer);
  pollTimer = null;
  setLoading(false);

  if (job.status === "awaiting_input") {
    showInputPrompt(job);
    return;
  }

  if (job.status === "failed") {
    showError(job.error || "Job failed");
    if (job.log_tail?.length) {
      $("log-tail").classList.remove("hidden");
      $("log-tail").textContent = job.log_tail.join("\n");
    }
    return;
  }

  if (job.status === "completed") {
    showResult(job.result);
  }
}

function showError(message) {
  hideAllResultStates();
  $("error").textContent = message;
  $("error").classList.remove("hidden");
}

function showInputPrompt(job) {
  hideAllResultStates();
  const wrap = $("input-prompt");
  wrap.classList.remove("hidden");
  wrap.innerHTML = "";

  const p = document.createElement("p");
  p.textContent = job.input?.message || "Input required";
  wrap.appendChild(p);

  const kind = job.input?.kind;

  if (kind === "low_confidence") {
    const btn = document.createElement("button");
    btn.className = "primary";
    btn.textContent = "Proceed anyway (--yes)";
    btn.onclick = () => continueJob({ auto_confirm: true });
    wrap.appendChild(btn);
    return;
  }

  if (kind === "file_pick" && job.input?.file_candidates?.length) {
    const ul = document.createElement("ul");
    ul.className = "candidate-list";
    job.input.file_candidates.forEach((c) => {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = `${c.label} — ${c.path}`;
      btn.onclick = () => continueJob({ media_path: c.path, auto_confirm: true });
      li.appendChild(btn);
      ul.appendChild(li);
    });
    wrap.appendChild(ul);
  }
}

function showResult(result) {
  hideAllResultStates();
  $("result").classList.remove("hidden");

  const meta = $("meta");
  meta.innerHTML = `
    <div><strong>Output</strong></div>
    <div>${escapeHtml(result.output_path || "")}</div>
    <div>Format: ${escapeHtml(result.output_format || "")}</div>
  `;

  const preview = $("preview");
  preview.innerHTML = "";
  const url = result.output_url;

  if (result.output_format === "clip") {
    const video = document.createElement("video");
    video.src = url;
    video.controls = true;
    video.playsInline = true;
    preview.appendChild(video);
  } else {
    const img = document.createElement("img");
    img.src = url;
    img.alt = "Rendered GIF";
    preview.appendChild(img);
  }

  $("download-btn").href = result.download_url || `${url}?download=1`;
  loadHistory();
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function showPreviewFromUrls(url, outputFormat, metaHtml) {
  hideAllResultStates();
  $("result").classList.remove("hidden");
  $("meta").innerHTML = metaHtml;
  const preview = $("preview");
  preview.innerHTML = "";
  if (outputFormat === "clip") {
    const video = document.createElement("video");
    video.src = url;
    video.controls = true;
    video.playsInline = true;
    preview.appendChild(video);
  } else {
    const img = document.createElement("img");
    img.src = url;
    img.alt = "Rendered GIF";
    preview.appendChild(img);
  }
}

async function loadHistory() {
  const list = $("history-list");
  try {
    const res = await fetch("/api/history?limit=100", fetchOpts);
    if (!res.ok) throw new Error("Failed to load history");
    const data = await res.json();
    $("history-output-dir").textContent = `Your files: ${data.output_dir}`;
    if (!data.items.length) {
      list.innerHTML = '<p class="idle-hint">No queries yet.</p>';
      return;
    }
    list.innerHTML = "";
    for (const item of data.items) {
      const el = document.createElement("article");
      el.className = "history-item";
      if (item.id === currentJobId) el.classList.add("is-active");

      const ep = [item.show, item.episode].filter(Boolean).join(" · ");
      const editNote = item.edit_summary ? ` · ${item.edit_summary}` : "";
      const parentNote = item.parent_id ? '<span class="history-badge">edited</span>' : "";
      const sub = [ep, formatDate(item.created_at), item.output_format || ""]
        .filter(Boolean)
        .join(" · ");

      el.innerHTML = `
        <div class="history-item-top">
          <div>
            <p class="history-quote">“${escapeHtml(item.quote)}”${parentNote}</p>
            <p class="history-sub">${escapeHtml(sub)}${escapeHtml(editNote)}</p>
          </div>
          <span class="history-status ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span>
        </div>
      `;

      if (item.status === "completed" && item.output_url) {
        const actions = document.createElement("div");
        actions.className = "history-actions";
        const viewBtn = document.createElement("button");
        viewBtn.type = "button";
        viewBtn.className = "ghost-btn";
        viewBtn.textContent = "View";
        viewBtn.onclick = () => {
          document.querySelectorAll(".history-item").forEach((n) => n.classList.remove("is-active"));
          el.classList.add("is-active");
          const metaParts = [escapeHtml(item.quote), escapeHtml(sub)];
          if (item.edit_summary) metaParts.push(escapeHtml(item.edit_summary));
          showPreviewFromUrls(
            item.output_url,
            item.output_format,
            metaParts.map((p) => `<div>${p}</div>`).join("")
          );
          $("download-btn").href = item.download_url;
        };
        const editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "ghost-btn";
        editBtn.textContent = "Trim";
        editBtn.onclick = () => openTrimEditor(item.id);
        const dl = document.createElement("a");
        dl.className = "button";
        dl.href = item.download_url;
        dl.textContent = "Download";
        dl.download = "";
        actions.appendChild(viewBtn);
        if (item.can_edit) actions.appendChild(editBtn);
        actions.appendChild(dl);
        el.appendChild(actions);
      } else if (item.error) {
        const err = document.createElement("p");
        err.className = "history-sub";
        err.textContent = item.error;
        el.appendChild(err);
      }

      list.appendChild(el);
    }
  } catch (e) {
    list.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`;
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function clampTrimValue(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function setTrimError(message) {
  const el = $("trim-error");
  if (!message) {
    el.textContent = "";
    el.classList.add("hidden");
    return;
  }
  el.textContent = message;
  el.classList.remove("hidden");
}

function updateTrimUi() {
  if (!trimState) return;

  const { duration, trimStart, trimEnd } = trimState;
  const startPct = (trimStart / duration) * 100;
  const endPct = (trimEnd / duration) * 100;

  $("trim-range-start").max = String(duration);
  $("trim-range-end").max = String(duration);
  $("trim-range-start").value = String(trimStart);
  $("trim-range-end").value = String(trimEnd);
  $("trim-input-start").max = String(duration);
  $("trim-input-end").max = String(duration);
  $("trim-input-start").value = trimStart.toFixed(2);
  $("trim-input-end").value = trimEnd.toFixed(2);

  const bar = $("trim-selection-bar");
  bar.style.left = `${startPct}%`;
  bar.style.width = `${Math.max(0, endPct - startPct)}%`;

  $("trim-duration-label").textContent = `Source: ${formatDuration(duration)}`;
  $("trim-selection-label").textContent =
    `Selection: ${formatDuration(trimStart)} → ${formatDuration(trimEnd)} (${formatDuration(trimEnd - trimStart)})`;

  renderTrimSourcePreview();
  trimPreviewToken = null;
  $("trim-result-preview").classList.add("hidden");
  $("trim-result-preview").innerHTML = "";
}

function renderTrimSourcePreview() {
  if (!trimState) return;
  const wrap = $("trim-source-preview");
  wrap.innerHTML = "";
  const { sourceUrl, outputFormat, trimStart, trimEnd } = trimState;

  if (outputFormat === "clip") {
    const video = document.createElement("video");
    video.src = `${sourceUrl}#t=${trimStart.toFixed(2)},${trimEnd.toFixed(2)}`;
    video.controls = true;
    video.playsInline = true;
    wrap.appendChild(video);
  } else {
    const hint = document.createElement("p");
    hint.className = "idle-hint";
    hint.textContent = "Use Preview trim to see the shortened GIF.";
    wrap.appendChild(hint);
  }
}

function applyTrimStart(value) {
  if (!trimState) return;
  const next = clampTrimValue(value, 0, trimState.trimEnd - 0.1);
  trimState.trimStart = next;
  updateTrimUi();
}

function applyTrimEnd(value) {
  if (!trimState) return;
  const next = clampTrimValue(value, trimState.trimStart + 0.1, trimState.duration);
  trimState.trimEnd = next;
  updateTrimUi();
}

function closeTrimEditor() {
  trimState = null;
  trimPreviewToken = null;
  setTrimError("");
  $("trim-modal").classList.add("hidden");
  $("trim-modal").setAttribute("aria-hidden", "true");
  $("trim-result-preview").classList.add("hidden");
  $("trim-result-preview").innerHTML = "";
  $("trim-source-preview").innerHTML = "";
}

async function openTrimEditor(historyId) {
  setTrimError("");
  $("trim-save-btn").disabled = true;
  $("trim-preview-btn").disabled = true;
  $("trim-modal-quote").textContent = "Loading…";
  $("trim-modal").classList.remove("hidden");
  $("trim-modal").setAttribute("aria-hidden", "false");

  try {
    const res = await fetch(`/api/history/${historyId}/edit`, fetchOpts);
    if (res.status === 401) {
      window.location.href = "/login.html";
      return;
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    const info = await res.json();
    trimState = {
      historyId,
      duration: info.duration,
      trimStart: 0,
      trimEnd: info.duration,
      outputFormat: info.output_format,
      sourceUrl: info.source_url,
      quote: info.quote,
    };
    $("trim-modal-quote").textContent = `“${info.quote}” · ${info.filename}`;
    updateTrimUi();
    $("trim-save-btn").disabled = false;
    $("trim-preview-btn").disabled = false;
  } catch (e) {
    setTrimError(e.message);
    $("trim-modal-quote").textContent = "Could not open editor";
  }
}

function trimPayload() {
  return {
    trim_start: trimState.trimStart,
    trim_end: trimState.trimEnd,
  };
}

async function previewTrim() {
  if (!trimState) return;
  setTrimError("");
  $("trim-preview-btn").disabled = true;
  try {
    const res = await fetch(`/api/history/${trimState.historyId}/trim/preview`, {
      method: "POST",
      ...fetchOpts,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(trimPayload()),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    trimPreviewToken = data.preview_token;
    const wrap = $("trim-result-preview");
    wrap.classList.remove("hidden");
    wrap.innerHTML = "";
    if (data.output_format === "clip") {
      const video = document.createElement("video");
      video.src = `${data.preview_url}?t=${Date.now()}`;
      video.controls = true;
      video.playsInline = true;
      wrap.appendChild(video);
    } else {
      const img = document.createElement("img");
      img.src = `${data.preview_url}?t=${Date.now()}`;
      img.alt = "Trim preview";
      wrap.appendChild(img);
    }
  } catch (e) {
    setTrimError(e.message);
  } finally {
    $("trim-preview-btn").disabled = false;
  }
}

async function saveTrim() {
  if (!trimState) return;
  setTrimError("");
  $("trim-save-btn").disabled = true;
  try {
    const res = await fetch(`/api/history/${trimState.historyId}/trim`, {
      method: "POST",
      ...fetchOpts,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(trimPayload()),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    closeTrimEditor();
    await loadHistory();
    const item = data.item;
    if (item?.output_url) {
      showPreviewFromUrls(
        item.output_url,
        item.output_format,
        `<div><strong>${escapeHtml(item.quote)}</strong></div>
         <div>${escapeHtml(data.message || item.edit_summary || "Trim saved")}</div>`
      );
      $("download-btn").href = item.download_url;
    }
  } catch (e) {
    setTrimError(e.message);
  } finally {
    $("trim-save-btn").disabled = false;
  }
}

$("find-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await startFind(buildPayload());
  } catch (err) {
    setLoading(false);
    showError(err.message);
  }
});

$("logout-btn").addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST", ...fetchOpts });
  window.location.href = "/login.html";
});

$("output_format").addEventListener("change", updateGifFields);
$("refresh-history-btn").addEventListener("click", () => loadHistory());

$("trim-close-btn").addEventListener("click", closeTrimEditor);
$("trim-modal").addEventListener("click", (e) => {
  if (e.target === $("trim-modal")) closeTrimEditor();
});
$("trim-range-start").addEventListener("input", (e) => {
  applyTrimStart(parseFloat(e.target.value));
});
$("trim-range-end").addEventListener("input", (e) => {
  applyTrimEnd(parseFloat(e.target.value));
});
$("trim-input-start").addEventListener("change", (e) => {
  applyTrimStart(parseFloat(e.target.value) || 0);
});
$("trim-input-end").addEventListener("change", (e) => {
  applyTrimEnd(parseFloat(e.target.value) || trimState?.duration || 0);
});
$("trim-preview-btn").addEventListener("click", () => previewTrim());
$("trim-save-btn").addEventListener("click", () => saveTrim());

(async () => {
  if (await requireAuth()) {
    await loadConfig();
    updateGifFields();
    showIdle();
    loadHistory();
  }
})();
