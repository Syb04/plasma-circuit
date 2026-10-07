#!/usr/bin/env python3
"""Read-only browser verification of Warm Clay modes and existing charts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--output", type=Path, default=Path("reports/ui-theme"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip("/")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, color_scheme="dark")
        page = context.new_page()
        errors: list[str] = []
        writes: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: writes.append(request.method+" "+request.url)
                if request.method not in {"GET", "HEAD", "OPTIONS"} else None)
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(base, wait_until="networkidle")
        theme = page.get_by_label("外観", exact=True)
        theme.wait_for()

        def verify_mode(mode: str) -> None:
            page.wait_for_function("mode => document.documentElement.dataset.theme === mode", arg=mode)
            expected = "rgb(250, 249, 245)" if mode == "light" else "rgb(38, 38, 36)"
            actual = page.evaluate("getComputedStyle(document.body).backgroundColor")
            assert actual == expected, (mode, actual)
            # Existing button/card transitions last 150 ms; capture their final
            # colors rather than an intermediate blend immediately after switch.
            page.wait_for_timeout(250)

        assert theme.input_value() == "system"
        verify_mode("dark")
        theme.select_option("light")
        verify_mode("light")
        page.reload(wait_until="networkidle")
        assert theme.input_value() == "light"
        verify_mode("light")
        page.emulate_media(color_scheme="light")
        theme.select_option("dark")
        verify_mode("dark")
        page.reload(wait_until="networkidle")
        assert theme.input_value() == "dark"
        verify_mode("dark")
        theme.select_option("system")
        verify_mode("light")
        page.emulate_media(color_scheme="dark")
        verify_mode("dark")

        for mode in ("light", "dark"):
            theme.select_option(mode)
            verify_mode(mode)
            page.locator(".react-flow__node").first.wait_for()
            page.locator("#employee-id").focus()
            page.screenshot(path=str(args.output/f"{mode}-editor.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth+2")
            page.screenshot(path=str(args.output/f"{mode}-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1600, "height": 1000})

        # Read existing results only: do not save a circuit or start a run for a
        # color-change check. The returned IDs are used locally, not published.
        response = page.request.get(base+"/api/runs")
        assert response.ok, response.status
        existing = response.json().get("runs", [])
        waveform = None
        for run in existing:
            if run.get("status") != "succeeded":
                continue
            detail = page.request.get(base+"/api/runs/"+run["id"])
            assert detail.ok
            candidate = detail.json()
            result = candidate.get("result") or {}
            if (result.get("axis") or {}).get("values") and result.get("signals"):
                waveform = candidate
                break
        chart_verified = False
        if waveform is not None:
            circuit = page.request.get(base+"/api/circuits/"+waveform["circuit_id"])
            assert circuit.ok
            listing = page.request.get(base+"/api/circuits")
            assert listing.ok
            circuit_index = next(i for i, row in enumerate(listing.json()["circuits"])
                                 if row["id"] == waveform["circuit_id"])
            page.get_by_role("button", name="保存済みの回路", exact=False).click()
            page.locator(".saved-circuits button").nth(circuit_index).click()
            page.wait_for_function("""() => !document.querySelector('.modal') ||
                document.querySelector('#modal-title')?.textContent === '未保存の変更があります'""")
            replace = page.get_by_role("button", name="変更を閉じて開く", exact=True)
            if replace.is_visible():
                replace.click()
            page.wait_for_function("() => document.querySelectorAll('.recent-run').length > 0")
            history = page.request.get(base+"/api/runs?circuit_id="+waveform["circuit_id"])
            assert history.ok
            run_index = next(i for i, row in enumerate(history.json()["runs"])
                             if row["id"] == waveform["id"])
            page.get_by_role("button", name="すべて", exact=True).click()
            page.locator(".run-list button").nth(run_index).click()
            page.locator(".recharts-wrapper").first.wait_for()
            strokes = {}
            for mode in ("light", "dark"):
                theme.select_option(mode)
                verify_mode(mode)
                line = page.locator(".recharts-line-curve").first
                if line.count():
                    strokes[mode] = line.evaluate("el => getComputedStyle(el).stroke")
                page.screenshot(path=str(args.output/f"{mode}-results.png"), full_page=True)
                wrapper = page.locator(".recharts-wrapper").first.bounding_box()
                if wrapper:
                    page.mouse.move(wrapper["x"]+wrapper["width"]*.6,
                                    wrapper["y"]+wrapper["height"]*.5)
                    page.wait_for_timeout(250)
                    tooltip = page.locator(".recharts-tooltip-wrapper").first
                    if tooltip.count() and tooltip.is_visible():
                        page.screenshot(path=str(args.output/f"{mode}-tooltip.png"), full_page=True)
            if len(strokes) == 2:
                assert strokes["light"] != strokes["dark"], strokes
            chart_verified = True
        assert not writes, writes
        assert not errors, errors
        summary = {"base_url": base, "modes": ["system", "light", "dark"],
            "system_default_and_live_os_change": True, "explicit_mode_overrides_os": True,
            "light_and_dark_persist_after_reload": True,
            "desktop_and_mobile_no_horizontal_overflow": True,
            "existing_waveform_rendered": chart_verified,
            "database_mutating_requests": len(writes), "browser_runtime_errors": errors}
        (args.output/"verification.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        print(json.dumps(summary, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
