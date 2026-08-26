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
  5: { emoji: '🤩', text: 'Excellent Experience!',      cls: 'r5' },
  4: { emoji: '😊', text: 'Great, with minor notes.',   cls: 'r4' },
  3: { emoji: '😐', text: 'Average Experience.',        cls: 'r3' },
  2: { emoji: '😕', text: 'Needs Improvement.',         cls: 'r2' },
  1: { emoji: '😞', text: 'Poor Experience.',           cls: 'r1' },
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
  successReviewText: document.getElementById('successReviewText'),
  successReviewCard: document.getElementById('successReviewCard'),
  btnCopyReview:     document.getElementById('btnCopyReview'),
  btnPostGoogle:     document.getElementById('btnPostGoogle'),
  googleUrlMissing:  document.getElementById('googleUrlMissing'),
  autoCopyMsg:       document.getElementById('autoCopyMsg'),
  googleInstructions:document.getElementById('googleInstructions'),

  // Toast
  copyToast:         document.getElementById('copyToast'),
  toastMsg:          document.getElementById('toastMsg'),
};

/* ============================================================
   STATE — single source of truth for app state
   ============================================================ */
const State = {
  selectedRating:    null,    // 1–5
  selectedFeedback:  null,    // string
  isSubmitting:      false,   // prevent double-submit
  isLoadingAI:       false,   // prevent concurrent AI calls
};

/* ============================================================
   UI STATE MANAGER
   ============================================================ */
const UIState = {
  /** Show the AI loading spinner, hide suggestions. */
  showLoading() {
    DOM.loadingSection.classList.add('active');
    DOM.suggestionsSection.classList.remove('active');
    DOM.submitSection.classList.remove('active');
    UIState.clearValidation();
    UIState.clearErrorBanner();
  },

  /** Show the suggestions grid, hide spinner. */
  showSuggestions() {
    DOM.loadingSection.classList.remove('active');
    DOM.suggestionsSection.classList.add('active');
  },

  /** Show the submit button. */
  showSubmit() {
    DOM.submitSection.classList.add('active');
  },

  /** Hide suggestions and submit; reset. */
  hideSuggestionsAndSubmit() {
    DOM.suggestionsSection.classList.remove('active');
    DOM.submitSection.classList.remove('active');
    DOM.loadingSection.classList.remove('active');
    State.selectedFeedback = null;
    UIState.clearValidation();
    UIState.clearErrorBanner();
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

    // ── Google Review — Clipboard-First, Then Open Tab ──────────────────────
    // IMPORTANT: Google's review page does NOT support any URL parameter to
    // pre-fill the text box. The ONLY supported approach is:
    //   1. Copy the review text to clipboard FIRST (before tab opens).
    //   2. Open the Google review page in a new tab.
    //   3. User clicks in the text box and presses Ctrl+V (one action).
    // This is enforced by browser same-origin security — no script on our
    // domain can interact with a page on google.com.
    const googleUrl = (DOM.successScreen.dataset.googleReviewUrl || '').trim();

    if (googleUrl) {

      // ── STEP 1: Copy text to clipboard NOW, before the tab opens ──────────
      // This guarantees the text is in the clipboard the moment Google opens.
      Clipboard.copy(
        State.selectedFeedback,
        () => {
          // ── STEP 2 (copy succeeded): Open the Google review tab ───────────
          if (DOM.autoCopyMsg) {
            DOM.autoCopyMsg.textContent = '📋 Review copied to clipboard!';
            DOM.autoCopyMsg.style.display = 'block';
          }
          UIState._openGoogleTab(googleUrl);
        },
        () => {
          // ── STEP 2 (copy failed): Still open tab, warn user to copy manually
          if (DOM.autoCopyMsg) {
            DOM.autoCopyMsg.textContent =
              '⚠️ Auto-copy failed. Use the "Copy Review" button, then paste on Google.';
            DOM.autoCopyMsg.style.display = 'block';
          }
          UIState._openGoogleTab(googleUrl);
        }
      );

      // ── Configure the manual "Post on Google" fallback button ─────────────
      DOM.btnPostGoogle.href = googleUrl;
      DOM.btnPostGoogle.classList.remove('disabled');
      if (DOM.googleUrlMissing) DOM.googleUrlMissing.style.display = 'none';

    } else {
      // No Google Review URL configured — disable the button
      DOM.btnPostGoogle.removeAttribute('href');
      DOM.btnPostGoogle.classList.add('disabled');
      DOM.btnPostGoogle.setAttribute('aria-disabled', 'true');
      DOM.btnPostGoogle.addEventListener('click', (e) => e.preventDefault());
      if (DOM.googleUrlMissing) DOM.googleUrlMissing.style.display = 'block';
      if (DOM.googleInstructions) DOM.googleInstructions.style.display = 'none';
      if (DOM.autoCopyMsg) DOM.autoCopyMsg.style.display = 'none';
    }

    // ── Wire Copy Review button (manual backup) ─────────────────────────────
    if (DOM.btnCopyReview) {
      DOM.btnCopyReview.addEventListener('click', () => {
        Clipboard.copy(State.selectedFeedback, () => {
          Toast.show('✅ Review copied! Now paste it on Google.');
        });
      });
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
              <strong>Google Review tab is open!</strong><br /><br />
              Your review text is already copied.<br />
              👉 Click inside the Google text box<br />
              👉 Press <kbd style="
                display:inline-block;
                padding:2px 8px;
                background:#2a2a3a;
                border:1px solid #555;
                border-radius:4px;
                font-family:monospace;
                font-size:0.95em;
                color:#00E5FF;
                letter-spacing:1px;
              ">Ctrl + V</kbd> to paste<br />
              👉 Click <strong>Post</strong> ✅
            </p>
          `;
        }
      } else {
        // ── Browser blocked popup — manual click needed ───────────────────
        if (DOM.googleInstructions) {
          DOM.googleInstructions.innerHTML = `
            <div class="instructions-icon" aria-hidden="true">📋</div>
            <p class="instructions-text">
              Your review is copied to clipboard.<br /><br />
              👉 Click <strong>"Post on Google"</strong> button below<br />
              👉 Click inside the Google text box<br />
              👉 Press <kbd style="
                display:inline-block;
                padding:2px 8px;
                background:#2a2a3a;
                border:1px solid #555;
                border-radius:4px;
                font-family:monospace;
                font-size:0.95em;
                color:#00E5FF;
                letter-spacing:1px;
              ">Ctrl + V</kbd> to paste<br />
              👉 Click <strong>Post</strong> ✅
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
      btn.textContent = 'Try Again';
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
      SuggestionCards.clear();
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

    DOM.ratingBadge.textContent = `${info.emoji}  ${info.text}`;
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
    DOM.suggestionsGrid.innerHTML = '';
    State.selectedFeedback = null;
  },

  /**
   * Render a list of AI-generated suggestions as selectable cards.
   * @param {string[]} suggestions — Array of feedback strings.
   */
  render(suggestions) {
    SuggestionCards.clear();

    if (!suggestions || suggestions.length === 0) {
      UIState.showValidation('error', '⚠️', 'No suggestions were generated. Please try again.');
      return;
    }

    suggestions.forEach((text, index) => {
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
    // Deselect all
    document.querySelectorAll('.suggestion-card').forEach(c => {
      c.classList.remove('selected');
      c.setAttribute('aria-pressed', 'false');
    });

    // Select the clicked one
    selectedCard.classList.add('selected');
    selectedCard.setAttribute('aria-pressed', 'true');
    State.selectedFeedback = text;

    // Show submit button and clear any validation errors
    UIState.showSubmit();
    UIState.clearValidation();
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
    // Prevent concurrent calls
    if (State.isLoadingAI) return;
    State.isLoadingAI = true;

    UIState.clearErrorBanner();
    UIState.showLoading();

    try {
      const response = await fetch('/generate-feedback', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ rating, business_id: BUSINESS_ID }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || `Server error (${response.status}). Please try again.`);
      }

      if (!data.suggestions || data.suggestions.length === 0) {
        throw new Error('AI returned an empty suggestion list. Please try again.');
      }

      // SUCCESS — hide loading, show cards
      UIState.clearErrorBanner();
      SuggestionCards.render(data.suggestions);

    } catch (error) {
      console.error('[API] fetchSuggestions error:', error);
      DOM.loadingSection.classList.remove('active');

      // Build a friendly message
      let msg;
      if (!navigator.onLine || error.message.includes('Failed to fetch')) {
        msg = 'No internet connection. Please check your WiFi and try again.';
      } else if (
        error.message.toLowerCase().includes('busy') ||
        error.message.toLowerCase().includes('unavailable') ||
        error.message.toLowerCase().includes('temporarily')
      ) {
        msg = '✨ AI is busy right now. Please tap Try Again in a moment.';
      } else {
        msg = error.message || 'Could not load suggestions. Please try again.';
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
    DOM.btnText.textContent = 'Submitting…';
    UIState.clearErrorBanner();

    try {
      const payload = {
        company:    COMPANY_NAME,
        company_id: COMPANY_ID,
        rating:     State.selectedRating,
        feedback:   State.selectedFeedback,
        business_id: BUSINESS_ID,
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
        userMessage = 'No internet connection. Please check your network and try again.';
      } else if (error.message.includes('503') || error.message.includes('unavailable')) {
        userMessage = 'Database is temporarily unavailable. Please try again in a moment.';
      } else {
        userMessage = error.message || 'Failed to submit feedback. Please try again.';
      }

      UIState.showErrorBanner(userMessage);

      // Re-enable button so user can retry
      State.isSubmitting = false;
      DOM.btnSubmit.disabled = false;
      DOM.btnSubmit.classList.remove('loading');
      DOM.btnText.textContent = 'Submit Feedback';
      return; // early return — don't run finally block's re-enable
    }

    // Only reached on SUCCESS — keep button disabled (form is done)
    State.isSubmitting = false;
  },
};

/* ============================================================
   SUBMIT BUTTON HANDLER
   ============================================================ */
function onSubmitClick() {
  // Validate: rating must be selected
  if (!State.selectedRating) {
    UIState.showValidation('error', '⚠️', 'Please select a star rating first.');
    return;
  }

  // Validate: a suggestion card must be selected
  if (!State.selectedFeedback) {
    UIState.showValidation('error', '⚠️', 'Please select one feedback suggestion before submitting.');
    return;
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
   INITIALISE
   ============================================================ */
document.addEventListener('DOMContentLoaded', () => {
  StarRating.init();

  // Wire up submit button
  if (DOM.btnSubmit) {
    DOM.btnSubmit.addEventListener('click', onSubmitClick);
  }

  console.info('[TechnoBuzz] Feedback app initialised — all phases ready.');
});
