import { StyleSheet } from "react-native";

export const controlStyles = StyleSheet.create({
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
  emulator: {
    flex: 1,
    backgroundColor: "#c8d4a4",
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
