"""One SDK WebSocket owner per app; transport process never opens business storage."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from riji_agent.mentors.configuration import MentorConfig, credential, load_credentials
from riji_agent.mentors.feishu import FeishuChannel, build_client, normalize, normalize_host_group
from riji_agent.mentors.models import MentorError
from riji_agent.mentors.receiver_spool import ReceiverSpool, read_spool_status
from riji_agent.mentors.receiver_worker import ReceiverWorker


def receiver_credentials(config, application, journal_root):
    values = load_credentials(config, journal_root)
    return credential(application.secret_env, values), credential(application.transport_token_env, values)


def enqueue_event(event, application, worker):
    """Return only after local receipt commit; SDK sends 500 on storage failure."""
    try:
        message = normalize(event, application, allow_unsupported=True)
        if message.chat_type == "group":
            message = normalize_host_group(event, application)
    except Exception:
        # Invalid external payloads must not appear in SDK exception logging.
        return
    try:
        worker.spool.enqueue(message)
        worker.wake()
    except Exception:
        raise MentorError("receiver_enqueue_failed") from None


def run(config, application_id, base_url, lock_dir, journal_root):
    application = next((item for item in config.applications if item.external_id == application_id and item.platform == "feishu"), None)
    if (application is None or application.receiver != "dedicated"
            or config.feishu_receiver_ownership != "dedicated_apps"):
        raise MentorError("dedicated_feishu_application_required")
    url = urlsplit(base_url)
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or url.port != 8765
            or url.path not in {"", "/"} or url.query or url.username or url.fragment):
        raise MentorError("loopback_mentor_endpoint_required")
    secret, token = receiver_credentials(config, application, journal_root)
    client = build_client(application.external_id, secret.get_secret_value())
    channel = FeishuChannel({}, {application_id: client})
    transport = httpx.Client(base_url=base_url, timeout=20, trust_env=False,
                             headers={"Authorization": "Bearer " + token.get_secret_value()})

    if lock_dir.is_symlink() or lock_dir.resolve().is_relative_to(journal_root.resolve()):
        raise MentorError("receiver_spool_path_invalid")
    lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if lock_dir.stat().st_mode & 0o077:
        raise MentorError("receiver_spool_permissions_invalid")
    filename = hashlib.sha256(application_id.encode()).hexdigest() + ".lock"
    with (lock_dir / filename).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MentorError("feishu_receiver_already_running") from None
        spool = ReceiverSpool(lock_dir / (filename + ".sqlite3"), application_id)
        worker = ReceiverWorker(spool, transport, channel)
        try:
            worker.start()
            start_connection(application, secret, worker)
        finally:
            worker.close()
            transport.close()


def start_connection(application, secret, worker):
    import lark_oapi as lark
    from lark_oapi.ws import Client
    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(
        lambda event: enqueue_event(event, application, worker)).build()
    Client(application.external_id, secret.get_secret_value(), event_handler=handler,
           log_level=lark.LogLevel.ERROR).start()


def main():
    parser = argparse.ArgumentParser(description="Run a dedicated fixed-mentor Feishu receiver.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--application", required=True)
    parser.add_argument("--journal-root", required=True, type=Path)
    parser.add_argument("--lock-dir", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--status", action="store_true", help="Read receipt counts without starting or recovering a receiver.")
    args = parser.parse_args()
    try:
        if args.status:
            name = hashlib.sha256(args.application.encode()).hexdigest() + ".lock.sqlite3"
            print(json.dumps(read_spool_status(args.lock_dir / name), sort_keys=True))
            return
        run(MentorConfig.load(args.config, args.journal_root), args.application, args.base_url, args.lock_dir, args.journal_root)
    except MentorError as exc:
        parser.exit(2, exc.code + "\n")
    except Exception:
        parser.exit(2, "mentor_receiver_unavailable\n")


if __name__ == "__main__":
    main()
