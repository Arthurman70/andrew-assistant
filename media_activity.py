"""Read Windows output meters only. Never capture audio, titles, or transcripts."""
import threading
import time


class MediaActivity:
    def __init__(self):
        self.until=0
        self.ready=False

    def active(self): return time.monotonic()<self.until

    @staticmethod
    def meters():
        import comtypes
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        from pycaw.constants import EDataFlow, DEVICE_STATE
        result=[]
        for device in AudioUtilities.GetAllDevices(EDataFlow.eRender.value,DEVICE_STATE.ACTIVE.value):
            try:
                meter=device._dev.Activate(IAudioMeterInformation._iid_,comtypes.CLSCTX_ALL,None)
                result.append(meter.QueryInterface(IAudioMeterInformation))
            except Exception: pass
        return result

    def run(self):
        import comtypes
        comtypes.CoInitialize()
        meters=[];refresh=0
        while True:
            try:
                if time.monotonic()>refresh:
                    meters=self.meters();refresh=time.monotonic()+10
                if any(m.GetPeakValue()>.002 for m in meters): self.until=time.monotonic()+5
                self.ready=bool(meters)
            except Exception:
                self.ready=False;refresh=0
            time.sleep(.2)
