import json
import re
import google.generativeai as genai
from config import get_config

cfg = get_config()
genai.configure(api_key=cfg.GEMINI_API_KEY)

keys_en = {
    'languageLabel': 'Review language',
    'popularGroup': 'Popular',
    'moreGroup': 'More languages',
    'welcomeLine1': "We'd love to hear about your experience!",
    'welcomeLine2': 'Your feedback helps us grow and serve you better. 💙',
    'ratingLabel': 'How was your experience?',
    'loadingText': '✨ AI is generating suggestions...',
    'suggestionsTitle': 'Pick a feedback suggestion',
    'suggestionsSubtitle': 'Choose the one that best matches your experience.',
    'submitBtn': 'Submit Feedback',
    'submittingBtn': 'Submitting...',
    'successTitle': 'Thank You!',
    'successSubtitle': 'Your feedback was successfully submitted.',
    'copyReview': 'Copy Review',
    'postGoogle': 'Post on Google',
    'googleUrlMissing': '⚠️ Google review link is not set.',
    'copiedClipboard': '📋 Review copied to clipboard!',
    'copyFailed': '⚠️ Could not copy automatically. Please use "Copy Review" and paste on Google.',
    'googleTabOpenTitle': 'Google Review tab opened!',
    'googleTabOpenBody': 'Your review is already copied.',
    'clickTextBox': '👉 Click in the Google text box',
    'pressPaste': '👉 Press',
    'toPaste': 'to paste',
    'clickPost': '👉 Click',
    'postWord': 'Post',
    'popupBlockedTitle': 'Your review is copied to clipboard.',
    'popupBlockedBody': '👉 Click "Post on Google" below',
    'successBadge': '✔ Feedback recorded',
    'toastCopied': 'Review successfully copied.',
    'copySuccessToast': '✅ Review copied! Now paste it on Google.',
    'noSuggestions': 'No suggestions generated. Please try again.',
    'noInternet': 'No internet connection. Check WiFi and try again.',
    'aiBusy': '✨ AI is busy right now. Please try again in a moment.',
    'genericError': 'Failed to load suggestions. Please try again.',
    'selectRating': 'Please select a star rating first.',
    'selectSuggestion': 'Please select a feedback suggestion before submitting.',
    'submitFail': 'Failed to submit feedback. Please try again.',
    'tryAgain': 'Try again',
    'rating5': 'Excellent experience!',
    'rating4': 'Good, with minor notes.',
    'rating3': 'Average experience.',
    'rating2': 'Needs improvement.',
    'rating1': 'Terrible experience.',
    'star5': '5 stars — Excellent',
    'star4': '4 stars — Good',
    'star3': '3 stars — Average',
    'star2': '2 stars — Poor',
    'star1': '1 star — Very Poor',
    'googlePolicy': 'Google requires each review to be posted directly by the customer from their own Google account.',
    'googleCopied': 'For your convenience, we have copied your review.',
    'googleClickPaste': 'Click "Post on Google", paste the review, and hit Post.'
}

langs = {'ta': 'Tamil', 'te': 'Telugu', 'kn': 'Kannada', 'bn': 'Bengali', 'pa': 'Punjabi', 'ml': 'Malayalam'}

results = {}
model = genai.GenerativeModel('gemini-1.5-flash')

for code, lang in langs.items():
    prompt = f"Translate the following JSON values to {lang}. Keep the exact same keys. Return ONLY valid raw JSON with no markdown formatting or backticks.\n\n{json.dumps(keys_en)}"
    response = model.generate_content(prompt)
    try:
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:-3].strip()
        elif text.startswith("```"):
            text = text[3:-3].strip()
        results[code] = json.loads(text)
        print(f"Translated {lang}")
    except Exception as e:
        print(f"Failed {lang}: {e}")

with open('static/js/i18n.js', 'r', encoding='utf-8') as f:
    content = f.read()

# insert before };\n\nconst I18n = {
insert_idx = content.find('};\n\nconst I18n')
if insert_idx != -1:
    new_str = ""
    for code, trans in results.items():
        # fix identation a bit
        lines = json.dumps(trans, ensure_ascii=False, indent=2).split('\n')
        indented = '\n  '.join(lines)
        new_str += f",\n  {code}: {indented}"
    
    new_content = content[:insert_idx] + new_str + "\n" + content[insert_idx:]
    with open('static/js/i18n.js', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Successfully updated i18n.js")
else:
    print("Could not find insertion point")
