// emulator/linux/main_linux.cpp — Pokeboy device frontend for Linux (Buildroot on the OSD3358).
//
//   gbemu_linux rom.gbc [--fb /dev/fb0] [--input /dev/input/eventN] [--audio card,device|none]
//                       [--save path.sav] [--bench N] [--margin-us N]
//
// One process, one thread. Per frame (spec §6.1): run_frame → blit → audio →
// ai_step(absolute deadline) → sleep until the next 16.742 ms tick. The AI
// slice is whatever is left of the frame; a frame may give it nothing.
#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <fcntl.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <sched.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#ifdef POKEBOY_TINYALSA
#include <tinyalsa/pcm.h>
#endif

#include "core.h"
#include "gb_api.h"

namespace {

constexpr int64_t FRAME_NS = 16742706;           // 70224 T-cycles at 4194304 Hz
constexpr int GB_W = 160, GB_H = 144;
constexpr int AUDIO_RATE = 44100;

volatile sig_atomic_t g_stop = 0;
void on_signal(int) { g_stop = 1; }

int64_t now_ns() {
    timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return int64_t(ts.tv_sec) * 1000000000 + ts.tv_nsec;
}

void sleep_until(int64_t t) {
    timespec ts{time_t(t / 1000000000), long(t % 1000000000)};
    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, nullptr) == EINTR && !g_stop) {}
}

// ---------------------------------------------------------------- framebuffer
struct Framebuffer {
    int fd = -1; uint8_t* mem = nullptr; size_t size = 0;
    fb_var_screeninfo v{}; fb_fix_screeninfo f{};
    std::vector<int> xmap, ymap;   // nearest-neighbour source column/row per target pixel
    int x0 = 0, y0 = 0, w = 0, h = 0;
    uint32_t pal[4]{};

    bool open(const char* path) {
        fd = ::open(path, O_RDWR);
        if (fd < 0) { perror(path); return false; }
        if (ioctl(fd, FBIOGET_VSCREENINFO, &v) || ioctl(fd, FBIOGET_FSCREENINFO, &f)) { perror("fb ioctl"); return false; }
        size = size_t(f.line_length) * v.yres;
        mem = static_cast<uint8_t*>(mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0));
        if (mem == MAP_FAILED) { perror("fb mmap"); mem = nullptr; return false; }
        // Integer-free nearest scaling that fills the shorter axis, centred.
        const double s = std::min(double(v.xres) / GB_W, double(v.yres) / GB_H);
        w = int(GB_W * s); h = int(GB_H * s); x0 = (int(v.xres) - w) / 2; y0 = (int(v.yres) - h) / 2;
        xmap.resize(w); ymap.resize(h);
        for (int x = 0; x < w; ++x) xmap[x] = std::min(GB_W - 1, int(x / s));
        for (int y = 0; y < h; ++y) ymap[y] = std::min(GB_H - 1, int(y / s));
        static const uint8_t shades[4][3] = {{0x9b, 0xbc, 0x0f}, {0x8b, 0xac, 0x0f}, {0x30, 0x62, 0x30}, {0x0f, 0x38, 0x0f}};
        for (int i = 0; i < 4; ++i) pal[i] = pack(shades[i][0], shades[i][1], shades[i][2]);
        memset(mem, 0, size);
        fprintf(stderr, "fb: %ux%u %ubpp line %u, image %dx%d at %d,%d\n", v.xres, v.yres, v.bits_per_pixel, f.line_length, w, h, x0, y0);
        return true;
    }
    uint32_t pack(uint8_t r, uint8_t g, uint8_t b) const {
        if (v.bits_per_pixel == 16) return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3);
        return (uint32_t(r) << v.red.offset) | (uint32_t(g) << v.green.offset) | (uint32_t(b) << v.blue.offset);
    }
    void blit(const uint8_t* fb) {
        if (!mem) return;
        for (int y = 0; y < h; ++y) {
            uint8_t* row = mem + size_t(y0 + y) * f.line_length + size_t(x0) * (v.bits_per_pixel / 8);
            const uint8_t* src = fb + ymap[y] * GB_W;
            if (v.bits_per_pixel == 16) {
                uint16_t* p = reinterpret_cast<uint16_t*>(row);
                for (int x = 0; x < w; ++x) p[x] = uint16_t(pal[src[xmap[x]] & 3]);
            } else {
                uint32_t* p = reinterpret_cast<uint32_t*>(row);
                for (int x = 0; x < w; ++x) p[x] = pal[src[xmap[x]] & 3];
            }
        }
    }
    ~Framebuffer() { if (mem) munmap(mem, size); if (fd >= 0) close(fd); }
};

// ---------------------------------------------------------------- input (evdev)
struct Input {
    int fd = -1; uint8_t buttons = 0, dpad = 0;
    bool open(const char* path) {
        fd = ::open(path, O_RDONLY | O_NONBLOCK);
        if (fd < 0) { perror(path); return false; }
        char name[64] = "?"; ioctl(fd, EVIOCGNAME(sizeof name), name);
        fprintf(stderr, "input: %s (%s)\n", path, name);
        return true;
    }
    static bool map(int code, uint8_t& mask, bool& is_dpad) {
        is_dpad = false;
        switch (code) {
            case KEY_Z: case BTN_SOUTH: mask = GB_BTN_A; return true;
            case KEY_X: case BTN_EAST: mask = GB_BTN_B; return true;
            case KEY_ENTER: case BTN_START: mask = GB_BTN_START; return true;
            case KEY_SPACE: case KEY_RIGHTSHIFT: case BTN_SELECT: mask = GB_BTN_SELECT; return true;
            case KEY_UP: case BTN_DPAD_UP: mask = GB_PAD_UP; is_dpad = true; return true;
            case KEY_DOWN: case BTN_DPAD_DOWN: mask = GB_PAD_DOWN; is_dpad = true; return true;
            case KEY_LEFT: case BTN_DPAD_LEFT: mask = GB_PAD_LEFT; is_dpad = true; return true;
            case KEY_RIGHT: case BTN_DPAD_RIGHT: mask = GB_PAD_RIGHT; is_dpad = true; return true;
        }
        return false;
    }
    void poll() {
        if (fd < 0) return;
        input_event ev[32];
        for (;;) {
            const ssize_t n = read(fd, ev, sizeof ev);
            if (n <= 0) break;
            for (size_t i = 0; i < size_t(n) / sizeof(input_event); ++i) {
                if (ev[i].type != EV_KEY) continue;
                uint8_t mask; bool dp;
                if (!map(ev[i].code, mask, dp)) continue;
                uint8_t& target = dp ? dpad : buttons;
                if (ev[i].value) target |= mask; else target &= ~mask;
            }
        }
    }
    ~Input() { if (fd >= 0) close(fd); }
};

// ---------------------------------------------------------------- audio
struct Audio {
    virtual ~Audio() = default;
    virtual void push(const float* stereo, int frames) = 0;
};
struct NullAudio : Audio { void push(const float*, int) override {} };
#ifdef POKEBOY_TINYALSA
struct TinyAlsaAudio : Audio {
    pcm* p = nullptr; std::vector<int16_t> buf;
    bool open(unsigned card, unsigned device) {
        pcm_config cfg{}; cfg.channels = 2; cfg.rate = AUDIO_RATE; cfg.format = PCM_FORMAT_S16_LE;
        cfg.period_size = 736; cfg.period_count = 4;   // ~1 frame per period, ~3 frames queued
        p = pcm_open(card, device, PCM_OUT, &cfg);
        if (!p || !pcm_is_ready(p)) { fprintf(stderr, "audio: %s\n", p ? pcm_get_error(p) : "open failed"); return false; }
        return true;
    }
    void push(const float* stereo, int frames) override {
        if (!p || frames <= 0) return;
        buf.resize(size_t(frames) * 2);
        for (size_t i = 0; i < buf.size(); ++i) { float v = stereo[i]; v = v < -1 ? -1 : v > 1 ? 1 : v; buf[i] = int16_t(v * 32767); }
        pcm_writei(p, buf.data(), unsigned(frames));   // blocking: the codec clock paces us
    }
    ~TinyAlsaAudio() override { if (p) pcm_close(p); }
};
#endif

// ---------------------------------------------------------------- battery save
bool read_file(const std::string& path, std::vector<uint8_t>& out) {
    FILE* f = fopen(path.c_str(), "rb"); if (!f) return false;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    out.resize(size_t(n)); const size_t got = fread(out.data(), 1, out.size(), f); fclose(f);
    return got == out.size();
}
void write_file(const std::string& path, const uint8_t* data, size_t len) {
    const std::string tmp = path + ".tmp";
    FILE* f = fopen(tmp.c_str(), "wb"); if (!f) return;
    fwrite(data, 1, len, f); fclose(f); rename(tmp.c_str(), path.c_str());
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s rom.gbc [--fb dev] [--input dev] [--audio card,dev|none] [--save path] [--bench N] [--margin-us N]\n", argv[0]); return 1; }
    std::string rom_path = argv[1], fb_path = "/dev/fb0", input_path = "/dev/input/event0", audio = "0,0", save_path;
    long bench = 0, margin_us = 600;
    for (int i = 2; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&]() -> const char* { return i + 1 < argc ? argv[++i] : ""; };
        if (a == "--fb") fb_path = next(); else if (a == "--input") input_path = next(); else if (a == "--audio") audio = next();
        else if (a == "--save") save_path = next(); else if (a == "--bench") bench = atol(next()); else if (a == "--margin-us") margin_us = atol(next());
    }
    if (save_path.empty()) save_path = rom_path + ".sav";

    std::vector<uint8_t> rom;
    if (!read_file(rom_path, rom)) { fprintf(stderr, "cannot read %s\n", rom_path.c_str()); return 1; }
    GameBoy gb;
    if (!gb.load_rom(rom.data(), rom.size())) { fprintf(stderr, "bad rom\n"); return 1; }
    gb.reset_post_boot();
    fprintf(stderr, "ai: %s\n", gb.ai.recognised ? "armed (ROM recognised)" : "disarmed (unknown ROM, native AI)");
    std::vector<uint8_t> sav;
    if (gb.has_battery() && read_file(save_path, sav)) gb.load_save_ram(sav.data(), sav.size());

    signal(SIGINT, on_signal); signal(SIGTERM, on_signal);

    if (bench > 0) {
        // Unoptimised-core timing: emulation only, then emulation + AI slice.
        const int64_t t0 = now_ns();
        for (long i = 0; i < bench; ++i) gb.run_frame();
        const int64_t t1 = now_ns();
        for (long i = 0; i < bench; ++i) { gb.run_frame(); gb.ai_step(now_ns() + 4000000); }
        const int64_t t2 = now_ns();
        printf("bench: %ld frames  emulate %.3f ms/frame  emulate+ai %.3f ms/frame  ai stage costs(ns):", bench,
               double(t1 - t0) / bench / 1e6, double(t2 - t1) / bench / 1e6);
        for (auto c : gb.ai.scheduler.cost_ns) printf(" %llu", (unsigned long long)c);
        printf("\n");
        return 0;
    }

    Framebuffer fb; Input in;
    if (!fb.open(fb_path.c_str())) return 1;
    in.open(input_path.c_str());
    Audio* out = new NullAudio;
#ifdef POKEBOY_TINYALSA
    if (audio != "none") {
        unsigned card = 0, dev = 0; sscanf(audio.c_str(), "%u,%u", &card, &dev);
        auto* ta = new TinyAlsaAudio; if (ta->open(card, dev)) { delete out; out = ta; } else delete ta;
    }
#endif
    // Real-time scheduling and locked memory: best effort.
    sched_param sp{}; sp.sched_priority = 20;
    if (sched_setscheduler(0, SCHED_FIFO, &sp) != 0) fprintf(stderr, "sched: SCHED_FIFO unavailable (%s)\n", strerror(errno));
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) fprintf(stderr, "mlockall: %s\n", strerror(errno));

    std::vector<float> audio_buf(4096);
    int64_t next_tick = now_ns();
    int64_t last_save = next_tick; uint64_t frames = 0, late = 0;
    while (!g_stop) {
        next_tick += FRAME_NS;
        in.poll(); gb.set_input(in.buttons, in.dpad);
        gb.run_frame();
        fb.blit(gb.framebuffer());
        const int n = gb.read_audio(audio_buf.data(), int(audio_buf.size() / 2));
        out->push(audio_buf.data(), n);
        gb.ai_step(next_tick - margin_us * 1000);
        const int64_t t = now_ns();
        if (t > next_tick) { ++late; next_tick = t; } else sleep_until(next_tick);
        if (++frames % 300 == 0 && gb.has_battery() && t - last_save > 5000000000) {
            size_t len = 0; const uint8_t* ram = gb.save_ram(&len);
            if (ram && len) write_file(save_path, ram, len);
            last_save = t;
        }
    }
    if (gb.has_battery()) { size_t len = 0; const uint8_t* ram = gb.save_ram(&len); if (ram && len) write_file(save_path, ram, len); }
    fprintf(stderr, "frames %llu, late %llu, ai completions %llu timeouts %llu\n", (unsigned long long)frames, (unsigned long long)late,
            (unsigned long long)gb.ai.scheduler.completions, (unsigned long long)gb.ai.scheduler.timeouts);
    delete out;
    return 0;
}
