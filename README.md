# llm-bias-fingerprint

Sampling and analysis of quantized Qwen3.5 models on identical prompts.

## Runbook: project llama-server

The project owns a second `llama-server` on **127.0.0.1:8013**, separate from the
global `llamad` unit on **127.0.0.1:8012**. FastAPI/web runs on **:8000**; it reads
`API_URL` from `.env`, which points at the project server.

The server is configured by CLI flags only (no `presets.ini`): `--models-max 5`
(one slot per project quant, so the router never evicts a model that a worker is
mid-request on), `--parallel` equal to the sampler's worker count, `--ctx-size 8192`,
`--fit-ctx 4096`, `--fit on`, and `--no-models-autoload`. `llama/models/` is a
generated, gitignored directory of symlinks to the five project quants in
`~/models`, so the server routes only those models.

`make run` starts the sampler/API with reload off: auto-reload kills the worker
tasks on every save and strands their runs until the lease expires. Use `make dev`
when you are editing code and not sampling.

### Commands

```sh
make server   # creates llama/models links and starts llama-server in the background
```

Stop it with `kill "$(cat .run/llama-server.pid)"`. Inspect it with `curl -s
localhost:8013/models | jq`, `curl -s localhost:8013/props | jq`, or `tail -f
.run/llama-server.log`.

### Memory safety (no systemd)

There is no systemd unit for the project server. Memory safety relies on the
server-side controls (`--fit on`, `--models-max 5`) plus the kernel OOM killer;
there is no hard cgroup cap on the project server. For a hard cap, launch the
same `llama-server` command under a scope, e.g.

```sh
systemd-run --user --scope -p MemoryMax=12G -p MemoryHigh=10G \
    llama-server --host 127.0.0.1 --port 8013 ...
```

### Coexistence with the global `llamad`

`llamad` on :8012 serves the same ggufs from `~/models` with `models-max 5`.
Both servers share unified memory, so during experiments either stop the global
one (`systemctl --user stop llamad`) or keep the loaded quants exclusive between
the two. Running both with all quants resident can exhaust RAM and trigger the
cgroup OOM killer on the project unit (which is why it is capped).
