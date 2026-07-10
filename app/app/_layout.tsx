import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { SettingsProvider } from "@/settings";
import { theme } from "@/theme";

export default function RootLayout() {
  return (
    <SettingsProvider>
      <SafeAreaProvider>
        <StatusBar style="light" />
        <Stack
          screenOptions={{
            headerStyle: { backgroundColor: theme.surface },
            headerTintColor: theme.text,
            contentStyle: { backgroundColor: theme.bg },
          }}
        >
          <Stack.Screen name="index" options={{ headerShown: false }} />
          <Stack.Screen name="cartridge/[id]" options={{ title: "Cartridge" }} />
        </Stack>
      </SafeAreaProvider>
    </SettingsProvider>
  );
}
