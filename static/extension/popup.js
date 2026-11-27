document.addEventListener("DOMContentLoaded", () => {
  const defaults = {
    apiBaseUrl: "http://localhost:5001",
    businessId: "technobuzz",
    autonomousAutoPilot: true,
    autoSendEnabled: true,
    autoSendDelaySec: 2,
    tone: "professional_warm",
    languageMode: "auto",
  };

  const fields = {
    apiBaseUrl: document.getElementById("apiBaseUrl"),
    businessId: document.getElementById("businessId"),
    autonomousAutoPilot: document.getElementById("autonomousAutoPilot"),
    autoSendEnabled: document.getElementById("autoSendEnabled"),
    autoSendDelaySec: document.getElementById("autoSendDelaySec"),
    tone: document.getElementById("tone"),
    languageMode: document.getElementById("languageMode"),
  };

  if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.sync) {
    chrome.storage.sync.get(defaults, (items) => {
      fields.apiBaseUrl.value = items.apiBaseUrl || defaults.apiBaseUrl;
      fields.businessId.value = items.businessId || defaults.businessId;
      if (fields.autonomousAutoPilot) fields.autonomousAutoPilot.checked = items.autonomousAutoPilot !== false;
      fields.autoSendEnabled.checked = items.autoSendEnabled !== false;
      fields.autoSendDelaySec.value = items.autoSendDelaySec ?? defaults.autoSendDelaySec;
      fields.tone.value = items.tone || defaults.tone;
      fields.languageMode.value = items.languageMode || defaults.languageMode;
    });
  }

  document.getElementById("btnSave").addEventListener("click", () => {
    const data = {
      apiBaseUrl: fields.apiBaseUrl.value.trim() || defaults.apiBaseUrl,
      businessId: fields.businessId.value.trim() || defaults.businessId,
      autonomousAutoPilot: fields.autonomousAutoPilot ? fields.autonomousAutoPilot.checked : true,
      autoSendEnabled: fields.autoSendEnabled.checked,
      autoSendDelaySec: parseInt(fields.autoSendDelaySec.value, 10) || 2,
      tone: fields.tone.value,
      languageMode: fields.languageMode.value,
    };

    if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.sync) {
      chrome.storage.sync.set(data, () => {
        const msg = document.getElementById("saveMsg");
        msg.style.display = "block";
        setTimeout(() => { msg.style.display = "none"; }, 2500);
      });
    }
  });
});
