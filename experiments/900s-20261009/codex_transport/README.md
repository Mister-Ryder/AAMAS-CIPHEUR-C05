# C05–Codex transport for the 900-second experiment

The deployed arm used an HTTPS relay bound to loopback on the local Codex workstation and an SSH reverse tunnel that exposed the same loopback port on the cloud host. The cloud worker sent the frozen C05 `PlanProvider` request to `https://127.0.0.1:24488/v1/chat/completions`. Only a short-lived relay bearer token and the public TLS certificate were placed on the cloud host. Codex CLI authentication and the TLS private key stayed on the local workstation.

The relay [c05_codex_relay.py](c05_codex_relay.py) is byte-identical to the code used for the experiment. It validates the compact C05 observation, extracts its `snapshot_id` and registered plan IDs, then calls `codex exec --model gpt-6-luna` with a JSON schema restricted to that snapshot and those IDs. It returns a chat-completion envelope to the existing C05 provider. Native search continues during the request. The solver performs its own plan validation before installation. The model is choosing one finite C05 whole-plan ID; this experiment does not ask it to synthesize a new four-member strategy.

The cloud [run_codex_900.py](run_codex_900.py), [launch script](launch_codex_900.sh), and local [reverse tunnel](codex_reverse_tunnel.py) are public adaptations of the deployed scripts. They replace machine-specific paths and the private SSH helper with explicit environment variables or command-line arguments. Their source hashes therefore differ from the deployed copies; both sets of hashes are in [live_transport_hashes.json](live_transport_hashes.json). The algorithm settings, model request, result schema, and independent audit are unchanged by these path adaptations.

## Local relay

Use Python 3.10+, Paramiko for the tunnel, and an authenticated Codex CLI on the local workstation. Set these environment variables to paths outside the repository:

| Variable | Purpose |
|---|---|
| `CIPHEUR_RELAY_TOKEN_FILE` | File holding a generated relay bearer token |
| `CIPHEUR_RELAY_CERT_FILE` | TLS certificate with loopback IP subject alternative name |
| `CIPHEUR_RELAY_KEY_FILE` | Matching private key, kept local |
| `CIPHEUR_RELAY_LOG` | Status/hash log, kept local |
| `CIPHEUR_RELAY_ARCHIVE_DIR` | Private gzip archive of actual request and response pairs |
| `CIPHEUR_RELAY_SCRATCH` | Local scratch directory used by `codex exec` |

The relay defaults to port `24488`, model `gpt-6-luna`, reasoning effort `low`, a 37-second Codex CLI deadline, and eight concurrent requests. `CODEX_RELAY_EXE`, `CODEX_RELAY_MODEL`, `CODEX_RELAY_REASONING`, `CODEX_RELAY_TIMEOUT`, and `CIPHEUR_RELAY_PORT` allow an independently registered run to change those transport settings. The published experiment requested `gpt-6-luna` and used the defaults. Start the relay with `python c05_codex_relay.py` after setting the paths.

Start [codex_reverse_tunnel.py](codex_reverse_tunnel.py) with `--host`, `--ssh-port`, `--user`, and `--stop-file`; it accepts SSH agent/key authentication or a private `--password-file`. Load a verified host key through the system known-hosts file or `--known-hosts`. Both forwarded endpoints bind to `127.0.0.1`; port `24488` is the default on each side. Keep the tunnel running while the cloud jobs run.

## Cloud runner

The isolated cloud root holds the patched C05 source, its native library, the preregistration and source-patch receipts, and an empty result directory. Set:

| Variable | Purpose |
|---|---|
| `CIPHEUR_EXPERIMENT_ROOT` | New isolated output root |
| `CIPHEUR_INPUT_DIR` | Directory containing the eight registered NPZ graphs |
| `CIPHEUR_RELAY_SHARED_DIR` | Directory with `relay_token.txt` and `relay_cert.pem` |
| `CIPHEUR_CODEX_CPUS` | Six distinct permitted CPU IDs, comma-separated |
| `CIPHEUR_C05_SOURCE` | Optional source path; defaults to `source_c05_controls_900s` under the root |
| `CIPHEUR_PREREGISTRATION` | Optional path to the registered protocol JSON |
| `CIPHEUR_C05_PATCH_RECORD` | Optional path to the source-patch receipt JSON |
| `CIPHEUR_RELAY_ENDPOINT` | Optional relay endpoint; defaults to loopback port `24488` |

Run `python -B run_codex_900.py`, or set `CIPHEUR_CODEX_RUNNER` to its absolute path and use `launch_codex_900.sh`. The original deployed source reserved CPUs `12–17`, six processes, one native thread each, `Config(seconds=900, max_calls=21)`, a 45-second provider timeout, a 60-second response lifetime, and a 40-second decision interval beginning at 20 seconds. Each result JSON contains full per-call prompt and response data and must remain private. Only the audited, allowlisted tables created by `build_public_result_package.py` belong in the public results directory.

The relay writes one private gzip file per actual model request, including the provider request, Codex stdin, CLI output, parsed response, and response envelope. It does not archive the bearer token or SSH credentials. The private manifest records per-file SHA256 and byte counts, and the strict audit links each result call to its archive by response ID, graph, seed, call index, and snapshot. The public package carries hashes and aggregate counts only.
