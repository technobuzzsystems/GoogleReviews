/**
 * feedback.js — TechnoBuzz AI Feedback System
 * =============================================
 * Phase 1: Star rating widget + full UI state machine.
 * Phase 2: /generate-feedback API call (activated later).
 * Phase 3: /submit-feedback API call (activated later).
 *
 * Architecture:
 *   - FeedbackApp: Main controller object (no globals).
 *   - UIState: Manages which sections are visible.
 *   - StarRating: Manages star selection logic.
 *   - SuggestionCards: Manages card rendering and selection.
 *   - API: Handles all fetch calls.
 */

'use strict';

/* ============================================================
   CONSTANTS
   ============================================================ */
const COMPANY_NAME = document.getElementById('companyName')?.dataset.company  || 'TechnoBuzz';
const COMPANY_ID   = document.getElementById('companyName')?.dataset.companyId || 'TECHNOBUZZ-001';
const BUSINESS_ID  = document.getElementById('companyName')?.dataset.businessId || 'technobuzz';

const RATING_LABELS = {
  5: { emoji: '🤩', cls: 'r5' },
  4: { emoji: '😊', cls: 'r4' },
  3: { emoji: '😐', cls: 'r3' },
  2: { emoji: '😕', cls: 'r2' },
  1: { emoji: '😞', cls: 'r1' },
};

/* ============================================================
   DOM CACHE — gather all elements once
   ============================================================ */
const DOM = {
  // Sections
  ratingSection:     document.getElementById('ratingSection'),
  loadingSection:    document.getElementById('loadingSection'),
  suggestionsSection:document.getElementById('suggestionsSection'),
  submitSection:     document.getElementById('submitSection'),
  successScreen:     document.getElementById('successScreen'),
  feedbackForm:      document.getElementById('feedbackForm'),
  errorBanner:       document.getElementById('errorBanner'),
  errorBannerText:   document.getElementById('errorBannerText'),

  // Star rating
  starInputs:        document.querySelectorAll('.star-rating input[type="radio"]'),
  ratingBadge:       document.getElementById('ratingBadge'),

  // Suggestions
  suggestionsGrid:   document.getElementById('suggestionsGrid'),
  validationMsg:     document.getElementById('validationMsg'),
  validationIcon:    document.getElementById('validationIcon'),
  validationText:    document.getElementById('validationText'),

  // Submit button
  btnSubmit:         document.getElementById('btnSubmit'),
  btnText:           document.getElementById('btnText'),
  btnSpinner:        document.getElementById('btnSpinner'),

  // Success screen
  successStars:      document.getElementById('successStars'),
  successScreen:     document.getElementById('successScreen'),
  successActions:    document.querySelector('.success-actions'),
  successReviewText: document.getElementById('successReviewText'),
  successReviewCard: document.getElementById('successReviewCard'),
  btnCopyReview:     document.getElementById('btnCopyReview'),
  btnPostGoogle:     document.getElementById('btnPostGoogle'),
  googleUrlMissing:  document.getElementById('googleUrlMissing'),
  autoCopyMsg:       document.getElementById('autoCopyMsg'),
  googleInstructions:document.getElementById('googleInstructions'),

  // Language
  langSelect:        document.getElementById('reviewLanguage'),
  langChips:         document.querySelectorAll('.lang-chip'),

  // Toast
  copyToast:         document.getElementById('copyToast'),
  toastMsg:          document.getElementById('toastMsg'),

  // Manual Feedback & Suggestions Sections
  manualPane:         document.getElementById('manualFeedbackPane'),
  suggestionsWrapper: document.getElementById('suggestionsWrapper'),
  manualTextarea:     document.getElementById('manualFeedbackText'),
  manualCharCounter:  document.getElementById('manualCharCounter'),
  manualMinHint:      document.getElementById('manualMinHint'),
  submitHint:         document.querySelector('.submit-hint'),
};

/* ============================================================
   STATE — single source of truth for app state
   ============================================================ */
const State = {
  selectedRating:    null,    // 1–5
  selectedFeedback:  null,    // string
  responseIds:       [],      // IDs of predefined static responses
  selectedResponseId:null,    // ID of selected static response
  mode:              'suggestions', // 'suggestions' | 'manual'
  isManual:          false,   // true if manual feedback active
  manualText:        '',      // raw typed text
  isSubmitting:      false,   // prevent double-submit
  isLoadingAI:       false,   // prevent concurrent AI calls
  language:          'en',
  _fetchController:  null,    // AbortController for in-flight AI request
  _activeRequestId:  0,       // Monotonic request ID counter to prevent race conditions
};

/* ============================================================
   UI STATE MANAGER
   ============================================================ */
const UIState = {
  /** Restore submit section to its default position at the end of suggestions section. */
  resetSubmitPosition() {
    if (DOM.submitSection && DOM.suggestionsSection) {
      if (DOM.submitSection.parentNode !== DOM.suggestionsSection || DOM.suggestionsSection.lastElementChild !== DOM.submitSection) {
        DOM.suggestionsSection.appendChild(DOM.submitSection);
      }
    }
  },

  /** Show the AI loading spinner, hide suggestions. */
  showLoading() {
    DOM.loadingSection.classList.add('active');
    DOM.suggestionsSection.classList.remove('active');
    DOM.submitSection.classList.remove('active');
    UIState.resetSubmitPosition();
    UIState.clearValidation();
    UIState.clearErrorBanner();
  },

  /** Show the suggestions grid, hide spinner. */
  showSuggestions() {
    DOM.loadingSection.classList.remove('active');
    DOM.suggestionsSection.classList.add('active');

    // For 4★/5★: hide the Write Your Own / manual feedback pane and clear it.
    // For 1★/2★/3★: always show the manual feedback pane.
    const isLowRating = State.selectedRating && State.selectedRating <= 3;
    if (DOM.manualPane) {
      DOM.manualPane.style.display = isLowRating ? '' : 'none';
    }
    // Clear manual state when hiding pane so stale text is never submitted
    if (!isLowRating) {
      UIState.resetManual();
    }

    // Submit button is intentionally NOT shown here;
    // it appears dynamically above the selected feedback card once clicked.
  },

  /** Show the submit button. */
  showSubmit() {
    DOM.submitSection.classList.add('active');
    UIState.updateSubmitTexts();
  },

  /** Hide suggestions and submit; reset. */
  hideSuggestionsAndSubmit() {
    DOM.suggestionsSection.classList.remove('active');
    DOM.submitSection.classList.remove('active');
    DOM.loadingSection.classList.remove('active');
    State.selectedFeedback = null;
    UIState.resetManual();
    UIState.clearValidation();
    UIState.clearErrorBanner();
    UIState.resetSubmitPosition();
    // Always restore manual pane visibility so it re-appears when user picks 1–3 stars
    if (DOM.manualPane) DOM.manualPane.style.display = '';
  },

  /** Update submit button label and hint text based on mode and rating. */
  updateSubmitTexts() {
    if (!DOM.btnText || !DOM.submitHint) return;
    const isLowRating = State.selectedRating && State.selectedRating <= 3;
    if (isLowRating) {
      DOM.btnText.textContent = I18n.t('submitFeedback') || 'Submit Feedback';
      DOM.submitHint.textContent = I18n.t('privateFeedbackHint') || '';
    } else {
      if (State.isManual) {
        DOM.btnText.textContent = I18n.t('submitFeedback') || 'Submit Feedback';
        DOM.submitHint.textContent = I18n.t('submitHint');
      } else {
        DOM.btnText.textContent = I18n.t('submitBtn');
        DOM.submitHint.textContent = I18n.t('submitHint');
      }
    }
  },

  /** Reset manual feedback inputs and selection state. */
  resetManual() {
    State.isManual = false;
    State.mode = 'suggestions';
    State.manualText = '';
    if (DOM.manualTextarea) DOM.manualTextarea.value = '';
    if (DOM.manualCharCounter) DOM.manualCharCounter.textContent = '0 / 1000';
    if (DOM.manualMinHint) DOM.manualMinHint.classList.remove('valid', 'invalid');
  },

  /** Replace entire feedback form with the thank-you screen. */
  showSuccess() {
    DOM.feedbackForm.style.display = 'none';
    DOM.successScreen.classList.add('active');

    // ── Stars ──────────────────────────────────────────────────────────────
    DOM.successStars.textContent = '⭐'.repeat(State.selectedRating);

    // ── Populate review card ────────────────────────────────────────────────
    if (DOM.successReviewText && State.selectedFeedback) {
      DOM.successReviewText.textContent = State.selectedFeedback;
    }

    // ── Google Review Redirection & Buttons (Rating-Gated) ──────────────────
    // 4★/5★: Keep existing Google Review workflow (copy clipboard, redirect tab, show buttons).
    // 1★/2★/3★: Strictly private feedback (no Google button, no redirection, no Google instructions).
    const isHighRating = State.selectedRating >= 4;
    const googleUrl = (DOM.successScreen.dataset.googleReviewUrl || '').trim();

    if (isHighRating) {
      // ── 4★/5★: Public Google Review Flow ──────────────────────────────────
      if (DOM.successActions) DOM.successActions.style.display = '';

      if (googleUrl) {
        Clipboard.copy(
          State.selectedFeedback,
          () => {
            if (DOM.autoCopyMsg) {
              DOM.autoCopyMsg.textContent = I18n.t('copiedClipboard');
              DOM.autoCopyMsg.style.display = 'block';
            }
            UIState._openGoogleTab(googleUrl);
          },
          () => {
            if (DOM.autoCopyMsg) {
              DOM.autoCopyMsg.textContent = I18n.t('copyFailed');
              DOM.autoCopyMsg.style.display = 'block';
            }
            UIState._openGoogleTab(googleUrl);
          }
        );
        DOM.btnPostGoogle.href = googleUrl;
        DOM.btnPostGoogle.classList.remove('disabled');
        DOM.btnPostGoogle.style.display = 'inline-flex';
        if (DOM.btnCopyReview) DOM.btnCopyReview.style.display = 'inline-flex';
        if (DOM.googleUrlMissing) DOM.googleUrlMissing.style.display = 'none';

      } else {
        // No Google Review URL configured — disable the button
        DOM.btnPostGoogle.removeAttribute('href');
        DOM.btnPostGoogle.classList.add('disabled');
        DOM.btnPostGoogle.setAttribute('aria-disabled', 'true');
        DOM.btnPostGoogle.addEventListener('click', (e) => e.preventDefault());
        DOM.btnPostGoogle.style.display = 'inline-flex';
        if (DOM.btnCopyReview) DOM.btnCopyReview.style.display = 'none';
        if (DOM.googleUrlMissing) DOM.googleUrlMissing.style.display = 'block';
        if (DOM.googleInstructions) DOM.googleInstructions.style.display = 'none';
        if (DOM.autoCopyMsg) DOM.autoCopyMsg.style.display = 'none';
      }

      // Wire Copy Review button (manual backup for 4★/5★)
      if (DOM.btnCopyReview) {
        DOM.btnCopyReview.addEventListener('click', () => {
          Clipboard.copy(State.selectedFeedback, () => {
            Toast.show(I18n.t('copySuccessToast'));
          });
        });
      }

    } else {
      // ── 1★/2★/3★: Private Feedback Only (Zero Google Redirection / Buttons) ──
      if (DOM.btnPostGoogle) {
        DOM.btnPostGoogle.removeAttribute('href');
        DOM.btnPostGoogle.classList.add('disabled');
        DOM.btnPostGoogle.style.display = 'none';
      }
      if (DOM.btnCopyReview) DOM.btnCopyReview.style.display = 'none';
      if (DOM.successActions) DOM.successActions.style.display = 'none';
      if (DOM.autoCopyMsg) DOM.autoCopyMsg.style.display = 'none';
      if (DOM.googleInstructions) DOM.googleInstructions.style.display = 'none';
      if (DOM.googleUrlMissing) DOM.googleUrlMissing.style.display = 'none';
    }
  },

  /**
   * Open the Google review page in a new tab and update the instructions card.
   * Called only after the clipboard copy attempt completes (success or fail).
   * @param {string} googleUrl — The base Google review URL.
   */
  _openGoogleTab(googleUrl) {
    // Small delay so the Thank You screen renders fully before the tab opens
    setTimeout(() => {
      const tab = window.open(googleUrl, '_blank', 'noopener,noreferrer');

      if (tab) {
        // ── Tab opened — show Ctrl+V paste instructions ───────────────────
        if (DOM.googleInstructions) {
          DOM.googleInstructions.innerHTML = `
            <div class="instructions-icon" aria-hidden="true">📋</div>
            <p class="instructions-text">
              <strong>${escapeHtml(I18n.t('googleTabOpenTitle'))}</strong><br /><br />
              ${escapeHtml(I18n.t('googleTabOpenBody'))}<br />
              ${escapeHtml(I18n.t('clickTextBox'))}<br />
              ${escapeHtml(I18n.t('pressPaste'))} <kbd style="
                display:inline-block;
                padding:2px 8px;
                background:#2a2a3a;
                border:1px solid #555;
                border-radius:4px;
                font-family:monospace;
                font-size:0.95em;
                color:#00E5FF;
                letter-spacing:1px;
              ">Ctrl + V</kbd> ${escapeHtml(I18n.t('toPaste'))}<br />
              ${escapeHtml(I18n.t('selectStars'))}<br />
              ${escapeHtml(I18n.t('clickPost'))} <strong>${escapeHtml(I18n.t('postWord'))}</strong> ✅
            </p>
          `;
        }
      } else {
        // ── Browser blocked popup — manual click needed ───────────────────
        if (DOM.googleInstructions) {
          DOM.googleInstructions.innerHTML = `
            <div class="instructions-icon" aria-hidden="true">📋</div>
            <p class="instructions-text">
              ${escapeHtml(I18n.t('popupBlockedTitle'))}<br /><br />
              ${escapeHtml(I18n.t('popupBlockedBody'))}<br />
              ${escapeHtml(I18n.t('clickTextBox'))}<br />
              ${escapeHtml(I18n.t('pressPaste'))} <kbd style="
                display:inline-block;
                padding:2px 8px;
                background:#2a2a3a;
                border:1px solid #555;
                border-radius:4px;
                font-family:monospace;
                font-size:0.95em;
                color:#00E5FF;
                letter-spacing:1px;
              ">Ctrl + V</kbd> ${escapeHtml(I18n.t('toPaste'))}<br />
              ${escapeHtml(I18n.t('selectStars'))}<br />
              ${escapeHtml(I18n.t('clickPost'))} <strong>${escapeHtml(I18n.t('postWord'))}</strong> ✅
            </p>
          `;
        }
      }
    }, 400);
  },

  /** Show an inline validation message. */
  showValidation(type, icon, message) {
    DOM.validationMsg.className = `validation-message ${type}`;
    DOM.validationIcon.textContent = icon;
    DOM.validationText.textContent = message;
  },

  clearValidation() {
    DOM.validationMsg.className = 'validation-message';
  },

  /** Show the top-level error banner with an optional Try Again button. */
  showErrorBanner(message, showRetry = false, retryRating = null) {
    DOM.errorBannerText.textContent = message;

    // Remove any old retry button
    const oldBtn = DOM.errorBanner.querySelector('.btn-retry-ai');
    if (oldBtn) oldBtn.remove();

    // Add Try Again button if this is an AI error
    if (showRetry && retryRating) {
      const btn = document.createElement('button');
      btn.textContent = I18n.t('tryAgain');
      btn.className   = 'btn-retry-ai';
      btn.style.cssText = [
        'margin-left:12px', 'padding:4px 14px',
        'background:#00B4D8', 'color:#fff',
        'border:none', 'border-radius:6px',
        'font-size:0.8rem', 'font-weight:600',
        'cursor:pointer', 'vertical-align:middle'
      ].join(';');
      btn.onclick = () => {
        UIState.clearErrorBanner();
        API.fetchSuggestions(retryRating);
      };
      DOM.errorBanner.appendChild(btn);
    }

    DOM.errorBanner.classList.add('active');
  },

  clearErrorBanner() {
    const oldBtn = DOM.errorBanner.querySelector('.btn-retry-ai');
    if (oldBtn) oldBtn.remove();
    DOM.errorBanner.classList.remove('active');
  },
};

/* ============================================================
   STAR RATING MANAGER
   ============================================================ */
const StarRating = {
  /** Attach change listeners to all star radio inputs. */
  init() {
    DOM.starInputs.forEach(input => {
      input.addEventListener('change', StarRating.onRatingChange);
    });
  },

  /** Called whenever the user selects (or changes) a star rating. */
  onRatingChange(event) {
    const rating = parseInt(event.target.value, 10);

    // If rating changed, discard previous suggestions
    if (State.selectedRating !== rating) {
      State.selectedRating = rating;
      State.selectedFeedback = null;
      State.selectedResponseId = null;
      State.responseIds = [];
      SuggestionCards.clear();
      UIState.resetManual();
      UIState.hideSuggestionsAndSubmit();
    }

    // Update the rating badge
    StarRating.updateBadge(rating);

    // Fetch AI suggestions (Phase 2)
    API.fetchSuggestions(rating);
  },

  /** Update the descriptive badge below the stars. */
  updateBadge(rating) {
    const info = RATING_LABELS[rating];
    if (!info) return;

    DOM.ratingBadge.textContent = `${info.emoji}  ${I18n.ratingText(rating)}`;
    DOM.ratingBadge.className   = `rating-badge ${info.cls}`;

    // Trigger CSS visibility transition
    requestAnimationFrame(() => {
      DOM.ratingBadge.classList.add('visible');
    });
  },
};

/* ============================================================
   SUGGESTION CARDS MANAGER
   ============================================================ */
const SuggestionCards = {
  /** Clear the suggestions grid and reset selection. */
  clear() {
    UIState.resetSubmitPosition();
    DOM.suggestionsGrid.innerHTML = '';
    State.selectedFeedback = null;
    State.selectedResponseId = null;
  },

  /**
   * Render a list of AI-generated suggestions as selectable cards.
   * @param {string[]} suggestions — Array of feedback strings.
   */
  render(suggestions) {
    SuggestionCards.clear();

    if (!suggestions || suggestions.length === 0) {
      UIState.showValidation('error', '⚠️', I18n.t('noSuggestions'));
      return;
    }

    // Strict display limit: maximum 3 suggestions for 1–3 star ratings, full list for 4–5 stars
    const maxSuggestions = (State.selectedRating && State.selectedRating <= 3) ? 3 : suggestions.length;
    const displayList = suggestions.slice(0, maxSuggestions);

    displayList.forEach((text, index) => {
      const card = SuggestionCards.createCard(text, index);
      DOM.suggestionsGrid.appendChild(card);
    });

    UIState.showSuggestions();
  },

  /**
   * Create a single suggestion card DOM element.
   * @param {string} text — The feedback suggestion text.
   * @param {number} index — Card index for aria labels.
   * @returns {HTMLElement}
   */
  createCard(text, index) {
    const card = document.createElement('div');
    card.className       = 'suggestion-card';
    card.setAttribute('role', 'button');
    card.setAttribute('tabindex', '0');
    card.setAttribute('aria-label', `Feedback suggestion ${index + 1}: ${text}`);

    card.innerHTML = `
      <div class="card-indicator" aria-hidden="true"></div>
      <p class="card-text">${escapeHtml(text)}</p>
    `;

    // Click handler
    card.addEventListener('click', () => SuggestionCards.select(card, text));

    // Keyboard handler (Enter / Space)
    card.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        SuggestionCards.select(card, text);
      }
    });

    return card;
  },

  /**
   * Mark a card as selected; deselect all others.
   * @param {HTMLElement} selectedCard — The clicked card element.
   * @param {string} text — The feedback text.
   */
  select(selectedCard, text) {
    // Deselect all cards
    document.querySelectorAll('#suggestionsGrid .suggestion-card').forEach(c => {
      c.classList.remove('selected');
      c.setAttribute('aria-pressed', 'false');
    });

    // Switch off manual mode if it was active
    State.mode = 'suggestions';
    State.isManual = false;

    // Reset manual textarea so card selection is unambiguous
    if (DOM.manualTextarea) DOM.manualTextarea.value = '';
    if (DOM.manualCharCounter) DOM.manualCharCounter.textContent = '0 / 1000';
    if (DOM.manualMinHint) DOM.manualMinHint.classList.remove('valid', 'invalid');

    // Select the clicked one
    selectedCard.classList.add('selected');
    selectedCard.setAttribute('aria-pressed', 'true');
    State.selectedFeedback = text;

    // Get response ID for static pool tracking
    const cards = Array.from(DOM.suggestionsGrid.querySelectorAll('.suggestion-card'));
    const selectedIndex = cards.indexOf(selectedCard);
    State.selectedResponseId = (State.responseIds && State.responseIds[selectedIndex] !== undefined)
      ? State.responseIds[selectedIndex]
      : null;

    // Dynamically insert submit section immediately BELOW the entire row containing selectedCard
    SuggestionCards.positionSubmitSection(selectedCard);

    // Auto-copy selected feedback to clipboard (for 4★/5★ public review flow)
    if (!State.selectedRating || State.selectedRating >= 4) {
      Clipboard.copy(text, () => {
        Toast.show(I18n.t('copySuccessToast') || 'Review copied successfully.');
      });
    }

    // Show submit button below the selected row and update texts
    UIState.showSubmit();
    UIState.clearValidation();
    UIState.updateSubmitTexts();
  },

  /**
   * Dynamically insert the submit button immediately BELOW the entire row
   * containing the selected feedback card, spanning the full width of both columns.
   * @param {HTMLElement} selectedCard — The currently selected card element.
   */
  positionSubmitSection(selectedCard) {
    if (!DOM.submitSection || !selectedCard || !DOM.suggestionsGrid) return;
    const cards = Array.from(DOM.suggestionsGrid.querySelectorAll('.suggestion-card'));
    const selectedIndex = cards.indexOf(selectedCard);
    if (selectedIndex === -1) return;

    // Desktop/tablet (>= 480px) has 2 columns; mobile (< 480px) has 1 column
    const isTwoCol = window.matchMedia ? window.matchMedia('(min-width: 480px)').matches : true;
    const cols = isTwoCol ? 2 : 1;
    const rowIndex = Math.floor(selectedIndex / cols);
    const lastCardIndex = Math.min((rowIndex + 1) * cols - 1, cards.length - 1);
    const targetCard = cards[lastCardIndex];

    if (targetCard && targetCard.parentNode) {
      targetCard.parentNode.insertBefore(DOM.submitSection, targetCard.nextSibling);
    }
  },
};

/* ============================================================
   API — all fetch calls live here
   ============================================================ */
const API = {
  /**
   * POST /generate-feedback — request AI suggestions for a given rating.
   * @param {number} rating — Star rating 1–5.
   */
  async fetchSuggestions(rating) {
    // Cancel any previous in-flight request before starting a new one.
    // This fixes the race condition where a slow 1-star response could
    // overwrite a 5-star request that was ignored by the old isLoadingAI guard.
    if (State._fetchController) {
      State._fetchController.abort();
    }
    State._fetchController = new AbortController();

    const reqId = ++State._activeRequestId;
    State.isLoadingAI = true;
    UIState.clearErrorBanner();
    UIState.showLoading();

    try {
      const response = await fetch('/generate-feedback', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ rating, business_id: BUSINESS_ID, language: State.language }),
        signal:  State._fetchController.signal,
      });

      const data = await response.json();

      // Guard against race conditions: if another request was triggered or rating switched, discard!
      if (reqId !== State._activeRequestId || (data.rating && data.rating !== State.selectedRating)) {
        console.warn(`[API] Discarding stale response for rating ${data.rating} (active: ${State.selectedRating})`);
        return;
      }

      if (!response.ok) {
        const detail = Array.isArray(data.detail) ? data.detail[0] : data.detail;
        throw new Error(detail || data.error || `Server error (${response.status}). Please try again.`);
      }

      if (!data.suggestions || data.suggestions.length === 0) {
        throw new Error('AI returned an empty suggestion list. Please try again.');
      }

      // SUCCESS — hide loading, show cards
      const maxCount = (rating <= 3) ? 3 : (data.suggestions ? data.suggestions.length : 10);
      State.responseIds = (data.response_ids || []).slice(0, maxCount);
      UIState.clearErrorBanner();
      SuggestionCards.render(data.suggestions);

    } catch (error) {
      // If the request was deliberately cancelled (user changed rating), ignore silently.
      if (error.name === 'AbortError' || reqId !== State._activeRequestId) return;

      console.error('[API] fetchSuggestions error:', error);
      DOM.loadingSection.classList.remove('active');

      // Build a friendly message
      let msg;
      if (!navigator.onLine || error.message.includes('Failed to fetch')) {
        msg = I18n.t('noInternet');
      } else if (
        error.message.toLowerCase().includes('busy') ||
        error.message.toLowerCase().includes('unavailable') ||
        error.message.toLowerCase().includes('temporarily')
      ) {
        msg = I18n.t('aiBusy');
      } else {
        msg = error.message || I18n.t('genericError');
      }

      // Show banner with a Try Again button so user doesn\'t have to re-select stars
      UIState.showErrorBanner(msg, true, rating);

    } finally {
      State.isLoadingAI = false;
    }
  },

  /**
   * POST /submit-feedback — submit the selected feedback to the server.
   */
  async submitFeedback() {
    // ── Duplicate-click guard ──────────────────────────────────────────────
    if (State.isSubmitting) return;
    State.isSubmitting = true;

    // Disable button immediately to prevent double-submit
    DOM.btnSubmit.disabled = true;
    DOM.btnSubmit.classList.add('loading');
    const isLowRating = State.selectedRating && State.selectedRating <= 3;
    DOM.btnText.textContent = (State.isManual || isLowRating)
      ? (I18n.t('submittingFeedback') || 'Submitting…')
      : I18n.t('submittingBtn');
    UIState.clearErrorBanner();

    try {
      const payload = {
        company:     COMPANY_NAME,
        company_id:  COMPANY_ID,
        rating:      State.selectedRating,
        feedback:    State.selectedFeedback,
        business_id: BUSINESS_ID,
        response_id: State.isManual ? null : (State.selectedResponseId || null),
        is_manual:   !!State.isManual,
      };

      const response = await fetch('/submit-feedback', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(payload),
      });

      const data = await response.json();

      if (!response.ok) {
        // Use the server's specific error message
        throw new Error(data.error || `Submission failed (${response.status}). Please try again.`);
      }

      // ── SUCCESS: show the Thank You screen ────────────────────────────────
      UIState.showSuccess();

    } catch (error) {
      console.error('[API] submitFeedback error:', error);

      // Friendly message based on error type
      let userMessage;
      if (!navigator.onLine || error.message.includes('Failed to fetch')) {
        userMessage = I18n.t('noInternet');
      } else if (error.message.includes('503') || error.message.includes('unavailable')) {
        userMessage = I18n.t('aiBusy');
      } else {
        userMessage = error.message || I18n.t('submitFail');
      }

      UIState.showErrorBanner(userMessage);

      // Re-enable button so user can retry
      State.isSubmitting = false;
      DOM.btnSubmit.disabled = false;
      DOM.btnSubmit.classList.remove('loading');
      DOM.btnText.textContent = I18n.t('submitBtn');
      return; // early return — don't run finally block's re-enable
    }

    // Only reached on SUCCESS — keep button disabled (form is done)
    State.isSubmitting = false;
  },
};

/* ============================================================
   MANUAL FEEDBACK HANDLER
   ============================================================ */
const ManualFeedback = {
  init() {
    if (DOM.manualTextarea) {
      DOM.manualTextarea.addEventListener('input', ManualFeedback.onInput);
      DOM.manualTextarea.addEventListener('focus', ManualFeedback.onFocus);
    }
  },

  onInput() {
    const raw = DOM.manualTextarea ? DOM.manualTextarea.value : '';
    const trimmed = raw.trim();

    if (DOM.manualCharCounter) {
      DOM.manualCharCounter.textContent = `${raw.length} / 1000`;
    }

    if (trimmed.length > 0) {
      // User is writing manual feedback — deselect any selected suggestion card
      document.querySelectorAll('#suggestionsGrid .suggestion-card').forEach(c => {
        c.classList.remove('selected');
        c.setAttribute('aria-pressed', 'false');
      });
      State.mode = 'manual';
      State.isManual = true;
      State.selectedFeedback = trimmed;
      State.selectedResponseId = null;
      ManualFeedback.updateHint(trimmed);

      // Position submit section right below manual pane
      if (DOM.submitSection && DOM.manualPane && DOM.suggestionsWrapper) {
        DOM.manualPane.parentNode.insertBefore(DOM.submitSection, DOM.suggestionsWrapper);
      }
      UIState.showSubmit();
    } else {
      State.isManual = false;
      State.selectedFeedback = null;
      if (DOM.manualMinHint) {
        DOM.manualMinHint.classList.remove('valid', 'invalid');
      }
      DOM.submitSection.classList.remove('active');
      UIState.resetSubmitPosition();
    }

    UIState.updateSubmitTexts();
  },

  onFocus() {
    const raw = DOM.manualTextarea ? DOM.manualTextarea.value : '';
    const trimmed = raw.trim();
    if (trimmed.length > 0) {
      document.querySelectorAll('#suggestionsGrid .suggestion-card').forEach(c => {
        c.classList.remove('selected');
        c.setAttribute('aria-pressed', 'false');
      });
      State.mode = 'manual';
      State.isManual = true;
      State.selectedFeedback = trimmed;
      State.selectedResponseId = null;
      if (DOM.submitSection && DOM.manualPane && DOM.suggestionsWrapper) {
        DOM.manualPane.parentNode.insertBefore(DOM.submitSection, DOM.suggestionsWrapper);
      }
      UIState.showSubmit();
      UIState.updateSubmitTexts();
    }
  },

  updateHint(text) {
    if (!DOM.manualMinHint) return;
    if (text.length >= 5) {
      DOM.manualMinHint.classList.add('valid');
      DOM.manualMinHint.classList.remove('invalid');
      UIState.clearValidation();
    } else {
      DOM.manualMinHint.classList.remove('valid');
    }
  },
};

/* ============================================================
   SUBMIT BUTTON HANDLER
   ============================================================ */
function onSubmitClick() {
  // Validate: rating must be selected
  if (!State.selectedRating) {
    UIState.showValidation('error', '⚠️', I18n.t('selectRating'));
    return;
  }

  const manualVal = (DOM.manualTextarea ? DOM.manualTextarea.value : '').trim();

  // If manual feedback has text, validate and use it
  if (State.isManual || (manualVal.length > 0 && !State.selectedFeedback)) {
    if (!manualVal) {
      UIState.showValidation('error', '⚠️', I18n.t('manualEmptyError'));
      if (DOM.manualMinHint) DOM.manualMinHint.classList.add('invalid');
      if (DOM.manualTextarea) DOM.manualTextarea.focus();
      return;
    }
    if (manualVal.length < 5) {
      UIState.showValidation('error', '⚠️', I18n.t('manualShortError'));
      if (DOM.manualMinHint) DOM.manualMinHint.classList.add('invalid');
      if (DOM.manualTextarea) DOM.manualTextarea.focus();
      return;
    }
    State.isManual = true;
    State.selectedFeedback = manualVal;
    State.selectedResponseId = null;
  } else {
    // Validate: a suggestion card must be selected
    if (!State.selectedFeedback) {
      if (manualVal.length > 0 && manualVal.length < 5) {
        UIState.showValidation('error', '⚠️', I18n.t('manualShortError'));
        if (DOM.manualMinHint) DOM.manualMinHint.classList.add('invalid');
        if (DOM.manualTextarea) DOM.manualTextarea.focus();
        return;
      }
      UIState.showValidation('error', '⚠️', I18n.t('selectSuggestion'));
      return;
    }
  }

  API.submitFeedback();
}

/* ============================================================
   TOAST — snackbar notification helper
   ============================================================ */
const Toast = {
  _timer: null,

  /**
   * Show a toast message for a given duration.
   * @param {string} message  — Text to display.
   * @param {number} duration — Milliseconds before auto-hide (default 3000).
   */
  show(message = 'Done.', duration = 3000) {
    if (!DOM.copyToast || !DOM.toastMsg) return;
    DOM.toastMsg.textContent = message;
    DOM.copyToast.classList.add('visible');

    // Clear any existing timer
    if (Toast._timer) clearTimeout(Toast._timer);
    Toast._timer = setTimeout(() => {
      DOM.copyToast.classList.remove('visible');
    }, duration);
  },
};

/* ============================================================
   CLIPBOARD — Clipboard API with graceful fallback
   ============================================================ */
const Clipboard = {
  /**
   * Copy text to clipboard.
   * Tries the modern Clipboard API first; falls back to execCommand.
   * @param {string}   text      — Text to copy.
   * @param {Function} onSuccess — Called if copy succeeds.
   * @param {Function} onFail    — Called if copy fails (optional).
   */
  copy(text, onSuccess, onFail) {
    if (!text) {
      if (typeof onFail === 'function') onFail();
      return;
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      // Modern Clipboard API
      navigator.clipboard.writeText(text)
        .then(() => { if (typeof onSuccess === 'function') onSuccess(); })
        .catch(() => {
          // Clipboard API exists but failed (e.g. permissions) — try fallback
          Clipboard._fallback(text, onSuccess, onFail);
        });
    } else {
      // No Clipboard API — use execCommand fallback
      Clipboard._fallback(text, onSuccess, onFail);
    }
  },

  /** execCommand-based fallback for older browsers / restricted contexts. */
  _fallback(text, onSuccess, onFail) {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.cssText = 'position:fixed;opacity:0;pointer-events:none;';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok) {
        if (typeof onSuccess === 'function') onSuccess();
      } else {
        if (typeof onFail === 'function') onFail();
      }
    } catch (err) {
      console.warn('[Clipboard] execCommand fallback failed:', err);
      if (typeof onFail === 'function') onFail();
    }
  },
};

/* ============================================================
   UTILITY — sanitize text before inserting into DOM
   ============================================================ */
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/* ============================================================
   LANGUAGE — chips + dropdown, persist, refetch reviews
   ============================================================ */
const Language = {
  detect() {
    try {
      const saved = localStorage.getItem('reviewLanguage');
      if (saved && LANG_OPTIONS.some((l) => l.code === saved)) return saved;
    } catch (_) { /* ignore */ }
    const nav = (navigator.language || 'en').toLowerCase();
    const match = LANG_OPTIONS.find((l) => nav.startsWith(l.code));
    return match ? match.code : 'en';
  },

  syncUi(lang) {
    if (DOM.langSelect) DOM.langSelect.value = lang;
    DOM.langChips.forEach((chip) => {
      chip.classList.toggle('active', chip.dataset.lang === lang);
    });
  },

  set(lang, { refetch = true } = {}) {
    if (!LANG_OPTIONS.some((l) => l.code === lang)) lang = 'en';
    const changed = State.language !== lang;
    State.language = lang;
    I18n.lang = lang;
    try { localStorage.setItem('reviewLanguage', lang); } catch (_) { /* ignore */ }
    Language.syncUi(lang);
    I18n.apply();
    if (State.selectedRating && DOM.ratingBadge.classList.contains('visible')) {
      StarRating.updateBadge(State.selectedRating);
    }
    if (refetch && changed && State.selectedRating && !State.isSubmitting) {
      State.selectedFeedback = null;
      SuggestionCards.clear();
      UIState.hideSuggestionsAndSubmit();
      API.fetchSuggestions(State.selectedRating);
    }
  },

  init() {
    const lang = Language.detect();
    Language.set(lang, { refetch: false });
    if (DOM.langSelect) {
      DOM.langSelect.addEventListener('change', () => Language.set(DOM.langSelect.value));
    }
    DOM.langChips.forEach((chip) => {
      chip.addEventListener('click', () => Language.set(chip.dataset.lang));
    });
  },
};

/* ============================================================
   INITIALISE
   ============================================================ */
document.addEventListener('DOMContentLoaded', () => {
  Language.init();
  StarRating.init();
  ManualFeedback.init();

  // Wire up submit button
  if (DOM.btnSubmit) {
    DOM.btnSubmit.addEventListener('click', onSubmitClick);
  }

  // Handle responsive rearrangement of submit button below selected row on screen resize
  window.addEventListener('resize', () => {
    const selectedCard = DOM.suggestionsGrid ? DOM.suggestionsGrid.querySelector('.suggestion-card.selected') : null;
    if (selectedCard) {
      SuggestionCards.positionSubmitSection(selectedCard);
    }
  });

  console.info('[TechnoBuzz] Feedback app initialised — all phases ready.');
});
