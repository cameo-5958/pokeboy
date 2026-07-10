import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Link } from "expo-router";

import { api, type Cartridge } from "@/api/client";
import { theme } from "@/theme";

export default function LibraryScreen() {
  const [carts, setCarts] = useState<Cartridge[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      setCarts(await api.listCartridges());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load cartridges");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={theme.accent} />
      </View>
    );
  }

  return (
    <FlatList
      data={carts}
      keyExtractor={(c) => c.id}
      contentContainerStyle={styles.list}
      refreshControl={
        <RefreshControl refreshing={loading} onRefresh={load} tintColor={theme.accent} />
      }
      ListEmptyComponent={
        <View style={styles.center}>
          <Text style={styles.muted}>
            {error ?? "No cartridges yet. Add ROMs to the backend."}
          </Text>
        </View>
      }
      renderItem={({ item }) => (
        <Link href={`/cartridge/${item.id}`} asChild>
          <Pressable style={styles.card}>
            {item.img ? (
              <Image source={{ uri: item.img }} style={styles.label} />
            ) : (
              <View style={[styles.label, styles.labelFallback]} />
            )}
            <Text style={styles.title}>{item.title}</Text>
          </Pressable>
        </Link>
      )}
    />
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  list: { padding: 16, gap: 12 },
  muted: { color: theme.textMuted, textAlign: "center" },
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    backgroundColor: theme.surface,
    borderColor: theme.border,
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
  },
  label: { width: 56, height: 56, borderRadius: 8, backgroundColor: theme.border },
  labelFallback: { opacity: 0.4 },
  title: { color: theme.text, fontSize: 16, fontWeight: "600", flexShrink: 1 },
});
