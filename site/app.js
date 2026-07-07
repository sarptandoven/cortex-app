(function () {
  const fallbackRelease = {
    version: "0.2.0",
    build: "11",
    channel: "local-beta",
    artifacts: [
      {
        kind: "dmg",
        filename: "Cortex-0.2.0-11.dmg",
        url: "downloads/Cortex-0.2.0-11.dmg"
      },
      {
        kind: "zip",
        filename: "Cortex-0.2.0-11.app.zip",
        url: "downloads/Cortex-0.2.0-11.app.zip"
      }
    ]
  };

  function bytes(value) {
    if (!Number.isFinite(value)) return "download ready";
    const mb = value / 1024 / 1024;
    return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(value / 1024)} KB`;
  }

  function artifactUrl(artifact) {
    return artifact.url || `downloads/${artifact.filename}`;
  }

  function applyRelease(release) {
    const dmg = (release.artifacts || []).find((item) => item.kind === "dmg") || fallbackRelease.artifacts[0];
    const zip = (release.artifacts || []).find((item) => item.kind === "zip") || fallbackRelease.artifacts[1];
    document.querySelectorAll(".download-link").forEach((link) => {
      link.href = artifactUrl(dmg);
      link.setAttribute("download", dmg.filename);
    });
    document.querySelectorAll(".zip-link").forEach((link) => {
      link.href = artifactUrl(zip);
      link.setAttribute("download", zip.filename);
    });
    const version = document.getElementById("releaseVersion");
    const channel = document.getElementById("releaseChannel");
    const size = document.getElementById("releaseSize");
    const primaryArtifact = document.getElementById("primaryArtifactLabel");
    const zipArtifact = document.getElementById("zipArtifactMeta");
    const checksum = document.getElementById("checksumText");
    const zipChecksum = document.getElementById("zipChecksumText");
    if (version) version.textContent = `Cortex ${release.version || fallbackRelease.version} (${release.build || fallbackRelease.build})`;
    if (channel) channel.textContent = release.channel || fallbackRelease.channel;
    if (size) size.textContent = `DMG ${bytes(Number(dmg.size_bytes))}`;
    if (primaryArtifact) primaryArtifact.textContent = `${dmg.filename} for macOS ${release.minimum_macos || "13.0"} or later.`;
    if (zipArtifact) zipArtifact.textContent = `App archive for advanced installs, ${bytes(Number(zip.size_bytes))}.`;
    if (checksum && dmg.sha256) checksum.textContent = `DMG SHA-256: ${dmg.sha256}`;
    if (zipChecksum && zip.sha256) zipChecksum.textContent = `ZIP SHA-256: ${zip.sha256}`;
  }

  fetch("downloads/latest.json", { cache: "no-store" })
    .then((response) => response.ok ? response.json() : fallbackRelease)
    .then(applyRelease)
    .catch(() => applyRelease(fallbackRelease));

  const canvas = document.getElementById("memoryCanvas");
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  const labels = ["Preference", "Decision", "Project", "Follow-up", "Claude", "ChatGPT", "Cursor", "AI tool", "Today"];
  const colors = ["#1f7a5c", "#365d8c", "#bd5d45", "#a77722"];
  let points = [];
  let pointer = { x: 0, y: 0, active: false };

  function reset() {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 2));
    canvas.width = Math.floor(rect.width * dpr);
    canvas.height = Math.floor(rect.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const count = Math.max(18, Math.floor(rect.width / 62));
    points = Array.from({ length: count }, (_, index) => ({
      x: rect.width * (0.42 + Math.random() * 0.55),
      y: 74 + Math.random() * Math.max(160, rect.height - 150),
      vx: (Math.random() - 0.5) * 0.18,
      vy: (Math.random() - 0.5) * 0.18,
      r: 3 + Math.random() * 5,
      label: labels[index % labels.length],
      color: colors[index % colors.length]
    }));
  }

  function drawPanel(rect) {
    const panelX = rect.width * 0.62;
    const panelY = rect.height * 0.22;
    const panelW = Math.min(360, rect.width * 0.32);
    const panelH = 240;
    ctx.fillStyle = "rgba(255, 253, 248, 0.88)";
    ctx.strokeStyle = "rgba(24, 33, 31, 0.16)";
    ctx.lineWidth = 1;
    roundRect(panelX, panelY, panelW, panelH, 8);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = "#18211f";
    ctx.font = "700 18px system-ui, sans-serif";
    ctx.fillText("Today", panelX + 20, panelY + 34);
    const rows = [
      ["Connect", "#1f7a5c"],
      ["Review", "#365d8c"],
      ["Ask", "#bd5d45"],
      ["Control", "#a77722"]
    ];
    rows.forEach((row, index) => {
      const y = panelY + 72 + index * 38;
      ctx.fillStyle = row[1];
      ctx.beginPath();
      ctx.arc(panelX + 26, y - 5, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#5c6965";
      ctx.font = "600 14px system-ui, sans-serif";
      ctx.fillText(row[0], panelX + 44, y);
      ctx.fillStyle = "rgba(24, 33, 31, 0.12)";
      roundRect(panelX + 122, y - 13, panelW - 146, 11, 6);
      ctx.fill();
    });
  }

  function roundRect(x, y, width, height, radius) {
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.lineTo(x + width - radius, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
    ctx.lineTo(x + width, y + height - radius);
    ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
    ctx.lineTo(x + radius, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
    ctx.lineTo(x, y + radius);
    ctx.quadraticCurveTo(x, y, x + radius, y);
    ctx.closePath();
  }

  function render() {
    const rect = canvas.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);
    ctx.fillStyle = "#eef1eb";
    ctx.fillRect(0, 0, rect.width, rect.height);

    for (let i = 0; i < points.length; i += 1) {
      const a = points[i];
      a.x += a.vx;
      a.y += a.vy;
      if (a.x < rect.width * 0.38 || a.x > rect.width - 24) a.vx *= -1;
      if (a.y < 70 || a.y > rect.height - 40) a.vy *= -1;
      if (pointer.active) {
        const dx = pointer.x - a.x;
        const dy = pointer.y - a.y;
        const distance = Math.sqrt(dx * dx + dy * dy);
        if (distance < 160 && distance > 1) {
          a.x -= dx * 0.0018;
          a.y -= dy * 0.0018;
        }
      }
      for (let j = i + 1; j < points.length; j += 1) {
        const b = points[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const distance = Math.sqrt(dx * dx + dy * dy);
        if (distance < 145) {
          ctx.strokeStyle = `rgba(54, 93, 140, ${0.22 - distance / 800})`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
    }

    points.forEach((point, index) => {
      ctx.fillStyle = point.color;
      ctx.beginPath();
      ctx.arc(point.x, point.y, point.r, 0, Math.PI * 2);
      ctx.fill();
      if (index % 4 === 0 && rect.width > 760) {
        ctx.fillStyle = "rgba(24, 33, 31, 0.68)";
        ctx.font = "600 12px system-ui, sans-serif";
        ctx.fillText(point.label, point.x + 10, point.y + 4);
      }
    });

    if (rect.width > 700) drawPanel(rect);
    requestAnimationFrame(render);
  }

  canvas.addEventListener("pointermove", (event) => {
    const rect = canvas.getBoundingClientRect();
    pointer = { x: event.clientX - rect.left, y: event.clientY - rect.top, active: true };
  });
  canvas.addEventListener("pointerleave", () => {
    pointer.active = false;
  });
  window.addEventListener("resize", reset);
  reset();
  render();
})();
