// Win32 frontend: GDI (StretchDIBits) video + waveOut audio. No dependencies
// beyond user32/gdi32/winmm/comdlg32. Talks to the core only via adapter/gb_api.h.
#define WIN32_LEAN_AND_MEAN
#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
#include <mmsystem.h>
#include <mmreg.h>
#include <commdlg.h>
#include <cstdio>
#include <cstdint>
#include <vector>
#include <string>
#include "gb_api.h"

static const uint32_t PALETTE[4] = { 0xFFE0F8D0, 0xFF88C070, 0xFF346856, 0xFF081820 };
static const int SCALE = 4;

static gb_handle* g_gb = nullptr;
static uint32_t   g_pixels[GB_SCREEN_W * GB_SCREEN_H];
static uint8_t    g_buttons = 0, g_dpad = 0;
static bool       g_running = true;
static bool       g_paused = false;
static std::string g_save_path;

// ---- battery saves ----------------------------------------------------------

static void save_battery() {
    if (!g_gb || !gb_has_battery(g_gb) || g_save_path.empty()) return;
    size_t len = 0;
    const uint8_t* data = gb_save_ram(g_gb, &len);
    if (!data || !len) return;
    FILE* f = fopen(g_save_path.c_str(), "wb");
    if (f) { fwrite(data, 1, len, f); fclose(f); }
}

static void load_battery() {
    if (!g_gb || !gb_has_battery(g_gb) || g_save_path.empty()) return;
    FILE* f = fopen(g_save_path.c_str(), "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    std::vector<uint8_t> data(n > 0 ? n : 0);
    if (n > 0 && fread(data.data(), 1, n, f) == (size_t)n)
        gb_load_save_ram(g_gb, data.data(), data.size());
    fclose(f);
}

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

// ---- input -------------------------------------------------------------------

static void handle_key(WPARAM vk, bool down) {
    uint8_t bb = 0, dd = 0;
    switch (vk) {
        case 'Z':          bb = GB_BTN_A;      break;
        case 'X':          bb = GB_BTN_B;      break;
        case VK_RETURN:    bb = GB_BTN_START;  break;
        case VK_BACK:
        case VK_RSHIFT:
        case VK_SHIFT:     bb = GB_BTN_SELECT; break;
        case VK_RIGHT:     dd = GB_PAD_RIGHT;  break;
        case VK_LEFT:      dd = GB_PAD_LEFT;   break;
        case VK_UP:        dd = GB_PAD_UP;     break;
        case VK_DOWN:      dd = GB_PAD_DOWN;   break;
        case 'P':          if (down) g_paused = !g_paused; return;
        case VK_ESCAPE:    if (down) g_running = false;    return;
        default: return;
    }
    if (down) { g_buttons |= bb; g_dpad |= dd; }
    else      { g_buttons &= ~bb; g_dpad &= ~dd; }
    gb_set_input(g_gb, g_buttons, g_dpad);
}

// ---- window ------------------------------------------------------------------

static void paint(HWND hwnd) {
    PAINTSTRUCT ps;
    HDC dc = BeginPaint(hwnd, &ps);
    RECT rc; GetClientRect(hwnd, &rc);
    BITMAPINFO bmi = {};
    bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth = GB_SCREEN_W;
    bmi.bmiHeader.biHeight = -GB_SCREEN_H;           // top-down
    bmi.bmiHeader.biPlanes = 1;
    bmi.bmiHeader.biBitCount = 32;
    bmi.bmiHeader.biCompression = BI_RGB;
    StretchDIBits(dc, 0, 0, rc.right, rc.bottom,
                  0, 0, GB_SCREEN_W, GB_SCREEN_H,
                  g_pixels, &bmi, DIB_RGB_COLORS, SRCCOPY);
    EndPaint(hwnd, &ps);
}

static LRESULT CALLBACK wnd_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_PAINT:      paint(hwnd); return 0;
        case WM_KEYDOWN:    if (!(lp & (1 << 30))) handle_key(wp, true); return 0;
        case WM_KEYUP:      handle_key(wp, false); return 0;
        case WM_CLOSE:
        case WM_DESTROY:    g_running = false; return 0;
        case WM_ERASEBKGND: return 1;
    }
    return DefWindowProcA(hwnd, msg, wp, lp);
}

static std::string pick_rom() {
    char path[MAX_PATH] = "";
    OPENFILENAMEA ofn = {};
    ofn.lStructSize = sizeof(ofn);
    ofn.lpstrFilter = "Game Boy ROMs (*.gb;*.gbc)\0*.gb;*.gbc\0All files\0*.*\0";
    ofn.lpstrFile = path;
    ofn.nMaxFile = MAX_PATH;
    ofn.Flags = OFN_FILEMUSTEXIST;
    return GetOpenFileNameA(&ofn) ? path : "";
}

int main(int argc, char** argv) {
    SetProcessDPIAware();                            // 1 GB pixel = SCALE real pixels
    std::string rom_path = argc > 1 ? argv[1] : pick_rom();
    if (rom_path.empty()) return 0;

    FILE* f = fopen(rom_path.c_str(), "rb");
    if (!f) { MessageBoxA(nullptr, "Cannot open ROM file.", "gbemu", MB_ICONERROR); return 1; }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    std::vector<uint8_t> rom(n);
    fread(rom.data(), 1, n, f); fclose(f);

    g_gb = gb_create();
    if (!gb_load_rom(g_gb, rom.data(), rom.size())) {
        MessageBoxA(nullptr, "Not a valid Game Boy ROM.", "gbemu", MB_ICONERROR);
        return 1;
    }
    size_t dot = rom_path.find_last_of('.');
    g_save_path = (dot == std::string::npos ? rom_path : rom_path.substr(0, dot)) + ".sav";
    load_battery();

    char title[64] = "gbemu";
    char rom_title[17];
    gb_rom_title(g_gb, rom_title);
    if (rom_title[0]) snprintf(title, sizeof(title), "gbemu - %s", rom_title);

    WNDCLASSA wc = {};
    wc.lpfnWndProc = wnd_proc;
    wc.hInstance = GetModuleHandleA(nullptr);
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.lpszClassName = "gbemu_wnd";
    RegisterClassA(&wc);

    RECT rc = { 0, 0, GB_SCREEN_W * SCALE, GB_SCREEN_H * SCALE };
    AdjustWindowRect(&rc, WS_OVERLAPPEDWINDOW, FALSE);
    HWND hwnd = CreateWindowA("gbemu_wnd", title, WS_OVERLAPPEDWINDOW,
                              CW_USEDEFAULT, CW_USEDEFAULT,
                              rc.right - rc.left, rc.bottom - rc.top,
                              nullptr, nullptr, wc.hInstance, nullptr);
    ShowWindow(hwnd, SW_SHOW);

    g_audio.init();
    timeBeginPeriod(1);

    LARGE_INTEGER qpf, next, now;
    QueryPerformanceFrequency(&qpf);
    QueryPerformanceCounter(&next);
    const double frame_ticks = qpf.QuadPart * 70224.0 / 4194304.0;   // 59.7275 Hz
    double next_t = (double)next.QuadPart;
    DWORD last_save = GetTickCount();

    while (g_running) {
        MSG msg;
        while (PeekMessageA(&msg, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageA(&msg);
        }
        if (!g_paused) {
            gb_run_frame(g_gb);
            gb_framebuffer_argb(g_gb, g_pixels, PALETTE);
            InvalidateRect(hwnd, nullptr, FALSE);
            g_audio.pump();
        }
        if (GetTickCount() - last_save > 5000) { save_battery(); last_save = GetTickCount(); }

        next_t += frame_ticks;
        QueryPerformanceCounter(&now);
        double wait_ms = (next_t - now.QuadPart) * 1000.0 / qpf.QuadPart;
        if (wait_ms > 2.0)      Sleep((DWORD)(wait_ms - 1.0));
        else if (wait_ms < -100.0) next_t = (double)now.QuadPart;    // fell behind: resync
        do { QueryPerformanceCounter(&now); } while (now.QuadPart < next_t);
    }

    save_battery();
    timeEndPeriod(1);
    g_audio.close();
    DestroyWindow(hwnd);
    gb_destroy(g_gb);
    return 0;
}
