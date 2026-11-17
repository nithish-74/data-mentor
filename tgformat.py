"""Convert Gemini's light Markdown to Telegram HTML and split long messages."""
import html
import re

FENCE = re.compile(r"```[\w+-]*\n?(.*?)```", re.S)


def to_html(md: str) -> str:
    parts = FENCE.split(md)
    out = []
    for i, part in enumerate(parts):
        if i % 2:  # code block
            out.append("<pre>" + html.escape(part.rstrip()) + "</pre>")
            continue
        t = html.escape(part, quote=False)
        t = re.sub(r"^#{1,6}\s*(.+)$", r"**\1**", t, flags=re.M)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"^(\s*)[*-]\s+", r"\1• ", t, flags=re.M)
        t = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)\*(?![\w*])", r"<i>\1</i>", t)
        t = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", t)
        out.append(t)
    return "".join(out)


def split_message(md: str, limit: int = 3500) -> list:
    """Split on line boundaries, avoiding cuts inside ``` fences when possible."""
    chunks, cur, in_fence = [], [], False
    size = 0
    for line in md.splitlines(keepends=True):
        if size + len(line) > limit and cur and (not in_fence or size > limit + 400):
            if in_fence:
                cur.append("```\n")
            chunks.append("".join(cur))
            cur, size = (["```\n"] if in_fence else []), 0
        cur.append(line)
        size += len(line)
        if line.strip().startswith("```"):
            in_fence = not in_fence
    if cur:
        chunks.append("".join(cur))
    return [c for c in chunks if c.strip()]
