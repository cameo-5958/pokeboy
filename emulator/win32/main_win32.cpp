// Win32 frontend: GDI (StretchDIBits) video + waveOut audio + virtual gamepad.
// No dependencies beyond user32/gdi32/winmm/comdlg32.
#define WIN32_LEAN_AND_MEAN
#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
#include <mmsystem.h>
#include <mmreg.h>
#include <commdlg.h>
#include <cstdio>
#include <cstdint>
#include <algorithm>
#include <vector>
#include <string>
#include "gb_api.h"

static const uint32_t PALETTE[4] = { 0xFFE0F8D0, 0xFF88C070, 0xFF346856, 0xFF081820 };
static const int SCALE = 4;

// ---- virtual gamepad layout constants --------------------------------------
static const int VPAD_SIZE  = 120;
static const int VBUTTON_R  = 30;
static const int VMARGIN    = 12;
static const int VGAP       = 16;
static const int MIN_LANDSCAPE_W = 580;

static gb_handle* g_gb = nullptr;
static uint32_t   g_pixels[GB_SCREEN_W * GB_SCREEN_H];
static uint8_t    g_kb_buttons = 0, g_kb_dpad = 0;
static uint8_t    g_mouse_buttons = 0, g_mouse_dpad = 0;
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

#include "win32_audio.inc"

#include "win32_controls.inc"

// ---- window procedure ---------------------------------------------------------

static LRESULT CALLBACK wnd_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_PAINT:
            paint(hwnd);
            return 0;
        case WM_KEYDOWN:
            if (!(lp & (1 << 30))) handle_key(wp, true);
            return 0;
        case WM_KEYUP:
            handle_key(wp, false);
            return 0;
        case WM_LBUTTONDOWN:
        case WM_LBUTTONUP:
        case WM_MOUSEMOVE: {
            int mx = LOWORD(lp), my = HIWORD(lp);
            bool down = (msg == WM_LBUTTONDOWN) || ((wp & MK_LBUTTON) && msg == WM_MOUSEMOVE);
            if (!down) {
                g_mouse_buttons = 0;
                g_mouse_dpad = 0;
            } else if (in_rect(g_layout.dpad, mx, my)) {
                g_mouse_buttons = 0;
                hit_dpad(g_layout.dpad, mx, my);
            } else if (in_rect(g_layout.btnA, mx, my)) {
                g_mouse_dpad = 0;
                set_mouse_btn(GB_BTN_A, true);
                set_mouse_btn(GB_BTN_B, false);
            } else if (in_rect(g_layout.btnB, mx, my)) {
                g_mouse_dpad = 0;
                set_mouse_btn(GB_BTN_A, false);
                set_mouse_btn(GB_BTN_B, true);
            } else {
                g_mouse_buttons = 0;
                g_mouse_dpad = 0;
            }
            sync_input();
            if (down) SetCapture(hwnd); else ReleaseCapture();
            return 0;
        }
        case WM_CAPTURECHANGED:
            g_mouse_buttons = 0;
            g_mouse_dpad = 0;
            sync_input();
            return 0;
        case WM_CLOSE:
        case WM_DESTROY:
            g_running = false;
            return 0;
        case WM_ERASEBKGND: {
            RECT rc; GetClientRect(hwnd, &rc);
            FillRect((HDC)wp, &rc, (HBRUSH)GetStockObject(BLACK_BRUSH));
            return 1;
        }
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

    RECT rc = { 0, 0,
        VPAD_SIZE + VGAP + GB_SCREEN_W * SCALE + VGAP + VBUTTON_R * 2 + VMARGIN * 2,
        (std::max)(VPAD_SIZE, GB_SCREEN_H * SCALE) + VMARGIN * 2 };
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
