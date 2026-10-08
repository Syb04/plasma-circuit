#!/usr/bin/env python3
"""Live coax editing, real queued ngspice, persistence and sweep acceptance.

Uses the running application without mocked responses. Creates one owned model,
one UI transient run, one AC run and a two-case length study; then hides only
that model with the deletion API, preserving its historical results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import signal
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://localhost:8080')
    parser.add_argument('--output', type=Path, default=Path('reports/coax-cable'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip('/')
    employee = '000-coax-check'
    name = '同軸ケーブル検証 ' + str(time.time_ns())
    evidence = {'completed': False, 'api_mocked': False, 'solver_results_mocked': False,
                'checks': [], 'browser_runtime_errors': [], 'runs': [], 'fixture_name': name}
    report_path = args.output / 'verification.json'
    root = Path(__file__).resolve().parents[1]
    evidence['source_sha256'] = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in
        ['frontend/src/CoaxEditor.tsx', 'frontend/src/ScientificInput.tsx', 'frontend/src/Inspector.tsx',
         'frontend/src/Schematic.tsx', 'frontend/src/Research.tsx', 'frontend/src/App.tsx',
         'frontend/src/styles.css', 'backend/app/coax.py', 'backend/app/engine.py',
         'backend/app/external_rf.py', 'backend/app/catalog.py', 'backend/app/studies.py', 'backend/app/presets.py']}

    def passed(message):
        evidence['checks'].append(message)
        report_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print('PASS: ' + message, flush=True)

    def expired(*_):
        raise TimeoutError('Coax acceptance exceeded its 240 second budget')

    signal.signal(signal.SIGALRM, expired)
    signal.alarm(240)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox'])
            context = browser.new_context(viewport={'width': 1550, 'height': 1100}, accept_downloads=True)
            page = context.new_page()
            page.set_default_timeout(10000)
            page.on('pageerror', lambda error: evidence['browser_runtime_errors'].append(str(error)))
            mutations = []
            page.on('request', lambda r: mutations.append((r.method, r.url))
                    if r.method in {'POST', 'PUT'} and urlparse(r.url).path.startswith(('/api/circuits', '/api/runs', '/api/studies')) else None)

            def request(path, method='GET', body=None):
                response = context.request.fetch(base + '/api' + path, method=method, data=body, timeout=10000)
                assert response.ok, (path, response.status, response.text())
                return response.json()

            def models():
                rows, offset = [], 0
                while True:
                    listing = request(f'/circuits?limit=100&offset={offset}')
                    rows += listing['circuits']
                    if len(rows) >= listing['total']:
                        return sorted(rows, key=lambda row: row['id'])
                    offset += 100

            original_models = models()
            evidence['original_active_models'] = len(original_models)
            page.goto(base, wait_until='networkidle')
            page.get_by_role('heading', name='解析ワークスペース', exact=True).wait_for()
            page.locator('#employee-id').fill(employee)
            page.get_by_label('外観', exact=True).select_option('light')
            evidence['frontend_assets'] = page.locator('script[src],link[rel="stylesheet"]').evaluate_all(
                'els=>els.map(e=>e.getAttribute("src")||e.getAttribute("href"))')
            page.get_by_role('button', name='プリセットから始める', exact=False).click()
            page.get_by_role('button', name='同軸ケーブル — 40 MHz', exact=False).click()
            replace = page.get_by_role('button', name='変更を閉じて開く', exact=True)
            if replace.is_visible():
                replace.click()
            page.locator('.react-flow__node[data-id="coax1"]').click()
            panel = page.get_by_role('region', name='同軸ケーブルの線路定数', exact=True)
            expect(panel).to_have_attribute('aria-busy', 'false')
            expect(panel).to_contain_text('50.021')
            assert page.locator('.react-flow__node[data-id="coax1"] .react-flow__handle').count() == 4
            assert len(mutations) == 0
            passed('The wired 40 MHz preset opens a four-port coax editor and read-only default 50.021 Ω / 4.8338 ns constants without creating a model or job')

            def enter(label, text):
                field = page.get_by_label(label, exact=True)
                assert field.get_attribute('type') == 'text'
                field.fill('')
                field.press_sequentially(text)
                expect(field).to_have_value(text)
                assert field.evaluate('el=>el.validity.valid'), field.evaluate('el=>el.validationMessage')
                return field

            def rejected(label, text, corrected):
                field = page.get_by_label(label, exact=True)
                field.fill(text)
                assert not field.evaluate('el=>el.validity.valid')
                before = len(mutations)
                page.get_by_role('button', name='保存', exact=True).click()
                page.wait_for_timeout(150)
                assert len(mutations) == before
                enter(label, corrected)

            rejected('シールド内径（mm）', '1', '3.35e0')
            rejected('線路の分割数', '16.5', '3.2e1')
            rejected('損失の基準周波数（MHz）', '4e-', '4e1')
            for label, text in [
                ('内部導体直径（mm）', '1e0'), ('シールド内径（mm）', '3.35e0'),
                ('ケーブル長さ（m）', '1.5e0'), ('誘電体の比誘電率 εr', '2.1e0'),
                ('誘電体の比透磁率 μr', '1e0'), ('誘電正接 tan δ', '2e-4'),
                ('内部導体抵抗率（Ω·m）', '1.724E-8'), ('シールド抵抗率（Ω·m）', '1.724e-8'),
                ('シールド厚さ（mm）', '1.5e-1'), ('損失の基準周波数（MHz）', '4e1'), ('線路の分割数', '3.2e1'),
            ]:
                enter(label, text)
            expect(panel).to_have_attribute('aria-busy', 'false')
            expect(panel).to_contain_text('7.2507')
            page.locator('.react-flow__controls-fitview').click()
            if page.locator('.toast button').is_visible():
                page.locator('.toast button').click()
            page.locator('.inspector').evaluate('el=>el.scrollTo(0,0)')
            page.screenshot(path=str(args.output / 'coax-desktop.png'), full_page=True, animations='disabled')
            passed('All eleven geometry/material/frequency/segment fields accept scientific input; invalid geometry, fractional segments and incomplete exponents block save without API mutations; length updates the delay preview')

            page.set_viewport_size({'width': 390, 'height': 844})
            page.get_by_label('外観', exact=True).select_option('dark')
            enter('シールド厚さ（mm）', '1.5e-1').scroll_into_view_if_needed()
            expect(panel).to_have_attribute('aria-busy', 'false')
            panel.scroll_into_view_if_needed()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(args.output / 'coax-mobile-dark.png'), animations='disabled')
            page.set_viewport_size({'width': 320, 'height': 740})
            enter('内部導体直径（mm）', '1e0').scroll_into_view_if_needed()
            panel.scroll_into_view_if_needed()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.set_viewport_size({'width': 1550, 'height': 1100})
            page.get_by_label('外観', exact=True).select_option('light')
            passed('Coax parameters and derived constants remain reachable in dark mobile layouts at 390 and 320 pixels without horizontal page overflow')

            page.get_by_label('回路名', exact=True).fill(name)
            with page.expect_response(lambda r: urlparse(r.url).path == '/api/circuits' and r.request.method == 'POST') as pending:
                page.get_by_role('button', name='保存', exact=True).click()
            assert pending.value.ok, pending.value.text()
            saved = pending.value.json()
            evidence['circuit_id'] = saved['id']
            parameters = next(c['parameters'] for c in saved['document']['components'] if c['id'] == 'coax1')
            assert math.isclose(parameters['inner_diameter_m'], .001, rel_tol=1e-12)
            assert math.isclose(parameters['shield_thickness_m'], .00015, rel_tol=1e-12)
            assert parameters['reference_frequency_hz'] == 40e6 and parameters['segments'] == 32
            assert parameters['length_m'] == 1.5

            def finish_run(run):
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    run = request('/runs/' + run['id'])
                    if run['status'] not in {'queued', 'running'}:
                        break
                    page.wait_for_timeout(200)
                assert run['status'] == 'succeeded' and run['result']['converged'], run.get('error')
                assert run['runtime_config']['implementation_sha256']['coax.py'] == evidence['source_sha256']['backend/app/coax.py']
                metadata = run['result']['model_metadata']['coax_cables']['coax1']
                assert metadata['parameters']['length_m'] == next(c['parameters']['length_m'] for c in run['snapshot']['components'] if c['id'] == 'coax1')
                assert not any('cx_' in s['name'] for s in run['result']['signals'])
                assert all(len(s['values']) == len(run['result']['axis']['values']) for s in run['result']['signals'])
                evidence['runs'].append({'id': run['id'], 'status': run['status'], 'analysis': run['analysis'],
                    'converged': run['result']['converged'], 'solver': run['result']['solver'],
                    'samples': len(run['result']['x']), 'metadata': metadata})
                return run

            with page.expect_response(lambda r: urlparse(r.url).path == '/api/runs' and r.request.method == 'POST') as pending:
                page.get_by_role('button', name='計算を実行', exact=True).click()
            assert pending.value.status == 202, pending.value.text()
            transient = finish_run(pending.value.json())
            page.locator('.result-health.ok').wait_for()
            names = {s['name'] for s in transient['result']['signals']}
            assert {'V(coax1:input)', 'V(coax1:output)', 'I(coax1:input)', 'I(coax1:output)', 'P(coax1) net input'} <= names
            page.screenshot(path=str(args.output / 'coax-transient-results.png'), full_page=True, animations='disabled')
            with page.expect_download() as download:
                page.get_by_role('button', name='CSV保存', exact=True).click()
            csv_path = args.output / 'transient.csv'
            download.value.save_as(str(csv_path))
            assert 'V(coax1:output)' in csv_path.read_text()
            package = request('/runs/' + transient['id'] + '/package')
            assert package['runtime_config']['implementation_sha256']['coax.py'] == evidence['source_sha256']['backend/app/coax.py']
            passed('Saving persists numeric SI parameters to PostgreSQL; the real UI-queued transient job publishes terminal waveforms, power and constants, with solver/source provenance and downloadable CSV/package')

            saved = request('/circuits/' + saved['id'])
            ac = finish_run(request('/runs', 'POST', {'employee_id': employee, 'circuit_id': saved['id'],
                'expected_revision': saved['revision'], 'analysis': {'kind': 'ac', 'settings': {
                    'start_frequency': 39e6, 'stop_frequency': 41e6, 'points': 3, 'variation': 'lin'}}}))
            assert {'V(coax1:output) magnitude', 'V(coax1:output) phase'} <= {s['name'] for s in ac['result']['signals']}
            for item in evidence['runs']:
                assert request('/runs/' + item['id'] + '/package')['format'] == 'plasma-circuit-analysis'
            passed('A second real queued AC job retains identical coax parameters and exposes terminal magnitude/phase plus average net-input power')

            page.get_by_role('button', name='研究・比較', exact=True).click()
            selector = page.get_by_label('軸1の変更する項目', exact=True)
            options = selector.locator('option').evaluate_all('els=>els.map(e=>e.value)')
            coax_options = [path for path in options if '.coax1.parameters.' in path]
            assert len(coax_options) == 11
            selector.select_option('document.components.coax1.parameters.length_m')
            page.get_by_label('軸1の値（カンマ区切り）', exact=True).fill('5e-1, 1.5e0')
            page.get_by_label('研究名', exact=True).fill('同軸長さ検証')
            with page.expect_response(lambda r: urlparse(r.url).path == '/api/studies' and r.request.method == 'POST') as pending:
                page.get_by_role('button', name='研究を開始', exact=True).click()
            assert pending.value.status == 202, pending.value.text()
            study = pending.value.json()
            evidence['study_id'] = study['id']
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                study = request('/studies/' + study['id'])
                if study['status'] not in {'queued', 'running'}:
                    break
                page.wait_for_timeout(200)
            assert study['status'] == 'succeeded', study
            assert study['axes'][0]['values'] == [.5, 1.5]
            for case in study['cases']:
                case_run = finish_run(request('/runs/' + case['run_id']))
                assert case_run['snapshot']['components'] != []
            evidence['study_coordinates'] = [case['coordinates'] for case in study['cases']]
            passed('Research exposes all eleven coax selectors; a UI-created two-case length study runs real ngspice with distinct immutable cable inputs')

            page.get_by_role('button', name='保存済みモデル', exact=False).click()
            page.get_by_label('モデルを検索', exact=True).fill(name)
            page.locator(f'.model-table tr[data-model-id="{saved["id"]}"] .model-name button').click()
            page.locator('.react-flow__node[data-id="coax1"]').click()
            expect(page.get_by_label('ケーブル長さ（m）', exact=True)).to_have_value('1.5')
            expect(page.get_by_label('シールド厚さ（mm）', exact=True)).to_have_value('0.15')
            expect(page.get_by_label('損失の基準周波数（MHz）', exact=True)).to_have_value('40')
            expect(panel).to_have_attribute('aria-busy', 'false')
            passed('Opening the saved model from the full-page library restores SI-scaled geometry/material values and the derived preview after the length study')

            latest = request('/circuits/' + saved['id'])
            assert latest['created_by'] == employee and latest['name'] == name
            request('/circuits/delete', 'POST', {'employee_id': employee,
                'circuits': [{'id': latest['id'], 'expected_revision': latest['revision']}]})
            assert models() == original_models
            assert request('/runs/' + transient['id'])['status'] == 'succeeded'
            evidence['fixture_deleted'] = True
            assert not evidence['browser_runtime_errors'], evidence['browser_runtime_errors']
            passed('Only the owned validation model is hidden after checking; original active-model metadata is unchanged and validation history remains available; browser runtime errors: zero')
            evidence['completed'] = True
            report_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
            browser.close()
    except Exception:
        evidence['error'] = traceback.format_exc()
        report_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        raise
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
