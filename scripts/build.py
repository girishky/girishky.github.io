from __future__ import annotations

import datetime as dt
import html
import json
import re
import shutil
from pathlib import Path
from string import Template
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
PAGES = CONTENT / "pages"
NOTES = CONTENT / "notes"
BLOG = CONTENT / "blog"
PUBLICATIONS = CONTENT / "publications"
PUBLICATIONS_BIB = PUBLICATIONS / "my_papers.bib"
TEMPLATES = ROOT / "templates"
DOCS = ROOT / "docs"
ASSETS = ROOT / "assets"
VENDOR = ROOT / "vendor"
BLOG_STREAM_LIMIT = 5
EXCLUDED_PUBLICATION_KEYWORDS = {"conference", "unpublished"}

NAV_ITEMS = [
    ("Home", "/"),
    ("Publications", "/publications.html"),
    ("Talks", "/talks.html"),
    ("Notes", "/notes.html"),
    ("Blog", "/blog.html"),

]

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def parse_value(value: str) -> Any:
    value = value.strip()
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip('"').strip("'") for item in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text

    raw_meta = text[4:end]
    body = text[end + 5 :]
    meta: dict[str, Any] = {}
    for line in raw_meta.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        meta[key.strip()] = parse_value(value)
    return meta, body.lstrip("\n")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def render_plain_inline(text: str) -> str:
    return html.escape(text, quote=True)


def footnote_suffix(label: str) -> str:
    return slugify(label)


def extract_footnotes(markdown: str) -> tuple[str, dict[str, str]]:
    lines = markdown.splitlines()
    body: list[str] = []
    footnotes: dict[str, str] = {}
    i = 0

    while i < len(lines):
        match = re.match(r"^\[\^([^\]]+)\]:\s*(.*)$", lines[i])
        if not match:
            body.append(lines[i])
            i += 1
            continue

        label = match.group(1).strip()
        text_parts = [match.group(2).strip()]
        i += 1
        while i < len(lines) and (
            lines[i].startswith("    ") or lines[i].startswith("\t")
        ):
            text_parts.append(lines[i].strip())
            i += 1
        footnotes[label] = " ".join(part for part in text_parts if part)

    return "\n".join(body), footnotes


def render_inline_segment(
    text: str,
    footnotes: dict[str, str] | None = None,
    footnote_order: list[str] | None = None,
) -> str:
    elements: list[str] = []

    def placeholder(html_text: str) -> str:
        elements.append(html_text)
        return f"@@HTML{len(elements) - 1}@@"

    def image_repl(match: re.Match[str]) -> str:
        alt = html.escape(match.group(1), quote=True)
        url = match.group(2)
        # Make image URLs absolute so they resolve correctly from any page
        relative_url = url.lstrip("./")
        absolute_url = f"/{relative_url}"
        title = match.group(3)
        title_attr = f' title="{html.escape(title, quote=True)}"' if title else ""
        return placeholder(f'<img src="{html.escape(absolute_url, quote=True)}" alt="{alt}"{title_attr}>')

    def link_repl(match: re.Match[str]) -> str:
        label = render_plain_inline(match.group(1))
        url = html.escape(match.group(2), quote=True)
        return placeholder(f'<a href="{url}">{label}</a>')

    def line_break_repl(match: re.Match[str]) -> str:
        return placeholder("<br>")

    def footnote_repl(match: re.Match[str]) -> str:
        if footnotes is None or footnote_order is None:
            return placeholder(render_plain_inline(match.group(0)))

        label = match.group(1).strip()
        if label not in footnotes:
            return placeholder(render_plain_inline(match.group(0)))

        if label not in footnote_order:
            footnote_order.append(label)
        number = footnote_order.index(label) + 1
        suffix = footnote_suffix(label)
        return placeholder(
            f'<sup id="fnref-{suffix}"><a class="footnote-ref" href="#fn-{suffix}">{number}</a></sup>'
        )

    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]+)\")?\)", image_repl, text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link_repl, text)
    text = re.sub(r"\s+\\\\\s+", line_break_repl, text)
    text = re.sub(r"\[\^([^\]]+)\]", footnote_repl, text)
    escaped = html.escape(text, quote=True)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    for index, element in enumerate(elements):
        escaped = escaped.replace(f"@@HTML{index}@@", element)
    return escaped


def render_inlines(
    text: str,
    footnotes: dict[str, str] | None = None,
    footnote_order: list[str] | None = None,
) -> str:
    code_parts = re.split(r"(`[^`]+`)", text)
    rendered: list[str] = []
    for code_part in code_parts:
        if not code_part:
            continue
        if code_part.startswith("`") and code_part.endswith("`"):
            rendered.append(f"<code>{html.escape(code_part[1:-1])}</code>")
            continue

        math_parts: list[str] = []

        def math_repl(match: re.Match[str]) -> str:
            math_parts.append(html.escape(match.group(0)))
            return f"%%MATH{len(math_parts) - 1}%%"

        protected = re.sub(r"\$[^$\n]+\$", math_repl, code_part)
        segment = render_inline_segment(protected, footnotes, footnote_order)
        for index, math_part in enumerate(math_parts):
            segment = segment.replace(f"%%MATH{index}%%", math_part)
        rendered.append(segment)
    return "".join(rendered)


def is_unordered_item(line: str) -> bool:
    return bool(re.match(r"^\s*[-*]\s+", line))


def is_ordered_item(line: str) -> bool:
    return bool(re.match(r"^\s*\d+\.\s+", line))


def render_markdown(markdown: str) -> str:
    markdown, footnotes = extract_footnotes(markdown)
    footnote_order: list[str] = []
    lines = markdown.splitlines()
    output: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        if line.startswith("```"):
            language = line.strip().strip("`").strip()
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            class_name = f' class="language-{slugify(language)}"' if language else ""
            output.append(
                f"<pre><code{class_name}>{html.escape(chr(10).join(code))}</code></pre>"
            )
            continue

        if line.strip() == "$$":
            math_lines = ["$$"]
            i += 1
            while i < len(lines):
                math_lines.append(lines[i])
                if lines[i].strip() == "$$":
                    i += 1
                    break
                i += 1
            math = html.escape("\n".join(math_lines))
            output.append(f'<div class="math-display">{math}</div>')
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            text = heading.group(2).strip()
            output.append(
                f'<h{level} id="{slugify(text)}">{render_inlines(text, footnotes, footnote_order)}</h{level}>'
            )
            i += 1
            continue

        if is_unordered_item(line):
            items: list[str] = []
            while i < len(lines) and is_unordered_item(lines[i]):
                item = re.sub(r"^\s*[-*]\s+", "", lines[i]).strip()
                items.append(
                    f"<li>{render_inlines(item, footnotes, footnote_order)}</li>"
                )
                i += 1
            output.append("<ul>\n" + "\n".join(items) + "\n</ul>")
            continue

        if is_ordered_item(line):
            items = []
            while i < len(lines) and is_ordered_item(lines[i]):
                item = re.sub(r"^\s*\d+\.\s+", "", lines[i]).strip()
                items.append(
                    f"<li>{render_inlines(item, footnotes, footnote_order)}</li>"
                )
                i += 1
            output.append("<ol>\n" + "\n".join(items) + "\n</ol>")
            continue

        if line.startswith(">"):
            quote: list[str] = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i].lstrip("> ").strip())
                i += 1
            output.append(
                f"<blockquote><p>{render_inlines(' '.join(quote), footnotes, footnote_order)}</p></blockquote>"
            )
            continue

        paragraph: list[str] = []
        while i < len(lines):
            current = lines[i]
            if (
                not current.strip()
                or current.startswith("```")
                or current.strip() == "$$"
                or re.match(r"^(#{1,6})\s+", current)
                or is_unordered_item(current)
                or is_ordered_item(current)
                or current.startswith(">")
            ):
                break
            paragraph.append(current.strip())
            i += 1
        output.append(
            f"<p>{render_inlines(' '.join(paragraph), footnotes, footnote_order)}</p>"
        )

    if footnote_order:
        output.append(render_footnotes(footnotes, footnote_order))
    return "\n".join(output)


def matching_outer_braces(text: str) -> bool:
    if not (text.startswith("{") and text.endswith("}")):
        return False

    depth = 0
    in_quote = False
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and index != len(text) - 1:
                return False
    return depth == 0


def clean_bib_value(value: str) -> str:
    value = value.strip().rstrip(",").strip()
    while True:
        if (value.startswith('"') and value.endswith('"')) or matching_outer_braces(
            value
        ):
            value = value[1:-1].strip()
            continue
        break
    return value.replace(r"\&", "&")


def split_bib_fields(text: str) -> list[str]:
    fields: list[str] = []
    start = 0
    depth = 0
    in_quote = False
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "," and depth == 0:
            fields.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    if tail:
        fields.append(tail)
    return fields


def find_entry_end(text: str, open_brace: int) -> int:
    depth = 0
    in_quote = False
    escaped = False
    for index in range(open_brace, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return len(text) - 1


def parse_bibtex(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    index = 0
    while True:
        start = text.find("@", index)
        if start == -1:
            break
        open_brace = text.find("{", start)
        if open_brace == -1:
            break
        entry_type = text[start + 1 : open_brace].strip().lower()
        close_brace = find_entry_end(text, open_brace)
        raw_entry = text[open_brace + 1 : close_brace]
        parts = split_bib_fields(raw_entry)
        if not parts:
            index = close_brace + 1
            continue

        entry: dict[str, Any] = {"type": entry_type, "key": parts[0].strip()}
        for part in parts[1:]:
            name, sep, value = part.partition("=")
            if not sep:
                continue
            entry[name.strip().lower()] = clean_bib_value(value)
        entries.append(entry)
        index = close_brace + 1
    return entries


def publication_keywords(entry: dict[str, Any]) -> set[str]:
    raw_keywords = str(entry.get("keywords", ""))
    return {
        keyword.strip().lower()
        for keyword in raw_keywords.split(",")
        if keyword.strip()
    }


def is_published_article(entry: dict[str, Any]) -> bool:
    if entry.get("type") != "article":
        return False
    if publication_keywords(entry) & EXCLUDED_PUBLICATION_KEYWORDS:
        return False
    if entry.get("booktitle"):
        return False
    return bool(entry.get("journal") and entry.get("year"))


def format_author(author: str) -> str:
    if "," not in author:
        return author.strip()
    last, first = [part.strip() for part in author.split(",", 1)]
    return f"{first} {last}".strip()


def render_authors(authors: str) -> str:
    rendered = []
    for author in authors.split(" and "):
        name = format_author(author)
        escaped = html.escape(name)
        if name == "Girish Kumar":
            rendered.append(f'<span class="publication-author-self">{escaped}</span>')
        else:
            rendered.append(escaped)
    return ", ".join(rendered)


def render_publication_meta(entry: dict[str, Any]) -> str:
    journal = html.escape(str(entry.get("journal", "")))
    volume = html.escape(str(entry.get("volume", "")))
    pages = html.escape(str(entry.get("pages", "")))
    year = html.escape(str(entry.get("year", "")))

    citation = journal
    if volume:
        citation += f" {volume}"
    if pages:
        citation += f", {pages}"
    if year:
        citation += f" ({year})"
    return citation


def render_publication_links(entry: dict[str, Any]) -> str:
    links = []
    doi = str(entry.get("doi", "")).strip()
    eprint = str(entry.get("eprint", "")).strip()
    if doi:
        links.append(
            f'<a href="https://doi.org/{html.escape(doi, quote=True)}">DOI</a>'
        )
    if eprint:
        links.append(
            f'<a href="https://arxiv.org/abs/{html.escape(eprint, quote=True)}">arXiv:{html.escape(eprint)}</a>'
        )
    return " ".join(links)


def render_publication_item(entry: dict[str, Any]) -> str:
    title = html.escape(str(entry.get("title", "")))
    authors = render_authors(str(entry.get("author", "")))
    meta = render_publication_meta(entry)
    links = render_publication_links(entry)
    links_html = f'<p class="publication-links">{links}</p>' if links else ""
    return f"""<li class="publication">
  <p class="publication-title">{title}</p>
  <p class="publication-authors">{authors}</p>
  <p class="publication-meta">{meta}</p>
  {links_html}
</li>"""


def render_publications() -> str:
    if not PUBLICATIONS_BIB.exists():
        return ""

    entries = [
        entry
        for entry in parse_bibtex(read_text(PUBLICATIONS_BIB))
        if is_published_article(entry)
    ]
    entries.sort(key=lambda entry: int(str(entry.get("year", "0"))), reverse=True)

    if not entries:
        return '<section class="publications" aria-label="Research papers"><p>No journal articles yet.</p></section>'

    groups = []
    current_year = ""
    current_items: list[str] = []

    for entry in entries:
        year = str(entry.get("year", ""))
        if current_year and year != current_year:
            groups.append((current_year, current_items))
            current_items = []
        current_year = year
        current_items.append(render_publication_item(entry))

    if current_year:
        groups.append((current_year, current_items))

    year_sections = []
    for year, items in groups:
        year_id = f"papers-{html.escape(year, quote=True)}"
        items_html = "\n".join(items)
        year_sections.append(
            f"""<section class="publication-year-group" aria-labelledby="{year_id}">
  <h2 class="publication-year" id="{year_id}">{html.escape(year)}</h2>
  <ol class="publication-list">
    {items_html}
  </ol>
</section>"""
        )

    return """<section class="publications" aria-label="Research papers">
  {groups}
</section>""".format(groups="\n".join(year_sections))


def render_footnotes(footnotes: dict[str, str], footnote_order: list[str]) -> str:
    items = []
    for label in footnote_order:
        suffix = footnote_suffix(label)
        text = render_inlines(footnotes[label])
        items.append(
            f'<li id="fn-{suffix}">{text} <a class="footnote-backref" href="#fnref-{suffix}" aria-label="Back to reference">&#8617;</a></li>'
        )
    return (
        '<section class="footnotes" aria-label="Footnotes">\n<ol>\n'
        + "\n".join(items)
        + "\n</ol>\n</section>"
    )


def template(name: str) -> Template:
    return Template(read_text(TEMPLATES / name))


def page_title(site_title: str, title: str) -> str:
    if title == site_title:
        return site_title
    return f"{title} - {site_title}"


def render_nav(current_url: str) -> str:
    links: list[str] = []
    for label, href in NAV_ITEMS:
        is_current = (
            href == current_url
            or (href == "/notes.html" and current_url.startswith("/notes/"))
            or (href == "/blog.html" and current_url.startswith("/blog/"))
        )
        current = ' aria-current="page"' if is_current else ""
        links.append(f'<a href="{href}"{current}>{html.escape(label)}</a>')
    return chr(10).join(links)


def render_footer(site: dict[str, Any]) -> str:
    github_url = html.escape(
        str(site.get("github_url", "https://github.com/girishky")), quote=True
    )
    orcid_url = html.escape(str(site.get("orcid_url", "")), quote=True)
    scholar_url = html.escape(str(site.get("scholar_url", "")), quote=True)
    profile_links = [
        '<a href="/contact.html">Contact</a>',
        f'<a href="{github_url}">GitHub</a>',
    ]
    if orcid_url:
        profile_links.append(f'<a href="{orcid_url}">ORCID</a>')
    if scholar_url:
        profile_links.append(f'<a href="{scholar_url}">Google Scholar</a>')

    separator = '<span aria-hidden="true">•</span>'
    links = f"\n  {separator}\n  ".join(profile_links)
    return f"""<footer class="site-footer" aria-label="Footer">
  <p class="footer-links">
  {links}
  </p>
</footer>"""


def katex_head(enabled: bool) -> str:
    if not enabled:
        return ""
    return '<link rel="stylesheet" href="/assets/katex/katex.min.css">'


def katex_scripts(enabled: bool) -> str:
    if not enabled:
        return ""
    return """<script defer src="/assets/katex/katex.min.js"></script>
    <script defer src="/assets/katex/auto-render.min.js" onload="renderMathInElement(document.getElementById('main'), {delimiters: [{left: '$$', right: '$$', display: true}, {left: '$', right: '$', display: false}], throwOnError: false});"></script>"""


def format_date(date: dt.date) -> str:
    return f"{date.strftime('%B')} {date.day}, {date.year}"


def render_base(
    site: dict[str, Any], title: str, description: str, body: str, url: str, math: bool
) -> str:
    return template("base.html").substitute(
        body_class="site-home" if url == "/" else "site-page",
        title=html.escape(page_title(site["title"], title)),
        description=html.escape(description or site["description"], quote=True),
        site_title=html.escape(site["title"]),
        nav=render_nav(url),
        body=body,
        footer=render_footer(site),
        katex_head=katex_head(math),
        katex_scripts=katex_scripts(math),
    )


def render_page_header(heading: str, intro: str = "") -> str:
    intro_html = f'\n   <p class="page-dek">{render_inlines(intro)}</p>' if intro else ""
    return f"""<header class="page-header">{intro_html}
</header>
"""

def render_page(site: dict[str, Any], source: Path) -> None:
    meta, body = parse_front_matter(read_text(source))
    title = str(meta.get("title", source.stem.replace("-", " ").title()))
    output = str(meta.get("output", f"{source.stem}.html"))
    url = "/" if output == "index.html" else f"/{output}"
    description = str(meta.get("description", site["description"]))
    heading = str(
        meta.get("heading", site["title"] if output == "index.html" else title)
    )
    intro = str(meta.get("intro", ""))
    content = render_markdown(body)
    math = bool(meta.get("math"))
    if output == "publications.html":
        content = f"{content}\n{render_publications()}"
        math = True

    body_html = template("page.html").substitute(
        page_class=f"prose page page-{slugify(Path(output).stem)}",
        header=render_page_header(heading, intro),
        content=content,
    )
    write_text(
        DOCS / output, render_base(site, title, description, body_html, url, math)
    )


def entry_slug(path: Path, meta: dict[str, Any]) -> str:
    if meta.get("slug"):
        return str(meta["slug"])
    return path.stem


def entry_url(path: Path, meta: dict[str, Any], section: str) -> str:
    return f"/{section}/{entry_slug(path, meta)}/"


def load_entries(source_dir: Path, section: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for source in sorted(source_dir.glob("*.md")):
        meta, body = parse_front_matter(read_text(source))
        title = str(meta.get("title", source.stem.replace("-", " ").title()))
        description = str(meta.get("description", ""))
        date_text = str(meta.get("date", ""))
        try:
            date = dt.date.fromisoformat(date_text)
        except ValueError:
            date = dt.date.fromtimestamp(source.stat().st_mtime)
            date_text = date.isoformat()
        entries.append(
            {
                "source": source,
                "meta": meta,
                "body": body,
                "title": title,
                "description": description,
                "date": date,
                "date_text": date_text,
                "url": entry_url(source, meta, section),
                "math": bool(meta.get("math")),
            }
        )
    return sorted(entries, key=lambda entry: entry["date"], reverse=True)


def render_entry(site: dict[str, Any], entry: dict[str, Any]) -> None:
    description = entry["description"]
    body_html = template("post.html").substitute(
        date=format_date(entry["date"]),
        heading=html.escape(entry["title"]),
        content=render_markdown(entry["body"]),
    )
    output = DOCS / entry["url"].strip("/") / "index.html"
    render = render_base(
        site,
        entry["title"],
        description,
        body_html,
        entry["url"],
        entry["math"],
    )
    write_text(output, render)


def render_collection_index(
    site: dict[str, Any],
    title: str,
    output: str,
    entries: list[dict[str, Any]],
    intro: str = "",
) -> None:
    if entries:
        cards = []
        for entry in entries:
            date = format_date(entry["date"])
            cards.append(
                f"""<article class="post-row">
  <h2><a href="{entry["url"]}">{html.escape(entry["title"])}</a></h2>
  <time datetime="{entry["date"].isoformat()}">{date}</time>
</article>"""
            )
        posts_html = "\n".join(cards)
    else:
        posts_html = f"<p>No {title.lower()} yet.</p>"

    body_html = template("collection.html").substitute(
        label=html.escape(title),
        entries=posts_html,
    )
    url = f"/{output}"
    render = render_base(
        site,
        title,
        site["description"],
        render_page_header(title, intro) + body_html,
        url,
        False,
    )
    write_text(DOCS / output, render)


def render_blog_index(site: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    if not entries:
        body = (
            render_page_header("Blog")
            + '<section class="prose blog-stream"><p>No blog posts yet.</p></section>'
        )
        render = render_base(
            site, "Blog", site["description"], body, "/blog.html", False
        )
        write_text(DOCS / "blog.html", render)
        return

    recent = entries[:BLOG_STREAM_LIMIT]
    older = entries[BLOG_STREAM_LIMIT:]
    posts = []
    for entry in recent:
        posts.append(
            f"""<article class="blog-entry">
  <header class="blog-entry-header">
    <h2><a href="{entry["url"]}">{html.escape(entry["title"])}</a></h2>
    <time datetime="{entry["date"].isoformat()}">{format_date(entry["date"])}</time>
  </header>
  <div class="blog-entry-body">
    {render_markdown(entry["body"])}
  </div>
</article>"""
        )

    archive = ""
    if older:
        archive_items = []
        for entry in older:
            archive_items.append(
                f"""<li>
  <a href="{entry["url"]}">{html.escape(entry["title"])}</a>
  <time datetime="{entry["date"].isoformat()}">{format_date(entry["date"])}</time>
</li>"""
            )
        archive = """<section class="blog-archive" aria-label="Older blog posts">
  <h2>Older Posts</h2>
  <ul>
    {items}
  </ul>
</section>""".format(items="\n".join(archive_items))

    body = template("blog.html").substitute(
        posts="\n".join(posts),
        archive=archive,
    )
    render = render_base(
        site,
        "Blog",
        site["description"],
        render_page_header("Blog") + body,
        "/blog.html",
        any(entry["math"] for entry in recent),
    )
    write_text(DOCS / "blog.html", render)


def render_404(site: dict[str, Any]) -> None:
    body = """<article class="prose page">
  <header class="page-header">
    <h1>Page not found</h1>
  </header>
  <p>The page you requested does not exist.</p>
  <p><a href="/">Return home</a></p>
</article>"""
    write_text(
        DOCS / "404.html",
        render_base(site, "Page not found", site["description"], body, "", False),
    )


def copy_assets() -> None:
    assets_out = DOCS / "assets"
    assets_out.mkdir(parents=True, exist_ok=True)

    for asset in ASSETS.iterdir():
        if asset.name.startswith("."):
            continue
        target = assets_out / asset.name
        if asset.is_dir():
            shutil.copytree(asset, target)
        else:
            shutil.copy2(asset, target)

    katex_source = VENDOR / "katex"
    if katex_source.exists():
        katex_out = assets_out / "katex"
        if katex_out.exists():
            shutil.rmtree(katex_out)
        shutil.copytree(katex_source, katex_out)

    fonts_source = VENDOR / "fonts"
    fonts_out = assets_out / "fonts"
    if fonts_source.exists():
        for font_dir in fonts_source.iterdir():
            if font_dir.name == "katex" or font_dir.name.startswith("."):
                continue
            if font_dir.is_dir():
                target = fonts_out / font_dir.name
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(font_dir, target)
        license_file = fonts_source / "OFL.txt"
        if license_file.exists():
            fonts_out.mkdir(parents=True, exist_ok=True)
            shutil.copy2(license_file, fonts_out / "OFL.txt")


def build() -> None:
    site = json.loads(read_text(CONTENT / "site.json"))
    if DOCS.exists():
        shutil.rmtree(DOCS)
    DOCS.mkdir()
    write_text(DOCS / ".nojekyll", "")

    for page in sorted(PAGES.glob("*.md")):
        render_page(site, page)

    notes = load_entries(NOTES, "notes")
    blog = load_entries(BLOG, "blog")
    for entry in notes + blog:
        render_entry(site, entry)
    render_collection_index(site, "Notes", "notes.html", notes)
    render_blog_index(site, blog)
    render_404(site)
    copy_assets()


if __name__ == "__main__":
    build()
