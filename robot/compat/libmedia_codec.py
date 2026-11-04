"""Local real-media adapter, not DJI's binary codec or a simulated SDK.

Implements the decoder surface used by the pinned official SDK. Live rover
stream compatibility is a separate hardware acceptance check.
"""

import struct

import av


class H264Decoder:
    def __init__(self, output_format="BGR", verbose=True):
        if output_format not in ("BGR", "RGB"):
            raise ValueError("output_format must be BGR or RGB")
        self._format = "bgr24" if output_format == "BGR" else "rgb24"
        self._codec = av.CodecContext.create("h264", "r")

    def decode(self, data):
        """Parse arbitrary stream chunks; return packed pixels and dimensions."""
        frames = []
        for packet in self._codec.parse(data):
            for frame in self._codec.decode(packet):
                pixels = frame.to_ndarray(format=self._format)
                frames.append((pixels.tobytes(), frame.width, frame.height,
                               frame.width * 3))
        return frames


class OpusDecoder:
    def __init__(self, frame_size=960, sample_rate=48000, channels=1):
        if (sample_rate, channels) != (48000, 1):
            raise ValueError("This adapter supports DJI's 48 kHz mono audio only")
        self._codec = av.CodecContext.create("opus", "r")
        self._codec.extradata = (
            b"OpusHead" + struct.pack("<BBHIhB", 1, channels, 0,
                                      sample_rate, 0, 0)
        )
        self._resampler = av.AudioResampler(format="s16", layout="mono",
                                            rate=sample_rate)

    def decode(self, data):
        output = []
        for frame in self._codec.decode(av.Packet(data)):
            for pcm in self._resampler.resample(frame):
                output.append(pcm.to_ndarray().tobytes())
        return b"".join(output)
