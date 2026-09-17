import { useEffect, useState } from "react";
import { Modal, Pressable, Switch, Text, TextInput, View, useWindowDimensions } from "react-native";

import { createApi, type RegistryMod } from "@/api/client";
import { playSfx } from "@/sfx";
import { diffSection, loadInstalled, saveInstalled, useSettings, type DiffEntry } from "@/settings";

import { styles } from "./styles";
import type { BattleLinkMode, Unit } from "./types";

type PullState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "done"; changed: DiffEntry[]; total: number; newCount: number; updateCount: number };

// True full-screen settings dialog. Rendered through React Native's Modal so it
// escapes the widget's transformed layer and dims the whole screen. Holds the
// locally-persisted backend connection (URL + API key) and the "pull latest"
// action that syncs the installed ROM/mod manifest with the backend registry.
export function SettingsModal({
  open,
  onClose,
  appBuildVersion,
}: {
  open: boolean;
  onClose: () => void;
  appBuildVersion: string;
}) {
  const { width } = useWindowDimensions();
  const cardWidth = Math.min(width * 0.86, 380);
  const { settings, updateSettings } = useSettings();
  const [pull, setPull] = useState<PullState>({ phase: "idle" });

  const pullLatest = async () => {
    playSfx("select");
    setPull({ phase: "loading" });
    try {
      const api = createApi({ baseUrl: settings.backendUrl, apiKey: settings.apiKey });
      const registry = await api.fetchRegistry();
      const installed = await loadInstalled();
      const romDiff = diffSection(
        "rom",
        installed.roms,
        registry.roms.map((r) => ({ id: r.id, version: r.version, label: r.title })),
      );
      const modDiff = diffSection(
        "mod",
        installed.mods,
        registry.mods.map((m) => ({ id: m.id, version: m.version, label: m.name })),
      );
      const all = [...romDiff, ...modDiff];
      const changed = all.filter((e) => e.status !== "current");

      // Pulling installs the registry's current versions as the new baseline.
      await saveInstalled({
        roms: Object.fromEntries(registry.roms.map((r) => [r.id, r.version])),
        mods: Object.fromEntries(registry.mods.map((m) => [m.id, m.version])),
      });

      setPull({
        phase: "done",
        changed,
        total: all.length,
        newCount: changed.filter((e) => e.status === "new").length,
        updateCount: changed.filter((e) => e.status === "update").length,
      });
    } catch (e) {
      setPull({ phase: "error", message: e instanceof Error ? e.message : "Pull failed" });
    }
  };

  return (
    <Modal visible={open} transparent animationType="fade" onRequestClose={onClose}>
      {/* Backdrop - tapping outside the card closes the dialog. */}
      <Pressable style={styles.settingsBackdrop} onPress={onClose}>
        {/* Stop taps on the card itself from bubbling to the backdrop. */}
        <Pressable style={[styles.settingsCard, { width: cardWidth }]} onPress={() => {}}>
          <View style={styles.settingsCardHeader}>
            <Text selectable={false} style={styles.settingsTitle}>
              SETTINGS
            </Text>
            <Pressable
              onPress={onClose}
              hitSlop={10}
              style={({ pressed }) => [styles.settingsCloseBtn, pressed && { opacity: 0.6 }]}
            >
              <Text selectable={false} style={styles.settingsCloseBtnText}>
                ✕
              </Text>
            </Pressable>
          </View>

          <View style={styles.settingsField}>
            <Text selectable={false} style={styles.settingsFieldLabel}>
              BACKEND URL
            </Text>
            <TextInput
              value={settings.backendUrl}
              onChangeText={(t) => updateSettings({ backendUrl: t })}
              placeholder="http://localhost:4000"
              placeholderTextColor="#9a958a"
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              style={styles.settingsInput}
            />
          </View>

          <View style={styles.settingsField}>
            <Text selectable={false} style={styles.settingsFieldLabel}>
              BACKEND API KEY
            </Text>
            <TextInput
              value={settings.apiKey}
              onChangeText={(t) => updateSettings({ apiKey: t })}
              placeholder="••••••••"
              placeholderTextColor="#9a958a"
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
              style={styles.settingsInput}
            />
          </View>

          <View style={styles.settingsField}>
            <View style={styles.settingsToggleRow}>
              <View style={{ flex: 1 }}>
                <Text selectable={false} style={styles.settingsFieldLabel}>
                  TELEMETRY
                </Text>
                <Text selectable={false} style={styles.settingsToggleHint}>
                  Stream emulator vitals to the backend
                </Text>
              </View>
              <Switch
                value={settings.telemetry}
                onValueChange={(v) => updateSettings({ telemetry: v })}
                trackColor={{ false: "#aaa596", true: "#9a1f4c" }}
                thumbColor="#d8d4c6"
              />
            </View>
          </View>

          <View style={styles.settingsField}>
            <View style={styles.settingsToggleRow}>
              <View style={{ flex: 1 }}>
                <Text selectable={false} style={styles.settingsFieldLabel}>
                  DEV MODE
                </Text>
                <Text selectable={false} style={styles.settingsToggleHint}>
                  Allow remote button presses &amp; LCD screenshots
                </Text>
              </View>
              <Switch
                value={settings.devMode}
                onValueChange={(v) => updateSettings({ devMode: v })}
                trackColor={{ false: "#aaa596", true: "#9a1f4c" }}
                thumbColor="#d8d4c6"
              />
            </View>
          </View>

          <View style={styles.settingsSyncSection}>
            <Pressable
              onPress={pullLatest}
              disabled={pull.phase === "loading"}
              style={({ pressed }) => [
                styles.pullButton,
                pull.phase === "loading" && { opacity: 0.6 },
                pressed && { opacity: 0.75 },
              ]}
            >
              <Text selectable={false} style={styles.pullButtonText}>
                {pull.phase === "loading" ? "PULLING…" : "PULL LATEST"}
              </Text>
            </Pressable>
            <PullResult pull={pull} />
          </View>

          {/* Identifies the installed IPA. Pull latest never changes it; only
              sideloading a newly built IPA does. */}
          <Text selectable={false} style={styles.settingsBuildStamp}>
            BUILD v{appBuildVersion}
          </Text>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

// Status readout for the "pull latest" action: comparison summary plus a short
// list of what changed against the installed manifest.
function PullResult({ pull }: { pull: PullState }) {
  if (pull.phase === "idle") {
    return (
      <Text selectable={false} style={styles.pullHint}>
        Compares installed ROM &amp; mods against the backend registry.
      </Text>
    );
  }
  if (pull.phase === "loading") {
    return <Text selectable={false} style={styles.pullHint}>Checking registry…</Text>;
  }
  if (pull.phase === "error") {
    return (
      <Text selectable={false} style={styles.pullError}>
        {pull.message}
      </Text>
    );
  }

  if (pull.changed.length === 0) {
    return (
      <Text selectable={false} style={styles.pullOk}>
        ✓ Up to date — {pull.total} item{pull.total === 1 ? "" : "s"} installed.
      </Text>
    );
  }
  return (
    <View>
      <Text selectable={false} style={styles.pullOk}>
        ✓ Pulled {pull.newCount} new, {pull.updateCount} updated.
      </Text>
      <View style={styles.pullList}>
        {pull.changed.map((e) => (
          <View key={`${e.kind}:${e.id}`} style={styles.pullRow}>
            <Text
              selectable={false}
              style={[
                styles.pullBadge,
                e.status === "new" ? styles.pullBadgeNew : styles.pullBadgeUpdate,
              ]}
            >
              {e.status === "new" ? "NEW" : "UPD"}
            </Text>
            <Text selectable={false} style={styles.pullItemLabel} numberOfLines={1}>
              {e.label}
            </Text>
            <Text selectable={false} style={styles.pullItemVer}>
              {e.status === "update" ? `${e.from} → ${e.to}` : `v${e.to}`}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
}

// Per-mod configuration pop-up. Battle Link exposes its controls here; mods
// without configurable options receive the fallback message below.
export function ModConfigModal({
  open,
  mod,
  onClose,
  battleLinkEndpoint,
  onBattleLinkEndpoint,
  battleLinkMaxWaitS,
  onBattleLinkMaxWait,
  battleLinkMode,
  onBattleLinkMode,
}: {
  open: boolean;
  mod: RegistryMod;
  onClose: () => void;
  battleLinkEndpoint: string;
  onBattleLinkEndpoint: (value: string) => void;
  battleLinkMaxWaitS: number;
  onBattleLinkMaxWait: (seconds: number) => void;
  battleLinkMode: BattleLinkMode;
  onBattleLinkMode: (mode: BattleLinkMode) => void;
}) {
  const { width } = useWindowDimensions();
  const cardWidth = Math.min(width * 0.86, 380);
  const [draft, setDraft] = useState(battleLinkEndpoint);
  const [maxWaitDraft, setMaxWaitDraft] = useState(String(battleLinkMaxWaitS));
  const [modeDraft, setModeDraft] = useState<BattleLinkMode>(battleLinkMode);
  useEffect(() => { if (open) setDraft(battleLinkEndpoint); }, [open, battleLinkEndpoint]);
  useEffect(() => { if (open) setMaxWaitDraft(String(battleLinkMaxWaitS)); }, [open, battleLinkMaxWaitS]);
  useEffect(() => { if (open) setModeDraft(battleLinkMode); }, [open, battleLinkMode]);
  const endpointError = modeDraft === "get" && !/^https:\/\/[^\s]+$/i.test(draft.trim());
  const maxWaitSeconds = Number(maxWaitDraft.trim());
  const maxWaitError =
    maxWaitDraft.trim() === "" || !Number.isFinite(maxWaitSeconds) || maxWaitSeconds < 0 || maxWaitSeconds > 300;
  const saveError = endpointError || maxWaitError;
  const save = () => {
    if (saveError) return;
    onBattleLinkMode(modeDraft);
    if (modeDraft === "get") onBattleLinkEndpoint(draft);
    onBattleLinkMaxWait(maxWaitSeconds);
    onClose();
  };
  return (
    <Modal visible={open} transparent animationType="fade" onRequestClose={onClose}>
      {/* Backdrop - tapping outside the card closes the dialog. */}
      <Pressable style={styles.settingsBackdrop} onPress={onClose}>
        {/* Stop taps on the card itself from bubbling to the backdrop. */}
        <Pressable style={[styles.settingsCard, { width: cardWidth }]} onPress={() => {}}>
          <View style={styles.settingsCardHeader}>
            <Text selectable={false} style={styles.settingsTitle}>
              {mod.name} CONFIG
            </Text>
            <Pressable
              onPress={onClose}
              hitSlop={10}
              style={({ pressed }) => [styles.settingsCloseBtn, pressed && { opacity: 0.6 }]}
            >
              <Text selectable={false} style={styles.settingsCloseBtnText}>
                ✕
              </Text>
            </Pressable>
          </View>
          {mod.id === "battle-link" ? (
            <>
              <Text selectable={false} style={styles.settingsFieldLabel}>DECISION SOURCE</Text>
              <View style={styles.modConfigModeRow}>
                {(["get", "discord"] as const).map((mode) => (
                  <Pressable
                    key={mode}
                    onPress={() => setModeDraft(mode)}
                    style={({ pressed }) => [
                      styles.modConfigModeBtn,
                      modeDraft === mode && styles.modConfigModeBtnOn,
                      pressed && { opacity: 0.65 },
                    ]}
                  >
                    <Text
                      selectable={false}
                      style={[
                        styles.modConfigModeText,
                        modeDraft === mode && styles.modConfigModeTextOn,
                      ]}
                    >
                      {mode === "get" ? "GET" : "DISC"}
                    </Text>
                  </Pressable>
                ))}
              </View>
              {modeDraft === "get" ? (
                <>
                  <Text selectable={false} style={styles.settingsFieldLabel}>PUBLIC DECISION API</Text>
                  <TextInput
                    value={draft}
                    onChangeText={setDraft}
                    autoCapitalize="none"
                    autoCorrect={false}
                    keyboardType="url"
                    placeholder="https://example.com/decision"
                    placeholderTextColor="#8b8679"
                    style={[styles.settingsInput, endpointError && styles.settingsInputError]}
                  />
                  <Text selectable={false} style={styles.modConfigHelp}>
                    The endpoint must accept Battle Link GET requests and allow WebView CORS.
                  </Text>
                  {endpointError ? (
                    <Text selectable={false} style={styles.modConfigError}>Enter a public HTTPS URL.</Text>
                  ) : null}
                </>
              ) : (
                <Text selectable={false} style={styles.modConfigHelp}>
                  The bot runs on the Pokeboy server. Put the bot token in
                  backend/data/discord.json there, invite the bot as a user
                  app, then /connect during a battle.
                </Text>
              )}
              <Text selectable={false} style={styles.settingsFieldLabel}>MAX TIME TILL RANDOM (SECONDS)</Text>
              <TextInput
                value={maxWaitDraft}
                onChangeText={setMaxWaitDraft}
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="numeric"
                placeholder="30"
                placeholderTextColor="#8b8679"
                style={[styles.settingsInput, maxWaitError && styles.settingsInputError]}
              />
              <Text selectable={false} style={styles.modConfigHelp}>
                How long a battle waits for a decision before picking a random legal action. 0 always picks randomly.
              </Text>
              {maxWaitError ? (
                <Text selectable={false} style={styles.modConfigError}>Enter 0–300 seconds.</Text>
              ) : null}
              <Pressable
                disabled={saveError}
                onPress={save}
                style={({ pressed }) => [
                  styles.modConfigSave,
                  saveError && { opacity: 0.4 },
                  pressed && { opacity: 0.65 },
                ]}
              >
                <Text selectable={false} style={styles.settingsButtonText}>SAVE SETTINGS</Text>
              </Pressable>
            </>
          ) : (
            <Text selectable={false} style={styles.modConfigPlaceholder}>
              No options for this mod yet.
            </Text>
          )}
        </Pressable>
      </Pressable>
    </Modal>
  );
}

export function ModScroll({
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

export function ModChoice({
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
