# OpenPI scripts

This directory contains the command-line entry points for preparing data, training models, serving policies, and supporting project workflows.

Run commands from the repository root so that relative paths, configuration files, and output directories resolve correctly.

## Research testing and analysis

The latency experiments, report aggregation, and research plotting tools are documented separately to keep this general script guide focused.

See the [LAMPS 2026 VLA access-control testing guide](lamps_2026/README.md) for the monitor, actuator, test runner, result plotting, threshold calibration, and reproducibility commands.

## Setup

Install the project and its development dependencies before running a script:

```bash
GIT_LFS_SKIP_SMUDGE=1 uv sync
GIT_LFS_SKIP_SMUDGE=1 uv pip install -e .
```

Use `uv run` for model preparation, training, serving, and development commands so that they run inside the managed project environment.

Most scripts expose their current options through `--help`:

```bash
uv run scripts/compute_norm_stats.py --help
uv run scripts/serve_policy.py --help
```

## Script index

| Script | Purpose |
| --- | --- |
| [`compute_norm_stats.py`](compute_norm_stats.py) | Computes state and action normalization statistics for a training configuration |
| [`train.py`](train.py) | Trains an OpenPI model with JAX |
| [`train_pytorch.py`](train_pytorch.py) | Trains an OpenPI model with PyTorch and supports distributed training |
| [`serve_policy.py`](serve_policy.py) | Loads a default or trained policy and exposes it through a WebSocket server |
| [`lamps_2026/`](lamps_2026/) | Contains the LAMPS 2026 monitor, actuator, testing, and plotting workflow |
| [`docker/`](docker/) | Contains the policy-server image, Compose configuration, and host setup helpers |

Files ending in `_test.py` are automated tests, not command-line workflows.

Run them with pytest:

```bash
uv run pytest scripts
```

## Model preparation and training

### Compute normalization statistics

Training configurations are defined in [`src/openpi/training/config.py`](../src/openpi/training/config.py).

Compute statistics after adding a dataset and training configuration:

```bash
uv run scripts/compute_norm_stats.py --config-name pi05_libero
```

The script processes the configured dataset and writes `norm_stats.json` beneath the configuration's assets directory.

For a faster development pass, limit the number of dataset frames:

```bash
uv run scripts/compute_norm_stats.py \
    --config-name pi05_libero \
    --max-frames 10000
```

Recompute these statistics whenever the state representation, action representation, or training data distribution changes.

### Train with JAX

Start a JAX training run by passing a configuration name and a unique experiment name:

```bash
XLA_PYTHON_CLIENT_MEM_FRACTION=0.9 \
uv run scripts/train.py pi05_libero \
    --exp-name=my_experiment \
    --overwrite
```

Checkpoints are written under `checkpoints/<config-name>/<exp-name>/`.

Use `--resume` to continue an existing run and `--overwrite` only when replacing an existing checkpoint directory is intentional.

The training configuration controls data loading, model initialization, checkpoint intervals, device sharding, and Weights & Biases logging.

### Train with PyTorch

Run single-process PyTorch training with:

```bash
uv run scripts/train_pytorch.py debug \
    --exp_name pytorch_test
```

Resume the latest checkpoint for the same configuration and experiment with:

```bash
uv run scripts/train_pytorch.py debug \
    --exp_name pytorch_test \
    --resume
```

For multi-GPU training on one node, launch the script through `torchrun`:

```bash
uv run torchrun \
    --standalone \
    --nnodes=1 \
    --nproc_per_node=2 \
    scripts/train_pytorch.py pi0_aloha_sim \
    --exp_name pytorch_ddp_test
```

For multi-node training, provide `--nnodes`, `--node_rank`, `--master_addr`, and `--master_port` to `torchrun`.

Each node must use the same training configuration and have access to the same dataset and checkpoint storage.

## Serve a policy

Serve the default policy for a supported environment on port 8000:

```bash
uv run scripts/serve_policy.py --env libero
```

Supported environments are `aloha`, `aloha_sim`, `droid`, and `libero`.

Serve a specific trained checkpoint with:

```bash
uv run scripts/serve_policy.py policy:checkpoint \
    --policy.config=pi05_libero \
    --policy.dir=checkpoints/pi05_libero/my_experiment/20000
```

Useful server options include:

- `--port <port>` changes the listening port.
- `--default-prompt <text>` supplies a prompt when an observation does not contain one.
- `--record` writes policy inputs and outputs to `policy_records/` for debugging.

The server binds to all interfaces.

Review firewall and network exposure before serving a policy outside a trusted machine.

See [`docs/remote_inference.md`](../docs/remote_inference.md) for client integration guidance.

## Docker policy server

Build and start the policy server through Compose:

```bash
SERVER_ARGS="policy:checkpoint --policy.config=pi05_libero --policy.dir=/path/to/checkpoint" \
docker compose -f scripts/docker/compose.yml up --build
```

The Compose service mounts the repository at `/app`, mounts the OpenPI asset cache at `/openpi_assets`, uses host networking, and requests one NVIDIA GPU by default.

Edit or override the GPU reservation when running on a host without an NVIDIA GPU.

The Docker helpers are:

- [`docker/serve_policy.Dockerfile`](docker/serve_policy.Dockerfile) builds the policy-server image.
- [`docker/compose.yml`](docker/compose.yml) defines the local server service.
- [`docker/install_docker_ubuntu22.sh`](docker/install_docker_ubuntu22.sh) installs Docker on Ubuntu 22.04.
- [`docker/install_nvidia_container_toolkit.sh`](docker/install_nvidia_container_toolkit.sh) installs NVIDIA container runtime support.

Review installation scripts before running them because they modify host package repositories, services, and user groups.

For broader Docker setup and troubleshooting, see [`docs/docker.md`](../docs/docker.md).

## Related documentation

- [`README.md`](../README.md) covers installation, checkpoints, fine-tuning, and inference.
- [`lamps_2026/README.md`](lamps_2026/README.md) covers the research monitor, actuator, testing, and plotting workflow.
- [`docs/norm_stats.md`](../docs/norm_stats.md) explains normalization-stat reuse.
- [`docs/remote_inference.md`](../docs/remote_inference.md) describes policy-server clients.
- [`openpi_monitor_README.md`](../openpi_monitor_README.md) documents the secure action monitor.
- [`secure_architecture_design.md`](../secure_architecture_design.md) describes the monitor and actuator architecture.
