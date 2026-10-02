"""Bounded retry delay; monotonic waits are interrupted by the camera stop event."""

import random


def reconnect_delay(settings, failures):
    base = min(
        settings.rtsp_reconnect_max_seconds,
        settings.rtsp_reconnect_initial_seconds * 2 ** min(max(0, failures - 1), 16),
    )
    jitter = settings.rtsp_reconnect_jitter
    return min(settings.rtsp_reconnect_max_seconds, base * random.uniform(1 - jitter, 1 + jitter))
