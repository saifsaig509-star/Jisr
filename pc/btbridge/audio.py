"""Capture everything the PC plays (WASAPI loopback) so it can be streamed to the phone.

Needs:  pip install PyAudioWPatch     (Windows only)
"""
from array import array

try:
    import pyaudiowpatch as pyaudio
except Exception:
    pyaudio = None


def audio_available() -> bool:
    return pyaudio is not None


class LoopbackCapture:
    """Calls on_chunk(bytes) from a PortAudio thread with 16-bit PCM, at most stereo."""

    def __init__(self, on_chunk):
        self.on_chunk = on_chunk
        self.pa = None
        self.stream = None

    def start(self):
        """Returns (sample_rate, channels). Blocking - run in an executor."""
        if pyaudio is None:
            raise RuntimeError("PyAudioWPatch is not installed")
        pa = pyaudio.PyAudio()
        try:
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            dev = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if not dev.get("isLoopbackDevice"):
                for lb in pa.get_loopback_device_info_generator():
                    if dev["name"] in lb["name"]:
                        dev = lb
                        break
                else:
                    raise RuntimeError("No loopback device found for the default output")
            rate = int(dev["defaultSampleRate"])
            src_ch = int(dev["maxInputChannels"])
            out_ch = 1 if src_ch == 1 else 2

            def cb(in_data, frame_count, time_info, status):
                self.on_chunk(self._downmix(in_data, src_ch))
                return (None, pyaudio.paContinue)

            self.stream = pa.open(format=pyaudio.paInt16, channels=src_ch, rate=rate, input=True,
                                  input_device_index=dev["index"], frames_per_buffer=rate // 50,
                                  stream_callback=cb)
        except Exception:
            pa.terminate()
            raise
        self.pa = pa
        return rate, out_ch

    @staticmethod
    def _downmix(data: bytes, src_ch: int) -> bytes:
        if src_ch <= 2:
            return data
        a = array("h")
        a.frombytes(data)
        frames = len(a) // src_ch
        out = array("h", bytes(frames * 4))
        out[0::2] = a[0:frames * src_ch:src_ch]
        out[1::2] = a[1:frames * src_ch:src_ch]
        return out.tobytes()

    def stop(self):
        try:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
        finally:
            self.stream = None
            if self.pa:
                self.pa.terminate()
                self.pa = None
