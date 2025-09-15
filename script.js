document.addEventListener('DOMContentLoaded', () => {
  lucide.createIcons();

  const API_BASE_URL = 'http://127.0.0.1:5000';
  // ... (all other const declarations are the same)
  const analyzeBtn = document.getElementById('analyzeBtn');
  const tickerInput = document.getElementById('tickerInput');
  const loader = document.getElementById('loader');
  const btnText = document.getElementById('btn-text');
  const errorMessage = document.getElementById('error-message');
  const resultsSection = document.getElementById('resultsSection');
  const welcomeMessage = document.getElementById('welcomeMessage');
  const headlinesList = document.getElementById('headlinesList');
  const tickerContent = document.getElementById('ticker-content');
  const companyNameEl = document.getElementById('companyName');

  // NEW: Selectors for the new stat cards
  const sentimentCard = document.getElementById('sentimentCard');
  const overallSentimentEl = document.getElementById('overallSentiment');
  const sentimentIconEl = document.getElementById('sentimentIcon');
  const lastCloseEl = document.getElementById('lastClose');
  const pctChangeEl = document.getElementById('pctChange');
  const modelUsedEl = document.getElementById('modelUsed');

  // Modal elements
  const articleModal = document.getElementById('articleModal');
  const modalTitle = document.getElementById('modalTitle');
  const modalSource = document.getElementById('modalSource');
  const modalDescription = document.getElementById('modalDescription');
  const impactsContainer = document.getElementById('impacts');
  const closeModalBtn = document.getElementById('closeModal');

  function showLoading(isLoading) {
    btnText.classList.toggle('hidden', isLoading);
    loader.classList.toggle('hidden', !isLoading);
    analyzeBtn.disabled = isLoading;
    tickerInput.disabled = isLoading;
  }

  analyzeBtn.addEventListener('click', async () => {
    const query = tickerInput.value.trim();
    if (!query) {
      errorMessage.textContent = 'Please enter a company name or stock ticker.';
      errorMessage.classList.remove('hidden');
      return;
    }
    errorMessage.classList.add('hidden');
    showLoading(true);
    welcomeMessage.classList.add('hidden');
    resultsSection.classList.add('hidden');

    try {
      const resp = await fetch(`${API_BASE_URL}/analyze?ticker=${encodeURIComponent(query)}`);
      const data = await resp.json();
      if (!resp.ok) { throw new Error(data.error || 'Analysis failed'); }
      updateUI(data);
    } catch (err) {
      errorMessage.textContent = `Analysis failed: ${err.message || 'Unknown error'}`;
      errorMessage.classList.remove('hidden');
      resultsSection.classList.add('hidden');
      welcomeMessage.classList.remove('hidden');
    } finally {
      showLoading(false);
    }
  });

  tickerInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') analyzeBtn.click(); });

  function updateUI(data) {
    const resolvedTicker = data.ticker;
    companyNameEl.textContent = `Analysis for ${data.company_name || data.query}`;

    // --- UPDATED: Sentiment Card Logic ---
    const sentiment = (data.overall_sentiment || 'Neutral').toLowerCase();
    overallSentimentEl.textContent = data.overall_sentiment || 'Neutral';
    overallSentimentEl.className = `text-2xl font-bold sentiment-${sentiment}`;
    
    sentimentCard.classList.remove('glow-positive', 'glow-negative');
    if (sentiment === 'positive') sentimentCard.classList.add('glow-positive');
    if (sentiment === 'negative') sentimentCard.classList.add('glow-negative');
    
    const iconName = sentiment === 'positive' ? 'trending-up' : sentiment === 'negative' ? 'trending-down' : 'minus';
    sentimentIconEl.setAttribute('data-lucide', iconName);
    sentimentIconEl.className = `w-6 h-6 sentiment-${sentiment}`;

    // --- UPDATED: Price Card Logic ---
    if (data.price && data.price.last_close !== undefined) {
      lastCloseEl.textContent = `$${data.price.last_close.toFixed(2)}`;
      const pct = data.price.pct_change;
      pctChangeEl.textContent = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
      pctChangeEl.className = `font-semibold ${pct >= 0 ? 'text-green-400' : 'text-red-400'}`;
    } else {
      lastCloseEl.textContent = 'N/A';
      pctChangeEl.textContent = '';
    }

    modelUsedEl.textContent = (data.models_used || []).join(', ');

    // --- UPDATED: Headlines Rendering ---
    headlinesList.innerHTML = '';
    (data.articles || []).forEach(article => {
      const el = document.createElement('div');
      el.className = 'headline-card flex justify-between items-center gap-4';
      el.innerHTML = `
        <div class="flex-1">
          <p class="text-slate-200 font-semibold text-sm">${article.title}</p>
          <p class="text-xs text-slate-400 mt-1">${article.source || 'Unknown Source'}</p>
        </div>
        <div class="flex items-center gap-2 flex-shrink-0">
          <button class="impact-btn text-xs font-semibold px-3 py-1.5 rounded-md flex items-center gap-1.5">
            <i data-lucide="crosshair" class="w-3 h-3"></i> Impact
          </button>
          <a href="${article.url}" target="_blank" rel="noopener noreferrer" class="text-xs text-slate-400 hover:text-sky-400 flex items-center gap-1">
            Open <i data-lucide="external-link" class="w-3 h-3"></i>
          </a>
        </div>
      `;
      el.querySelector('.impact-btn').addEventListener('click', () => {
        openArticleImpactModal(article, resolvedTicker);
      });
      headlinesList.appendChild(el);
    });

    resultsSection.classList.remove('hidden');
    welcomeMessage.classList.add('hidden');
    lucide.createIcons();
  }
  
  async function openArticleImpactModal(article, ticker) {
    modalTitle.textContent = article.title || 'Article';
    modalSource.textContent = article.source || 'Unknown Source';
    modalDescription.textContent = article.description || 'No description available.';
    impactsContainer.innerHTML = `<div class="text-sm text-slate-400">Analyzing...</div>`;
    articleModal.style.display = 'flex';
    lucide.createIcons();
    try {
      const resp = await fetch(`${API_BASE_URL}/impact`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: article.title, description: article.description || '', ticker: ticker
        })
      });
      const data = await resp.json();
      if (!resp.ok) { throw new Error(data.error || 'Impact analysis failed'); }
      renderImpact(data);
    } catch (err) {
      impactsContainer.innerHTML = `<div class="text-red-400">${err.message}</div>`;
    }
  }

  function renderImpact(impactData) {
    if (!impactData || !impactData.impact_on) {
      impactsContainer.innerHTML = '<div class="text-slate-400">No specific impact on the target company was detected.</div>';
      return;
    }
    impactsContainer.innerHTML = '';
    const item = impactData;
    const wrapper = document.createElement('div');
    const sentiment = (item.sentiment || 'neutral').toLowerCase();
    const iconName = sentiment === 'positive' ? 'trending-up' : sentiment === 'negative' ? 'trending-down' : 'minus';
    
    wrapper.className = `impact-card ${sentiment}`; // Adds 'positive' or 'negative' class for border
    wrapper.innerHTML = `
      <div class="flex justify-between items-start gap-4">
        <div>
          <p class="font-semibold text-white">${item.impact_on.name} (${item.impact_on.ticker})</p>
          <blockquote class="text-sm text-slate-400 mt-1 italic">
            "${item.evidence && item.evidence.length ? item.evidence[0] : 'No specific evidence found.'}"
          </blockquote>
        </div>
        <div class="flex items-center gap-2 text-right flex-shrink-0 sentiment-${sentiment}">
             <i data-lucide="${iconName}" class="w-4 h-4"></i>
             <span class="font-bold capitalize">${item.sentiment}</span>
        </div>
      </div>
    `;
    impactsContainer.appendChild(wrapper);
    lucide.createIcons();
  }

  function closeModal() {
    articleModal.style.display = 'none';
  }

  closeModalBtn.addEventListener('click', closeModal);
  articleModal.addEventListener('click', (e) => { if (e.target === articleModal) closeModal(); });

  // fetchGeneralNews and populateTicker can remain the same
  async function fetchGeneralNews() {
    // ... logic is unchanged
    try {
      const resp = await fetch(`${API_BASE_URL}/general_news`);
      if (!resp.ok) throw new Error('Failed to fetch news');
      const articles = await resp.json();
      populateTicker(articles);
    } catch (err) {
      console.error('Failed to fetch general news:', err);
      tickerContent.innerHTML = '<div class="text-red-400">Could not load news.</div>';
    }
  }

  function populateTicker(articles) {
    // ... logic is unchanged
    const container = document.getElementById('ticker-content');
    if (!container) return;
    container.innerHTML = '';
    const articlesToShow = (articles || []).slice(0, 20);
    // Duplicate articles to ensure smooth infinite scroll
    [...articlesToShow, ...articlesToShow].forEach(a => {
      const el = document.createElement('a');
      el.href = a.url;
      el.target = '_blank';
      el.rel = 'noopener noreferrer';
      el.className = 'block py-2 text-slate-300 text-sm';
      el.textContent = a.title;
      container.appendChild(el);
    });
  }

  fetchGeneralNews();
});
