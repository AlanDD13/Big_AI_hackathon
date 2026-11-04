"""Small local-receipt-time yaw estimator tolerant of bursty SDK callbacks."""

from collections import deque
import math


class YawRate:
    def __init__(self):
        self.samples = deque(maxlen=64)

    def update(self, stamp, yaw):
        if not math.isfinite(stamp) or not math.isfinite(yaw):
            self.samples.clear()
            return None
        if self.samples and stamp <= self.samples[-1][0]:
            self.samples.clear()
        self.samples.append((stamp, yaw))
        while self.samples and stamp - self.samples[0][0] > 0.35:
            self.samples.popleft()
        if not self.samples or stamp - self.samples[0][0] < 0.15:
            return None
        before, angle = self.samples[0]
        return ((yaw - angle + 180) % 360 - 180) / (stamp - before)
