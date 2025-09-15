document.addEventListener('DOMContentLoaded', () => {
  lucide.createIcons();

  const API_BASE_URL = 'http://127.0.0.1:5000';
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
      if (!resp.ok) {
        throw new Error(data.error || 'Analysis failed');
      }
      updateUI(data);
    } catch (err) {
      console.error('Analysis error:', err);
      errorMessage.textContent = `Analysis failed: ${err.message || 'Unknown error'}`;
      errorMessage.classList.remove('hidden');
      resultsSection.classList.add('hidden');
      welcomeMessage.classList.remove('hidden');
    } finally {
      showLoading(false);
    }
  });

  tickerInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      analyzeBtn.click();
    }
  });

  function updateUI(data) {
    const resolvedTicker = data.ticker;
    companyNameEl.textContent = `Analysis for ${data.company_name || data.query}`;

    // --- START: UPDATED SENTIMENT AND ICON LOGIC ---
    const sentiment = (data.overall_sentiment || 'Neutral').toLowerCase();

    // Set text and color for the sentiment word
    overallSentimentEl.textContent = data.overall_sentiment || 'Neutral';
    overallSentimentEl.className = `font-semibold text-lg sentiment-${sentiment}`;

    // Determine the correct icon name based on sentiment
    const iconName = sentiment === 'positive' ? 'trending-up' : sentiment === 'negative' ? 'trending-down' : 'minus';

    // Set the icon and its color
    sentimentIconEl.setAttribute('data-lucide', iconName);
    sentimentIconEl.className = `sentiment-${sentiment}`;
    // --- END: UPDATED SENTIMENT AND ICON LOGIC ---

    if (data.price && data.price.last_close !== undefined) {
      lastCloseEl.textContent = `$${data.price.last_close.toFixed(2)}`;
      const pct = data.price.pct_change;
      pctChangeEl.textContent = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
      pctChangeEl.className = `text-lg font-semibold mt-1 ${pct >= 0 ? 'text-green-400' : 'text-red-400'}`;
    } else {
      lastCloseEl.textContent = 'N/A';
      pctChangeEl.textContent = '';
    }

    modelUsedEl.textContent = (data.models_used || []).join(', ') || 'Default';

    headlinesList.innerHTML = '';
    (data.articles || []).forEach(article => {
      const el = document.createElement('div');
      el.className = 'bg-slate-800/50 p-3 rounded-lg flex justify-between items-center hover:bg-slate-700/50 transition-colors duration-200';
      el.innerHTML = `
        <div class="flex-1 pr-4">
          <p class="text-slate-200 font-medium text-sm leading-snug">${article.title}</p>
          <p class="text-xs text-slate-400 mt-1">${article.source || 'Unknown Source'}</p>
        </div>
        <div class="flex flex-col items-center gap-2">
          <button class="impact-btn bg-sky-600 hover:bg-sky-500 transition-colors px-3 py-1 rounded-md text-sm font-semibold w-full">Impact</button>
          <a href="${article.url}" target="_blank" rel="noopener noreferrer" class="text-xs text-slate-400 hover:text-sky-400">Read More &rarr;</a>
        </div>
      `;
      el.querySelector('.impact-btn').addEventListener('click', () => {
        openArticleImpactModal(article, resolvedTicker);
      });
      headlinesList.appendChild(el);
    });

    populateTicker(data.articles || []);
    resultsSection.classList.remove('hidden');
    welcomeMessage.classList.add('hidden');
    lucide.createIcons(); // This call renders the icons
  }

  function populateTicker(articles) {
    tickerContent.innerHTML = '';
    (articles || []).slice(0, 15).forEach(a => {
      const aEl = document.createElement('a');
      aEl.href = a.url || '#';
      aEl.target = '_blank';
      aEl.rel = 'noopener noreferrer';
      aEl.className = 'block p-2 text-slate-300 text-sm hover:bg-slate-800 rounded-md transition-colors';
      aEl.textContent = a.title || 'Untitled';
      tickerContent.appendChild(aEl);
    });
  }

  async function openArticleImpactModal(article, ticker) {
    modalTitle.textContent = article.title || 'Article';
    modalSource.textContent = article.source || 'Unknown Source';
    modalDescription.textContent = article.description || 'No description available.';
    impactsContainer.innerHTML = `<div class="text-sm text-slate-400">Analyzing article for impact on ${ticker}...</div>`;
    articleModal.classList.remove('hidden');
    articleModal.classList.add('flex');
    lucide.createIcons(); // Render the close 'x' icon
    try {
      const resp = await fetch(`${API_BASE_URL}/impact`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: article.title,
          description: article.description || '',
          source: article.source || '',
          ticker: ticker
        })
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error || 'Article impact analysis failed');
      }
      renderImpact(data);
    } catch (err) {
      console.error('Impact analysis error:', err);
      impactsContainer.innerHTML = `<div class="text-red-400">Could not analyze article: ${err.message}</div>`;
    }
  }

  function renderImpact(impactData) {
    if (!impactData || !impactData.impact_on) {
      impactsContainer.innerHTML = '<div class="text-slate-400">No specific impact on the target company was detected in this article.</div>';
      return;
    }
    impactsContainer.innerHTML = '';
    const item = impactData;
    const wrapper = document.createElement('div');

    // --- START: UPDATED ICON LOGIC FOR IMPACT MODAL ---
    const sentiment = (item.sentiment || 'neutral').toLowerCase();
    const sentimentClass = `sentiment-${sentiment}`;
    const iconName = sentiment === 'positive' ? 'trending-up' : sentiment === 'negative' ? 'trending-down' : 'minus';

    wrapper.className = 'p-4 bg-slate-800 rounded-lg';
    wrapper.innerHTML = `
      <div class="flex justify-between items-start gap-4">
        <div>
          <div class="font-semibold text-slate-100">${item.impact_on.name} (${item.impact_on.ticker})</div>
          <blockquote class="text-sm text-slate-400 mt-2 border-l-2 border-slate-600 pl-3 italic">
            ${item.evidence && item.evidence.length ? item.evidence[0] : 'No specific evidence found.'}
          </blockquote>
        </div>
        <div class="flex items-center justify-end gap-2 text-right flex-shrink-0">
          <i data-lucide="${iconName}" class="${sentimentClass} w-4 h-4"></i>
          <span class="${sentimentClass} font-bold capitalize">${item.sentiment}</span>
        </div>
      </div>
    `;
    // --- END: UPDATED ICON LOGIC FOR IMPACT MODAL ---

    impactsContainer.appendChild(wrapper);
    lucide.createIcons(); // IMPORTANT: Render the new icon inside the modal
  }

  function closeModal() {
      articleModal.classList.add('hidden');
      articleModal.classList.remove('flex');
  }

  closeModalBtn.addEventListener('click', closeModal);
  articleModal.addEventListener('click', (event) => {
    if (event.target === articleModal) {
      closeModal();
    }
  });

  async function fetchGeneralNews() {
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

  fetchGeneralNews();
});
