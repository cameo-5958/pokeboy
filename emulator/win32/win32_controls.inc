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

    HPEN pen_null = (HPEN)GetStockObject(NULL_PEN);
    HGDIOBJ old_pen = SelectObject(dc, pen_null);

    HBRUSH br_base = CreateSolidBrush(base);
    HGDIOBJ old_br = SelectObject(dc, br_base);
    Ellipse(dc, r.left, r.top, r.right, r.bottom);

    int cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
    int rr = (r.right - r.left) / 2;
    int hi_r = rr * 2 / 3;
    HBRUSH br_hi = CreateSolidBrush(hilite);
    SelectObject(dc, br_hi);
    Ellipse(dc, cx - rr + 4, r.top + 4, cx - rr + 4 + hi_r, r.top + 4 + hi_r);
    SelectObject(dc, old_br);
    DeleteObject(br_hi);
    DeleteObject(br_base);

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
