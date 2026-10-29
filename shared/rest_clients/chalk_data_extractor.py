# -*- coding: utf-8 -*-
"""
Chalk Data Extractor — Server-side HTML parsing for Confluence pages.

Converts Confluence storage format HTML into the same text + tables
format that chalk_parser.py expects. Eliminates the need for browser-based
JavaScript extraction (EXTRACT_TEXT_JS / EXTRACT_TABLES_JS).

This module replaces:
  - page.evaluate(EXTRACT_TEXT_JS) → extract_text_from_html(html)
  - page.evaluate(EXTRACT_TABLES_JS) → extract_tables_from_html(html)

Usage:
    from shared.rest_clients.chalk_data_extractor import (
        extract_text_from_html,
        extract_tables_from_html,
    )

    html = chalk_client.get_page_html(page_id)
    text = extract_text_from_html(html)
    tables = extract_tables_from_html(html)
    # Now pass to existing chalk_parser logic
"""

import re
from typing import List
from bs4 import BeautifulSoup


def extract_text_from_html(html: str) -> str:
    """Extract plain text from Confluence storage format HTML.

    Handles:
      - Structured macros (ac:structured-macro) — expands their body content
      - Tab panels (ac:structured-macro name="tabs") — includes all tab content
      - Rich text bodies (ac:rich-text-body) — extracts inner text
      - Standard HTML elements (p, h1-h6, li, td, th, span, div)
      - Preserves line breaks between block elements

    Equivalent to the browser's EXTRACT_TEXT_JS but runs server-side.

    Args:
        html: Confluence storage format HTML string

    Returns:
        Plain text with newlines between blocks (same format as page.innerText)
    """
    if not html:
        return ''

    soup = BeautifulSoup(html, 'html.parser')

    # Confluence uses custom XML namespaces (ac:, ri:) — BeautifulSoup handles them
    # but we need to ensure macro bodies are included

    # Remove elements that shouldn't contribute text
    for tag in soup.find_all(['ac:parameter', 'ri:attachment', 'ri:page']):
        tag.decompose()

    # Get text with newlines between block elements
    # BeautifulSoup's get_text with separator handles this well
    text = soup.get_text(separator='\n')

    # Clean up: collapse multiple blank lines, strip trailing spaces
    lines = []
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped:
            lines.append(stripped)

    return '\n'.join(lines)


def extract_tables_from_html(html: str) -> List[List[List[str]]]:
    """Extract all tables from Confluence storage format HTML.

    Returns tables in the same format as the browser's EXTRACT_TABLES_JS:
    a list of tables, where each table is a list of rows, where each row
    is a list of cell text strings.

    Handles:
      - Standard HTML tables (<table>, <tr>, <th>, <td>)
      - Nested tables (only extracts top-level tables)
      - Confluence macro tables (inside ac:structured-macro)

    Args:
        html: Confluence storage format HTML string

    Returns:
        List[List[List[str]]] — tables[table_idx][row_idx][cell_idx]
    """
    if not html:
        return []

    soup = BeautifulSoup(html, 'html.parser')
    tables = []

    for table in soup.find_all('table'):
        # Skip nested tables (only process top-level)
        if table.find_parent('table'):
            continue

        rows = []
        for tr in table.find_all('tr'):
            cells = []
            for td in tr.find_all(['th', 'td']):
                # Get cell text, preserving internal structure
                cell_text = td.get_text(separator=' ', strip=True)
                cells.append(cell_text)
            if cells:
                rows.append(cells)

        if rows:
            tables.append(rows)

    return tables


def extract_features_from_html(html: str) -> List[tuple]:
    """Extract MWTGPROV-XXXX feature IDs and titles from page HTML.

    Scans all text content (including table cells) for feature ID patterns.
    Same logic as the browser's discover_features_on_pi JavaScript.

    Args:
        html: Confluence storage format HTML string

    Returns:
        List of (feature_id, title) tuples, sorted by ID number.
    """
    if not html:
        return []

    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(separator='\n')

    features = []
    seen = set()
    feature_pat = re.compile(r'(MWTGPROV-\d{3,5})', re.IGNORECASE)

    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue

        matches = feature_pat.findall(line)
        for fid in matches:
            fid_upper = fid.upper()
            if fid_upper in seen:
                continue
            seen.add(fid_upper)

            # Extract title (text after the feature ID on the same line)
            idx = line.upper().find(fid_upper)
            title = line[idx + len(fid):].strip()
            # Clean up common prefixes
            title = re.sub(r'^[\s\-:_]+', '', title).strip()

            # Truncate long titles
            if len(title) > 120:
                title = title[:120] + '...'
            if not title or title.isdigit():
                title = '(no title)'

            features.append((fid_upper, title))

    # Sort by feature number
    features.sort(key=lambda x: int(x[0].split('-')[1]))
    return features


def extract_pi_links_from_html(html: str, pi_range: range = range(46, 60)) -> List[dict]:
    """Extract PI page links from the TMO Testing Scope page HTML.

    Scans for links matching the PI-XX pattern within the page content.

    Args:
        html: HTML of the TMO Testing Scope page
        pi_range: Range of PI numbers to include

    Returns:
        List of dicts with 'label', 'url', 'number' keys.
    """
    if not html:
        return []

    soup = BeautifulSoup(html, 'html.parser')
    pi_pat = re.compile(r'PI[-\s]?(\d+)', re.IGNORECASE)

    results = []
    seen = set()

    # Look for links to PI pages
    for link in soup.find_all('a', href=True):
        text = link.get_text(strip=True)
        m = pi_pat.search(text)
        if m:
            num = int(m.group(1))
            if num in pi_range and num not in seen:
                seen.add(num)
                href = link['href']
                if not href.startswith('http'):
                    href = f'https://chalk.charter.com{href}'
                results.append({
                    'label': text.strip(),
                    'url': href,
                    'number': num,
                })

    # Also check ri:page references (Confluence internal links)
    for ri_page in soup.find_all('ri:page'):
        title = ri_page.get('ri:content-title', '') or ri_page.get('content-title', '')
        m = pi_pat.search(title)
        if m:
            num = int(m.group(1))
            if num in pi_range and num not in seen:
                seen.add(num)
                results.append({
                    'label': title.strip(),
                    'url': '',  # Will need page ID lookup
                    'number': num,
                })

    results.sort(key=lambda x: x['number'])
    return results
