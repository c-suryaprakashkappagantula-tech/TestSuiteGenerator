"""
preload_db.py — Pre-load the SQLite DB with all PI features + full Chalk data.
Run this ONCE before launching the dashboard. After this, the dashboard loads instantly.

Usage:  python preload_db.py
        python preload_db.py --pi PI-52        (single PI only)
        python preload_db.py --force            (re-fetch even if DB has data)
"""
import sys, os, time, argparse
sys.path.insert(0, os.path.dirname(__file__))

from playwright.sync_api import sync_playwright
from modules.config import get_browser_channel, PAGE_LOAD_TIMEOUT_MS
from modules.chalk_parser import discover_pi_links, discover_features_on_pi, fetch_feature_from_pi
from modules.database import (init_db, save_pi_pages, save_features, load_all_features,
                               save_chalk, get_features_count, get_chalk_cache_count, get_db_stats,
                               DB_PATH)

CHALK_PI_BASE = 'https://chalk.charter.com/spaces/MDA/pages'
DEFAULT_PIS = [
    ('PI-46', f'{CHALK_PI_BASE}/3007682660/PI-46'),
    ('PI-47', f'{CHALK_PI_BASE}/3007682684/PI-47'),
    ('PI-48', f'{CHALK_PI_BASE}/3007682700/PI-48'),
    ('PI-49', f'{CHALK_PI_BASE}/3034265360/PI-49'),
    ('PI-50', f'{CHALK_PI_BASE}/3055797856/PI-50'),
    ('PI-51', f'{CHALK_PI_BASE}/3146012807/PI-51'),
    ('PI-52', f'{CHALK_PI_BASE}/3146012810/PI-52'),
    ('PI-53', f'{CHALK_PI_BASE}/3281127794/PI-53'),
    ('PI-54', f'{CHALK_PI_BASE}/3281128572/PI-54'),
    ('PI-55', f'{CHALK_PI_BASE}/3281128730/PI-55'),
]


def log(msg):
    print(msg, flush=True)


def preload(pi_filter=None, force=False):
    init_db()

    # Check if DB already has data
    if not force and not pi_filter and get_features_count() > 0:
        # Check if all PIs are loaded
        existing = load_all_features()
        missing_pis = [l for l, u in DEFAULT_PIS if l not in existing]
        if not missing_pis:
            stats = get_db_stats()
            chalk_count = get_chalk_cache_count()
            log('DB already has %d features and %d Chalk entries (%dKB) — all PIs loaded.' % (
                stats['feature_count'], chalk_count, stats['db_size_kb']))
            log('Use --force to re-fetch. Exiting.')
            return
        else:
            log('DB has data but missing PIs: %s. Loading those...' % ', '.join(missing_pis))
            pi_filter = None  # will be filtered below to only missing ones
            force = True  # allow the run

    t0 = time.time()
    log('=' * 60)
    log('  TSG DB Pre-loader')
    log('  DB: %s' % DB_PATH)
    log('=' * 60)

    # Filter PIs if requested
    pi_list = DEFAULT_PIS
    if pi_filter:
        pi_list = [(l, u) for l, u in DEFAULT_PIS if l == pi_filter]
        if not pi_list:
            log('ERROR: PI "%s" not found. Available: %s' % (pi_filter, ', '.join(l for l, _ in DEFAULT_PIS)))
            return
    else:
        # Auto-skip PIs already in DB (unless --force)
        existing = load_all_features()
        if existing and not force:
            pi_list = [(l, u) for l, u in DEFAULT_PIS if l not in existing]
            if not pi_list:
                log('All PIs already loaded. Use --force to re-fetch.')
                return

    log('\nLaunching browser...')
    pw = sync_playwright().start()
    br = pw.chromium.launch(headless=True, channel=get_browser_channel())
    ctx = br.new_context(viewport={'width': 1920, 'height': 1080})
    page = ctx.new_page()

    save_pi_pages(pi_list)
    total_features = 0
    total_chalk = 0

    for pi_idx, (pi_label, pi_url) in enumerate(pi_list, 1):
        pi_t0 = time.time()
        log('\n[%d/%d] %s' % (pi_idx, len(pi_list), pi_label))
        log('  URL: %s' % pi_url)

        # Step 1: Get feature list
        log('  Scanning for features...')
        feats = discover_features_on_pi(page, pi_url, log=lambda m: None)
        save_features(pi_label, feats)
        log('  Found %d features' % len(feats))
        total_features += len(feats)

        # Step 2: Fetch full Chalk data for each feature
        for fi, (fid, ftitle) in enumerate(feats, 1):
            try:
                chalk = fetch_feature_from_pi(page, pi_url, fid, log=lambda m: None)
                if chalk and chalk.scenarios:
                    save_chalk(fid, pi_label, chalk)
                    total_chalk += 1
                    log('  [%d/%d] %s: %d scenarios' % (fi, len(feats), fid, len(chalk.scenarios)))
                else:
                    log('  [%d/%d] %s: no scenarios found' % (fi, len(feats), fid))
            except Exception as e:
                log('  [%d/%d] %s: FAILED (%s)' % (fi, len(feats), fid, str(e)[:50]))

        pi_elapsed = time.time() - pi_t0
        log('  %s done in %.0fs' % (pi_label, pi_elapsed))

    ctx.close(); br.close(); pw.stop()

    elapsed = time.time() - t0
    m, s = divmod(int(elapsed), 60)
    stats = get_db_stats()

    log('\n' + '=' * 60)
    log('  PRE-LOAD COMPLETE')
    log('  PIs scanned:     %d' % len(pi_list))
    log('  Features found:  %d' % total_features)
    log('  Chalk cached:    %d' % total_chalk)
    log('  DB size:         %dKB' % stats['db_size_kb'])
    log('  Time:            %dm %ds' % (m, s))
    log('  DB path:         %s' % DB_PATH)
    log('=' * 60)
    log('\nDashboard will now load instantly. Run:')
    log('  streamlit run TSG_Dashboard_V2.0.py')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Pre-load TSG database with Chalk data')
    parser.add_argument('--pi', type=str, help='Fetch only a specific PI (e.g. PI-52)')
    parser.add_argument('--force', action='store_true', help='Re-fetch even if DB has data')
    args = parser.parse_args()
    preload(pi_filter=args.pi, force=args.force)
