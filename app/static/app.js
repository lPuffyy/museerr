
// ==============================
// Spotiseerr Enhanced Frontend
// ==============================

document.addEventListener("DOMContentLoaded", () => {
  console.log("🎧 Spotiseerr Frontend Ready");

  // ------------------------------
  // Helpers
  // ------------------------------
  function trackProgress(taskId) {
    const ws = new WebSocket(
      (location.protocol === "https:" ? "wss://" : "ws://") +
        location.host +
        "/ws/" +
        taskId
    );

    ws.onmessage = (ev) => {
      const data = JSON.parse(ev.data);
      const bar = document.querySelector(`#progress-${taskId} .bar`);
      const pct = document.querySelector(`#progress-${taskId} .pct`);
      const wrapper = document.getElementById(`progress-${taskId}`);

      if (data.progress !== undefined && bar && pct) {
        const p = Math.max(0, Math.min(100, data.progress));
        bar.style.width = p + "%";
        pct.textContent = Math.round(p) + "%";

        // unhide the progress element if still hidden
        if (wrapper && wrapper.hasAttribute("hidden")) {
          wrapper.removeAttribute("hidden");
        }
      }

      if (data.status === "completed" && bar && pct) {
        bar.style.width = "100%";
        pct.textContent = "100%";
        if (wrapper) wrapper.classList.add("done");
        setTimeout(() => ws.close(), 1500);
      }

      if (data.status === "failed" && wrapper) {
        wrapper.classList.add("error");
        if (pct) pct.textContent = "Failed";
        ws.close();
      }
    };

    ws.onclose = () => console.log(`🔌 WebSocket closed for ${taskId}`);
  }

  // ------------------------------
  // Single Track Download
  // ------------------------------
  document.querySelectorAll(".dl-form").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = form.querySelector("button[type=submit]");
      const progress = form.querySelector(".progress");
      const bar = form.querySelector(".bar");
      const pct = form.querySelector(".pct");

      if (progress) {
        progress.hidden = false;
        if (bar) bar.style.width = "0%";
        if (pct) pct.textContent = "0%";
      }

      btn.disabled = true;
      const formData = new FormData(form);

      try {
        const res = await fetch("/download_track", { method: "POST", body: formData });
        const json = await res.json();
        const taskId = json.task_id;
        if (progress) progress.id = `progress-${taskId}`;
        trackProgress(taskId);
      } catch (err) {
        console.error("Download error:", err);
        btn.disabled = false;
      }
    });
  });

  // ------------------------------
  // Album Downloads
  // ------------------------------
  document.querySelectorAll(".dl-album").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = form.querySelector("button[type=submit]");
      const progress = form.querySelector(".progress");
      const bar = form.querySelector(".bar");
      const pct = form.querySelector(".pct");

      if (progress) {
        progress.hidden = false;
        if (bar) bar.style.width = "0%";
        if (pct) pct.textContent = "0%";
      }

      btn.disabled = true;
      const formData = new FormData(form);

      try {
        const res = await fetch("/download_album", { method: "POST", body: formData });
        const json = await res.json();

        if (json.tasks && Array.isArray(json.tasks)) {
          let count = 0;
          json.tasks.forEach((taskId) => {
            if (progress) progress.id = `progress-${taskId}-${count++}`;
            trackProgress(taskId);
          });
        }
      } catch (err) {
        console.error("Album download error:", err);
        btn.disabled = false;
      }
    });
  });

  // ------------------------------
  // Artist Downloads
  // ------------------------------
  document.querySelectorAll(".dl-artist").forEach((form) => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = form.querySelector("button[type=submit]");
      const progress = form.querySelector(".progress");
      const bar = form.querySelector(".bar");
      const pct = form.querySelector(".pct");

      if (progress) {
        progress.hidden = false;
        if (bar) bar.style.width = "0%";
        if (pct) pct.textContent = "0%";
      }

      btn.disabled = true;
      const formData = new FormData(form);

      try {
        const res = await fetch("/download_artist", { method: "POST", body: formData });
        const json = await res.json();

        if (json.tasks && Array.isArray(json.tasks)) {
          let count = 0;
          json.tasks.forEach((taskId) => {
            if (progress) progress.id = `progress-${taskId}-${count++}`;
            trackProgress(taskId);
          });
        }
      } catch (err) {
        console.error("Artist download error:", err);
        btn.disabled = false;
      }
    });
  });

  // ------------------------------
  // Global refresh for active tasks
  // ------------------------------
  async function refreshActive() {
    try {
      const res = await fetch("/status");
      const data = await res.json();
      if (data.active && data.active.length) {
        data.active.forEach(trackProgress);
      }
    } catch (e) {
      console.warn("Status check failed:", e);
    }
  }

  refreshActive();
  setInterval(refreshActive, 10000); // auto-reconnect every 10s
});
