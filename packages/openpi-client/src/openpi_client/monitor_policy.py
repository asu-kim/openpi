import typing
import time

import numpy as np
from typing_extensions import override
from openpi_client import base_policy as _base_policy


class MonitorPolicyWrapper(_base_policy.BasePolicy):
    """Wraps a BasePolicy to route actions through an ActionMonitor before execution."""

    def __init__(self, policy: _base_policy.BasePolicy, monitor: typing.Any):
        self._policy = policy
        self._monitor = monitor
        self._record_idx = 0

    @override
    def infer(self, obs: typing.Dict) -> typing.Dict:
        observation_timestamp_ms = int(time.time() * 1000)
        result = self._policy.infer(obs)
        if "actions" in result:
            actions = np.asarray(result["actions"], dtype=np.float32)
            current_state = np.asarray(obs["state"], dtype=np.float32) if "state" in obs else None
            processed_actions = self._monitor.process_actions(
                actions,
                record_idx=self._record_idx,
                current_state=current_state,
                observation_timestamp_ms=observation_timestamp_ms,
            )
            result["actions"] = processed_actions
            if self._monitor.current_delivery_record_id is not None:
                # The broker carries this scalar on all H ticks so the actuator can
                # consume only the globally ordered record expected by the runtime.
                result["action_record_id"] = self._monitor.current_delivery_record_id
            self._record_idx += 1
        return result

    @override
    def reset(self) -> None:
        self._policy.reset()
