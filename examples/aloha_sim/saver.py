import datetime
import logging
import pathlib

import imageio
import numpy as np
from openpi_client.runtime import subscriber as _subscriber
from typing_extensions import override


class VideoSaver(_subscriber.Subscriber):
    """Saves episode data."""

    def __init__(self, out_dir: pathlib.Path, subsample: int = 1) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        self._out_dir = out_dir
        self._images: list[np.ndarray] = []
        self._subsample = subsample

    @override
    def on_episode_start(self) -> None:
        self._images = []

    @override
    def on_step(self, observation: dict, action: dict) -> None:
        im = observation["images"]["cam_high"]  # [C, H, W]
        im = np.transpose(im, (1, 2, 0))  # [H, W, C]
        self._images.append(im)

    @override
    def on_episode_end(self) -> None:
        # Local-time suffix, matching the FAST token-log filename format
        # (YYYY-MM-DD-HH-MM-SS), so each run/episode writes its own video.
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        out_path = self._out_dir / f"out_{timestamp}.mp4"
        # Disambiguate if multiple episodes finish within the same second.
        dedup = 1
        while out_path.exists():
            out_path = self._out_dir / f"out_{timestamp}_{dedup}.mp4"
            dedup += 1

        logging.info(f"Saving video to {out_path}")
        imageio.mimwrite(
            out_path,
            [np.asarray(x) for x in self._images[:: self._subsample]],
            fps=50 // max(1, self._subsample),
        )
