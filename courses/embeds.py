import re
from html.parser import HTMLParser

# Mirrors the URL shapes produced by the frontend's videoEmbed.js, since that
# is what actually gets written into Lesson.html by the rich-text editor.
_ID_PATTERNS = {
    'youtube': re.compile(r'youtube(?:-nocookie)?\.com/embed/([\w-]+)'),
    'loom': re.compile(r'loom\.com/embed/([\w-]+)'),
    'vimeo': re.compile(r'vimeo\.com/video/(\d+)'),
}


def _external_id(provider, src):
    pattern = _ID_PATTERNS.get(provider)
    if not pattern or not src:
        return None
    match = pattern.search(src)
    return match.group(1) if match else None


class _EmbedParser(HTMLParser):
    """Walks `<div data-embed data-provider="…"><iframe src="…"></div>`
    blocks (see EmbedNode.js) and collects (provider, external_id) pairs."""

    def __init__(self):
        super().__init__()
        self.embeds = []
        self._pending_provider = None

    def _handle_tag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div' and 'data-embed' in attrs:
            self._pending_provider = attrs.get('data-provider')
            return
        if tag == 'iframe' and self._pending_provider:
            provider = self._pending_provider
            self._pending_provider = None
            external_id = _external_id(provider, attrs.get('src'))
            if external_id:
                self.embeds.append((provider, external_id))

    def handle_starttag(self, tag, attrs):
        self._handle_tag(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._handle_tag(tag, attrs)


def extract_lesson_videos(html):
    """Every recognized (provider, external_id) video embed in `html`, in
    document order and de-duplicated (the same video embedded twice in one
    lesson only needs to be watched once)."""
    if not html:
        return []
    parser = _EmbedParser()
    parser.feed(html)
    seen = set()
    result = []
    for pair in parser.embeds:
        if pair not in seen:
            seen.add(pair)
            result.append(pair)
    return result
