import re

emoji_map = {
    '⏰': '[SYSTEM]',
    '🚨': '[ALERT]',
    '🎯': '[MATCH]',
    '🔔': '[UPDATE]',
    '🌐': '[LINK]',
    '⭐': '[SAVE]',
    '💬': '[CHAT]',
    '👋': '[HELLO]',
    '👉': '[INFO]',
    '⚙️': '[SYS]',
    '✅': '[SUCCESS]',
    '✍️': '[DRAFT]',
    '🚀': '[DEPLOY]',
    '📄': '[DOC]'
}

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

for emoji, replacement in emoji_map.items():
    content = content.replace(emoji, replacement)

# Catch any remaining ones by replacing all non-ASCII characters that aren't typical formatting
# Actually, let's just use the direct map first, as it's safer than blindly replacing all non-ASCII.

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Emojis replaced.")
