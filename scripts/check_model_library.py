#!/usr/bin/env python3
"""Verify saved-model search, pagination, navigation and copying against real APIs.

Creates 27 named EDD fixtures, one solver run and two additional saved models.
No API responses or solver results are mocked. Requires Playwright/Chromium.
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
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://localhost:8080')
    parser.add_argument('--output', type=Path, default=Path('reports/model-library'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip('/')
    stamp = str(time.time_ns())
    prefix = '一覧検証 ' + stamp
    memo_query = '検索用メモ ' + stamp + '：シリコン壁・RF'
    employee = '000-library-check'
    other = 'library-other'
    evidence = {'completed': False, 'api_mocked': False, 'solver_results_mocked': False,
                'checks': [], 'browser_runtime_errors': [], 'fixture_prefix': prefix}
    report_path = args.output / 'verification.json'
    root = Path(__file__).resolve().parents[1]
    evidence['source_sha256'] = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in
        ['frontend/src/App.tsx', 'frontend/src/ModelLibrary.tsx', 'frontend/src/styles.css',
         'frontend/src/types.ts', 'backend/app/api.py']}

    def passed(message):
        evidence['checks'].append(message)
        report_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print('PASS: ' + message, flush=True)

    def expired(*_):
        raise TimeoutError('Model-library verification exceeded its 180 second budget')

    signal.signal(signal.SIGALRM, expired)
    signal.alarm(180)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium'), args=['--no-sandbox'])
            context = browser.new_context(viewport={'width': 1500, 'height': 1050})
            page = context.new_page()
            page.set_default_timeout(10000)
            page.on('pageerror', lambda error: evidence['browser_runtime_errors'].append(str(error)))
            page.on('dialog', lambda dialog: dialog.accept())

            def request(path, method='GET', body=None):
                response = context.request.fetch(base + '/api' + path, method=method, data=body, timeout=10000)
                assert response.ok, (path, response.status, response.text())
                return response.json()

            preset = next(p for p in request('/presets')['presets'] if p['id'] == 'edd')
            fixtures = []
            for index in range(27):
                document = json.loads(json.dumps(preset['document']))
                document.update(name=f'{prefix} / EDD {index + 1:02}', description=memo_query + ' 40 MHz' if index == 0 else 'ページ送り検証用モデル')
                saved = request('/circuits', 'POST', {'employee_id': employee, 'document': document})
                if index < 5:
                    saved = request('/circuits/' + saved['id'], 'PUT', {'employee_id': other, 'expected_revision': 1, 'document': document})
                fixtures.append(saved)
            target = fixtures[0]
            run = request('/runs', 'POST', {'employee_id': employee, 'circuit_id': target['id'], 'expected_revision': target['revision'],
                'analysis': {'kind': 'transient', 'settings': {'time_step': 25e-9, 'stop_time': 2e-6}}})
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                run = request('/runs/' + run['id'])
                if run['status'] not in {'queued', 'running'}:
                    break
                page.wait_for_timeout(200)
            assert run['status'] == 'succeeded' and run['result']['converged'], run.get('error')
            evidence['real_solver_run'] = {'id': run['id'], 'status': run['status'], 'converged': run['result']['converged'], 'analysis': run['analysis']}
            evidence['fixture_ids'] = [model['id'] for model in fixtures]

            listing_requests = []
            page.on('request', lambda req: listing_requests.append(req.url) if req.method == 'GET' and urlparse(req.url).path == '/api/circuits' else None)
            page.goto(base, wait_until='networkidle')
            page.get_by_role('heading', name='解析ワークスペース', exact=True).wait_for()
            page.get_by_label('外観', exact=True).select_option('light')
            page.locator('#employee-id').fill('')
            unsaved = '未保存の編集 ' + stamp
            page.get_by_label('回路名', exact=True).fill(unsaved)
            page.get_by_label('メモ', exact=False).fill('一覧から戻っても維持するメモ')
            page.get_by_label('出力時間刻みの単位', exact=True).select_option('ns')
            page.get_by_label('出力時間刻み', exact=True).fill('37')

            def list_action(action):
                with page.expect_response(lambda r: urlparse(r.url).path == '/api/circuits' and r.request.method == 'GET' and parse_qs(urlparse(r.url).query).get('limit') != ['1']) as pending:
                    action()
                response = pending.value
                assert response.ok, response.text()
                result = response.json()
                page.wait_for_function("""expected => {
                    const section=document.querySelector('.model-library-results');
                    const count=document.querySelector('.model-results-toolbar strong')?.textContent.replaceAll(',','');
                    const ids=[...document.querySelectorAll('.model-table tbody tr')].map(el=>el.dataset.modelId);
                    return section?.getAttribute('aria-busy')==='false' && count===String(expected.total) && JSON.stringify(ids)===JSON.stringify(expected.ids);
                }""", arg={'total': result['total'], 'ids': [model['id'] for model in result['circuits']]})
                return result

            def search(term):
                return list_action(lambda: page.get_by_label('モデルを検索', exact=True).fill(term))

            def open_target():
                page.locator(f'.model-table tr[data-model-id="{target["id"]}"] .model-name button').click()
                page.wait_for_function("""() => !document.querySelector('.model-library') ||
                    document.querySelector('#modal-title')?.textContent === '未保存の変更があります'""")

            list_action(lambda: page.get_by_role('button', name='保存済みモデル', exact=False).click())
            expect(page.get_by_role('heading', name='保存済みモデル', exact=True)).to_be_visible()
            expect(page.locator('.modal-backdrop')).to_have_count(0)
            assert page.url.endswith('#models')
            page.go_back()
            expect(page.get_by_label('回路名', exact=True)).to_have_value(unsaved)
            expect(page.get_by_label('メモ', exact=False)).to_have_value('一覧から戻っても維持するメモ')
            assert math.isclose(float(page.get_by_label('出力時間刻み', exact=True).input_value()), 0.037, rel_tol=1e-12)
            list_action(lambda: page.get_by_role('button', name='保存済みモデル', exact=False).click())
            passed('A full-page library opens without a modal or employee ID; browser Back preserves unsaved document and physical-time settings')

            first = search(prefix)
            assert first['total'] == 27 and len(first['circuits']) == 25
            second = list_action(lambda: page.get_by_role('button', name='次のページ', exact=True).click())
            assert second['offset'] == 25 and len(second['circuits']) == 2
            assert len({model['id'] for model in first['circuits'] + second['circuits']}) == 27
            larger = list_action(lambda: page.get_by_label('表示件数', exact=True).select_option('50'))
            assert larger['offset'] == 0 and len(larger['circuits']) == 27
            sorted_models = list_action(lambda: page.get_by_label('並べ替え', exact=True).select_option('name_asc'))
            assert [model['name'] for model in sorted_models['circuits']] == [model['name'] for model in fixtures]
            passed('Real PostgreSQL pagination shows 25 + 2 distinct models; page-size and sort changes reset the page and order correctly')

            memo = search(memo_query)
            assert [model['id'] for model in memo['circuits']] == [target['id']]
            search(prefix)
            filtered = list_action(lambda: page.get_by_label('更新者（社員番号）', exact=True).fill(other))
            assert filtered['total'] == 5 and all(model['updated_by'] == other for model in filtered['circuits'])
            period = list_action(lambda: page.get_by_label('更新期間', exact=True).select_option('7'))
            assert period['total'] == 5
            page.screenshot(path=str(args.output / 'models-desktop.png'), full_page=True, animations='disabled')
            page.locator('#employee-id').fill(employee)
            mine = list_action(lambda: page.get_by_role('button', name='自分が更新したモデル', exact=True).click())
            assert mine['total'] == 22 and all(model['updated_by'] == employee for model in mine['circuits'])
            empty = search(prefix + ' no match')
            assert empty['total'] == 0
            expect(page.get_by_role('heading', name='検索条件に一致するモデルがありません', exact=True)).to_be_visible()
            list_action(lambda: page.locator('.model-library-filters').get_by_role('button', name='検索条件をクリア', exact=True).click())
            passed('Name/memo/employee searches, exact updater and period filters, own-updater shortcut and no-match recovery work on persisted models')

            search(target['name'])
            open_target()
            page.get_by_role('button', name='編集を続ける', exact=True).click()
            expect(page.get_by_role('heading', name='保存済みモデル', exact=True)).to_be_visible()
            page.get_by_role('button', name='編集画面に戻る', exact=True).click()
            expect(page.get_by_label('回路名', exact=True)).to_have_value(unsaved)
            list_action(lambda: page.get_by_role('button', name='保存済みモデル', exact=False).click())
            expect(page.get_by_label('モデルを検索', exact=True)).to_have_value(target['name'])
            open_target()
            page.get_by_role('button', name='変更を閉じて開く', exact=True).click()
            expect(page.get_by_label('回路名', exact=True)).to_have_value(target['name'])
            expect(page.get_by_label('解析方法', exact=True)).to_have_value('transient')
            assert math.isclose(float(page.get_by_label('出力時間刻み', exact=True).input_value()), 0.025, rel_tol=1e-12)
            page.get_by_role('button', name='すべて', exact=True).click()
            page.locator('.run-list button').first.click()
            page.locator('.result-health.ok').wait_for()
            passed('Opening a model protects unsaved input, cancellation preserves edits, search state survives navigation, and latest saved calculation conditions/results restore')

            list_action(lambda: page.get_by_role('button', name='保存済みモデル', exact=False).click())
            expect(page.locator(f'.model-table tr[data-model-id="{target["id"]}"] .kind-tag').first).to_have_text('編集中')
            page.get_by_role('button', name='コピーとして保存', exact=True).click()
            copy_name = prefix + ' / コピー'
            page.get_by_label('回路名', exact=True).fill(copy_name)
            with page.expect_response(lambda r: urlparse(r.url).path == '/api/circuits' and r.request.method == 'POST') as pending:
                page.get_by_role('button', name='保存', exact=True).click()
            assert pending.value.status == 201
            copied = pending.value.json()
            assert copied['id'] != target['id']
            assert request('/circuits/' + target['id'])['revision'] == target['revision']
            evidence['copied_model_id'] = copied['id']
            passed('Copy-as-new still creates a separate model without overwriting the original revision')

            list_action(lambda: page.get_by_role('button', name='保存済みモデル', exact=False).click())
            search(prefix)
            external_document = json.loads(json.dumps(preset['document']))
            external_document.update(name=prefix + ' / 別ブラウザの保存', description='更新ボタンで共有保存を再取得')
            request('/circuits', 'POST', {'employee_id': other, 'document': external_document})
            refreshed = list_action(lambda: page.get_by_role('button', name='一覧を更新', exact=True).click())
            assert refreshed['total'] == 29
            context.set_offline(True)
            page.get_by_role('button', name='一覧を更新', exact=True).click()
            expect(page.get_by_role('heading', name='一覧を取得できませんでした', exact=True)).to_be_visible()
            context.set_offline(False)
            list_action(lambda: page.get_by_role('button', name='再試行', exact=True).click())
            passed('Refresh fetches another client\'s newly saved model; a real network interruption displays an error and retry recovers')

            search(memo_query)
            for width in [390, 320]:
                page.set_viewport_size({'width': width, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            page.set_viewport_size({'width': 390, 'height': 844})
            toast = page.get_by_role('button', name='通知を閉じる', exact=True)
            if toast.is_visible():
                toast.click()
            page.screenshot(path=str(args.output / 'models-mobile.png'), full_page=True, animations='disabled')
            page.get_by_label('外観', exact=True).select_option('dark')
            page.get_by_label('モデルを検索', exact=True).focus()
            assert page.get_by_label('モデルを検索', exact=True).evaluate("el => getComputedStyle(el.parentElement).outlineStyle") == 'solid'
            page.screenshot(path=str(args.output / 'models-mobile-dark.png'), full_page=True, animations='disabled')
            direct = context.new_page()
            direct.goto(base + '/#models', wait_until='networkidle')
            expect(direct.get_by_role('heading', name='保存済みモデル', exact=True)).to_be_visible()
            expect(direct.locator('.modal-backdrop')).to_have_count(0)
            assert all(parse_qs(urlparse(url).query).get('limit') in [['1'], ['25'], ['50'], ['100']] for url in listing_requests), listing_requests
            evidence['list_requests'] = len(listing_requests)
            evidence['all_ui_list_requests_bounded'] = True
            passed('320/390 px mobile views have no horizontal overflow; dark keyboard focus and direct #models navigation work; every UI list request is bounded')
            assert not evidence['browser_runtime_errors'], evidence['browser_runtime_errors']
            evidence['completed'] = True
            browser.close()
    except Exception:
        evidence['error'] = traceback.format_exc()
        print(evidence['error'], flush=True)
        raise
    finally:
        signal.alarm(0)
        report_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
