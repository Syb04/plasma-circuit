#!/usr/bin/env python3
"""Exercise model deletion against real React, PostgreSQL, Redis and ngspice.

Only fixtures created by this invocation are deleted. Existing models are
checked before/after; deletion keeps historical run snapshots and results.
Requires Playwright and Chromium and leaves an exported result as evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import signal
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://localhost:8080')
    parser.add_argument('--output', type=Path, default=Path('reports/model-deletion'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip('/')
    stamp = str(time.time_ns())
    prefix = '削除検証 ' + stamp
    employee = '000-delete-' + stamp
    evidence = {'completed': False, 'api_responses_mocked': False, 'solver_results_mocked': False,
                'checks': [], 'browser_runtime_errors': [], 'fixture_prefix': prefix, 'employee_id': employee}
    report = args.output / 'verification.json'
    root = Path(__file__).resolve().parents[1]
    evidence['source_sha256'] = {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in
        ['frontend/src/App.tsx', 'frontend/src/ModelLibrary.tsx', 'frontend/src/styles.css',
         'backend/app/api.py', 'backend/app/database.py', 'backend/app/schemas.py']}

    def write():
        report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')

    def passed(message):
        evidence['checks'].append(message)
        write()
        print('PASS: ' + message, flush=True)

    def expired(*_):
        raise TimeoutError('Deletion verification exceeded its 180 second budget')

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

            def response(path, method='GET', body=None):
                return context.request.fetch(base + '/api' + path, method=method, data=body, timeout=10000)

            def request(path, method='GET', body=None):
                result = response(path, method, body)
                assert result.ok, (path, result.status, result.text())
                return result.json()

            original = request('/circuits')['circuits']
            originals = {m['id']: request('/circuits/' + m['id']) for m in original}
            preset = next(p for p in request('/presets')['presets'] if p['id'] == 'edd')
            fixtures = []

            def create(name):
                doc = json.loads(json.dumps(preset['document']))
                doc.update(name=name, description='削除機能の検証用。既存データとは別のモデル。')
                saved = request('/circuits', 'POST', {'employee_id': employee, 'document': doc})
                fixtures.append(saved)
                evidence['fixture_ids'] = [m['id'] for m in fixtures]
                write()
                return saved

            for index in range(26):
                create(f'{prefix} / EDD {index + 1:02}')
            target = fixtures[2]
            run = request('/runs', 'POST', {'employee_id': employee, 'circuit_id': target['id'], 'expected_revision': 1,
                'analysis': {'kind': 'transient', 'settings': {'time_step': 25e-9, 'stop_time': 2e-6}}})
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                run = request('/runs/' + run['id'])
                if run['status'] not in {'queued', 'running'}:
                    break
                page.wait_for_timeout(200)
            assert run['status'] == 'succeeded' and run['result']['converged'], run.get('error')
            package_before = request('/runs/' + run['id'] + '/package')
            csv_before = response('/runs/' + run['id'] + '/export.csv').body()
            evidence['real_solver_run'] = {'id': run['id'], 'status': run['status'], 'converged': True}

            def list_action(action):
                with page.expect_response(lambda r: urlparse(r.url).path == '/api/circuits' and r.request.method == 'GET'
                    and parse_qs(urlparse(r.url).query).get('limit') != ['1']) as pending:
                    action()
                result = pending.value.json()
                assert pending.value.ok, result
                # After deleting the last row on the final page the app requests page 1.
                if result['offset'] >= result['total'] and result['offset'] > 0:
                    page.wait_for_function("() => document.querySelector('.model-library-results')?.getAttribute('aria-busy')==='false'")
                    result = request('/circuits?' + 'q=' + prefix.replace(' ', '%20') + '&limit=25&sort=name_asc')
                page.wait_for_function("""expected => {
                    const section=document.querySelector('.model-library-results');
                    const ids=[...document.querySelectorAll('.model-table tbody tr')].map(el=>el.dataset.modelId);
                    const count=document.querySelector('.model-results-toolbar strong')?.textContent.replaceAll(',','');
                    return section?.getAttribute('aria-busy')==='false' && count===String(expected.total) && JSON.stringify(ids)===JSON.stringify(expected.ids);
                }""", arg={'total': result['total'], 'ids': [m['id'] for m in result['circuits']]})
                return result

            def search(term):
                return list_action(lambda: page.get_by_label('モデルを検索', exact=True).fill(term))

            def row(model):
                return page.locator(f'.model-table tr[data-model-id="{model["id"]}"]')

            def confirm():
                page.get_by_role('button', name='選択したモデルを削除', exact=True).click()
                return page.get_by_role('dialog', name='件のモデルを削除', exact=False)

            def submit_delete(count, status=200):
                with page.expect_response(lambda r: urlparse(r.url).path == '/api/circuits/delete' and r.request.method == 'POST') as pending:
                    result = list_action(lambda: page.get_by_role('dialog').get_by_role('button', name=f'{count} 件を削除', exact=True).click())
                assert pending.value.status == status, pending.value.text()
                expect(page.get_by_role('dialog')).to_have_count(0)
                return result

            page.goto(base, wait_until='networkidle')
            page.get_by_label('外観', exact=True).select_option('light')
            page.locator('#employee-id').fill('')
            list_action(lambda: page.get_by_role('button', name='保存済みモデル', exact=False).click())
            search(prefix)
            list_action(lambda: page.get_by_label('並べ替え', exact=True).select_option('name_asc'))
            row(fixtures[0]).get_by_role('checkbox').check()
            expect(page.get_by_role('button', name='選択したモデルを削除', exact=True)).to_be_disabled()
            expect(page.get_by_text('削除するには上部に社員番号を入力してください。', exact=True)).to_be_visible()
            page.locator('#employee-id').fill(employee)
            dialog = confirm()
            expect(dialog.get_by_role('button', name='キャンセル', exact=True)).to_be_focused()
            dialog.get_by_role('button', name='キャンセル', exact=True).click()
            assert response('/circuits/' + fixtures[0]['id']).status == 200
            confirm()
            page.keyboard.press('Escape')
            expect(page.get_by_role('dialog')).to_have_count(0)
            passed('Employee ID is required for deletion; selection alone does not delete, and Cancel/Escape keep the model')

            list_action(lambda: page.get_by_role('button', name='一覧を更新', exact=True).click())
            expect(page.locator('.model-selection-toolbar')).to_contain_text('0 件を選択')
            row(fixtures[0]).get_by_role('checkbox').check()
            list_action(lambda: page.get_by_role('button', name='次のページ', exact=True).click())
            expect(page.locator('.model-selection-toolbar')).to_contain_text('0 件を選択')
            row(fixtures[25]).get_by_role('checkbox').check()
            confirm()
            listing = submit_delete(1)
            assert listing['offset'] == 0 and listing['total'] == 25
            expect(page.locator('.model-pagination>div>span')).to_have_text('1 / 1')
            passed('Refresh and page changes clear selection; deleting the only final-page model returns to page 1 with correct counts')

            for model in fixtures[:2]:
                row(model).get_by_role('checkbox').check()
            assert page.get_by_role('checkbox', name='このページをすべて選択', exact=True).evaluate('el => el.indeterminate')
            dialog = confirm()
            expect(dialog.locator('.model-delete-list li')).to_have_count(2)
            updated = request('/circuits/' + fixtures[0]['id'], 'PUT', {'employee_id': 'other-delete-check',
                'expected_revision': 1, 'document': {**fixtures[0]['document'], 'description': '別ユーザーによる確認後の更新'}})
            listing = submit_delete(2, status=409)
            assert listing['total'] == 25
            expect(page.locator('.model-action-error')).to_contain_text('今回は削除していません')
            assert all(response('/circuits/' + m['id']).status == 200 for m in fixtures[:2])
            expect(row(fixtures[0])).to_contain_text('rev.2')
            expect(page.locator('.model-selection-toolbar')).to_contain_text('0 件を選択')
            for model in fixtures[:2]:
                row(model).get_by_role('checkbox').check()
            confirm()
            listing = submit_delete(2)
            assert listing['total'] == 23
            assert all(response('/circuits/' + m['id']).status == 404 for m in fixtures[:2])
            passed('A real concurrent update aborts the entire two-model deletion with 409; refreshed selection deletes both models atomically')

            row(target).locator('.model-name button').click()
            page.wait_for_function("() => !document.querySelector('.model-library') || document.querySelector('#modal-title')?.textContent==='未保存の変更があります'")
            if page.get_by_role('button', name='変更を閉じて開く', exact=True).count():
                page.get_by_role('button', name='変更を閉じて開く', exact=True).click()
            retained_name = prefix + ' / 保持した編集中の内容'
            retained_memo = '削除しても失わない未保存のメモ'
            page.get_by_label('回路名', exact=True).fill(retained_name)
            page.get_by_label('メモ', exact=False).fill(retained_memo)
            page.get_by_label('出力時間刻みの単位', exact=True).select_option('ns')
            page.get_by_label('出力時間刻み', exact=True).fill('37')
            list_action(lambda: page.get_by_role('button', name='保存済みモデル', exact=False).click())
            row(target).get_by_role('checkbox').check()
            page.screenshot(path=str(args.output / 'selected-desktop.png'), full_page=True, animations='disabled')
            dialog = confirm()
            expect(dialog).to_contain_text('編集中の内容は保持します')
            page.screenshot(path=str(args.output / 'confirmation-desktop.png'), full_page=True, animations='disabled')
            for width, theme in ((390, 'light'), (320, 'dark')):
                page.set_viewport_size({'width': width, 'height': 844})
                # The modal traps keyboard focus; changing appearance here emulates stored preference.
                page.evaluate("theme => {document.documentElement.dataset.theme=theme;}", theme)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
                expect(dialog.get_by_role('button', name='1 件を削除', exact=True)).to_be_visible()
                page.screenshot(path=str(args.output / f'confirmation-mobile-{theme}.png'), full_page=False, animations='disabled')
            page.set_viewport_size({'width': 1500, 'height': 1050})
            page.evaluate("document.documentElement.dataset.theme='light'")
            dialog.get_by_role('button', name='1 件を削除', exact=True).focus()
            page.keyboard.press('Tab')
            expect(dialog.get_by_role('button', name='削除確認を閉じる', exact=True)).to_be_focused()
            page.keyboard.press('Shift+Tab')
            expect(dialog.get_by_role('button', name='1 件を削除', exact=True)).to_be_focused()
            submit_delete(1)
            expect(page.get_by_role('button', name='コピーとして保存', exact=True)).to_have_count(0)
            page.get_by_role('button', name='編集画面に戻る', exact=True).click()
            expect(page.get_by_label('回路名', exact=True)).to_have_value(retained_name)
            expect(page.get_by_label('メモ', exact=False)).to_have_value(retained_memo)
            # AnalysisPanel remounts with its default display unit; the SI setting is retained.
            expect(page.get_by_label('出力時間刻み', exact=True)).to_have_value('0.037')
            expect(page.get_by_label('出力時間刻みの単位', exact=True)).to_have_value('us')
            page.get_by_label('出力時間刻みの単位', exact=True).select_option('ns')
            expect(page.get_by_label('出力時間刻み', exact=True)).to_have_value('37')
            expect(page.locator('.sidebar-history')).to_contain_text('過渡解析')
            with page.expect_response(lambda r: urlparse(r.url).path == '/api/circuits' and r.request.method == 'POST') as pending:
                page.get_by_role('button', name='保存', exact=True).click()
            copied = pending.value.json()
            assert pending.value.status == 201 and copied['id'] != target['id']
            assert copied['document']['name'] == retained_name and copied['document']['description'] == retained_memo
            fixtures.append(copied)
            evidence['fixture_ids'] = [m['id'] for m in fixtures]
            expect(page.locator('.document-title')).to_contain_text('rev.1')
            assert response('/circuits/' + target['id']).status == 404
            assert request('/runs/' + run['id']) == run
            package_after = request('/runs/' + run['id'] + '/package')
            # Export timestamps/envelope digests change on each export, content hashes do not.
            stable_package = lambda value: {k: v for k, v in value.items() if k not in {'exported_at', 'package_sha256'}}
            assert stable_package(package_after) == stable_package(package_before)
            evidence['retained_package_content_hashes'] = package_after['hashes']
            assert response('/runs/' + run['id'] + '/export.csv').body() == csv_before
            (args.output / 'retained-result.csv').write_bytes(csv_before)
            passed('Deleting the current model preserves unsaved name/memo/physical-time settings and history; Save creates a new ID without resurrecting the original')
            passed('Native ngspice run, immutable input/result, CSV bytes and analysis-package content/hashes are unchanged after model deletion')
            passed('Deletion confirmation fits 390 px light and 320 px dark viewports and keeps keyboard focus within the dialog')

            list_action(lambda: page.get_by_role('button', name='保存済みモデル', exact=False).click())
            row(fixtures[3]).get_by_role('checkbox').check()
            dialog = confirm()
            context.set_offline(True)
            dialog.get_by_role('button', name='1 件を削除', exact=True).click()
            expect(dialog.locator('.model-action-error')).to_be_visible()
            expect(dialog.get_by_role('button', name='1 件を削除', exact=True)).to_be_enabled()
            context.set_offline(False)
            assert response('/circuits/' + fixtures[3]['id']).status == 200
            submit_delete(1)
            passed('A real offline failure keeps the dialog and model; reconnecting and retrying completes deletion')

            overlapping = [create(f'{prefix} / 同時削除 {i}') for i in range(3)]
            barrier = threading.Barrier(2)
            def concurrent_delete(models):
                payload = {'employee_id': employee, 'circuits': [{'id': m['id'], 'expected_revision': 1} for m in models]}
                req = Request(base + '/api/circuits/delete', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
                barrier.wait(timeout=10)
                try:
                    with urlopen(req, timeout=10) as result:
                        return result.status, json.load(result)
                except HTTPError as error:
                    return error.code, json.load(error)
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(concurrent_delete, models) for models in (overlapping[:2], overlapping[1:])]
                outcomes = [future.result(timeout=15) for future in futures]
            assert sorted(status for status, _ in outcomes) == [200, 409], outcomes
            deleted = next(payload['deleted_ids'] for status, payload in outcomes if status == 200)
            assert len(deleted) == 2
            assert all(response('/circuits/' + m['id']).status == (404 if m['id'] in deleted else 200) for m in overlapping)
            evidence['concurrent_batch_statuses'] = [status for status, _ in outcomes]
            passed('Two simultaneous overlapping PostgreSQL batches yield one success and one 409 with no partial deletion or deadlock')

            list_action(lambda: page.get_by_role('button', name='一覧を更新', exact=True).click())
            owned = request('/circuits')['circuits']
            owned = [m for m in owned if m['id'] in {f['id'] for f in fixtures}]
            assert all(m['created_by'] == employee for m in owned)
            assert len(owned) <= 25
            displayed_ids = page.locator('.model-table tbody tr').evaluate_all('rows => rows.map(row => row.dataset.modelId)')
            assert set(displayed_ids) == {m['id'] for m in owned}, 'Refuse select-all on a page containing unrelated models'
            page.get_by_role('checkbox', name='このページをすべて選択', exact=True).check()
            expect(page.locator('.model-selection-toolbar')).to_contain_text(f'{len(owned)} 件を選択')
            confirm()
            listing = submit_delete(len(owned))
            assert listing['total'] == 0
            expect(page.get_by_role('heading', name='検索条件に一致するモデルがありません', exact=True)).to_be_visible()
            final = request('/circuits')['circuits']
            assert {m['id'] for m in final} == set(originals)
            assert all(request('/circuits/' + id) == value for id, value in originals.items())
            assert not evidence['browser_runtime_errors'], evidence['browser_runtime_errors']
            evidence.update(completed=True, original_model_count=len(originals), remaining_model_count=len(final))
            passed('Select-all deletes only the displayed fixture page; empty results and sidebar counts update while every original model remains unchanged')
            browser.close()
    except Exception:
        evidence['failure'] = traceback.format_exc()
        write()
        raise
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
