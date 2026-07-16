// ---- audio (waveOut) ----------------------------------------------------------

struct AudioOut {
    static const int NBUF = 8, FRAMES = 1024;
    HWAVEOUT dev = nullptr;
    WAVEHDR  hdr[NBUF] = {};
    float    data[NBUF][FRAMES * 2] = {};
    bool     ok = false;

    void init() {
        WAVEFORMATEX wf = {};
        wf.wFormatTag = WAVE_FORMAT_IEEE_FLOAT;
        wf.nChannels = 2;
        wf.nSamplesPerSec = GB_AUDIO_RATE;
        wf.wBitsPerSample = 32;
        wf.nBlockAlign = wf.nChannels * wf.wBitsPerSample / 8;
        wf.nAvgBytesPerSec = wf.nSamplesPerSec * wf.nBlockAlign;
        if (waveOutOpen(&dev, WAVE_MAPPER, &wf, 0, 0, CALLBACK_NULL) != MMSYSERR_NOERROR)
            return;
        for (int i = 0; i < NBUF; i++) {
            hdr[i].lpData = (LPSTR)data[i];
            hdr[i].dwBufferLength = FRAMES * 2 * sizeof(float);
            waveOutPrepareHeader(dev, &hdr[i], sizeof(WAVEHDR));
            hdr[i].dwFlags |= WHDR_DONE;             // mark free
        }
        ok = true;
    }
    void pump() {                                    // drain APU into free buffers
        if (!ok) { float sink[FRAMES * 2]; while (gb_read_audio(g_gb, sink, FRAMES) > 0) {} return; }
        for (int i = 0; i < NBUF; i++) {
            if (!(hdr[i].dwFlags & WHDR_DONE)) continue;
            int n = gb_read_audio(g_gb, data[i], FRAMES);
            if (n <= 0) break;
            if (n < FRAMES)                          // pad partial buffer with silence
                memset(data[i] + n * 2, 0, (FRAMES - n) * 2 * sizeof(float));
            hdr[i].dwFlags &= ~WHDR_DONE;
            waveOutWrite(dev, &hdr[i], sizeof(WAVEHDR));
        }
    }
    void close() {
        if (!ok) return;
        waveOutReset(dev);
        for (int i = 0; i < NBUF; i++) waveOutUnprepareHeader(dev, &hdr[i], sizeof(WAVEHDR));
        waveOutClose(dev);
    }
};
static AudioOut g_audio;
