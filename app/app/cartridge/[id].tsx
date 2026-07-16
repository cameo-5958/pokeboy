import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Image,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Stack, useLocalSearchParams } from "expo-router";

import { createApi, type Cartridge } from "@/api/client";
import { useSettings } from "@/settings";
import { theme } from "@/theme";

export default function CartridgeScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { settings, settingsLoaded } = useSettings();
  const api = useMemo(
    () => createApi({ baseUrl: settings.backendUrl, apiKey: settings.apiKey }),
    [settings.backendUrl, settings.apiKey],
  );
  const [cart, setCart] = useState<Cartridge | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id || !settingsLoaded) return;
    setError(null);
    api
      .getCartridge(id)
      .then(setCart)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"));
  }, [id, settingsLoaded, api]);

  if (error) {
    return (
      <View style={styles.center}>
        <Text style={styles.muted}>{error}</Text>
      </View>
    );
  }

  if (!cart) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={theme.accent} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <Stack.Screen options={{ title: cart.title }} />
      {cart.img ? (
        <Image source={{ uri: cart.img }} style={styles.label} />
      ) : (
        <View style={[styles.label, styles.labelFallback]} />
      )}
      <Text style={styles.title}>{cart.title}</Text>
      <Text style={styles.muted}>{cart.file}</Text>
      {/*
        The emulator surface (WASM core bridged into React Native) mounts here.
        For now this screen just resolves and displays cartridge metadata.
      */}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", padding: 24, gap: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  label: { width: 160, height: 160, borderRadius: 16, backgroundColor: theme.border },
  labelFallback: { opacity: 0.4 },
  title: { color: theme.text, fontSize: 22, fontWeight: "700" },
  muted: { color: theme.textMuted },
});
