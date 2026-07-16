import Constants from "expo-constants";

import EmulatorScreen from "@/emulator/screen/EmulatorScreen";

// On-device build identity: expo.version from app.json is baked into the
// binary at build time. "Pull latest" can't change the installed binary.
const APP_BUILD_VERSION = Constants.expoConfig?.version ?? "unknown";

export default function EmulatorRoute() {
  return <EmulatorScreen appBuildVersion={APP_BUILD_VERSION} />;
}
