/* OPECT 文献雷达 · 静态站脚本
 * 无后端：检索、筛选、排序全部在浏览器完成；收藏与笔记存 localStorage。 */
(function () {
  "use strict";

  const scriptEl = document.currentScript;
  const ROOT = scriptEl ? new URL("../", scriptEl.src) : new URL("./", location.href);
  const $ = (sel, ctx) => (ctx || document).querySelector(sel);
  const $$ = (sel, ctx) => Array.from((ctx || document).querySelectorAll(sel));

  const store = {
    get(key, fallback) {
      try {
        const raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : fallback;
      } catch (e) {
        return fallback;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(key, JSON.stringify(value));
      } catch (e) {}
    },
  };

  /* ------------------------------------------------------------------ theme */
  const THEMES = ["auto", "light", "dark"];
  const THEME_LABEL = { auto: "跟随系统", light: "浅色", dark: "深色" };

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    const label = $(".theme-label");
    if (label) label.textContent = THEME_LABEL[theme] || THEME_LABEL.auto;
  }

  function initTheme() {
    let theme = store.get("opect-theme", "auto");
    if (typeof theme !== "string" || !THEMES.includes(theme)) theme = "auto";
    applyTheme(theme);
    const btn = $("#theme-toggle");
    if (btn) {
      btn.addEventListener("click", function () {
        const current = store.get("opect-theme", "auto");
        const next = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
        store.set("opect-theme", next);
        applyTheme(next);
      });
    }
  }

  /* ------------------------------------------------------------------- misc */
  function toast(message) {
    const el = $("#toast");
    if (!el) return;
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => el.classList.remove("show"), 2400);
  }

  function initNav() {
    const btn = $("#menu-btn");
    const sidebar = $(".sidebar");
    if (!btn || !sidebar) return;
    btn.addEventListener("click", () => sidebar.classList.toggle("open"));
    document.addEventListener("click", function (e) {
      if (window.innerWidth > 860) return;
      if (sidebar.contains(e.target) || btn.contains(e.target)) return;
      sidebar.classList.remove("open");
    });
  }

  function initExport() {
    $$("[data-export-toggle]").forEach((btn) => {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        const pop = $(".export-pop", btn.parentElement);
        if (pop) pop.hidden = !pop.hidden;
      });
    });
    document.addEventListener("click", () => {
      $$(".export-pop").forEach((p) => (p.hidden = true));
    });
  }

  /* --------------------------------------------------------------- favourites */
  const favs = new Set((store.get("opect-favs", []) || []).map(String));

  function paintFavs() {
    $$("[data-fav]").forEach((btn) => {
      const on = favs.has(String(btn.dataset.fav));
      btn.classList.toggle("active", on);
      const label = $("span", btn);
      if (label) label.textContent = on ? "已收藏" : "收藏";
    });
    const counter = $("#fav-count");
    if (counter) counter.textContent = String(favs.size);
  }

  function initFavs() {
    document.addEventListener("click", function (e) {
      const btn = e.target.closest("[data-fav]");
      if (!btn) return;
      e.preventDefault();
      const id = String(btn.dataset.fav);
      if (favs.has(id)) {
        favs.delete(id);
        toast("已取消收藏");
      } else {
        favs.add(id);
        toast("已收藏");
      }
      store.set("opect-favs", Array.from(favs));
      paintFavs();
      if (window.OPECT_APPLY) window.OPECT_APPLY();
    });
    paintFavs();
  }

  /* -------------------------------------------------------------------- notes */
  function initNotes() {
    const box = $("#note-box");
    if (!box) return;
    const id = String(box.dataset.note || "");
    const notes = store.get("opect-notes", {}) || {};
    if (notes[id]) box.value = notes[id];

    const status = $("#note-status");
    const save = $("#note-save");

    function persist() {
      const all = store.get("opect-notes", {}) || {};
      if (box.value.trim()) all[id] = box.value;
      else delete all[id];
      store.set("opect-notes", all);
      if (status) {
        status.textContent = "已保存到本机浏览器";
        setTimeout(() => (status.textContent = ""), 1800);
      }
    }

    if (save) save.addEventListener("click", persist);
    let timer = null;
    box.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(persist, 900);
    });
  }

  /* ---------------------------------------------------------------- listing */
  const state = { q: "", tier: "", year: "", topic: "", venue: "", source: "", author: "", favorite: false, sort: "newest" };
  const LABELS = { tier: "层级", year: "年份", topic: "主题", venue: "期刊", source: "来源", author: "作者" };

  function textOf(card) {
    if (card._haystack) return card._haystack;
    const parts = [];
    const title = $(".paper-title", card);
    const abstract = $(".paper-abstract", card);
    const authors = $(".paper-authors", card);
    if (title) parts.push(title.textContent);
    if (abstract) parts.push(abstract.textContent);
    if (authors) parts.push(authors.textContent);
    card._haystack = parts.join(" ").toLowerCase();
    return card._haystack;
  }

  function matches(card) {
    if (state.tier && card.dataset.tier !== state.tier) return false;
    if (state.year && card.dataset.year !== state.year) return false;
    if (state.venue && card.dataset.venue !== state.venue) return false;
    if (state.source && !(card.dataset.sources || "").split("|").includes(state.source)) return false;
    if (state.topic && !(card.dataset.topics || "").split("|").includes(state.topic)) return false;
    if (state.author && !(card.dataset.authors || "").split("|").includes(state.author)) return false;
    if (state.favorite && !favs.has(String(card.dataset.id))) return false;
    if (state.q) {
      const hay = textOf(card);
      for (const token of state.q.toLowerCase().split(/\s+/)) {
        if (token && !hay.includes(token)) return false;
      }
    }
    return true;
  }

  function sortCards(cards) {
    const num = (v) => Number(v || 0);
    return cards.sort(function (a, b) {
      switch (state.sort) {
        case "relevance":
          return num(b.dataset.relevance) - num(a.dataset.relevance);
        case "citations":
          return num(b.dataset.cites) - num(a.dataset.cites);
        case "oldest":
          return (a.dataset.date || "").localeCompare(b.dataset.date || "");
        default:
          return (b.dataset.date || "").localeCompare(a.dataset.date || "");
      }
    });
  }

  function renderChips() {
    const box = $("#active-chips");
    if (!box) return;
    const chips = [];
    if (state.q) chips.push(["q", "关键词：" + state.q]);
    Object.keys(LABELS).forEach((key) => {
      if (state[key]) chips.push([key, LABELS[key] + "：" + state[key]]);
    });
    if (state.favorite) chips.push(["favorite", "仅看收藏"]);
    box.innerHTML = chips
      .map(
        ([key, text]) =>
          '<button class="chip" type="button" data-clear="' +
          key +
          '">' +
          text +
          '<span aria-hidden="true">×</span></button>'
      )
      .join("");
  }

  function apply() {
    const list = $("#paper-list");
    if (!list) return;
    const cards = $$(".paper", list);
    const visible = [];
    cards.forEach(function (card) {
      const ok = matches(card);
      card.hidden = !ok;
      if (ok) visible.push(card);
    });
    sortCards(visible).forEach((card) => list.appendChild(card));

    const counter = $("#result-count");
    if (counter) counter.textContent = "显示 " + visible.length + " / " + cards.length + " 篇";
    const empty = $("#no-result");
    if (empty) empty.hidden = visible.length > 0;

    $$(".facet-item[data-facet]").forEach((btn) => {
      const key = btn.dataset.facet;
      const value = btn.dataset.value;
      let active = false;
      if (key === "favorite") active = state.favorite;
      else active = String(state[key] || "") === value;
      btn.classList.toggle("active", active);
    });
    $$("#sort-seg .sort-opt").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.sort === state.sort);
    });
    renderChips();
  }

  function readUrl() {
    const params = new URLSearchParams(location.search);
    state.q = params.get("q") || "";
    ["tier", "year", "topic", "venue", "source", "author"].forEach((key) => {
      state[key] = params.get(key) || "";
    });
    state.favorite = params.get("favorite") === "1";
    state.sort = params.get("sort") || "newest";
    const input = $("#global-search");
    if (input && state.q) input.value = state.q;
  }

  function writeUrl() {
    const params = new URLSearchParams();
    if (state.q) params.set("q", state.q);
    ["tier", "year", "topic", "venue", "source", "author"].forEach((key) => {
      if (state[key]) params.set(key, state[key]);
    });
    if (state.favorite) params.set("favorite", "1");
    if (state.sort && state.sort !== "newest") params.set("sort", state.sort);
    const query = params.toString();
    history.replaceState(null, "", query ? "?" + query : location.pathname);
  }

  function initListing() {
    const list = $("#paper-list");
    if (!list) return;
    readUrl();

    $$(".facet-item[data-facet]").forEach((btn) => {
      btn.addEventListener("click", function () {
        const key = btn.dataset.facet;
        const value = btn.dataset.value;
        if (key === "favorite") state.favorite = !state.favorite;
        else state[key] = String(state[key] || "") === value ? "" : value;
        writeUrl();
        apply();
      });
    });

    $$("#sort-seg .sort-opt").forEach((btn) => {
      btn.addEventListener("click", function () {
        state.sort = btn.dataset.sort;
        writeUrl();
        apply();
      });
    });

    const chips = $("#active-chips");
    if (chips) {
      chips.addEventListener("click", function (e) {
        const btn = e.target.closest("[data-clear]");
        if (!btn) return;
        const key = btn.dataset.clear;
        if (key === "favorite") state.favorite = false;
        else state[key] = "";
        if (key === "q") {
          const input = $("#global-search");
          if (input) input.value = "";
        }
        writeUrl();
        apply();
      });
    }

    window.OPECT_APPLY = apply;
    apply();
  }

  /* ---------------------------------------------------------------- search */
  function initSearch() {
    const form = $(".search");
    const input = $("#global-search");
    if (!form || !input) return;

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      const q = input.value.trim();
      if ($("#paper-list")) {
        state.q = q;
        writeUrl();
        apply();
        return;
      }
      const target = new URL("papers.html", ROOT);
      if (q) target.searchParams.set("q", q);
      location.href = target.toString();
    });

    if ($("#paper-list")) {
      let timer = null;
      input.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
          state.q = input.value.trim();
          writeUrl();
          apply();
        }, 160);
      });
    }

    document.addEventListener("keydown", function (e) {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = (document.activeElement && document.activeElement.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      e.preventDefault();
      input.focus();
      input.select();
    });
  }

  /* ------------------------------------------------------------------- boot */
  function boot() {
    initTheme();
    initNav();
    initExport();
    initFavs();
    initNotes();
    initListing();
    initSearch();
    const side = $("#side-sync");
    if (side) side.textContent = "静态站 · 每日自动更新";
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
