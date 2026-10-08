#!/usr/bin/env python3
"""Browser acceptance checks. Requires playwright and its Chromium browser."""
from __future__ import annotations

import argparse
import math
import json
import signal
import traceback
import shutil
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--artifacts", default="artifacts")
    parser.add_argument("--include-oxygen", action="store_true", help="O2指定電力モデルの実装・起動後に追加の受入確認を実行")
    args = parser.parse_args()
    artifacts = Path(args.artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    report = {"base_url": args.base_url, "api_mocked": False, "completed": False, "checks": [], "run_ids": []}
    report_path = artifacts / "verification.json"
    def expired(signum, frame):
        raise TimeoutError("Browser smoke exceeded its 240 second budget")
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(240)
    def passed(message):
        report["checks"].append(message)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print("PASS: " + message, flush=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium"), args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        try:
            # localhost is a secure context; emulate the UUID API availability of
            # an ordinary HTTP intranet URL to exercise the actual browser fallback.
            page.add_init_script("Object.defineProperty(window.crypto, 'randomUUID', {value: undefined, configurable: true})")
            errors: list[str] = []
            report["browser_runtime_errors"] = errors
            page.set_default_timeout(10000)
            page.set_default_navigation_timeout(20000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(args.base_url, wait_until="networkidle")
            page.get_by_role("heading", name="解析ワークスペース").wait_for()
            page.locator(".react-flow__node").first.wait_for()
            page.get_by_role("button", name="計算を実行", exact=True).click()
            page.get_by_role("alert").filter(has_text="社員番号").wait_for()
            assert page.locator("#employee-id").evaluate("el => el === document.activeElement")
            page.locator("#employee-id").fill("000777")
            page.get_by_label("回路名", exact=True).fill("ブラウザ検証：非線形EDD")
            page.locator('.react-flow__node[data-id="r1"]').click()
            page.get_by_label("値（SI）", exact=True).fill("180")
            page.get_by_label("値（SI）", exact=True).press("Tab")
            page.screenshot(path=str(artifacts / "editor.png"), full_page=True)

            def execute():
                with page.expect_response(lambda response: response.url.endswith("/api/runs") and response.request.method == "POST") as pending:
                    page.get_by_role("button", name="計算を実行", exact=True).click()
                assert pending.value.status == 202, pending.value.text()
                run_id = pending.value.json()["id"]
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    response = page.request.get(f"{args.base_url}/api/runs/{run_id}", timeout=10000)
                    assert response.ok, response.text()
                    run = response.json()
                    report["last_run"] = {"id": run_id, "status": run["status"], "error": run.get("error"), "converged": (run.get("result") or {}).get("converged")}
                    if run["status"] in {"succeeded", "failed", "canceled", "timed_out"}:
                        assert run["status"] == "succeeded", run.get("error")
                        page.locator(".result-health.ok").wait_for(timeout=10000)
                        report["run_ids"].append(run_id)
                        return run
                    time.sleep(.5)
                raise AssertionError("browser run timed out")

            edd = execute()
            assert edd["employee_id"] == "000777"
            assert next(c for c in edd["snapshot"]["components"] if c["id"] == "r1")["parameters"]["value"] == 180
            signals = edd["result"]["signals"]
            assert any(signal["unit"] == "C" for signal in signals)
            assert any(signal["name"] == "V(edd1:branch1)" for signal in signals)
            page.get_by_role("button", name="X–Y / Q–V", exact=True).click()
            assert page.locator(".xy-select select").first.input_value() == "V(edd1:branch1)"
            page.screenshot(path=str(artifacts / "edd-qv.png"), full_page=True)
            page.get_by_role("button", name="波形", exact=True).click()
            with page.expect_download() as download:
                page.get_by_role("button", name="CSV保存", exact=True).click()
            download.value.save_as(artifacts / "edd.csv")
            assert (artifacts / "edd.csv").stat().st_size > 100
            page.screenshot(path=str(artifacts / "results.png"), full_page=True)
            passed("employee validation, edited EDD snapshot, real run, charge/branch voltage, Q–V, CSV")

            page.get_by_role("button", name="新規", exact=True).click()
            for title in ("電圧源を追加", "抵抗を追加", "抵抗を追加", "GNDを追加"):
                page.get_by_title(title, exact=True).click()
            page.get_by_role("button", name="回路全体を表示").click()
            page.wait_for_timeout(400)
            node_ids = page.locator(".react-flow__node").evaluate_all("nodes => nodes.map(n => n.dataset.id)")
            voltage, r1, r2, ground = node_ids

            def connect(source: str, source_port: str, target: str, target_port: str):
                a = page.locator(f'.react-flow__node[data-id="{source}"] .react-flow__handle[data-handleid="{source_port}"]').bounding_box()
                b = page.locator(f'.react-flow__node[data-id="{target}"] .react-flow__handle[data-handleid="{target_port}"]').bounding_box()
                assert a and b
                page.mouse.move(a["x"]+a["width"]/2, a["y"]+a["height"]/2)
                page.mouse.down()
                page.mouse.move(b["x"]+b["width"]/2, b["y"]+b["height"]/2, steps=15)
                page.mouse.up()

            for endpoints in ((voltage,"p",r1,"p"),(r1,"n",r2,"p"),(r2,"n",ground,"g"),(voltage,"n",ground,"g")):
                connect(*endpoints)
            assert page.locator(".react-flow__edge").count() == 4
            page.get_by_label("解析方法", exact=True).select_option("op")
            page.get_by_label("回路名", exact=True).fill("ブラウザ検証：手配線の分圧回路")
            divider = execute()
            assert len(divider["snapshot"]["wires"]) == 4
            assert any(math.isclose(v, 2.5, rel_tol=1e-6) for v in divider["result"]["summary"].values() if isinstance(v,(float,int)))
            page.get_by_role("button", name="回路エディタ", exact=True).click()
            page.screenshot(path=str(artifacts / "constructed-circuit.png"), full_page=True)
            page.get_by_role("button", name="保存済みモデル", exact=False).click()
            page.get_by_label("モデルを検索", exact=True).fill("ブラウザ検証：非線形EDD")
            page.locator(".model-table .model-name button").filter(has_text="ブラウザ検証：非線形EDD").first.click()
            # Opening a saved circuit waits for document/history GETs. A single
            # input_value() can still read the previous operating-point analysis.
            page.wait_for_function("""() => !document.querySelector('.model-library') ||
                document.querySelector('#modal-title')?.textContent === '未保存の変更があります'""")
            replace = page.get_by_role("button", name="変更を閉じて開く", exact=True)
            if replace.is_visible():
                replace.click()
            expect(page.get_by_label("解析方法", exact=True)).to_have_value("transient")
            expect(page.get_by_label("回路名", exact=True)).to_have_value("ブラウザ検証：非線形EDD")
            page.get_by_role("button", name="すべて", exact=True).click()
            page.locator(".run-list button").first.click()
            page.locator(".result-health.ok").wait_for()
            passed("palette, terminal drag wiring, saved divider numeric value, reopening circuit analysis/history")

            if args.include_oxygen:
                page.get_by_role("button", name="プリセットから始める", exact=False).click()
                assert not page.locator(".preset-card").filter(has_text="CF4").count()
                page.locator(".preset-card").filter(has_text="O2 CCP").first.click()
                replace = page.get_by_role("button", name="変更を閉じて開く", exact=True)
                if replace.is_visible():
                    replace.click()
                page.get_by_label("解析方法", exact=True).select_option("global")
                page.get_by_label("電力の与え方", exact=True).select_option("prescribed_absorbed")
                expect(page.get_by_label("解析方法", exact=True)).to_have_value("global")
                expect(page.get_by_label("電力の与え方", exact=True)).to_have_value("prescribed_absorbed")
                assert not page.get_by_label("RF周波数", exact=False).count()
                assert not page.get_by_label("RF電圧・ピーク", exact=False).count()
                displayed_power = float(page.get_by_label("総吸収プラズマ電力", exact=True).input_value())
                assert displayed_power > 0
                assert "CF4" not in page.get_by_label("単一ガス", exact=True).locator("option").evaluate_all("options => options.map(option => option.value)")
                oxygen = execute()
                assert oxygen["analysis"]["settings"]["power_mode"] == "prescribed_absorbed"
                assert math.isclose(oxygen["analysis"]["settings"]["absorbed_power_w"], displayed_power)
                assert oxygen["analysis"]["settings"]["gas"] == "O2"
                assert not oxygen["result"]["signals"]
                assert not page.locator(".chart-card").count()
                assert page.locator(".numeric-table tbody tr").count() > 5
                assert page.get_by_role("heading", name="O₂・指定電力0D反応計算", exact=True).count() == 1
                page.screenshot(path=str(artifacts / "oxygen-0d.png"), full_page=True)
                with page.expect_download() as download:
                    page.get_by_role("button", name="CSV保存", exact=True).click()
                download.value.save_as(artifacts / "oxygen-0d.csv")
                assert (artifacts / "oxygen-0d.csv").stat().st_size > 100
                passed("Ar/O2 presets, prescribed-power O2 global settings, steady tables without RF waveforms, CSV")

            page.set_viewport_size({"width": 390,"height": 844})
            page.screenshot(path=str(artifacts / "mobile.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth+2")
            assert not errors, errors
            passed("responsive layout and no browser runtime errors")
            report["completed"] = True
            signal.alarm(0)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            browser.close()
        except BaseException as exc:
            report["failure"] = str(exc)
            report["traceback"] = traceback.format_exc()
            try:
                page.screenshot(path=str(artifacts / "failure.png"), full_page=True)
                report["failure_screenshot"] = str(artifacts / "failure.png")
            except Exception as screenshot_error:
                report["screenshot_error"] = str(screenshot_error)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print("FAIL: " + str(exc), flush=True)
            browser.close()
            raise



if __name__ == "__main__":
    main()
