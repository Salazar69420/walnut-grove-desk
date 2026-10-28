import os
import re

import pytest
from playwright.sync_api import expect

from helpers import BASE, sign_in

OWNER = os.environ.get("WALNUT_E2E_OWNER", "garg")
DESK = os.environ.get("WALNUT_E2E_DESK", "priya")
NURSE = os.environ.get("WALNUT_E2E_NURSE", "anita")


def page_for(ctx, user):
    p = ctx().new_page()
    sign_in(p, user)
    return p


def go(p, label):
    p.get_by_role("link", name=re.compile("^" + label)).click()


@pytest.mark.parametrize("user,landing", [(OWNER, "Good"), (DESK, "Overnight"), (NURSE, "Lab results")])
def test_sign_in_each_role(ctx, user, landing):
    p = page_for(ctx, user)
    expect(p.get_by_role("heading", level=1).first).to_contain_text(landing)
    expect(p.get_by_text("Walnut Grove Desk").first).to_be_visible()


def test_wrong_password_generic(ctx):
    p = ctx().new_page()
    p.goto(BASE + "/login")
    p.get_by_label("Username").fill(DESK)
    p.get_by_label("Password").fill("not-the-password")
    p.get_by_role("button", name="Sign in").click()
    expect(p.get_by_role("alert")).to_have_text("Wrong username or password")


def test_desk_claims_done_and_approves_draft(ctx):
    p = page_for(ctx, DESK)
    go(p, "Callbacks")
    row = p.locator(".row", has_text="Walker").first
    row.get_by_role("button", name="I will call back").click()
    expect(p.get_by_text("Assigned to you")).to_be_visible()
    expect(p.locator(".row", has_text="Walker").first.get_by_role("button", name="Mark done")).to_be_visible()
    p.locator(".row", has_text="Walker").first.get_by_role("button", name="Mark done").click()
    expect(p.locator(".row.done", has_text="Walker")).to_have_count(1)
    draft = p.locator(".draft", has_text="Ready for pickup").first
    draft.get_by_role("button", name="Approve and send").click()
    expect(p.get_by_text("Mock only: a real version would send this text now")).to_be_visible()
    expect(p.locator(".draft", has_text="Sent (mock)").first).to_be_visible()


def test_nurse_advances_result_and_cannot_see_drafts(ctx):
    p = page_for(ctx, NURSE)
    go(p, "Lab results")
    btn = p.get_by_role("button", name="Mark reviewed").first
    btn.click()
    expect(p.get_by_text("Marked reviewed")).to_be_visible()
    go(p, "Callbacks")
    expect(p.get_by_text("Messages to approve")).to_have_count(0)


def test_live_update_between_two_users(ctx):
    desk = page_for(ctx, DESK)
    nurse = page_for(ctx, NURSE)
    go(desk, "Callbacks")
    go(nurse, "Callbacks")
    row = desk.locator(".row:not(.done)", has=desk.get_by_role("button", name="I will call back")).first
    who = row.locator(".who").inner_text().split(" for ")[0].strip()
    row.get_by_role("button", name="I will call back").click()
    expect(nurse.locator(".row", has_text=who).first.locator(".tag.acc")).to_contain_text("With " + os.environ.get("WALNUT_E2E_DESK_NAME", "Priya"), timeout=5000)


def test_owner_approves_desk_shift(ctx):
    p = page_for(ctx, OWNER)
    rec = p.locator(".rec", has_text="open desk shift").first
    rec.get_by_role("button", name="Approve").click()
    expect(p.get_by_text("Added to the schedule")).to_be_visible()
    go(p, "Schedule")
    expect(p.locator(".shift.new").first).to_be_visible()


def run_sim(p, channel, first_option, expect_text):
    go(p, "Overnight")
    sim = p.locator("#sim")
    sim.get_by_role("button", name=channel, exact=True).click()
    sim.get_by_role("button", name=first_option).first.wait_for()
    sim.get_by_role("button", name=first_option).first.click()
    return sim


@pytest.mark.parametrize("channel", ["Text message", "Phone call"])
def test_simulator_emergency(ctx, channel):
    p = page_for(ctx, DESK)
    sim = run_sim(p, channel, "Something urgent", "")
    expect(sim.locator(".bub.bot").last).to_contain_text("vet", timeout=5000)
    expect(p.get_by_text("New emergency routing")).to_have_count(0)  # event text is in the feed, not here
    expect(sim.get_by_text("added to the overnight list")).to_be_visible(timeout=8000)


@pytest.mark.parametrize("channel", ["Text message", "Phone call"])
def test_simulator_booking_and_free_text(ctx, channel):
    p = page_for(ctx, DESK)
    go(p, "Overnight")
    sim = p.locator("#sim")
    sim.get_by_role("button", name=channel, exact=True).click()
    sim.get_by_role("button", name="Book a visit").wait_for()
    sim.get_by_role("button", name="Book a visit").click()
    sim.get_by_role("button", name=re.compile("Biscuit")).first.click()
    sim.get_by_role("button", name=re.compile(r"\d+:\d\d (am|pm)")).first.click()
    expect(sim.get_by_text("added to the overnight list")).to_be_visible(timeout=8000)
    expect(p.locator(".tag.warn", has_text="From your test").first).to_be_visible()


def test_simulator_refill_and_typed_message(ctx):
    p = page_for(ctx, DESK)
    go(p, "Overnight")
    sim = p.locator("#sim")
    sim.get_by_role("button", name="Refill or food order").click()
    sim.get_by_role("button", name=re.compile("Biscuit")).first.click()
    expect(sim.get_by_text("added to the overnight list")).to_be_visible(timeout=8000)
    sim.get_by_role("button", name="Start over").click()
    sim.get_by_label("Type your own message").wait_for()
    sim.get_by_label("Type your own message").fill("My dog is having a seizure")
    sim.get_by_role("button", name="Send").click()
    expect(sim.locator(".bub.bot").last).to_contain_text("vet", timeout=5000)


def test_desk_blocked_from_brief(ctx):
    p = page_for(ctx, DESK)
    expect(p.get_by_role("link", name=re.compile("^Brief"))).to_have_count(0)
    r = p.request.get(BASE + "/api/brief")
    assert r.status == 403
    p.goto(BASE + "/brief")
    p.wait_for_selector("nav[aria-label=Sections]")
    assert p.url.rstrip("/").endswith("/overnight")


def test_admin_hidden_from_non_admin(ctx):
    p = page_for(ctx, NURSE)
    expect(p.get_by_role("link", name="Admin")).to_have_count(0)
