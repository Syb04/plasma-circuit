"""Console-only first administrator creation and password recovery.

docker compose exec api python -m app.accounts create-admin --username admin
docker compose exec api python -m app.accounts reset-password --username admin
"""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from . import auth, database as db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create-admin", "reset-password"])
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name")
    parser.add_argument("--password-stdin", action="store_true", help="Read a password from standard input, never a command argument")
    parser.add_argument("--temporary", action="store_true", help="Require a password change at the next login")
    args = parser.parse_args()
    account_data = auth.CreateAccount(username=args.username, display_name=args.display_name or args.username, is_admin=True)
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("新しいパスワード（12〜128文字）: ")
        if getpass.getpass("確認のため再入力: ") != password:
            parser.error("パスワードが一致しません")
    if not 12 <= len(password) <= 128:
        parser.error("パスワードは12〜128文字で指定してください")
    db.init_database()
    with db.session() as session:
        auth.auth_lock(session)
        account = session.scalar(select(db.Account).where(db.Account.username == account_data.username))
        if args.command == "create-admin":
            if account is not None:
                parser.error("このログインIDは登録済みです")
            account = db.Account(username=account_data.username, display_name=account_data.display_name,
                      password_hash=auth.hash_password(password), is_admin=True, must_change_password=args.temporary)
            session.add(account)
            session.flush()
            auth.audit(session, account, account, "console_created")
        else:
            if account is None or account.deleted_at is not None:
                parser.error("有効なアカウントが見つかりません。削除したIDは再使用できません")
            account.password_hash = auth.hash_password(password)
            account.must_change_password = args.temporary
            auth.invalidate_sessions(session, account.id)
            auth.audit(session, account, account, "console_password_reset")
        session.commit()
    print("アカウント設定を保存しました。")


if __name__ == "__main__":
    main()
