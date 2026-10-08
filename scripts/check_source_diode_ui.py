#!/usr/bin/env python3
"""Live exponent-entry, per-device diode and persistence acceptance.

Creates one validation circuit and two real ngspice jobs in the running app.
Requires Playwright and Chromium. No API/solver responses are mocked.
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
    parser.add_argument('--output', type=Path, default=Path('reports/source-diode'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip('/')
    evidence = {'completed': False, 'api_mocked': False, 'solver_results_mocked': False,
                'checks': [], 'browser_runtime_errors': [], 'runs': []}
    report_path = args.output / 'verification.json'
    root = Path(__file__).resolve().parents[1]
    evidence['source_sha256'] = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in
        ['frontend/src/ScientificInput.tsx', 'frontend/src/DiodeEditor.tsx', 'frontend/src/Inspector.tsx',
         'frontend/src/TimeInput.tsx', 'frontend/src/PlasmaControls.tsx', 'frontend/src/App.tsx',
         'frontend/src/Schematic.tsx', 'frontend/src/styles.css', 'backend/app/diode.py',
         'backend/app/catalog.py', 'backend/app/engine.py']}

    def passed(message):
        evidence['checks'].append(message)
        report_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print('PASS: ' + message, flush=True)

    def expired(*_):
        raise TimeoutError('Source/diode acceptance exceeded its 240 second budget')

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
            mutations = []
            page.on('request', lambda request: mutations.append((request.method, request.url))
                    if request.method in {'POST', 'PUT'} else None)
            page.goto(base, wait_until='networkidle')
            page.get_by_role('heading', name='解析ワークスペース', exact=True).wait_for()
            page.locator('#employee-id').fill('000-diode-check')
            page.get_by_label('外観', exact=True).select_option('light')
            evidence['frontend_assets'] = page.locator('script[src],link[rel="stylesheet"]').evaluate_all(
                'els=>els.map(e=>e.getAttribute("src")||e.getAttribute("href"))')

            def get(path):
                response = context.request.get(base + '/api' + path)
                assert response.ok, (path, response.status, response.text())
                return response.json()

            def input_value(label, text):
                field = page.get_by_label(label, exact=True)
                assert field.get_attribute('type') == 'text'
                field.fill('')
                field.press_sequentially(text)
                expect(field).to_have_value(text)
                assert field.evaluate('el=>el.validity.valid'), field.evaluate('el=>el.validationMessage')
                return field

            doc_section = page.locator('details').filter(has=page.get_by_text('回路ドキュメントを編集', exact=True))

            def apply_document(document):
                if not doc_section.evaluate('el=>el.open'):
                    doc_section.locator('summary').click()
                page.get_by_label('回路JSON', exact=True).fill(json.dumps(document, ensure_ascii=False))
                doc_section.get_by_role('button', name='適用', exact=True).click()
                expect(page.get_by_label('回路名', exact=True)).to_have_value(document['name'])
                doc_section.locator('summary').click()

            name = '指数・詳細ダイオード検証 ' + str(time.time_ns())
            document = {'schema_version': 1, 'name': name, 'description': '実ブラウザ・ngspice検証',
                'components': [
                    {'id': 'v1', 'kind': 'V', 'label': 'V1', 'ports': ['p', 'n'],
                     'parameters': {'dc': 0, 'ac_magnitude': 1, 'ac_phase': 0}, 'position': {'x': 80, 'y': 100}, 'rotation': 90},
                    {'id': 'r1', 'kind': 'R', 'label': 'R1', 'ports': ['p', 'n'],
                     'parameters': {'value': 1000}, 'position': {'x': 300, 'y': 60}, 'rotation': 0},
                    {'id': 'r2', 'kind': 'R', 'label': 'R2', 'ports': ['p', 'n'],
                     'parameters': {'value': 100000}, 'position': {'x': 500, 'y': 210}, 'rotation': 90},
                    {'id': 'gnd', 'kind': 'GND', 'label': 'GND', 'ports': ['g'],
                     'parameters': {}, 'position': {'x': 300, 'y': 390}, 'rotation': 0},
                ], 'wires': [], 'models': [], 'parameters': {}}

            def wire(a, ap, b, bp):
                return {'id': f'w_{a}_{ap}_{b}_{bp}', 'source': {'component_id': a, 'port': ap},
                        'target': {'component_id': b, 'port': bp}}

            document['wires'] = [wire('v1', 'p', 'r1', 'p'), wire('r1', 'n', 'r2', 'p'),
                                  wire('v1', 'n', 'gnd', 'g'), wire('r2', 'n', 'gnd', 'g')]
            apply_document(document)
            page.get_by_title('ダイオード（詳細設定）を追加', exact=True).click()
            expect(page.get_by_label('飽和電流 IS（A）', exact=True)).to_have_value('1e-14')
            expect(page.get_by_label('逆ブレーク電圧 BV（V）', exact=True)).to_have_value('')
            document = json.loads(page.get_by_label('回路JSON', exact=True).input_value())
            diode = next(c for c in document['components'] if c['kind'] == 'D')
            diode_id = diode['id']
            assert diode['parameters']['model_parameters'] == {'IS': 1e-14, 'N': 1, 'RS': 0.1, 'CJO': 1e-12}
            assert 'model' not in diode['parameters']
            diode['position'] = {'x': 500, 'y': 100}
            diode['rotation'] = 90
            document['wires'] += [wire('r1', 'n', diode_id, 'p'), wire(diode_id, 'n', 'gnd', 'g')]
            apply_document(document)
            page.locator('.react-flow__controls-fitview').click()
            passed('The additional diode palette variant uses native D ports, independent numeric model parameters and an initially unspecified BV')

            page.locator(f'.react-flow__node[data-id="{diode_id}"]').click()
            for label, text in [('飽和電流 IS（A）', '1e-12'), ('理想係数 N', '1.5e0'),
                                ('直列抵抗 RS（Ω）', '5e-1'), ('逆ブレーク電圧 BV（V）', '7.5e1'),
                                ('ブレーク時電流 IBV（A）', '1e-3')]:
                input_value(label, text)
            page.get_by_text('接合容量・走行時間・温度依存', exact=True).click()
            for label, text in [('ゼロバイアス接合容量 CJO（F）', '1e-11'), ('接合電位 VJ（V）', '7e-1'),
                                ('接合傾斜係数 M', '5e-1'), ('走行時間 TT（s）', '1e-8'),
                                ('エネルギーギャップ EG（eV）', '1.11e0'), ('飽和電流の温度指数 XTI', '3e0'),
                                ('順方向接合容量係数 FC', '5e-1'), ('モデル基準温度 TNOM（°C）', '2.7e1')]:
                input_value(label, text)
            page.locator('.inspector').evaluate('el=>el.scrollTo(0,0)')
            page.screenshot(path=str(args.output / 'diode-desktop.png'), full_page=True, animations='disabled')
            page.set_viewport_size({'width': 390, 'height': 844})
            page.get_by_label('外観', exact=True).select_option('dark')
            page.get_by_label('理想係数 N', exact=True).scroll_into_view_if_needed()
            page.screenshot(path=str(args.output / 'diode-mobile-dark.png'), full_page=True, animations='disabled')
            page.set_viewport_size({'width': 1550, 'height': 1100})
            page.get_by_label('外観', exact=True).select_option('light')
            passed('All 13 diode model fields accept character-by-character scientific notation, including optional capacitance/transit/temperature fields, in light/dark desktop/mobile layouts')

            page.get_by_title('ダイオードを追加', exact=True).click()
            expect(page.get_by_label('モデル名', exact=True)).to_have_value('DDEFAULT')
            input_value('面積倍率', '1e0')
            page.get_by_role('button', name='部品を削除', exact=True).click()
            page.locator('.react-flow__node[data-id="r1"]').click()
            input_value('値（SI）', '1e3')
            page.locator('.react-flow__node[data-id="v1"]').click()
            input_value('DC値', '-2.5E-1')
            page.get_by_text('電源波形・2周波数RF', exact=True).click()
            page.get_by_label('波形', exact=True).select_option('sin')
            frequency = page.get_by_label('周波数（Hz）', exact=True)
            frequency.fill('')
            frequency.press_sequentially('4e')
            expect(frequency).to_have_value('4e')
            assert not frequency.evaluate('el=>el.validity.valid')
            frequency.press_sequentially('+7')
            expect(frequency).to_have_value('4e+7')
            assert frequency.evaluate('el=>el.validity.valid')
            input_value('オフセット（V）', '0e0')
            input_value('ピーク振幅（V）', '2.5e2')
            page.get_by_label('出力時間刻みの単位', exact=True).select_option('ns')
            input_value('出力時間刻み', '1e0')
            page.get_by_label('終了時間の単位', exact=True).select_option('ns')
            input_value('終了時間', '2e2')
            settings_section = page.locator('details').filter(has=page.get_by_text('詳細な解析条件', exact=True))
            settings_section.locator('summary').click()
            settings = json.loads(page.get_by_label('settings', exact=True).input_value())
            settings['max_step'] = 1e-9
            page.get_by_label('settings', exact=True).fill(json.dumps(settings))
            settings_section.get_by_role('button', name='適用', exact=True).click()
            settings_section.locator('summary').click()
            page.screenshot(path=str(args.output / 'source-sine-desktop.png'), full_page=True, animations='disabled')
            passed('Sine frequency keeps incomplete 4e and completes 4e+7; signed uppercase exponents work in scalar component fields, and scientific time input retains SI scaling')

            def save():
                with page.expect_response(lambda r: urlparse(r.url).path.startswith('/api/circuits') and r.request.method in {'POST', 'PUT'}) as pending:
                    page.get_by_role('button', name='保存', exact=True).click()
                assert pending.value.ok, pending.value.text()
                return pending.value.json()

            def start_run():
                with page.expect_response(lambda r: urlparse(r.url).path == '/api/runs' and r.request.method == 'POST') as pending:
                    page.get_by_role('button', name='計算を実行', exact=True).click()
                assert pending.value.status == 202, pending.value.text()
                result = pending.value.json()
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    result = get('/runs/' + result['id'])
                    if result['status'] not in {'queued', 'running'}:
                        break
                    page.wait_for_timeout(200)
                assert result['status'] == 'succeeded', result.get('error')
                assert result['result']['converged']
                assert 'diode.py' in result['runtime_config']['implementation_sha256']
                page.locator('.result-health.ok').wait_for()
                evidence['runs'].append({'id': result['id'], 'status': result['status'],
                    'analysis': result['analysis'], 'solver': result['result']['solver'],
                    'samples': len(result['result']['x']), 'converged': result['result']['converged'],
                    'netlist': result['result']['netlist']})
                return result

            saved = save()
            evidence['circuit_id'] = saved['id']
            source = next(c for c in saved['document']['components'] if c['id'] == 'v1')
            assert source['parameters']['waveform']['frequency'] == 40e6
            assert source['parameters']['waveform']['amplitude'] == 250
            assert source['parameters']['dc'] == -0.25
            saved_diode = next(c for c in saved['document']['components'] if c['id'] == diode_id)
            assert len(saved_diode['parameters']['model_parameters']) == 13
            sine_run = start_run()
            assert math.isclose(sine_run['analysis']['settings']['time_step'], 1e-9, rel_tol=1e-12)
            assert math.isclose(sine_run['analysis']['settings']['stop_time'], 2e-7, rel_tol=1e-12)
            assert sine_run['snapshot']['components'] == saved['document']['components']
            assert 'SIN(0 250 40000000' in sine_run['result']['netlist']
            assert any('dc value used for op' in line for line in sine_run['result']['logs'])
            for parameter in ['IS=1e-12', 'N=1.5', 'RS=0.5', 'BV=75', 'IBV=0.001']:
                assert parameter in sine_run['result']['netlist']
            page.screenshot(path=str(args.output / 'sine-results.png'), full_page=True, animations='disabled')
            passed('Saving emits numeric SI values to PostgreSQL; the real queued 40 MHz sine/diode run retains all 13 parameters, a reproducible netlist, analysis snapshot and benign DC-initialization notice')

            page.get_by_role('button', name='回路エディタ', exact=True).click()
            page.locator('.react-flow__node[data-id="v1"]').click()
            input_value('DC値', '5e0')
            page.get_by_text('電源波形・2周波数RF', exact=True).click()
            page.get_by_label('波形', exact=True).select_option('pulse')
            rise = page.get_by_label('立上り（s）', exact=True)
            rise.fill('')
            rise.press_sequentially('1e-')
            expect(rise).to_have_value('1e-')
            before = len(mutations)
            page.get_by_role('button', name='保存', exact=True).click()
            page.wait_for_timeout(150)
            assert len(mutations) == before
            expect(rise).to_have_attribute('aria-invalid', 'true')
            expect(page.locator('.toast')).to_contain_text('有限の数値')
            for invalid in ['1e309', 'Infinity', 'NaN', '0x10', '1e-999']:
                rise.fill(invalid)
                before = len(mutations)
                page.get_by_role('button', name='計算を実行', exact=True).click()
                page.wait_for_timeout(50)
                assert len(mutations) == before
                assert not rise.evaluate('el=>el.validity.valid')
            input_value('立上り（s）', '1e-9')
            for label, text in [('立下り（s）', '2E-9'), ('遅延（s）', '1e-7'),
                                ('ON幅（s）', '8e-7'), ('周期（s）', '2e-6'), ('ON値', '2.5e0')]:
                input_value(label, text)
            page.get_by_label('終了時間の単位', exact=True).select_option('us')
            input_value('終了時間', '6e0')
            page.screenshot(path=str(args.output / 'source-pulse-desktop.png'), full_page=True, animations='disabled')
            passed('An unfinished negative exponent remains editable; incomplete, nonfinite, hexadecimal and underflow drafts block save/run without API mutations; pulse timing fields accept signed exponents')

            pulse_run = start_run()
            source = next(c for c in pulse_run['snapshot']['components'] if c['id'] == 'v1')
            assert source['parameters']['waveform'] == {'kind': 'pulse', 'initial': 0, 'pulsed': 2.5,
                'delay': 1e-7, 'rise': 1e-9, 'fall': 2e-9, 'width': 8e-7, 'period': 2e-6}
            assert math.isclose(pulse_run['analysis']['settings']['stop_time'], 6e-6, rel_tol=1e-12)
            result = pulse_run['result']
            assert any('dc value used for op' in line for line in result['logs'])
            source_node = result['diagnostics']['node_map']['v1.p']
            index_on = min(range(len(result['x'])), key=lambda i: abs(result['x'][i] - 0.5e-6))
            index_off = min(range(len(result['x'])), key=lambda i: abs(result['x'][i] - 1.5e-6))
            assert math.isclose(result['vectors'][source_node][index_on], 2.5, rel_tol=1e-9)
            assert abs(result['vectors'][source_node][index_off]) < 1e-9
            page.screenshot(path=str(args.output / 'pulse-results.png'), full_page=True, animations='disabled')
            passed('The real pulse run reproduces the entered delay, rise/fall, width and period; saved source voltage is 2.5 V during ON and 0 V during OFF')

            page.get_by_role('button', name='保存済みモデル', exact=False).click()
            page.get_by_label('モデルを検索', exact=True).fill(name)
            page.locator(f'.model-table tr[data-model-id="{saved["id"]}"] .model-name button').click()
            page.get_by_label('回路名', exact=True).wait_for()
            page.locator(f'.react-flow__node[data-id="{diode_id}"]').click()
            expect(page.get_by_label('飽和電流 IS（A）', exact=True)).to_have_value('1e-12')
            expect(page.get_by_label('逆ブレーク電圧 BV（V）', exact=True)).to_have_value('75')
            expect(page.get_by_label('終了時間', exact=True)).to_have_value('0.006')
            page.get_by_label('逆ブレーク電圧 BV（V）', exact=True).fill('')
            cleared = save()
            cleared_parameters = next(c for c in cleared['document']['components'] if c['id'] == diode_id)['parameters']['model_parameters']
            assert 'BV' not in cleared_parameters
            old = get('/runs/' + pulse_run['id'])
            assert next(c for c in old['snapshot']['components'] if c['id'] == diode_id)['parameters']['model_parameters']['BV'] == 75
            passed('The full-page model library restores source/diode values and physical-time settings; clearing optional BV removes the override while historical run input stays unchanged')

            page.locator('.react-flow__node[data-id="v1"]').click()
            page.get_by_text('電源波形・2周波数RF', exact=True).click()
            page.get_by_label('波形', exact=True).select_option('rf')
            input_value('RF周波数（Hz）', '4E+7')
            input_value('第2 RF周波数（Hz）', '8e7')
            input_value('包絡パルス周波数（Hz）', '1e5')
            page.get_by_label('包絡パルス周波数（Hz）', exact=True).fill('')
            cleared_rf = save()
            waveform = next(c for c in cleared_rf['document']['components'] if c['id'] == 'v1')['parameters']['waveform']
            assert waveform['frequency_hz'] == 40e6 and waveform['second_frequency_hz'] == 80e6
            assert 'pulse_frequency_hz' not in waveform
            page.get_by_label('波形', exact=True).select_option('pulse')
            # Leave the validation model on the last physically calculated input.
            final_document = json.loads(page.get_by_label('回路JSON', exact=True).input_value())
            for component in final_document['components']:
                if component['id'] == 'v1':
                    component['parameters'] = source['parameters']
            apply_document(final_document)
            save()
            passed('RF carrier/second-frequency/envelope fields support e/E notation and clearing the optional envelope removes it from the saved waveform')

            assert not evidence['browser_runtime_errors'], evidence['browser_runtime_errors']
            evidence['completed'] = True
            passed('No browser runtime errors; final validation circuit and both real solver histories are retained')
            browser.close()
    except Exception:
        evidence['failure'] = traceback.format_exc()
        report_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        raise
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
