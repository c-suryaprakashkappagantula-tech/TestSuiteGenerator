"""
extract_nmno_to_db.py — Extract NMNO+Services Transaction Flow from Chalk → DB
================================================================================
Extracts the TMO Testing Scope page (NMNO tabs) from Chalk and stores
into the TSG database table: TMO_NMNO_Transaction_Flow_Chalk

Run: python TestSuiteGenerator/extract_nmno_to_db.py
"""
import sys, os, time, re, json, sqlite3
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from modules.config import get_browser_channel, ROOT

MAIN_URL = "https://chalk.charter.com/spaces/MDA/pages/3007682647/TMO+Testing+Scope"
STORAGE = Path("storage_state.json")
DB_PATH = ROOT / 'tsg_cache.db'
PI_RANGE = range(46, 60)
PI_PAT = re.compile(r"PI[-\s]?(\d+)", re.IGNORECASE)
BASE_URL = "https://chalk.charter.com"

def log(m):
    print('[%s] %s' % (datetime.now().strftime('%H:%M:%S'), m), flush=True)

def wait_pg(page, t=30000):
    page.wait_for_load_state("domcontentloaded", timeout=t)
    try: page.wait_for_load_state("networkidle", timeout=t)
    except: pass

def expand(page):
    page.evaluate("""()=>{
        document.querySelectorAll('button[aria-expanded="false"],.expand-control,.expand-control-text')
        .forEach(b=>{try{b.click()}catch(e){}});
    }""")
    time.sleep(1)

def get_tabs(page):
    return page.evaluate("""()=>{
        var t=[];
        ['.tabs-menu li a','[role="tab"]','.tab-menu a'].forEach(s=>{
            document.querySelectorAll(s).forEach(e=>{
                var x=e.textContent.trim(); if(x&&!t.includes(x))t.push(x);
            });
        }); return t;
    }""")

def click_tab(page, name):
    for sel in [".tabs-menu li a", ".aui-tabs .menu-item a", "[role='tab']"]:
        try:
            el = page.locator('%s:has-text("%s")' % (sel, name)).first
            if el.count() > 0:
                el.click(timeout=8000); time.sleep(1.5); return True
        except: pass
    try:
        page.locator("text='%s'" % name).first.click(timeout=8000); time.sleep(1.5); return True
    except: return False

def get_html(page):
    return page.evaluate("""()=>{
        var a=document.querySelector('.tabs-pane.active-pane,[role="tabpanel"][aria-hidden="false"]');
        if(a)return a.innerHTML;
        var c=document.querySelector('#main-content,.wiki-content,article');
        return c?c.innerHTML:document.body.innerHTML;
    }""")

def collect_pi(page):
    for _ in range(3):
        n = page.evaluate("""() => {
            var c=0;
            document.querySelectorAll(
              '[data-testid="page-tree"] button[aria-expanded="false"],' +
              '.ia-splitter-left button[aria-expanded="false"],' +
              'nav button[aria-expanded="false"],' +
              '.plugin_pagetree_childtoggle_container a'
            ).forEach(a=>{try{a.click();c++}catch(e){}});
            return c;
        }""")
        if n: time.sleep(2)
        else: break
    time.sleep(1)
    links = page.evaluate("""() => {
        var r=[],s=new Set();
        document.querySelectorAll('a[href*="/spaces/MDA/pages/"]').forEach(a=>{
            var t=a.textContent.trim();
            if(/PI[-\\s]?\\d+/i.test(t)&&!s.has(a.href)){s.add(a.href);r.push({text:t,href:a.href})}
        }); return r;
    }""")
    seen = set(); out = []
    for it in links:
        m = PI_PAT.search(it["text"])
        if m:
            num = int(m.group(1))
            if num in PI_RANGE and num not in seen:
                seen.add(num)
                h = it["href"] if it["href"].startswith("http") else BASE_URL + it["href"]
                out.append((it["text"].strip(), h, num))
    out.sort(key=lambda x: x[2])
    return [(l, h) for l, h, _ in out]

def parse_html_to_blocks(html):
    """Parse HTML into structured blocks (headings, text, tables)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    blocks = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table"]):
        if tag.name.startswith("h"):
            t = tag.get_text(separator=" ", strip=True)
            if t: blocks.append({"type": "heading", "level": int(tag.name[1]), "content": t})
        elif tag.name == "table":
            if tag.find_parent("table"): continue
            rows = []
            for tr in tag.find_all("tr"):
                cells = [td.get_text(separator=" ", strip=True) for td in tr.find_all(["th", "td"])]
                cells = [c for c in cells if c]
                if cells: rows.append(cells)
            if rows: blocks.append({"type": "table", "content": rows})
        else:
            if tag.find_parent("table"): continue
            t = tag.get_text(separator=" ", strip=True)
            if t: blocks.append({"type": "text", "content": ("  * " if tag.name == "li" else "") + t})
    return blocks


# ════════════════════════════════════════════════════════════════════
#  DB SETUP
# ════════════════════════════════════════════════════════════════════

def init_nmno_table():
    """Create the TMO_NMNO_Transaction_Flow_Chalk table."""
    c = sqlite3.connect(str(DB_PATH))
    c.executescript('''
        CREATE TABLE IF NOT EXISTS TMO_NMNO_Transaction_Flow_Chalk (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pi_label        TEXT NOT NULL,
            pi_url          TEXT,
            tab_name        TEXT NOT NULL,
            block_type      TEXT NOT NULL,
            block_level     INTEGER DEFAULT 0,
            content         TEXT NOT NULL,
            raw_json        TEXT,
            last_fetched    TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_nmno_pi ON TMO_NMNO_Transaction_Flow_Chalk(pi_label);
        CREATE INDEX IF NOT EXISTS idx_nmno_tab ON TMO_NMNO_Transaction_Flow_Chalk(tab_name);
    ''')
    c.commit(); c.close()
    log('DB table TMO_NMNO_Transaction_Flow_Chalk ready')

def clear_pi_data(pi_label):
    """Clear existing data for a PI before re-inserting."""
    c = sqlite3.connect(str(DB_PATH))
    c.execute('DELETE FROM TMO_NMNO_Transaction_Flow_Chalk WHERE pi_label=?', (pi_label,))
    c.commit(); c.close()

def save_blocks(pi_label, pi_url, tab_name, blocks):
    """Save parsed blocks to DB."""
    c = sqlite3.connect(str(DB_PATH))
    now = datetime.now().isoformat()
    for block in blocks:
        content = block['content'] if isinstance(block['content'], str) else json.dumps(block['content'])
        c.execute('''INSERT INTO TMO_NMNO_Transaction_Flow_Chalk
            (pi_label, pi_url, tab_name, block_type, block_level, content, raw_json, last_fetched)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (pi_label, pi_url, tab_name, block['type'],
             block.get('level', 0), content,
             json.dumps(block), now))
    c.commit(); c.close()
    return len(blocks)


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    log('=' * 80)
    log('NMNO+Services Transaction Flow Extraction → DB')
    log('=' * 80)

    init_nmno_table()

    log('Launching browser...')
    pw = sync_playwright().start()
    browser = pw.chromium.launch(channel=get_browser_channel(), headless=True)
    ctx = browser.new_context(
        storage_state=str(STORAGE) if STORAGE.exists() else None,
        viewport={"width": 1920, "height": 1080})
    page = ctx.new_page()
    page.set_default_timeout(60000)

    log('Navigating to TMO Testing Scope...')
    page.goto(MAIN_URL, wait_until="domcontentloaded", timeout=60000)
    wait_pg(page, 60000); time.sleep(3)
    log('Page loaded: %s' % page.title())
    ctx.storage_state(path=str(STORAGE))

    pi_links = collect_pi(page)
    if not pi_links:
        log('ERROR: No PI links found')
        ctx.close(); browser.close(); pw.stop()
        return

    log('Found %d PIs' % len(pi_links))
    total_blocks = 0

    for idx, (label, url) in enumerate(pi_links, 1):
        log('[%d/%d] %s' % (idx, len(pi_links), label))
        try:
            clear_pi_data(label)
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            wait_pg(page, 60000); time.sleep(2); expand(page)

            tabs = get_tabs(page)
            log('  Tabs: %s' % (tabs or ['(main)']))

            if not tabs:
                html = get_html(page)
                blocks = parse_html_to_blocks(html)
                saved = save_blocks(label, url, 'Main Content', blocks)
                total_blocks += saved
                log('  Main Content: %d blocks saved' % saved)
            else:
                for tab in tabs:
                    if click_tab(page, tab):
                        expand(page)
                        html = get_html(page)
                        blocks = parse_html_to_blocks(html)
                        saved = save_blocks(label, url, tab, blocks)
                        total_blocks += saved
                        log('  %s: %d blocks saved' % (tab, saved))
                    else:
                        log('  %s: FAILED to click tab' % tab)

        except Exception as e:
            log('  ERROR: %s' % str(e)[:100])

    ctx.close(); browser.close(); pw.stop()

    # Summary
    c = sqlite3.connect(str(DB_PATH))
    row_count = c.execute('SELECT COUNT(*) FROM TMO_NMNO_Transaction_Flow_Chalk').fetchone()[0]
    pi_count = c.execute('SELECT COUNT(DISTINCT pi_label) FROM TMO_NMNO_Transaction_Flow_Chalk').fetchone()[0]
    tab_count = c.execute('SELECT COUNT(DISTINCT tab_name) FROM TMO_NMNO_Transaction_Flow_Chalk').fetchone()[0]
    c.close()

    log('')
    log('DONE: %d PIs | %d tabs | %d total blocks saved to DB' % (pi_count, tab_count, row_count))
    log('Table: TMO_NMNO_Transaction_Flow_Chalk in %s' % DB_PATH)


if __name__ == '__main__':
    main()
