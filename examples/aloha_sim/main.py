import dataclasses
import logging
import os
import pathlib

import env as _env
from openpi_client import action_chunk_broker
from openpi_client import websocket_client_policy as _websocket_client_policy
from openpi_client.runtime import runtime as _runtime
from openpi_client.runtime.agents import policy_agent as _policy_agent
import saver as _saver
import tyro


@dataclasses.dataclass
class Args:
    out_dir: pathlib.Path = pathlib.Path("data/aloha_sim/videos")

    task: str = "gym_aloha/AlohaTransferCube-v0"
    seed: int = 0

    action_horizon: int = 10

    host: str = "0.0.0.0"
    port: int = 8000

    display: bool = False

    # Cap the episode length (number of environment steps). 0 = run until the
    # environment terminates/truncates on its own (~a full episode). The number of
    # logged records is roughly max_episode_steps / action_horizon, since the policy
    # re-infers once per action_horizon steps. Can also be set via the
    # ALOHA_MAX_EPISODE_STEPS env var (which the runtime container forwards).
    max_episode_steps: int = 0


def main(args: Args) -> None:
    # Env var override so episode length can be set through compose/.env without
    # changing the container's hardcoded CMD.
    max_episode_steps = args.max_episode_steps
    if env_val := os.environ.get("ALOHA_MAX_EPISODE_STEPS"):
        max_episode_steps = int(env_val)

    runtime = _runtime.Runtime(
        environment=_env.AlohaSimEnvironment(
            task=args.task,
            seed=args.seed,
        ),
        agent=_policy_agent.PolicyAgent(
            policy=action_chunk_broker.ActionChunkBroker(
                policy=_websocket_client_policy.WebsocketClientPolicy(
                    host=args.host,
                    port=args.port,
                ),
                action_horizon=args.action_horizon,
            )
        ),
        subscribers=[
            _saver.VideoSaver(args.out_dir),
        ],
        max_hz=50,
        max_episode_steps=max_episode_steps,
    )

    runtime.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, force=True)
    tyro.cli(main)
