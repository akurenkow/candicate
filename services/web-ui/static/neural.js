(() => {
  const canvas = document.getElementById("bg");
  if (!canvas) return;
  const ctx = canvas.getContext("2d", { alpha: true });

  const DPR = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
  const cfg = {
    particleCount: 90,
    maxVel: 0.18,
    linkDist: 130,
    linkDist2: 130 * 130,
    repulseRadius: 150,
    repulseRadius2: 150 * 150,
    repulseForce: 0.9,
    color: { r: 80, g: 220, b: 255 },
  };

  let w = 0;
  let h = 0;
  let particles = [];
  let pointer = { x: -9999, y: -9999, active: false };

  function resize() {
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = Math.floor(w * DPR);
    canvas.height = Math.floor(h * DPR);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }

  function rand(min, max) {
    return min + Math.random() * (max - min);
  }

  function init() {
    particles = new Array(cfg.particleCount).fill(0).map(() => ({
      x: rand(0, w),
      y: rand(0, h),
      vx: rand(-cfg.maxVel, cfg.maxVel),
      vy: rand(-cfg.maxVel, cfg.maxVel),
      r: rand(1.2, 2.2),
    }));
  }

  function step() {
    ctx.clearRect(0, 0, w, h);

    // Move
    for (const p of particles) {
      // slow drift + wrap
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < -20) p.x = w + 20;
      if (p.x > w + 20) p.x = -20;
      if (p.y < -20) p.y = h + 20;
      if (p.y > h + 20) p.y = -20;

      // repulse from pointer
      if (pointer.active) {
        const dx = p.x - pointer.x;
        const dy = p.y - pointer.y;
        const d2 = dx * dx + dy * dy;
        if (d2 > 0.001 && d2 < cfg.repulseRadius2) {
          const d = Math.sqrt(d2);
          const k = (1 - d / cfg.repulseRadius) * cfg.repulseForce;
          p.vx += (dx / d) * k * 0.02;
          p.vy += (dy / d) * k * 0.02;
        }
      }

      // velocity clamp + gentle damping
      p.vx = Math.max(-0.6, Math.min(0.6, p.vx * 0.999));
      p.vy = Math.max(-0.6, Math.min(0.6, p.vy * 0.999));
    }

    // Links
    ctx.lineWidth = 1;
    for (let i = 0; i < particles.length; i++) {
      const a = particles[i];
      for (let j = i + 1; j < particles.length; j++) {
        const b = particles[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < cfg.linkDist2) {
          const alpha = 1 - d2 / cfg.linkDist2;
          ctx.strokeStyle = `rgba(${cfg.color.r},${cfg.color.g},${cfg.color.b},${0.22 * alpha})`;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
    }

    // Stars (glow)
    ctx.shadowColor = `rgba(${cfg.color.r},${cfg.color.g},${cfg.color.b},0.8)`;
    ctx.shadowBlur = 10;
    for (const p of particles) {
      ctx.fillStyle = `rgba(${cfg.color.r},${cfg.color.g},${cfg.color.b},0.95)`;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.shadowBlur = 0;

    requestAnimationFrame(step);
  }

  window.addEventListener("mousemove", (e) => {
    pointer.x = e.clientX;
    pointer.y = e.clientY;
    pointer.active = true;
  });
  window.addEventListener("mouseleave", () => {
    pointer.active = false;
  });
  window.addEventListener("touchstart", (e) => {
    const t = e.touches && e.touches[0];
    if (!t) return;
    pointer.x = t.clientX;
    pointer.y = t.clientY;
    pointer.active = true;
  }, { passive: true });
  window.addEventListener("touchmove", (e) => {
    const t = e.touches && e.touches[0];
    if (!t) return;
    pointer.x = t.clientX;
    pointer.y = t.clientY;
    pointer.active = true;
  }, { passive: true });
  window.addEventListener("touchend", () => {
    pointer.active = false;
  }, { passive: true });

  window.addEventListener("resize", () => {
    resize();
    init();
  });

  resize();
  init();
  requestAnimationFrame(step);
})();

