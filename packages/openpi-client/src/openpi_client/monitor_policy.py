import typing
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
        result = self._policy.infer(obs)
        if "actions" in result:
            actions = np.asarray(result["actions"], dtype=np.float32)
            processed_actions = self._monitor.process_actions(actions, record_idx=self._record_idx)
            result["actions"] = processed_actions
            self._record_idx += 1
        return result

    @override
    def reset(self) -> None:
        self._policy.reset()
