import { StyleSheet } from "react-native";

import { PAD_BOTTOM, PAD_TOP } from "./constants";

export const shellStyles = StyleSheet.create({
  page: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingTop: PAD_TOP,
    paddingBottom: PAD_BOTTOM,
    backgroundColor: "#26262f",
  },
  bootError: {
    position: "absolute",
    top: PAD_TOP,
    left: 16,
    right: 16,
    zIndex: 10,
    padding: 10,
    borderRadius: 6,
    backgroundColor: "#7c1740",
  },
  bootErrorText: {
    color: "#f2d9e2",
    fontSize: 11,
    fontWeight: "800",
    textAlign: "center",
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
  modConfigPlaceholder: {
    color: "#6b665a",
    fontWeight: "600",
    userSelect: "none",
  },
  modConfigHelp: {
    marginTop: 8,
    color: "#6b665a",
    fontSize: 12,
    lineHeight: 17,
  },
  modConfigModeRow: {
    flexDirection: "row",
    gap: 8,
    marginTop: 4,
  },
  modConfigModeBtn: {
    flex: 1,
    minHeight: 36,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 7,
    borderWidth: 1,
    borderColor: "#aaa596",
    backgroundColor: "#c8c3b4",
  },
  modConfigModeBtnOn: {
    borderColor: "#4c4a55",
    backgroundColor: "#4c4a55",
  },
  modConfigModeText: {
    color: "#4c4a55",
    fontWeight: "700",
    letterSpacing: 1,
    userSelect: "none",
  },
  modConfigModeTextOn: {
    color: "#d6d1c2",
  },
  modConfigError: {
    marginTop: 6,
    color: "#8e2f35",
    fontSize: 12,
    fontWeight: "700",
  },
  modConfigSave: {
    marginTop: 14,
    minHeight: 40,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 7,
    borderWidth: 1,
    borderColor: "#aaa596",
    backgroundColor: "#c8c3b4",
  },
  settingsInputError: {
    borderColor: "#8e2f35",
  },
  settingsBuildStamp: {
    marginTop: 12,
    textAlign: "center",
    color: "#8b8679",
    fontSize: 11,
    letterSpacing: 1,
    userSelect: "none",
  },
});
