"""Browser regression against the explicitly synthetic local frontend fixture.
Run the fixture with PORT=8767 node tests/preview_session_titles.cjs --serve --indicators.
Then: python tests/check_sidebar_indicators.py
"""
from playwright.sync_api import sync_playwright, expect

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    # Normalize the fixture so this check can be repeated without restarting it.
    rows = page.request.get('http://127.0.0.1:8767/api/runs').json()
    if rows[0]['status'] != 'running':
        page.request.post('http://127.0.0.1:8767/fixture/work')
    page.goto('http://127.0.0.1:8767')
    row = page.locator('[data-run="' + 'a' * 32 + '"]')
    expect(row.locator('.session-spinner')).to_be_visible()
    assert 'Working now' not in row.inner_text()
    page.get_by_role('button', name='Test fixture: finish work', exact=True).click()
    expect(row.locator('.session-completion:not(.is-read)')).to_be_visible()
    row.click()
    expect(row).to_have_attribute('aria-current', 'page')
    expect(row.locator('.session-completion')).to_have_css('opacity', '0')
    page.get_by_role('button', name='New session', exact=False).first.click()
    expect(row.locator('.session-completion')).to_have_count(0)
    page.reload()
    expect(row).to_be_visible()
    expect(row.locator('.session-completion')).to_have_count(0)
    page.get_by_role('button', name='Test fixture: start follow-up', exact=True).click()
    expect(row.locator('.session-spinner')).to_be_visible()
    page.get_by_role('button', name='Test fixture: finish work', exact=True).click()
    expect(row.locator('.session-completion:not(.is-read)')).to_be_visible()
    page.emulate_media(reduced_motion='reduce')
    row.click()
    expect(row.locator('.session-completion')).to_have_css('opacity', '0')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.get_by_role('button', name='Open sidebar', exact=True).click()
    expect(row).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert not errors, errors
    print('PASS: spinner, icon-only layout, unread completion, click-to-fade, reload persistence, follow-up completion, reduced motion, mobile layout; no page errors')
    browser.close()
