/* OPECT 文献雷达 — progressive enhancement */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  /* ------------------------------------------------------------------ theme */
  const THEMES = ["auto", "light", "dark"];
  const THEME_LABEL = { auto: "跟随系统", light: "浅色", dark: "深色" };

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("opect-theme", theme);
    const label = $(".theme-label");
    if (label) label.textContent = THEME_LABEL[theme] || "跟随系统";
  }

  const stored = localStorage.getItem("opect-theme");
  if (stored && THEMES.includes(stored)) applyTheme(stored);
  else applyTheme("auto");

  const themeBtn = $("#theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      const current = document.documentElement.getAttribute("data-theme") || "auto";
      applyTheme(THEMES[(THEMES.indexOf(current) + 1) % THEMES.length]);
    });
  }

  /* --------------------------------------------------------------- mobile nav */
  const menuBtn = $("#menu-btn");
  if (menuBtn) {
    menuBtn.addEventListener("click", () => document.body.classList.toggle("nav-open"));
    document.addEventListener("click", (e) => {
      if (!document.body.classList.contains("nav-open")) return;
      if (e.target.closest(".sidebar") || e.target.closest("#menu-btn")) return;
      document.body.classList.remove("nav-open");
    });
  }

  /* ------------------------------------------------------------------- toast */
  let toastTimer = null;
  function toast(message) {
    const el = $("#toast");
    if (!el) return;
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
  }

  /* --------------------------------------------------------------- favorites */
  function setFav(button, value) {
    const icon = $("svg", button);
    button.classList.toggle("active", value);
    if ($("span", button)) $("span", button).textContent = value ? "已收藏" : "收藏";
    if (icon) icon.style.fill = value ? "currentColor" : "none";
  }

  document.addEventListener("click", async function (event) {
    const button = event.target.closest("[data-fav]");
    if (!button) return;
    event.preventDefault();
    const id = button.getAttribute("data-fav");
    const next = !button.classList.contains("active");
    setFav(button, next);
    try {
      const res = await fetch("/api/favorite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: Number(id), value: next ? 1 : 0 }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error("failed");
      toast(next ? "已加入收藏" : "已取消收藏");
    } catch (err) {
      setFav(button, !next);
      toast("操作失败，请稍后重试");
    }
  });

  /* -------------------------------------------------------------------- sync */
  const syncButtons = [$("#sync-btn"), $("#sync-btn-hero")].filter(Boolean);
  const banner = $("#sync-banner");
  const sideSync = $("#side-sync");
  let pollTimer = null;
  let wasRunning = false;

  function paintSync(state) {
    const running = !!state.running;
    syncButtons.forEach((btn) => {
      btn.classList.toggle("spinning", running);
      btn.classList.toggle("stopping", running);
      const label = $("span", btn);
      if (label) label.textContent = running ? "停止" : "同步";
      btn.title = running ? "停止本次同步" : "立即抓取最新文献";
    });
    if (sideSync) {
      sideSync.classList.toggle("running", running);
      if (running) {
        sideSync.textContent = "同步中 " + Math.round(state.progress || 0) + "%";
      } else if (state.finished_at) {
        const s = state.stats || {};
        sideSync.textContent = "上次同步：新增 " + (s.new || 0) + " 篇";
      } else {
        sideSync.textContent = "等待同步";
      }
    }
    if (banner) {
      if (running) {
        banner.hidden = false;
        banner.innerHTML =
          '<div class="sync-banner-row"><span>正在抓取：' +
          escapeHtml(state.stage || "准备中") +
          "</span><span>" +
          Math.round(state.progress || 0) +
          '%</span></div><div class="progress"><span style="width:' +
          (state.progress || 0) +
          '%"></span></div>';
      } else {
        banner.hidden = true;
      }
    }
    const panel = $("#sync-detail");
    if (panel && running) {
      panel.innerHTML =
        '<div class="sync-live"><div class="sync-stage">' +
        escapeHtml(state.stage || "同步中") +
        '</div><div class="progress"><span style="width:' +
        (state.progress || 0) +
        '%"></span></div><div class="sync-nums">' +
        Math.round(state.progress || 0) +
        "% · 已入库 " +
        ((state.stats && state.stats.stored) || 0) +
        "</div></div>";
    }
  }

  function escapeHtml(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  async function pollStatus() {
    try {
      const res = await fetch("/api/sync/status", { headers: { Accept: "application/json" } });
      const data = await res.json();
      const state = data.state || {};
      paintSync(state);
      if (state.running) {
        wasRunning = true;
        syncing = true;
        clearTimeout(pollTimer);
        pollTimer = setTimeout(pollStatus, 1400);
      } else if (wasRunning) {
        wasRunning = false;
        syncing = false;
        const stats = state.stats || {};
        toast("同步完成：新增 " + (stats.new || 0) + " 篇相关 " + (stats.relevant || 0) + " 篇");
        setTimeout(() => window.location.reload(), 900);
      }
    } catch (err) {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(pollStatus, 4000);
    }
  }

  let syncing = false;

  async function startSync() {
    if (syncing) {
      try {
        await fetch("/api/sync/stop", { method: "POST" });
        toast("已请求停止，将在当前抓取结束后退出");
      } catch (err) {
        toast("停止请求失败");
      }
      return;
    }
    syncing = true;
    syncButtons.forEach((b) => (b.disabled = true));
    try {
      const res = await fetch("/api/sync", { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        toast(data.message || "无法启动同步");
        syncing = false;
        syncButtons.forEach((b) => (b.disabled = false));
        return;
      }
      toast("已开始同步，抓取多个来源中…");
      wasRunning = true;
      syncButtons.forEach((b) => (b.disabled = false));
      pollStatus();
    } catch (err) {
      toast("同步启动失败：" + err.message);
      syncing = false;
      syncButtons.forEach((b) => (b.disabled = false));
    }
  }

  syncButtons.forEach((btn) => btn.addEventListener("click", startSync));

  // If a sync is already running (e.g. auto-start), attach to it.
  fetch("/api/sync/status", { headers: { Accept: "application/json" } })
    .then((r) => r.json())
    .then((data) => {
      const state = data.state || {};
      paintSync(state);
      if (state.running) {
        wasRunning = true;
        pollStatus();
      }
    })
    .catch(() => {});

  /* ------------------------------------------------------------------- notes */
  const noteBox = $("#note-box");
  const noteSave = $("#note-save");
  if (noteBox && noteSave) {
    noteSave.addEventListener("click", async function () {
      const status = $("#note-status");
      noteSave.disabled = true;
      try {
        const res = await fetch("/api/note", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: Number(noteBox.dataset.note), note: noteBox.value }),
        });
        const data = await res.json();
        if (!data.ok) throw new Error("failed");
        if (status) status.textContent = "已保存 " + new Date().toLocaleTimeString();
        toast("笔记已保存");
      } catch (err) {
        toast("保存失败，请重试");
      } finally {
        noteSave.disabled = false;
      }
    });
  }

  /* ------------------------------------------------------------------ rescore */
  const rescoreBtn = $("#rescore-btn");
  if (rescoreBtn) {
    rescoreBtn.addEventListener("click", async function () {
      rescoreBtn.disabled = true;
      rescoreBtn.classList.add("spinning");
      try {
        const res = await fetch("/api/rescore", { method: "POST" });
        const data = await res.json();
        if (!data.ok) throw new Error("failed");
        const r = data.result || {};
        toast("已重新打分 " + (r.scanned || 0) + " 篇，更新 " + (r.changed || 0) + " 篇，清理 " + (r.pruned || 0) + " 篇");
        setTimeout(() => window.location.reload(), 1400);
      } catch (err) {
        toast("重新打分失败");
      } finally {
        rescoreBtn.disabled = false;
        rescoreBtn.classList.remove("spinning");
      }
    });
  }

  /* ---------------------------------------------------------------- search UI */
  const searchInput = $("#global-search");
  if (searchInput) {
    let box = null;
    let seq = 0;
    let items = [];
    let active = -1;

    function ensureBox() {
      if (box) return box;
      box = document.createElement("div");
      box.className = "search-pop";
      box.hidden = true;
      searchInput.closest(".search").appendChild(box);
      return box;
    }

    function render() {
      const el = ensureBox();
      if (!items.length) {
        el.hidden = true;
        return;
      }
      el.innerHTML = items
        .map(function (item, i) {
          return (
            '<a class="search-item' +
            (i === active ? " active" : "") +
            '" href="/paper/' +
            item.id +
            '"><span class="si-title">' +
            escapeHtml(item.title) +
            '</span><span class="si-meta">' +
            escapeHtml(item.venue || "—") +
            " · " +
            escapeHtml((item.date || "").slice(0, 7)) +
            '</span><span class="si-score">' +
            Math.round(item.relevance || 0) +
            "</span></a>"
          );
        })
        .join("");
      el.hidden = false;
    }

    let debounce = null;
    searchInput.addEventListener("input", function () {
      clearTimeout(debounce);
      const q = searchInput.value.trim();
      if (q.length < 2) {
        items = [];
        active = -1;
        render();
        return;
      }
      debounce = setTimeout(async function () {
        const mine = ++seq;
        try {
          const res = await fetch("/api/search?q=" + encodeURIComponent(q));
          const data = await res.json();
          if (mine !== seq) return;
          items = data.items || [];
          active = -1;
          render();
        } catch (err) {
          items = [];
          render();
        }
      }, 180);
    });

    searchInput.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        if (box) box.hidden = true;
        searchInput.blur();
        return;
      }
      if (!items.length) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        active = (active + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        render();
      } else if (event.key === "Enter" && active >= 0) {
        event.preventDefault();
        window.location.href = "/paper/" + items[active].id;
      }
    });

    document.addEventListener("click", function (event) {
      if (box && !event.target.closest(".search")) box.hidden = true;
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
        event.preventDefault();
        searchInput.focus();
        searchInput.select();
      }
    });
  }

  /* ------------------------------------------------------------- export menu */
  $$("[data-export-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function (event) {
      event.stopPropagation();
      const pop = btn.parentElement.querySelector(".export-pop");
      if (pop) pop.hidden = !pop.hidden;
    });
  });
  document.addEventListener("click", function () {
    $$(".export-pop").forEach((pop) => (pop.hidden = true));
  });

  /* ------------------------------------------------------------ list filtering */
  // Auto-submit the search form on the papers page is handled natively; add a
  // small hint when a query is present so users can clear it quickly.
  const params = new URLSearchParams(window.location.search);
  if (params.get("q") && searchInput) searchInput.value = params.get("q");
})();
