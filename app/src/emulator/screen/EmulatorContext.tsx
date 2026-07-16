import { createContext } from "react";
import type WebView from "react-native-webview";
import type { WebViewMessageEvent } from "react-native-webview";

import { DEFAULT_BATTLE_LINK_MAX_WAIT_S } from "./constants";
import type { BattleLinkMode, InputGroup } from "./types";

export const EmulatorContext = createContext<{
  emulator: { uri: string; injected: string; readAccessUri: string; key: string } | null;
  webViewRef: { current: WebView | null };
  onMessage: (event: WebViewMessageEvent) => void;
  setInput: (group: InputGroup, mask: number, held: boolean) => void;
  settings: {
    speed: number | "inf";
    muted: boolean;
    volume: number;
    telemetry: boolean;
    battleLinkEndpoint: string;
    battleLinkMode: BattleLinkMode;
    battleLinkMaxTimeTillRandomMs: number;
  };
  paused: boolean;
  mods: readonly string[];
}>({
  emulator: null,
  webViewRef: { current: null },
  onMessage: () => undefined,
  setInput: () => undefined,
  settings: {
    speed: 1,
    muted: false,
    volume: 1,
    telemetry: false,
    battleLinkEndpoint: "",
    battleLinkMode: "get" as BattleLinkMode,
    battleLinkMaxTimeTillRandomMs: DEFAULT_BATTLE_LINK_MAX_WAIT_S * 1000,
  },
  paused: false,
  mods: [],
});
