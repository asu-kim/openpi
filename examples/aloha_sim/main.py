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

    # Set the episode length (number of environment steps). This overrides the gym
    # ALOHA default TimeLimit of 300 steps, so it can shorten OR extend an episode.
    # 0 = keep the default. The number of logged records is roughly max_episode_steps
    # / action_horizon, since the policy re-infers once per action_horizon steps. Can
    # also be set via the ALOHA_MAX_EPISODE_STEPS env var (forwarded into the container).
    max_episode_steps: int = 0

    # Number of episodes to run back-to-back. Each episode resets the environment to a
    # fresh (re-seeded) initial state, so this is the preferred way to gather more
    # records without pushing a single episode past its designed horizon. Records are
    # numbered continuously across episodes. Can also be set via ALOHA_NUM_EPISODES.
    num_episodes: int = 1

    # Optional path to IoTAuth configuration file. If provided, the simulation
    # will run the ActionMonitor logic to authorize and filter inferences.
    monitor_config: str | None = None


def main(args: Args) -> None:
    # Env var overrides so these can be set through compose/.env without changing the
    # container's hardcoded CMD.
    max_episode_steps = args.max_episode_steps
    if env_val := os.environ.get("ALOHA_MAX_EPISODE_STEPS"):
        max_episode_steps = int(env_val)

    num_episodes = args.num_episodes
    if env_val := os.environ.get("ALOHA_NUM_EPISODES"):
        num_episodes = int(env_val)

    if env_val := os.environ.get("MONITOR_CONFIG"):
        args.monitor_config = env_val

    base_policy = _websocket_client_policy.WebsocketClientPolicy(
        host=args.host,
        port=args.port,
    )
    
    if args.monitor_config:
        import sys
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
        from openpi_monitor import ActionMonitor
        from openpi_client.monitor_policy import MonitorPolicyWrapper
        
        logging.info(f"Initializing ActionMonitor with config: {args.monitor_config}")
        monitor = ActionMonitor(args.monitor_config)
        policy_to_use = MonitorPolicyWrapper(base_policy, monitor)
    else:
        policy_to_use = base_policy

    runtime = _runtime.Runtime(
        environment=_env.AlohaSimEnvironment(
            task=args.task,
            seed=args.seed,
            # Override the env's default 300-step TimeLimit so episodes can run longer
            # (more records). 0 keeps the default. Mirrored in the runtime cap below.
            max_episode_steps=max_episode_steps,
        ),
        agent=_policy_agent.PolicyAgent(
            policy=action_chunk_broker.ActionChunkBroker(
                policy=policy_to_use,
                action_horizon=args.action_horizon,
            )
        ),
        subscribers=[
            _saver.VideoSaver(args.out_dir),
        ],
        max_hz=50,
        max_episode_steps=max_episode_steps,
        num_episodes=num_episodes,
    )

    runtime.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, force=True)
    tyro.cli(main)
