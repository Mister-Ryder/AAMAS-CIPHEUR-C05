"""Loopback-bound SSH reverse tunnel from the cloud host to the local relay."""
from __future__ import annotations

import argparse
import select
import socket
import threading
import time
from pathlib import Path

import paramiko


def bridge(channel, local_port: int) -> None:
    local = None
    try:
        local = socket.create_connection(("127.0.0.1", local_port), timeout=10)
        local.settimeout(20)
        channel.settimeout(20)
        pairs = ((channel, local), (local, channel))
        while True:
            readable, _, _ = select.select([channel, local], [], [], 1.0)
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                target = local if source is channel else channel
                target.sendall(data)
    except (OSError, EOFError, paramiko.SSHException):
        return
    finally:
        try:
            channel.close()
        except Exception:
            pass
        if local is not None:
            try:
                local.close()
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password-file", type=Path)
    parser.add_argument("--identity-file", type=Path)
    parser.add_argument("--known-hosts", type=Path)
    parser.add_argument("--local-port", type=int, default=24488)
    parser.add_argument("--remote-port", type=int, default=24488)
    parser.add_argument("--stop-file", type=Path, required=True)
    args = parser.parse_args()

    password = args.password_file.read_text(encoding="utf-8").strip() if args.password_file else None
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if args.known_hosts:
        client.load_host_keys(str(args.known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(args.host, port=args.ssh_port, username=args.user, password=password,
                   key_filename=str(args.identity_file) if args.identity_file else None,
                   look_for_keys=password is None and args.identity_file is None,
                   allow_agent=password is None,
                   timeout=20, banner_timeout=20, auth_timeout=20)
    transport = client.get_transport()
    transport.set_keepalive(10)

    def accept_forward(channel, _origin, _server):
        threading.Thread(target=bridge, args=(channel, args.local_port), daemon=True).start()

    try:
        transport.request_port_forward("127.0.0.1", args.remote_port, handler=accept_forward)
        print("SSH reverse tunnel is listening on the remote loopback interface.", flush=True)
        while transport.is_active() and not args.stop_file.exists():
            time.sleep(1)
    finally:
        try:
            transport.cancel_port_forward("127.0.0.1", args.remote_port)
        except Exception:
            pass
        client.close()
        print("SSH reverse tunnel stopped.", flush=True)


if __name__ == "__main__":
    main()
