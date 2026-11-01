// mxl-syncgen: A/V sync test source written straight into an MXL Flow Domain.
//
// Video: black V210 frames, white on every grain whose index is a multiple of 30.
// Audio: float32 silence with a 1 kHz beep for one frame (1602 samples) wherever
//        the sample index modulo 48048 is below 1602.
//
// Both patterns are functions of the absolute MXL index, i.e. of TAI time:
// at 30000/1001 and 48000 Hz, grain 30k and sample 48048k both fall exactly at
// k * 1.001 s. Flash and beep are therefore aligned by construction, from any
// start, with no decoder or pipeline latency involved (a file looper's A/V
// alignment depends on decoder start-up timing and drifts at every loop).
//
// usage: mxl-syncgen -d <domain> -v <video flow json> -a <audio flow json>
// The flow definitions are NMOS JSON as for mxl-gst-testsrc. Video must be
// 30000/1001 V210 progressive, audio 48 kHz float32.
#include <mxl/mxl.h>
#include <mxl/flow.h>
#include <mxl/flowinfo.h>
#include <mxl/time.h>
#include <math.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define FLASH_PERIOD_GRAINS 30
#define BEEP_PERIOD_SAMPLES 48048 // 30 frames at 30000/1001 = 1.001 s at 48 kHz
#define BEEP_SAMPLES 1602         // one frame (1601.6 samples)
#define BEEP_HZ 1000.0
#define BEEP_LEVEL 0.5f           // -6 dBFS
#define AUDIO_CHUNK 480           // write in 10 ms chunks

static volatile sig_atomic_t running = 1;
static void stop(int sig) { (void)sig; running = 0; }

static char *read_file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(1); }
    fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    char *buf = malloc((size_t)n + 1);
    if (!buf || fread(buf, 1, (size_t)n, f) != (size_t)n) { fprintf(stderr, "cannot read %s\n", path); exit(1); }
    buf[n] = 0; fclose(f);
    return buf;
}

static mxlFlowWriter create_writer(mxlInstance inst, const char *path, mxlFlowConfigInfo *cfg) {
    char *def = read_file(path);
    mxlFlowWriter w = NULL; bool created = false;
    if (mxlCreateFlowWriter(inst, def, "", &w, cfg, &created) != MXL_STATUS_OK) {
        fprintf(stderr, "cannot create flow writer for %s\n", path); exit(1);
    }
    free(def);
    return w;
}

// One V210 line (any width) filled with a flat colour: 6 pixels per 16 bytes.
static void v210_fill(uint8_t *line, size_t stride, uint32_t y, uint32_t c) {
    const uint32_t words[4] = { c | y << 10 | c << 20, y | c << 10 | y << 20, c | y << 10 | c << 20, y | c << 10 | y << 20 };
    for (size_t off = 0; off + 16 <= stride; off += 16) memcpy(line + off, words, 16);
}

int main(int argc, char **argv) {
    const char *domain = NULL, *vdef = NULL, *adef = NULL;
    for (int o; (o = getopt(argc, argv, "d:v:a:")) != -1;) {
        if (o == 'd') domain = optarg; else if (o == 'v') vdef = optarg; else if (o == 'a') adef = optarg;
        else { fprintf(stderr, "usage: %s -d domain -v video.json -a audio.json\n", argv[0]); return 2; }
    }
    if (!domain || !vdef || !adef) { fprintf(stderr, "usage: %s -d domain -v video.json -a audio.json\n", argv[0]); return 2; }
    signal(SIGTERM, stop); signal(SIGINT, stop);

    mxlInstance inst = mxlCreateInstance(domain, "");
    if (!inst) { fprintf(stderr, "cannot open MXL domain %s\n", domain); return 1; }
    mxlFlowConfigInfo vc, ac;
    mxlFlowWriter vw = create_writer(inst, vdef, &vc), aw = create_writer(inst, adef, &ac);
    mxlRational vRate = vc.common.grainRate, aRate = ac.common.grainRate;
    if (vRate.numerator != 30000 || vRate.denominator != 1001 || aRate.numerator != 48000 || aRate.denominator != 1) {
        fprintf(stderr, "need 30000/1001 video and 48000 Hz audio (got %lld/%lld, %lld/%lld)\n",
                (long long)vRate.numerator, (long long)vRate.denominator, (long long)aRate.numerator, (long long)aRate.denominator);
        return 1;
    }
    size_t maxSamples = AUDIO_CHUNK;
    mxlFlowWriterGetMaxWriteLengthSamples(aw, &maxSamples);
    size_t chunk = maxSamples < AUDIO_CHUNK ? maxSamples : AUDIO_CHUNK;

    // Pre-render one beep period so the audio loop only copies.
    float *pattern = calloc(BEEP_PERIOD_SAMPLES, sizeof *pattern);
    for (int s = 0; s < BEEP_SAMPLES; s++) pattern[s] = BEEP_LEVEL * (float)sin(2.0 * M_PI * BEEP_HZ * s / 48000.0);

    uint64_t vIdx = mxlGetCurrentIndex(&vRate), aIdx = mxlGetCurrentIndex(&aRate);
    fprintf(stderr, "mxl-syncgen: writing from grain %llu / sample %llu (flash every %d grains, beep every %d samples)\n",
            (unsigned long long)vIdx, (unsigned long long)aIdx, FLASH_PERIOD_GRAINS, BEEP_PERIOD_SAMPLES);

    while (running) {
        uint64_t vNow = mxlGetCurrentIndex(&vRate);
        if (vNow > vIdx + 2) vIdx = vNow - 2; // fell behind (stalled host): skip ahead
        for (; vIdx <= vNow; vIdx++) {
            mxlGrainInfo gi; uint8_t *payload = NULL;
            memset(&gi, 0, sizeof gi);
            if (mxlFlowWriterOpenGrain(vw, vIdx, &gi, &payload) != MXL_STATUS_OK) { fprintf(stderr, "open grain %llu failed\n", (unsigned long long)vIdx); continue; }
            size_t stride = gi.totalSlices ? gi.grainSize / gi.totalSlices : gi.grainSize;
            int white = vIdx % FLASH_PERIOD_GRAINS == 0;
            v210_fill(payload, stride, white ? 940 : 64, 512);
            for (uint16_t l = 1; l < gi.totalSlices; l++) memcpy(payload + l * stride, payload, stride);
            gi.validSlices = gi.totalSlices;
            if (mxlFlowWriterCommitGrain(vw, &gi) != MXL_STATUS_OK) fprintf(stderr, "commit grain %llu failed\n", (unsigned long long)vIdx);
        }

        uint64_t aNow = mxlGetCurrentIndex(&aRate);
        if (aNow > aIdx + 48000) aIdx = aNow - chunk;
        while (aIdx + chunk <= aNow) {
            mxlMutableWrappedMultiBufferSlice sl;
            // MXL sample ranges are [index - count, index): pass the end index.
            if (mxlFlowWriterOpenSamples(aw, aIdx + chunk, chunk, &sl) != MXL_STATUS_OK) { fprintf(stderr, "open samples %llu failed\n", (unsigned long long)aIdx); break; }
            for (size_t ch = 0; ch < sl.count; ch++) {
                uint64_t s = aIdx;
                for (int f = 0; f < 2; f++) {
                    float *dst = (float *)((uint8_t *)sl.base.fragments[f].pointer + ch * sl.stride);
                    size_t n = sl.base.fragments[f].size / sizeof(float);
                    for (size_t i = 0; i < n; i++, s++) dst[i] = pattern[s % BEEP_PERIOD_SAMPLES];
                }
            }
            if (mxlFlowWriterCommitSamples(aw) != MXL_STATUS_OK) fprintf(stderr, "commit samples %llu failed\n", (unsigned long long)aIdx);
            aIdx += chunk;
        }

        uint64_t ns = mxlGetNsUntilIndex(vIdx, &vRate), nsA = mxlGetNsUntilIndex(aIdx + chunk, &aRate);
        mxlSleepForNs(ns < nsA ? ns : nsA);
    }
    mxlReleaseFlowWriter(inst, vw);
    mxlReleaseFlowWriter(inst, aw);
    mxlDestroyInstance(inst);
    return 0;
}
