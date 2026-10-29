function safeStorage(fn, fallback) { try { return fn(); } catch (e) { return fallback; } }

async function startGeneration(query, force, msgEl, btn) {
  if (btn) btn.disabled = true;
  if (msgEl) { msgEl.className = "hint"; msgEl.textContent = "Starting…"; }
  try {
    const r = await fetch("/api/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, force: !!force }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
    location.href = data.slug ? `/g/${data.slug}` : `/generate/${data.job_id}`;
  } catch (e) {
    if (msgEl) { msgEl.className = "hint err"; msgEl.textContent = e.message; }
    else alert(e.message);
    if (btn) btn.disabled = false;
  }
}

function initGenerateForm() {
  const form = document.getElementById("gen-form");
  const q = document.getElementById("q");
  const prefill = new URLSearchParams(location.search).get("q");
  if (prefill) q.value = prefill;
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    startGeneration(q.value.trim(), false, document.getElementById("gen-msg"), form.querySelector("button"));
  });
}

function initGuidePage() {
  const slug = document.querySelector(".guide").dataset.slug;
  const key = `runway:${slug}:steps`;
  const done = new Set(safeStorage(() => JSON.parse(localStorage.getItem(key) || "[]"), []));
  document.querySelectorAll("input[data-step]").forEach((cb) => {
    const li = cb.closest(".step");
    cb.checked = done.has(cb.dataset.step);
    li.classList.toggle("done", cb.checked);
    cb.addEventListener("change", () => {
      cb.checked ? done.add(cb.dataset.step) : done.delete(cb.dataset.step);
      li.classList.toggle("done", cb.checked);
      safeStorage(() => localStorage.setItem(key, JSON.stringify([...done])));
    });
  });
  document.querySelectorAll("[data-copy]").forEach((el) => {
    el.title = "Click to copy";
    el.addEventListener("click", () => {
      navigator.clipboard?.writeText(el.textContent.trim());
      el.classList.add("copied"); setTimeout(() => el.classList.remove("copied"), 900);
    });
  });
  const linkBtn = document.querySelector("[data-copy-link]");
  linkBtn?.addEventListener("click", () => {
    navigator.clipboard?.writeText(location.origin + location.pathname);
    linkBtn.textContent = "Copied"; setTimeout(() => (linkBtn.textContent = "Copy link"), 1200);
  });
  const regen = document.querySelector("[data-regenerate]");
  regen?.addEventListener("click", () => {
    if (confirm("Start a fresh agent research run for this app? The result is saved as a separate agent-generated guide.")) {
      startGeneration(regen.dataset.regenerate, true, null, regen);
    }
  });
}

const KIND_LABEL = {
  start: "start", turn: "thinking", search: "search", results: "results", fetch: "read",
  probe: "probe", probe_result: "probe result", composio: "composio", thinking: "reasoning",
  note: "note", drafting: "drafting", validating: "validating", warning: "warning", done: "done", error: "error",
  escalate: "escalating", findings: "findings",
};

function initLive(jobId) {
  const log = document.getElementById("log");
  const end = document.getElementById("live-end");
  const counts = { search: 0, fetch: 0, probe: 0 };
  const t0 = Date.now();
  const timer = setInterval(() => (document.getElementById("n-time").textContent = Math.round((Date.now() - t0) / 1000) + "s"), 1000);
  end.hidden = false;
  end.innerHTML = '<span class="spinner"></span>Working…';

  const es = new EventSource(`/api/jobs/${jobId}/events`);
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "turn") return;
    if (counts[ev.type] !== undefined) {
      counts[ev.type]++;
      document.getElementById(`n-${ev.type}`).textContent = counts[ev.type];
    }
    const li = document.createElement("li");
    li.className = `k-${ev.type}`;
    li.innerHTML = `<span class="t"></span><span class="k k-${ev.type}"></span><span class="x"></span>`;
    li.querySelector(".t").textContent = `${ev.t ?? ""}s`;
    li.querySelector(".k").textContent = KIND_LABEL[ev.type] || ev.type;
    li.querySelector(".x").textContent = ev.text || "";
    log.appendChild(li);
    log.scrollTop = log.scrollHeight;
    if (ev.type === "done") {
      end.innerHTML = `Guide ready. <a href="/g/${ev.slug}">Open the guide →</a>`;
      setTimeout(() => (location.href = `/g/${ev.slug}`), 1500);
    }
    if (ev.type === "error") {
      end.innerHTML = `<b>Research failed:</b> <span class="muted"></span> <a href="/">Back to search</a>`;
      end.querySelector(".muted").textContent = ev.text;
    }
  };
  es.addEventListener("end", () => { es.close(); clearInterval(timer); });
  es.onerror = () => { if (es.readyState === EventSource.CLOSED) clearInterval(timer); };
}
