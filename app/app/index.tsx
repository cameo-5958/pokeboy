import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useRef, useState } from "react";
import {
  Animated,
  PanResponder,
  Pressable,
  StyleSheet,
  Text,
  View,
  type LayoutChangeEvent,
  useWindowDimensions,
} from "react-native";

import { playSfx } from "@/sfx";

// Native Game Boy screen is 160x144. The LCD keeps that ratio; the emulator
// mounts its framebuffer into the LCD well later.
const SCREEN_RATIO = 160 / 144;

// The console keeps the same formation. Ejecting pops the cartridge out of the
// top slot, then the whole console slides down from under it — the cartridge
// always renders BEHIND the console body, never over it.
const PAD_TOP = 28;
const PAD_BOTTOM = 28;
const PULL_DIST = 56; // drag distance that fully pulls the cartridge out
const SPEEDS = ["x0.5", "x1", "x3", "xINF"] as const;

type Unit = (value: number) => number;
type Translate = Animated.AnimatedInterpolation<number>;
type AnimValue = Animated.Value;
type PanHandlers = ReturnType<typeof PanResponder.create>["panHandlers"];

// Real DMG carts are nearly square; the label sticker (where the game image
// mounts) dominates the front face.
function cartMetrics(u: Unit, width: number) {
  const labelWidth = width - u(24);
  const labelHeight = labelWidth * 0.85;
  // paddings + grip ridges above the label + contact strip below it
  return { labelWidth, labelHeight, height: labelHeight + u(58) };
}

// Hardware add-ons that can be snapped onto the console. Pure client state —
// enabling one just tracks it locally (behavior wiring comes later).
const MODS = [
  { id: "turbo", name: "TURBO CPU", desc: "Overclocks the core for double-speed play." },
  { id: "rumble", name: "RUMBLE PAK", desc: "Adds force feedback on big hits." },
  { id: "backlight", name: "BACKLIGHT", desc: "Lights up the LCD for night sessions." },
  { id: "link", name: "LINK CABLE", desc: "Hooks up a second console for trades." },
] as const;

// Fixed-size pieces so the panel stack height is known up front — portrait
// uses it to park the ejected cartridge low enough to leave room above.
function modMetrics(u: Unit) {
  const pad = u(10);
  const nameH = u(18); // name row, flanked by the scroll arrows
  const descH = u(24); // two lines of description
  const btnH = u(22); // YES / NO row
  const gap = u(6);
  const stackGap = u(8); // between the browser panel and the count panel
  const countH = u(24);
  const settingsH = u(26);
  const panelH = pad * 2 + nameH + gap + descH + gap + btnH;
  return {
    pad,
    nameH,
    descH,
    btnH,
    gap,
    stackGap,
    countH,
    settingsH,
    height: panelH + stackGap + countH + stackGap + settingsH,
  };
}

export default function EmulatorScreen() {
  const { width, height } = useWindowDimensions();
  const landscape = width > height;

  const bodyWidth = landscape ? width * 0.96 : Math.min(width * 0.92, height * 0.5);
  const bodyHeight = height - PAD_TOP - PAD_BOTTOM;
  const scale = (landscape ? bodyHeight : bodyWidth) / 360;
  const u: Unit = (v) => v * scale;

  const cartWidth = landscape ? u(178) : bodyWidth * 0.66;
  const cart = cartMetrics(u, cartWidth);
  // Landscape hugs the left grip, but never so far left that the selector
  // arrow (size u34 + gap u12) would fall off screen.
  const slotLeft = landscape
    ? Math.max(u(30), u(46) + 8 - (width - bodyWidth) / 2)
    : (bodyWidth - cartWidth) / 2;

  // How much of the seated cartridge peeks above the slot.
  const lip = u(12);
  // Phase-1 pop. Landscape fills the viewport vertically, so cap the pop to
  // the headroom above the shell to keep the cartridge on screen.
  const popOut = landscape ? Math.max(PAD_TOP - lip - 4, 4) : u(34);
  // Once dropped, the cartridge parks at the top of the page (matters in
  // portrait, where the centered shell leaves headroom above it) and the
  // shell drops just far enough below to clear it with a small gap.
  const settle = landscape ? u(6) : 0;
  const [bodyTop, setBodyTop] = useState(PAD_TOP);
  const mod = modMetrics(u);
  // Portrait sinks the shell well below the cartridge to free up the middle
  // of the screen; landscape only needs enough to clear it.
  const clearance = landscape ? u(16) : u(160);
  const modGap = mod.height + u(14);
  // Portrait centers the ejected cartridge + mod stack in the empty space
  // above the lowered shell; landscape keeps it at the top with the panel right.
  const parkTop = landscape ? PAD_TOP : clearance + modGap;
  const parkDelta = landscape ? Math.max(0, bodyTop - parkTop) : bodyTop - parkTop;
  const bodyDrop = Math.max(0, cart.height + settle + clearance - parkDelta);

  // 0 = inserted, 1 = cartridge popped out, 2 = console dropped down.
  const anim = useRef(new Animated.Value(0)).current;
  const ejectedRef = useRef(false);
  const startVal = useRef(0);
  // State mirror of ejectedRef so the mod panel can gate its touch handling
  // (it sits at opacity 0 while the cartridge is seated).
  const [ejected, setEjected] = useState(false);
  const [volume, setVolume] = useState(0.72);
  const [muted, setMuted] = useState(false);
  const [speedIdx, setSpeedIdx] = useState(1);
  const cycleSpeed = () => {
    playSfx("select");
    setSpeedIdx((i) => (i + 1) % SPEEDS.length);
  };
  const toggleMute = () => {
    playSfx("select");
    setMuted((v) => !v);
  };

  // Enabled mods, by id.
  const [enabledMods, setEnabledMods] = useState<ReadonlySet<string>>(new Set());
  const setMod = (id: string, on: boolean) => {
    if (enabledMods.has(id) === on) return;
    playSfx(on ? "modOn" : "modOff");
    setEnabledMods((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const pan = useRef(
    (() => {
      const eject = () => {
        ejectedRef.current = true;
        setEjected(true);
        playSfx("eject");
        anim.stopAnimation(() =>
          Animated.sequence([
            Animated.timing(anim, { toValue: 1, duration: 190, useNativeDriver: false }),
            Animated.spring(anim, { toValue: 2, useNativeDriver: false, bounciness: 6, speed: 11 }),
          ]).start(),
        );
      };
      const insert = () => {
        ejectedRef.current = false;
        setEjected(false);
        playSfx("insert");
        anim.stopAnimation((v) => {
          if (v > 1) {
            Animated.sequence([
              Animated.spring(anim, { toValue: 1, useNativeDriver: false, bounciness: 3, speed: 12 }),
              Animated.timing(anim, { toValue: 0, duration: 190, useNativeDriver: false }),
            ]).start(({ finished }) => {
              if (finished) playSfx("snap");
            });
          } else {
            Animated.timing(anim, { toValue: 0, duration: 180, useNativeDriver: false }).start(({ finished }) => {
              if (finished) playSfx("snap");
            });
          }
        });
      };
      const toggle = () => (ejectedRef.current ? insert() : eject());

      return PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: (_, g) => Math.abs(g.dy) > 3,
        onPanResponderGrant: () => anim.stopAnimation((v) => (startVal.current = v)),
        onPanResponderMove: (_, g) => {
          if (ejectedRef.current) return; // only live-scrub the pull, not the drop
          // Dragging up pulls the cartridge out of the slot.
          anim.setValue(Math.max(0, Math.min(1, startVal.current - g.dy / PULL_DIST)));
        },
        onPanResponderRelease: (_, g) => {
          const moved = Math.abs(g.dy) + Math.abs(g.dx);
          if (moved < 6 || ejectedRef.current) return toggle();
          anim.stopAnimation((v) => (v >= 0.6 ? eject() : insert()));
        },
        onPanResponderTerminationRequest: () => false,
      });
    })(),
  ).current;

  const bodyTranslate = anim.interpolate({ inputRange: [0, 1, 2], outputRange: [0, 0, bodyDrop] });
  // The cartridge is a child of the dropping wrapper, so phase 2 subtracts the
  // drop to leave it (almost) fixed on screen while the console slides away.
  const cartTranslate = anim.interpolate({
    inputRange: [0, 1, 2],
    outputRange: [0, -popOut, lip + settle - bodyDrop - parkDelta],
  });

  const onBodyLayout = (e: LayoutChangeEvent) => setBodyTop(e.nativeEvent.layout.y);

  return (
    <View style={styles.page}>
      <Stack.Screen options={{ headerShown: false }} />
      <StatusBar hidden />
      <Animated.View onLayout={onBodyLayout} style={{ transform: [{ translateY: bodyTranslate }] }}>
        {/* Cartridge layer — kept UNDER the console body (zIndex 0 vs 1) so it
            emerges from behind the top edge and slides back under on insert. */}
        <Animated.View
          style={{
            position: "absolute",
            top: -lip,
            left: slotLeft,
            width: cartWidth,
            height: cart.height,
            zIndex: 0,
            transform: [{ translateY: cartTranslate }],
          }}
        >
          <Cartridge
            u={u}
            width={cartWidth}
            labelWidth={cart.labelWidth}
            labelHeight={cart.labelHeight}
            height={cart.height}
            panHandlers={pan.panHandlers}
          />
          <CartridgeArrow u={u} anim={anim} side="left" cartHeight={cart.height} />
          <CartridgeArrow u={u} anim={anim} side="right" cartHeight={cart.height} />
          <ModChanger
            u={u}
            anim={anim}
            active={ejected}
            landscape={landscape}
            cartWidth={cartWidth}
            enabled={enabledMods}
            onSet={setMod}
          />
        </Animated.View>

        {landscape ? (
          <LandscapeConsole
            bodyWidth={bodyWidth}
            bodyHeight={bodyHeight}
            u={u}
            slotLeft={slotLeft}
            slotWidth={cartWidth}
            panHandlers={pan.panHandlers}
            volume={volume}
            muted={muted}
            speed={SPEEDS[speedIdx]}
            onVolume={setVolume}
            onMute={toggleMute}
            onCycleSpeed={cycleSpeed}
          />
        ) : (
          <PortraitConsole
            bodyWidth={bodyWidth}
            u={u}
            slotLeft={slotLeft}
            slotWidth={cartWidth}
            panHandlers={pan.panHandlers}
            volume={volume}
            muted={muted}
            speed={SPEEDS[speedIdx]}
            onVolume={setVolume}
            onMute={toggleMute}
            onCycleSpeed={cycleSpeed}
          />
        )}
      </Animated.View>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Layouts
 * ------------------------------------------------------------------ */

function PortraitConsole({
  bodyWidth,
  u,
  slotLeft,
  slotWidth,
  panHandlers,
  volume,
  muted,
  speed,
  onVolume,
  onMute,
  onCycleSpeed,
}: {
  bodyWidth: number;
  u: Unit;
  slotLeft: number;
  slotWidth: number;
  panHandlers: PanHandlers;
  volume: number;
  muted: boolean;
  speed: (typeof SPEEDS)[number];
  onVolume: (value: number) => void;
  onMute: () => void;
  onCycleSpeed: () => void;
}) {
  const screenWidth = bodyWidth * 0.82;
  const screenHeight = screenWidth / SCREEN_RATIO;

  return (
    <View
      style={[
        styles.body,
        {
          width: bodyWidth,
          borderRadius: u(22),
          borderBottomRightRadius: u(66),
          padding: u(18),
          paddingTop: u(26),
        },
      ]}
    >
      <CartSlot u={u} left={slotLeft} width={slotWidth} panHandlers={panHandlers} />
      <Bezel u={u} screenWidth={screenWidth} screenHeight={screenHeight} variant="portrait" />
      <MiniControls
        u={u}
        volume={volume}
        muted={muted}
        speed={speed}
        onVolume={onVolume}
        onMute={onMute}
        onCycleSpeed={onCycleSpeed}
      />
      <Wordmark u={u} style={{ alignSelf: "flex-start", marginTop: u(14), marginLeft: u(4) }} />

      <View style={{ width: "100%", marginTop: u(18), gap: u(16) }}>
        <View style={styles.portraitTopRow}>
          <Dpad u={u} size={u(108)} />
          <FaceButtons u={u} />
        </View>
        <PillRow u={u} />
      </View>
    </View>
  );
}

function LandscapeConsole({
  bodyWidth,
  bodyHeight,
  u,
  slotLeft,
  slotWidth,
  panHandlers,
  volume,
  muted,
  speed,
  onVolume,
  onMute,
  onCycleSpeed,
}: {
  bodyWidth: number;
  bodyHeight: number;
  u: Unit;
  slotLeft: number;
  slotWidth: number;
  panHandlers: PanHandlers;
  volume: number;
  muted: boolean;
  speed: (typeof SPEEDS)[number];
  onVolume: (value: number) => void;
  onMute: () => void;
  onCycleSpeed: () => void;
}) {
  // Wide shell, shrunk vertically: the left grip carries the slot up top with
  // the "Poké boy" mark over the D-pad, the screen sits in the middle, and the
  // action buttons ride low on the right.
  // As tall as the bezel allows, with a small strip reserved under it.
  const screenHeight = bodyHeight - u(92);
  const screenWidth = screenHeight * SCREEN_RATIO;

  return (
    <View
      style={[
        styles.body,
        styles.bodyLandscape,
        {
          width: bodyWidth,
          height: bodyHeight,
          borderRadius: u(30),
          borderBottomRightRadius: u(58),
          paddingHorizontal: u(30),
          paddingVertical: u(16),
        },
      ]}
    >
      <CartSlot u={u} left={slotLeft} width={slotWidth} panHandlers={panHandlers} />

      {/* Left grip: wordmark over D-pad, sized to match the slot above it */}
      <View style={[styles.landZone, { width: slotWidth }]}>
        <Wordmark u={u} style={{ marginBottom: u(14) }} />
        <Dpad u={u} size={u(112)} />
      </View>

      {/* Center: screen */}
      <View style={[styles.landZone, { width: screenWidth }]}>
        <Bezel u={u} screenWidth={screenWidth} screenHeight={screenHeight} variant="landscape" />
        <MiniControls
          u={u}
          volume={volume}
          muted={muted}
          speed={speed}
          onVolume={onVolume}
          onMute={onMute}
          onCycleSpeed={onCycleSpeed}
          width={screenWidth}
        />
      </View>

      {/* Right grip: A/B (wide) over Start/Select, vertically centered */}
      <View style={[styles.landZone, { gap: u(48) }]}>
        <FaceButtons u={u} w={u(70)} h={u(58)} />
        <PillRow u={u} gap={u(18)} />
      </View>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Cartridge
 * ------------------------------------------------------------------ */

// The slot mouth on the shell's top edge. Also a grab zone, so the seated
// cartridge can be tapped/pulled even though only its lip peeks out.
function CartSlot({
  u,
  left,
  width,
  panHandlers,
}: {
  u: Unit;
  left: number;
  width: number;
  panHandlers: PanHandlers;
}) {
  return (
    <View
      {...panHandlers}
      style={{
        position: "absolute",
        top: 0,
        left: left - u(9),
        width: width + u(18),
        height: u(30),
        zIndex: 3,
        alignItems: "stretch",
      }}
    >
      <View
        style={[
          styles.cartSlot,
          { height: u(7), borderBottomLeftRadius: u(3), borderBottomRightRadius: u(3) },
        ]}
      />
    </View>
  );
}

function Cartridge({
  u,
  width,
  height,
  labelWidth,
  labelHeight,
  panHandlers,
}: {
  u: Unit;
  width: number;
  height: number;
  labelWidth: number;
  labelHeight: number;
  panHandlers: PanHandlers;
}) {
  return (
    <View
      {...panHandlers}
      style={[
        styles.cartBody,
        {
          width,
          height,
          borderTopLeftRadius: u(10),
          borderTopRightRadius: u(10),
          borderBottomLeftRadius: u(6),
          borderBottomRightRadius: u(6),
          borderWidth: u(1.5),
          paddingTop: u(8),
          paddingBottom: u(9),
          paddingHorizontal: u(12),
        },
      ]}
    >
      {/* Grip ridges */}
      <View style={[styles.cartRidges, { marginBottom: u(6) }]}>
        {[0, 1, 2].map((i) => (
          <View
            key={i}
            style={[styles.cartRidge, { height: u(2), marginBottom: u(3), borderRadius: u(1) }]}
          />
        ))}
      </View>
      {/* Label sticker — the game image mounts inside the reserve */}
      <View
        style={[
          styles.cartLabel,
          { width: labelWidth, height: labelHeight, borderRadius: u(5), padding: u(5) },
        ]}
      >
        <View style={[styles.cartImageReserve, { borderRadius: u(3) }]} />
      </View>
      {/* Edge connector */}
      <View style={[styles.cartBottom, { height: u(12), marginTop: u(8), borderRadius: u(2) }]}>
        {[0, 1, 2, 3, 4].map((i) => (
          <View key={i} style={[styles.cartContact, { width: u(6), borderRadius: u(1) }]} />
        ))}
      </View>
    </View>
  );
}

// Cartridge selector chevrons. Fixed circles beside the cartridge that fade
// and scale in as the console drops away (selection wiring comes later).
function CartridgeArrow({
  u,
  anim,
  side,
  cartHeight,
}: {
  u: Unit;
  anim: AnimValue;
  side: "left" | "right";
  cartHeight: number;
}) {
  const size = u(34);
  const opacity = anim.interpolate({ inputRange: [1, 2], outputRange: [0, 1], extrapolate: "clamp" });
  const grow = anim.interpolate({ inputRange: [1, 2], outputRange: [0.5, 1], extrapolate: "clamp" });
  return (
    <Animated.View
      style={[
        {
          position: "absolute",
          top: cartHeight / 2 - size / 2,
          opacity,
          transform: [{ scale: grow }],
        },
        side === "left" ? { left: -(size + u(12)) } : { right: -(size + u(12)) },
      ]}
    >
      <Pressable
        style={({ pressed }) => [
          styles.cartArrow,
          { width: size, height: size, borderRadius: size / 2 },
          pressed && { opacity: 0.6 },
        ]}
      >
        <Text selectable={false} style={[styles.cartArrowText, { fontSize: size * 0.62 }]}>
          {side === "left" ? "‹" : "›"}
        </Text>
      </Pressable>
    </Animated.View>
  );
}

/* ------------------------------------------------------------------ *
 * Mod changer
 * ------------------------------------------------------------------ */

// Accessory bay for snapping mods onto the console. Anchored to the ejected
// cartridge — right of it in landscape, above it in portrait — and fades in
// with the console drop, like the selector arrows. Two stacked panels: a
// one-mod-at-a-time browser (name, blurb, YES/NO), and an enabled count.
function ModChanger({
  u,
  anim,
  active,
  landscape,
  cartWidth,
  enabled,
  onSet,
}: {
  u: Unit;
  anim: AnimValue;
  active: boolean;
  landscape: boolean;
  cartWidth: number;
  enabled: ReadonlySet<string>;
  onSet: (id: string, on: boolean) => void;
}) {
  const [idx, setIdx] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const m = modMetrics(u);
  const width = landscape ? u(170) : cartWidth + u(24);
  const opacity = anim.interpolate({ inputRange: [1, 2], outputRange: [0, 1], extrapolate: "clamp" });
  const grow = anim.interpolate({ inputRange: [1, 2], outputRange: [0.7, 1], extrapolate: "clamp" });

  const mod = MODS[idx];
  const on = enabled.has(mod.id);
  const scroll = (dir: 1 | -1) => {
    playSfx("dpad");
    setIdx((i) => (i + dir + MODS.length) % MODS.length);
  };
  const toggleSettings = () => {
    playSfx("select");
    setSettingsOpen((v) => !v);
  };

  return (
    <Animated.View
      pointerEvents={active ? "auto" : "none"}
      style={[
        { position: "absolute", width, opacity, transform: [{ scale: grow }] },
        landscape
          ? // Clear the right selector arrow (offset u12 + size u34) plus a gap.
            { left: cartWidth + u(58), top: 0 }
          : { left: (cartWidth - width) / 2, top: -(m.height + u(14)) },
      ]}
    >
      {/* Panel 1: mod browser */}
      <View style={[styles.modPanel, { borderRadius: u(10), padding: m.pad }]}>
        <View style={[styles.modNameRow, { height: m.nameH }]}>
          <ModScroll u={u} h={m.nameH} dir={-1} onPress={scroll} />
          <Text selectable={false} style={[styles.modName, { fontSize: u(9), letterSpacing: u(1) }]}>
            {mod.name}
          </Text>
          <ModScroll u={u} h={m.nameH} dir={1} onPress={scroll} />
        </View>
        <Text
          selectable={false}
          numberOfLines={2}
          style={[
            styles.modDesc,
            { height: m.descH, fontSize: u(8), lineHeight: u(12), marginTop: m.gap },
          ]}
        >
          {mod.desc}
        </Text>
        <View style={{ flexDirection: "row", gap: m.gap, marginTop: m.gap }}>
          <ModChoice u={u} h={m.btnH} label="YES" active={on} onPress={() => onSet(mod.id, true)} />
          <ModChoice u={u} h={m.btnH} label="NO" active={!on} onPress={() => onSet(mod.id, false)} />
        </View>
      </View>

      {/* Panel 2: enabled count */}
      <View
        style={[
          styles.modPanel,
          styles.modCountRow,
          { height: m.countH, marginTop: m.stackGap, borderRadius: u(8), paddingHorizontal: m.pad },
        ]}
      >
        <Text selectable={false} style={[styles.modName, { fontSize: u(8), letterSpacing: u(1) }]}>
          ENABLED
        </Text>
        <Text selectable={false} style={[styles.modCountValue, { fontSize: u(9) }]}>
          {enabled.size}/{MODS.length}
        </Text>
      </View>

      {active ? (
        <>
          <View
            style={[
              styles.modPanel,
              { height: m.settingsH, marginTop: m.stackGap, borderRadius: u(8), padding: u(4) },
            ]}
          >
            <Pressable
              onPress={toggleSettings}
              style={({ pressed }) => [
                styles.settingsButton,
                { flex: 1, borderRadius: u(5) },
                settingsOpen && styles.settingsButtonOn,
                pressed && { opacity: 0.62 },
              ]}
            >
              <Text
                selectable={false}
                style={[
                  styles.modName,
                  styles.settingsButtonText,
                  { fontSize: u(8), letterSpacing: u(1) },
                  settingsOpen && styles.settingsButtonTextOn,
                ]}
              >
                SETTINGS
              </Text>
            </Pressable>
          </View>
          {settingsOpen ? (
            <View
              style={[
                styles.settingsPopup,
                {
                  width,
                  borderRadius: u(10),
                  padding: u(10),
                  top: 0,
                },
              ]}
            >
              <View style={styles.settingsPopupHeader}>
                <Text selectable={false} style={[styles.modName, { fontSize: u(9), letterSpacing: u(1) }]}>
                  SETTINGS
                </Text>
                <Pressable
                  onPress={toggleSettings}
                  style={({ pressed }) => [
                    styles.settingsClose,
                    { width: u(20), height: u(20), borderRadius: u(5) },
                    pressed && { opacity: 0.62 },
                  ]}
                >
                  <Text selectable={false} style={[styles.settingsCloseText, { fontSize: u(10) }]}>
                    X
                  </Text>
                </Pressable>
              </View>
              <View style={[styles.settingsPopupRows, { marginTop: u(8), gap: u(7) }]}>
                {["AUDIO", "SPEED", "DISPLAY"].map((label) => (
                  <View key={label} style={styles.settingsMockRow}>
                    <Text selectable={false} style={[styles.settingsMockText, { fontSize: u(7) }]}>
                      {label}
                    </Text>
                    <View style={[styles.settingsMockDash, { width: u(56) }]} />
                  </View>
                ))}
              </View>
            </View>
          ) : null}
        </>
      ) : null}
    </Animated.View>
  );
}

function ModScroll({
  u,
  h,
  dir,
  onPress,
}: {
  u: Unit;
  h: number;
  dir: 1 | -1;
  onPress: (dir: 1 | -1) => void;
}) {
  return (
    <Pressable
      onPress={() => onPress(dir)}
      style={({ pressed }) => [
        styles.modScroll,
        { width: u(20), height: h, borderRadius: u(4) },
        pressed && { opacity: 0.6 },
      ]}
    >
      <Text selectable={false} style={[styles.modScrollText, { fontSize: h * 0.7 }]}>
        {dir === -1 ? "‹" : "›"}
      </Text>
    </Pressable>
  );
}

function ModChoice({
  u,
  h,
  label,
  active,
  onPress,
}: {
  u: Unit;
  h: number;
  label: string;
  active: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.modChoice,
        { height: h, borderRadius: u(5) },
        active && styles.modChoiceOn,
        pressed && { opacity: 0.6 },
      ]}
    >
      <Text
        selectable={false}
        style={[styles.modChoiceText, { fontSize: u(8) }, active && styles.modChoiceTextOn]}
      >
        {label}
      </Text>
    </Pressable>
  );
}

/* ------------------------------------------------------------------ *
 * Shared pieces
 * ------------------------------------------------------------------ */

function Bezel({
  u,
  screenWidth,
  screenHeight,
  variant,
}: {
  u: Unit;
  screenWidth: number;
  screenHeight: number;
  variant: "portrait" | "landscape";
}) {
  const portrait = variant === "portrait";
  return (
    <View
      style={[
        styles.bezel,
        {
          width: portrait ? "100%" : undefined,
          borderRadius: u(12),
          borderBottomRightRadius: u(40),
          paddingTop: u(16),
          paddingBottom: portrait ? u(12) : u(14),
          paddingHorizontal: u(16),
        },
      ]}
    >
      {/* Twin pinstripes — the signature DMG mark */}
      <View style={[styles.stripes, { top: u(9), height: u(5), left: u(16), right: u(16) }]}>
        <View style={[styles.stripe, { backgroundColor: "#1c2a86", marginBottom: u(2) }]} />
        <View style={[styles.stripe, { backgroundColor: "#8a1f45" }]} />
      </View>

      {portrait ? (
        <View style={[styles.batteryRow, { left: u(14), top: u(26) }]}>
          <View style={[styles.led, { width: u(8), height: u(8), borderRadius: u(4) }]} />
          <Text style={[styles.batteryLabel, { fontSize: u(6), marginTop: u(3) }]}>BATTERY</Text>
        </View>
      ) : null}

      <Lcd u={u} width={screenWidth} height={screenHeight} />

      {portrait ? (
        <Text style={[styles.dotMatrix, { fontSize: u(8), marginTop: u(8), letterSpacing: u(1.2) }]}>
          DOT MATRIX WITH STEREO SOUND
        </Text>
      ) : null}
    </View>
  );
}

function Lcd({ u, width, height }: { u: Unit; width: number; height: number }) {
  return (
    <View style={[styles.lcd, { width, height, borderRadius: u(4), borderWidth: u(1.5) }]}>
      <View style={styles.lcdWell} pointerEvents="none" />
      <View
        style={[styles.lcdGlare, { top: -u(20), right: -u(28), width: u(120), height: u(64) }]}
        pointerEvents="none"
      />
    </View>
  );
}

function Wordmark({ u, style }: { u: Unit; style?: object }) {
  return (
    <View style={[styles.wordmark, style]}>
      <Text style={[styles.brandPoke, { fontSize: u(20) }]}>Poké</Text>
      <Text style={[styles.brandBoy, { fontSize: u(24), marginLeft: u(4) }]}>boy</Text>
    </View>
  );
}

function Dpad({ u, size }: { u: Unit; size: number }) {
  const arm = u(32);
  const off = (size - arm) / 2; // length of each arm beyond the center hub
  return (
    <View style={{ width: size, height: size }}>
      {/* Visual cross (non-interactive) */}
      <View
        pointerEvents="none"
        style={[styles.dpadBar, { width: arm, height: size, left: off, borderRadius: u(5) }]}
      />
      <View
        pointerEvents="none"
        style={[styles.dpadBar, { height: arm, width: size, top: off, borderRadius: u(5) }]}
      />
      <View
        pointerEvents="none"
        style={[
          styles.dpadHub,
          { width: u(22), height: u(22), borderRadius: u(11), top: size / 2 - u(11), left: size / 2 - u(11) },
        ]}
      />
      {/* Directional press zones */}
      <DpadZone style={{ left: off, top: 0, width: arm, height: off, borderTopLeftRadius: u(5), borderTopRightRadius: u(5) }} />
      <DpadZone style={{ left: off, top: size - off, width: arm, height: off, borderBottomLeftRadius: u(5), borderBottomRightRadius: u(5) }} />
      <DpadZone style={{ top: off, left: 0, height: arm, width: off, borderTopLeftRadius: u(5), borderBottomLeftRadius: u(5) }} />
      <DpadZone style={{ top: off, left: size - off, height: arm, width: off, borderTopRightRadius: u(5), borderBottomRightRadius: u(5) }} />
    </View>
  );
}

function DpadZone({ style }: { style: object }) {
  return (
    <Pressable
      onPress={() => playSfx("dpad")}
      style={({ pressed }) => [{ position: "absolute" }, style, pressed && styles.dpadZonePressed]}
    />
  );
}

function FaceButtons({ u, w, h }: { u: Unit; w?: number; h?: number }) {
  const bw = w ?? u(52);
  const bh = h ?? u(52);
  const fontSize = bh * 0.46;
  return (
    <View style={styles.abGroup}>
      <FaceButton label="B" w={bw} h={bh} fontSize={fontSize} />
      <View style={{ marginLeft: u(20), marginBottom: u(30) }}>
        <FaceButton label="A" w={bw} h={bh} fontSize={fontSize} />
      </View>
    </View>
  );
}

function FaceButton({
  label,
  w,
  h,
  fontSize,
}: {
  label: "A" | "B";
  w: number;
  h: number;
  fontSize: number;
}) {
  return (
    <Pressable
      onPress={() => playSfx(label === "A" ? "a" : "b")}
      style={({ pressed }) => [
        styles.faceButton,
        { width: w, height: h, borderRadius: h / 2, alignItems: "center", justifyContent: "center" },
        pressed && styles.faceButtonPressed,
      ]}
    >
      {/* Counter-rotate the label so it stays upright despite the tilted group */}
      <Text selectable={false} style={[styles.faceLabelOn, { fontSize, transform: [{ rotate: "25deg" }] }]}>
        {label}
      </Text>
    </Pressable>
  );
}

function PillRow({ u, gap }: { u: Unit; gap?: number }) {
  return (
    <View style={[styles.pillRow, { gap: gap ?? u(24) }]}>
      <Pill u={u} label="SELECT" />
      <Pill u={u} label="START" />
    </View>
  );
}

function Pill({ u, label }: { u: Unit; label: string }) {
  return (
    <Pressable
      onPress={() => playSfx(label === "START" ? "start" : "select")}
      style={({ pressed }) => [styles.pillGroup, pressed && { opacity: 0.55 }]}
    >
      <View
        style={[
          styles.pill,
          { width: u(40), height: u(12), borderRadius: u(6), transform: [{ rotate: "-25deg" }] },
        ]}
      />
      <Text style={[styles.pillLabel, { fontSize: u(8), marginTop: u(6) }]}>{label}</Text>
    </Pressable>
  );
}

function MiniControls({
  u,
  volume,
  muted,
  speed,
  onVolume,
  onMute,
  onCycleSpeed,
  width,
}: {
  u: Unit;
  volume: number;
  muted: boolean;
  speed: (typeof SPEEDS)[number];
  onVolume: (value: number) => void;
  onMute: () => void;
  onCycleSpeed: () => void;
  width?: number;
}) {
  const controlWidth = Math.min(width ?? u(214), u(214));
  const volumeWidth = u(86);

  return (
    <View style={[styles.miniControls, { marginTop: u(7), width: controlWidth, height: u(20) }]}>
      <View style={[styles.miniControlSide, { width: volumeWidth }]}>
        <Pressable
          onPress={onMute}
          style={({ pressed }) => [
            styles.muteButton,
            { width: u(19), height: u(15), borderRadius: u(4) },
            muted && styles.muteButtonOn,
            pressed && { opacity: 0.62 },
          ]}
        >
          <Text
            selectable={false}
            style={[styles.miniLabel, styles.muteText, muted && styles.muteTextOn, { fontSize: u(6) }]}
          >
            M
          </Text>
        </Pressable>
        <VolumeSlider
          u={u}
          width={u(56)}
          value={muted ? 0 : volume}
          onChange={onVolume}
          disabled={muted}
        />
      </View>
      <View style={[styles.miniDivider, { height: u(13), marginHorizontal: u(7) }]} />
      <Pressable
        onPress={onCycleSpeed}
        style={({ pressed }) => [
          styles.speedButton,
          { width: u(48), height: u(16), borderRadius: u(8) },
          pressed && { opacity: 0.62 },
        ]}
      >
        <Text selectable={false} style={[styles.miniLabel, styles.speedText, { fontSize: u(7) }]}>
          {speed}
        </Text>
      </Pressable>
    </View>
  );
}

function VolumeSlider({
  u,
  width,
  value,
  onChange,
  disabled,
}: {
  u: Unit;
  width: number;
  value: number;
  onChange: (value: number) => void;
  disabled?: boolean;
}) {
  const trackWidth = useRef(1);
  const setFromX = (x: number) => {
    if (!disabled) onChange(Math.max(0, Math.min(1, x / trackWidth.current)));
  };
  const pan = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderGrant: (e) => setFromX(e.nativeEvent.locationX),
      onPanResponderMove: (e) => setFromX(e.nativeEvent.locationX),
    }),
  ).current;

  return (
    <View
      {...pan.panHandlers}
      onLayout={(e) => {
        trackWidth.current = Math.max(1, e.nativeEvent.layout.width);
      }}
      style={[styles.volumeTrack, { width, height: u(4), borderRadius: u(2) }]}
    >
      <View style={[styles.volumeFill, { width: `${value * 100}%`, borderRadius: u(2) }]} />
      <View
        pointerEvents="none"
        style={[
          styles.volumeThumb,
          {
            width: u(8),
            height: u(8),
            borderRadius: u(4),
            left: `${value * 100}%`,
            marginLeft: -u(4),
            top: -u(2),
          },
        ]}
      />
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Styles
 * ------------------------------------------------------------------ */

const styles = StyleSheet.create({
  page: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingTop: PAD_TOP,
    paddingBottom: PAD_BOTTOM,
    backgroundColor: "#26262f",
  },

  body: {
    alignItems: "center",
    backgroundColor: "#c8c3b4",
    borderColor: "#b0ab9c",
    borderWidth: 2,
    borderTopColor: "#dcd8cc",
    borderLeftColor: "#d2cec1",
    borderRightColor: "#aaa596",
    borderBottomColor: "#a09b8c",
    shadowColor: "#5c5647",
    shadowOffset: { width: 0, height: 18 },
    shadowOpacity: 0.3,
    shadowRadius: 30,
    zIndex: 1,
  },
  bodyLandscape: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },

  landZone: {
    alignSelf: "stretch",
    alignItems: "center",
    justifyContent: "center",
  },

  portraitTopRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 8,
  },
  miniControls: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
  },
  miniControlSide: {
    flexDirection: "row",
    alignItems: "center",
  },
  miniLabel: {
    color: "#4c4a55",
    fontWeight: "800",
    letterSpacing: 0.5,
    userSelect: "none",
  },
  miniDivider: {
    width: 1,
    backgroundColor: "#aaa596",
  },
  volumeTrack: {
    marginLeft: 5,
    backgroundColor: "#aaa596",
  },
  volumeFill: {
    height: "100%",
    backgroundColor: "#4c4a55",
  },
  volumeThumb: {
    position: "absolute",
    backgroundColor: "#9a1f4c",
  },
  muteButton: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#c8c3b4",
    borderWidth: 1,
    borderColor: "#aaa596",
  },
  muteButtonOn: {
    backgroundColor: "#454550",
    borderColor: "#33333c",
  },
  muteText: {
    color: "#4c4a55",
  },
  muteTextOn: {
    color: "#d6d1c2",
  },
  speedButton: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#d8d4c6",
    borderWidth: 1,
    borderColor: "#aaa596",
  },
  speedText: {
    color: "#31316f",
  },

  cartSlot: {
    backgroundColor: "#2a2a2e",
  },
  cartBody: {
    alignItems: "center",
    backgroundColor: "#454550",
    borderColor: "#33333c",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.25,
    shadowRadius: 10,
  },
  cartRidges: {
    width: "34%",
  },
  cartRidge: {
    width: "100%",
    backgroundColor: "#5c5c67",
  },
  cartLabel: {
    backgroundColor: "#d6d1c2",
  },
  cartImageReserve: {
    flex: 1,
    backgroundColor: "#c5bfab",
    borderWidth: 1,
    borderColor: "#aaa38d",
  },
  cartBottom: {
    width: "78%",
    flexDirection: "row",
    justifyContent: "space-around",
    backgroundColor: "#2f2f37",
    paddingHorizontal: 4,
    paddingVertical: 2,
  },
  cartContact: {
    height: "100%",
    backgroundColor: "#b7aa75",
  },
  cartArrow: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#d8d4c6",
    borderWidth: 1,
    borderColor: "#a59f8d",
    shadowColor: "#5c5647",
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.18,
    shadowRadius: 3,
  },
  cartArrowText: {
    color: "#4c4a55",
    fontWeight: "800",
    userSelect: "none",
  },

  modPanel: {
    backgroundColor: "#d8d4c6",
    borderWidth: 1,
    borderColor: "#a59f8d",
    shadowColor: "#5c5647",
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.2,
    shadowRadius: 6,
  },
  modNameRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  modName: {
    color: "#4c4a55",
    fontWeight: "800",
    userSelect: "none",
  },
  modDesc: {
    color: "#6b665a",
    fontWeight: "600",
    textAlign: "center",
    userSelect: "none",
  },
  modScroll: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#c8c3b4",
    borderWidth: 1,
    borderColor: "#aaa596",
  },
  modScrollText: {
    color: "#4c4a55",
    fontWeight: "800",
    userSelect: "none",
  },
  modChoice: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#c8c3b4",
    borderWidth: 1,
    borderColor: "#aaa596",
  },
  modChoiceOn: {
    backgroundColor: "#454550",
    borderColor: "#33333c",
  },
  modChoiceText: {
    color: "#4c4a55",
    fontWeight: "800",
    letterSpacing: 1,
    userSelect: "none",
  },
  modChoiceTextOn: {
    color: "#d6d1c2",
  },
  modCountRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  modCountValue: {
    color: "#9a1f4c",
    fontWeight: "800",
    userSelect: "none",
  },
  settingsButton: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#c8c3b4",
    borderWidth: 1,
    borderColor: "#aaa596",
  },
  settingsButtonOn: {
    backgroundColor: "#454550",
    borderColor: "#33333c",
  },
  settingsButtonText: {
    color: "#4c4a55",
  },
  settingsButtonTextOn: {
    color: "#d6d1c2",
  },
  settingsMockRows: {
    flex: 1,
    justifyContent: "space-between",
  },
  settingsMockRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  settingsMockText: {
    color: "#6b665a",
    fontWeight: "800",
    letterSpacing: 0.5,
    userSelect: "none",
  },
  settingsMockDash: {
    height: 2,
    borderRadius: 1,
    backgroundColor: "#aaa596",
  },
  settingsPopup: {
    position: "absolute",
    left: 0,
    backgroundColor: "#d8d4c6",
    borderWidth: 1,
    borderColor: "#a59f8d",
    shadowColor: "#5c5647",
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.24,
    shadowRadius: 12,
    zIndex: 4,
  },
  settingsPopupHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  settingsPopupRows: {
    justifyContent: "space-between",
  },
  settingsClose: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#c8c3b4",
    borderWidth: 1,
    borderColor: "#aaa596",
  },
  settingsCloseText: {
    color: "#4c4a55",
    fontWeight: "800",
    userSelect: "none",
  },

  bezel: {
    alignItems: "center",
    backgroundColor: "#4c4a55",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.25,
    shadowRadius: 6,
  },
  stripes: {
    position: "absolute",
  },
  stripe: {
    height: 2,
    borderRadius: 1,
  },
  batteryRow: {
    position: "absolute",
    alignItems: "center",
  },
  led: {
    backgroundColor: "#d64b3a",
    shadowColor: "#ff6a52",
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.8,
    shadowRadius: 4,
  },
  batteryLabel: {
    color: "#c4c2cc",
    fontWeight: "700",
    letterSpacing: 1,
  },

  lcd: {
    overflow: "hidden",
    backgroundColor: "#8fa027",
    borderColor: "#2b2d1a",
  },
  lcdWell: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "#9cad3a",
    borderColor: "rgba(20, 26, 8, 0.28)",
    borderWidth: 1,
  },
  lcdGlare: {
    position: "absolute",
    backgroundColor: "rgba(255, 255, 255, 0.14)",
    transform: [{ rotate: "-20deg" }],
    borderRadius: 999,
  },

  dotMatrix: {
    color: "#b7b5c1",
    fontWeight: "600",
  },

  wordmark: {
    flexDirection: "row",
    alignItems: "baseline",
  },
  brandPoke: {
    color: "#31316f",
    fontStyle: "italic",
    fontWeight: "800",
  },
  brandBoy: {
    color: "#9a1f4c",
    fontWeight: "800",
    fontStyle: "italic",
    letterSpacing: 0.5,
  },

  dpadBar: {
    position: "absolute",
    backgroundColor: "#2c2c30",
  },
  dpadHub: {
    position: "absolute",
    backgroundColor: "#242428",
  },
  dpadZonePressed: {
    backgroundColor: "rgba(255, 255, 255, 0.12)",
  },
  abGroup: {
    flexDirection: "row",
    alignItems: "flex-end",
    transform: [{ rotate: "-25deg" }],
  },
  faceButton: {
    backgroundColor: "#9a1f4c",
    borderWidth: 2,
    borderColor: "#6f153a",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 3,
  },
  faceButtonPressed: {
    backgroundColor: "#7c1740",
    transform: [{ scale: 0.92 }],
  },
  faceLabelOn: {
    color: "#f2d9e2",
    fontWeight: "800",
    fontStyle: "italic",
    userSelect: "none",
  },

  pillRow: {
    flexDirection: "row",
    justifyContent: "center",
  },
  pillGroup: {
    alignItems: "center",
  },
  pill: {
    backgroundColor: "#8a8577",
    borderWidth: 1,
    borderColor: "#6f6b5e",
  },
  pillLabel: {
    color: "#31316f",
    fontWeight: "800",
    fontStyle: "italic",
    letterSpacing: 0.5,
  },
});
