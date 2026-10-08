#!/usr/bin/env python3
"""Live component duplication, independent edits, undo and persistence acceptance.

Requires Playwright and Chromium; uses the running app without mocks. Creates
one owned model and a real ngspice OP run, then hides only that model while
preserving its history and verifying existing active models are unchanged.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import shutil
import signal
import time
import traceback
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://localhost:8080')
    parser.add_argument('--output', type=Path, default=Path('reports/component-duplication'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip('/')
    employee = '000-duplicate-check'
    name = '部品複製検証 ' + str(time.time_ns())
    evidence = {'completed': False, 'api_mocked': False, 'solver_results_mocked': False,
                'checks': [], 'browser_runtime_errors': [], 'fixture_name': name}
    report_path = args.output / 'verification.json'
    root = Path(__file__).resolve().parents[1]
    evidence['source_sha256'] = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in
        ['frontend/src/App.tsx', 'frontend/src/Inspector.tsx', 'frontend/src/Schematic.tsx',
         'frontend/src/ScientificInput.tsx']}

    def passed(message):
        evidence['checks'].append(message)
        report_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print('PASS: ' + message, flush=True)

    def expired(*_):
        raise TimeoutError('Duplication acceptance exceeded its 240 second budget')

    signal.signal(signal.SIGALRM, expired)
    signal.alarm(240)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox'])
            context = browser.new_context(viewport={'width': 1550, 'height': 1100})
            page = context.new_page()
            page.set_default_timeout(10000)
            page.on('pageerror', lambda error: evidence['browser_runtime_errors'].append(str(error)))
            page.on('dialog', lambda dialog: dialog.accept())

            def request(path, method='GET', body=None):
                response = context.request.fetch(base + '/api' + path, method=method, data=body, timeout=10000)
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
            canvas_copy = page.get_by_role('button', name='選択部品を複製', exact=True)
            expect(canvas_copy).to_be_disabled()
            section = page.locator('details').filter(has=page.get_by_text('回路ドキュメントを編集', exact=True))
            panel = page.locator('.component-inspector')

            def document():
                return json.loads(page.get_by_label('回路JSON', exact=True).input_value())

            def expect_document(value):
                page.wait_for_function("""expected => {
                    const normalize = value => Array.isArray(value) ? value.map(normalize) :
                        value && typeof value === 'object' ? Object.fromEntries(
                            Object.keys(value).sort().map(key => [key, normalize(value[key])])) : value;
                    return JSON.stringify(normalize(JSON.parse(document.querySelector('textarea[aria-label="回路JSON"]').value))) === JSON.stringify(normalize(expected));
                }""", arg=value)

            def expect_parameter(path, value):
                page.wait_for_function("({path,value}) => JSON.stringify(path.reduce((v,k)=>v[k], JSON.parse(document.querySelector('textarea[aria-label=\"回路JSON\"]').value).components.at(-1).parameters)) === JSON.stringify(value)", arg={'path': path, 'value': value})

            def apply_document(value):
                if not section.evaluate('el=>el.open'):
                    section.locator('summary').click()
                page.get_by_label('回路JSON', exact=True).fill(json.dumps(value, ensure_ascii=False))
                section.get_by_role('button', name='適用', exact=True).click()
                expect(page.locator('.react-flow__node')).to_have_count(len(value['components']))
                expect_document(value)
                section.locator('summary').click()
                page.get_by_role('button', name='回路全体を表示', exact=True).click()

            def select(cid):
                page.get_by_role('button', name='回路全体を表示', exact=True).click()
                page.locator(f'.react-flow__node[data-id="{cid}"]').click()
                expect(panel.locator('.component-footer')).to_contain_text(cid)

            def duplicate(method='panel'):
                before = document()
                source_id = page.locator('.react-flow__node.selected').get_attribute('data-id')
                source = next(c for c in before['components'] if c['id'] == source_id)
                if method == 'panel':
                    panel.get_by_role('button', name='部品を複製', exact=True).click()
                elif method == 'canvas':
                    canvas_copy.click()
                else:
                    page.keyboard.press(method)
                expect(page.locator('.react-flow__node')).to_have_count(len(before['components']) + 1)
                after = document()
                cloned = after['components'][-1]
                assert after['components'][:-1] == before['components']
                assert all(after[key] == before[key] for key in before if key != 'components')
                assert cloned['id'] not in {c['id'] for c in before['components']}
                assert re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', cloned['id'])
                assert cloned['label'] not in {c['label'] for c in before['components']}
                assert len(cloned['label']) <= 100
                assert cloned['position'] != source['position']
                assert not any(abs(c['position']['x'] - cloned['position']['x']) < 100 and
                               abs(c['position']['y'] - cloned['position']['y']) < 120 for c in before['components'])
                assert all(cloned[key] == source[key] for key in ['kind', 'parameters', 'ports', 'rotation'])
                expect(panel.locator('.component-footer')).to_contain_text(cloned['id'])
                return before, cloned

            catalog = request('/catalog')['components']
            kinds = catalog
            cases = []
            for index, entry in enumerate(kinds):
                source = {'id': f'source_{index}', 'kind': entry['kind'], 'label': f'{entry["kind"]}{index + 1}',
                          'ports': entry['ports'], 'parameters': copy.deepcopy(entry['parameters']),
                          'position': {'x': 260, 'y': 100}, 'rotation': 90}
                if entry['kind'] == 'V':
                    source['parameters']['waveform'] = {'kind': 'pulse', 'initial': -2, 'pulsed': 7,
                        'delay': 1e-8, 'rise': 2e-9, 'fall': 3e-9, 'width': 1e-6, 'period': 2e-6}
                if entry['kind'] == 'EDD':
                    source['parameters'] = {'branches': [
                        {'positive': 'p1', 'negative': 'n1', 'current': 'V1/R', 'charge': 'C0*V1'},
                        {'positive': 'p2', 'negative': 'n1', 'current': 'V2/R', 'charge': 'C0*(V1+V2)'}],
                        'parameters': {'R': 1700, 'C0': 2e-9}, 'intermediates': {'energy': 'C0*V1*V1/2'}}
                    source['ports'] = ['p1', 'n1', 'p2']
                value = {'schema_version': 1, 'name': name, 'description': '実ブラウザで設定コピーを検証',
                         'components': [source], 'wires': [], 'models': [], 'parameters': {'scale': 1.5}}
                apply_document(value)
                select(source['id'])
                _, cloned = duplicate('canvas' if index % 2 else 'panel')
                if entry['kind'] == 'V':
                    panel.locator('summary').filter(has_text='電源波形・2周波数RF').click()
                    panel.get_by_label('周期（s）', exact=True).fill('4e-6')
                    expect_parameter(['waveform', 'period'], 4e-6)
                elif entry['kind'] == 'D' and 'model_parameters' in source['parameters']:
                    panel.get_by_label('飽和電流 IS（A）', exact=True).fill('3e-13')
                    expect_parameter(['model_parameters', 'IS'], 3e-13)
                elif entry['kind'] == 'EDD':
                    branch_current = panel.get_by_label('I1（A）', exact=True)
                    branch_current.fill('2*V1/R')
                    expect(branch_current).to_have_value('2*V1/R')
                    panel.get_by_role('button', name='枝を適用', exact=True).click()
                    expect_parameter(['branches', 0, 'current'], '2*V1/R')
                elif entry['kind'] in {'COAX', 'COAX_GND'}:
                    panel.get_by_label('ケーブル長さ（m）', exact=True).fill('2.5')
                    expect_parameter(['length_m'], 2.5)
                    assert page.locator(f'.react-flow__node[data-id="{cloned["id"]}"] .react-flow__handle').count() == len(source['ports'])
                elif entry['kind'] == 'PLASMA':
                    panel.get_by_label('ガス', exact=True).select_option('O2')
                    expect_parameter(['gas'], 'O2')
                else:
                    panel.get_by_label('部品名', exact=True).fill('個別編集済み')
                assert document()['components'][0] == source
                cases.append({'kind': entry['kind'], 'catalog_label': entry['label']})
            evidence['catalog_cases'] = cases
            passed(f'All {len(cases)} catalog variants retain settings, ports and rotation; source/diode/EDD/coax/plasma copy edits preserve originals')

            resistor = {'id': 'r1', 'kind': 'R', 'label': 'R1', 'ports': ['p', 'n'],
                        'parameters': {'value': 1000}, 'position': {'x': 260, 'y': 100}, 'rotation': 90}
            source = {'id': 'v1', 'kind': 'V', 'label': 'V1', 'ports': ['p', 'n'],
                      'parameters': {'dc': 5}, 'position': {'x': 60, 'y': 100}, 'rotation': 90}
            ground = {'id': 'gnd', 'kind': 'GND', 'label': 'GND', 'ports': ['g'],
                      'parameters': {}, 'position': {'x': 60, 'y': 300}, 'rotation': 0}

            def wire(a, ap, b, bp):
                return {'id': f'w_{a}_{ap}_{b}_{bp}', 'source': {'component_id': a, 'port': ap},
                        'target': {'component_id': b, 'port': bp}}

            value = {'schema_version': 1, 'name': name, 'description': '複製抵抗による分圧回路',
                     'components': [source, resistor, ground],
                     'wires': [wire('v1', 'p', 'r1', 'p'), wire('v1', 'n', 'gnd', 'g')],
                     'models': [{'name': 'DDEFAULT', 'definition': '.model DDEFAULT D(IS=1e-14 N=1)'}],
                     'parameters': {'scale': 1.5}}
            apply_document(value)
            select('r1')
            before, cloned = duplicate('Control+d')
            copied_document = document()
            page.get_by_role('button', name='元に戻す', exact=True).click()
            expect_document(before)
            page.get_by_role('button', name='やり直す', exact=True).click()
            expect_document(copied_document)
            expect(canvas_copy).to_be_disabled()
            select(cloned['id'])
            duplicate('Meta+d')
            expect(panel.get_by_label('部品名', exact=True)).to_have_value('R1（コピー 2）')
            page.get_by_role('button', name='元に戻す', exact=True).click()
            expect_document(copied_document)
            select(cloned['id'])
            field = panel.get_by_label('値（SI）', exact=True)
            field.fill('1e-')
            canvas_copy.click()
            assert document() == copied_document
            expect(field).to_have_value('1e-')
            expect(field).to_be_focused()
            assert field.evaluate('el=>!el.validity.valid')
            field.press('Control+d')
            assert document() == copied_document
            field.fill('2e3')
            expect_parameter(['value'], 2000)
            assert document()['components'][1]['parameters']['value'] == 1000
            assert document()['components'][-1]['parameters']['value'] == 2000
            page.get_by_role('button', name='プリセットから始める', exact=False).click()
            page.get_by_role('button', name='ダイアログを閉じる', exact=True).focus()
            page.keyboard.press('Control+d')
            assert len(document()['components']) == 4
            page.get_by_role('button', name='ダイアログを閉じる', exact=True).click()
            page.get_by_role('button', name='計算結果', exact=False).click()
            page.keyboard.press('Control+d')
            assert len(document()['components']) == 4
            page.get_by_role('button', name='回路エディタ', exact=True).click()
            passed('Ctrl+D / Cmd+D produce unique IDs/labels; duplication is one undo step; redo restores exact data; invalid numeric drafts and editable/modal/results contexts are protected')

            # Labels near the server limit and repeated duplication stay valid.
            edge_case = copy.deepcopy(value)
            edge_case['components'][1]['label'] = '抵' * 99 + '⚡'
            apply_document(edge_case)
            select('r1')
            duplicate()
            duplicate()
            assert len({c['label'] for c in document()['components']}) == 5
            assert all(len(c['label']) <= 100 for c in document()['components'])
            full = copy.deepcopy(value)
            full['components'] = [{**copy.deepcopy(resistor), 'id': f'r_{i}', 'label': f'R{i}',
                'position': {'x': (i % 25) * 120, 'y': (i // 25) * 120}} for i in range(500)]
            full['wires'] = []
            apply_document(full)
            select('r_0')
            expect(canvas_copy).to_be_disabled()
            expect(panel.get_by_role('button', name='部品を複製', exact=True)).to_be_disabled()
            page.locator('.react-flow__node[data-id="r_0"]').focus()
            page.keyboard.press('Control+d')
            assert len(document()['components']) == 500
            passed('100-character Unicode labels remain bounded and unique; both buttons and keyboard enforce the 500-component document limit')

            final_doc = copy.deepcopy(copied_document)
            final_doc['components'][-1]['parameters']['value'] = 2000
            final_doc['wires'] += [wire('r1', 'n', cloned['id'], 'p'), wire(cloned['id'], 'n', 'gnd', 'g')]
            apply_document(final_doc)
            select(cloned['id'])
            page.get_by_label('解析方法', exact=True).select_option('op')
            close = page.get_by_role('button', name='通知を閉じる', exact=True)
            if close.is_visible():
                close.click()
            page.screenshot(path=str(args.output / 'duplication-desktop.png'), full_page=True, animations='disabled')
            page.set_viewport_size({'width': 390, 'height': 844})
            select(cloned['id'])
            panel.get_by_role('button', name='部品を複製', exact=True).scroll_into_view_if_needed()
            duplicate()
            page.get_by_role('button', name='元に戻す', exact=True).click()
            expect_document(final_doc)
            select(cloned['id'])
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(args.output / 'duplication-mobile.png'), full_page=True, animations='disabled')
            page.set_viewport_size({'width': 1550, 'height': 1100})
            passed('Desktop and 390px mobile expose the duplicate action; mobile copy/undo works without page overflow')

            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/circuits')) as saved_response:
                page.get_by_role('button', name='保存', exact=True).click()
            saved = saved_response.value.json()
            assert saved_response.value.ok and saved['document'] == final_doc
            evidence['fixture_id'] = saved['id']
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/runs')) as run_response:
                page.get_by_role('button', name='計算を実行', exact=True).click()
            run = run_response.value.json()
            assert run_response.value.ok
            evidence['run_id'] = run['id']
            deadline = time.monotonic() + 60
            while run['status'] in {'queued', 'running'} and time.monotonic() < deadline:
                page.wait_for_timeout(250)
                run = request('/runs/' + run['id'])
            assert run['status'] == 'succeeded', run.get('error')
            assert run['snapshot'] == final_doc
            result = run['result']
            net = result['diagnostics']['node_map'][cloned['id'] + '.p']
            voltage = result['summary']['V(' + net + ')']
            assert math.isclose(voltage, 10 / 3, rel_tol=1e-9), voltage
            assert {'r1', cloned['id']} <= {line.split()[0] for line in result['netlist'].splitlines() if line.strip()}
            evidence['run'] = {'id': run['id'], 'status': run['status'], 'solver': result['solver'],
                               'divider_voltage_v': voltage, 'expected_voltage_v': 10 / 3}
            page.get_by_role('button', name='保存済みモデル', exact=False).click()
            page.get_by_label('モデルを検索', exact=True).fill(name)
            page.locator(f'.model-table tr[data-model-id="{saved["id"]}"] .model-name button').click()
            expect_document(final_doc)
            select(cloned['id'])
            expect(panel.get_by_label('値（SI）', exact=True)).to_have_value('2000')
            passed('A real saved duplicate resistor and original become distinct ngspice devices; 5V/1kΩ/2kΩ gives 3.333333333V; saved model reload retains settings, IDs and connections')

            latest = request('/circuits/' + saved['id'])
            assert latest['created_by'] == employee and latest['name'] == name
            request('/circuits/delete', 'POST', {'employee_id': employee,
                'circuits': [{'id': latest['id'], 'expected_revision': latest['revision']}]})
            evidence['fixture_deleted'] = True
            assert models() == original_models
            assert request('/runs/' + run['id'])['status'] == 'succeeded'
            assert not evidence['browser_runtime_errors'], evidence['browser_runtime_errors']
            passed('Only the owned validation model is hidden; existing active-model metadata is unchanged, run history remains available, and browser runtime errors are zero')
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
