#!/usr/bin/env python3
"""Live browser/API acceptance for studies, comparison, references and packages.

Creates bounded validation circuits and real solver jobs. No API mocking or
synthetic solver results are used. Requires Playwright and Chromium.
"""
from __future__ import annotations

import argparse
import json
import shutil
import signal
import time
import traceback
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

LATEST_EVIDENCE: dict = {}
REPORT_PATH: Path | None = None


def budget_expired(signum, frame) -> None:
    raise TimeoutError("Feature browser acceptance exceeded its 240 second overall budget")


def main() -> None:
    global LATEST_EVIDENCE, REPORT_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--output", type=Path, default=Path("reports/feature-ui"))
    parser.add_argument("--verify-existing-physics", action="store_true", help="Require and render successful real IEDF/macro/radial runs; run check_plasma_features.py first")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip("/")
    stamp = str(int(time.time()))
    evidence: dict = {"base_url": base, "api_mocked": False, "solver_results_mocked": False,
                      "employee_id": "feature-validation", "completed": False, "checks": []}
    LATEST_EVIDENCE = evidence
    REPORT_PATH = args.output / "verification.json"
    signal.signal(signal.SIGALRM, budget_expired)
    signal.alarm(240)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which("chromium"), args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1600, "height": 1100}, accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(10000)
        page.set_default_navigation_timeout(20000)
        errors: list[str] = []
        evidence["browser_runtime_errors"] = errors
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(base, wait_until="networkidle")
        page.get_by_role("heading", name="解析ワークスペース", exact=True).wait_for()
        page.get_by_label("社員番号", exact=False).fill("feature-validation")
        page.get_by_label("外観", exact=True).select_option("light")

        def passed(message: str) -> None:
            evidence["checks"].append(message)
            REPORT_PATH.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
            print("PASS: " + message, flush=True)

        def get(path: str) -> dict:
            response = page.request.get(base + "/api" + path, timeout=10000)
            assert response.ok, (path, response.status, response.text())
            value = response.json()
            if path.startswith(("/studies/", "/runs/")):
                evidence["last_polled_state"] = {"path": path, "id": value.get("id"),
                    "status": value.get("status"), "error": value.get("error"), "counts": value.get("counts"),
                    "converged": (value.get("result") or {}).get("converged"),
                    "cases": [{"status": case["status"], "run_id": case.get("run_id"),
                               "error": case.get("attempts", [{}])[-1].get("error")}
                              for case in value.get("cases", [])]}
            return value

        def settle_document_open() -> None:
            # A saved circuit waits for document/history GETs before asking to
            # replace dirty input. Wait for that outcome instead of polling the
            # confirmation button immediately after the saved-circuit click.
            page.wait_for_function("""() => !document.querySelector('.modal') ||
                document.querySelector('#modal-title')?.textContent === '未保存の変更があります'""")
            replace = page.get_by_role("button", name="変更を閉じて開く", exact=True)
            if replace.is_visible():
                replace.click()
            expect(page.locator(".modal-backdrop")).to_have_count(0)

        def open_preset(name: str) -> None:
            page.get_by_role("button", name="プリセットから始める", exact=False).click()
            page.locator(".preset-card").filter(has_text=name).first.click()
            settle_document_open()
            page.get_by_label("解析方法", exact=True).wait_for()

        def wait_study(study_id: str, timeout: float = 60) -> dict:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                value = get("/studies/" + study_id)
                if value["status"] not in {"queued", "running"}:
                    return value
                page.wait_for_timeout(300)
            raise AssertionError("Study did not finish within the browser acceptance budget")

        def start_study(name: str, values: str) -> dict:
            page.get_by_label("研究名", exact=True).fill(name)
            page.get_by_label("軸1の変更する項目", exact=True).select_option("document.components.r1.parameters.value")
            page.get_by_label("軸1の値（カンマ区切り）", exact=True).fill(values)
            with page.expect_response(lambda r: r.url.endswith("/api/studies") and r.request.method == "POST") as request:
                page.get_by_role("button", name="研究を開始", exact=True).click()
            assert request.value.status == 202, request.value.text()
            return request.value.json()

        def screenshot(name: str) -> None:
            page.screenshot(path=str(args.output / name), full_page=True)

        open_preset("非線形電荷EDD")
        page.get_by_label("回路名", exact=True).fill("Feature UI validation " + stamp)
        page.get_by_role("button", name="研究・比較", exact=True).click()
        page.get_by_role("button", name="第2軸を追加", exact=True).click()
        page.get_by_label("軸2の変更する項目", exact=True).select_option("document.components.v1.parameters.waveform.frequency")
        page.get_by_label("軸2の値（カンマ区切り）", exact=True).fill("1000, 2000")
        study = start_study("Real EDD study " + stamp, "100, 180")
        completed = wait_study(study["id"])
        assert completed["counts"]["succeeded"] == 4, completed
        all_study_runs = [get("/runs/" + case["run_id"]) for case in completed["cases"]]
        assert {next(c for c in run["snapshot"]["components"] if c["id"] == "v1")["parameters"]["waveform"]["frequency"]
                for run in all_study_runs} == {1000, 2000}
        run_ids = [case["run_id"] for case in completed["cases"]
                   if case["coordinates"]["document.components.v1.parameters.waveform.frequency"] == 1000]
        actual_runs = [get("/runs/" + run_id) for run_id in run_ids]
        assert all(run["result"]["converged"] for run in actual_runs)
        assert [next(c for c in run["snapshot"]["components"] if c["id"] == "r1")["parameters"]["value"]
                for run in actual_runs] == [100, 180]
        page.get_by_role("button", name="結果を開く", exact=True).first.wait_for(timeout=10000)
        with page.expect_download() as download:
            page.get_by_role("button", name="研究CSV", exact=True).click()
        download.value.save_as(args.output / "study.csv")
        assert "document.components.r1.parameters.value" in (args.output / "study.csv").read_text()
        screenshot("studies-live.png")
        evidence["study_id"] = study["id"]
        passed("UI-created two-axis study runs real EDD component and source-waveform cases; immutable inputs and CSV")
        page.get_by_role("button", name="第2軸を削除", exact=True).click()

        retry_study = start_study("Cancel resume validation " + stamp, "130, 135, 140, 145, 150, 155, 160, 165")
        with page.expect_response(lambda r: r.url.endswith("/cancel") and r.request.method == "POST") as request:
            page.get_by_role("button", name="研究を停止", exact=True).click()
        assert request.value.ok, request.value.text()
        stopped = wait_study(retry_study["id"])
        page.get_by_role("button", name="未完了ケースを再開", exact=True).wait_for(timeout=10000)
        with page.expect_response(lambda r: r.url.endswith("/resume") and r.request.method == "POST") as request:
            page.get_by_role("button", name="未完了ケースを再開", exact=True).click()
        assert request.value.status == 202, request.value.text()
        resumed = wait_study(retry_study["id"])
        assert resumed["counts"]["succeeded"] == 8, resumed
        if stopped["counts"]["canceled"]:
            assert any(len(case["attempts"]) > 1 for case in resumed["cases"]), resumed
        evidence["cancel_resume_study_id"] = retry_study["id"]
        evidence["canceled_cases"] = stopped["counts"]["canceled"]
        passed("UI cancel/resume uses real case status and preserves attempts")

        page.get_by_role("button", name="結果比較", exact=True).click()
        for run_id in run_ids:
            page.locator(".compare-run-list label").filter(has_text=run_id[:12]).locator("input").check()
        page.get_by_label("RFの基本波位相を合わせる", exact=True).uncheck()
        with page.expect_response(lambda r: r.url.endswith("/api/compare") and r.request.method == "POST") as request:
            page.get_by_role("button", name="2件を比較", exact=True).click()
        assert request.value.ok, request.value.text()
        comparison = request.value.json()
        assert any(d["path"].endswith("r1.parameters.value") for d in comparison["input_differences"])
        page.get_by_role("heading", name="保存波形の重ね合わせ", exact=True).wait_for()
        page.get_by_label("比較する信号", exact=True).wait_for()
        screenshot("compare-live-waveforms.png")
        passed("Real saved waveform comparison and input differences")

        # A real operating-point job has no waveform axis. Compare it with a
        # transient job to exercise nullable-axis handling in the frontend.
        preset = next(p for p in get("/presets")["presets"] if p["id"] == "divider")
        circuit_response = page.request.post(base + "/api/circuits", data={"employee_id": "feature-validation", "document": preset["document"]})
        assert circuit_response.status == 201, circuit_response.text()
        circuit = circuit_response.json()
        run_response = page.request.post(base + "/api/runs", data={"employee_id": "feature-validation", "circuit_id": circuit["id"], "expected_revision": circuit["revision"], "analysis": preset["analysis"]})
        assert run_response.status == 202, run_response.text()
        op_id = run_response.json()["id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            op_run = get("/runs/" + op_id)
            if op_run["status"] not in {"queued", "running"}:
                break
            page.wait_for_timeout(250)
        assert op_run["status"] == "succeeded", op_run
        page.get_by_role("button", name="パラメータ研究", exact=True).click()
        page.get_by_role("button", name="結果比較", exact=True).click()
        page.locator(".compare-run-list label").filter(has_text=run_ids[1][:12]).locator("input").uncheck()
        page.locator(".compare-run-list label").filter(has_text=op_id[:12]).locator("input").check()
        page.get_by_role("button", name="2件を比較", exact=True).click()
        page.get_by_text("定常結果は以下の数値で比較できます。", exact=False).wait_for()
        screenshot("compare-live-null-axis.png")
        passed("Mixed operating-point/transient comparison handles absent axis")

        page.get_by_role("button", name="参照データ", exact=True).click()
        metric, metric_value = next((name, value) for name, value in op_run["result"]["summary"].items()
                                  if isinstance(value, (int, float)) and not isinstance(value, bool))
        metric_unit = next(row["unit"] for table in op_run["result"]["tables"] for row in table.get("rows", [])
                           if isinstance(row, dict) and row.get("signal") == metric)
        page.get_by_label("参照名", exact=True).fill("Acceptance numeric fixture " + stamp)
        page.get_by_label("材料・ガス", exact=True).fill("Ar")
        page.get_by_label("RF周波数（Hz）", exact=True).fill("40000000")
        page.get_by_label("圧力（Pa）", exact=True).fill("1.333223684")
        page.get_by_label("測定の定義", exact=True).fill("Synthetic reference copied from an actual operating-point result for workflow verification")
        page.get_by_label("出典・測定条件", exact=True).fill("Workflow test fixture, not experimental validation; run " + op_id)
        csv_text = f"metric,value,unit,uncertainty\n{metric},{metric_value},{metric_unit},0.001\n"
        page.get_by_label("CSV / JSONファイル", exact=True).set_input_files({"name": "reference.csv", "mimeType": "text/csv", "buffer": csv_text.encode()})
        with page.expect_response(lambda r: r.url.endswith("/api/benchmarks") and r.request.method == "POST") as request:
            page.get_by_role("button", name="参照を登録", exact=True).click()
        assert request.value.status == 201, request.value.text()
        benchmark = request.value.json()
        page.get_by_label("計算結果", exact=True).select_option(op_id)
        with page.expect_response(lambda r: "/api/benchmarks/" in r.url and r.url.endswith("/compare")) as request:
            page.get_by_role("button", name="参照と比較", exact=True).click()
        assert request.value.ok, request.value.text()
        assert request.value.json()["comparisons"][0]["absolute_error"] == 0
        screenshot("reference-live-synthetic-benchmark.png")
        evidence["benchmark_id"] = benchmark["id"]
        evidence["reference_is_experimental"] = False
        passed("CSV upload and real benchmark comparison; explicitly synthetic reference")

        page.get_by_role("button", name="モデルパッケージ", exact=True).click()
        page.get_by_label("保存結果", exact=True).select_option(run_ids[0])
        with page.expect_download() as download:
            page.get_by_role("button", name="モデルパッケージ保存", exact=True).click()
        package_path = args.output / "analysis-package.json"
        download.value.save_as(package_path)
        package = json.loads(package_path.read_text())
        assert package["format"] == "plasma-circuit-analysis" and package["hashes"]
        page.get_by_label("JSONファイル", exact=True).set_input_files(package_path)
        screenshot("package-live-before-import.png")
        with page.expect_response(lambda r: r.url.endswith("/api/packages/import") and r.request.method == "POST") as request:
            page.get_by_role("button", name="検証して新規回路に読込", exact=True).click()
        assert request.value.status == 201, request.value.text()
        imported = request.value.json()
        assert imported["verification"] == {"hashes_verified": True, "authenticity_verified": False}
        assert imported["requires_recalculation"] and imported["result"] is None
        assert imported["circuit"]["id"] != actual_runs[0]["circuit_id"]
        evidence["imported_circuit_id"] = imported["circuit"]["id"]
        passed("Real exported package upload/import verifies hashes and requires recalculation")

        open_preset("O2 CCP")
        page.get_by_label("解析方法", exact=True).select_option("global")
        page.get_by_label("電力の与え方", exact=True).select_option("prescribed_absorbed")
        assert page.get_by_label("総吸収プラズマ電力", exact=True).count() == 1
        assert page.get_by_label("RF電圧・ピーク", exact=True).count() == 0
        assert page.get_by_label("電子加熱モデル", exact=True).count() == 0
        assert page.get_by_text("イオンエネルギー分布（IEDF・実験的）", exact=True).count() == 0
        screenshot("controls-oxygen-prescribed.png")
        page.get_by_label("解析方法", exact=True).select_option("global_transient")
        assert page.get_by_label("初期ガス温度", exact=True).count() == 1
        assert page.get_by_label("RF再評価間隔", exact=True).count() == 0
        screenshot("controls-macro-prescribed.png")
        page.get_by_label("解析方法", exact=True).select_option("radial")
        assert page.get_by_label("径方向セル数", exact=True).count() == 1
        assert page.get_by_label("RF電圧・ピーク", exact=True).count() == 1
        assert page.get_by_text("表面・材料条件", exact=True).count() == 0
        screenshot("controls-radial.png")
        page.get_by_label("解析方法", exact=True).select_option("ccp")
        page.get_by_text("イオンエネルギー分布（IEDF・実験的）", exact=True).click()
        page.get_by_label("シース内粒子軌道を計算", exact=True).check()
        assert page.get_by_label("粒子数 / イオン種", exact=True).count() == 1
        screenshot("controls-iedf.png")
        passed("Analysis controls expose only supported power/heating/IEDF/radial inputs")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
        screenshot("controls-mobile.png")
        passed("Mobile layout has no horizontal overflow")
        if args.verify_existing_physics:
            page.set_viewport_size({"width": 1600, "height": 1100})
            page.reload(wait_until="networkidle")
            # Global history is capped at 100; inspect each saved circuit so
            # workflow reruns cannot hide the required older physics results.
            circuits = get("/circuits")["circuits"]
            listed_runs = [run for circuit in circuits
                           for run in get("/runs?circuit_id=" + circuit["id"])["runs"]]
            targets = {}
            for item in listed_runs:
                if item["status"] != "succeeded":
                    continue
                kind = item["analysis"]["kind"]
                if kind not in {"ccp", "global_transient", "radial"}:
                    continue
                if kind == "ccp" and not item["analysis"]["settings"].get("iedf", {}).get("enabled"):
                    continue
                detail = get("/runs/" + item["id"])
                key = "iedf" if kind == "ccp" else kind
                if key not in targets and detail.get("result", {}).get("converged"):
                    targets[key] = detail
                if len(targets) == 3:
                    break
            assert set(targets) == {"iedf", "global_transient", "radial"}, "Run check_plasma_features.py first: converged CCP+IEDF, macro and radial results are required"
            evidence["existing_physics_runs_rendered"] = {}
            circuits = get("/circuits")["circuits"]
            for key, target in targets.items():
                circuit_index = next(i for i, value in enumerate(circuits) if value["id"] == target["circuit_id"])
                page.get_by_role("button", name="保存済みの回路", exact=False).click()
                page.locator(".saved-circuits button").nth(circuit_index).click()
                settle_document_open()
                expect(page.get_by_label("回路名", exact=True)).to_have_value(circuits[circuit_index]["name"])
                history = get("/runs?circuit_id=" + target["circuit_id"])["runs"]
                run_index = next(i for i, value in enumerate(history) if value["id"] == target["id"])
                page.get_by_role("button", name="すべて", exact=True).click()
                page.locator(".run-list button").nth(run_index).click()
                page.locator(".result-health.ok").wait_for()
                if key == "iedf":
                    page.get_by_role("heading", name="イオンエネルギー分布（IEDF）", exact=True).wait_for()
                    histogram = page.get_by_role("img", name="IEDFエネルギーヒストグラム", exact=True)
                    histogram.wait_for()
                    histogram.scroll_into_view_if_needed()
                else:
                    page.get_by_role("button", name="時間発展" if key == "global_transient" else "径方向分布", exact=True).wait_for()
                    page.locator(".chart-card .chart-area").first.scroll_into_view_if_needed()
                screenshot("result-live-" + key + ".png")
                page.get_by_label("外観", exact=True).select_option("dark")
                page.wait_for_timeout(250)
                screenshot("result-live-" + key + "-dark.png")
                page.get_by_label("外観", exact=True).select_option("light")
                evidence["existing_physics_runs_rendered"][key] = target["id"]
                passed("Existing real solver result rendered: " + key)
            assert set(evidence["existing_physics_runs_rendered"]) == {"iedf", "global_transient", "radial"}
        assert not errors, errors
        evidence["browser_runtime_errors"] = errors
        evidence["completed"] = True
        signal.alarm(0)
        REPORT_PATH.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        print(json.dumps(evidence, ensure_ascii=False), flush=True)
        browser.close()


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        LATEST_EVIDENCE["failure"] = str(exc)
        LATEST_EVIDENCE["traceback"] = traceback.format_exc()
        if REPORT_PATH:
            REPORT_PATH.write_text(json.dumps(LATEST_EVIDENCE, ensure_ascii=False, indent=2))
        print("FAIL: " + str(exc), flush=True)
        raise
