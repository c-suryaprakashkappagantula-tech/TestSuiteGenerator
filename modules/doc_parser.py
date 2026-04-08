"""
doc_parser.py — Parse .docx, .xlsx, .pdf files into structured text.
Used for Jira attachments and user-uploaded HLD/LLD docs.
"""
from pathlib import Path
from typing import List, Dict
from dataclasses import dataclass, field


@dataclass
class ParsedDoc:
    filename: str = ''
    file_type: str = ''
    paragraphs: List[str] = field(default_factory=list)
    tables: List[List[List[str]]] = field(default_factory=list)
    open_items: List[str] = field(default_factory=list)
    raw_text: str = ''


def parse_file(filepath: Path, log=print) -> ParsedDoc:
    """Auto-detect file type and parse."""
    fp = Path(filepath)
    doc = ParsedDoc(filename=fp.name, file_type=fp.suffix.lower())

    if doc.file_type == '.docx':
        return _parse_docx(fp, doc, log)
    elif doc.file_type in ('.xlsx', '.xls'):
        return _parse_xlsx(fp, doc, log)
    elif doc.file_type == '.pdf':
        return _parse_pdf(fp, doc, log)
    elif doc.file_type in ('.txt', '.csv'):
        return _parse_text(fp, doc, log)
    else:
        log(f'[DOC] WARN Unsupported file type: {doc.file_type}')
        return doc


def _parse_docx(fp, doc: ParsedDoc, log=print) -> ParsedDoc:
    """Parse a .docx file into paragraphs and tables."""
    try:
        from docx import Document
        d = Document(str(fp))

        for p in d.paragraphs:
            t = p.text.strip()
            if t:
                doc.paragraphs.append(t)
                # Detect open items — capture content lines, not headers
                t_low = t.lower()
                if t_low.startswith('cannot ') or t_low.startswith('need to '):
                    doc.open_items.append(t)

        for table in d.tables:
            rows = []
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    rows.append(cells)
            if rows:
                doc.tables.append(rows)

        doc.raw_text = '\n'.join(doc.paragraphs)
        log(f'[DOC] OK Parsed {fp.name}: {len(doc.paragraphs)} paragraphs, {len(doc.tables)} tables, {len(doc.open_items)} open items')
    except Exception as e:
        log(f'[DOC] FAIL to parse {fp.name}: {e}')
    return doc


def _parse_xlsx(fp, doc: ParsedDoc, log=print) -> ParsedDoc:
    """Parse an .xlsx file — each sheet becomes a table."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(fp), data_only=True)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) if c is not None else '' for c in row]
                if any(c for c in cells):
                    rows.append(cells)
            if rows:
                doc.tables.append(rows)
                # Also add as text
                for r in rows:
                    doc.paragraphs.append(' | '.join(r))

        doc.raw_text = '\n'.join(doc.paragraphs)
        log(f'[DOC] OK Parsed {fp.name}: {len(doc.tables)} sheets as tables')
    except Exception as e:
        log(f'[DOC] FAIL to parse {fp.name}: {e}')
    return doc


def _parse_pdf(fp, doc: ParsedDoc, log=print) -> ParsedDoc:
    """Parse a .pdf file into text."""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(str(fp))
        for page in reader.pages:
            text = page.extract_text()
            if text:
                for ln in text.split('\n'):
                    ln = ln.strip()
                    if ln:
                        doc.paragraphs.append(ln)

        doc.raw_text = '\n'.join(doc.paragraphs)
        log(f'[DOC] OK Parsed {fp.name}: {len(doc.paragraphs)} lines from {len(reader.pages)} pages')
    except Exception as e:
        log(f'[DOC] FAIL to parse {fp.name}: {e}')
    return doc


def _parse_text(fp, doc: ParsedDoc, log=print) -> ParsedDoc:
    """Parse plain text / CSV."""
    try:
        text = fp.read_text(encoding='utf-8', errors='ignore')
        doc.paragraphs = [ln.strip() for ln in text.split('\n') if ln.strip()]
        doc.raw_text = text
        log(f'[DOC] OK Parsed {fp.name}: {len(doc.paragraphs)} lines')
    except Exception as e:
        log(f'[DOC] FAIL to parse {fp.name}: {e}')
    return doc
