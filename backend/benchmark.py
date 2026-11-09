"""Small repeatable local benchmark; reports actual measurements when run."""
import json, os, platform, time
import pandas as pd
from app.analysis import analyze
from app.profiling import profile

ROWS=100_000
frame=pd.DataFrame({"product":[f"product_{i%100}" for i in range(ROWS)],"revenue":[float((i*17)%1000) for i in range(ROWS)],"units":[i%20+1 for i in range(ROWS)]})
measurements={"python":platform.python_version(),"platform":platform.platform(),"rows":ROWS}
for name,fn in [("profile",lambda:profile(frame)),("analysis",lambda:analyze(frame,"total revenue by product"))]:
    start=time.perf_counter(); fn(); measurements[name+"_seconds"]=round(time.perf_counter()-start,4)
if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[("cb",wintypes.DWORD),("PageFaultCount",wintypes.DWORD),("PeakWorkingSetSize",ctypes.c_size_t),
                  ("WorkingSetSize",ctypes.c_size_t),("QuotaPeakPagedPoolUsage",ctypes.c_size_t),
                  ("QuotaPagedPoolUsage",ctypes.c_size_t),("QuotaPeakNonPagedPoolUsage",ctypes.c_size_t),
                  ("QuotaNonPagedPoolUsage",ctypes.c_size_t),("PagefileUsage",ctypes.c_size_t),
                  ("PeakPagefileUsage",ctypes.c_size_t)]
    counters=Counters(); counters.cb=ctypes.sizeof(counters)
    get_memory=ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    get_memory.restype=wintypes.BOOL
    get_memory(wintypes.HANDLE(-1),ctypes.byref(counters),counters.cb)
    measurements["peak_rss_mb"]=round(counters.PeakWorkingSetSize/1024**2,2)
else:
    import resource
    measurements["peak_rss_mb"]=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2)
print(json.dumps(measurements,indent=2))
