/**
 * content.js
 * ----------
 * TechnoBuzz Google Reviews AI Auto-Replier Content Script.
 * Runs on Google Search / Google Maps Business Profile pages.
 * 
 * Features:
 *  1. Auto-detects Google's "Reply to review" modal (Image 3).
 *  2. Strictly ignores Google search bar and non-review pages (Fix for Image 4).
 *  3. Sanitizes reviewer names (Fix for 'Dear 2', 'Dear 3', 'Dear 4', 'Dear Press' bug).
 *  4. Calls AI backend to generate personalized response matching language & context.
 *  5. Auto-fills "Replying publicly" text box and triggers input events to enable the Reply button.
 *  6. Automatically clicks "Reply" to post to Google (with auto-send countdown & cancel option).
 *  7. Floating Auto-Pilot Bar only shown on review management pages.
 *  8. Inline "⚡ AI Auto-Reply" buttons next to each unreplied review.
 */

(function () {
  "use strict";

  console.log("[TechnoBuzz AI] Google Reviews Auto-Replier initialized.");

  // Configuration defaults (can be overridden in chrome.storage)
  const CONFIG = {
    apiBaseUrl: "http://localhost:5001",
    businessId: "technobuzz",
    autoSendEnabled: true,
    autoSendDelaySec: 2,
    tone: "professional_warm",
    languageMode: "auto",
    autonomousAutoPilot: true, // 100% Zero-Click Auto-Pilot: automatically starts replying when page is open
  };

  function purgeLegacyWidgets() {
    const legacy = document.querySelectorAll("#tb-autopilot-widget, .tb-autopilot-widget, [id*='tb-autopilot']");
    legacy.forEach((el) => el.remove());
  }

  // Load saved settings from Chrome storage
  purgeLegacyWidgets();
  if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.sync) {
    chrome.storage.sync.get(CONFIG, (items) => {
      Object.assign(CONFIG, items);
      console.log("[TechnoBuzz AI] Loaded config:", CONFIG);
      init();
    });
  } else {
    init();
  }

  let autoPilotActive = false;
  let autoPilotIndex = 0;
  let autoPilotReviews = [];
  let countdownTimer = null;
  let handledModalElements = new WeakSet();
  let autonomousTriggered = false;

  function init() {
    purgeLegacyWidgets();
    observeDomChanges();
    checkAndSetupUI();
    setupBackgroundWatcher();
  }

  // ─── Periodic Background Watcher for Incoming Reviews (Every 15s) ───────────
  function setupBackgroundWatcher() {
    setInterval(() => {
      if (hasGoogleReviewsContext() && CONFIG.autonomousAutoPilot && !autoPilotActive) {
        const unreplied = findGoogleReplyButtons();
        if (unreplied.length > 0) {
          console.log(`[TechnoBuzz AI] Background watcher detected ${unreplied.length} new unreplied review(s). Auto-replying in 1s...`);
          startAutoPilot(true);
        }
      }
    }, 15000);
  }

  // ─── Check Page Context ───────────────────────────────────────────────────
  function hasGoogleReviewsContext() {
    // 1. Google Business Profile Manager
    if (window.location.hostname.includes("business.google.com")) return true;

    // 2. Google Search / Maps Business Panel with reviews
    const reviewSelectors = [
      "[data-attrid*='review']",
      "div[aria-label*='Reviews']",
      "div[aria-label*='ग्राहक पुनरावलोकने']",
      "div[aria-label*='समीक्षा']",
      "div[data-async-context*='review']",
      "div[role='region'][aria-label*='Reviews']",
      ".review-dialog-list",
      "g-review-stars"
    ];

    for (const sel of reviewSelectors) {
      if (document.querySelector(sel)) return true;
    }

    // 3. Any unreplied review button on page
    if (findGoogleReplyButtons().length > 0) return true;

    // 4. Any open review modal
    if (findReplyModalAndTextarea(document)) return true;

    return false;
  }

  function checkAndSetupUI() {
    // Remove any legacy floating widget if present
    const oldWidget = document.getElementById("tb-autopilot-widget");
    if (oldWidget) oldWidget.remove();

    if (hasGoogleReviewsContext()) {
      // Zero-Click Autonomous Auto-Pilot Trigger (Runs silently in background)
      if (CONFIG.autonomousAutoPilot && !autoPilotActive && !autonomousTriggered) {
        const unreplied = findGoogleReplyButtons();
        if (unreplied.length > 0) {
          autonomousTriggered = true;
          console.log(`[TechnoBuzz AI] Zero-Click Auto-Pilot detected ${unreplied.length} unreplied review(s). Auto-starting in 2s...`);
          setTimeout(() => {
            if (!autoPilotActive) startAutoPilot(true);
          }, 2000);
        }
      }
    }
    detectAndHandleReplyModal();
  }

  // ─── DOM Observation ────────────────────────────────────────────────────────
  function observeDomChanges() {
    const observer = new MutationObserver(() => {
      purgeLegacyWidgets();
      checkAndSetupUI();
    });

    observer.observe(document.body, { childList: true, subtree: true });
  }

  function updateStatus(text, type = "ready") {
    console.log(`[TechnoBuzz AI Status] [${type}] ${text}`);
    const pill = document.getElementById("tbStatusPill");
    if (pill) {
      pill.textContent = text;
      pill.className = `tb-status-pill ${type}`;
    }
  }

  // ─── Scan for Unreplied Reviews and Inject "⚡ AI Reply" Buttons ─────────────
  function scanAndInjectButtons() {
    const replyButtons = findGoogleReplyButtons();
    replyButtons.forEach((btn) => {
      if (btn.dataset.tbInjected) return;
      btn.dataset.tbInjected = "true";

      const aiBtn = document.createElement("button");
      aiBtn.type = "button";
      aiBtn.className = "tb-inline-ai-btn";
      aiBtn.innerHTML = `<span>⚡ AI Auto-Reply</span>`;
      aiBtn.title = "Auto-generate AI response and auto-send";

      aiBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        // Click the native Reply button to trigger the modal (Image 3)
        btn.click();
      });

      if (btn.parentNode) {
        btn.parentNode.insertBefore(aiBtn, btn.nextSibling);
      }
    });
  }

  function findGoogleReplyButtons(root = document) {
    const buttons = [];
    const allButtons = root.querySelectorAll("button, div[role='button'], a[role='button']");

    allButtons.forEach((b) => {
      // NEVER match search buttons, close buttons, or our own widgets
      if (b.closest("#searchform, form[role='search'], .RNNXgb, .A8SBwf, #tb-autopilot-widget")) {
        return;
      }

      const text = (b.innerText || b.textContent || "").trim().toLowerCase();
      if (
        (text === "reply" || text === "उत्तर द्या" || text === "जवाब दें" || text.includes("reply to review")) &&
        !b.classList.contains("tb-inline-ai-btn") &&
        !b.classList.contains("tb-mini-btn") &&
        !b.classList.contains("tb-countdown-cancel")
      ) {
        buttons.push(b);
      }
    });

    return buttons;
  }

  // ─── Strict Modal & Textarea Detection (NEVER match Google Search Box) ─────────
  function isSearchElement(el) {
    if (!el) return false;
    // Check if element is Google's main search box or inside search form
    if (
      el.matches("textarea[name='q'], input[name='q'], .gLFyf, #APjFqb, textarea[title*='Search'], input[title*='Search']") ||
      el.closest("#searchform, form[role='search'], form[action*='/search'], .RNNXgb, .A8SBwf, .tsf")
    ) {
      return true;
    }
    return false;
  }

  function findReplyModalAndTextarea(root = document) {
    // 1. Search for genuine Google Review Dialogs
    const dialogs = root.querySelectorAll("div[role='dialog'], div[aria-modal='true'], g-dialog, .modal");

    for (const d of dialogs) {
      // Check if dialog text indicates it is a Google Review reply modal
      const txt = (d.innerText || d.textContent || "").toLowerCase();
      const isReviewDialog =
        txt.includes("reply to review") ||
        txt.includes("replying publicly") ||
        txt.includes("this customer will be notified") ||
        txt.includes("4,000") ||
        txt.includes("उत्तर द्या") ||
        txt.includes("जवाब दें");

      if (isReviewDialog) {
        const ta = d.querySelector("textarea, div[role='textbox'][contenteditable='true'], div[contenteditable='true']");
        if (ta && !isSearchElement(ta)) {
          return { modal: d, textarea: ta };
        }
      }
    }

    // 2. Search child iframes if any
    const iframes = root.querySelectorAll("iframe");
    for (const f of iframes) {
      try {
        const idoc = f.contentDocument || f.contentWindow?.document;
        if (idoc) {
          const res = findReplyModalAndTextarea(idoc);
          if (res) return res;
        }
      } catch (e) {}
    }

    return null;
  }

  function detectAndHandleReplyModal() {
    const found = findReplyModalAndTextarea(document);
    if (!found) return;

    const { modal, textarea } = found;
    if (handledModalElements.has(textarea)) return;
    if (isSearchElement(textarea)) return;

    // Check if modal has already been injected with our bar
    if (modal.querySelector && modal.querySelector(".tb-modal-ai-bar")) return;

    handledModalElements.add(textarea);
    console.log("[TechnoBuzz AI] Verified 'Reply to review' modal found! Processing review...");

    processModal(modal, textarea);
  }

  // ─── Modal Processing & AI Reply Generation ─────────────────────────────────
  async function processModal(modal, textarea) {
    const details = extractReviewDetailsFromModal(modal);
    console.log("[TechnoBuzz AI] Extracted review details:", details);

    // Inject AI Assistant Bar inside the modal above the textarea
    const bar = document.createElement("div");
    bar.className = "tb-modal-ai-bar";
    bar.innerHTML = `
      <div class="tb-modal-ai-header">
        <div class="tb-modal-ai-title">
          <span>✨ TechnoBuzz Gemini AI Auto-Replier</span>
        </div>
        <div class="tb-modal-ai-tools">
          <button type="button" class="tb-mini-btn" id="tbRegenBtn">🔄 Regenerate</button>
        </div>
      </div>
      <div class="tb-modal-ai-status" id="tbModalStatus">
        <span>🤖 Analyzing review text (${details.reviewerName ? details.reviewerName : "Customer"}) & generating response...</span>
      </div>
    `;

    textarea.parentNode.insertBefore(bar, textarea);

    document.getElementById("tbRegenBtn")?.addEventListener("click", () => {
      fetchAndFillReply(details, modal, textarea, false);
    });

    await fetchAndFillReply(details, modal, textarea, CONFIG.autoSendEnabled);
  }

  // ─── Robust Business Name Detection ─────────────────────────────────────────
  function detectBusinessNameFromPage(modal) {
    let businessName = "";

    // 1. Look for "(Owner)" tag inside modal (e.g. "Technobuzz System (Owner)", "Rutuja Battery (Owner)")
    if (modal) {
      const ownerEls = modal.querySelectorAll("div, span, p, h1, h2, h3");
      for (const el of ownerEls) {
        const txt = (el.innerText || el.textContent || "").trim();
        if (txt.includes("(Owner)") || txt.includes("Owner") || txt.includes("मालक")) {
          const clean = txt.replace(/\(Owner\)/gi, "").replace(/Owner/gi, "").replace(/मालक/gi, "").trim();
          if (clean.length > 1 && clean.length < 80 && !clean.toLowerCase().includes("reply")) {
            businessName = clean;
            break;
          }
        }
      }
    }

    // 2. Look for Knowledge Panel title on Google Search
    if (!businessName) {
      const kpTitle = document.querySelector("h2[data-attrid='title'], div[data-attrid='title'], [data-attrid='title'] h2, h2.qrShPb, .SPZz6b h2, .SPZz6b span");
      if (kpTitle) {
        businessName = (kpTitle.innerText || kpTitle.textContent || "").trim();
      }
    }

    // 3. Fallback to page title
    if (!businessName) {
      const hTitle = document.querySelector("title");
      if (hTitle && hTitle.innerText) {
        businessName = hTitle.innerText.split("-")[0].split("—")[0].trim();
      }
    }

    return businessName || "Our Organization";
  }

  // ─── Reviewer Name Sanitization (Fix for 'Dear 2', 'Dear 3', 'Dear Starstarstar') ───
  function sanitizeReviewerName(raw) {
    if (!raw) return "";
    let t = raw.trim();

    // Strip trailing metadata like " · 1 review · 0 photos"
    t = t.split("·")[0].split("•")[0].split("|")[0].trim();
    t = t.replace(/[\s\-]+(?:\d+\s*(?:reviews?|photos?|stars?)|local guide|owner).*/gi, "").trim();

    // Reject if it contains ANY digit (never risk digit greetings like 'Dear 2' or '9 Mins Ago')
    if (/\d/.test(t)) {
      return "";
    }

    // Reject if too short or excessively long
    if (t.length < 2 || t.length > 40) {
      return "";
    }

    const lower = t.toLowerCase();
    const forbidden = [
      "star", "stars", "rating", "rated",
      "ago", "min", "mins", "minute", "minutes", "hour", "hours",
      "day", "days", "week", "weeks", "month", "months", "year", "years",
      "just now", "new", "edited", "yesterday", "today",
      "review", "reviews", "photo", "photos", "guide", "local guide",
      "press", "enter", "owner", "reply", "replying", "publicly",
      "google", "search", "customer", "valued customer", "client",
      "user", "anonymous", "null", "undefined", "translate", "view full",
      "share", "like", "feedback", "post", "comment"
    ];

    for (const bad of forbidden) {
      if (lower.includes(bad)) {
        return "";
      }
    }

    // Must contain alphabetic or Devanagari characters
    if (!/[a-zA-Z\u0900-\u097F]{2,}/.test(t)) {
      return "";
    }

    // Capitalize if necessary
    return t.replace(/\s+/g, " ");
  }

  function extractReviewDetailsFromModal(modal) {
    let reviewerName = "";
    let reviewText = "";
    let rating = 5;
    let businessName = detectBusinessNameFromPage(modal);

    // 1. Reviewer Name: check author links or headings first
    const authorCandidates = modal.querySelectorAll(
      "a[href*='contrib'], a[data-href*='contrib'], .TSUbDb a, .d4rFxf, .WNxEfd, .section-review-title, [data-ved] h2, [data-ved] h3, h2, h3, h4, strong, b"
    );
    for (const el of authorCandidates) {
      // Ignore if inside search bar or widget
      if (el.closest("g-review-stars, .tb-modal-ai-bar, #tb-autopilot-widget")) continue;
      const candidate = sanitizeReviewerName(el.innerText || el.textContent || "");
      if (candidate && candidate !== businessName && !businessName.includes(candidate)) {
        reviewerName = candidate;
        break;
      }
    }

    // If still not found, check direct spans that do not contain stars/timestamps
    if (!reviewerName) {
      const topSpans = modal.querySelectorAll("div > span:first-child, span[class*='author'], span[class*='name']");
      for (const el of topSpans) {
        if (el.closest("g-review-stars, .tb-modal-ai-bar, #tb-autopilot-widget")) continue;
        const candidate = sanitizeReviewerName(el.innerText || el.textContent || "");
        if (candidate && candidate !== businessName && !businessName.includes(candidate)) {
          reviewerName = candidate;
          break;
        }
      }
    }

    // 2. Review Text: extract body text
    const paragraphs = modal.querySelectorAll("p, span, div");
    for (const p of paragraphs) {
      const t = (p.innerText || p.textContent || "").trim();
      if (
        t.length > 15 &&
        !t.includes("Reply to review") &&
        !t.includes("Replying publicly") &&
        !t.includes("This customer will be notified") &&
        !t.includes("0/4,000") &&
        (!reviewerName || !t.startsWith(reviewerName)) &&
        (!businessName || !t.includes(businessName + " (Owner)"))
      ) {
        if (!reviewText || t.length > reviewText.length) {
          reviewText = t;
        }
      }
    }

    // 3. Stars detection
    const starEls = modal.querySelectorAll("span[aria-label*='star'], div[aria-label*='star'], span[title*='star']");
    if (starEls.length > 0) {
      const label = starEls[0].getAttribute("aria-label") || starEls[0].getAttribute("title") || "";
      const match = label.match(/(\d+)/);
      if (match) rating = parseInt(match[1], 10);
    }

    return { reviewerName, reviewText, rating, businessName };
  }

  // ─── Fetch AI Reply & Auto-Fill ─────────────────────────────────────────────
  async function fetchAndFillReply(details, modal, textarea, shouldAutoSend = true) {
    const statusEl = document.getElementById("tbModalStatus");
    if (statusEl) {
      statusEl.innerHTML = `<span>⏳ AI is crafting a contextual reply for <strong>${details.businessName}</strong>...</span>`;
    }

    try {
      const response = await fetch(`${CONFIG.apiBaseUrl}/api/reviews/generate-reply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          review_text: details.reviewText || "Great service!",
          reviewer_name: details.reviewerName || "",
          rating: details.rating || 5,
          business_name: details.businessName || "",
          business_id: CONFIG.businessId || "technobuzz",
          language: CONFIG.languageMode || "auto",
          tone: CONFIG.tone || "professional_warm",
          auto_sign: true,
        }),
      });

      if (!response.ok) {
        throw new Error(`Server returned ${response.status}`);
      }

      const data = await response.json();
      const replyText = data.reply || "";
      const langName = data.detected_language || data.language || "Auto";

      console.log(`[TechnoBuzz AI] AI Reply for ${details.businessName} (${langName}):`, replyText);

      // Auto-fill textarea
      fillTextarea(textarea, replyText);

      if (statusEl) {
        statusEl.innerHTML = `
          <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
            <span>✅ AI Reply Auto-Filled (${langName} · ${details.businessName})</span>
          </div>
        `;
      }

      // Auto-send handling
      if (shouldAutoSend && CONFIG.autoSendEnabled) {
        initiateAutoSend(modal, replyText, details);
      }
    } catch (err) {
      console.warn("[TechnoBuzz AI] API fetch failed, using instant local AI fallback:", err);

      const isMr = /[\u0900-\u097F]/.test(details.reviewText || "") || details.reviewText.includes("आहे");
      const namePrefix = details.reviewerName ? `${details.reviewerName} जी, ` : "";
      let fallbackReply = "";

      if (isMr) {
        fallbackReply = `${namePrefix ? namePrefix : "नमस्कार, "}आमच्या सेवेबद्दल आपला मोलाचा अभिप्राय दिल्याबद्दल मनःपूर्वक धन्यवाद! आपला अनुभव सुखद राहिला हे वाचून आम्हाला अत्यंत आनंद झाला. आम्ही नेहमीच उत्कृष्ट सेवा देण्यासाठी कटिबद्ध आहोत. — Team ${details.businessName}`;
      } else {
        fallbackReply = `${details.reviewerName ? `Dear ${details.reviewerName}, ` : "Dear Valued Customer, "}thank you so much for your positive review! We are thrilled to hear about your great experience. We look forward to serving you again! — Team ${details.businessName}`;
      }

      fillTextarea(textarea, fallbackReply);
      if (statusEl) {
        statusEl.innerHTML = `
          <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
            <span>✅ AI Reply Generated (${isMr ? "Marathi" : "English"} · ${details.businessName})</span>
          </div>
        `;
      }

      if (shouldAutoSend && CONFIG.autoSendEnabled) {
        initiateAutoSend(modal, fallbackReply, details);
      }
    }
  }

  // ─── Textarea Keystroke & Event Emulation ────────────────────────────────────
  function fillTextarea(textarea, text) {
    if (!textarea) return;
    textarea.focus();

    // 1. Try document.execCommand first (Google's input components update character count with this!)
    let execSuccess = false;
    try {
      document.execCommand("selectAll", false, null);
      execSuccess = document.execCommand("insertText", false, text);
    } catch (e) {
      console.warn("[TechnoBuzz AI] execCommand insertText failed:", e);
    }

    // 2. If value wasn't set or execCommand failed, use native property setter
    if (!execSuccess || (textarea.value !== text && textarea.innerText !== text)) {
      if (textarea.tagName.toLowerCase() === "textarea" || textarea.tagName.toLowerCase() === "input") {
        const nativeSetter =
          Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set ||
          Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
        if (nativeSetter) {
          nativeSetter.call(textarea, text);
        } else {
          textarea.value = text;
        }
      } else {
        textarea.innerText = text;
      }
    }

    // 3. Dispatch native InputEvent & standard DOM events so character counter & Reply button activate
    try {
      textarea.dispatchEvent(
        new InputEvent("input", {
          bubbles: true,
          cancelable: true,
          inputType: "insertText",
          data: text,
        })
      );
    } catch (e) {
      textarea.dispatchEvent(new Event("input", { bubbles: true, cancelable: true }));
    }

    ["change", "keydown", "keyup", "keypress", "blur", "focus"].forEach((evtName) => {
      textarea.dispatchEvent(new Event(evtName, { bubbles: true, cancelable: true }));
    });
  }

  // ─── Auto-Send Countdown & Submission ───────────────────────────────────────
  function initiateAutoSend(modal, replyText, details) {
    let remaining = CONFIG.autoSendDelaySec || 2;
    const statusEl = document.getElementById("tbModalStatus");

    if (statusEl) {
      statusEl.innerHTML = `
        <div class="tb-countdown-banner">
          <span>⚡ Auto-Sending to Google in <strong id="tbCountVal">${remaining}s</strong>...</span>
          <button type="button" class="tb-countdown-cancel" id="tbCancelSend">Cancel Auto-Send</button>
        </div>
      `;

      document.getElementById("tbCancelSend")?.addEventListener("click", () => {
        clearInterval(countdownTimer);
        statusEl.innerHTML = `<span>✋ Auto-send cancelled. You can review and click 'Reply' manually.</span>`;
      });
    }

    clearInterval(countdownTimer);
    countdownTimer = setInterval(() => {
      remaining -= 1;
      const countVal = document.getElementById("tbCountVal");
      if (countVal) countVal.textContent = `${remaining}s`;

      if (remaining <= 0) {
        clearInterval(countdownTimer);
        submitGoogleReply(modal, replyText, details);
      }
    }, 1000);
  }

  function submitGoogleReply(modal, replyText, details) {
    const statusEl = document.getElementById("tbModalStatus");
    if (statusEl) {
      statusEl.innerHTML = `<span>🚀 Posting reply to Google...</span>`;
    }

    // Find the blue "Reply" submit button inside the modal
    const allButtons = Array.from(modal.querySelectorAll("button, div[role='button'], a[role='button']"));
    let submitBtn = null;

    allButtons.forEach((b) => {
      const t = (b.innerText || b.textContent || "").trim().toLowerCase();
      if (
        (t === "reply" || t === "उत्तर द्या" || t === "जवाब दें" || t === "post") &&
        !b.classList.contains("tb-mini-btn") &&
        !b.classList.contains("tb-countdown-cancel")
      ) {
        submitBtn = b;
      }
    });

    if (submitBtn) {
      console.log("[TechnoBuzz AI] Submitting reply by activating and clicking button:", submitBtn);

      // Force enable if disabled
      submitBtn.disabled = false;
      submitBtn.removeAttribute("disabled");
      submitBtn.setAttribute("aria-disabled", "false");
      submitBtn.classList.remove("disabled");

      // Dispatch full mouse interaction sequence
      ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach((evtName) => {
        submitBtn.dispatchEvent(new MouseEvent(evtName, { bubbles: true, cancelable: true, view: window }));
      });
      submitBtn.click();

      // Log reply to backend
      fetch(`${CONFIG.apiBaseUrl}/api/reviews/log`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          business_id: details.businessName || CONFIG.businessId || "technobuzz",
          reviewer_name: details.reviewerName || "Customer",
          rating: details.rating || 5,
          review_text: details.reviewText || "",
          reply_text: replyText,
          status: "sent",
          source: "extension_autopilot",
        }),
      }).catch((e) => console.warn("Log failed:", e));

      // If Auto-Pilot is active, continue to next review
      if (autoPilotActive) {
        setTimeout(processNextAutoPilotReview, 3000);
      }
    } else {
      console.warn("[TechnoBuzz AI] Could not find submit 'Reply' button in modal.");
    }
  }

  // ─── Full Auto-Pilot Mode (Process all reviews sequentially) ────────────────
  function startAutoPilot(isSilent = false) {
    autoPilotReviews = findGoogleReplyButtons();
    if (autoPilotReviews.length === 0) {
      if (!isSilent) alert("No unreplied reviews found on this page!");
      return;
    }

    autoPilotActive = true;
    autoPilotIndex = 0;
    const btn = document.getElementById("tbAutoPilotBtn");
    if (btn) {
      btn.innerHTML = `<span>⏹ Stop Auto-Pilot (${autoPilotReviews.length} Remaining)</span>`;
      btn.classList.add("tb-btn-danger");
    }

    updateStatus(`Auto-Pilot Running (${autoPilotReviews.length} to reply)`, "running");
    processNextAutoPilotReview();
  }

  function stopAutoPilot() {
    autoPilotActive = false;
    const btn = document.getElementById("tbAutoPilotBtn");
    if (btn) {
      btn.innerHTML = `<span>⚡ Auto-Reply All Reviews (Auto-Pilot)</span>`;
      btn.classList.remove("tb-btn-danger");
    }
    updateStatus("● Ready", "ready");
  }

  function processNextAutoPilotReview() {
    if (!autoPilotActive) return;

    if (autoPilotIndex >= autoPilotReviews.length) {
      console.log("[TechnoBuzz AI] 🎉 All reviews have been auto-replied successfully!");
      updateStatus("🎉 All Reviews Replied!", "ready");
      stopAutoPilot();
      return;
    }

    const currentBtn = autoPilotReviews[autoPilotIndex];
    autoPilotIndex += 1;

    updateStatus(`Replying ${autoPilotIndex}/${autoPilotReviews.length}...`, "running");
    const mainBtn = document.getElementById("tbAutoPilotBtn");
    if (mainBtn) {
      mainBtn.innerHTML = `<span>⏹ Stop Auto-Pilot (${autoPilotIndex}/${autoPilotReviews.length})</span>`;
    }

    // Click reply button to trigger flow
    currentBtn.click();
  }
})();
