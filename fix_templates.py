import re

file_path = r'C:\Users\PRITI\GoogleReviews\routes\admin_routes.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Regex to match: templates.TemplateResponse( \n? \s* "filename.html",
# We'll replace it with: templates.TemplateResponse(request=request, name="filename.html", context=
# Since the dict or function call for context follows the comma.

def replacer(match):
    return 'templates.TemplateResponse(request=request, name=' + match.group(1) + ', context='

new_content = re.sub(r'templates\.TemplateResponse\(\s*(["\'][^"\']+["\'])\s*,', replacer, content)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print('Updated admin_routes.py')
