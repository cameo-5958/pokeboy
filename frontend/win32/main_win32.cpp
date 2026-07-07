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

static void sync_input() {
    gb_set_input(g_gb, g_kb_buttons | g_mouse_buttons, g_kb_dpad | g_mouse_dpad);
}

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
    if (down) { g_kb_buttons |= bb; g_kb_dpad |= dd; }
    else      { g_kb_buttons &= ~bb; g_kb_dpad &= ~dd; }
    sync_input();
}

static void set_mouse_btn(uint8_t mask, bool on) {
    if (on) g_mouse_buttons |= mask;
    else    g_mouse_buttons &= ~mask;
    sync_input();
}

static void set_mouse_dpad(uint8_t mask, bool on) {
    if (on) g_mouse_dpad |= mask;
    else    g_mouse_dpad &= ~mask;
    sync_input();
}

// ---- layout + drawing ---------------------------------------------------------

struct Layout {
    bool landscape;
    RECT screen;
    RECT dpad;
    RECT btnA;
    RECT btnB;
};

static bool  g_last_landscape = true;
static int   g_last_scale = 0;

static Layout calc_layout(int cw, int ch) {
    Layout L = {};
    int pad = VPAD_SIZE;
    int btnD = VBUTTON_R * 2;

    // horizontal space consumed by controls in landscape
    int controls_w = VMARGIN + pad + VGAP + VGAP + btnD + VMARGIN;
    int avail_lw = cw - controls_w;
    if (avail_lw < 0) avail_lw = 0;

    int scale_l = (std::max)(1, (std::min)(avail_lw / GB_SCREEN_W, ch / GB_SCREEN_H));
    int sw_l = GB_SCREEN_W * scale_l;
    int sh_l = GB_SCREEN_H * scale_l;
    bool landscape_fits = (avail_lw >= GB_SCREEN_W && ch >= (std::max)(pad, sh_l) + VMARGIN * 2);

    // hysteresis: stay in current mode unless the other clearly fits
    if (g_last_landscape)
        landscape_fits = (avail_lw >= GB_SCREEN_W && ch >= (std::max)(pad, sh_l) + VMARGIN * 3);
    else
        landscape_fits = (avail_lw >= GB_SCREEN_W + 20 && ch >= (std::max)(pad, sh_l) + VMARGIN * 3);

    if (landscape_fits) {
        L.landscape = true;
        int sw = sw_l, sh = sh_l;
        g_last_scale = scale_l;

        L.dpad.left   = VMARGIN;
        L.dpad.top    = (ch - pad) / 2;
        L.dpad.right  = VMARGIN + pad;
        L.dpad.bottom = L.dpad.top + pad;

        int zone_l = VMARGIN + pad + VGAP;
        int zone_r = cw - VMARGIN - btnD - VGAP;
        L.screen.left   = zone_l + ((zone_r - zone_l) - sw) / 2;
        L.screen.top    = (ch - sh) / 2;
        L.screen.right  = L.screen.left + sw;
        L.screen.bottom = L.screen.top + sh;

        int right_x = cw - VMARGIN - btnD;
        L.btnB.left   = right_x;
        L.btnB.top    = (ch - btnD) / 2;
        L.btnB.right  = right_x + btnD;
        L.btnB.bottom = L.btnB.top + btnD;

        L.btnA.left   = right_x - btnD / 2;
        L.btnA.top    = L.btnB.top - btnD - VMARGIN / 2;
        L.btnA.right  = L.btnA.left + btnD;
        L.btnA.bottom = L.btnA.top + btnD;
    } else {
        L.landscape = false;
        int scale = (std::max)(1, (std::min)(cw / GB_SCREEN_W, ch / GB_SCREEN_H));
        int sw = GB_SCREEN_W * scale;
        int sh = GB_SCREEN_H * scale;
        g_last_scale = scale;

        int top_h = sh + VMARGIN;
        L.screen.left   = (cw - sw) / 2;
        L.screen.top    = (top_h - sh) / 2;
        L.screen.right  = L.screen.left + sw;
        L.screen.bottom = L.screen.top + sh;

        int ctrl_y = top_h;
        int ctrl_h = ch - top_h;
        int mid = cw / 2;

        L.dpad.left   = mid / 2 - pad / 2;
        L.dpad.top    = ctrl_y + (ctrl_h - pad) / 2;
        L.dpad.right  = L.dpad.left + pad;
        L.dpad.bottom = L.dpad.top + pad;

        int right_x = cw - mid / 2 - btnD;
        L.btnB.left   = right_x;
        L.btnB.top    = ctrl_y + (ctrl_h - btnD) / 2;
        L.btnB.right  = right_x + btnD;
        L.btnB.bottom = L.btnB.top + btnD;

        L.btnA.left   = right_x - btnD / 2;
        L.btnA.top    = L.btnB.top - btnD - VMARGIN / 2;
        L.btnA.right  = L.btnA.left + btnD;
        L.btnA.bottom = L.btnA.top + btnD;
    }
    g_last_landscape = L.landscape;
    return L;
}

static Layout g_layout;

static void draw_dpad(HDC dc, const RECT& r, uint8_t pressed) {
    COLORREF bg = RGB(0x28, 0x2A, 0x22);
    COLORREF cross_clr = RGB(0x18, 0x1A, 0x14);
    COLORREF on = RGB(0xE8, 0xA3, 0x3D);

    HBRUSH br_bg = CreateSolidBrush(bg);
    HPEN pen_null = (HPEN)GetStockObject(NULL_PEN);
    HGDIOBJ old_pen = SelectObject(dc, pen_null);
    HGDIOBJ old_br = SelectObject(dc, br_bg);
    RoundRect(dc, r.left, r.top, r.right, r.bottom, 12, 12);
    SelectObject(dc, old_br);
    DeleteObject(br_bg);

    int cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
    int bw = (r.right - r.left) / 6;
    int bh = (r.bottom - r.top) / 6;
    int cr = (std::min)(bw, bh);

    // cross bars
    HBRUSH br_cross = CreateSolidBrush(cross_clr);
    SelectObject(dc, br_cross);
    RoundRect(dc, cx - bw, r.top + cr, cx + bw, r.bottom - cr, 4, 4);
    RoundRect(dc, r.left + cr, cy - bh, r.right - cr, cy + bh, 4, 4);
    // center dot
    Ellipse(dc, cx - cr/2, cy - cr/2, cx + cr/2, cy + cr/2);
    SelectObject(dc, old_br);
    DeleteObject(br_cross);

    // direction highlights
    HBRUSH br_on = CreateSolidBrush(on);
    int hw = bw + 6, hh = bh + 6;
    if (pressed & GB_PAD_UP)
        SelectObject(dc, br_on);
    else
        SelectObject(dc, GetStockObject(NULL_BRUSH));
    RoundRect(dc, cx - hw, r.top + 2, cx + hw, cy - cr, 4, 4);

    if (pressed & GB_PAD_DOWN)
        SelectObject(dc, br_on);
    else
        SelectObject(dc, GetStockObject(NULL_BRUSH));
    RoundRect(dc, cx - hw, cy + cr, cx + hw, r.bottom - 2, 4, 4);

    if (pressed & GB_PAD_LEFT)
        SelectObject(dc, br_on);
    else
        SelectObject(dc, GetStockObject(NULL_BRUSH));
    RoundRect(dc, r.left + 2, cy - hh, cx - cr, cy + hh, 4, 4);

    if (pressed & GB_PAD_RIGHT)
        SelectObject(dc, br_on);
    else
        SelectObject(dc, GetStockObject(NULL_BRUSH));
    RoundRect(dc, cx + cr, cy - hh, r.right - 2, cy + hh, 4, 4);

    SelectObject(dc, old_br);
    SelectObject(dc, old_pen);
    DeleteObject(br_on);
}

static void draw_button(HDC dc, const RECT& r, bool pressed, const char* label) {
    COLORREF base = pressed ? RGB(0x5A, 0x3A, 0x58) : RGB(0x4A, 0x33, 0x48);
    COLORREF hilite = pressed ? RGB(0x7A, 0x5A, 0x78) : RGB(0x6A, 0x53, 0x68);
    COLORREF text_clr = RGB(0xF2, 0xD9, 0xE8);

    HBRUSH br_base = CreateSolidBrush(base);
    HPEN pen_null = (HPEN)GetStockObject(NULL_PEN);
    HGDIOBJ old_pen = SelectObject(dc, pen_null);
    HGDIOBJ old_br = SelectObject(dc, br_base);
    Ellipse(dc, r.left, r.top, r.right, r.bottom);
    DeleteObject(br_base);

    int cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
    int rr = (r.right - r.left) / 2;
    int hi_r = rr * 2 / 3;
    HBRUSH br_hi = CreateSolidBrush(hilite);
    SelectObject(dc, br_hi);
    Ellipse(dc, cx - rr + 4, r.top + 4, cx - rr + 4 + hi_r, r.top + 4 + hi_r);
    DeleteObject(br_hi);

    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, text_clr);
    HFONT fnt = CreateFontA(-(rr * 3 / 4), 0, 0, 0, FW_BOLD, 0, 0, 0,
                             DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                             DEFAULT_QUALITY, DEFAULT_PITCH | FF_SWISS, "Arial");
    HGDIOBJ old_fnt = SelectObject(dc, fnt);
    SIZE sz; GetTextExtentPoint32A(dc, label, 1, &sz);
    TextOutA(dc, cx - sz.cx / 2, cy - sz.cy / 2, label, 1);
    SelectObject(dc, old_fnt);
    DeleteObject(fnt);
    SelectObject(dc, old_br);
    SelectObject(dc, old_pen);
}

static void paint(HWND hwnd) {
    PAINTSTRUCT ps;
    HDC dc = BeginPaint(hwnd, &ps);
    RECT rc; GetClientRect(hwnd, &rc);
    int cw = rc.right, ch = rc.bottom;

    g_layout = calc_layout(cw, ch);

    // fill background black
    HBRUSH bb = (HBRUSH)GetStockObject(BLACK_BRUSH);
    FillRect(dc, &rc, bb);

    // screen
    RECT& sr = g_layout.screen;
    SetStretchBltMode(dc, STRETCH_DELETESCANS);
    BITMAPINFO bmi = {};
    bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth = GB_SCREEN_W;
    bmi.bmiHeader.biHeight = -GB_SCREEN_H;
    bmi.bmiHeader.biPlanes = 1;
    bmi.bmiHeader.biBitCount = 32;
    bmi.bmiHeader.biCompression = BI_RGB;
    StretchDIBits(dc, sr.left, sr.top, sr.right - sr.left, sr.bottom - sr.top,
                  0, 0, GB_SCREEN_W, GB_SCREEN_H,
                  g_pixels, &bmi, DIB_RGB_COLORS, SRCCOPY);

    // virtual controls
    uint8_t dpad_state = g_kb_dpad | g_mouse_dpad;
    uint8_t btn_state  = g_kb_buttons | g_mouse_buttons;
    draw_dpad(dc, g_layout.dpad, dpad_state);
    draw_button(dc, g_layout.btnA, (btn_state & GB_BTN_A) != 0, "A");
    draw_button(dc, g_layout.btnB, (btn_state & GB_BTN_B) != 0, "B");

    EndPaint(hwnd, &ps);
}

// ---- d-pad hit testing --------------------------------------------------------

static void hit_dpad(const RECT& r, int mx, int my) {
    int cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
    int dx = mx - cx, dy = my - cy;
    int dead = (r.right - r.left) / 8;
    if (abs(dx) < dead && abs(dy) < dead) {
        g_mouse_dpad = 0;
        return;
    }
    uint8_t dd = 0;
    if (abs(dx) * 3 > abs(dy)) { dd |= (dx > 0) ? GB_PAD_RIGHT : GB_PAD_LEFT; }
    if (abs(dy) * 3 > abs(dx)) { dd |= (dy > 0) ? GB_PAD_DOWN  : GB_PAD_UP; }
    if (g_mouse_dpad != dd) {
        g_mouse_dpad = dd;
        sync_input();
    }
}

static bool in_rect(const RECT& r, int mx, int my) {
    return mx >= r.left && mx < r.right && my >= r.top && my < r.bottom;
}

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
