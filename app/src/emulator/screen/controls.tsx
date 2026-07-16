import { useCallback, useContext, useRef } from "react";
import { PanResponder, Pressable, Text, View } from "react-native";
import WebView from "react-native-webview";

import { playSfx } from "@/sfx";

import { SPEEDS } from "./constants";
import { EmulatorContext } from "./EmulatorContext";
import { styles } from "./styles";
import type { Unit } from "./types";

/* ------------------------------------------------------------------ *
 * Shared pieces
 * ------------------------------------------------------------------ */

export function Bezel({
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
  const { emulator, webViewRef, onMessage, settings, paused, mods } = useContext(EmulatorContext);
  const onLoad = useCallback(() => {
    // A fresh WebView needs the current host state immediately.
    webViewRef.current?.postMessage(JSON.stringify({ type: "input", buttons: 0, dpad: 0 }));
    webViewRef.current?.postMessage(JSON.stringify({ type: "settings", ...settings }));
    webViewRef.current?.postMessage(JSON.stringify({ type: "paused", value: paused }));
    webViewRef.current?.postMessage(JSON.stringify({ type: "mods", ids: mods }));
  }, [webViewRef, settings, paused, mods]);
  return (
    <View style={[styles.lcd, { width, height, borderRadius: u(4), borderWidth: u(1.5) }]}>
      {emulator ? (
        <WebView
          key={emulator.key}
          ref={webViewRef}
          source={{ uri: emulator.uri }}
          // The bundled emulator loads from a file:// URI, which the default
          // whitelist (http/https) silently blocks — leaving a blank WebView.
          originWhitelist={["file://*", "http://*", "https://*"]}
          injectedJavaScriptBeforeContentLoaded={emulator.injected}
          allowingReadAccessToURL={emulator.readAccessUri}
          style={styles.emulator}
          pointerEvents="none"
          scrollEnabled={false}
          overScrollMode="never"
          javaScriptEnabled
          allowsInlineMediaPlayback
          mediaPlaybackRequiresUserAction={false}
          onMessage={onMessage}
          onLoad={onLoad}
          // Android/iOS can kill the WebView's content process under memory
          // pressure; reload and let onLoad replay the host state.
          onContentProcessDidTerminate={() => webViewRef.current?.reload()}
        />
      ) : (
        <View style={styles.lcdWell} pointerEvents="none" />
      )}
      <View
        style={[styles.lcdGlare, { top: -u(20), right: -u(28), width: u(120), height: u(64) }]}
        pointerEvents="none"
      />
    </View>
  );
}

export function Wordmark({ u, style }: { u: Unit; style?: object }) {
  return (
    <View style={[styles.wordmark, style]}>
      <Text style={[styles.brandPoke, { fontSize: u(20) }]}>Poké</Text>
      <Text style={[styles.brandBoy, { fontSize: u(24), marginLeft: u(4) }]}>boy</Text>
    </View>
  );
}

export function Dpad({ u, size }: { u: Unit; size: number }) {
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
      <DpadZone mask={0x04} style={{ left: off, top: 0, width: arm, height: off, borderTopLeftRadius: u(5), borderTopRightRadius: u(5) }} />
      <DpadZone mask={0x08} style={{ left: off, top: size - off, width: arm, height: off, borderBottomLeftRadius: u(5), borderBottomRightRadius: u(5) }} />
      <DpadZone mask={0x02} style={{ top: off, left: 0, height: arm, width: off, borderTopLeftRadius: u(5), borderBottomLeftRadius: u(5) }} />
      <DpadZone mask={0x01} style={{ top: off, left: size - off, height: arm, width: off, borderTopRightRadius: u(5), borderBottomRightRadius: u(5) }} />
    </View>
  );
}

function DpadZone({ style, mask }: { style: object; mask: number }) {
  const { setInput } = useContext(EmulatorContext);
  return (
    <Pressable
      onPressIn={() => { setInput("dpad", mask, true); playSfx("dpad"); }}
      onPressOut={() => setInput("dpad", mask, false)}
      style={({ pressed }) => [{ position: "absolute" }, style, pressed && styles.dpadZonePressed]}
    />
  );
}

export function FaceButtons({ u, w, h }: { u: Unit; w?: number; h?: number }) {
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
  const { setInput } = useContext(EmulatorContext);
  const mask = label === "A" ? 0x01 : 0x02;
  return (
    <Pressable
      onPressIn={() => { setInput("buttons", mask, true); playSfx(label === "A" ? "a" : "b"); }}
      onPressOut={() => setInput("buttons", mask, false)}
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

export function PillRow({ u, gap }: { u: Unit; gap?: number }) {
  return (
    <View style={[styles.pillRow, { gap: gap ?? u(24) }]}>
      <Pill u={u} label="SELECT" />
      <Pill u={u} label="START" />
    </View>
  );
}

function Pill({ u, label }: { u: Unit; label: string }) {
  const { setInput } = useContext(EmulatorContext);
  const mask = label === "START" ? 0x08 : 0x04;
  return (
    <Pressable
      onPressIn={() => {
        setInput("buttons", mask, true);
        playSfx(label === "START" ? "start" : "select");
      }}
      onPressOut={() => setInput("buttons", mask, false)}
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

export function MiniControls({
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
