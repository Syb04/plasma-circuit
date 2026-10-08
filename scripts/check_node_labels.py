#!/usr/bin/env python3
"""Live node selection, label scope, real plots and immutable run acceptance.

Requires Playwright/Chromium. Creates one owned model and two ngspice runs;
soft-deletes only that model on success. No solver or API responses are mocked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import signal
import time
import traceback
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://localhost:8080')
    parser.add_argument('--output', type=Path, default=Path('reports/node-labels'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base, employee = args.base_url.rstrip('/'), '000-node-check'
    name = 'ノード名検証 ' + str(time.time_ns())
    evidence = {'completed': False, 'api_mocked': False, 'solver_results_mocked': False,
                'checks': [], 'browser_runtime_errors': [], 'fixture_name': name, 'runs': []}
    report = args.output / 'verification.json'
    root = Path(__file__).resolve().parents[1]
    evidence['source_sha256'] = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in
        ['frontend/src/App.tsx', 'frontend/src/Inspector.tsx', 'frontend/src/NodeLabels.tsx',
         'frontend/src/Schematic.tsx', 'frontend/src/Results.tsx', 'frontend/src/Research.tsx',
         'frontend/src/types.ts', 'frontend/src/styles.css', 'backend/app/node_labels.py',
         'backend/app/schemas.py', 'backend/app/engine.py', 'backend/app/external_rf.py', 'backend/app/api.py']}

    def passed(message):
        evidence['checks'].append(message)
        report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print('PASS: ' + message, flush=True)

    def expired(*_):
        raise TimeoutError('Node label acceptance exceeded its 240 second budget')

    signal.signal(signal.SIGALRM, expired)
    signal.alarm(240)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox'])
            context = browser.new_context(viewport={'width': 1550, 'height': 1100}, accept_downloads=True)
            page = context.new_page()
            page.set_default_timeout(10000)
            page.on('pageerror', lambda error: evidence['browser_runtime_errors'].append(str(error)))
            page.on('dialog', lambda dialog: dialog.accept())

            def request(path, method='GET', body=None):
                response = context.request.fetch(base + '/api' + path, method=method, data=body)
                assert response.ok, (path, response.status, response.text())
                return response.json()

            def models():
                rows = []
                while True:
                    listing = request(f'/circuits?limit=100&offset={len(rows)}')
                    rows += listing['circuits']
                    if len(rows) >= listing['total']:
                        return sorted(rows, key=lambda row: row['id'])

            original_models = models()
            evidence['original_active_models'] = len(original_models)
            page.goto(base, wait_until='networkidle')
            page.locator('#employee-id').fill(employee)
            page.get_by_label('外観', exact=True).select_option('light')
            evidence['frontend_assets'] = page.locator('script[src],link[rel="stylesheet"]').evaluate_all(
                'els=>els.map(e=>e.getAttribute("src")||e.getAttribute("href"))')
            doc_section = page.locator('details').filter(has=page.get_by_text('回路ドキュメントを編集', exact=True))
            editor = page.get_by_role('region', name='ノード名の設定', exact=True)

            def document():
                return json.loads(page.get_by_label('回路JSON', exact=True).input_value())

            def apply_json(label, value):
                area = page.get_by_label(label, exact=True)
                details = area.locator('xpath=ancestor::details[1]')
                if not details.evaluate('el=>el.open'):
                    details.locator('summary').click()
                area.fill(json.dumps(value, ensure_ascii=False))
                details.get_by_role('button', name='適用', exact=True).click()
                page.wait_for_function("({label,expected})=>JSON.stringify(JSON.parse(document.querySelector('textarea[aria-label=\"'+label+'\"]').value))===JSON.stringify(expected)", arg={'label': label, 'expected': value})
                details.locator('summary').click()

            def choose_port(cid, port):
                page.get_by_role('button', name='回路全体を表示', exact=True).click()
                page.locator(f'.react-flow__node[data-id="{cid}"] [data-node-port="{port}"]').click()
                expect(editor.get_by_label('ノードの端子', exact=True)).to_have_value(port)

            def assign(cid, port, value):
                choose_port(cid, port)
                editor.get_by_label('ノード名', exact=True).fill(value)
                editor.get_by_role('button', name='ノード名を適用', exact=True).click()
                page.wait_for_function("name=>JSON.parse(document.querySelector('textarea[aria-label=\"回路JSON\"]').value).node_labels?.some(label=>label.name===name) ?? false", arg=value.strip())
                expect(editor.get_by_label('ノード名', exact=True)).to_have_value(value.strip())

            def wire(a, ap, b, bp):
                return {'id': f'w_{a}_{ap}_{b}_{bp}', 'source': {'component_id': a, 'port': ap}, 'target': {'component_id': b, 'port': bp}}

            doc = {'schema_version': 1, 'name': name, 'description': 'ノード名・凡例・履歴検証',
                'components': [
                    {'id': 'v1', 'kind': 'V', 'label': 'V1', 'ports': ['p', 'n'], 'parameters': {'dc': 0, 'ac_magnitude': 1,
                        'waveform': {'kind': 'sin', 'offset': 0, 'amplitude': 2, 'frequency': 1000}}, 'position': {'x': 80, 'y': 100}, 'rotation': 90},
                    {'id': 'r1', 'kind': 'R', 'label': 'R1', 'ports': ['p', 'n'], 'parameters': {'value': 1000}, 'position': {'x': 300, 'y': 80}, 'rotation': 0},
                    {'id': 'r2', 'kind': 'R', 'label': 'R2', 'ports': ['p', 'n'], 'parameters': {'value': 2000}, 'position': {'x': 500, 'y': 240}, 'rotation': 90},
                    {'id': 'gnd', 'kind': 'GND', 'label': 'GND', 'ports': ['g'], 'parameters': {}, 'position': {'x': 100, 'y': 340}, 'rotation': 0}],
                'wires': [wire('v1', 'p', 'r1', 'p'), wire('r1', 'n', 'r2', 'p'), wire('v1', 'n', 'gnd', 'g'), wire('r2', 'n', 'gnd', 'g')],
                'models': [], 'parameters': {}}
            apply_json('回路JSON', doc)
            assign('r1', 'n', '  電極  ')
            expect(page.locator('.react-flow__node[data-id="r2"] [data-node-port="p"]')).to_contain_text('電極')
            choose_port('r2', 'p')
            expect(editor.get_by_label('ノード名', exact=True)).to_have_value('電極')
            editor.get_by_label('ノード名', exact=True).fill('負荷側')
            editor.get_by_role('button', name='ノード名を適用', exact=True).click()
            expect(page.locator('.react-flow__node[data-id="r1"] [data-node-port="n"]')).to_contain_text('負荷側')
            page.get_by_role('button', name='元に戻す', exact=True).click()
            expect(page.locator('.react-flow__node[data-id="r1"] [data-node-port="n"]')).to_contain_text('電極')
            page.get_by_role('button', name='やり直す', exact=True).click()
            expect(page.locator('.react-flow__node[data-id="r1"] [data-node-port="n"]')).to_contain_text('負荷側')
            page.get_by_role('button', name='元に戻す', exact=True).click()
            choose_port('r1', 'n')
            editor.get_by_role('button', name='ノード名を解除', exact=True).click()
            expect(page.locator('.react-flow__node[data-id="r2"] [data-node-port="p"]')).to_have_text('p')
            page.get_by_role('button', name='元に戻す', exact=True).click()
            passed('Clicking terminal names selects electrical nodes; connected terminals share a name; rename/clear and undo/redo update the whole node')

            assign('v1', 'p', 'RF入力')
            before = document()
            editor.get_by_label('ノード名', exact=True).fill('電極')
            editor.get_by_role('button', name='ノード名を適用', exact=True).click()
            expect(editor.get_by_role('alert')).to_contain_text('別のノード')
            assert document() == before
            editor.get_by_label('ノード名', exact=True).fill('RF入力')
            editor.get_by_label('ノード名', exact=True).press('Enter')
            assign('gnd', 'g', '接地')
            expect(page.locator('.react-flow__node[data-id="v1"] [data-node-port="n"]')).to_contain_text('接地')
            edge = page.locator('.react-flow__edge[data-id="w_v1_p_r1_p"] .react-flow__edge-interaction')
            point = edge.evaluate('el=>{const p=el.getPointAtLength(el.getTotalLength()/2).matrixTransform(el.getScreenCTM());return {x:p.x,y:p.y}}')
            page.mouse.click(point['x'], point['y'])
            expect(editor.get_by_label('ノード名', exact=True)).to_have_value('RF入力')
            passed('Wire selection opens the correct node; ground terminals share their label; assigning the same name to a different node is rejected without modifying the document')

            def drag_wire(a, ap, b, bp):
                points = [page.locator(f'.react-flow__node[data-id="{cid}"] .react-flow__handle[data-handleid="{port}"]').bounding_box() for cid, port in [(a, ap), (b, bp)]]
                start, end = points
                page.mouse.move(start['x'] + start['width'] / 2, start['y'] + start['height'] / 2)
                page.mouse.down()
                page.mouse.move(end['x'] + end['width'] / 2, end['y'] + end['height'] / 2, steps=20)
                page.mouse.up()

            before = document()
            drag_wire('r1', 'p', 'r2', 'p')
            expect(page.locator('.toast')).to_contain_text('異なる名前')
            assert document() == before
            choose_port('v1', 'p')
            editor.get_by_role('button', name='ノード名を解除', exact=True).click()
            expect(page.locator('.react-flow__node[data-id="v1"] [data-node-port="p"]')).to_have_text('p')
            drag_wire('r1', 'p', 'r2', 'p')
            expect(page.locator('.react-flow__edge')).to_have_count(len(before['wires']) + 1)
            page.get_by_role('button', name='元に戻す', exact=True).click()
            expect(page.locator('.react-flow__edge')).to_have_count(len(before['wires']))
            page.get_by_role('button', name='元に戻す', exact=True).click()
            expect(page.locator('.react-flow__node[data-id="v1"] [data-node-port="p"]')).to_contain_text('RF入力')
            page.get_by_role('button', name='通知を閉じる', exact=True).click()
            passed('Actual terminal dragging rejects a connection between differently named nodes; clearing one name permits wiring; undo restores both the original wiring and names')

            page.get_by_label('解析方法', exact=True).select_option('transient')
            apply_json('settings', {'time_step': 1e-5, 'stop_time': .002})
            choose_port('r1', 'n')
            page.screenshot(path=str(args.output / 'labels-editor.png'), full_page=True, animations='disabled')
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/circuits')) as response:
                page.get_by_role('button', name='保存', exact=True).click()
            saved = response.value.json()
            assert response.value.ok
            evidence['fixture_id'] = saved['id']

            def run_current():
                with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/runs')) as response:
                    page.get_by_role('button', name='計算を実行', exact=True).click()
                result = response.value.json()
                assert response.value.ok
                deadline = time.monotonic() + 60
                while result['status'] in {'queued', 'running'} and time.monotonic() < deadline:
                    page.wait_for_timeout(250)
                    result = request('/runs/' + result['id'])
                assert result['status'] == 'succeeded', result.get('error')
                evidence['runs'].append({'id': result['id'], 'kind': result['analysis']['kind'], 'status': result['status'], 'solver': result['result']['solver']})
                return result

            transient = run_current()
            result = transient['result']
            nets = result['diagnostics']['node_map']
            traces = {s['name']: s for s in result['signals']}
            original_in, original_out = f'V({nets["v1.p"]})', f'V({nets["r1.n"]})'
            assert traces[original_out]['display_name'] == 'V(電極)'
            assert max(abs(y - x * 2 / 3) for x, y in zip(traces[original_in]['values'], traces[original_out]['values'])) < 1e-10
            assert not any(traces['V(0)']['values'])
            trace = page.locator('.trace-list').get_by_role('button', name='V(電極)', exact=True)
            expect(trace).to_be_visible(timeout=15000)
            expect(trace).to_have_attribute('title', original_out)
            trace.click()
            expect(trace).to_have_attribute('aria-pressed', 'false')
            trace.click()
            page.get_by_role('button', name='X–Y / Q–V', exact=True).click()
            page.get_by_label('X軸の信号', exact=True).select_option(original_in)
            page.get_by_label('Y軸の信号', exact=True).select_option(original_out)
            expect(page.locator('.chart-area')).to_contain_text('V(RF入力)')
            expect(page.locator('.chart-area')).to_contain_text('V(電極)')
            page.get_by_role('button', name='波形', exact=True).click()
            chart = page.locator('.chart-area').bounding_box()
            page.mouse.move(chart['x'] + chart['width'] * .5, chart['y'] + chart['height'] * .5)
            expect(page.locator('.recharts-tooltip-wrapper')).to_contain_text('V(電極)')
            page.mouse.move(10, 10)
            page.screenshot(path=str(args.output / 'labels-waveform.png'), full_page=True, animations='disabled')
            csv = context.request.get(base + '/api/runs/' + transient['id'] + '/export.csv')
            assert csv.ok and 'V(電極) [V]' in csv.text() and 'V(RF入力) [V]' in csv.text()
            passed('Real transient solver returns the expected 2/3 divider ratio and zero ground trace; named legend toggles, tooltips, X–Y axes and CSV work while raw signal IDs remain stable')

            page.get_by_role('button', name='回路エディタ', exact=True).click()
            assign('r1', 'n', 'Vout')
            with page.expect_response(lambda r: r.request.method == 'PUT' and r.url.endswith('/api/circuits/' + saved['id'])) as response:
                page.get_by_role('button', name='保存', exact=True).click()
            assert response.value.ok
            page.get_by_role('button', name='計算結果', exact=False).click()
            expect(page.locator('.trace-list').get_by_role('button', name='V(電極)', exact=True)).to_be_visible()
            assert page.locator('.trace-list').get_by_role('button', name='V(Vout)', exact=True).count() == 0
            page.get_by_role('button', name='保存済みモデル', exact=False).click()
            page.get_by_label('モデルを検索', exact=True).fill(name)
            page.locator(f'.model-table tr[data-model-id="{saved["id"]}"] .model-name button').click()
            choose_port('r2', 'p')
            expect(editor.get_by_label('ノード名', exact=True)).to_have_value('Vout')
            page.locator('.recent-run').first.click()
            expect(page.locator('.trace-list').get_by_role('button', name='V(電極)', exact=True)).to_be_visible()
            historical = request('/runs/' + transient['id'])
            assert historical['snapshot']['node_labels'] == transient['snapshot']['node_labels']
            assert historical['result'] == transient['result']
            passed('Saving a new node name and reopening the model restores it on all connected terminals; the existing waveform and reopened historical run keep the name used at calculation time')

            page.get_by_role('button', name='回路エディタ', exact=True).click()
            page.get_by_label('解析方法', exact=True).select_option('ac')
            apply_json('settings', {'start_frequency': 100, 'stop_frequency': 10000, 'points': 3, 'variation': 'lin'})
            ac = run_current()
            expect(page.locator('.trace-list').get_by_role('button', name='V(Vout) magnitude', exact=True)).to_be_visible(timeout=15000)
            page.get_by_label('表示する信号単位', exact=True).select_option('deg')
            expect(page.locator('.trace-list').get_by_role('button', name='V(Vout) phase', exact=True)).to_be_visible()
            passed('Real AC magnitude and phase traces use the saved node name; transient and AC histories retain their own labels')

            page.get_by_role('button', name='回路エディタ', exact=True).click()
            page.set_viewport_size({'width': 390, 'height': 844})
            choose_port('r2', 'p')
            expect(editor.get_by_label('ノード名', exact=True)).to_have_value('Vout')
            editor.scroll_into_view_if_needed()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(args.output / 'labels-mobile.png'), full_page=True, animations='disabled')
            passed('At 390px, terminal selection reaches the editable node name and apply/clear actions without page overflow')

            latest = request('/circuits/' + saved['id'])
            assert latest['name'] == name and latest['created_by'] == employee
            request('/circuits/delete', 'POST', {'employee_id': employee, 'circuits': [{'id': saved['id'], 'expected_revision': latest['revision']}]})
            assert models() == original_models
            assert request('/runs/' + ac['id'])['status'] == 'succeeded'
            evidence['fixture_deleted'] = True
            assert not evidence['browser_runtime_errors'], evidence['browser_runtime_errors']
            passed('Only the owned fixture is hidden; existing active models are unchanged, historical runs remain available, and browser runtime errors are zero')
            evidence['completed'] = True
            report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
            browser.close()
    except Exception:
        evidence['error'] = traceback.format_exc()
        report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        raise
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
