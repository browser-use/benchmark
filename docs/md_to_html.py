"""Convert a Markdown file to a styled, self-contained HTML page.

Usage:
    python docs/md_to_html.py docs/scoring.md  [-o docs/scoring.html]

Uses the `markdown` package (pure Python) so no external binaries are needed.
"""

from __future__ import annotations

import argparse
import html
from pathlib import Path

import markdown


CSS = """
:root { color-scheme: light dark; }
body {
  max-width: 880px; margin: 0 auto; padding: 40px 24px 80px;
  font: 16px/1.6 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: #1f2328; background: #ffffff;
}
h1, h2, h3, h4 { line-height: 1.25; }
h1 { font-size: 2em; border-bottom: 1px solid #d0d7de; padding-bottom: 0.3em; }
h2 { font-size: 1.5em; border-bottom: 1px solid #eaeef2; padding-bottom: 0.3em; margin-top: 2em; }
h3 { font-size: 1.2em; margin-top: 1.6em; }
a { color: #0969da; text-decoration: none; }
a:hover { text-decoration: underline; }
code { background: #f6f8fa; border-radius: 6px; padding: 0.1em 0.35em; font: 0.92em ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
pre { background: #f6f8fa; border-radius: 8px; padding: 14px 16px; overflow-x: auto; font: 0.9em ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
pre code { background: transparent; padding: 0; }
blockquote { border-left: 4px solid #d0d7de; color: #57606a; padding: 0 1em; margin: 1em 0; }
table { border-collapse: collapse; margin: 1em 0; display: block; overflow-x: auto; }
th, td { border: 1px solid #d0d7de; padding: 6px 12px; }
th { background: #f6f8fa; text-align: left; }
hr { border: 0; border-top: 1px solid #d0d7de; margin: 2em 0; }
.toc { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 8px; padding: 12px 18px; margin-bottom: 24px; }
.toc > ul { margin: 0; padding-left: 18px; }
@media (prefers-color-scheme: dark) {
  body { color: #e6edf3; background: #0d1117; }
  h1, h2 { border-color: #30363d; }
  a { color: #2f81f7; }
  code, pre { background: #161b22; }
  th { background: #161b22; }
  th, td { border-color: #30363d; }
  blockquote { border-color: #30363d; color: #8b949e; }
  hr { border-color: #30363d; }
  .toc { background: #161b22; border-color: #30363d; }
}
""".strip()


def convert(md_path: Path, out_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    md = markdown.Markdown(
        extensions=["extra", "tables", "fenced_code", "sane_lists", "toc"],
        extension_configs={"toc": {"title": "Contents", "anchorlink": True}},
    )
    body_html = md.convert(text)
    title = md_path.stem.replace("_", " ").replace("-", " ").title()
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
{body_html}
</body>
</html>
"""
    out_path.write_text(page, encoding="utf-8")
    print(f"Wrote {out_path} ({out_path.stat().st_size:,} bytes)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path)
    p.add_argument("-o", "--output", type=Path, default=None)
    args = p.parse_args()
    out = args.output or args.input.with_suffix(".html")
    convert(args.input, out)


if __name__ == "__main__":
    main()
