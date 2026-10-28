import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture(scope="session")
def pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(pw):
    b = pw.chromium.launch()
    yield b
    b.close()


@pytest.fixture
def ctx(browser):
    made = []

    def make(**kw):
        c = browser.new_context(viewport={"width": 1400, "height": 1000}, **kw)
        made.append(c)
        return c

    yield make
    for c in made:
        c.close()
