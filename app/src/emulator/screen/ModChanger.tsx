import { useEffect, useState } from "react";
import { Animated, Pressable, Text, View } from "react-native";

import type { RegistryMod } from "@/api/client";
import { playSfx } from "@/sfx";

import { ModChoice, ModConfigModal, ModScroll, SettingsModal } from "./dialogs";
import { modMetrics } from "./metrics";
import { styles } from "./styles";
import type { AnimValue, BattleLinkMode, Unit } from "./types";

/* ------------------------------------------------------------------ *
 * Mod changer
 * ------------------------------------------------------------------ */

// Accessory bay for snapping mods onto the console. Anchored to the ejected
// cartridge — right of it in landscape, above it in portrait — and fades in
// with the console drop, like the selector arrows. Two stacked panels: a
// one-mod-at-a-time browser (name, blurb, YES/NO), and an enabled count.
export function ModChanger({
  u,
  anim,
  active,
  landscape,
  cartWidth,
  mods,
  enabled,
  onSet,
  battleLinkEndpoint,
  onBattleLinkEndpoint,
  battleLinkMaxWaitS,
  onBattleLinkMaxWait,
  battleLinkMode,
  onBattleLinkMode,
  appBuildVersion,
}: {
  u: Unit;
  anim: AnimValue;
  active: boolean;
  landscape: boolean;
  cartWidth: number;
  mods: readonly RegistryMod[];
  enabled: ReadonlySet<string>;
  onSet: (id: string, on: boolean) => void;
  battleLinkEndpoint: string;
  onBattleLinkEndpoint: (value: string) => void;
  battleLinkMode: BattleLinkMode;
  onBattleLinkMode: (mode: BattleLinkMode) => void;
  appBuildVersion: string;
  battleLinkMaxWaitS: number;
  onBattleLinkMaxWait: (seconds: number) => void;
}) {
  const [idx, setIdx] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const m = modMetrics(u);
  const width = landscape ? u(170) : cartWidth + u(24);
  const opacity = anim.interpolate({ inputRange: [1, 2], outputRange: [0, 1], extrapolate: "clamp" });
  const grow = anim.interpolate({ inputRange: [1, 2], outputRange: [0.7, 1], extrapolate: "clamp" });

  useEffect(() => {
    setIdx((current) => Math.min(current, Math.max(0, mods.length - 1)));
  }, [mods.length]);

  const mod = mods[idx];
  const on = mod ? enabled.has(mod.id) : false;
  const scroll = (dir: 1 | -1) => {
    if (mods.length < 2) return;
    playSfx("dpad");
    setIdx((i) => (i + dir + mods.length) % mods.length);
  };
  const toggleSettings = () => {
    playSfx("select");
    setSettingsOpen((v) => !v);
  };
  const toggleConfig = () => {
    playSfx("select");
    setConfigOpen((v) => !v);
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
      {/* Panel 1: registry-backed mod browser */}
      <View style={[styles.modPanel, { borderRadius: u(10), padding: m.pad }]}>
        {mod ? (
          <>
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
              {mod.desc ?? "No description available."}
            </Text>
            <View style={{ flexDirection: "row", gap: m.gap, marginTop: m.gap }}>
              <ModChoice u={u} h={m.btnH} label="YES" active={on} onPress={() => onSet(mod.id, true)} />
              <ModChoice u={u} h={m.btnH} label="NO" active={!on} onPress={() => onSet(mod.id, false)} />
            </View>
            <Pressable
              onPress={toggleConfig}
              style={({ pressed }) => [
                styles.settingsButton,
                { height: m.configH, borderRadius: u(5), marginTop: m.gap },
                pressed && { opacity: 0.62 },
              ]}
            >
              <Text
                selectable={false}
                style={[styles.modName, styles.settingsButtonText, { fontSize: u(8), letterSpacing: u(1) }]}
              >
                CONFIG
              </Text>
            </Pressable>
          </>
        ) : (
          <Text selectable={false} style={[styles.modDesc, { fontSize: u(8), lineHeight: u(12) }]}>
            NO MODS IN REGISTRY
          </Text>
        )}
      </View>

      {active && mod ? (
        <ModConfigModal
          open={configOpen}
          mod={mod}
          onClose={toggleConfig}
          battleLinkEndpoint={battleLinkEndpoint}
          onBattleLinkEndpoint={onBattleLinkEndpoint}
          battleLinkMaxWaitS={battleLinkMaxWaitS}
          onBattleLinkMaxWait={onBattleLinkMaxWait}
          battleLinkMode={battleLinkMode}
          onBattleLinkMode={onBattleLinkMode}
        />
      ) : null}

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
          {enabled.size}/{mods.length}
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
          <SettingsModal
            open={settingsOpen}
            onClose={toggleSettings}
            appBuildVersion={appBuildVersion}
          />
        </>
      ) : null}
    </Animated.View>
  );
}
