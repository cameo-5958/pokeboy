import { Animated, Image, Pressable, Text, View } from "react-native";

import { SCREEN_RATIO, SPEEDS } from "./constants";
import { Bezel, Dpad, FaceButtons, MiniControls, PillRow, Wordmark } from "./controls";
import { styles } from "./styles";
import type { AnimValue, PanHandlers, Unit } from "./types";

/* ------------------------------------------------------------------ *
 * Layouts
 * ------------------------------------------------------------------ */

export function PortraitConsole({
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

export function LandscapeConsole({
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

export function Cartridge({
  u,
  width,
  height,
  labelWidth,
  labelHeight,
  panHandlers,
  image,
}: {
  u: Unit;
  width: number;
  height: number;
  labelWidth: number;
  labelHeight: number;
  panHandlers: PanHandlers;
  image: string | null;
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
      {/* Label sticker - the game image mounts inside the reserve */}
      <View
        style={[
          styles.cartLabel,
          { width: labelWidth, height: labelHeight, borderRadius: u(5), padding: u(5) },
        ]}
      >
        {image ? (
          <Image source={{ uri: image }} style={[styles.cartImageReserve, { borderRadius: u(3) }]} resizeMode="cover" />
        ) : (
          <View style={[styles.cartImageReserve, { borderRadius: u(3) }]} />
        )}
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
export function CartridgeArrow({
  u,
  anim,
  side,
  cartHeight,
  onPress,
}: {
  u: Unit;
  anim: AnimValue;
  side: "left" | "right";
  cartHeight: number;
  onPress: () => void;
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
        onPress={onPress}
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
