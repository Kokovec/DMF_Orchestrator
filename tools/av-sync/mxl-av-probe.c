// mxl-av-probe: log MXL-clock timestamps of white video frames (flash) and
// audio onsets (beep) in a video+audio flow pair, for A/V sync measurement.
// usage: mxl-av-probe <domain> <videoFlowId> <audioFlowId> <seconds>
// prints "F <ns>" per flash start and "B <ns>" per beep onset (MXL TAI ns).
#include <mxl/mxl.h>
#include <mxl/flow.h>
#include <mxl/flowinfo.h>
#include <mxl/time.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static double v210_mean(const uint8_t *p, size_t n) {  // mean of 10-bit components
    const uint32_t *w = (const uint32_t *)p; size_t words = n / 4, step = words / 20000 + 1; double s = 0; size_t k = 0;
    for (size_t i = 0; i < words; i += step) { uint32_t x = w[i]; s += (x & 0x3ff) + ((x >> 10) & 0x3ff) + ((x >> 20) & 0x3ff); k += 3; }
    return k ? s / k : 0;
}

int main(int argc, char **argv) {
    if (argc < 5) { fprintf(stderr, "usage: %s domain videoId audioId seconds\n", argv[0]); return 2; }
    mxlInstance inst = mxlCreateInstance(argv[1], "");
    if (!inst) { fprintf(stderr, "instance failed\n"); return 1; }
    mxlFlowReader vr, ar;
    if (mxlCreateFlowReader(inst, argv[2], "", &vr) != MXL_STATUS_OK || mxlCreateFlowReader(inst, argv[3], "", &ar) != MXL_STATUS_OK) { fprintf(stderr, "reader failed\n"); return 1; }
    mxlFlowConfigInfo vc, ac; mxlFlowReaderGetConfigInfo(vr, &vc); mxlFlowReaderGetConfigInfo(ar, &ac);
    mxlRational vRate = vc.common.grainRate, aRate = ac.common.grainRate;
    fprintf(stderr, "video rate %lld/%lld, audio rate %lld/%lld\n", (long long)vRate.numerator, (long long)vRate.denominator, (long long)aRate.numerator, (long long)aRate.denominator);
    // Start from each flow's actual head (writers may run offset from wall-clock time).
    mxlFlowRuntimeInfo vrt, art; mxlFlowReaderGetRuntimeInfo(vr, &vrt); mxlFlowReaderGetRuntimeInfo(ar, &art);
    uint64_t vIdx = vrt.headIndex - 2, aIdx = art.headIndex - 4800;
    fprintf(stderr, "head offsets vs wall clock: video %lld grains, audio %lld samples\n", (long long)vrt.headIndex - (long long)mxlGetCurrentIndex(&vRate), (long long)art.headIndex - (long long)mxlGetCurrentIndex(&aRate));
    uint64_t end = mxlGetTime() + (uint64_t)atoll(argv[4]) * 1000000000ULL;
    int inFlash = 0; long quiet = 4800; const size_t chunk = 480;
    double acc = 0; long accN = 0; long okReads = 0, errReads = 0, lastErr = 0; float peak = 0; uint64_t nextStat = mxlGetTime() + 1000000000ULL;
    while (mxlGetTime() < end) {
        // video: catch up to the head
        for (int n = 0; n < 4; n++) {
            mxlGrainInfo gi; uint8_t *pl = NULL; memset(&gi, 0, sizeof gi);
            mxlStatus st = mxlFlowReaderGetGrain(vr, vIdx, 5000000, &gi, &pl);
            if (st == MXL_STATUS_OK) {
                int white = v210_mean(pl, gi.grainSize) > 500.0;
                if (white && !inFlash) printf("F %llu\n", (unsigned long long)mxlIndexToTimestamp(&vRate, vIdx));
                inFlash = white; vIdx++;
            } else if (st == MXL_ERR_OUT_OF_RANGE_TOO_LATE) { mxlFlowRuntimeInfo r; mxlFlowReaderGetRuntimeInfo(vr, &r); vIdx = r.headIndex - 2; fprintf(stderr, "video too late, resync\n"); }
            else break;
        }
        // audio: consume 10 ms chunks while available
        for (int n = 0; n < 8; n++) {
            mxlWrappedMultiBufferSlice sl; memset(&sl, 0, sizeof sl);
            // MXL sample ranges are [index - count, index): ask for the end index.
            mxlStatus st = mxlFlowReaderGetSamples(ar, aIdx + chunk, chunk, 5000000, &sl);
            if (st == MXL_STATUS_OK) {
                size_t idx = 0;
                for (int f = 0; f < 2; f++) {
                    const float *x = (const float *)sl.base.fragments[f].pointer; size_t cnt = sl.base.fragments[f].size / sizeof(float);
                    for (size_t i = 0; i < cnt; i++, idx++) {
                        float a = x[i]; if (fabsf(a) > peak) peak = fabsf(a);
                        uint64_t si = aIdx + idx; acc += (double)a * a; accN++;
                        if (si % 48 == 47) { printf("A %llu %.6f\n", (unsigned long long)mxlIndexToTimestamp(&aRate, si - 47), sqrt(acc / accN)); acc = 0; accN = 0; }
                    }
                }
                aIdx += chunk; okReads++;
            } else if (st == MXL_ERR_OUT_OF_RANGE_TOO_LATE) { mxlFlowRuntimeInfo r; mxlFlowReaderGetRuntimeInfo(ar, &r); aIdx = r.headIndex - 4800; quiet = 0; fprintf(stderr, "audio too late, resync\n"); }
            else { errReads++; lastErr = st; break; }
        }
        if (mxlGetTime() > nextStat) { fprintf(stderr, "audio: ok=%ld err=%ld lastErr=%ld peak=%.3f\n", okReads, errReads, lastErr, peak); okReads = errReads = 0; peak = 0; nextStat += 1000000000ULL; }
        fflush(stdout);
    }
    mxlReleaseFlowReader(inst, vr); mxlReleaseFlowReader(inst, ar); mxlDestroyInstance(inst);
    return 0;
}
