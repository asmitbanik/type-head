(() => {
  "use strict";

  const DEBOUNCE_MS = 200;
  const API = "";

  const input = document.getElementById("search-input");
  const suggestionsEl = document.getElementById("suggestions");
  const suggestionsList = document.getElementById("suggestions-list");
  const searchBtn = document.getElementById("search-btn");
  const searchStatus = document.getElementById("search-status");
  const searchError = document.getElementById("search-error");
  const responseBox = document.getElementById("response-box");
  const trendingList = document.getElementById("trending-list");
  const trendingLoading = document.getElementById("trending-loading");
  const trendingError = document.getElementById("trending-error");
  const modeBtns = document.querySelectorAll(".mode-btn");
  const statHit = document.getElementById("stat-hit");
  const statTerms = document.getElementById("stat-terms");
  const statBatch = document.getElementById("stat-batch");
  const statNode = document.getElementById("stat-node");

  let mode = "basic";
  let debounceTimer = null;
  let activeIndex = -1;
  let currentSuggestions = [];
  let abortController = null;

  function show(el) { el.classList.remove("hidden"); }
  function hide(el) { el.classList.add("hidden"); }

  function popularityMeta(count, maxCount) {
    const max = Math.max(maxCount, count, 1);
    const log = Math.log10(1 + count);
    const maxLog = Math.log10(1 + max);
    const pct = Math.round((log / maxLog) * 100);
    let label;
    if (pct >= 85) label = "viral";
    else if (pct >= 65) label = "hot";
    else if (pct >= 40) label = "popular";
    else if (pct >= 15) label = "rising";
    else label = "low";
    return { pct, label };
  }

  function popularityHtml(count, maxCount) {
    const { pct, label } = popularityMeta(count, maxCount);
    return `
      <div class="popularity" title="search popularity">
        <div class="pop-bar" aria-hidden="true"><div class="pop-fill" style="width:${pct}%"></div></div>
        <span class="pop-label">${label}</span>
      </div>`;
  }

  function renderSearchResponse(data) {
    const maxPop = Math.max(data.count || 1, 1000);
    const { pct, label } = popularityMeta(data.count || 0, maxPop);
    responseBox.innerHTML = `
      <div class="response-card">
        <div class="response-row"><span class="response-key">query</span><span class="response-val">${escapeHtml(data.query)}</span></div>
        <div class="response-row"><span class="response-key">status</span><span class="response-val">${escapeHtml(data.message || data.status)}</span></div>
        <div class="response-row"><span class="response-key">popularity</span>
          <div class="popularity inline">
            <div class="pop-bar"><div class="pop-fill" style="width:${pct}%"></div></div>
            <span class="pop-label">${label}</span>
          </div>
        </div>
      </div>`;
  }

  modeBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      modeBtns.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      mode = btn.dataset.mode;
      fetchSuggestions(input.value);
    });
  });

  async function fetchSuggestions(prefix) {
    if (abortController) abortController.abort();
    abortController = new AbortController();

    hide(searchError);
    show(searchStatus);
    searchStatus.textContent = "fetching suggestions...";

    try {
      const params = new URLSearchParams({ q: prefix, mode });
      const res = await fetch(`${API}/suggest?${params}`, {
        signal: abortController.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      currentSuggestions = data.suggestions || [];
      renderSuggestions(currentSuggestions);
      activeIndex = -1;
      hide(searchStatus);

      if (prefix) {
        const routeRes = await fetch(
          `${API}/cache/debug?prefix=${encodeURIComponent(prefix)}`
        );
        if (routeRes.ok) {
          const route = await routeRes.json();
          statNode.textContent = `${route.basic.node} (${route.basic.hit ? "HIT" : "MISS"})`;
        }
      }
    } catch (err) {
      if (err.name === "AbortError") return;
      hide(searchStatus);
      searchError.textContent = `Suggestion error: ${err.message}`;
      show(searchError);
      hideSuggestions();
    }
  }

  function renderSuggestions(items) {
    suggestionsList.innerHTML = "";
    if (!items.length) {
      hideSuggestions();
      return;
    }
    const maxCount = Math.max(...items.map((i) => i.count || 0), 1);
    items.forEach((item, i) => {
      const li = document.createElement("li");
      li.role = "option";
      li.dataset.index = String(i);
      li.innerHTML = `<span class="query-text">${escapeHtml(item.query)}</span>${popularityHtml(item.count, maxCount)}`;
      li.addEventListener("mousedown", (e) => {
        e.preventDefault();
        selectSuggestion(item.query);
      });
      suggestionsList.appendChild(li);
    });
    show(suggestionsEl);
    input.setAttribute("aria-expanded", "true");
  }

  function hideSuggestions() {
    hide(suggestionsEl);
    input.setAttribute("aria-expanded", "false");
    activeIndex = -1;
  }

  function highlightActive() {
    const items = suggestionsList.querySelectorAll("li");
    items.forEach((li, i) => {
      li.classList.toggle("active", i === activeIndex);
    });
    if (activeIndex >= 0 && items[activeIndex]) {
      items[activeIndex].scrollIntoView({ block: "nearest" });
    }
  }

  function selectSuggestion(query) {
    input.value = query;
    hideSuggestions();
    submitSearch(query);
  }

  async function submitSearch(query) {
    const q = (query || input.value).trim();
    if (!q) {
      searchError.textContent = "Enter a search query";
      show(searchError);
      return;
    }

    hide(searchError);
    show(searchStatus);
    searchStatus.textContent = "searching...";
    searchBtn.disabled = true;

    try {
      const res = await fetch(`${API}/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      renderSearchResponse(data);
      hide(searchStatus);
      hideSuggestions();
      loadTrending();
      loadStats();
      fetchSuggestions(input.value);
    } catch (err) {
      searchError.textContent = err.message;
      show(searchError);
      hide(searchStatus);
    } finally {
      searchBtn.disabled = false;
    }
  }

  async function loadTrending() {
    show(trendingLoading);
    hide(trendingError);
    hide(trendingList);

    try {
      const res = await fetch(`${API}/trending?n=10`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const items = data.trending || [];
      const maxCount = Math.max(...items.map((i) => i.count || 0), 1);
      trendingList.innerHTML = "";
      items.forEach((item) => {
        const li = document.createElement("li");
        li.innerHTML = `
          <div class="trending-item">
            <span class="trending-query">${escapeHtml(item.query)}</span>
            <div class="trending-meta">
              <span class="trend-badge">momentum ${item.score.toFixed(1)}</span>
              ${popularityHtml(item.count, maxCount)}
            </div>
          </div>`;
        li.addEventListener("click", () => {
          input.value = item.query;
          submitSearch(item.query);
        });
        trendingList.appendChild(li);
      });
      hide(trendingLoading);
      show(trendingList);
    } catch (err) {
      hide(trendingLoading);
      trendingError.textContent = err.message;
      show(trendingError);
    }
  }

  async function loadStats() {
    try {
      const res = await fetch(`${API}/stats`);
      if (!res.ok) return;
      const data = await res.json();
      const hitRate = (data.cache?.hit_rate ?? data.suggest_cache_hit_rate ?? 0) * 100;
      statHit.textContent = `${hitRate.toFixed(1)}%`;
      statTerms.textContent = formatCompact(data.terms_indexed || 0);
      const reduction = (data.batch?.write_reduction ?? 0) * 100;
      statBatch.textContent = `${reduction.toFixed(1)}%`;
    } catch (_) { /* silent */ }
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function formatCompact(n) {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
  }

  input.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => fetchSuggestions(input.value), DEBOUNCE_MS);
  });

  input.addEventListener("keydown", (e) => {
    const items = suggestionsList.querySelectorAll("li");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!items.length) return;
      activeIndex = Math.min(activeIndex + 1, items.length - 1);
      highlightActive();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!items.length) return;
      activeIndex = Math.max(activeIndex - 1, 0);
      highlightActive();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (activeIndex >= 0 && currentSuggestions[activeIndex]) {
        selectSuggestion(currentSuggestions[activeIndex].query);
      } else {
        submitSearch();
      }
    } else if (e.key === "Escape") {
      hideSuggestions();
    }
  });

  searchBtn.addEventListener("click", () => submitSearch());
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".input-wrap")) hideSuggestions();
  });

  loadTrending();
  loadStats();
  fetchSuggestions("");
})();
