#pragma once
#include <cstdint>

// Full DMG APU: 2 pulse channels (sweep on 1), wave, noise; 512 Hz frame
// sequencer; float stereo output resampled to 44100 Hz into a ring buffer.
struct APU {
    static constexpr int SAMPLE_RATE = 44100;

    void    tick(int tcycles);
    int     drain(float* stereo, int max_frames);   // returns frames written
    uint8_t read_reg(uint16_t a);
    void    write_reg(uint16_t a, uint8_t v);
    void    reset_regs();                           // post-boot defaults applied by Bus

    // Frontend mix mask: bit n = 0 silences channel n+1 in the output mix
    // (emulation is unaffected). Lets a frontend replace the game's music
    // while passing through channels currently carrying sound effects.
    void    set_out_mask(uint8_t m) { out_mask = m & 0x0F; }

private:
    struct Pulse {
        // registers
        uint8_t sweep = 0, duty_len = 0, env = 0;
        uint16_t freq = 0;                          // 11-bit
        // state
        bool enabled = false, dac = false;
        int  len = 0, duty_pos = 0, timer = 0;
        int  vol = 0, env_timer = 0;
        // sweep (channel 1 only)
        bool sweep_on = false; int sweep_timer = 0; uint16_t shadow = 0;

        void trigger(bool is_ch1);
        void step_len(bool len_enable);
        void step_env();
        void step_sweep(bool& ch_enabled);
        void clock(int t);
        int  output() const;
        bool len_enable = false;
    };
    struct Wave {
        uint8_t dac_ctl = 0, len_reg = 0, out_lvl = 0;
        uint16_t freq = 0;
        bool enabled = false, len_enable = false;
        int  len = 0, pos = 0, timer = 0;
        uint8_t ram[16] = {};
        void trigger();
        void clock(int t);
        int  output() const;
    };
    struct Noise {
        uint8_t len_reg = 0, env = 0, poly = 0;
        bool enabled = false, dac = false, len_enable = false;
        int  len = 0, timer = 0, vol = 0, env_timer = 0;
        uint16_t lfsr = 0x7FFF;
        void trigger();
        void step_env();
        void clock(int t);
        int  output() const;
    };

    Pulse ch1, ch2;
    Wave  ch3;
    Noise ch4;
    uint8_t nr50 = 0x77, nr51 = 0xF3;
    uint8_t out_mask = 0x0F;
    bool power = true;
    uint8_t raw[0x30] = {};                         // last written values, for readback

    int fs_counter = 0, fs_step = 0;                // frame sequencer
    double sample_counter = 0;

    static constexpr int BUF_FRAMES = 32768;        // ring buffer, power of two
    float buf[BUF_FRAMES * 2] = {};
    uint32_t wpos = 0, rpos = 0;                    // in frames

    void frame_seq_step();
    void mix_sample();
};
