#pragma once
struct APU {
    void tick(int) {}
    int  drain(float* out, int max_frames) { (void)out; (void)max_frames; return 0; }
    // TODO(M6): pulse channels, wave, noise, 512 Hz frame sequencer — PDF §9
};
