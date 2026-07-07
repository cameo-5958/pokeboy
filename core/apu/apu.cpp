#include "apu.h"
#include <cstring>

static const uint8_t DUTY[4][8] = {
    {0,0,0,0,0,0,0,1}, {1,0,0,0,0,0,0,1}, {1,0,0,0,0,1,1,1}, {0,1,1,1,1,1,1,0},
};

// ---- pulse -----------------------------------------------------------------

void APU::Pulse::trigger(bool is_ch1) {
    enabled = dac;
    if (len == 0) len = 64;
    timer = (2048 - freq) * 4;
    vol = env >> 4;
    env_timer = (env & 7) ? (env & 7) : 8;
    if (is_ch1) {
        shadow = freq;
        int period = (sweep >> 4) & 7, shift = sweep & 7;
        sweep_timer = period ? period : 8;
        sweep_on = period || shift;
        if (shift) {                                 // immediate overflow check
            uint16_t nf = shadow >> shift;
            nf = (sweep & 8) ? shadow - nf : shadow + nf;
            if (nf > 2047) enabled = false;
        }
    }
}
void APU::Pulse::step_len(bool) {
    if (len_enable && len > 0 && --len == 0) enabled = false;
}
void APU::Pulse::step_env() {
    int period = env & 7;
    if (!period) return;
    if (--env_timer <= 0) {
        env_timer = period;
        if ((env & 8) && vol < 15) vol++;
        else if (!(env & 8) && vol > 0) vol--;
    }
}
void APU::Pulse::step_sweep(bool& ch_enabled) {
    if (--sweep_timer > 0) return;
    int period = (sweep >> 4) & 7, shift = sweep & 7;
    sweep_timer = period ? period : 8;
    if (!sweep_on || !period) return;
    uint16_t nf = shadow >> shift;
    nf = (sweep & 8) ? shadow - nf : shadow + nf;
    if (nf > 2047) { enabled = false; ch_enabled = false; return; }
    if (shift) {
        shadow = nf; freq = nf;
        uint16_t nf2 = shadow >> shift;
        nf2 = (sweep & 8) ? shadow - nf2 : shadow + nf2;
        if (nf2 > 2047) { enabled = false; ch_enabled = false; }
    }
}
void APU::Pulse::clock(int t) {
    timer -= t;
    while (timer <= 0) {
        timer += (2048 - freq) * 4;
        duty_pos = (duty_pos + 1) & 7;
    }
}
int APU::Pulse::output() const {
    if (!enabled || !dac) return -1;                 // -1 = DAC off / silent digital 0
    return DUTY[duty_len >> 6][duty_pos] ? vol : 0;
}

// ---- wave ------------------------------------------------------------------

void APU::Wave::trigger() {
    enabled = (dac_ctl & 0x80) != 0;
    if (len == 0) len = 256;
    timer = (2048 - freq) * 2;
    pos = 0;
}
void APU::Wave::clock(int t) {
    if (!enabled) return;
    timer -= t;
    while (timer <= 0) {
        timer += (2048 - freq) * 2;
        pos = (pos + 1) & 31;
    }
}
int APU::Wave::output() const {
    if (!enabled || !(dac_ctl & 0x80)) return -1;
    uint8_t s = ram[pos >> 1];
    s = (pos & 1) ? (s & 0xF) : (s >> 4);
    int shift_tab[4] = {4, 0, 1, 2};                 // mute, 100%, 50%, 25%
    return s >> shift_tab[(out_lvl >> 5) & 3];
}

// ---- noise -----------------------------------------------------------------

static int noise_period(uint8_t poly) {
    int r = poly & 7, s = poly >> 4;
    return (r ? r * 16 : 8) << s;
}
void APU::Noise::trigger() {
    enabled = dac;
    if (len == 0) len = 64;
    timer = noise_period(poly);
    lfsr = 0x7FFF;
    vol = env >> 4;
    env_timer = (env & 7) ? (env & 7) : 8;
}
void APU::Noise::step_env() {
    int period = env & 7;
    if (!period) return;
    if (--env_timer <= 0) {
        env_timer = period;
        if ((env & 8) && vol < 15) vol++;
        else if (!(env & 8) && vol > 0) vol--;
    }
}
void APU::Noise::clock(int t) {
    timer -= t;
    while (timer <= 0) {
        timer += noise_period(poly);
        uint16_t bit = (lfsr ^ (lfsr >> 1)) & 1;
        lfsr = (lfsr >> 1) | (bit << 14);
        if (poly & 8) lfsr = (lfsr & ~0x40) | (bit << 6);
    }
}
int APU::Noise::output() const {
    if (!enabled || !dac) return -1;
    return (~lfsr & 1) ? vol : 0;
}

// ---- frame sequencer / mixing ----------------------------------------------

void APU::frame_seq_step() {
    bool dummy = true;
    if ((fs_step & 1) == 0) {                        // length: steps 0 2 4 6
        ch1.step_len(true); ch2.step_len(true);
        if (ch3.len_enable && ch3.len > 0 && --ch3.len == 0) ch3.enabled = false;
        if (ch4.len_enable && ch4.len > 0 && --ch4.len == 0) ch4.enabled = false;
    }
    if (fs_step == 2 || fs_step == 6) ch1.step_sweep(dummy);   // sweep
    if (fs_step == 7) { ch1.step_env(); ch2.step_env(); ch4.step_env(); }  // envelope
    fs_step = (fs_step + 1) & 7;
}

void APU::mix_sample() {
    int dig[4] = { ch1.output(), ch2.output(), ch3.output(), ch4.output() };
    float L = 0, R = 0;
    for (int i = 0; i < 4; i++) {
        if (dig[i] < 0) continue;                    // DAC off contributes 0 analog
        float v = dig[i] / 7.5f - 1.0f;
        if (nr51 & (1 << (i + 4))) L += v;
        if (nr51 & (1 << i))      R += v;
    }
    L *= (((nr50 >> 4) & 7) + 1) / 8.0f * 0.25f;
    R *= ((nr50 & 7) + 1) / 8.0f * 0.25f;
    if (wpos - rpos < (uint32_t)BUF_FRAMES) {        // drop when full
        uint32_t i = (wpos++ & (BUF_FRAMES - 1)) * 2;
        buf[i] = L; buf[i + 1] = R;
    }
}

void APU::tick(int tcycles) {
    if (!power) {
        // sample clock still runs so the frontend gets (silent) audio
        sample_counter += tcycles;
        const double per = 4194304.0 / SAMPLE_RATE;
        while (sample_counter >= per) { sample_counter -= per; mix_sample(); }
        return;
    }
    ch1.clock(tcycles); ch2.clock(tcycles); ch3.clock(tcycles); ch4.clock(tcycles);
    fs_counter += tcycles;
    while (fs_counter >= 8192) { fs_counter -= 8192; frame_seq_step(); }
    sample_counter += tcycles;
    const double per = 4194304.0 / SAMPLE_RATE;
    while (sample_counter >= per) { sample_counter -= per; mix_sample(); }
}

int APU::drain(float* stereo, int max_frames) {
    int n = 0;
    while (n < max_frames && rpos != wpos) {
        uint32_t i = (rpos++ & (BUF_FRAMES - 1)) * 2;
        stereo[n * 2] = buf[i]; stereo[n * 2 + 1] = buf[i + 1];
        n++;
    }
    return n;
}

// ---- registers ---------------------------------------------------------------

void APU::reset_regs() {
    ch1 = Pulse{}; ch2 = Pulse{}; ch3 = Wave{}; ch4 = Noise{};
    nr50 = 0; nr51 = 0;
    memset(raw, 0, sizeof(raw));
}

uint8_t APU::read_reg(uint16_t a) {
    static const uint8_t mask[0x20] = {              // OR-mask for FF10-FF2F
        0x80,0x3F,0x00,0xFF,0xBF, 0xFF,0x3F,0x00,0xFF,0xBF,
        0x7F,0xFF,0x9F,0xFF,0xBF, 0xFF,0xFF,0x00,0x00,0xBF,
        0x00,0x00,0x70, 0xFF,0xFF,0xFF,0xFF,0xFF,0xFF,0xFF,0xFF,0xFF,
    };
    if (a >= 0xFF30) return ch3.ram[a - 0xFF30];
    int i = a - 0xFF10;
    if (a == 0xFF26) {
        uint8_t v = 0x70 | (power ? 0x80 : 0);
        if (ch1.enabled) v |= 1;
        if (ch2.enabled) v |= 2;
        if (ch3.enabled) v |= 4;
        if (ch4.enabled) v |= 8;
        return v;
    }
    return raw[i] | mask[i];
}

void APU::write_reg(uint16_t a, uint8_t v) {
    if (a >= 0xFF30) { ch3.ram[a - 0xFF30] = v; return; }
    if (a == 0xFF26) {
        bool on = v & 0x80;
        if (power && !on) reset_regs();
        power = on;
        return;
    }
    if (!power) return;                              // registers locked while off
    raw[a - 0xFF10] = v;
    switch (a) {
        case 0xFF10: ch1.sweep = v; break;
        case 0xFF11: ch1.duty_len = v; ch1.len = 64 - (v & 0x3F); break;
        case 0xFF12: ch1.env = v; ch1.dac = (v & 0xF8) != 0; if (!ch1.dac) ch1.enabled = false; break;
        case 0xFF13: ch1.freq = (ch1.freq & 0x700) | v; break;
        case 0xFF14: ch1.freq = (ch1.freq & 0xFF) | ((v & 7) << 8);
                     ch1.len_enable = v & 0x40;
                     if (v & 0x80) ch1.trigger(true);
                     break;
        case 0xFF16: ch2.duty_len = v; ch2.len = 64 - (v & 0x3F); break;
        case 0xFF17: ch2.env = v; ch2.dac = (v & 0xF8) != 0; if (!ch2.dac) ch2.enabled = false; break;
        case 0xFF18: ch2.freq = (ch2.freq & 0x700) | v; break;
        case 0xFF19: ch2.freq = (ch2.freq & 0xFF) | ((v & 7) << 8);
                     ch2.len_enable = v & 0x40;
                     if (v & 0x80) ch2.trigger(false);
                     break;
        case 0xFF1A: ch3.dac_ctl = v; if (!(v & 0x80)) ch3.enabled = false; break;
        case 0xFF1B: ch3.len_reg = v; ch3.len = 256 - v; break;
        case 0xFF1C: ch3.out_lvl = v; break;
        case 0xFF1D: ch3.freq = (ch3.freq & 0x700) | v; break;
        case 0xFF1E: ch3.freq = (ch3.freq & 0xFF) | ((v & 7) << 8);
                     ch3.len_enable = v & 0x40;
                     if (v & 0x80) ch3.trigger();
                     break;
        case 0xFF20: ch4.len_reg = v; ch4.len = 64 - (v & 0x3F); break;
        case 0xFF21: ch4.env = v; ch4.dac = (v & 0xF8) != 0; if (!ch4.dac) ch4.enabled = false; break;
        case 0xFF22: ch4.poly = v; break;
        case 0xFF23: ch4.len_enable = v & 0x40;
                     if (v & 0x80) ch4.trigger();
                     break;
        case 0xFF24: nr50 = v; break;
        case 0xFF25: nr51 = v; break;
        default: break;
    }
}
